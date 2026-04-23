#!/usr/bin/env python3
"""
伝書鳩 — Discord Bot
====================
常駐型 Discord Bot。えむが寝ている間も Empire Monitor の各部門キャラを
Canvas 上に生き続けさせ、Discord チャンネルのメッセージを Canvas へ転送する。

アーキテクチャルール:
- Discord = 部門が自律的に動く場。えむは審査員
- Office (Canvas) = 視覚的に見える場
- Claude Code = えむが直接指示する場（別世界線）

指揮系統:
- 基本: えむ → フィル（司令塔）→ 伝書鳩 → 各部門
- 例外: 外部イレギュラーは直接部門に届く
- 14部門パネル: えむ（どらどら）専用の指示ツール

成果物:
- えむ指示の成果物 → 📦ドラの成果物
- 部門自律の成果物 → 📦部門の成果物

セッション連携:
- Claude Code セッション切替時 → Discord に通知
- 全ログは流さない → 要約・状態のみ同期

機能:
1. スラッシュコマンド: /status /ask /bridge
2. テキストコマンド: テスト, ステータス, ヘルプ, 部門一覧
3. Discord → Canvas メッセージ転送 (HMAC 署名付き external_event API)
4. Canvas ハートビート (5 分ごとに全キャラ再送信で TTL をリセット)
5. 自動再接続 / グレースフルシャットダウン
6. 14部門中継パネル (RelayPanelView: 永続View + モーダル)
7. 添付ファイル処理 (画像/PDF/MD/txt/動画)
8. 保管庫チャンネル自動作成

使い方:
    pip install discord.py python-dotenv aiohttp
    python scripts/discord_bot.py

必須環境変数 (.env):
    DISCORD_BOT_TOKEN
    EXTERNAL_EVENT_SECRET=EqeUaSghh0inJxagunS-Zoyo91_683Ja-6cmWHkNBmQ

オプション環境変数:
    OFFICE_BASE_URL=http://localhost:8000  (デフォルト)
    CANVAS_SESSION_ID=bridge_live          (デフォルト)
    HEARTBEAT_INTERVAL=300                 (秒、デフォルト 5 分)
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import os
import signal
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

import aiohttp
import discord
from discord import app_commands

# 自律オフィスモジュール (Phase 2: 部門AI頭脳)
try:
    from dept_brain import DeptBrain
    from dept_prompts import DEPT_PROMPTS as AI_DEPT_PROMPTS
    from dept_scheduler import DeptScheduler
    from dept_meeting import MeetingEngine, MeetingScheduler
    from daily_report import DailyReportEngine
    from discord_logger import DiscordLogger
    from instruction_engine import InstructionEngine
    _AUTONOMOUS_AVAILABLE = True
except ImportError:
    _AUTONOMOUS_AVAILABLE = False
    InstructionEngine = None  # type: ignore[assignment,misc]
    DeptBrain = None  # type: ignore[assignment,misc]
    DeptScheduler = None  # type: ignore[assignment,misc]
    MeetingEngine = None  # type: ignore[assignment,misc]
    MeetingScheduler = None  # type: ignore[assignment,misc]

# ---------------------------------------------------------------------------
# .env 読み込み (python-dotenv がなければ手動フォールバック)
# ---------------------------------------------------------------------------
_ENV_FILE = Path(__file__).parent.parent / ".env"


def _load_dotenv(path: Path) -> None:
    """python-dotenv なしで .env を読み込む最小実装"""
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
            if key:
                os.environ[key] = val


try:
    from dotenv import load_dotenv
    load_dotenv(_ENV_FILE, override=True)
except ImportError:
    _load_dotenv(_ENV_FILE)

# ---------------------------------------------------------------------------
# ロギング設定
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger("discord_bot")

# ---------------------------------------------------------------------------
# 設定
# ---------------------------------------------------------------------------
BOT_TOKEN: str = os.environ.get("DISCORD_BOT_TOKEN", "")
HMAC_SECRET: str = os.environ.get(
    "EXTERNAL_EVENT_SECRET", "EqeUaSghh0inJxagunS-Zoyo91_683Ja-6cmWHkNBmQ"
)
OFFICE_BASE_URL: str = os.environ.get("OFFICE_BASE_URL", "http://localhost:8000").rstrip("/")
CANVAS_SESSION_ID: str = os.environ.get("CANVAS_SESSION_ID", "bridge_live")
HEARTBEAT_INTERVAL: int = int(os.environ.get("HEARTBEAT_INTERVAL", "300"))  # 秒
GUILD_ID: int = 1494899621460312145
JST = timezone(timedelta(hours=9))

# 添付ファイルのテキスト最大文字数
ATTACHMENT_TEXT_MAX = 2000

# ---------------------------------------------------------------------------
# チャンネル定義
# ---------------------------------------------------------------------------
CHANNELS: dict[str, int] = {
    "司令塔": 1494948235125592206,
    "コンテンツ部": 1494948238292291714,
    "デザイン部": 1494948239751909379,
    "ライティング部": 1494948241215590480,
    "リサーチ部": 1494948243656806450,
    "新規事業部": 1494948244994658376,
    "コピーロボット部": 1494948246122922064,
    "営業部": 1494948248975314955,
    "広告部": 1494948250065699008,
    "フィルコンサル部": 1494948252984807426,
    "タクミX部": 1494948254264201347,
    "AI投資部": 1494948255392464896,
    "不動産部": 1494948256688640040,
    "どらどらSNS部": 1494948258219425832,
    "セキュリティ部": 1494948261289656460,
    "会議室": 1495637503175168092,
    "司令塔ログ": 1495637504580386978,
    "一般": 1494899622253170781,
    # 保管庫・ダッシュボード・日報チャンネルは on_ready で動的追加される
    # 改善6・7: 以下は on_ready で自動作成後にIDが追加される
    # "📊ステータス": (動的)
    # "📝日報": (動的)
}

# チャンネル ID → 部門名 の逆引き
CHANNEL_TO_DEPT: dict[int, str] = {v: k for k, v in CHANNELS.items()}

# ---------------------------------------------------------------------------
# 部門キャラクター定義
# ---------------------------------------------------------------------------
# agent_color は #RRGGBB 形式 (external_event API の regex: ^#[0-9A-Fa-f]{6}$)
CHARACTERS: list[dict[str, Any]] = [
    {
        "dept_id": "commander",
        "display_name": "🛰️ フィル（司令塔）",
        "agent_color": "#D4AF37",
        "model": "opus",
        "discord_color": 0xD4AF37,
        "icon": "🛰️",
        "role": "司令塔・統括",
    },
    {
        "dept_id": "research",
        "display_name": "🔬 リョウ（リサーチ）",
        "agent_color": "#00BFFF",
        "model": "sonnet",
        "discord_color": 0x00BFFF,
        "icon": "🔬",
        "role": "リサーチ部長",
    },
    {
        "dept_id": "sales",
        "display_name": "💼 レイ（営業）",
        "agent_color": "#FF6B6B",
        "model": "sonnet",
        "discord_color": 0xFF6B6B,
        "icon": "💼",
        "role": "営業部長",
    },
    {
        "dept_id": "design",
        "display_name": "🎨 リック（デザイン）",
        "agent_color": "#9B59B6",
        "model": "sonnet",
        "discord_color": 0x9B59B6,
        "icon": "🎨",
        "role": "デザイン部長",
    },
    {
        "dept_id": "content",
        "display_name": "🎬 コンテンツ部",
        "agent_color": "#E67E22",
        "model": "sonnet",
        "discord_color": 0xE67E22,
        "icon": "🎬",
        "role": "コンテンツ部長",
    },
    {
        "dept_id": "writing",
        "display_name": "✍️ カイ（ライティング）",
        "agent_color": "#2ECC71",
        "model": "sonnet",
        "discord_color": 0x2ECC71,
        "icon": "✍️",
        "role": "ライティング部長",
    },
    {
        "dept_id": "advertising",
        "display_name": "📢 エナ（広告）",
        "agent_color": "#E74C3C",
        "model": "sonnet",
        "discord_color": 0xE74C3C,
        "icon": "📢",
        "role": "広告部長",
    },
    {
        "dept_id": "ai_investment",
        "display_name": "📈 アキ（AI投資）",
        "agent_color": "#F39C12",
        "model": "sonnet",
        "discord_color": 0xF39C12,
        "icon": "📈",
        "role": "AI投資部長",
    },
    {
        "dept_id": "new_biz",
        "display_name": "🚀 タダシ（新規事業）",
        "agent_color": "#1ABC9C",
        "model": "sonnet",
        "discord_color": 0x1ABC9C,
        "icon": "🚀",
        "role": "新規事業部長",
    },
    {
        "dept_id": "bridge",
        "display_name": "🐦 伝書鳩（Bridge）",
        "agent_color": "#95A5A6",
        "model": "haiku",
        "discord_color": 0x95A5A6,
        "icon": "🐦",
        "role": "Bridge連絡係",
    },
    {
        "dept_id": "phil_consulting",
        "display_name": "📚 フィルコンサル",
        "agent_color": "#8E44AD",
        "model": "sonnet",
        "discord_color": 0x8E44AD,
        "icon": "📚",
        "role": "フィルコンサル部長",
    },
    {
        "dept_id": "security",
        "display_name": "🛡️ セキュリティ",
        "agent_color": "#34495E",
        "model": "haiku",
        "discord_color": 0x34495E,
        "icon": "🛡️",
        "role": "セキュリティ担当",
    },
    # 改善2: 不足キャラ4件追加
    {
        "dept_id": "takumi_x",
        "display_name": "🎯 タクミ（X運用）",
        "agent_color": "#E91E63",
        "model": "sonnet",
        "discord_color": 0xE91E63,
        "icon": "🎯",
        "role": "X運用部長",
    },
    {
        "dept_id": "real_estate",
        "display_name": "🏠 アイリ（不動産）",
        "agent_color": "#795548",
        "model": "sonnet",
        "discord_color": 0x795548,
        "icon": "🏠",
        "role": "不動産部長",
    },
    {
        "dept_id": "doradora_sns",
        "display_name": "📱 どらどらSNS",
        "agent_color": "#FF9800",
        "model": "sonnet",
        "discord_color": 0xFF9800,
        "icon": "📱",
        "role": "どらどらSNS担当",
    },
    {
        "dept_id": "origin_story",
        "display_name": "🤖 コピーロボット",
        "agent_color": "#607D8B",
        "model": "haiku",
        "discord_color": 0x607D8B,
        "icon": "🤖",
        "role": "コピーロボット担当",
    },
]

# dept_id → キャラ情報 の辞書
CHAR_BY_DEPT: dict[str, dict[str, Any]] = {c["dept_id"]: c for c in CHARACTERS}

# チャンネル名 → dept_id のマッピング (転送用)
CHANNEL_TO_DEPT_ID: dict[str, str] = {
    "司令塔": "commander",
    "コンテンツ部": "content",
    "デザイン部": "design",
    "ライティング部": "writing",
    "リサーチ部": "research",
    "新規事業部": "new_biz",
    "コピーロボット部": "origin_story",
    "営業部": "sales",
    "広告部": "advertising",
    "フィルコンサル部": "phil_consulting",
    "タクミX部": "takumi_x",
    "AI投資部": "ai_investment",
    "不動産部": "real_estate",
    "どらどらSNS部": "doradora_sns",
    "セキュリティ部": "security",
    "会議室": "commander",
    "司令塔ログ": "commander",
}

# 14部門パネル用: dept_id → チャンネル名 のマッピング
RELAY_DEPT_TO_CHANNEL: dict[str, str] = {
    "commander": "司令塔",
    "content": "コンテンツ部",
    "design": "デザイン部",
    "writing": "ライティング部",
    "research": "リサーチ部",
    "new_biz": "新規事業部",
    "sales": "営業部",
    "advertising": "広告部",
    "phil_consulting": "フィルコンサル部",
    "ai_investment": "AI投資部",
    "security": "セキュリティ部",
    # 改善2: 不足4部門のルーティング追加
    "takumi_x": "タクミX部",
    "real_estate": "不動産部",
    "doradora_sns": "どらどらSNS部",
    "origin_story": "コピーロボット部",
}

# ハートビートで使うステータスメッセージ (循環してマンネリを防ぐ)
HEARTBEAT_MESSAGES: dict[str, list[str]] = {
    "commander": [
        "全部門の稼働状況を確認中。Canvas 接続良好。",
        "どらの就寝中も全部門スタンバイ。",
        "Discord ↔ Canvas ブリッジ正常稼働中。",
    ],
    "research": [
        "リサーチキュー監視中。新ネタ待機。",
        "SSS 級ソースチェーン分析システム稼働中。",
        "X-first 原則で情報収集継続中。",
    ],
    "sales": [
        "リード管理システム稼働中。商談パイプライン監視。",
        "playbook_engine.py 待機中。次の商談に備える。",
        "新規リード通知待ち。温度感3段階で管理中。",
    ],
    "design": [
        "デザインシステム 5 テーマ待機中。依頼があれば即対応。",
        "LP テンプレート 15 種スタンバイ。品質 L2 チェック準備完了。",
        "Canvas 用キャラクター全体制維持中。",
    ],
    "content": [
        "台本在庫チェック中。在庫切れチャンネルへ補充準備。",
        "script_outline_engine.py 待機中。",
        "コンテンツファネル Tier 1→2→3 正常稼働。",
    ],
    "writing": [
        "taiyo-analyzer 85 点基準で品質管理中。",
        "writing_copy_engine.py スタンバイ。4 copy_type 対応済み。",
        "ステップメール構成エンジン待機中。",
    ],
    "advertising": [
        "ad_campaign_engine.py 待機中。3 コンセプト最小。",
        "FB 広告 A/B テスト監視中。14 日以上のデータ待ち。",
        "KPI ダッシュボード監視継続中。",
    ],
    "ai_investment": [
        "DeFi TVL 監視中。Tier 1 シグナル待機。",
        "Telegram 配信エンジン稼働中。",
        "仮想通貨マーケット監視システム正常。",
    ],
    "new_biz": [
        "新規事業評価フレームワーク待機中。",
        "どらのビジョン 3 フェーズ整合チェック準備完了。",
        "壁打ちモード待機中。いつでも構造化して返す。",
    ],
    "bridge": [
        "Discord ↔ Canvas ブリッジ全 18 チャンネル接続良好。",
        "HMAC 署名付きイベント転送システム稼働中。",
        "メッセージルーティング正常。部門間連携スタンバイ。",
    ],
    "phil_consulting": [
        "フィルコンサルカリキュラム STEP 0〜4 維持中。",
        "MD ファイルビジネス収集エンジン稼働。",
        "「猿でもわかる」翻訳エンジン待機中。",
    ],
    "security": [
        "9 項目セキュリティチェック自動監視中。",
        "50MB 超ファイル検知システム正常稼働。",
        "CVE 監視・依存パッケージ監視継続中。",
    ],
}

# ハートビートカウンター (メッセージをローテーションする)
_heartbeat_count: dict[str, int] = {c["dept_id"]: 0 for c in CHARACTERS}

# ---------------------------------------------------------------------------
# 改善4: 14部門パネルのボタンスタイル定義
# Discord は ButtonStyle 4色のみ: primary(blurple) / success(green) / secondary(grey) / danger(red)
# ---------------------------------------------------------------------------
DEPT_BUTTON_STYLE: dict[str, discord.ButtonStyle] = {
    "commander": discord.ButtonStyle.primary,
    "research": discord.ButtonStyle.success,
    "sales": discord.ButtonStyle.success,
    "design": discord.ButtonStyle.success,
    "content": discord.ButtonStyle.success,
    "writing": discord.ButtonStyle.success,
    "advertising": discord.ButtonStyle.success,
    "ai_investment": discord.ButtonStyle.secondary,
    "new_biz": discord.ButtonStyle.secondary,
    "phil_consulting": discord.ButtonStyle.secondary,
    "real_estate": discord.ButtonStyle.secondary,
    "takumi_x": discord.ButtonStyle.danger,
    "doradora_sns": discord.ButtonStyle.danger,
    "origin_story": discord.ButtonStyle.danger,
    "security": discord.ButtonStyle.primary,
    "bridge": discord.ButtonStyle.secondary,
}

# ---------------------------------------------------------------------------
# 改善3: チャンネルtopics定義
# ---------------------------------------------------------------------------
CHANNEL_TOPICS: dict[str, str] = {
    "司令塔": "どら → フィル → 各部門。全体指揮・戦略判断",
    "司令塔ログ": "Bot自動ログ・イベント記録",
    "会議室": "部門間連携・プロジェクト横断ミーティング",
    "一般": "雑談・お知らせ・フリートーク",
    "コンテンツ部": "台本・動画企画・3チャンネル管理",
    "デザイン部": "LP・スライド・ビジュアル制作",
    "ライティング部": "コピー・メルマガ・note・SNS文",
    "リサーチ部": "市場調査・競合分析・ソースチェーン",
    "営業部": "リード管理・商談・クロージング",
    "広告部": "FB/Instagram/X広告運用・A/Bテスト",
    "新規事業部": "新規事業企画・壁打ち",
    "AI投資部": "DeFi・Bot・コピートレード",
    "フィルコンサル部": "コンサル・カリキュラム・受講者対応",
    "不動産部": "不動産AI秘書・スクール企画",
    "タクミX部": "タクミX自動投稿・画像生成",
    "どらどらSNS部": "どら本人SNS運用",
    "コピーロボット部": "どらペルソナ管理・口調分析",
    "セキュリティ部": "セキュリティ監視・依存パッケージ管理",
    "📊ステータス": "部門稼働状況ダッシュボード（自動更新）",
    "📝日報": "フィルの日報(22:00自動) + どらの日報(/nippo)",
}

# ---------------------------------------------------------------------------
# 改善1: カテゴリ自動整理の構造定義
# ---------------------------------------------------------------------------
CATEGORY_STRUCTURE: dict[str, list[str]] = {
    "🏢 本部": ["一般", "司令塔", "📝日報"],
    "🤝 会議室": ["会議室"],
    "💼 事業部門": ["コンテンツ部", "デザイン部", "ライティング部", "リサーチ部", "営業部", "広告部"],
    "🚀 特殊部門": ["新規事業部", "AI投資部", "フィルコンサル部", "不動産部"],
    "📣 SNS・ブランディング": ["タクミX部", "どらどらSNS部", "コピーロボット部"],
    "📦 保管庫": ["📦ドラの成果物", "📦部門の成果物"],
    "🔧 システム管理": ["📊ステータス", "司令塔ログ", "セキュリティ部"],
}

# ---------------------------------------------------------------------------
# HMAC 署名ユーティリティ
# ---------------------------------------------------------------------------

def _sign_request(body: bytes, secret: str) -> tuple[str, str]:
    """
    HMAC-SHA256 で body に署名する。
    署名形式: sha256=<hex>
    signing input: "<unix_timestamp>.<body_bytes>"
    Returns: (signature_header_value, timestamp_str)
    """
    timestamp = str(int(time.time()))
    signing_input = f"{timestamp}.".encode("utf-8") + body
    mac = hmac.new(
        secret.encode("utf-8"),
        signing_input,
        hashlib.sha256,
    )
    signature = f"sha256={mac.hexdigest()}"
    return signature, timestamp


# ---------------------------------------------------------------------------
# TASK-06: aiohttp による非同期 Canvas API クライアント
# ---------------------------------------------------------------------------

# Bot 起動時に作成、close 時に cleanup する shared session
_aiohttp_session: aiohttp.ClientSession | None = None


def _get_aiohttp_session() -> aiohttp.ClientSession:
    """既存の aiohttp session を返す。未作成なら新規作成する。"""
    global _aiohttp_session
    if _aiohttp_session is None or _aiohttp_session.closed:
        _aiohttp_session = aiohttp.ClientSession()
    return _aiohttp_session


async def _close_aiohttp_session() -> None:
    """aiohttp session を安全に閉じる。"""
    global _aiohttp_session
    if _aiohttp_session and not _aiohttp_session.closed:
        await _aiohttp_session.close()
        _aiohttp_session = None
        log.info("aiohttp session closed.")


async def _post_external_event_async(payload: dict[str, Any]) -> bool:
    """
    Canvas の external_event API に HMAC 署名付きで非同期 POST する。
    Returns: True if 2xx, False otherwise
    """
    body = json.dumps(payload, ensure_ascii=True).encode("utf-8")
    signature, timestamp = _sign_request(body, HMAC_SECRET)

    url = f"{OFFICE_BASE_URL}/api/v1/external_event"
    headers = {
        "Content-Type": "application/json",
        "X-Bridge-Signature": signature,
        "X-Bridge-Timestamp": timestamp,
    }

    session = _get_aiohttp_session()
    try:
        async with session.post(
            url, data=body, headers=headers, timeout=aiohttp.ClientTimeout(total=10)
        ) as resp:
            if 200 <= resp.status < 300:
                return True
            body_text = await resp.text()
            log.warning("Canvas API returned HTTP %d: %s", resp.status, body_text[:200])
            return False
    except aiohttp.ClientResponseError as exc:
        log.error("Canvas API HTTP error %d: %s", exc.status, exc.message)
        return False
    except aiohttp.ClientConnectionError as exc:
        log.error("Canvas API connection error: %s", exc)
        return False
    except asyncio.TimeoutError:
        log.error("Canvas API request timed out.")
        return False
    except Exception as exc:
        log.error("Canvas API unexpected error: %s", exc)
        return False


async def send_canvas_event(
    dept_id: str,
    message: str,
    event_kind: str = "ASK_COMPLETED",
    session_id: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> bool:
    """
    Canvas に指定部門のイベントを非同期で送信する。
    aiohttp を使用してノンブロッキングで実行する。

    Args:
        dept_id: 部門ID
        message: Canvas に送るメッセージ本文
        event_kind: イベント種別 (ASK_COMPLETED / ASK_STARTED 等)
        session_id: Canvas セッションID (省略時はデフォルト)
        metadata: 追加メタデータ (添付ファイル情報など)
    """
    char = CHAR_BY_DEPT.get(dept_id, CHAR_BY_DEPT["bridge"])
    sid = session_id or CANVAS_SESSION_ID

    payload: dict[str, Any] = {
        "session_id": sid,
        "dept_id": dept_id,
        "display_name": char["display_name"],
        "agent_color": char["agent_color"],
        "event_kind": event_kind,
        "message": message[:3900],  # API の max_length=4000 に合わせてトリム
        "model": char.get("model", "sonnet"),
        "status": "ok",
    }
    if metadata:
        payload["metadata"] = metadata

    ok: bool = await _post_external_event_async(payload)
    if ok:
        log.debug("Canvas event sent: dept=%s kind=%s", dept_id, event_kind)
    return ok


# ---------------------------------------------------------------------------
# TASK-04: 添付ファイル処理ユーティリティ
# ---------------------------------------------------------------------------

# 対応拡張子
_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp"}
_PDF_EXTS = {".pdf"}
_TEXT_EXTS = {".md", ".txt"}
_VIDEO_EXTS = {".mp4", ".mov"}


async def _process_attachments(
    attachments: list[discord.Attachment],
) -> dict[str, Any]:
    """
    添付ファイルを解析して Canvas メタデータ用の dict を返す。

    Returns:
        metadata dict with keys: images, pdfs, texts, videos
    """
    metadata: dict[str, Any] = {}
    images = []
    pdfs = []
    texts = []
    videos = []

    session = _get_aiohttp_session()

    for att in attachments:
        ext = Path(att.filename).suffix.lower()

        if ext in _IMAGE_EXTS:
            images.append({"filename": att.filename, "url": att.url, "size": att.size})

        elif ext in _PDF_EXTS:
            pdfs.append({"filename": att.filename, "size": att.size, "url": att.url})

        elif ext in _TEXT_EXTS:
            # テキスト系は内容を読み込む
            try:
                async with session.get(
                    att.url, timeout=aiohttp.ClientTimeout(total=10)
                ) as resp:
                    raw = await resp.read()
                    text_content = raw.decode("utf-8", errors="replace")[:ATTACHMENT_TEXT_MAX]
                    texts.append({
                        "filename": att.filename,
                        "size": att.size,
                        "content": text_content,
                    })
            except Exception as exc:
                log.warning("Failed to fetch text attachment %s: %s", att.filename, exc)
                texts.append({"filename": att.filename, "size": att.size, "content": "(取得失敗)"})

        elif ext in _VIDEO_EXTS:
            videos.append({"filename": att.filename, "size": att.size, "url": att.url})

        else:
            # 未対応フォーマットはURL付きで記録のみ
            log.debug("Unsupported attachment format: %s", att.filename)

    if images:
        metadata["images"] = images
    if pdfs:
        metadata["pdfs"] = pdfs
    if texts:
        metadata["texts"] = texts
    if videos:
        metadata["videos"] = videos

    return metadata


def _format_attachment_summary(metadata: dict[str, Any]) -> str:
    """添付ファイルのサマリー文字列を生成する (Canvas メッセージ末尾に追記)。"""
    parts = []
    if "images" in metadata:
        for img in metadata["images"]:
            parts.append(f"[画像: {img['filename']}]")
    if "pdfs" in metadata:
        for pdf in metadata["pdfs"]:
            size_kb = pdf["size"] // 1024
            parts.append(f"[PDF: {pdf['filename']} ({size_kb}KB)]")
    if "texts" in metadata:
        for txt in metadata["texts"]:
            parts.append(f"[テキスト: {txt['filename']}]")
    if "videos" in metadata:
        for vid in metadata["videos"]:
            size_mb = vid["size"] // (1024 * 1024)
            parts.append(f"[動画: {vid['filename']} ({size_mb}MB)]")
    return " ".join(parts)


# ---------------------------------------------------------------------------
# 部門間メディア共有 (クロスポスト機能)
# ---------------------------------------------------------------------------

# キーワード → 転送先部門チャンネル名 のマッピング
_CROSS_POST_KEYWORDS: list[tuple[list[str], str]] = [
    (["台本", "コンテンツ", "動画企画", "シナリオ"], "コンテンツ部"),
    (["LP", "デザイン", "スライド", "バナー", "ビジュアル"], "デザイン部"),
    (["コピー", "広告文", "ライティング", "メルマガ", "セールスレター"], "ライティング部"),
    (["リサーチ", "調査", "市場分析", "ソースチェーン", "競合"], "リサーチ部"),
    (["広告", "キャンペーン", "FB広告", "Instagram広告", "A/Bテスト"], "広告部"),
    (["営業", "商談", "リード", "クロージング", "プレイブック"], "営業部"),
    (["セキュリティ", "脆弱性", "CVE", "依存パッケージ", "インシデント"], "セキュリティ部"),
]

# クロスポスト先の最大数 (スパム防止)
_CROSS_POST_MAX_DEPTS = 3

# 重複防止の時間窓 (秒)
_CROSS_POST_DEDUP_WINDOW = 300  # 5分


class SharedMediaTracker:
    """
    部門間クロスポストの重複送信を防止するトラッカー。
    同じ (送信元部門, 転送先部門, テキスト先頭100文字) の組み合わせを
    5分以内に再送しない。メモリ内辞書のみで管理 (永続化不要)。
    """

    def __init__(self) -> None:
        # key: (src_dept_id, dst_channel_name, text_hash) → unix timestamp
        self._seen: dict[tuple[str, str, str], float] = {}

    def _make_key(self, src_dept_id: str, dst_channel_name: str, text: str) -> tuple[str, str, str]:
        # テキストの先頭100文字を識別子として使う
        text_sig = text[:100].strip()
        return (src_dept_id, dst_channel_name, text_sig)

    def is_duplicate(self, src_dept_id: str, dst_channel_name: str, text: str) -> bool:
        """直近5分以内に同じ内容を送信済みなら True を返す。"""
        key = self._make_key(src_dept_id, dst_channel_name, text)
        now = time.time()
        last_sent = self._seen.get(key)
        if last_sent is not None and (now - last_sent) < _CROSS_POST_DEDUP_WINDOW:
            return True
        return False

    def mark_sent(self, src_dept_id: str, dst_channel_name: str, text: str) -> None:
        """送信済みとして記録する。"""
        key = self._make_key(src_dept_id, dst_channel_name, text)
        self._seen[key] = time.time()
        # 古いエントリを掃除 (1000件超えたら期限切れを削除)
        if len(self._seen) > 1000:
            cutoff = time.time() - _CROSS_POST_DEDUP_WINDOW
            self._seen = {k: v for k, v in self._seen.items() if v > cutoff}


# グローバルインスタンス (Bot 起動後に使い回す)
_shared_media_tracker = SharedMediaTracker()


async def cross_post_to_departments(
    guild: discord.Guild,
    src_dept_id: str,
    src_channel_name: str,
    text: str,
) -> int:
    """
    成果物テキストのキーワードを判定して、関連部門のチャンネルにクロスポストする。

    Args:
        guild: Discord Guild オブジェクト
        src_dept_id: 送信元の部門ID
        src_channel_name: 送信元チャンネル名 (embed の「続きは」リンク用)
        text: クロスポストする成果物テキスト

    Returns:
        実際に送信した部門数
    """
    # 送信元チャンネルと同じ部門名を転送先から除外するための逆引き
    src_channel_dept_name = RELAY_DEPT_TO_CHANNEL.get(src_dept_id, "")

    # キーワードで転送先を決定
    matched_channels: list[str] = []
    for keywords, dst_channel_name in _CROSS_POST_KEYWORDS:
        if dst_channel_name == src_channel_dept_name:
            continue  # 自分自身には送らない
        for kw in keywords:
            if kw in text:
                if dst_channel_name not in matched_channels:
                    matched_channels.append(dst_channel_name)
                break  # 1部門につき1ヒットで十分

    # 最大3部門に絞る
    matched_channels = matched_channels[:_CROSS_POST_MAX_DEPTS]

    if not matched_channels:
        return 0

    # 送信元キャラ情報
    src_char = CHAR_BY_DEPT.get(src_dept_id, CHAR_BY_DEPT.get("bridge"))
    if not src_char:
        return 0

    src_display = src_char["display_name"]
    src_color = src_char["discord_color"]

    # 転送本文: 最初の200文字 + 続きはリンク
    snippet = text[:200]
    if len(text) > 200:
        snippet += "..."
    footer_note = f"続きは #{src_channel_name} チャンネルで確認"

    sent_count = 0
    for dst_channel_name in matched_channels:
        # 重複チェック
        if _shared_media_tracker.is_duplicate(src_dept_id, dst_channel_name, text):
            log.debug(
                "クロスポストスキップ (重複): %s → %s", src_dept_id, dst_channel_name
            )
            continue

        # 転送先チャンネルを取得
        dst_channel_id = CHANNELS.get(dst_channel_name)
        if not dst_channel_id:
            log.debug("クロスポスト先チャンネルIDなし: %s", dst_channel_name)
            continue

        dst_ch = guild.get_channel(dst_channel_id)
        if not isinstance(dst_ch, discord.TextChannel):
            log.debug("クロスポスト先チャンネルが TextChannel でない: %s", dst_channel_name)
            continue

        # Embed 作成
        embed = discord.Embed(
            title=f"📨 {src_display} からの共有",
            description=snippet,
            color=src_color,
            timestamp=datetime.now(timezone.utc),
        )
        embed.add_field(name="参照元", value=footer_note, inline=False)
        embed.set_footer(text=f"部門間共有 | ミスターDオフィス × 伝書鳩")

        try:
            await dst_ch.send(embed=embed)
            _shared_media_tracker.mark_sent(src_dept_id, dst_channel_name, text)
            sent_count += 1
            log.info(
                "クロスポスト送信: %s → %s", src_dept_id, dst_channel_name
            )
        except discord.Forbidden:
            log.warning("権限不足: クロスポスト先 %s への送信失敗", dst_channel_name)
        except discord.HTTPException as exc:
            log.error("クロスポスト送信エラー (%s): %s", dst_channel_name, exc)

    return sent_count


# ---------------------------------------------------------------------------
# TASK-03: 14部門中継パネル — RelayPanelView (永続 View)
# ---------------------------------------------------------------------------

class RelayMessageModal(discord.ui.Modal):
    """部門へのメッセージ入力モーダル。ボタン押下後に表示される。"""

    message_input = discord.ui.TextInput(
        label="メッセージ",
        style=discord.TextStyle.paragraph,
        placeholder="部門へ送るメッセージを入力してください...",
        required=True,
        max_length=2000,
    )

    def __init__(self, dept_id: str, dept_display: str) -> None:
        super().__init__(title=f"{dept_display} へのメッセージ", timeout=120)
        self.dept_id = dept_id
        self.dept_display = dept_display

    async def on_submit(self, interaction: discord.Interaction) -> None:
        msg_text = self.message_input.value.strip()
        sender = interaction.user.display_name

        await interaction.response.defer(ephemeral=True)

        # 1. 該当部門チャンネルに転送
        channel_name = RELAY_DEPT_TO_CHANNEL.get(self.dept_id)
        forwarded_to_channel = False
        if channel_name and channel_name in CHANNELS:
            guild = interaction.guild
            if guild is not None:
                ch = guild.get_channel(CHANNELS[channel_name])
                if isinstance(ch, discord.TextChannel):
                    try:
                        char = CHAR_BY_DEPT.get(self.dept_id, CHAR_BY_DEPT["bridge"])
                        fwd_embed = discord.Embed(
                            title=f"{char['icon']} {channel_name} へのメッセージ",
                            description=msg_text,
                            color=char["discord_color"],
                            timestamp=datetime.now(timezone.utc),
                        )
                        fwd_embed.set_footer(text=f"差出人: {sender} | ミスターDオフィス × 伝書鳩")
                        await ch.send(embed=fwd_embed)
                        forwarded_to_channel = True
                    except discord.Forbidden:
                        log.warning("権限不足: %s チャンネルへの送信失敗", channel_name)
                    except discord.DiscordException as exc:
                        log.error("チャンネル転送エラー (%s): %s", channel_name, exc)

        # 2. Canvas に HMAC イベント送信
        canvas_msg = f"[中継パネル from {sender}] {msg_text}"
        ok = await send_canvas_event(
            self.dept_id, canvas_msg, event_kind="ASK_STARTED"
        )

        # 3. 送信者に確認メッセージ
        status_parts = []
        if forwarded_to_channel:
            status_parts.append(f"Discordチャンネル ({channel_name or '?'}) へ転送済み")
        status_parts.append("Canvas へ" + ("送信済み" if ok else "送信失敗 (Canvas 未接続)"))

        await interaction.followup.send(
            f"メッセージを送信しました。\n" + "\n".join(f"- {s}" for s in status_parts),
            ephemeral=True,
        )

        log.info(
            "RelayPanel: %s から %s へ転送 (channel_ok=%s, canvas_ok=%s)",
            sender, self.dept_id, forwarded_to_channel, ok,
        )

    async def on_error(self, interaction: discord.Interaction, error: Exception) -> None:
        log.error("RelayMessageModal error: %s", error)
        if not interaction.response.is_done():
            await interaction.response.send_message(
                "エラーが発生しました。もう一度お試しください。", ephemeral=True
            )


class RelayDeptButton(discord.ui.Button):
    """14部門パネルの各ボタン。custom_id = relay_{dept_id} 形式。"""

    def __init__(self, char: dict[str, Any]) -> None:
        dept_id = char["dept_id"]
        # 改善4: 部門グループごとにボタン色を変える
        style = DEPT_BUTTON_STYLE.get(dept_id, discord.ButtonStyle.secondary)
        super().__init__(
            label=char["display_name"],
            style=style,
            custom_id=f"relay_{dept_id}",
            emoji=None,  # display_name に絵文字が含まれるため不要
        )
        self.dept_id = dept_id
        self.dept_display = char["display_name"]

    async def callback(self, interaction: discord.Interaction) -> None:
        try:
            modal = RelayMessageModal(dept_id=self.dept_id, dept_display=self.dept_display)
            await interaction.response.send_modal(modal)
        except discord.NotFound:
            # 永続View の2重ハンドラ対策: 片方が先に応答済みなら黙殺
            pass
        except discord.HTTPException as exc:
            log.warning("RelayDeptButton callback error (%s): %s", self.dept_id, exc)


class RelayPanelView(discord.ui.View):
    """
    14部門中継パネルの永続 View。
    Bot 再起動後もボタンが動作するよう timeout=None + persistent=True で実装。
    bot.add_view(RelayPanelView()) で永続登録する。
    """

    def __init__(self) -> None:
        super().__init__(timeout=None)  # 永続View は timeout=None 必須
        for char in CHARACTERS:
            # bridge(伝書鳩自身) はパネルに出さない — 自分に送っても意味がない
            if char["dept_id"] == "bridge":
                continue
            self.add_item(RelayDeptButton(char))


# ---------------------------------------------------------------------------
# Discord Bot クラス
# ---------------------------------------------------------------------------

class CommanderBridgeBot(discord.Client):
    def __init__(self) -> None:
        intents = discord.Intents.default()
        intents.message_content = True
        intents.guilds = True
        intents.messages = True

        super().__init__(
            intents=intents,
        )

        self.tree = app_commands.CommandTree(self)
        self._heartbeat_task: asyncio.Task[None] | None = None
        self._guild: discord.Guild | None = None
        self._ready_event = asyncio.Event()

        self._shutdown_requested = False

        # --- 自律オフィス ---
        self._dept_brain: DeptBrain | None = None
        self._dept_scheduler: DeptScheduler | None = None
        self._meeting_engine: MeetingEngine | None = None
        self._meeting_scheduler: MeetingScheduler | None = None
        self._discord_logger: DiscordLogger | None = None

        # --- Phase 2: 指示実行エンジン ---
        self._instruction_engine: InstructionEngine | None = None

    # ------------------------------------------------------------------
    # ライフサイクル
    # ------------------------------------------------------------------

    async def setup_hook(self) -> None:
        """Bot 起動時に 1 回だけ呼ばれる。スラッシュコマンド登録と永続View 登録。"""
        # TASK-03: 永続 View を登録 (Bot 再起動後もボタンが動作する)
        self.add_view(RelayPanelView())
        log.info("RelayPanelView registered as persistent view.")

        guild = discord.Object(id=GUILD_ID)
        self.tree.copy_global_to(guild=guild)
        await self.tree.sync(guild=guild)
        log.info("Slash commands synced to guild %d", GUILD_ID)

    async def on_ready(self) -> None:
        log.info("伝書鳩: Logged in as %s (id=%d)", self.user, self.user.id)  # type: ignore[union-attr]

        await self.change_presence(
            status=discord.Status.online,
            activity=discord.Activity(
                type=discord.ActivityType.watching,
                name="Claude Office Canvas",
            ),
        )

        self._guild = self.get_guild(GUILD_ID)
        if self._guild is None:
            log.error("Guild %d not found. Check GUILD_ID.", GUILD_ID)
        else:
            log.info("Guild: %s (%d channels)", self._guild.name, len(self._guild.channels))

            # TASK-02: サーバー名を「ミスターDオフィス」に変更 (冪等)
            await self._ensure_guild_name("ミスターDオフィス")

            # 改善1・6・7: カテゴリ整理 + ステータス/日報チャンネル自動作成 (保管庫も統合)
            await self._ensure_server_structure()

            # 改善3: チャンネルtopic自動設定 (冪等)
            await self._ensure_channel_topics()

            # 改善5: ウェルカムembedをpinする (冪等)
            await self._send_welcome_embed()

            # 改善6: ステータスダッシュボードに静的部門一覧を投稿
            await self._post_startup_status()

        # 最初に全キャラを Canvas に送信してプレゼンスを確立する
        await self._announce_online()

        # ハートビートタスク起動
        if self._heartbeat_task is None or self._heartbeat_task.done():
            self._heartbeat_task = asyncio.create_task(
                self._heartbeat_loop(), name="canvas_heartbeat"
            )

        self._ready_event.set()

        # --- 自律オフィス初期化 ---
        await self._init_autonomous_office()

        # --- ログ記録エンジン (APIキー不要 — 常に動かす) ---
        try:
            self._discord_logger = DiscordLogger()
            log.info("Discordログ記録エンジン初期化完了")
        except Exception as exc:
            log.exception("Discordログ記録エンジン初期化エラー: %s", exc)

        # --- Phase 2: 指示実行エンジン初期化 ---
        try:
            if InstructionEngine is not None:
                self._instruction_engine = InstructionEngine()
                log.info("指示実行エンジン初期化完了 (Phase 2)")
            else:
                log.warning("InstructionEngine モジュール未ロード。Phase 2 無効。")
        except Exception as exc:
            log.exception("指示実行エンジン初期化エラー: %s", exc)

        log.info("伝書鳩: 完全起動完了。Heartbeat interval: %ds", HEARTBEAT_INTERVAL)

    # ------------------------------------------------------------------
    # 自律オフィス (REQ-001 ~ REQ-004)
    # ------------------------------------------------------------------

    async def _init_autonomous_office(self) -> None:
        """自律オフィスの全コンポーネントを初期化する。"""
        if not _AUTONOMOUS_AVAILABLE:
            log.warning("自律オフィスモジュールが利用できません。スキップ。")
            return

        api_key = os.environ.get("ANTHROPIC_API_KEY", "")
        if not api_key:
            log.warning("ANTHROPIC_API_KEY 未設定。部門AI返答は無効です。")
            return

        try:
            self._dept_brain = DeptBrain(api_key=api_key)
            log.info("DeptBrain 初期化完了")

            # スケジューラー (REQ-002: 自発活動)
            self._dept_scheduler = DeptScheduler(
                brain=self._dept_brain,
                post_callback=self._post_autonomous_activity,
                interval_hours=self._dept_brain.config.get("autonomous_interval_hours", 4),
            )
            self._dept_scheduler.start()
            log.info("部門自発活動スケジューラー開始")

            # 会議エンジン (REQ-003: 会議室)
            self._meeting_engine = MeetingEngine(
                brain=self._dept_brain,
                post_callback=self._post_meeting_message,
                forward_callback=self._forward_action_to_dept,
                canvas_callback=self._send_meeting_canvas_event,
            )

            # 定期会議スケジューラー
            schedule_hour = self._dept_brain.config.get("meeting_schedule_hour", 14)
            self._meeting_scheduler = MeetingScheduler(
                meeting_engine=self._meeting_engine,
                schedule_hour=schedule_hour,
            )
            self._meeting_scheduler.start()
            log.info("定期会議スケジューラー開始 (毎日 %02d:00 JST)", schedule_hour)

            # 日報エンジン (フィル日報22:00 + どら/nippoコマンド)
            self._daily_report = DailyReportEngine(
                brain=self._dept_brain,
                post_callback=self._post_daily_report,
                report_hour=22,
            )
            self._daily_report.start()
            log.info("日報エンジン開始 (フィル日報: 毎日 22:00 JST)")

        except Exception as exc:
            log.exception("自律オフィス初期化エラー: %s", exc)
            self._dept_brain = None

    async def _handle_dept_ai_response(
        self,
        message: discord.Message,
        dept_id: str,
        content: str,
        author_name: str,
    ) -> None:
        """部門AIがメッセージに返答する (REQ-001)。"""
        if not self._dept_brain:
            return

        try:
            # コンテキスト収集: チャンネルの直近5件
            context = []
            limit = self._dept_brain.config.get("context_history_limit", 5)
            async for hist_msg in message.channel.history(limit=limit + 1):
                if hist_msg.id == message.id:
                    continue
                role = "assistant" if hist_msg.author == self.user else "user"
                ctx_content = hist_msg.content or ""
                if hist_msg.embeds and not ctx_content:
                    # embed の description を取得
                    ctx_content = hist_msg.embeds[0].description or ""
                if ctx_content:
                    context.append({"role": role, "content": ctx_content})
            context.reverse()  # 古い順に

            # AI返答生成
            response = await self._dept_brain.respond(
                dept_id=dept_id,
                message=content,
                context=context,
                sender=author_name,
            )

            if not response:
                return

            # キャラ情報取得
            char = CHAR_BY_DEPT.get(dept_id, CHAR_BY_DEPT.get("bridge"))
            if not char:
                return

            # Embed形式で返答投稿
            embed = discord.Embed(
                description=response,
                color=char["discord_color"],
                timestamp=datetime.now(timezone.utc),
            )
            embed.set_author(
                name=f"{char['display_name']} (返答)",
            )
            embed.set_footer(text="ミスターDオフィス AI返答")

            await message.channel.send(embed=embed)

            # Canvas にも送信
            await send_canvas_event(
                dept_id,
                response[:200],
                event_kind="ASK_COMPLETED",
            )

            # 成果物判定 (REQ-004)
            if self._dept_brain.is_deliverable(response):
                await self._store_deliverable(
                    dept_id=dept_id,
                    text=response,
                    source_message=message,
                    is_relay=False,
                )

            # ログ記録
            if self._discord_logger:
                await self._discord_logger.log_conversation(
                    dept_id=dept_id,
                    channel=message.channel.name if hasattr(message.channel, 'name') else str(message.channel.id),
                    sender=author_name,
                    message=content,
                    response=response,
                    log_type="respond",
                )

            log.info(
                "部門AI返答: %s → %s (%.0f文字)",
                dept_id, author_name, len(response),
            )

        except Exception as exc:
            log.exception("AI返答エラー (%s): %s", dept_id, exc)

    async def _post_autonomous_activity(
        self, dept_id: str, text: str, is_deliverable: bool,
    ) -> None:
        """自発活動の投稿コールバック (REQ-002)。"""
        if not self._guild:
            return

        # チャンネル特定
        channel_name = RELAY_DEPT_TO_CHANNEL.get(dept_id)
        if not channel_name:
            return
        channel_id = CHANNELS.get(channel_name)
        if not channel_id:
            return
        channel = self._guild.get_channel(channel_id)
        if not isinstance(channel, discord.TextChannel):
            return

        char = CHAR_BY_DEPT.get(dept_id)
        if not char:
            return

        # Embed形式で投稿
        embed = discord.Embed(
            description=text,
            color=char["discord_color"],
            timestamp=datetime.now(timezone.utc),
        )
        embed.set_author(name=f"{char['display_name']} (自発活動)")
        embed.set_footer(text="ミスターDオフィス 自律活動")

        try:
            sent_msg = await channel.send(embed=embed)

            # Canvas反映
            await send_canvas_event(
                dept_id, text[:200], event_kind="ASK_COMPLETED",
            )

            # 成果物判定
            if is_deliverable:
                await self._store_deliverable(
                    dept_id=dept_id,
                    text=text,
                    source_message=sent_msg,
                    is_relay=False,
                )

            # ログ記録
            if self._discord_logger:
                await self._discord_logger.log_conversation(
                    dept_id=dept_id,
                    channel=channel_name,
                    sender="(自発活動)",
                    message="",
                    response=text,
                    log_type="autonomous",
                )

        except discord.HTTPException as exc:
            log.error("自発活動投稿エラー (%s): %s", dept_id, exc)

    async def _post_daily_report(
        self, text: str, title: str, is_phil: bool,
    ) -> None:
        """日報チャンネルへの投稿コールバック。"""
        if not self._guild:
            return

        channel_id = CHANNELS.get("📝日報")
        if not channel_id:
            return
        channel = self._guild.get_channel(channel_id)
        if not isinstance(channel, discord.TextChannel):
            return

        color = 0x58A6FF if is_phil else 0xD4AF37  # フィル=青, どら=金
        author_name = "フィル（司令塔）" if is_phil else "どらどら"

        embed = discord.Embed(
            title=title,
            description=text,
            color=color,
            timestamp=datetime.now(timezone.utc),
        )
        embed.set_author(name=f"{author_name} (日報)")
        embed.set_footer(text="ミスターDオフィス 日報")

        try:
            await channel.send(embed=embed)
        except discord.HTTPException as exc:
            log.error("日報投稿エラー: %s", exc)

    async def _post_meeting_message(
        self, text: str, dept_id: str, embed_title: str | None,
    ) -> None:
        """会議室への投稿コールバック (REQ-003)。"""
        if not self._guild:
            return

        channel_id = CHANNELS.get("会議室")
        if not channel_id:
            return
        channel = self._guild.get_channel(channel_id)
        if not isinstance(channel, discord.TextChannel):
            return

        char = CHAR_BY_DEPT.get(dept_id, CHAR_BY_DEPT.get("commander"))
        if not char:
            return

        embed = discord.Embed(
            description=text,
            color=char["discord_color"],
            timestamp=datetime.now(timezone.utc),
        )
        if embed_title:
            embed.title = embed_title
        embed.set_author(name=char["display_name"])
        embed.set_footer(text="ミスターDオフィス 会議室")

        try:
            sent_msg = await channel.send(embed=embed)

            # 成果物判定 (会議まとめは成果物)
            if self._dept_brain and self._dept_brain.is_deliverable(text):
                await self._store_deliverable(
                    dept_id=dept_id,
                    text=text,
                    source_message=sent_msg,
                    is_relay=False,
                )
        except discord.HTTPException as exc:
            log.error("会議投稿エラー: %s", exc)

    async def _forward_action_to_dept(self, dept_id: str, action_text: str) -> None:
        """アクションアイテムを部門チャンネルにフォワード。"""
        if not self._guild:
            return

        channel_name = RELAY_DEPT_TO_CHANNEL.get(dept_id)
        if not channel_name:
            return
        channel_id = CHANNELS.get(channel_name)
        if not channel_id:
            return
        channel = self._guild.get_channel(channel_id)
        if not isinstance(channel, discord.TextChannel):
            return

        char = CHAR_BY_DEPT.get("commander")
        if not char:
            return

        embed = discord.Embed(
            title="会議からのアクションアイテム",
            description=action_text,
            color=0xD4AF37,
            timestamp=datetime.now(timezone.utc),
        )
        embed.set_author(name="フィル（司令塔）")
        embed.set_footer(text="会議室 → 部門転送")

        try:
            await channel.send(embed=embed)
        except discord.HTTPException as exc:
            log.error("アクションフォワードエラー (%s): %s", dept_id, exc)

    async def _send_meeting_canvas_event(
        self, dept_id: str, message: str, event_kind: str,
    ) -> None:
        """会議中のCanvas通知。"""
        await send_canvas_event(dept_id, message, event_kind=event_kind)

    async def _store_deliverable(
        self,
        dept_id: str,
        text: str,
        source_message: discord.Message,
        is_relay: bool,
    ) -> None:
        """成果物を📦チャンネルにMDファイル付きで自動保管する (REQ-004)。"""
        if not self._guild:
            return

        # 保管先チャンネル決定
        storage_name = "📦ドラの成果物" if is_relay else "📦部門の成果物"
        storage_id = CHANNELS.get(storage_name)
        if not storage_id:
            return
        storage_ch = self._guild.get_channel(storage_id)
        if not isinstance(storage_ch, discord.TextChannel):
            return

        char = CHAR_BY_DEPT.get(dept_id, CHAR_BY_DEPT.get("bridge"))
        if not char:
            return

        # サマリーEmbed
        summary = text[:200] + ("..." if len(text) > 200 else "")
        jump_url = source_message.jump_url

        embed = discord.Embed(
            title=f"成果物: {char['display_name']}",
            description=summary,
            color=char["discord_color"],
            timestamp=datetime.now(timezone.utc),
        )
        embed.add_field(
            name="元メッセージ",
            value=f"[ジャンプ]({jump_url})",
            inline=True,
        )
        embed.add_field(
            name="チャンネル",
            value=source_message.channel.name if hasattr(source_message.channel, 'name') else "不明",
            inline=True,
        )
        embed.set_footer(text=f"自動保管 | {storage_name}")

        try:
            # 成果物全文をMDファイルとしてアップロード
            import tempfile
            now_str = datetime.now(timezone(timedelta(hours=9))).strftime("%Y%m%d_%H%M")
            dept_name = char.get("display_name", dept_id)
            filename = f"成果物_{dept_name}_{now_str}.md"

            with tempfile.NamedTemporaryFile(
                mode="w", suffix=".md", delete=False, encoding="utf-8",
            ) as tmp:
                tmp.write(f"# 成果物: {dept_name}\n\n")
                tmp.write(f"日時: {now_str}\n\n")
                tmp.write(f"---\n\n")
                tmp.write(text)
                tmp_path = tmp.name

            file = discord.File(tmp_path, filename=filename)
            await storage_ch.send(embed=embed, file=file)

            # 一時ファイル削除
            import os
            os.unlink(tmp_path)

            log.info("成果物保管 (ファイル付き): %s → %s [%s]", dept_id, storage_name, filename)
        except discord.HTTPException as exc:
            log.error("成果物保管エラー: %s", exc)

        # クロスポスト: 成果物を関連部門に共有 (is_relay=True の場合はえむ直接指示なので除外)
        if not is_relay and self._guild is not None:
            src_ch_name = (
                source_message.channel.name
                if hasattr(source_message.channel, "name")
                else RELAY_DEPT_TO_CHANNEL.get(dept_id, "")
            )
            asyncio.create_task(
                cross_post_to_departments(
                    guild=self._guild,
                    src_dept_id=dept_id,
                    src_channel_name=src_ch_name,
                    text=text,
                ),
                name=f"cross_post_{dept_id}",
            )

    # TASK-02: サーバー名変更 (冪等)
    async def _ensure_guild_name(self, target_name: str) -> None:
        """サーバー名が target_name と異なる場合のみ変更する。"""
        if self._guild is None:
            return
        if self._guild.name == target_name:
            log.info("サーバー名は既に '%s' です。変更不要。", target_name)
            return
        try:
            await self._guild.edit(name=target_name)
            log.info("サーバー名を '%s' に変更しました。", target_name)
        except discord.Forbidden:
            log.warning("MANAGE_GUILD 権限不足: サーバー名変更をスキップします。")
        except discord.HTTPException as exc:
            log.error("サーバー名変更エラー: %s", exc)

    # 改善1・6・7: カテゴリ整理 + 動的チャンネル自動作成 (保管庫統合版)
    async def _ensure_server_structure(self) -> None:
        """
        CATEGORY_STRUCTURE に従って全チャンネルを各カテゴリに整理する。
        カテゴリが存在しなければ作成。チャンネルが存在しなければ作成。
        既にカテゴリが正しければスキップ（冪等）。
        権限不足時はlog.warningして続行。

        重要: Discord はチャンネル名を自動的に小文字化する。
        例: "AI投資部" → "ai投資部", "タクミX部" → "タクミx部"
        そのため名前比較はすべて小文字で行い、重複チャンネルも自動削除する。
        """
        if self._guild is None:
            return

        # 既存カテゴリを名前 → CategoryChannel で引く
        cat_by_name: dict[str, discord.CategoryChannel] = {
            cat.name: cat for cat in self._guild.categories
        }

        # 既存テキストチャンネルを小文字名 → リストで引く
        # (重複があるので1対多でマッピング)
        existing_by_lower: dict[str, list[discord.TextChannel]] = {}
        for ch in self._guild.channels:
            if isinstance(ch, discord.TextChannel):
                key = ch.name.lower()
                existing_by_lower.setdefault(key, []).append(ch)

        for cat_name, ch_names in CATEGORY_STRUCTURE.items():
            # カテゴリ確保
            category = cat_by_name.get(cat_name)
            if category is None:
                try:
                    category = await self._guild.create_category(cat_name)
                    cat_by_name[cat_name] = category
                    log.info("カテゴリ「%s」を作成しました。", cat_name)
                except discord.Forbidden:
                    log.warning("権限不足: カテゴリ「%s」の作成をスキップします。", cat_name)
                    continue
                except discord.HTTPException as exc:
                    log.error("カテゴリ作成エラー (%s): %s", cat_name, exc)
                    continue

            for ch_name in ch_names:
                # Discord はチャンネル名を小文字化する → 小文字で検索
                ch_name_lower = ch_name.lower()
                matches = existing_by_lower.get(ch_name_lower, [])

                if matches:
                    # 最初の1つを正規チャンネルとして使用
                    existing_ch = matches[0]

                    # 重複チャンネルを削除 (2つ目以降)
                    if len(matches) > 1:
                        for dup_ch in matches[1:]:
                            try:
                                await dup_ch.delete()
                                log.info(
                                    "重複チャンネル「%s」(id=%d) を削除しました。",
                                    dup_ch.name, dup_ch.id,
                                )
                            except (discord.Forbidden, discord.HTTPException) as exc:
                                log.warning("重複チャンネル削除エラー (%s): %s", dup_ch.name, exc)
                        # リストを正規チャンネルのみに更新
                        existing_by_lower[ch_name_lower] = [existing_ch]

                    # 既存チャンネルのカテゴリが正しいか確認
                    if existing_ch.category_id != category.id:
                        try:
                            await existing_ch.edit(category=category)
                            log.info(
                                "チャンネル「%s」をカテゴリ「%s」へ移動しました。",
                                ch_name, cat_name,
                            )
                        except discord.Forbidden:
                            log.warning(
                                "権限不足: チャンネル「%s」のカテゴリ移動をスキップします。",
                                ch_name,
                            )
                        except discord.HTTPException as exc:
                            log.error("チャンネル移動エラー (%s): %s", ch_name, exc)

                    # CHANNELS dict を最新IDで更新（元のch_name＝大文字名で登録）
                    CHANNELS[ch_name] = existing_ch.id
                    CHANNEL_TO_DEPT[existing_ch.id] = ch_name
                    log.info("チャンネル確認済み: %s (id=%d)", ch_name, existing_ch.id)
                else:
                    # チャンネル新規作成
                    try:
                        new_ch = await self._guild.create_text_channel(
                            ch_name, category=category
                        )
                        CHANNELS[ch_name] = new_ch.id
                        CHANNEL_TO_DEPT[new_ch.id] = ch_name
                        existing_by_lower[ch_name_lower] = [new_ch]
                        log.info(
                            "チャンネル「%s」を作成しました (id=%d, category=%s)。",
                            ch_name, new_ch.id, cat_name,
                        )
                    except discord.Forbidden:
                        log.warning(
                            "権限不足: チャンネル「%s」の作成をスキップします。", ch_name
                        )
                    except discord.HTTPException as exc:
                        log.error("チャンネル作成エラー (%s): %s", ch_name, exc)

        # CHANNEL_TO_DEPT_ID の逆引きも再構築（小文字対応）
        CHANNEL_TO_DEPT.clear()
        for name, cid in CHANNELS.items():
            CHANNEL_TO_DEPT[cid] = name

        # 旧名チャンネル「📦えむの成果物」が残っていたら削除
        for ch in list(self._guild.channels):
            if isinstance(ch, discord.TextChannel) and ch.name == "📦えむの成果物":
                try:
                    await ch.delete()
                    log.info("旧チャンネル「📦えむの成果物」を削除しました。")
                except (discord.Forbidden, discord.HTTPException) as exc:
                    log.warning("旧チャンネル削除エラー: %s", exc)

        # 空カテゴリを削除（CATEGORY_STRUCTURE に含まれない旧カテゴリを掃除）
        managed_cat_names = set(CATEGORY_STRUCTURE.keys())
        for cat in list(self._guild.categories):
            if cat.name not in managed_cat_names and len(cat.channels) == 0:
                try:
                    await cat.delete()
                    log.info("空カテゴリ「%s」を削除しました。", cat.name)
                except discord.Forbidden:
                    log.warning("権限不足: 空カテゴリ「%s」の削除をスキップします。", cat.name)
                except discord.HTTPException as exc:
                    log.error("カテゴリ削除エラー (%s): %s", cat.name, exc)

    # 改善3: チャンネルtopic自動設定 (冪等)
    async def _ensure_channel_topics(self) -> None:
        """
        CHANNEL_TOPICS に従って各チャンネルの topic を設定する。
        既に同一 topic ならスキップ（冪等）。
        権限不足時はlog.warningして続行。
        """
        if self._guild is None:
            return

        for ch_name, topic in CHANNEL_TOPICS.items():
            ch_id = CHANNELS.get(ch_name)
            if ch_id is None:
                continue
            ch = self._guild.get_channel(ch_id)
            if not isinstance(ch, discord.TextChannel):
                continue
            if ch.topic == topic:
                continue
            try:
                await ch.edit(topic=topic)
                log.info("チャンネル「%s」のtopicを設定しました。", ch_name)
            except discord.Forbidden:
                log.warning("権限不足: チャンネル「%s」のtopic設定をスキップします。", ch_name)
            except discord.HTTPException as exc:
                log.error("チャンネルtopic設定エラー (%s): %s", ch_name, exc)

    # 改善5: ウェルカムembedを「一般」チャンネルにpin (冪等)
    async def _send_welcome_embed(self) -> None:
        """
        「一般」チャンネルに組織図embedをpinする。
        既にBotが送ったpinメッセージがあればスキップ（冪等）。
        """
        if self._guild is None:
            return

        ch_id = CHANNELS.get("一般")
        if ch_id is None:
            return
        ch = self._guild.get_channel(ch_id)
        if not isinstance(ch, discord.TextChannel):
            return

        # 既にpinがあるか確認
        try:
            pins = [msg async for msg in ch.pins()]
            for pin in pins:
                if pin.author == self.user and pin.embeds:
                    title = pin.embeds[0].title or ""
                    if "ミスターDオフィス" in title and "組織図" in title:
                        log.info("ウェルカムembedは既にpinされています。スキップします。")
                        return
        except discord.Forbidden:
            log.warning("権限不足: pinの確認/送信をスキップします。")
            return
        except discord.HTTPException as exc:
            log.error("pin確認エラー: %s", exc)
            return

        embed = discord.Embed(
            title="🏢 ミスターDオフィス — 組織図",
            description=(
                "**指揮系統**: どら → フィル（司令塔）→ 伝書鳩 → 各部門\n\n"
                "**【本部】** 司令塔 / 会議室\n"
                "**【事業】** コンテンツ / デザイン / ライティング / リサーチ / 営業 / 広告\n"
                "**【特殊】** 新規事業 / AI投資 / フィルコンサル / 不動産\n"
                "**【SNS】** タクミX / どらどらSNS / コピーロボット\n"
                "**【管理】** セキュリティ\n\n"
                "14部門パネル: 司令塔チャンネルで `/relay_panel`"
            ),
            color=0xD4AF37,
            timestamp=datetime.now(timezone.utc),
        )
        embed.set_footer(text="ミスターDオフィス × 伝書鳩")

        try:
            msg = await ch.send(embed=embed)
            await msg.pin()
            log.info("ウェルカムembedを「一般」チャンネルにpinしました。")
        except discord.Forbidden:
            log.warning("権限不足: ウェルカムembedのpinをスキップします。")
        except discord.HTTPException as exc:
            log.error("ウェルカムembed送信/pinエラー: %s", exc)

    # 改善6: ステータスダッシュボードに静的部門一覧を投稿
    async def _post_startup_status(self) -> None:
        """
        「📊ステータス」チャンネルに起動時の静的部門一覧embedを投稿する。
        shared_stateとの連携はPhase 2。今回は静的情報のみ。
        """
        if self._guild is None:
            return

        ch_id = CHANNELS.get("📊ステータス")
        if ch_id is None:
            log.warning("📊ステータスチャンネルが見つかりません。ステータス投稿をスキップします。")
            return
        ch = self._guild.get_channel(ch_id)
        if not isinstance(ch, discord.TextChannel):
            return

        now_jst = datetime.now(JST).strftime("%Y-%m-%d %H:%M JST")
        embed = discord.Embed(
            title="📊 ミスターDオフィス — 部門ステータス",
            description=f"最終起動: {now_jst}\n全 {len(CHARACTERS)} 部門スタンバイ中",
            color=0x00BCD4,
            timestamp=datetime.now(timezone.utc),
        )

        # 部門一覧をカテゴリ別に整理
        dept_groups = {
            "🏢 本部": ["commander"],
            "💼 事業部門": ["content", "design", "writing", "research", "sales", "advertising"],
            "🚀 特殊部門": ["new_biz", "ai_investment", "phil_consulting", "real_estate"],
            "📣 SNS・ブランディング": ["takumi_x", "doradora_sns", "origin_story"],
            "🔒 管理": ["security"],
        }

        for group_name, dept_ids in dept_groups.items():
            lines = []
            for dept_id in dept_ids:
                char = CHAR_BY_DEPT.get(dept_id)
                if char:
                    lines.append(f"{char['icon']} **{char['role']}** — `{dept_id}`")
            if lines:
                embed.add_field(name=group_name, value="\n".join(lines), inline=False)

        embed.set_footer(text="Phase 2でshared_state連携予定 | ミスターDオフィス × 伝書鳩")

        try:
            await ch.send(embed=embed)
            log.info("📊ステータスチャンネルに起動時ダッシュボードを投稿しました。")
        except discord.Forbidden:
            log.warning("権限不足: 📊ステータスへの投稿をスキップします。")
        except discord.HTTPException as exc:
            log.error("ステータス投稿エラー: %s", exc)

    async def on_disconnect(self) -> None:
        log.warning("伝書鳩: Discord から切断されました。再接続を試みます...")

    async def on_resumed(self) -> None:
        log.info("伝書鳩: 接続再開しました。")

    async def on_error(self, event: str, *args: Any, **kwargs: Any) -> None:
        log.exception("伝書鳩: イベント '%s' で未処理エラー発生", event)

    async def close(self) -> None:
        """グレースフルシャットダウン"""
        self._shutdown_requested = True
        if self._heartbeat_task and not self._heartbeat_task.done():
            self._heartbeat_task.cancel()
            try:
                await self._heartbeat_task
            except asyncio.CancelledError:
                pass
        # TASK-06: aiohttp session を cleanup
        await _close_aiohttp_session()
        await super().close()
        log.info("伝書鳩: 正常シャットダウン完了。")

    # ------------------------------------------------------------------
    # Canvas 通知
    # ------------------------------------------------------------------

    async def _announce_online(self) -> None:
        """起動時に全キャラを Canvas に表示する"""
        log.info("伝書鳩: 全キャラを Canvas に通知中 (session=%s)...", CANVAS_SESSION_ID)
        success = 0
        fail = 0
        for char in CHARACTERS:
            dept_id = char["dept_id"]
            msgs = HEARTBEAT_MESSAGES.get(dept_id, ["稼働中。"])
            msg = msgs[0]
            ok = await send_canvas_event(dept_id, msg, event_kind="ASK_COMPLETED")
            if ok:
                success += 1
            else:
                fail += 1
            await asyncio.sleep(0.15)  # API を叩きすぎない

        log.info("Canvas announce: %d OK, %d failed", success, fail)

    async def _heartbeat_loop(self) -> None:
        """
        定期的に全キャラを Canvas に再送信して TTL をリセットする。
        AgentSprite は一定時間イベントがないと消える可能性があるため。
        """
        while not self._shutdown_requested:
            try:
                await asyncio.sleep(HEARTBEAT_INTERVAL)
                if self._shutdown_requested:
                    break

                log.info("伝書鳩 ハートビート: %d キャラを Canvas に再送信...", len(CHARACTERS))
                now_jst = datetime.now(JST).strftime("%H:%M JST")
                success = 0

                for char in CHARACTERS:
                    dept_id = char["dept_id"]
                    msgs = HEARTBEAT_MESSAGES.get(dept_id, ["稼働中。"])
                    idx = _heartbeat_count.get(dept_id, 0) % len(msgs)
                    msg = f"[{now_jst}] {msgs[idx]}"
                    _heartbeat_count[dept_id] = idx + 1

                    ok = await send_canvas_event(
                        dept_id, msg, event_kind="ASK_COMPLETED"
                    )
                    if ok:
                        success += 1
                    await asyncio.sleep(0.1)

                log.info("ハートビート完了: %d/%d 更新", success, len(CHARACTERS))

            except asyncio.CancelledError:
                log.info("伝書鳩: ハートビートループ停止。")
                break
            except Exception:
                log.exception("ハートビートループエラー (次回インターバルで再試行)")

    # ------------------------------------------------------------------
    # TASK-04: メッセージイベント (添付ファイル処理を追加)
    # ------------------------------------------------------------------

    async def on_message(self, message: discord.Message) -> None:
        # 自分のメッセージは無視
        if message.author == self.user:
            return
        # Bot からのメッセージも基本無視 (Embed は転送しない)
        if message.author.bot:
            return

        channel_id = message.channel.id
        channel_name = CHANNEL_TO_DEPT.get(channel_id, "")

        content = (message.content or "").strip()

        # --- テキストコマンド ---
        if content in ("テスト", "test", "ping"):
            await self._cmd_test(message)
            return

        if content in ("ステータス", "status"):
            await self._cmd_status_text(message)
            return

        if content in ("ヘルプ", "help", "?", "！"):
            await self._cmd_help(message)
            return

        if content in ("部門一覧", "departments", "dept"):
            await self._cmd_dept_list(message)
            return

        # --- 部門チャンネルへの通常メッセージを Canvas に転送 + AI返答 ---
        if channel_name and channel_name in CHANNEL_TO_DEPT_ID:
            dept_id = CHANNEL_TO_DEPT_ID[channel_name]
            author_name = message.author.display_name

            # 添付ファイルを処理
            attachment_metadata: dict[str, Any] = {}
            attachment_summary = ""
            if message.attachments:
                attachment_metadata = await _process_attachments(message.attachments)
                attachment_summary = _format_attachment_summary(attachment_metadata)
                log.debug(
                    "添付ファイル処理: %s から %d 件 (channel=%s)",
                    author_name, len(message.attachments), channel_name,
                )

            # テキストメッセージの組み立て
            forward_parts = [f"[Discord/{channel_name}] {author_name}: {content}"]
            if attachment_summary:
                forward_parts.append(attachment_summary)

            # テキスト系添付の内容をメッセージに含める
            if "texts" in attachment_metadata:
                for txt in attachment_metadata["texts"]:
                    forward_parts.append(
                        f"\n--- {txt['filename']} ---\n{txt['content']}"
                    )

            forward_msg = "\n".join(forward_parts)

            # Canvas転送
            asyncio.create_task(
                send_canvas_event(
                    dept_id,
                    forward_msg,
                    event_kind="ASK_STARTED",
                    metadata=attachment_metadata if attachment_metadata else None,
                ),
                name=f"fwd_{dept_id}",
            )
            log.debug(
                "伝書鳩: %s → Canvas dept=%s (attachments=%d)",
                channel_name, dept_id, len(message.attachments),
            )

            # --- 自律オフィス: 部門AI返答 (REQ-001) ---
            if self._dept_brain and content:
                asyncio.create_task(
                    self._handle_dept_ai_response(
                        message, dept_id, content, author_name,
                    ),
                    name=f"ai_reply_{dept_id}",
                )

    # ------------------------------------------------------------------
    # テキストコマンド実装
    # ------------------------------------------------------------------

    async def _cmd_test(self, message: discord.Message) -> None:
        """テスト: Bot が生きているかの確認"""
        now_jst = datetime.now(JST).strftime("%Y-%m-%d %H:%M:%S JST")
        embed = discord.Embed(
            title="伝書鳩 — 接続テスト",
            description="Bot は正常稼働中です。",
            color=0xD4AF37,
            timestamp=datetime.now(timezone.utc),
        )
        embed.add_field(name="ステータス", value="オンライン", inline=True)
        embed.add_field(name="Canvas", value=f"`{OFFICE_BASE_URL}`", inline=True)
        embed.add_field(name="Session", value=f"`{CANVAS_SESSION_ID}`", inline=True)
        embed.add_field(name="時刻 (JST)", value=now_jst, inline=False)
        embed.set_footer(text="ミスターDオフィス × 伝書鳩")
        await message.reply(embed=embed, mention_author=False)

        # Canvas にも通知
        asyncio.create_task(
            send_canvas_event(
                "bridge",
                f"Discord テストコマンド受信 from {message.author.display_name}",
                event_kind="ASK_COMPLETED",
            )
        )

    async def _cmd_status_text(self, message: discord.Message) -> None:
        """ステータス: 全部門の稼働状況"""
        embed = _build_status_embed()
        await message.reply(embed=embed, mention_author=False)

    async def _cmd_help(self, message: discord.Message) -> None:
        """ヘルプ: 使い方一覧"""
        embed = _build_help_embed()
        await message.reply(embed=embed, mention_author=False)

    async def _cmd_dept_list(self, message: discord.Message) -> None:
        """部門一覧: キャラクター一覧を表示"""
        embed = _build_dept_list_embed()
        await message.reply(embed=embed, mention_author=False)


# ---------------------------------------------------------------------------
# Embed ビルダー (コマンド共通)
# ---------------------------------------------------------------------------

def _build_status_embed() -> discord.Embed:
    now_jst = datetime.now(JST).strftime("%Y-%m-%d %H:%M JST")
    embed = discord.Embed(
        title="伝書鳩 — システムステータス",
        description=f"現在時刻: {now_jst}",
        color=0xD4AF37,
        timestamp=datetime.now(timezone.utc),
    )
    embed.add_field(
        name="Discord Bot",
        value="オンライン",
        inline=True,
    )
    embed.add_field(
        name="Canvas API",
        value=f"`{OFFICE_BASE_URL}`",
        inline=True,
    )
    embed.add_field(
        name="Session ID",
        value=f"`{CANVAS_SESSION_ID}`",
        inline=True,
    )
    embed.add_field(
        name="ハートビート間隔",
        value=f"{HEARTBEAT_INTERVAL}秒 ({HEARTBEAT_INTERVAL // 60}分)",
        inline=True,
    )
    embed.add_field(
        name="Canvas キャラ数",
        value=str(len(CHARACTERS)),
        inline=True,
    )
    embed.add_field(
        name="監視チャンネル数",
        value=str(len(CHANNELS)),
        inline=True,
    )
    embed.set_footer(text="ミスターDオフィス × 伝書鳩")
    return embed


def _build_help_embed() -> discord.Embed:
    embed = discord.Embed(
        title="伝書鳩 — コマンド一覧",
        description="Discord ↔ Canvas ブリッジシステムの使い方",
        color=0x95A5A6,
        timestamp=datetime.now(timezone.utc),
    )
    embed.add_field(
        name="テキストコマンド",
        value=(
            "`テスト` — 接続確認\n"
            "`ステータス` — システム状況\n"
            "`ヘルプ` — この一覧\n"
            "`部門一覧` — 全キャラクター一覧"
        ),
        inline=False,
    )
    embed.add_field(
        name="スラッシュコマンド",
        value=(
            "`/status` — システムステータス\n"
            "`/ask [部門] [質問]` — 指定部門に質問\n"
            "`/bridge [メッセージ]` — Canvas に直接ブリッジ送信\n"
            "`/relay_panel` — 14部門中継パネル表示"
        ),
        inline=False,
    )
    embed.add_field(
        name="自動機能",
        value=(
            f"- 部門チャンネルのメッセージ → Canvas 自動転送\n"
            f"- 添付ファイル対応 (画像/PDF/MD/txt/動画)\n"
            f"- {HEARTBEAT_INTERVAL // 60}分ごとに Canvas ハートビート送信\n"
            f"- 保管庫チャンネル自動管理"
        ),
        inline=False,
    )
    embed.set_footer(text="ミスターDオフィス × 伝書鳩")
    return embed


def _build_dept_list_embed() -> discord.Embed:
    embed = discord.Embed(
        title="伝書鳩 — 部門キャラクター一覧",
        description="Canvas に常駐している部門エージェント",
        color=0x3498DB,
        timestamp=datetime.now(timezone.utc),
    )
    lines = []
    for char in CHARACTERS:
        icon = char["display_name"].split()[0]
        name = char["display_name"].split(" ", 1)[1] if " " in char["display_name"] else char["display_name"]
        color_hex = char["agent_color"]
        model = char.get("model", "sonnet")
        lines.append(f"{icon} **{name}** — `{char['dept_id']}` [{model}]")
    embed.add_field(name="エージェント", value="\n".join(lines), inline=False)
    embed.set_footer(text="ミスターDオフィス × 伝書鳩")
    return embed


# ---------------------------------------------------------------------------
# スラッシュコマンド登録
# ---------------------------------------------------------------------------
_bot: CommanderBridgeBot | None = None


def _get_bot() -> CommanderBridgeBot:
    global _bot
    assert _bot is not None
    return _bot


def _register_slash_commands(bot: CommanderBridgeBot) -> None:
    tree = bot.tree
    guild_obj = discord.Object(id=GUILD_ID)

    @tree.command(
        name="status",
        description="伝書鳩のシステムステータスを表示",
        guild=guild_obj,
    )
    async def slash_status(interaction: discord.Interaction) -> None:
        embed = _build_status_embed()
        await interaction.response.send_message(embed=embed)

        asyncio.create_task(
            send_canvas_event(
                "commander",
                f"/status コマンド受信 from {interaction.user.display_name}",
                event_kind="ASK_COMPLETED",
            )
        )

    @tree.command(
        name="ask",
        description="指定した部門のエージェントに質問する",
        guild=guild_obj,
    )
    @app_commands.describe(
        dept="部門を選択 (commander/research/sales/design 等)",
        question="質問内容",
    )
    async def slash_ask(
        interaction: discord.Interaction,
        dept: str,
        question: str,
    ) -> None:
        await interaction.response.defer(thinking=True)

        dept_clean = dept.strip().lower()
        char = CHAR_BY_DEPT.get(dept_clean)

        if char is None:
            dept_list = ", ".join(CHAR_BY_DEPT.keys())
            await interaction.followup.send(
                f"部門 `{dept_clean}` は見つかりません。\n"
                f"有効な部門: {dept_list}",
                ephemeral=True,
            )
            return

        canvas_msg = f"/ask from {interaction.user.display_name}: {question}"
        ok = await send_canvas_event(
            dept_clean, canvas_msg, event_kind="ASK_STARTED"
        )

        embed = discord.Embed(
            title=f"{char['display_name']} への質問",
            description=f"**質問**: {question}",
            color=char["discord_color"],
            timestamp=datetime.now(timezone.utc),
        )
        embed.add_field(
            name="Canvas 転送",
            value="送信済み" if ok else "Canvas 未接続 (ローカルで確認を)",
            inline=True,
        )
        embed.add_field(name="部門 ID", value=f"`{dept_clean}`", inline=True)
        embed.set_footer(text=f"質問者: {interaction.user.display_name}")
        await interaction.followup.send(embed=embed)

        async def _complete_later() -> None:
            await asyncio.sleep(3)
            await send_canvas_event(
                dept_clean,
                f"[応答] {question[:100]}... (Discord /ask から転送)",
                event_kind="ASK_COMPLETED",
            )

        asyncio.create_task(_complete_later(), name="ask_complete")

    @tree.command(
        name="bridge",
        description="指定メッセージを Canvas ブリッジ経由で全部門に配信",
        guild=guild_obj,
    )
    @app_commands.describe(message="Canvas に配信するメッセージ")
    async def slash_bridge(
        interaction: discord.Interaction,
        message: str,
    ) -> None:
        await interaction.response.defer(thinking=True)

        sender = interaction.user.display_name
        full_msg = f"[Bridge from {sender}] {message}"

        ok = await send_canvas_event(
            "commander", full_msg, event_kind="ASK_COMPLETED"
        )

        now_jst = datetime.now(JST).strftime("%H:%M JST")
        embed = discord.Embed(
            title="Bridge 配信",
            description=full_msg,
            color=0xD4AF37,
            timestamp=datetime.now(timezone.utc),
        )
        embed.add_field(name="Canvas 送信", value="OK" if ok else "失敗", inline=True)
        embed.add_field(name="時刻", value=now_jst, inline=True)
        embed.set_footer(text=f"送信者: {sender}")
        await interaction.followup.send(embed=embed)

        # 司令塔ログチャンネルにも記録
        if bot._guild is not None:
            log_ch = bot._guild.get_channel(CHANNELS["司令塔ログ"])
            if isinstance(log_ch, discord.TextChannel):
                log_embed = discord.Embed(
                    title="Bridge ログ",
                    description=full_msg,
                    color=0x00FF88,
                    timestamp=datetime.now(timezone.utc),
                )
                log_embed.set_footer(text=f"by {sender} | ミスターDオフィス × 伝書鳩")
                try:
                    await log_ch.send(embed=log_embed)
                except discord.Forbidden:
                    log.warning("権限不足: 司令塔ログへの送信失敗")
                except discord.DiscordException as exc:
                    log.warning("司令塔ログ送信エラー: %s", exc)

    @tree.command(
        name="relay_panel",
        description="14部門中継パネルを表示する（どら専用）",
        guild=guild_obj,
    )
    async def slash_relay_panel(interaction: discord.Interaction) -> None:
        """14部門中継パネルを表示する。ボタンを押すとモーダルが開く。"""
        embed = discord.Embed(
            title="14部門 中継パネル",
            description=(
                "送信したい部門のボタンを押してください。\n"
                "メッセージ入力欄が開きます。"
            ),
            color=0xD4AF37,
            timestamp=datetime.now(timezone.utc),
        )
        embed.set_footer(text="ミスターDオフィス × 伝書鳩 | どら専用指示ツール")

        # 永続 View を使用 (timeout=None)
        view = RelayPanelView()
        await interaction.response.send_message(embed=embed, view=view)

    # --- 自律オフィス スラッシュコマンド ---

    @tree.command(
        name="meeting",
        description="会議室で部門横断ディスカッションを開始する",
        guild=guild_obj,
    )
    @app_commands.describe(topic="議題（例: AI投資の方向性）")
    async def slash_meeting(
        interaction: discord.Interaction,
        topic: str,
    ) -> None:
        if not bot._meeting_engine:
            await interaction.response.send_message(
                "会議機能が初期化されていません。ANTHROPIC_API_KEYを確認してください。",
                ephemeral=True,
            )
            return

        if bot._meeting_engine.is_active:
            await interaction.response.send_message(
                "既に会議が進行中です。完了を待ってください。",
                ephemeral=True,
            )
            return

        await interaction.response.send_message(
            f"会議を開始します: 「{topic}」\n参加部門を自動選択中...",
        )

        # 会議は長時間かかるので非同期タスクで実行
        asyncio.create_task(
            bot._meeting_engine.start_meeting(topic),
            name="meeting_task",
        )

    @tree.command(
        name="silence",
        description="部門自発活動の一時停止/再開",
        guild=guild_obj,
    )
    @app_commands.describe(mode="on: 停止 / off: 再開")
    async def slash_silence(
        interaction: discord.Interaction,
        mode: str,
    ) -> None:
        if not bot._dept_brain:
            await interaction.response.send_message(
                "自律機能が初期化されていません。",
                ephemeral=True,
            )
            return

        enabled = mode.lower() in ("on", "1", "true", "yes", "停止")
        bot._dept_brain.set_silence(enabled)

        status = "停止" if enabled else "再開"
        await interaction.response.send_message(
            f"部門自発活動を **{status}** しました。",
        )

    @tree.command(
        name="nippo",
        description="どらの日報を投稿する",
        guild=guild_obj,
    )
    @app_commands.describe(text="今日やったこと・決めたこと・明日やること")
    async def slash_nippo(
        interaction: discord.Interaction,
        text: str,
    ) -> None:
        if not hasattr(bot, '_daily_report') or not bot._daily_report:
            await interaction.response.send_message(
                "日報機能が初期化されていません。",
                ephemeral=True,
            )
            return

        await interaction.response.send_message(
            "📝 どらの日報を投稿します...",
            ephemeral=True,
        )
        await bot._daily_report.post_dora_report(text)
        await interaction.followup.send(
            "✅ 日報を投稿しました。📝日報チャンネルを確認してください。",
            ephemeral=True,
        )

    @tree.command(
        name="phil_nippo",
        description="フィルの日報を今すぐ生成する（手動トリガー）",
        guild=guild_obj,
    )
    async def slash_phil_nippo(interaction: discord.Interaction) -> None:
        if not hasattr(bot, '_daily_report') or not bot._daily_report:
            await interaction.response.send_message(
                "日報機能が初期化されていません。",
                ephemeral=True,
            )
            return

        await interaction.response.send_message(
            "📝 フィルの日報を生成中...",
        )
        result = await bot._daily_report.generate_phil_report()
        if not result:
            await interaction.followup.send("日報生成に失敗しました。")

    # ------------------------------------------------------------------
    # ログ閲覧コマンド (Phase 1: P-11, P-12)
    # ------------------------------------------------------------------
    @tree.command(
        name="dept_log",
        description="部門の活動ログを表示する",
        guild=guild_obj,
    )
    @app_commands.describe(dept="部門名（例: デザイン部）", count="表示件数（デフォルト10）")
    async def slash_dept_log(
        interaction: discord.Interaction,
        dept: str | None = None,
        count: int = 10,
    ) -> None:
        if not hasattr(bot, '_discord_logger') or not bot._discord_logger:
            await interaction.response.send_message(
                "ログ機能が初期化されていません。", ephemeral=True,
            )
            return

        # 部門IDの特定（チャンネル名 or 部門名から）
        dept_id = None
        if dept:
            # チャンネル名→部門IDの逆引き
            for d, ch in RELAY_DEPT_TO_CHANNEL.items():
                if dept in ch or (AI_DEPT_PROMPTS and dept in AI_DEPT_PROMPTS.get(d, {}).get("name", "")):
                    dept_id = d
                    break
        if not dept_id:
            # 現在のチャンネルから推定
            ch_id = interaction.channel_id
            dept_id = CHANNEL_TO_DEPT.get(ch_id)

        if not dept_id:
            await interaction.response.send_message(
                "部門を特定できません。部門名を指定するか、部門チャンネルで実行してください。",
                ephemeral=True,
            )
            return

        logs = await bot._discord_logger.get_dept_log(dept_id, limit=count)
        if not logs:
            await interaction.response.send_message(
                f"部門 {dept_id} の活動ログはまだありません。", ephemeral=True,
            )
            return

        # サマリー取得
        summary = await bot._discord_logger.get_today_summary(dept_id)

        lines = [f"**本日のサマリー**: {summary}", ""]
        for entry in logs[-count:]:
            ts = entry.get("timestamp", "??:??")
            log_type = entry.get("type", "?")
            sender = entry.get("sender", "")
            msg = entry.get("message", "")[:50]
            resp = entry.get("response", "")[:50]

            type_label = {"respond": "返答", "autonomous": "自発", "meeting": "会議", "instruction": "指示"}.get(log_type, log_type)

            if log_type == "autonomous":
                lines.append(f"`{ts}` ({type_label}) {resp}")
            else:
                lines.append(f"`{ts}` ({type_label}) {sender}: {msg}")
                if resp:
                    lines.append(f"  → {resp}")

        embed = discord.Embed(
            title=f"活動ログ: {dept_id}",
            description="\n".join(lines)[:4000],
            color=0x58A6FF,
            timestamp=datetime.now(timezone.utc),
        )
        embed.set_footer(text="ミスターDオフィス ログ")
        await interaction.response.send_message(embed=embed)

    @tree.command(
        name="instruction_log",
        description="指示トラッキングログを表示する",
        guild=guild_obj,
    )
    @app_commands.describe(count="表示件数（デフォルト10）")
    async def slash_instruction_log(
        interaction: discord.Interaction,
        count: int = 10,
    ) -> None:
        if not hasattr(bot, '_discord_logger') or not bot._discord_logger:
            await interaction.response.send_message(
                "ログ機能が初期化されていません。", ephemeral=True,
            )
            return

        instructions = await bot._discord_logger.get_instruction_log(limit=count)
        if not instructions:
            await interaction.response.send_message(
                "指示ログはまだありません。", ephemeral=True,
            )
            return

        lines = []
        for inst in instructions:
            inst_id = inst.get("id", "?")
            ts = inst.get("timestamp", "??:??")
            dept = inst.get("dept_id", "?")
            instruction = inst.get("instruction", "")[:60]
            status = inst.get("status", "?")

            status_emoji = {
                "queued": "⏳",
                "executing": "🔄",
                "completed": "✅",
                "failed": "❌",
                "cancelled": "🚫",
            }.get(status, "❓")

            lines.append(f"{status_emoji} `[{inst_id}]` {ts} → {dept}")
            lines.append(f"  {instruction}")
            if inst.get("result"):
                lines.append(f"  結果: {inst['result'][:50]}")
            lines.append("")

        embed = discord.Embed(
            title="指示トラッキング",
            description="\n".join(lines)[:4000],
            color=0xD4AF37,
            timestamp=datetime.now(timezone.utc),
        )
        embed.set_footer(text="ミスターDオフィス 指示追跡")
        await interaction.response.send_message(embed=embed)

    # ------------------------------------------------------------------
    # ステータスコマンド
    # ------------------------------------------------------------------
    @tree.command(
        name="brain_status",
        description="部門AI頭脳のステータスを確認する",
        guild=guild_obj,
    )
    async def slash_brain_status(interaction: discord.Interaction) -> None:
        if not bot._dept_brain:
            await interaction.response.send_message(
                "自律機能が初期化されていません。",
                ephemeral=True,
            )
            return

        brain = bot._dept_brain
        embed = discord.Embed(
            title="DeptBrain ステータス",
            color=0xD4AF37,
            timestamp=datetime.now(timezone.utc),
        )
        embed.add_field(
            name="月間コスト",
            value=f"${brain.monthly_cost:.2f} / ${brain.config['monthly_cost_cap_usd']:.0f}",
            inline=True,
        )
        embed.add_field(
            name="静音モード",
            value="ON" if brain._silence_mode else "OFF",
            inline=True,
        )
        embed.add_field(
            name="会議中",
            value="はい" if brain._meeting_active else "いいえ",
            inline=True,
        )
        embed.add_field(
            name="モデル",
            value=brain.config["api_model"],
            inline=True,
        )
        embed.add_field(
            name="レートリミット",
            value=f"{brain.config['rate_limit_per_minute']}回/分",
            inline=True,
        )
        embed.set_footer(text="ミスターDオフィス × 自律オフィス")
        await interaction.response.send_message(embed=embed)

    # ===================================================================
    # Phase 2: 指示実行コマンド
    # ===================================================================

    @tree.command(
        name="run",
        description="部門AIにタスクを実行させる（Claude Codeが実際に動く）",
        guild=guild_obj,
    )
    @app_commands.describe(
        dept="部門を選択 (research/design/content/writing 等)",
        task="やらせたいこと（例: DeFi最新動向を調査してレポートにまとめて）",
    )
    async def slash_run(
        interaction: discord.Interaction,
        dept: str,
        task: str,
    ) -> None:
        engine = bot._instruction_engine
        if engine is None:
            await interaction.response.send_message(
                "指示実行エンジンが初期化されていません。",
                ephemeral=True,
            )
            return

        dept_clean = dept.strip().lower()
        char = CHAR_BY_DEPT.get(dept_clean)
        if char is None:
            dept_list = ", ".join(CHAR_BY_DEPT.keys())
            await interaction.response.send_message(
                f"部門 `{dept_clean}` は見つかりません。\n有効な部門: {dept_list}",
                ephemeral=True,
            )
            return

        # 安全性チェック
        classification = engine.classify(task)

        if classification == "forbidden":
            await interaction.response.send_message(
                f"その操作は安全上の理由で禁止されています。\n"
                f"**指示**: {task[:100]}",
                ephemeral=True,
            )
            return

        if classification == "approval_required":
            # 承認待ちキューに入れる
            inst_id = engine.queue_for_approval(
                dept_clean, task, interaction.user.display_name,
            )
            embed = discord.Embed(
                title="承認待ち",
                description=(
                    f"**部門**: {char['display_name']}\n"
                    f"**指示**: {task[:200]}\n"
                    f"**ID**: `{inst_id}`\n\n"
                    f"どらが `/approve {inst_id}` で承認すると実行されます。"
                ),
                color=0xF39C12,
            )
            embed.set_footer(text="Phase 2 指示実行エンジン")
            await interaction.response.send_message(embed=embed)

            # 司令塔ログにも通知
            cmd_log_ch = discord.utils.get(
                bot._guild.text_channels if bot._guild else [],
                name="司令塔ログ",
            )
            if cmd_log_ch:
                await cmd_log_ch.send(
                    f"承認待ちタスク: `{inst_id}` "
                    f"({char['display_name']})\n指示: {task[:100]}"
                )
            return

        # safe — 自動実行
        await interaction.response.defer(thinking=True)

        jst = timezone(timedelta(hours=9))
        now = datetime.now(jst)
        inst_id = f"INST-{now.strftime('%m%d%H%M')}"

        # 受付メッセージ
        await interaction.followup.send(
            f"実行開始: `{inst_id}` ({char['display_name']})\n"
            f"**タスク**: {task[:100]}\n"
            f"完了したらここに報告します。",
        )

        # バックグラウンドで実行
        asyncio.create_task(
            _run_and_report(bot, engine, dept_clean, task, inst_id, interaction.channel),
            name=f"run_{inst_id}",
        )

    @tree.command(
        name="approve",
        description="承認待ちタスクを承認して実行する（どら専用）",
        guild=guild_obj,
    )
    @app_commands.describe(
        inst_id="承認する指示ID (例: INST-04231200-001)",
    )
    async def slash_approve(
        interaction: discord.Interaction,
        inst_id: str,
    ) -> None:
        engine = bot._instruction_engine
        if engine is None:
            await interaction.response.send_message(
                "指示実行エンジンが初期化されていません。",
                ephemeral=True,
            )
            return

        inst = engine.get_queued(inst_id)
        if inst is None:
            # 承認待ち一覧を表示
            pending = engine.get_all_pending()
            if not pending:
                await interaction.response.send_message(
                    "承認待ちのタスクはありません。",
                    ephemeral=True,
                )
            else:
                lines = []
                for pid, pdata in pending:
                    lines.append(
                        f"`{pid}` — {pdata['dept_id']}: {pdata['task'][:60]}"
                    )
                await interaction.response.send_message(
                    f"`{inst_id}` が見つかりません。\n\n"
                    f"**承認待ち一覧:**\n" + "\n".join(lines),
                    ephemeral=True,
                )
            return

        if inst["status"] != "pending":
            await interaction.response.send_message(
                f"`{inst_id}` は既に処理済みです (status: {inst['status']})",
                ephemeral=True,
            )
            return

        # 承認して実行
        engine.mark_approved(inst_id)
        dept_clean = inst["dept_id"]
        task = inst["task"]
        char = CHAR_BY_DEPT.get(dept_clean, {"display_name": dept_clean})

        await interaction.response.defer(thinking=True)
        await interaction.followup.send(
            f"承認完了。`{inst_id}` を実行します。\n"
            f"**部門**: {char.get('display_name', dept_clean)}\n"
            f"**タスク**: {task[:100]}",
        )

        asyncio.create_task(
            _run_and_report(bot, engine, dept_clean, task, inst_id, interaction.channel),
            name=f"approve_{inst_id}",
        )

    @tree.command(
        name="pending",
        description="承認待ちタスクの一覧を表示",
        guild=guild_obj,
    )
    async def slash_pending(interaction: discord.Interaction) -> None:
        engine = bot._instruction_engine
        if engine is None:
            await interaction.response.send_message(
                "指示実行エンジンが初期化されていません。",
                ephemeral=True,
            )
            return

        pending = engine.get_all_pending()
        if not pending:
            await interaction.response.send_message(
                "承認待ちのタスクはありません。",
                ephemeral=True,
            )
            return

        embed = discord.Embed(
            title="承認待ちタスク一覧",
            color=0xF39C12,
        )
        for pid, pdata in pending:
            char = CHAR_BY_DEPT.get(pdata["dept_id"], {})
            embed.add_field(
                name=f"`{pid}` — {char.get('display_name', pdata['dept_id'])}",
                value=f"{pdata['task'][:100]}\n要求者: {pdata['requester']}",
                inline=False,
            )
        embed.set_footer(text="`/approve INST-ID` で承認・実行")
        await interaction.response.send_message(embed=embed)

    @tree.command(
        name="engine_status",
        description="指示実行エンジンのステータスを表示",
        guild=guild_obj,
    )
    async def slash_engine_status(interaction: discord.Interaction) -> None:
        engine = bot._instruction_engine
        if engine is None:
            await interaction.response.send_message(
                "指示実行エンジンが初期化されていません。",
                ephemeral=True,
            )
            return

        status = engine.get_status()
        embed = discord.Embed(
            title="Phase 2 指示実行エンジン",
            color=0x3498DB,
        )
        embed.add_field(
            name="月間コスト",
            value=f"${status['monthly_cost']:.2f} / ${status['monthly_cap']:.2f}",
            inline=True,
        )
        embed.add_field(
            name="実行回数",
            value=str(status['execution_count']),
            inline=True,
        )
        embed.add_field(
            name="承認待ち",
            value=str(status['pending_approvals']),
            inline=True,
        )
        embed.set_footer(text="ミスターDオフィス × Phase 2")
        await interaction.response.send_message(embed=embed)


# ---------------------------------------------------------------------------
# Phase 2: バックグラウンド実行ヘルパー
# ---------------------------------------------------------------------------

async def _run_and_report(
    bot: CommanderBridgeBot,
    engine: "InstructionEngine",
    dept_id: str,
    task: str,
    inst_id: str,
    channel: discord.TextChannel | discord.abc.Messageable,
) -> None:
    """バックグラウンドでclaude -pを実行し、結果をDiscordに投稿する。"""
    try:
        result = await engine.execute(task, dept_id)

        # 完了マーク
        engine.mark_completed(inst_id, result)

        char = CHAR_BY_DEPT.get(dept_id, {})
        char_name = char.get("display_name", dept_id)

        if result["status"] == "success":
            # 成功
            result_text = result.get("result", "")
            if len(result_text) > 1800:
                result_text = result_text[:1800] + "\n... (省略)"

            embed = discord.Embed(
                title=f"実行完了: {inst_id}",
                description=result_text or "(結果なし)",
                color=0x2ECC71,
            )
            embed.add_field(
                name="部門", value=char_name, inline=True,
            )
            embed.add_field(
                name="コスト", value=f"${result.get('cost', 0):.4f}", inline=True,
            )
            embed.add_field(
                name="所要時間",
                value=f"{result.get('duration_sec', 0):.1f}秒",
                inline=True,
            )
        else:
            # 失敗 / タイムアウト
            embed = discord.Embed(
                title=f"実行失敗: {inst_id}",
                description=(
                    f"**ステータス**: {result['status']}\n"
                    f"**エラー**: {result.get('error', '不明')}\n"
                    f"**部門**: {char_name}"
                ),
                color=0xE74C3C,
            )

        embed.set_footer(text="Phase 2 指示実行エンジン")
        await channel.send(embed=embed)

        # 司令塔ログにも記録
        if bot._guild:
            cmd_log_ch = discord.utils.get(
                bot._guild.text_channels, name="司令塔ログ",
            )
            if cmd_log_ch and cmd_log_ch != channel:
                status_icon = {
                    "success": "done",
                    "error": "NG",
                    "timeout": "TIMEOUT",
                    "budget_exceeded": "BUDGET OVER",
                }.get(result["status"], result["status"])
                await cmd_log_ch.send(
                    f"`{inst_id}` [{status_icon}] {char_name}: {task[:60]} "
                    f"(${result.get('cost', 0):.4f}, {result.get('duration_sec', 0):.1f}s)"
                )

    except Exception as exc:
        log.exception("_run_and_report エラー: %s", exc)
        try:
            await channel.send(
                f"`{inst_id}` 実行中にエラーが発生しました: {exc}"
            )
        except Exception:
            pass


# ---------------------------------------------------------------------------
# エントリポイント
# ---------------------------------------------------------------------------

async def _main() -> None:
    global _bot

    if not BOT_TOKEN:
        log.error(
            "DISCORD_BOT_TOKEN が設定されていません。.env を確認してください。\n"
            "ファイルパス: %s",
            _ENV_FILE,
        )
        sys.exit(1)

    if not HMAC_SECRET:
        log.error("EXTERNAL_EVENT_SECRET が設定されていません。")
        sys.exit(1)

    log.info("=" * 60)
    log.info("伝書鳩 Discord Bot 起動")
    log.info("  Canvas URL   : %s", OFFICE_BASE_URL)
    log.info("  Session ID   : %s", CANVAS_SESSION_ID)
    log.info("  Heartbeat    : %ds", HEARTBEAT_INTERVAL)
    log.info("  Guild ID     : %d", GUILD_ID)
    log.info("  Characters   : %d", len(CHARACTERS))
    log.info("  Autonomous   : %s", "available" if _AUTONOMOUS_AVAILABLE else "unavailable")
    log.info("  Anthropic Key: %s", "set" if os.environ.get("ANTHROPIC_API_KEY") else "NOT SET")
    log.info("=" * 60)

    # TASK-06: aiohttp session をBot起動前に作成
    _get_aiohttp_session()
    log.info("aiohttp session initialized.")

    _bot = CommanderBridgeBot()
    _register_slash_commands(_bot)

    loop = asyncio.get_running_loop()

    def _handle_signal(sig: signal.Signals) -> None:
        log.info("Signal %s received. Shutting down...", sig.name)
        loop.create_task(_bot.close())  # type: ignore[union-attr]

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _handle_signal, sig)
        except NotImplementedError:
            pass

    try:
        await _bot.start(BOT_TOKEN, reconnect=True)
    except discord.LoginFailure:
        log.error("ログイン失敗: Bot Token が無効です。")
        sys.exit(1)
    except discord.PrivilegedIntentsRequired:
        log.error(
            "Privileged Intents (MESSAGE CONTENT) が有効になっていません。\n"
            "Discord Developer Portal > Bot > Privileged Gateway Intents で有効化してください。"
        )
        sys.exit(1)
    except asyncio.CancelledError:
        pass
    finally:
        if not _bot.is_closed():
            await _bot.close()
        # セッションが残っていたら cleanup
        await _close_aiohttp_session()


if __name__ == "__main__":
    try:
        asyncio.run(_main())
    except KeyboardInterrupt:
        log.info("伝書鳩: Keyboard interrupt. Bye.")
