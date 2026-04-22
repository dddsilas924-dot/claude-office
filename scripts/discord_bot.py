#!/usr/bin/env python3
"""
Commander Bridge — Discord Bot
==============================
常駐型 Discord Bot。えむが寝ている間も Empire Monitor の各部門キャラを
Canvas 上に生き続けさせ、Discord チャンネルのメッセージを Canvas へ転送する。

機能:
1. スラッシュコマンド: /status /ask /bridge
2. テキストコマンド: テスト, ステータス, ヘルプ, 部門一覧
3. Discord → Canvas メッセージ転送 (HMAC 署名付き external_event API)
4. Canvas ハートビート (5 分ごとに全キャラ再送信で TTL をリセット)
5. 自動再接続 / グレースフルシャットダウン

使い方:
    pip install discord.py python-dotenv
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


def _post_external_event(payload: dict[str, Any]) -> bool:
    """
    Canvas の external_event API に HMAC 署名付きで POST する。
    Returns: True if 2xx, False otherwise
    """
    import urllib.error
    import urllib.request

    body = json.dumps(payload, ensure_ascii=True).encode("utf-8")
    signature, timestamp = _sign_request(body, HMAC_SECRET)

    url = f"{OFFICE_BASE_URL}/api/v1/external_event"
    req = urllib.request.Request(
        url,
        data=body,
        headers={
            "Content-Type": "application/json",
            "X-Bridge-Signature": signature,
            "X-Bridge-Timestamp": timestamp,
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            status = resp.status
            if 200 <= status < 300:
                return True
            log.warning("Canvas API returned HTTP %d", status)
            return False
    except urllib.error.HTTPError as exc:
        body_text = exc.read(200).decode("utf-8", errors="replace")
        log.error("Canvas API HTTP error %d: %s", exc.code, body_text)
        return False
    except urllib.error.URLError as exc:
        log.error("Canvas API URL error: %s", exc.reason)
        return False
    except Exception as exc:
        log.error("Canvas API unexpected error: %s", exc)
        return False


async def send_canvas_event(
    dept_id: str,
    message: str,
    event_kind: str = "ASK_COMPLETED",
    session_id: str | None = None,
) -> bool:
    """
    Canvas に指定部門のイベントを非同期で送信する。
    ブロッキング I/O を executor で実行して event loop をブロックしない。
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

    loop = asyncio.get_running_loop()
    ok: bool = await loop.run_in_executor(None, _post_external_event, payload)
    if ok:
        log.debug("Canvas event sent: dept=%s kind=%s", dept_id, event_kind)
    return ok


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
            # 自動シャードは小規模 guild なので不要
        )

        self.tree = app_commands.CommandTree(self)
        self._heartbeat_task: asyncio.Task[None] | None = None
        self._guild: discord.Guild | None = None
        self._ready_event = asyncio.Event()

        # シグナルハンドラ登録 (Ctrl+C / SIGTERM でグレースフルシャットダウン)
        self._shutdown_requested = False

    # ------------------------------------------------------------------
    # ライフサイクル
    # ------------------------------------------------------------------

    async def setup_hook(self) -> None:
        """Bot 起動時に 1 回だけ呼ばれる。スラッシュコマンドを登録。"""
        guild = discord.Object(id=GUILD_ID)
        # グローバル sync より guild sync の方が即時反映される
        self.tree.copy_global_to(guild=guild)
        await self.tree.sync(guild=guild)
        log.info("Slash commands synced to guild %d", GUILD_ID)

    async def on_ready(self) -> None:
        log.info("Logged in as %s (id=%d)", self.user, self.user.id)  # type: ignore[union-attr]

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

        # 最初に全キャラを Canvas に送信してプレゼンスを確立する
        await self._announce_online()

        # ハートビートタスク起動
        if self._heartbeat_task is None or self._heartbeat_task.done():
            self._heartbeat_task = asyncio.create_task(
                self._heartbeat_loop(), name="canvas_heartbeat"
            )

        self._ready_event.set()
        log.info("Bot is fully ready. Heartbeat interval: %ds", HEARTBEAT_INTERVAL)

    async def on_disconnect(self) -> None:
        log.warning("Disconnected from Discord. Will attempt reconnect...")

    async def on_resumed(self) -> None:
        log.info("Connection resumed.")

    async def on_error(self, event: str, *args: Any, **kwargs: Any) -> None:
        log.exception("Unhandled error in event '%s'", event)

    async def close(self) -> None:
        """グレースフルシャットダウン"""
        self._shutdown_requested = True
        if self._heartbeat_task and not self._heartbeat_task.done():
            self._heartbeat_task.cancel()
            try:
                await self._heartbeat_task
            except asyncio.CancelledError:
                pass
        await super().close()
        log.info("Bot shut down cleanly.")

    # ------------------------------------------------------------------
    # Canvas 通知
    # ------------------------------------------------------------------

    async def _announce_online(self) -> None:
        """起動時に全キャラを Canvas に表示する"""
        log.info("Announcing all characters to Canvas (session=%s)...", CANVAS_SESSION_ID)
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

                log.info("Heartbeat: refreshing %d characters on Canvas...", len(CHARACTERS))
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

                log.info("Heartbeat done: %d/%d refreshed", success, len(CHARACTERS))

            except asyncio.CancelledError:
                log.info("Heartbeat loop cancelled.")
                break
            except Exception:
                log.exception("Heartbeat loop error (will retry next interval)")

    # ------------------------------------------------------------------
    # メッセージイベント
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
            forward_msg = (
                f"[Discord/{channel_name}] {author_name}: {content}"
            )
            asyncio.create_task(
                send_canvas_event(dept_id, forward_msg, event_kind="ASK_STARTED"),
                name=f"fwd_{dept_id}",
            )
            log.debug("Forwarding message from %s to Canvas dept=%s", channel_name, dept_id)

    # ------------------------------------------------------------------
    # テキストコマンド実装
    # ------------------------------------------------------------------

    async def _cmd_test(self, message: discord.Message) -> None:
        """テスト: Bot が生きているかの確認"""
        now_jst = datetime.now(JST).strftime("%Y-%m-%d %H:%M:%S JST")
        embed = discord.Embed(
            title="Commander Bridge — 接続テスト",
            description="Bot は正常稼働中です。",
            color=0xD4AF37,
            timestamp=datetime.now(timezone.utc),
        )
        embed.add_field(name="ステータス", value="オンライン", inline=True)
        embed.add_field(name="Canvas", value=f"`{OFFICE_BASE_URL}`", inline=True)
        embed.add_field(name="Session", value=f"`{CANVAS_SESSION_ID}`", inline=True)
        embed.add_field(name="時刻 (JST)", value=now_jst, inline=False)
        embed.set_footer(text="Claude Office × Commander Bridge")
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
        title="Commander Bridge — システムステータス",
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
    embed.set_footer(text="Claude Office × Commander Bridge")
    return embed


