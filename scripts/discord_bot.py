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
- えむ指示の成果物 → 📦えむの成果物
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
            if key and key not in os.environ:
                os.environ[key] = val


try:
    from dotenv import load_dotenv
    load_dotenv(_ENV_FILE)
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
    # 保管庫チャンネルは on_ready で動的追加される
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
    "コピーロボット部": "bridge",
    "営業部": "sales",
    "広告部": "advertising",
    "フィルコンサル部": "phil_consulting",
    "タクミX部": "bridge",
    "AI投資部": "ai_investment",
    "不動産部": "bridge",
    "どらどらSNS部": "bridge",
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
    "bridge": "コピーロボット部",
}

# ハートビートで使うステータスメッセージ (循環してマンネリを防ぐ)
HEARTBEAT_MESSAGES: dict[str, list[str]] = {
    "commander": [
        "全部門の稼働状況を確認中。Canvas 接続良好。",
        "えむの就寝中も全部門スタンバイ。",
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
        "えむのビジョン 3 フェーズ整合チェック準備完了。",
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
        super().__init__(
            label=char["display_name"],
            style=discord.ButtonStyle.secondary,
            custom_id=f"relay_{dept_id}",
            emoji=None,  # display_name に絵文字が含まれるため不要
        )
        self.dept_id = dept_id
        self.dept_display = char["display_name"]

    async def callback(self, interaction: discord.Interaction) -> None:
        modal = RelayMessageModal(dept_id=self.dept_id, dept_display=self.dept_display)
        await interaction.response.send_modal(modal)


class RelayPanelView(discord.ui.View):
    """
    14部門中継パネルの永続 View。
    Bot 再起動後もボタンが動作するよう timeout=None + persistent=True で実装。
    bot.add_view(RelayPanelView()) で永続登録する。
    """

    def __init__(self) -> None:
        super().__init__(timeout=None)  # 永続View は timeout=None 必須
        for char in CHARACTERS:
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

            # TASK-05: 保管庫チャンネル自動作成
            await self._ensure_storage_channels()

        # 最初に全キャラを Canvas に送信してプレゼンスを確立する
        await self._announce_online()

        # ハートビートタスク起動
        if self._heartbeat_task is None or self._heartbeat_task.done():
            self._heartbeat_task = asyncio.create_task(
                self._heartbeat_loop(), name="canvas_heartbeat"
            )

        self._ready_event.set()
        log.info("伝書鳩: 完全起動完了。Heartbeat interval: %ds", HEARTBEAT_INTERVAL)

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

    # TASK-05: 保管庫チャンネル自動作成
    async def _ensure_storage_channels(self) -> None:
        """
        📦えむの成果物 / 📦部門の成果物 チャンネルが存在しなければ作成する。
        カテゴリ「保管庫」がなければ作成してから入れる。
        作成後は CHANNELS dict に動的追加する。
        """
        if self._guild is None:
            return

        storage_channels = ["📦えむの成果物", "📦部門の成果物"]
        existing_names = {ch.name for ch in self._guild.channels}

        # 保管庫カテゴリの確保
        storage_category: discord.CategoryChannel | None = None
        for cat in self._guild.categories:
            if cat.name == "保管庫":
                storage_category = cat
                break

        if storage_category is None:
            try:
                storage_category = await self._guild.create_category("保管庫")
                log.info("カテゴリ「保管庫」を作成しました。")
            except discord.Forbidden:
                log.warning("権限不足: カテゴリ「保管庫」の作成をスキップします。")
            except discord.HTTPException as exc:
                log.error("カテゴリ作成エラー: %s", exc)

        for ch_name in storage_channels:
            if ch_name in existing_names:
                # 既存チャンネルの ID を CHANNELS に追加
                existing_ch = discord.utils.get(self._guild.channels, name=ch_name)
                if existing_ch is not None and ch_name not in CHANNELS:
                    CHANNELS[ch_name] = existing_ch.id
                    CHANNEL_TO_DEPT[existing_ch.id] = ch_name
                    log.info("保管庫チャンネル確認済み: %s (id=%d)", ch_name, existing_ch.id)
                continue
            try:
                kwargs: dict[str, Any] = {}
                if storage_category is not None:
                    kwargs["category"] = storage_category
                new_ch = await self._guild.create_text_channel(ch_name, **kwargs)
                CHANNELS[ch_name] = new_ch.id
                CHANNEL_TO_DEPT[new_ch.id] = ch_name
                log.info("保管庫チャンネル作成: %s (id=%d)", ch_name, new_ch.id)
            except discord.Forbidden:
                log.warning("権限不足: チャンネル %s の作成をスキップします。", ch_name)
            except discord.HTTPException as exc:
                log.error("チャンネル作成エラー (%s): %s", ch_name, exc)

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

        # --- 部門チャンネルへの通常メッセージを Canvas に転送 ---
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
        description="14部門中継パネルを表示する（えむ専用）",
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
        embed.set_footer(text="ミスターDオフィス × 伝書鳩 | えむ専用指示ツール")

        # 永続 View を使用 (timeout=None)
        view = RelayPanelView()
        await interaction.response.send_message(embed=embed, view=view)


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
