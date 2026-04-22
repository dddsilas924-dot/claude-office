#!/usr/bin/env python3
"""
Memory Sync — 4システム間の記憶同期エンジン
==========================================
30分間隔で Claude Code / VPS / Discord / Office Canvas の状態を同期する。

フロー:
  ① 収集 (Collect)  — shared_state.json + Canvas /health
  ② 差分検出 (Diff)  — 前回スナップショットと比較
  ③ 組立 (Build)     — BridgeEventPayload 形式に変換
  ④ 配信 (Dispatch)  — HMAC署名付き POST /api/v1/external_event
  ⑤ キャッシュ (Cache) — 今回のスナップショット保存

将来のフェーズ3外部連携に備え、metadata.visibility で公開レベルを制御:
  - internal: blockers, warnings, decisions（非公開）
  - shared:   status, next_action, updated_at（部門間共有）
  - public:   wins, last_output（外部連携OK）

使い方:
    python3 scripts/memory_sync.py                    # 1回実行
    python3 scripts/memory_sync.py --watch             # 30分間隔で常駐
    python3 scripts/memory_sync.py --watch --interval 600  # 10分間隔

環境変数 (.env):
    EXTERNAL_EVENT_SECRET  — HMAC署名キー
    OFFICE_BASE_URL        — Canvas backend URL (default: http://localhost:8000)
    SHARED_STATE_PATH      — shared_state.json のパス (default: auto-detect)
"""
from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import logging
import os
import sys
import time
from copy import deepcopy
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# .env 読み込み
# ---------------------------------------------------------------------------
_ENV_FILE = Path(__file__).parent.parent / ".env"


def _load_dotenv(path: Path) -> None:
    if not path.is_file():
        return
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            key = key.strip()
            val = val.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = val


try:
    from dotenv import load_dotenv
    load_dotenv(_ENV_FILE)
except ImportError:
    _load_dotenv(_ENV_FILE)

# ---------------------------------------------------------------------------
# ロギング
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] memory_sync — %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("memory_sync")

# ---------------------------------------------------------------------------
# 定数・設定
# ---------------------------------------------------------------------------
JST = timezone(timedelta(hours=9))
HMAC_SECRET: str = os.environ.get(
    "EXTERNAL_EVENT_SECRET", "EqeUaSghh0inJxagunS-Zoyo91_683Ja-6cmWHkNBmQ"
)
OFFICE_BASE_URL: str = os.environ.get("OFFICE_BASE_URL", "http://localhost:8000").rstrip("/")
CANVAS_SESSION_ID: str = os.environ.get("CANVAS_SESSION_ID", "bridge_sync")
DEFAULT_INTERVAL: int = 1800  # 30分

# shared_state.json の自動検出パス（優先順）
SHARED_STATE_CANDIDATES = [
    Path(os.environ.get("SHARED_STATE_PATH", "")) if os.environ.get("SHARED_STATE_PATH") else None,
    Path(__file__).parent.parent.parent / "empire_monitor_full_20260321/.claude/shared_state.json",
    Path.home() / "Downloads/empire_monitor_full_20260321/.claude/shared_state.json",
    Path(__file__).parent.parent / ".claude/shared_state.json",
]

CACHE_PATH = Path("/tmp/memory_sync_cache.json")
SYNC_LOG_PATH = Path("/tmp/memory_sync_history.json")

# event_kind マッピング
EVENT_KIND_MAP = {
    "blocker": "SYNC_BLOCKER",
    "win": "SYNC_WIN",
    "status_change": "SYNC_STATE_CHANGED",
    "heartbeat": "SYNC_HEARTBEAT",
}