def _build_help_embed() -> discord.Embed:
    embed = discord.Embed(
        title="Commander Bridge — コマンド一覧",
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
            "`/bridge [メッセージ]` — Canvas に直接ブリッジ送信"
        ),
        inline=False,
    )
    embed.add_field(
        name="自動機能",
        value=(
            f"• 部門チャンネルのメッセージ → Canvas 自動転送\n"
            f"• {HEARTBEAT_INTERVAL // 60}分ごとに Canvas ハートビート送信"
        ),
        inline=False,
    )
    embed.set_footer(text="Claude Office × Commander Bridge")
    return embed


def _build_dept_list_embed() -> discord.Embed:
    embed = discord.Embed(
        title="Commander Bridge — 部門キャラクター一覧",
        description="Canvas に常駐している部門エージェント",
        color=0x3498DB,
        timestamp=datetime.now(timezone.utc),
    )
    lines = []
    for char in CHARACTERS:
        icon = char["display_name"].split()[0]  # 絵文字部分
        name = char["display_name"].split(" ", 1)[1] if " " in char["display_name"] else char["display_name"]
        color_hex = char["agent_color"]
        model = char.get("model", "sonnet")
        lines.append(f"{icon} **{name}** — `{char['dept_id']}` [{model}]")
    embed.add_field(name="エージェント", value="\n".join(lines), inline=False)
    embed.set_footer(text="Claude Office × Commander Bridge")
    return embed


# ---------------------------------------------------------------------------
# スラッシュコマンド登録
# ---------------------------------------------------------------------------
# Bot インスタンスを module レベルで持つことで setup_hook 内から参照する
_bot: CommanderBridgeBot | None = None


def _get_bot() -> CommanderBridgeBot:
    global _bot
    assert _bot is not None
    return _bot


# スラッシュコマンドはインスタンスに紐づけるため、Bot 生成後に登録する
def _register_slash_commands(bot: CommanderBridgeBot) -> None:
    tree = bot.tree
    guild_obj = discord.Object(id=GUILD_ID)

    @tree.command(
        name="status",
        description="Commander Bridge のシステムステータスを表示",
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

        # Canvas にイベント送信
        canvas_msg = (
            f"/ask from {interaction.user.display_name}: {question}"
        )
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

        # 3 秒後に完了イベントも送る (ASK_STARTED → ASK_COMPLETED)
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

        # commander チャンネルにも全体ブリッジ通知
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
                log_embed.set_footer(text=f"by {sender}")
                try:
                    await log_ch.send(embed=log_embed)
                except discord.DiscordException as exc:
                    log.warning("Failed to send to 司令塔ログ: %s", exc)

    # bot をクロージャ内で参照するため、ここで代入
    bot_ref = bot  # noqa: F841


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
    log.info("Commander Bridge Discord Bot 起動")
    log.info("  Canvas URL   : %s", OFFICE_BASE_URL)
    log.info("  Session ID   : %s", CANVAS_SESSION_ID)
    log.info("  Heartbeat    : %ds", HEARTBEAT_INTERVAL)
    log.info("  Guild ID     : %d", GUILD_ID)
    log.info("  Characters   : %d", len(CHARACTERS))
    log.info("=" * 60)

    _bot = CommanderBridgeBot()
    _register_slash_commands(_bot)

    # SIGINT / SIGTERM でグレースフルシャットダウン
    loop = asyncio.get_running_loop()

    def _handle_signal(sig: signal.Signals) -> None:
        log.info("Signal %s received. Shutting down...", sig.name)
        loop.create_task(_bot.close())  # type: ignore[union-attr]

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _handle_signal, sig)
        except NotImplementedError:
            # Windows では add_signal_handler が使えないので KeyboardInterrupt で対応
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


if __name__ == "__main__":
    try:
        asyncio.run(_main())
    except KeyboardInterrupt:
        log.info("Keyboard interrupt. Bye.")