# 部門→キャラ表示マッピング（discord_bot.py と同じ）
DEPT_CHARACTERS: dict[str, dict[str, str]] = {
    "commander": {"name": "フィル（司令塔）", "color": "#D4AF37", "model": "opus"},
    "research": {"name": "リョウ（リサーチ）", "color": "#00BFFF", "model": "sonnet"},
    "sales": {"name": "レイ（営業）", "color": "#FF6B6B", "model": "sonnet"},
    "design": {"name": "リック（デザイン）", "color": "#9B59B6", "model": "sonnet"},
    "content": {"name": "コンテンツ部", "color": "#E67E22", "model": "sonnet"},
    "writing": {"name": "カイ（ライティング）", "color": "#2ECC71", "model": "sonnet"},
    "advertising": {"name": "エナ（広告）", "color": "#E74C3C", "model": "sonnet"},
    "ai_investment": {"name": "アキ（AI投資）", "color": "#F39C12", "model": "sonnet"},
    "new_biz": {"name": "タダシ（新規事業）", "color": "#1ABC9C", "model": "sonnet"},
    "bridge": {"name": "伝書鳩（Bridge）", "color": "#95A5A6", "model": "haiku"},
    "phil_consulting": {"name": "フィルコンサル", "color": "#8E44AD", "model": "sonnet"},
    "security": {"name": "セキュリティ", "color": "#34495E", "model": "haiku"},
    "real_estate": {"name": "不動産部", "color": "#16A085", "model": "sonnet"},
    "origin_story": {"name": "コピーロボット", "color": "#E91E63", "model": "sonnet"},
    "takumi_x": {"name": "タクミX", "color": "#FF9800", "model": "sonnet"},
    "doradora_sns": {"name": "どらどらSNS", "color": "#673AB7", "model": "sonnet"},
}


# =====================================================================
# ① 収集 (Collect)
# =====================================================================

def find_shared_state() -> Path | None:
    """shared_state.json を自動検出"""
    for candidate in SHARED_STATE_CANDIDATES:
        if candidate and candidate.is_file():
            return candidate
    return None


def load_shared_state(path: Path) -> dict[str, Any]:
    """shared_state.json を安全に読み込む"""
    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
        return data
    except (json.JSONDecodeError, OSError) as e:
        log.error(f"shared_state.json 読み込み失敗: {e}")
        return {}


def check_canvas_health() -> dict[str, Any]:
    """Office Canvas の死活確認"""
    import urllib.request
    try:
        req = urllib.request.Request(f"{OFFICE_BASE_URL}/health", method="GET")
        with urllib.request.urlopen(req, timeout=5) as resp:
            return {"alive": True, "status": resp.status}
    except Exception as e:
        return {"alive": False, "error": str(e)}


def check_discord_bot() -> dict[str, Any]:
    """Discord Bot の死活確認"""
    import urllib.request
    token = os.environ.get("DISCORD_BOT_TOKEN", "")
    if not token:
        return {"alive": False, "error": "no token"}
    try:
        req = urllib.request.Request(
            "https://discord.com/api/v10/users/@me",
            headers={
                "Authorization": f"Bot {token}",
                "User-Agent": "DiscordBot (https://claude-office, 1.0)",
            },
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
            return {"alive": True, "username": data.get("username", "?"), "id": data.get("id")}
    except Exception as e:
        return {"alive": False, "error": str(e)}


# =====================================================================
# ② 差分検出 (Diff)
# =====================================================================

def load_cache() -> dict[str, Any]:
    """前回のスナップショットを読み込む"""
    if CACHE_PATH.is_file():
        try:
            with CACHE_PATH.open("r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def save_cache(snapshot: dict[str, Any]) -> None:
    """今回のスナップショットを保存"""
    with CACHE_PATH.open("w", encoding="utf-8") as f:
        json.dump(snapshot, f, ensure_ascii=False, indent=2)


def detect_changes(old_depts: dict, new_depts: dict) -> list[dict[str, Any]]:
    """部門ごとの変更を検出"""
    changes: list[dict[str, Any]] = []

    for dept_id, new_data in new_depts.items():
        if not isinstance(new_data, dict):
            continue
        old_data = old_depts.get(dept_id, {})
        if not isinstance(old_data, dict):
            old_data = {}

        change_types: list[str] = []

        # status 変更
        if new_data.get("status") != old_data.get("status"):
            change_types.append("status_change")

        # blockers 変更
        new_blockers = new_data.get("blockers", [])
        old_blockers = old_data.get("blockers", [])
        if new_blockers != old_blockers and new_blockers:
            change_types.append("blocker")

        # wins 変更
        new_wins = new_data.get("wins", [])
        old_wins = old_data.get("wins", [])
        if new_wins != old_wins and len(new_wins) > len(old_wins):
            change_types.append("win")

        # last_output 変更
        if new_data.get("last_output") != old_data.get("last_output"):
            change_types.append("status_change")

        if change_types:
            changes.append({
                "dept_id": dept_id,
                "change_types": list(set(change_types)),
                "data": new_data,
            })

    return changes


# =====================================================================
# ③ ペイロード組み立て (Build)
# =====================================================================

def classify_visibility(field: str) -> str:
    """フィールドの公開レベルを判定"""
    internal_fields = {"blockers", "warnings", "decisions", "narrative"}
    shared_fields = {"status", "next_action", "updated_at"}
    # public: wins, last_output
    if field in internal_fields:
        return "internal"
    elif field in shared_fields:
        return "shared"
    return "public"


def build_sync_payload(
    dept_id: str,
    dept_data: dict[str, Any],
    change_types: list[str],
) -> dict[str, Any]:
    """BridgeEventPayload 形式に変換"""
    char = DEPT_CHARACTERS.get(dept_id, {
        "name": dept_id,
        "color": "#888888",
        "model": "sonnet",
    })

    # 最も重要な変更タイプを event_kind に
    if "blocker" in change_types:
        event_kind = EVENT_KIND_MAP["blocker"]
    elif "win" in change_types:
        event_kind = EVENT_KIND_MAP["win"]
    elif "status_change" in change_types:
        event_kind = EVENT_KIND_MAP["status_change"]
    else:
        event_kind = EVENT_KIND_MAP["heartbeat"]

    # メッセージ組み立て
    status = dept_data.get("status", "unknown")
    last_output = dept_data.get("last_output", "")
    blockers = dept_data.get("blockers", [])
    wins = dept_data.get("wins", [])

    parts = [f"[{status}]"]
    if last_output:
        parts.append(last_output[:80])
    if blockers:
        parts.append(f"Blockers: {len(blockers)}")
    if wins:
        parts.append(f"Wins: {len(wins)}")
    message = " | ".join(parts)

    return {
        "session_id": CANVAS_SESSION_ID,
        "dept_id": dept_id,
        "display_name": char["name"],
        "agent_color": char["color"],
        "event_kind": event_kind,
        "message": message,
        "model": char.get("model", "sonnet"),
        "metadata": {
            "visibility": "shared",
            "source": "memory_sync",
            "snapshot_at": datetime.now(JST).isoformat(),
            "sync_version": "1.0",
            "dept_summary": {
                "status": status,
                "blockers_count": len(blockers),
                "wins_count": len(wins),
                "last_output": (last_output[:100] if last_output else ""),
                "next_action": dept_data.get("next_action", ""),
            },
        },
    }


def build_heartbeat_payload(dept_id: str) -> dict[str, Any]:
    """定期ハートビート用のペイロード"""
    char = DEPT_CHARACTERS.get(dept_id, {
        "name": dept_id, "color": "#888888", "model": "sonnet",
    })
    return {
        "session_id": CANVAS_SESSION_ID,
        "dept_id": dept_id,
        "display_name": char["name"],
        "agent_color": char["color"],
        "event_kind": "SYNC_HEARTBEAT",
        "message": "Memory sync heartbeat",
        "model": char.get("model", "sonnet"),
        "metadata": {
            "visibility": "internal",
            "source": "memory_sync",
            "snapshot_at": datetime.now(JST).isoformat(),
            "sync_version": "1.0",
        },
    }


# =====================================================================
# ④ 配信 (Dispatch)
# =====================================================================

def sign_and_post(payload: dict[str, Any]) -> tuple[bool, int | str]:
    """HMAC署名付きで external_event API に POST"""
    import urllib.request
    import urllib.error

    url = f"{OFFICE_BASE_URL}/api/v1/external_event"
    body = json.dumps(payload).encode("utf-8")
    ts = str(int(time.time()))
    signing_input = f"{ts}.".encode() + body
    sig = hmac.new(HMAC_SECRET.encode(), signing_input, hashlib.sha256).hexdigest()

    req = urllib.request.Request(
        url,
        data=body,
        headers={
            "Content-Type": "application/json",
            "X-Bridge-Signature": f"sha256={sig}",
            "X-Bridge-Timestamp": ts,
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return (True, resp.status)
    except urllib.error.HTTPError as e:
        return (False, e.code)
    except Exception as e:
        return (False, str(e))


# =====================================================================
# ⑤ 同期ログ (History)
# =====================================================================

def append_sync_log(entry: dict[str, Any]) -> None:
    """同期履歴を追記（最大100件保持）"""
    history: list[dict] = []
    if SYNC_LOG_PATH.is_file():
        try:
            with SYNC_LOG_PATH.open("r", encoding="utf-8") as f:
                history = json.load(f)
        except (json.JSONDecodeError, OSError):
            pass

    history.append(entry)
    history = history[-100:]  # 最新100件のみ

    with SYNC_LOG_PATH.open("w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, indent=2)


# =====================================================================
# メイン同期ロジック
# =====================================================================

def run_sync() -> dict[str, Any]:
    """1回の同期サイクルを実行"""
    now = datetime.now(JST)
    log.info(f"=== Memory Sync 開始 ({now.strftime('%H:%M:%S')}) ===")

    result = {
        "timestamp": now.isoformat(),
        "shared_state": False,
        "canvas": {"alive": False},
        "discord": {"alive": False},
        "changes_detected": 0,
        "events_sent": 0,
        "events_failed": 0,
        "heartbeats_sent": 0,
    }

    # ① 収集
    ss_path = find_shared_state()
    if ss_path:
        log.info(f"shared_state.json: {ss_path}")
        state = load_shared_state(ss_path)
        result["shared_state"] = True
    else:
        log.warning("shared_state.json が見つかりません")
        state = {}

    canvas = check_canvas_health()
    result["canvas"] = canvas
    log.info(f"Canvas: {'ALIVE' if canvas['alive'] else 'DOWN'}")

    discord_status = check_discord_bot()
    result["discord"] = discord_status
    log.info(f"Discord Bot: {'ALIVE' if discord_status['alive'] else 'DOWN'}")

    if not canvas["alive"]:
        log.warning("Canvas が応答なし — 配信スキップ")
        append_sync_log(result)
        return result

    # ② 差分検出
    cache = load_cache()
    old_depts = cache.get("departments", {})
    new_depts = state.get("departments", {})

    changes = detect_changes(old_depts, new_depts)
    result["changes_detected"] = len(changes)

    if changes:
        log.info(f"変更検出: {len(changes)} 部門")
        for ch in changes:
            log.info(f"  {ch['dept_id']}: {', '.join(ch['change_types'])}")
    else:
        log.info("変更なし — ハートビートのみ送信")

    # ③④ 変更があれば配信
    for ch in changes:
        payload = build_sync_payload(ch["dept_id"], ch["data"], ch["change_types"])
        ok, status_code = sign_and_post(payload)
        if ok:
            result["events_sent"] += 1
            log.info(f"  SYNC {ch['dept_id']}: HTTP {status_code}")
        else:
            result["events_failed"] += 1
            log.error(f"  SYNC {ch['dept_id']}: FAILED ({status_code})")
        time.sleep(0.2)

    # ハートビート（変更がない部門にも最低限の存在確認）
    heartbeat_depts = [d for d in DEPT_CHARACTERS if d not in [c["dept_id"] for c in changes]]
    # ハートビートは主要部門のみ（全部送ると重い）
    core_depts = ["commander", "research", "bridge"]
    for dept_id in core_depts:
        if dept_id in heartbeat_depts:
            payload = build_heartbeat_payload(dept_id)
            ok, status_code = sign_and_post(payload)
            if ok:
                result["heartbeats_sent"] += 1
            time.sleep(0.1)

    # ⑤ キャッシュ更新
    save_cache({
        "departments": deepcopy(new_depts),
        "last_sync": now.isoformat(),
        "canvas_alive": canvas["alive"],
        "discord_alive": discord_status.get("alive", False),
    })

    # 同期ログ追記
    append_sync_log(result)

    log.info(
        f"=== 完了: {result['events_sent']} sync / "
        f"{result['heartbeats_sent']} heartbeat / "
        f"{result['events_failed']} failed ==="
    )

    return result


def run_watch(interval: int = DEFAULT_INTERVAL) -> None:
    """常駐モード: interval秒ごとに同期"""
    log.info(f"常駐モード開始 (interval={interval}s)")

    # 初回は即実行
    run_sync()

    while True:
        log.info(f"次回同期まで {interval}s 待機...")
        try:
            time.sleep(interval)
        except KeyboardInterrupt:
            log.info("停止シグナル受信 — 終了")
            break
        run_sync()


# =====================================================================
# CLI
# =====================================================================

def main() -> None:
    parser = argparse.ArgumentParser(description="Memory Sync Engine")
    parser.add_argument("--watch", action="store_true", help="常駐モード")
    parser.add_argument("--interval", type=int, default=DEFAULT_INTERVAL, help="同期間隔(秒)")
    parser.add_argument("--state-path", type=str, help="shared_state.json のパス")
    args = parser.parse_args()

    if args.state_path:
        os.environ["SHARED_STATE_PATH"] = args.state_path

    if args.watch:
        run_watch(args.interval)
    else:
        result = run_sync()
        # 終了コード: 失敗があれば1
        sys.exit(1 if result["events_failed"] > 0 else 0)


if __name__ == "__main__":
    main()
