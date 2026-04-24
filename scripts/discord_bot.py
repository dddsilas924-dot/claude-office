#!/usr/bin/env python3
"""
伝書鳩 — Discord Bot
====================
常駐型 Discord Bot。ドラが寝ている間も Empire Monitor の各部門キャラを
Canvas 上に生き続けさせ、Discord チャンネルのメッセージを Canvas へ転送する。

アーキテクチャルール:
- Discord = 部門が自律的に動く場。ドラは審査員
- Office (Canvas) = 視覚的に見える場
- Claude Code = ドラが直接指示する場（別世界線）

指揮系統:
- 基本: ドラ → フィル（司令塔）→ 伝書鳩 → 各部門
- 例外: 外部イレギュラーは直接部門に届く
- 14部門パネル: ドラ専用の指示ツール

成果物:
- ドラ指示の成果物 → 📦ドラの成果物
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
    EXTERNAL_EVENT_SECRET=your-secret-here

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
import io
import re
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

# Phase 3: 品質・記録・部門間依頼機能
try:
    from state_watcher import StateWatcher
    from memory_bridge import MemoryBridge
    from notification_controller import NotificationController, NotificationLevel
    from dept_request import DeptRequestManager
    from daily_digest import DigestCollector, DigestScheduler
    _PHASE3_AVAILABLE = True
except ImportError:
    _PHASE3_AVAILABLE = False
    StateWatcher = None  # type: ignore[assignment,misc]
    MemoryBridge = None  # type: ignore[assignment,misc]
    NotificationController = None  # type: ignore[assignment,misc]
    NotificationLevel = None  # type: ignore[assignment,misc]
    DeptRequestManager = None  # type: ignore[assignment,misc]
    DigestCollector = None  # type: ignore[assignment,misc]
    DigestScheduler = None  # type: ignore[assignment,misc]

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
HMAC_SECRET: str = os.environ.get("EXTERNAL_EVENT_SECRET", "")
if not HMAC_SECRET:
    log.warning("EXTERNAL_EVENT_SECRET 未設定。Canvas連携は無効です。.env に設定してください。")
OFFICE_BASE_URL: str = os.environ.get("OFFICE_BASE_URL", "http://localhost:8000").rstrip("/")
CANVAS_SESSION_ID: str = os.environ.get("CANVAS_SESSION_ID", "bridge_live")
HEARTBEAT_INTERVAL: int = int(os.environ.get("HEARTBEAT_INTERVAL", "300"))  # 秒
GUILD_ID: int = int(os.environ.get("DISCORD_SERVER_ID", os.environ.get("DISCORD_GUILD_ID", "0")))
JST = timezone(timedelta(hours=9))

# ---------------------------------------------------------------------------
# Phase 4: アバター・UX・安全系 定数
# ---------------------------------------------------------------------------
_SPRITES_DIR: Path = Path(__file__).parent.parent / "frontend" / "public" / "sprites" / "characters"

# dept_id → スプライトファイル名のオーバーライド（dept_id.png と一致しない場合のみ）
_AVATAR_OVERRIDE: dict[str, str] = {
    "takumi_x": "char_takumi.png",
    "doradora_sns": "commander.png",  # 専用アバターなし → 司令塔で代用
    "development": "bridge.png",  # 開発部 → Bridge画像で代用
}

# APIコスト上限（1日あたり / 月あたり）— 顧客に使い放題させない安全装置
DAILY_API_COST_LIMIT: float = float(os.environ.get("DAILY_API_COST_LIMIT", "5.0"))   # $5/日
MONTHLY_API_COST_LIMIT: float = float(os.environ.get("MONTHLY_API_COST_LIMIT", "100.0"))  # $100/月

# メッセージカウンター（/usage用）
_msg_counter: dict[str, int] = {}  # dept_id → 今日のメッセージ数
_msg_counter_date: str = ""  # YYYY-MM-DD

# 朝ブリーフィング時刻
MORNING_BRIEFING_HOUR: int = int(os.environ.get("MORNING_BRIEFING_HOUR", "8"))

# Webhook用アバターCDN URLキャッシュ（起動時にDiscordにアップロードして取得）
_avatar_urls: dict[str, str] = {}  # dept_id → Discord CDN URL

# 動的ステータスメッセージ
_STATUS_MESSAGES: list[str] = [
    "全15部門を監視中",
    "Canvas ブリッジ稼働中",
    "部門AI 常時スタンバイ",
    "ミスターDオフィス運営中",
    "自律オフィス稼働中",
]


def _get_avatar_file(dept_id: str) -> discord.File | None:
    """部門IDに対応するピクセルアートアバターのdiscord.Fileを返す。"""
    filename = _AVATAR_OVERRIDE.get(dept_id, f"{dept_id}.png")
    path = _SPRITES_DIR / filename
    if path.is_file():
        return discord.File(str(path), filename=filename)
    return None


def _get_avatar_filename(dept_id: str) -> str:
    """アバターファイル名を返す（attachment:// URL用）。"""
    return _AVATAR_OVERRIDE.get(dept_id, f"{dept_id}.png")


def _sanitize_markdown(text: str) -> str:
    """ユーザー入力に含まれるDiscord Markdown Injectionを無害化する。"""
    return discord.utils.escape_markdown(text)


def _increment_msg_counter(dept_id: str) -> None:
    """部門別メッセージカウンターをインクリメントする。"""
    global _msg_counter_date
    today = datetime.now(JST).strftime("%Y-%m-%d")
    if _msg_counter_date != today:
        _msg_counter.clear()
        _msg_counter_date = today
    _msg_counter[dept_id] = _msg_counter.get(dept_id, 0) + 1


# どら（CEO）のDiscord User ID — /run, /approve はこのユーザーのみ実行可能
ALLOWED_USER_IDS: set[int] = {
    int(os.environ.get("DISCORD_OWNER_ID", "0")),
}
# .env に DISCORD_OWNER_ID が未設定でも、guild owner は自動許可される（on_readyで追加）

# 添付ファイルのテキスト最大文字数
ATTACHMENT_TEXT_MAX = 2000

# メッセージ重複排除: 最近処理済みのメッセージIDを保持（Gatewayの再送信対策）
_PROCESSED_MSG_IDS: set[int] = set()
_PROCESSED_MSG_MAX = 200  # 保持する最大件数

# PIDファイル: 二重起動を防止（商品化向け安全策）
_PID_FILE = Path("/tmp/claude_office_discord_bot.pid")

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
    {
        "dept_id": "development",
        "display_name": "🔧 ムー（開発）",
        "agent_color": "#00BCD4",
        "model": "haiku",
        "discord_color": 0x00BCD4,
        "icon": "🔧",
        "role": "開発部長",
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
    "開発部": "development",
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
    "development": "開発部",
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
    "🏢 本部": ["一般", "司令塔", "📝日報", "📋デイリーダイジェスト"],
    "🤝 会議室": ["会議室"],
    "💼 事業部門": ["コンテンツ部", "デザイン部", "ライティング部", "リサーチ部", "営業部", "広告部"],
    "🚀 特殊部門": ["新規事業部", "AI投資部", "フィルコンサル部", "不動産部"],
    "📣 SNS・ブランディング": ["タクミX部", "どらどらSNS部", "コピーロボット部"],
    "📦 保管庫": ["📦ドラの成果物", "📦部門の成果物"],
    "🔧 システム管理": ["📊ステータス", "司令塔ログ", "セキュリティ部", "開発部"],
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
# Phase 4: AI返答ボタン — AIResponseView
# ---------------------------------------------------------------------------

class ForwardDeptSelect(discord.ui.Select):
    """転送先の部門を選択するセレクトメニュー。"""

    def __init__(self, original_text: str, from_dept_id: str) -> None:
        self._original_text = original_text
        self._from_dept_id = from_dept_id
        options = []
        for char in CHARACTERS:
            if char["dept_id"] in ("bridge", from_dept_id):
                continue
            options.append(
                discord.SelectOption(
                    label=char["display_name"],
                    value=char["dept_id"],
                    description=char.get("role", ""),
                )
            )
        # Discord の Select は最大25件
        super().__init__(
            placeholder="転送先の部門を選んでください...",
            min_values=1,
            max_values=1,
            options=options[:25],
            custom_id=f"fwd_select_{from_dept_id}_{int(time.time())}",
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        target_dept = self.values[0]
        target_char = CHAR_BY_DEPT.get(target_dept)
        if not target_char:
            await interaction.response.send_message("部門が見つかりません。", ephemeral=True)
            return

        # 転送先チャンネルに送信
        channel_name = RELAY_DEPT_TO_CHANNEL.get(target_dept)
        if not channel_name or not interaction.guild:
            await interaction.response.send_message("転送先チャンネルが見つかりません。", ephemeral=True)
            return

        ch_id = CHANNELS.get(channel_name)
        if not ch_id:
            await interaction.response.send_message("チャンネルIDが見つかりません。", ephemeral=True)
            return

        ch = interaction.guild.get_channel(ch_id)
        if not isinstance(ch, discord.TextChannel):
            await interaction.response.send_message("テキストチャンネルが見つかりません。", ephemeral=True)
            return

        from_char = CHAR_BY_DEPT.get(self._from_dept_id, CHAR_BY_DEPT.get("bridge"))
        snippet = self._original_text[:300]
        if len(self._original_text) > 300:
            snippet += "..."

        fwd_embed = discord.Embed(
            title=f"転送: {from_char['display_name']} → {target_char['display_name']}",
            description=snippet,
            color=from_char["discord_color"],
            timestamp=datetime.now(timezone.utc),
        )
        fwd_embed.set_footer(text=f"転送者: {interaction.user.display_name}")

        # アバター付きで送信（Webhook CDN URLがあればそれを使う）
        avatar_cdn = _avatar_urls.get(self._from_dept_id)
        if avatar_cdn:
            fwd_embed.set_author(
                name=from_char["display_name"],
                icon_url=avatar_cdn,
            )
        else:
            avatar = _get_avatar_file(self._from_dept_id)
            if avatar:
                fwd_embed.set_author(
                    name=from_char["display_name"],
                    icon_url=f"attachment://{_get_avatar_filename(self._from_dept_id)}",
                )

        kwargs: dict[str, Any] = {"embed": fwd_embed}
        if not avatar_cdn:
            avatar_fb = _get_avatar_file(self._from_dept_id)
            if avatar_fb:
                kwargs["file"] = avatar_fb

        try:
            await ch.send(**kwargs)
            await interaction.response.send_message(
                f"{target_char['display_name']} に転送しました。",
                ephemeral=True,
            )
        except discord.HTTPException as exc:
            await interaction.response.send_message(f"転送エラー: {exc}", ephemeral=True)


class ForwardSelectView(discord.ui.View):
    """転送先セレクトメニューを包むView。"""

    def __init__(self, original_text: str, from_dept_id: str) -> None:
        super().__init__(timeout=120)
        self.add_item(ForwardDeptSelect(original_text, from_dept_id))


class AIResponseView(discord.ui.View):
    """
    AI返答に付けるインタラクティブボタン。
    - 深掘りする → スレッド作成
    - 別部門に転送 → セレクトメニュー表示
    - 保存する → memory_bridgeに保存
    """

    def __init__(
        self,
        dept_id: str,
        response_text: str,
        user_msg: str,
        bot_ref: "CommanderBridgeBot",
    ) -> None:
        super().__init__(timeout=300)  # 5分でタイムアウト
        self._dept_id = dept_id
        self._response_text = response_text
        self._user_msg = user_msg
        self._bot = bot_ref

    @discord.ui.button(
        label="深掘りする",
        style=discord.ButtonStyle.primary,
        emoji="🔍",
    )
    async def btn_deep_dive(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        """スレッドを作成して深掘り会話を続ける。"""
        msg = interaction.message
        if msg is None:
            await interaction.response.send_message("メッセージが見つかりません。", ephemeral=True)
            return

        char = CHAR_BY_DEPT.get(self._dept_id, CHAR_BY_DEPT.get("bridge"))
        dept_name = char["display_name"] if char else self._dept_id
        topic_preview = self._user_msg[:30] if self._user_msg else "深掘り"
        thread_name = f"{dept_name} — {topic_preview}"

        try:
            thread = await msg.create_thread(
                name=thread_name[:100],  # Discord制限: 100文字
                auto_archive_duration=1440,  # 24時間で自動アーカイブ
            )
            await interaction.response.send_message(
                f"スレッドを作成しました → {thread.mention}\nこの話題を深掘りしたいことを書いてください。",
                ephemeral=True,
            )
            # スレッド内に案内を投稿
            guide_embed = discord.Embed(
                description=(
                    f"このスレッドでは **{dept_name}** との深掘り会話ができます。\n"
                    f"自由に質問・指示を書いてください。AIが返答します。"
                ),
                color=char["discord_color"] if char else 0x95A5A6,
            )
            guide_embed.set_footer(text="スレッド内メッセージもAIが返答します")
            await thread.send(embed=guide_embed)
        except discord.HTTPException as exc:
            log.error("スレッド作成エラー: %s", exc)
            await interaction.response.send_message(
                f"スレッド作成に失敗しました: {exc}", ephemeral=True
            )

    @discord.ui.button(
        label="別部門に転送",
        style=discord.ButtonStyle.secondary,
        emoji="➡️",
    )
    async def btn_forward(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        """転送先部門のセレクトメニューを表示する。"""
        view = ForwardSelectView(self._response_text, self._dept_id)
        await interaction.response.send_message(
            "転送先の部門を選択してください:",
            view=view,
            ephemeral=True,
        )

    @discord.ui.button(
        label="保存する",
        style=discord.ButtonStyle.success,
        emoji="⭐",
    )
    async def btn_save(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        """会話をmemory_bridgeに保存する。"""
        if self._bot._memory_bridge:
            try:
                await self._bot._memory_bridge.save_conversation(
                    dept_id=self._dept_id,
                    user_msg=self._user_msg,
                    ai_response=self._response_text,
                )
                await interaction.response.send_message(
                    "会話をメモリに保存しました。次のClaude Codeセッションで参照されます。",
                    ephemeral=True,
                )
            except Exception as exc:
                log.error("memory_bridge保存エラー: %s", exc)
                await interaction.response.send_message(
                    f"保存に失敗しました: {exc}", ephemeral=True,
                )
        else:
            await interaction.response.send_message(
                "メモリブリッジが初期化されていません。", ephemeral=True,
            )


# ---------------------------------------------------------------------------
# Discord Bot クラス
# ---------------------------------------------------------------------------

class CommanderBridgeBot(discord.Client):
    def __init__(self) -> None:
        intents = discord.Intents.default()
        intents.message_content = True
        intents.guilds = True
        intents.messages = True
        intents.reactions = True
        intents.members = True  # ウェルカムメッセージ用

        super().__init__(
            intents=intents,
        )

        self.tree = app_commands.CommandTree(self)
        self._heartbeat_task: asyncio.Task[None] | None = None
        self._guild: discord.Guild | None = None
        self._ready_event = asyncio.Event()
        self._started_at: float = time.monotonic()  # Bot起動時刻（uptime計算用）
        self._last_heartbeat: float = 0.0  # 最後のハートビート成功時刻

        self._shutdown_requested = False

        # --- 自律オフィス ---
        self._dept_brain: DeptBrain | None = None
        self._dept_scheduler: DeptScheduler | None = None
        self._meeting_engine: MeetingEngine | None = None
        self._meeting_scheduler: MeetingScheduler | None = None
        self._discord_logger: DiscordLogger | None = None

        # --- Phase 2: 指示実行エンジン ---
        self._instruction_engine: InstructionEngine | None = None

        # --- Phase 3: 品質・記録機能 ---
        self._state_watcher: "StateWatcher | None" = None
        self._memory_bridge: "MemoryBridge | None" = None
        self._notification_controller: "NotificationController | None" = None
        self._dept_request_manager: "DeptRequestManager | None" = None
        self._digest_collector: "DigestCollector | None" = None
        self._digest_scheduler: "DigestScheduler | None" = None

        # --- Phase 4: UX・アバター・安全系 ---
        self._morning_task: asyncio.Task | None = None
        self._status_idx: int = 0
        self._webhook_cache: dict[int, discord.Webhook] = {}  # channel_id → Webhook

    # ------------------------------------------------------------------
    # ライフサイクル
    # ------------------------------------------------------------------

    async def setup_hook(self) -> None:
        """Bot 起動時に 1 回だけ呼ばれる。スラッシュコマンド登録と永続View 登録。"""
        # TASK-03: 永続 View を登録 (Bot 再起動後もボタンが動作する)
        self.add_view(RelayPanelView())
        log.info("RelayPanelView registered as persistent view.")
        log.info("Phase 4: AIResponseView / ForwardSelectView 利用可能")

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
            # Guild owner を自動的に ALLOWED_USER_IDS に追加
            if self._guild.owner_id:
                ALLOWED_USER_IDS.discard(0)  # .env未設定時のダミー0を除去
                ALLOWED_USER_IDS.add(self._guild.owner_id)
                log.info("Guild owner (id=%d) を ALLOWED_USER_IDS に追加", self._guild.owner_id)

            # TASK-02: サーバー名を「ミスターDオフィス」に変更 (冪等)
            await self._ensure_guild_name("ミスターDオフィス")

            # Phase 4: Botプロフィール画像をフィルのピクセルアートに設定（初回のみ）
            await self._ensure_bot_avatar()

            # Phase 4: 全部門アバターをDiscordにアップロード → CDN URLキャッシュ（Webhook用）
            await self._ensure_avatar_urls()

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

        # --- Phase 2: 指示実行エンジン初期化（先に初期化 — Scheduler/Meeting が参照） ---
        try:
            if InstructionEngine is not None:
                self._instruction_engine = InstructionEngine()
                log.info("指示実行エンジン初期化完了 (Phase 2)")
            else:
                log.warning("InstructionEngine モジュール未ロード。Phase 2 無効。")
        except Exception as exc:
            log.exception("指示実行エンジン初期化エラー: %s", exc)

        # --- 自律オフィス初期化 ---
        await self._init_autonomous_office()

        # --- ログ記録エンジン (APIキー不要 — 常に動かす) ---
        try:
            self._discord_logger = DiscordLogger()
            log.info("Discordログ記録エンジン初期化完了")
        except Exception as exc:
            log.exception("Discordログ記録エンジン初期化エラー: %s", exc)

        # --- Phase 3: 品質・記録機能初期化 ---
        await self._init_phase3()

        # --- Phase 4: 朝ブリーフィングスケジューラー ---
        if self._morning_task is None or self._morning_task.done():
            self._morning_task = asyncio.create_task(
                self._morning_briefing_loop(), name="morning_briefing"
            )
            log.info("朝ブリーフィングスケジューラー開始 (毎日 %02d:00 JST)", MORNING_BRIEFING_HOUR)

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

            # スケジューラー (REQ-002: 自発活動 + P-09: 実行能力付与)
            self._dept_scheduler = DeptScheduler(
                brain=self._dept_brain,
                post_callback=self._post_autonomous_activity,
                interval_hours=self._dept_brain.config.get("autonomous_interval_hours", 4),
                instruction_engine=self._instruction_engine,
                digest_collector=self._digest_collector,
            )
            self._dept_scheduler.start()
            log.info("部門自発活動スケジューラー開始")

            # 会議エンジン (REQ-003: 会議室 + P-10: アクション実行)
            self._meeting_engine = MeetingEngine(
                brain=self._dept_brain,
                post_callback=self._post_meeting_message,
                forward_callback=self._forward_action_to_dept,
                canvas_callback=self._send_meeting_canvas_event,
                instruction_engine=self._instruction_engine,
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

    async def _init_phase3(self) -> None:
        """Phase 3 コンポーネント（StateWatcher / MemoryBridge / NotificationController）を初期化する。"""
        if not _PHASE3_AVAILABLE:
            log.warning("Phase 3 モジュール未ロード。state_watcher / memory_bridge / notification_controller を確認してください。")
            return

        # NotificationController（依存なし・常に起動）
        try:
            self._notification_controller = NotificationController(self)
            log.info("NotificationController 初期化完了 (Phase 3)")
        except Exception as exc:
            log.exception("NotificationController 初期化エラー: %s", exc)

        # MemoryBridge（依存なし・常に起動）
        try:
            self._memory_bridge = MemoryBridge()
            log.info("MemoryBridge 初期化完了 (Phase 3)")
        except Exception as exc:
            log.exception("MemoryBridge 初期化エラー: %s", exc)

        # StateWatcher（shared_state.json のパスを dept_brain と同じロジックで解決）
        try:
            import os
            state_path_env = os.environ.get("SHARED_STATE_PATH", "")
            if state_path_env:
                state_path = Path(state_path_env)
            else:
                state_path = (
                    Path(__file__).parent.parent.parent
                    / "empire_monitor_full_20260321"
                    / ".claude"
                    / "shared_state.json"
                )
            self._state_watcher = StateWatcher(self, state_path=state_path, interval=60)
            await self._state_watcher.start()
            log.info("StateWatcher 起動完了 (Phase 3, path=%s)", state_path)
        except Exception as exc:
            log.exception("StateWatcher 初期化エラー: %s", exc)

        # DeptRequestManager（P-08: 部門間依頼フロー）
        try:
            if self._instruction_engine and DeptRequestManager:
                self._dept_request_manager = DeptRequestManager(
                    engine=self._instruction_engine,
                    bot=self,
                )
                log.info("DeptRequestManager 初期化完了 (Phase 3)")
        except Exception as exc:
            log.exception("DeptRequestManager 初期化エラー: %s", exc)

        # DigestCollector + DigestScheduler（デイリーダイジェスト）
        try:
            if DigestCollector and DigestScheduler:
                self._digest_collector = DigestCollector()
                self._digest_scheduler = DigestScheduler(
                    bot=self,
                    collector=self._digest_collector,
                )
                self._digest_scheduler.start()
                log.info("デイリーダイジェスト初期化完了 (毎日 21:00 JST)")
        except Exception as exc:
            log.exception("デイリーダイジェスト初期化エラー: %s", exc)

    async def _handle_dept_ai_response(
        self,
        message: discord.Message,
        dept_id: str,
        content: str,
        author_name: str,
    ) -> None:
        """部門AIがメッセージに返答する (REQ-001)。Phase 4: タイピング・アバター・ボタン・安全系対応。"""
        if not self._dept_brain:
            return

        try:
            # Phase 4: APIコスト上限チェック
            if hasattr(self._dept_brain, 'monthly_cost'):
                if self._dept_brain.monthly_cost >= MONTHLY_API_COST_LIMIT:
                    log.warning("月間APIコスト上限到達: $%.2f >= $%.2f", self._dept_brain.monthly_cost, MONTHLY_API_COST_LIMIT)
                    warn_embed = discord.Embed(
                        description="月間APIコスト上限に達しました。管理者に連絡してください。",
                        color=0xFF0000,
                    )
                    await message.channel.send(embed=warn_embed)
                    return

            # Phase 4: タイピングインジケーター表示
            async with message.channel.typing():
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

                # Phase 4: Markdown Injection対策（ユーザー入力をサニタイズ）
                safe_content = _sanitize_markdown(content)

                # AI返答生成（ツール使用対応: attachmentsに画像バイト列が入る場合あり）
                response, attachments = await self._dept_brain.respond(
                    dept_id=dept_id,
                    message=content,  # AI側には原文を渡す
                    context=context,
                    sender=author_name,
                )

            if not response:
                return

            # Phase 4: メッセージカウンター更新
            _increment_msg_counter(dept_id)

            # キャラ情報取得
            char = CHAR_BY_DEPT.get(dept_id, CHAR_BY_DEPT.get("bridge"))
            if not char:
                return

            # Phase 4: Embed形式で返答投稿（Webhook アバター付き）
            embed = discord.Embed(
                description=response,
                color=char["discord_color"],
                timestamp=datetime.now(timezone.utc),
            )
            # Webhookでサムネイルも部門アバターにする
            avatar_cdn = _avatar_urls.get(dept_id)
            if avatar_cdn:
                embed.set_thumbnail(url=avatar_cdn)
            embed.set_footer(text="ミスターDオフィス AI返答")

            # Phase 4: インタラクティブボタンを付与
            view = AIResponseView(
                dept_id=dept_id,
                response_text=response,
                user_msg=content,
                bot_ref=self,
            )

            # ツール生成画像をdiscord.Fileに変換
            discord_files: list[discord.File] = []
            for filename_hint, image_bytes in attachments:
                discord_files.append(
                    discord.File(io.BytesIO(image_bytes), filename=filename_hint)
                )

            # Phase 4: Webhook送信（部門キャラのアバターがプロフィール画像として表示）
            sent_msg = await self._send_as_dept(
                channel=message.channel,
                dept_id=dept_id,
                embed=embed,
                view=view,
            )

            # 画像添付ファイルを別途送信（Webhookは files= を直接受け取れないため）
            if discord_files:
                try:
                    await message.channel.send(files=discord_files)
                except Exception as _file_exc:
                    log.warning("画像ファイル送信エラー: %s", _file_exc)

            # Canvas にも送信
            await send_canvas_event(
                dept_id,
                response[:200],
                event_kind="ASK_COMPLETED",
            )

            # 成果物判定 (REQ-004)
            if sent_msg and self._dept_brain.is_deliverable(response):
                await self._store_deliverable(
                    dept_id=dept_id,
                    text=response,
                    source_message=sent_msg,
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

            # Phase 3: memory自動保存 (P-13) → 無効化（ボタン経由の手動保存に変更）
            # 設計方針: Discordは「ホワイトボード（アイデアの場）」。
            # ドラが「保存する」ボタンを押したものだけmemoryに記録される。

            # Phase 3: 部門間依頼自動検出 (P-08)
            if self._dept_request_manager:
                await self._dept_request_manager.scan_and_dispatch(
                    text=response,
                    from_dept=dept_id,
                    chain_depth=0,
                )

            log.info(
                "部門AI返答: %s → %s (%.0f文字, webhook=yes, buttons=yes)",
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

        # Embed形式で投稿（Phase 4: Webhook アバター付き）
        embed = discord.Embed(
            description=text,
            color=char["discord_color"],
            timestamp=datetime.now(timezone.utc),
        )
        embed.set_footer(text="ミスターDオフィス 自律活動")

        try:
            sent_msg = await self._send_as_dept(
                channel=channel,
                dept_id=dept_id,
                embed=embed,
                author_name=f"{char['display_name']} (自発活動)",
            )

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

            # デイリーダイジェストに記録
            if self._digest_collector:
                msg_url = sent_msg.jump_url if sent_msg else ""
                if is_deliverable:
                    self._digest_collector.add_deliverable(dept_id, text, msg_url)
                else:
                    self._digest_collector.add_proposal(dept_id, text, msg_url)

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
        author_label = "フィル（司令塔）" if is_phil else "どらどら"
        dept = "commander" if is_phil else "doradora_sns"

        embed = discord.Embed(
            title=title,
            description=text,
            color=color,
            timestamp=datetime.now(timezone.utc),
        )
        embed.set_footer(text="ミスターDオフィス 日報")

        try:
            await self._send_as_dept(
                channel=channel,
                dept_id=dept,
                embed=embed,
                author_name=f"{author_label} (日報)",
            )
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
        embed.set_footer(text="ミスターDオフィス 会議室")

        try:
            sent_msg = await self._send_as_dept(
                channel=channel,
                dept_id=dept_id,
                embed=embed,
            )

            # 成果物判定 (会議まとめは成果物)
            if sent_msg and self._dept_brain and self._dept_brain.is_deliverable(text):
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

        embed = discord.Embed(
            title="会議からのアクションアイテム",
            description=action_text,
            color=0xD4AF37,
            timestamp=datetime.now(timezone.utc),
        )
        embed.set_footer(text="会議室 → 部門転送")

        try:
            await self._send_as_dept(
                channel=channel,
                dept_id="commander",
                embed=embed,
                author_name="フィル（司令塔）",
            )
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

        import tempfile
        import os as _os_store
        tmp_path: str | None = None
        try:
            # 成果物全文をMDファイルとしてアップロード
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

            log.info("成果物保管 (ファイル付き): %s → %s [%s]", dept_id, storage_name, filename)
        except discord.HTTPException as exc:
            log.error("成果物保管エラー: %s", exc)
        finally:
            if tmp_path:
                try:
                    _os_store.unlink(tmp_path)
                except OSError:
                    pass

        # クロスポスト: 成果物を関連部門に共有 (is_relay=True の場合はドラ直接指示なので除外)
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

    async def _ensure_bot_avatar(self) -> None:
        """Botのプロフィール画像をフィル（司令塔）のピクセルアートに設定する。冪等。"""
        avatar_path = _SPRITES_DIR / "commander.png"
        if not avatar_path.is_file():
            log.warning("Botアバター画像が見つかりません: %s", avatar_path)
            return
        try:
            avatar_bytes = avatar_path.read_bytes()
            await self.user.edit(avatar=avatar_bytes)  # type: ignore[union-attr]
            log.info("Botプロフィール画像をフィルのピクセルアートに設定しました")
        except discord.HTTPException as exc:
            # レート制限等で失敗しても致命的ではない
            if exc.status == 429 or "Too many" in str(exc):
                log.info("Botアバター変更レート制限 — 既に設定済みか次回再試行")
            else:
                log.warning("Botアバター設定エラー: %s", exc)
        except Exception as exc:
            log.warning("Botアバター設定エラー: %s", exc)

    # ------------------------------------------------------------------
    # Phase 4: Webhook アバターシステム（全部門ピクセルアート対応）
    # ------------------------------------------------------------------

    async def _ensure_avatar_urls(self) -> None:
        """起動時に全部門のスプライトをDiscordにアップロードしてCDN URLをキャッシュする。"""
        global _avatar_urls
        if _avatar_urls:
            return  # 既にキャッシュ済み

        if not self._guild:
            return

        # 司令塔ログチャンネルにアップロード（ユーザーに見えにくい場所）
        log_ch_id = CHANNELS.get("司令塔ログ")
        if not log_ch_id:
            log.warning("司令塔ログチャンネル未設定 — アバターURL初期化スキップ")
            return
        channel = self._guild.get_channel(log_ch_id)
        if not isinstance(channel, discord.TextChannel):
            return

        # 全部門のスプライトファイルを収集
        files_to_upload: list[tuple[str, Path]] = []
        for char in CHARACTERS:
            dept_id = char["dept_id"]
            filename = _AVATAR_OVERRIDE.get(dept_id, f"{dept_id}.png")
            path = _SPRITES_DIR / filename
            if path.is_file():
                files_to_upload.append((dept_id, path))
            else:
                log.warning("スプライト未発見: %s → %s", dept_id, path)

        if not files_to_upload:
            log.warning("アバタースプライトが見つかりません: %s", _SPRITES_DIR)
            return

        # バッチアップロード（Discord制限: 1メッセージ最大10ファイル）
        batch_size = 10
        for i in range(0, len(files_to_upload), batch_size):
            batch = files_to_upload[i:i + batch_size]
            discord_files = []
            dept_ids_in_batch = []
            for dept_id, path in batch:
                # ファイル名にdept_idを含めて一意にする
                discord_files.append(discord.File(str(path), filename=f"avatar_{dept_id}.png"))
                dept_ids_in_batch.append(dept_id)

            try:
                msg = await channel.send(
                    content=f"🎨 アバター初期化 (バッチ {i // batch_size + 1})",
                    files=discord_files,
                )
                # 添付ファイルからCDN URLを取得（送信順 = 添付順）
                for attachment, dept_id in zip(msg.attachments, dept_ids_in_batch):
                    _avatar_urls[dept_id] = attachment.url
                    log.debug("アバターURL登録: %s → %s", dept_id, attachment.url[:80])
            except discord.HTTPException as exc:
                log.error("アバターアップロードエラー (バッチ %d): %s", i // batch_size + 1, exc)

        log.info("アバターURL初期化完了: %d/%d 部門", len(_avatar_urls), len(files_to_upload))

    async def _get_or_create_webhook(self, channel: discord.TextChannel) -> discord.Webhook | None:
        """チャンネルのWebhookを取得または作成する（1チャンネル1Webhook方式）。"""
        # キャッシュチェック
        if channel.id in self._webhook_cache:
            return self._webhook_cache[channel.id]

        webhook_name = "ミスターDオフィス"

        try:
            # 既存Webhookを検索
            webhooks = await channel.webhooks()
            for wh in webhooks:
                if wh.name == webhook_name and wh.user == self.user:
                    self._webhook_cache[channel.id] = wh
                    return wh

            # 新規作成（権限: manage_webhooks が必要）
            wh = await channel.create_webhook(name=webhook_name)
            self._webhook_cache[channel.id] = wh
            log.info("Webhook作成: #%s → %s", channel.name, wh.id)
            return wh

        except discord.Forbidden:
            log.warning("Webhook作成権限なし: #%s — フォールバック使用", channel.name)
            return None
        except discord.HTTPException as exc:
            log.warning("Webhook取得/作成エラー (#%s): %s", channel.name, exc)
            return None

    async def _send_as_dept(
        self,
        channel: discord.TextChannel | discord.Thread,
        dept_id: str,
        embed: discord.Embed,
        *,
        author_name: str | None = None,
        view: discord.ui.View | None = None,
        content: str | None = None,
    ) -> discord.Message | None:
        """Webhook経由で部門キャラのアバター付きメッセージを送信する。

        Webhookが使えない場合はembed attachment方式にフォールバック。

        Args:
            channel: 送信先チャンネル（Threadも対応）
            dept_id: 部門ID
            embed: 送信するEmbed（set_authorは本メソッドが設定する）
            author_name: 表示名オーバーライド（例: "フィル（司令塔） 朝ブリーフィング"）
            view: ボタン等のView
            content: Embed以外のテキスト
        """
        char = CHAR_BY_DEPT.get(dept_id, CHAR_BY_DEPT.get("bridge"))
        if not char:
            return None

        # 表示名の決定
        display = author_name or char["display_name"]
        # Webhook用の名前（emoji除去、Discord username制限80文字）
        wh_name_parts = display.split(" ", 1)
        wh_username = wh_name_parts[1] if len(wh_name_parts) > 1 and wh_name_parts[0].strip() else display
        wh_username = wh_username[:80]

        avatar_url = _avatar_urls.get(dept_id)

        # スレッド対応: Thread → 親チャンネルのWebhookを使い、thread引数で送信先指定
        target_channel = channel
        thread_arg: discord.Thread | None = None
        if isinstance(channel, discord.Thread):
            parent = channel.parent
            if parent and isinstance(parent, discord.TextChannel):
                target_channel = parent
                thread_arg = channel
            # parentが取れない場合はそのままフォールバックへ

        # --- Webhook送信 ---
        # 重要: view（ボタン等）がある場合はWebhookを使わない。
        # Webhook経由のメッセージはインタラクションがBotに届かないDiscord仕様のため、
        # ボタン付きメッセージは通常の channel.send() で送信する。
        if avatar_url and isinstance(target_channel, discord.TextChannel) and view is None:
            try:
                webhook = await self._get_or_create_webhook(target_channel)
                if webhook:
                    # Webhook送信時もembed.authorを設定（embed内に名前を表示）
                    embed.set_author(name=display, icon_url=avatar_url)

                    send_kw: dict[str, Any] = {
                        "embed": embed,
                        "username": wh_username,
                        "avatar_url": avatar_url,
                        "wait": True,
                    }
                    if content:
                        send_kw["content"] = content
                    if thread_arg:
                        send_kw["thread"] = thread_arg

                    sent = await webhook.send(**send_kw)
                    return sent
            except discord.HTTPException as exc:
                log.warning("Webhook送信失敗 (%s→#%s): %s — フォールバック",
                            dept_id, getattr(target_channel, 'name', '?'), exc)

        # --- フォールバック: embed + attachment方式 ---
        avatar_file = _get_avatar_file(dept_id)
        send_kw_fb: dict[str, Any] = {"embed": embed}

        if avatar_file:
            avatar_fn = _get_avatar_filename(dept_id)
            embed.set_author(name=display, icon_url=f"attachment://{avatar_fn}")
            send_kw_fb["file"] = avatar_file
        else:
            embed.set_author(name=display)

        if view:
            send_kw_fb["view"] = view
        if content:
            send_kw_fb["content"] = content

        return await channel.send(**send_kw_fb)

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
        # Phase 4: 朝ブリーフィングタスク停止
        if self._morning_task and not self._morning_task.done():
            self._morning_task.cancel()
            try:
                await self._morning_task
            except asyncio.CancelledError:
                pass
        # Phase 3: StateWatcher 停止
        if self._state_watcher is not None:
            await self._state_watcher.stop()
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
                self._last_heartbeat = time.monotonic()
                self._write_health_file()

                # Phase 4: 動的ステータスローテーション
                try:
                    status_msg = _STATUS_MESSAGES[self._status_idx % len(_STATUS_MESSAGES)]
                    self._status_idx += 1
                    await self.change_presence(
                        status=discord.Status.online,
                        activity=discord.Activity(
                            type=discord.ActivityType.watching,
                            name=status_msg,
                        ),
                    )
                except Exception:
                    pass  # ステータス更新失敗は無視

            except asyncio.CancelledError:
                log.info("伝書鳩: ハートビートループ停止。")
                break
            except Exception:
                log.exception("ハートビートループエラー (次回インターバルで再試行)")

    # ------------------------------------------------------------------
    # Bot ヘルスファイル（外部プロセスから死活確認用）
    # ------------------------------------------------------------------
    _HEALTH_FILE = Path("/tmp/claude_office_health.json")

    def _write_health_file(self) -> None:
        """ヘルスファイルを書き出す。外部スクリプトで mtime を見ればBot生存確認できる。"""
        try:
            uptime = time.monotonic() - self._started_at
            data = {
                "status": "alive",
                "uptime_sec": int(uptime),
                "last_heartbeat": datetime.now(JST).strftime("%Y-%m-%d %H:%M:%S"),
                "guild": self._guild.name if self._guild else None,
            }
            self._HEALTH_FILE.write_text(
                json.dumps(data, ensure_ascii=False), encoding="utf-8"
            )
        except OSError:
            pass

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
        # 重複排除: 同一メッセージIDを2回処理しない（Gatewayの再送信対策）
        if message.id in _PROCESSED_MSG_IDS:
            log.debug("重複メッセージをスキップ: %s", message.id)
            return
        _PROCESSED_MSG_IDS.add(message.id)
        if len(_PROCESSED_MSG_IDS) > _PROCESSED_MSG_MAX:
            # 古いIDを適当に間引く（setなので順序保証なし、最古ではないが十分）
            to_remove = len(_PROCESSED_MSG_IDS) - _PROCESSED_MSG_MAX
            it = iter(_PROCESSED_MSG_IDS)
            for _ in range(to_remove):
                _PROCESSED_MSG_IDS.discard(next(it))

        channel_id = message.channel.id

        # Phase 4: スレッド内メッセージは親チャンネルの部門として処理
        actual_channel_id = channel_id
        if isinstance(message.channel, discord.Thread) and message.channel.parent_id:
            actual_channel_id = message.channel.parent_id

        channel_name = CHANNEL_TO_DEPT.get(actual_channel_id, "")

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
                try:
                    attachment_metadata = await _process_attachments(message.attachments)
                    attachment_summary = _format_attachment_summary(attachment_metadata)
                    log.debug(
                        "添付ファイル処理: %s から %d 件 (channel=%s)",
                        author_name, len(message.attachments), channel_name,
                    )
                except Exception as att_exc:
                    log.error("添付ファイル処理エラー (メッセージ転送は続行): %s", att_exc)
                    attachment_summary = f"[添付ファイル処理エラー: {len(message.attachments)}件]"

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
    # Phase 4: リアクション→アクション
    # ------------------------------------------------------------------

    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent) -> None:
        """絵文字リアクションでアクションを実行する。
        ⭐ = memory_bridgeに保存
        ➡️ = 司令塔に転送
        📝 = アクションアイテムとして記録
        """
        # Bot自身のリアクションは無視
        if payload.user_id == self.user.id:  # type: ignore[union-attr]
            return
        # ギルド内のみ
        if not payload.guild_id or not self._guild:
            return

        emoji = str(payload.emoji)
        if emoji not in ("⭐", "➡️", "📝"):
            return

        channel = self._guild.get_channel(payload.channel_id)
        if not isinstance(channel, discord.TextChannel):
            return

        try:
            message = await channel.fetch_message(payload.message_id)
        except discord.HTTPException:
            return

        # Bot自身のメッセージ（AI返答）のみ対象
        if message.author != self.user:  # type: ignore[union-attr]
            return

        # 部門IDを特定
        channel_name = CHANNEL_TO_DEPT.get(channel.id, "")
        dept_id = CHANNEL_TO_DEPT_ID.get(channel_name, "commander")

        # embedのdescriptionからAI返答テキストを取得
        response_text = ""
        if message.embeds:
            response_text = message.embeds[0].description or ""

        if emoji == "⭐":
            # memory_bridgeに保存
            if self._memory_bridge and response_text:
                try:
                    await self._memory_bridge.save_conversation(
                        dept_id=dept_id,
                        user_msg="(リアクションで保存)",
                        ai_response=response_text,
                    )
                    await message.add_reaction("✅")
                    log.info("リアクション保存: %s (channel=%s)", dept_id, channel_name)
                except Exception as exc:
                    log.error("リアクション保存エラー: %s", exc)

        elif emoji == "➡️":
            # 司令塔チャンネルに転送
            cmd_ch_id = CHANNELS.get("司令塔")
            if cmd_ch_id:
                cmd_ch = self._guild.get_channel(cmd_ch_id)
                if isinstance(cmd_ch, discord.TextChannel) and response_text:
                    char = CHAR_BY_DEPT.get(dept_id, CHAR_BY_DEPT.get("bridge"))
                    fwd_embed = discord.Embed(
                        title=f"転送: {channel_name}",
                        description=response_text[:4000],
                        color=char["discord_color"] if char else 0x95A5A6,
                        timestamp=datetime.now(timezone.utc),
                    )
                    fwd_embed.set_footer(text=f"リアクション転送 | 元チャンネル: {channel_name}")
                    try:
                        await cmd_ch.send(embed=fwd_embed)
                        await message.add_reaction("✅")
                    except discord.HTTPException as exc:
                        log.error("リアクション転送エラー: %s", exc)

        elif emoji == "📝":
            # アクションアイテムとして司令塔ログに記録
            log_ch_id = CHANNELS.get("司令塔ログ")
            if log_ch_id:
                log_ch = self._guild.get_channel(log_ch_id)
                if isinstance(log_ch, discord.TextChannel) and response_text:
                    action_embed = discord.Embed(
                        title="📝 アクションアイテム",
                        description=response_text[:2000],
                        color=0xF39C12,
                        timestamp=datetime.now(timezone.utc),
                    )
                    action_embed.add_field(name="元チャンネル", value=channel_name, inline=True)
                    action_embed.add_field(
                        name="元メッセージ",
                        value=f"[ジャンプ]({message.jump_url})",
                        inline=True,
                    )
                    action_embed.set_footer(text="リアクション → アクションアイテム")
                    try:
                        await log_ch.send(embed=action_embed)
                        await message.add_reaction("✅")
                    except discord.HTTPException as exc:
                        log.error("アクションアイテム記録エラー: %s", exc)

    # ------------------------------------------------------------------
    # Phase 4: ウェルカムメッセージ
    # ------------------------------------------------------------------

    async def on_member_join(self, member: discord.Member) -> None:
        """新メンバー参加時のウェルカムメッセージ。"""
        if member.bot:
            return

        # 一般チャンネルに送信
        general_ch_id = CHANNELS.get("一般")
        if not general_ch_id or not self._guild:
            return
        channel = self._guild.get_channel(general_ch_id)
        if not isinstance(channel, discord.TextChannel):
            return

        embed = discord.Embed(
            title=f"ようこそ、{member.display_name}さん！",
            description=(
                "**ミスターDオフィス**へようこそ！\n\n"
                "このサーバーは15部門のAIエージェントが常駐する自律オフィスです。\n"
                "各部門チャンネルでメッセージを送ると、担当AIが返答します。\n\n"
                "**使い方:**\n"
                "• 各部門チャンネルで自由に質問・相談\n"
                "• `ヘルプ` と送信でコマンド一覧\n"
                "• `部門一覧` で全キャラクター確認\n"
                "• ⭐リアクションで会話を保存\n"
                "• AI返答のボタンで深掘り・転送・保存\n\n"
                "まずは気になる部門チャンネルを覗いてみてください！"
            ),
            color=0xD4AF37,
            timestamp=datetime.now(timezone.utc),
        )

        try:
            await self._send_as_dept(
                channel=channel,
                dept_id="commander",
                embed=embed,
                author_name="フィル（司令塔）",
            )
            log.info("ウェルカムメッセージ送信: %s", member.display_name)
        except discord.HTTPException as exc:
            log.error("ウェルカムメッセージ送信エラー: %s", exc)

    # ------------------------------------------------------------------
    # Phase 4: 朝ブリーフィングループ
    # ------------------------------------------------------------------

    async def _morning_briefing_loop(self) -> None:
        """毎朝 MORNING_BRIEFING_HOUR 時 (JST) に司令塔チャンネルへブリーフィングを投稿する。"""
        while not self._shutdown_requested:
            try:
                now_jst = datetime.now(JST)
                target = now_jst.replace(
                    hour=MORNING_BRIEFING_HOUR, minute=0, second=0, microsecond=0,
                )
                if now_jst >= target:
                    target += timedelta(days=1)

                wait_sec = (target - now_jst).total_seconds()
                log.info(
                    "次の朝ブリーフィング: %s JST (%.0f秒後)",
                    target.strftime("%Y-%m-%d %H:%M"), wait_sec,
                )
                await asyncio.sleep(wait_sec)

                if self._shutdown_requested:
                    break

                # ブリーフィング生成
                await self._post_morning_briefing()

            except asyncio.CancelledError:
                break
            except Exception as exc:
                log.exception("朝ブリーフィングエラー: %s", exc)
                await asyncio.sleep(60)

    async def _post_morning_briefing(self) -> None:
        """朝ブリーフィングを司令塔チャンネルに投稿する。"""
        if not self._guild:
            return

        cmd_ch_id = CHANNELS.get("司令塔")
        if not cmd_ch_id:
            return
        channel = self._guild.get_channel(cmd_ch_id)
        if not isinstance(channel, discord.TextChannel):
            return

        now_jst = datetime.now(JST)
        date_str = now_jst.strftime("%Y年%m月%d日 (%a)")

        # 各部門の稼働状況を収集
        dept_lines = []
        for char in CHARACTERS:
            dept_id = char["dept_id"]
            icon = char["icon"]
            name = char["display_name"].split(" ", 1)[-1] if " " in char["display_name"] else char["display_name"]
            msg_count = _msg_counter.get(dept_id, 0)
            status = f"{msg_count}件の応答" if msg_count > 0 else "待機中"
            dept_lines.append(f"{icon} **{name}** — {status}")

        # コスト情報
        cost_info = ""
        if self._dept_brain:
            cost_info = f"**月間APIコスト**: ${self._dept_brain.monthly_cost:.2f} / ${MONTHLY_API_COST_LIMIT:.0f}"

        # uptime
        uptime_sec = time.monotonic() - self._started_at
        uptime_hours = uptime_sec / 3600

        embed = discord.Embed(
            title=f"おはようございます — {date_str}",
            description=(
                f"**ミスターDオフィス 朝ブリーフィング**\n\n"
                f"Bot稼働時間: {uptime_hours:.1f}時間\n"
                f"{cost_info}\n"
                f"昨日のメッセージ数: {sum(_msg_counter.values())}件\n\n"
                f"**部門ステータス:**\n" + "\n".join(dept_lines)
            ),
            color=0xD4AF37,
            timestamp=datetime.now(timezone.utc),
        )
        embed.set_footer(text="ミスターDオフィス 自動ブリーフィング")

        try:
            await self._send_as_dept(
                channel=channel,
                dept_id="commander",
                embed=embed,
                author_name="フィル（司令塔） 朝ブリーフィング",
            )
            log.info("朝ブリーフィング投稿完了: %s", date_str)
            # カウンターリセット（新しい日の開始）
            _msg_counter.clear()
        except discord.HTTPException as exc:
            log.error("朝ブリーフィング投稿エラー: %s", exc)

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
        name="スラッシュコマンド（基本）",
        value=(
            "`/status` — システムステータス\n"
            "`/ask [部門] [質問]` — 指定部門に質問\n"
            "`/bridge [メッセージ]` — Canvas に直接ブリッジ送信\n"
            "`/relay_panel` — 14部門中継パネル表示\n"
            "`/usage` — APIコスト・メッセージ統計\n"
            "`/export [部門]` — 会話履歴をエクスポート"
        ),
        inline=False,
    )
    embed.add_field(
        name="スラッシュコマンド（管理）",
        value=(
            "`/meeting [議題]` — 部門横断会議を開始\n"
            "`/run [部門] [タスク]` — AI にタスク実行させる\n"
            "`/approve [ID]` — 承認待ちタスクを承認\n"
            "`/pending` — 承認待ち一覧\n"
            "`/silence [on/off]` — 自発活動の停止/再開\n"
            "`/brain_status` — 部門AI頭脳のステータス"
        ),
        inline=False,
    )
    embed.add_field(
        name="インタラクティブ機能",
        value=(
            "**ボタン**: AI返答に付く3ボタン\n"
            "  - 🔍 深掘りする → スレッド作成\n"
            "  - ➡️ 別部門に転送 → 部門選択メニュー\n"
            "  - ⭐ 保存する → メモリに保存\n\n"
            "**リアクション**: AI返答に絵文字でアクション\n"
            "  - ⭐ → メモリに保存\n"
            "  - ➡️ → 司令塔に転送\n"
            "  - 📝 → アクションアイテム記録"
        ),
        inline=False,
    )
    embed.add_field(
        name="自動機能",
        value=(
            f"- 部門チャンネルのメッセージ → Canvas 自動転送\n"
            f"- スレッド内メッセージもAIが返答\n"
            f"- 添付ファイル対応 (画像/PDF/MD/txt/動画)\n"
            f"- {HEARTBEAT_INTERVAL // 60}分ごとに Canvas ハートビート送信\n"
            f"- 毎朝 {MORNING_BRIEFING_HOUR}:00 に朝ブリーフィング\n"
            f"- 部門アバター付きEmbed表示\n"
            f"- 新メンバーにウェルカムメッセージ\n"
            f"- APIコスト上限 (日: ${DAILY_API_COST_LIMIT:.0f} / 月: ${MONTHLY_API_COST_LIMIT:.0f})\n"
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
        if interaction.user.id not in ALLOWED_USER_IDS:
            await interaction.response.send_message("このコマンドはどら（CEO）専用です。", ephemeral=True)
            return
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
        if interaction.user.id not in ALLOWED_USER_IDS:
            await interaction.response.send_message("このコマンドはどら（CEO）専用です。", ephemeral=True)
            return
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
        if interaction.user.id not in ALLOWED_USER_IDS:
            await interaction.response.send_message("このコマンドはどら（CEO）専用です。", ephemeral=True)
            return
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
        if interaction.user.id not in ALLOWED_USER_IDS:
            await interaction.response.send_message("このコマンドはどら（CEO）専用です。", ephemeral=True)
            return
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
        if interaction.user.id not in ALLOWED_USER_IDS:
            await interaction.response.send_message("このコマンドはどら（CEO）専用です。", ephemeral=True)
            return
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
        # どら本人チェック（セキュリティ: 誰でも実行できてはいけない）
        if interaction.user.id not in ALLOWED_USER_IDS:
            await interaction.response.send_message(
                "このコマンドはどら（CEO）専用です。",
                ephemeral=True,
            )
            return

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
        # どら本人チェック（セキュリティ: 承認はCEOのみ）
        if interaction.user.id not in ALLOWED_USER_IDS:
            await interaction.response.send_message(
                "このコマンドはどら（CEO）専用です。",
                ephemeral=True,
            )
            return

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

    # ===================================================================
    # Phase 4: /usage — APIコスト・メッセージ統計
    # ===================================================================

    @tree.command(
        name="usage",
        description="APIコスト・メッセージ数・部門別利用統計を表示",
        guild=guild_obj,
    )
    async def slash_usage(interaction: discord.Interaction) -> None:
        now_jst = datetime.now(JST)
        date_str = now_jst.strftime("%Y-%m-%d")

        embed = discord.Embed(
            title="利用統計",
            description=f"**日付**: {date_str}",
            color=0x3498DB,
            timestamp=datetime.now(timezone.utc),
        )

        # APIコスト
        if bot._dept_brain:
            embed.add_field(
                name="月間APIコスト",
                value=f"${bot._dept_brain.monthly_cost:.2f} / ${MONTHLY_API_COST_LIMIT:.0f}",
                inline=True,
            )
        embed.add_field(
            name="日次コスト上限",
            value=f"${DAILY_API_COST_LIMIT:.0f}",
            inline=True,
        )

        # uptime
        uptime_sec = time.monotonic() - bot._started_at
        uptime_hours = uptime_sec / 3600
        embed.add_field(
            name="稼働時間",
            value=f"{uptime_hours:.1f}時間",
            inline=True,
        )

        # メッセージカウンター
        total_msgs = sum(_msg_counter.values())
        embed.add_field(
            name="本日メッセージ数",
            value=str(total_msgs),
            inline=True,
        )

        # 部門別ランキング (上位5)
        if _msg_counter:
            sorted_depts = sorted(_msg_counter.items(), key=lambda x: x[1], reverse=True)[:5]
            ranking_lines = []
            for i, (dept_id, count) in enumerate(sorted_depts, 1):
                char = CHAR_BY_DEPT.get(dept_id, {})
                icon = char.get("icon", "")
                name = char.get("display_name", dept_id).split(" ", 1)[-1] if " " in char.get("display_name", "") else dept_id
                ranking_lines.append(f"{i}. {icon} {name}: {count}件")
            embed.add_field(
                name="部門別ランキング (Top 5)",
                value="\n".join(ranking_lines) if ranking_lines else "データなし",
                inline=False,
            )
        else:
            embed.add_field(
                name="部門別ランキング",
                value="本日のメッセージはまだありません",
                inline=False,
            )

        # 指示実行エンジン
        if bot._instruction_engine:
            status = bot._instruction_engine.get_status()
            embed.add_field(
                name="実行済みタスク",
                value=str(status.get("execution_count", 0)),
                inline=True,
            )
            embed.add_field(
                name="承認待ち",
                value=str(status.get("pending_approvals", 0)),
                inline=True,
            )

        embed.set_footer(text="ミスターDオフィス × 利用統計")
        await interaction.response.send_message(embed=embed)

    # ===================================================================
    # Phase 4: /export — 会話履歴エクスポート
    # ===================================================================

    @tree.command(
        name="export",
        description="部門の会話履歴をMD形式でエクスポートする",
        guild=guild_obj,
    )
    @app_commands.describe(
        dept="部門ID（例: research）。省略時は現在のチャンネルの部門",
        count="エクスポートするメッセージ数（デフォルト50）",
    )
    async def slash_export(
        interaction: discord.Interaction,
        dept: str | None = None,
        count: int = 50,
    ) -> None:
        if interaction.user.id not in ALLOWED_USER_IDS:
            await interaction.response.send_message("このコマンドはどら（CEO）専用です。", ephemeral=True)
            return

        await interaction.response.defer(thinking=True)

        # 部門IDの特定
        dept_id = None
        if dept:
            dept_clean = dept.strip().lower()
            if dept_clean in CHAR_BY_DEPT:
                dept_id = dept_clean
            else:
                # チャンネル名から推定
                for d, ch in RELAY_DEPT_TO_CHANNEL.items():
                    if dept in ch:
                        dept_id = d
                        break
        if not dept_id:
            ch_name = CHANNEL_TO_DEPT.get(interaction.channel_id, "")
            dept_id = CHANNEL_TO_DEPT_ID.get(ch_name)

        if not dept_id:
            await interaction.followup.send(
                "部門を特定できません。`/export dept:research` のように指定してください。",
                ephemeral=True,
            )
            return

        char = CHAR_BY_DEPT.get(dept_id, {})
        char_name = char.get("display_name", dept_id)

        # チャンネルからメッセージを取得
        ch_name = RELAY_DEPT_TO_CHANNEL.get(dept_id)
        if not ch_name or not bot._guild:
            await interaction.followup.send("チャンネルが見つかりません。", ephemeral=True)
            return

        ch_id = CHANNELS.get(ch_name)
        if not ch_id:
            await interaction.followup.send("チャンネルIDが見つかりません。", ephemeral=True)
            return

        channel = bot._guild.get_channel(ch_id)
        if not isinstance(channel, discord.TextChannel):
            await interaction.followup.send("テキストチャンネルではありません。", ephemeral=True)
            return

        # メッセージ収集
        messages_data = []
        async for msg in channel.history(limit=min(count, 200)):
            ts = msg.created_at.astimezone(JST).strftime("%Y-%m-%d %H:%M:%S")
            author = msg.author.display_name
            content = msg.content or ""

            # Embedの内容も取得
            embed_text = ""
            if msg.embeds:
                for emb in msg.embeds:
                    if emb.description:
                        embed_text += emb.description + "\n"

            messages_data.append({
                "timestamp": ts,
                "author": author,
                "content": content,
                "embed": embed_text.strip(),
            })

        messages_data.reverse()  # 古い順に

        # Markdown生成
        now_str = datetime.now(JST).strftime("%Y%m%d_%H%M")
        md_lines = [
            f"# {char_name} 会話履歴エクスポート",
            f"",
            f"> 生成日時: {datetime.now(JST).strftime('%Y-%m-%d %H:%M JST')}",
            f"> 部門: {char_name} (`{dept_id}`)",
            f"> メッセージ数: {len(messages_data)}件",
            f"",
            "---",
            "",
        ]

        for msg_data in messages_data:
            md_lines.append(f"## [{msg_data['timestamp']}] {msg_data['author']}")
            if msg_data["content"]:
                md_lines.append(f"{msg_data['content']}")
            if msg_data["embed"]:
                md_lines.append(f"\n> {msg_data['embed'][:500]}")
            md_lines.append("")
            md_lines.append("---")
            md_lines.append("")

        md_content = "\n".join(md_lines)

        # ファイルとして送信
        import io
        file_bytes = md_content.encode("utf-8")
        filename = f"export_{dept_id}_{now_str}.md"
        discord_file = discord.File(io.BytesIO(file_bytes), filename=filename)

        await interaction.followup.send(
            f"{char_name} の会話履歴 ({len(messages_data)}件) をエクスポートしました。",
            file=discord_file,
        )

    # ===================================================================
    # Phase 3: P-15 dept_lock連携 — /lock, /unlock, /locks
    # ===================================================================

    @tree.command(name="lock", description="部門をロックする（排他作業開始）")
    @app_commands.describe(dept="部門ID")
    async def slash_lock(interaction: discord.Interaction, dept: str) -> None:
        if interaction.user.id not in ALLOWED_USER_IDS:
            await interaction.response.send_message("このコマンドはどら（CEO）専用です。", ephemeral=True)
            return
        dept_clean = dept.strip().lower()
        _sp = os.environ.get("SHARED_STATE_PATH", "")
        lock_file = Path(_sp) if _sp else Path(__file__).parent.parent.parent / "empire_monitor_full_20260321" / ".claude" / "shared_state.json"

        if not lock_file.is_file():
            await interaction.response.send_message("shared_state.json が見つかりません", ephemeral=True)
            return

        try:
            import json as _json
            import fcntl as _fcntl

            # flock排他制御で読み→変更→書き込みをアトミックに行う
            with open(lock_file, "r+", encoding="utf-8") as fp:
                _fcntl.flock(fp, _fcntl.LOCK_EX)
                try:
                    data = _json.load(fp)
                    depts = data.get("departments", {})

                    if dept_clean not in depts:
                        await interaction.response.send_message(
                            f"部門 `{dept_clean}` は存在しません。\n有効な部門: {', '.join(sorted(depts.keys()))}",
                            ephemeral=True,
                        )
                        return

                    dept_data = depts[dept_clean]
                    existing_lock = dept_data.get("discord_lock")
                    if existing_lock:
                        await interaction.response.send_message(
                            f"⚠️ `{dept_clean}` は既にロック中です。\n"
                            f"**ロック者**: {existing_lock.get('holder', '不明')}\n"
                            f"**開始時刻**: {existing_lock.get('locked_at', '不明')}",
                            ephemeral=True,
                        )
                        return

                    jst = timezone(timedelta(hours=9))
                    now_str = datetime.now(jst).isoformat()
                    dept_data["discord_lock"] = {
                        "holder": str(interaction.user),
                        "locked_at": now_str,
                        "source": "discord",
                    }
                    fp.seek(0)
                    fp.truncate()
                    _json.dump(data, fp, ensure_ascii=False, indent=2)
                finally:
                    _fcntl.flock(fp, _fcntl.LOCK_UN)

            embed = discord.Embed(
                title=f"🔒 部門ロック: {dept_clean}",
                description=f"**ロック者**: {interaction.user}\n**時刻**: {now_str}",
                color=0xF39C12,
            )
            embed.set_footer(text="P-15 dept_lock連携")
            await interaction.response.send_message(embed=embed)
        except Exception as exc:
            if not interaction.response.is_done():
                await interaction.response.send_message(f"ロックエラー: {exc}", ephemeral=True)
            else:
                await interaction.followup.send(f"ロックエラー: {exc}", ephemeral=True)

    @tree.command(name="unlock", description="部門のロックを解除する")
    @app_commands.describe(dept="部門ID")
    async def slash_unlock(interaction: discord.Interaction, dept: str) -> None:
        if interaction.user.id not in ALLOWED_USER_IDS:
            await interaction.response.send_message("このコマンドはどら（CEO）専用です。", ephemeral=True)
            return
        dept_clean = dept.strip().lower()
        _sp = os.environ.get("SHARED_STATE_PATH", "")
        lock_file = Path(_sp) if _sp else Path(__file__).parent.parent.parent / "empire_monitor_full_20260321" / ".claude" / "shared_state.json"

        if not lock_file.is_file():
            await interaction.response.send_message("shared_state.json が見つかりません", ephemeral=True)
            return

        try:
            import json as _json
            import fcntl as _fcntl

            # flock排他制御で読み→変更→書き込みをアトミックに行う
            with open(lock_file, "r+", encoding="utf-8") as fp:
                _fcntl.flock(fp, _fcntl.LOCK_EX)
                try:
                    data = _json.load(fp)
                    dept_data = data.get("departments", {}).get(dept_clean)

                    if not dept_data:
                        await interaction.response.send_message(f"部門 `{dept_clean}` が見つかりません", ephemeral=True)
                        return

                    existing_lock = dept_data.get("discord_lock")
                    if not existing_lock:
                        await interaction.response.send_message(f"`{dept_clean}` はロックされていません", ephemeral=True)
                        return

                    dept_data.pop("discord_lock", None)
                    fp.seek(0)
                    fp.truncate()
                    _json.dump(data, fp, ensure_ascii=False, indent=2)
                finally:
                    _fcntl.flock(fp, _fcntl.LOCK_UN)

            embed = discord.Embed(
                title=f"🔓 ロック解除: {dept_clean}",
                description=f"**解除者**: {interaction.user}",
                color=0x2ECC71,
            )
            embed.set_footer(text="P-15 dept_lock連携")
            await interaction.response.send_message(embed=embed)
        except Exception as exc:
            if not interaction.response.is_done():
                await interaction.response.send_message(f"ロック解除エラー: {exc}", ephemeral=True)
            else:
                await interaction.followup.send(f"ロック解除エラー: {exc}", ephemeral=True)

    @tree.command(name="locks", description="全部門のロック状況を表示")
    async def slash_locks(interaction: discord.Interaction) -> None:
        _sp = os.environ.get("SHARED_STATE_PATH", "")
        lock_file = Path(_sp) if _sp else Path(__file__).parent.parent.parent / "empire_monitor_full_20260321" / ".claude" / "shared_state.json"

        if not lock_file.is_file():
            await interaction.response.send_message("shared_state.json が見つかりません", ephemeral=True)
            return

        try:
            import json as _json
            data = _json.loads(lock_file.read_text(encoding="utf-8"))
            depts = data.get("departments", {})

            locked = []
            for dept_id, dept_data in sorted(depts.items()):
                lock_info = dept_data.get("discord_lock")
                if lock_info:
                    locked.append(
                        f"🔒 **{dept_id}** — {lock_info.get('holder', '不明')} "
                        f"({lock_info.get('locked_at', '不明')[:16]})"
                    )

            if not locked:
                await interaction.response.send_message("現在ロック中の部門はありません ✅", ephemeral=True)
                return

            embed = discord.Embed(
                title="部門ロック状況",
                description="\n".join(locked),
                color=0xF39C12,
            )
            embed.set_footer(text=f"P-15 dept_lock連携 | {len(locked)}件ロック中")
            await interaction.response.send_message(embed=embed)
        except Exception as exc:
            if not interaction.response.is_done():
                await interaction.response.send_message(f"エラー: {exc}", ephemeral=True)
            else:
                await interaction.followup.send(f"エラー: {exc}", ephemeral=True)

    # ===================================================================
    # Phase 3: P-19 ロールバック — /rollback
    # ===================================================================

    @tree.command(name="rollback", description="最後の実行結果を取り消す（git revert）")
    @app_commands.describe(inst_id="取り消す指示ID (INST-xxxxx)")
    async def slash_rollback(interaction: discord.Interaction, inst_id: str) -> None:
        if interaction.user.id not in ALLOWED_USER_IDS:
            await interaction.response.send_message("このコマンドはどら（CEO）専用です。", ephemeral=True)
            return
        if not bot._instruction_engine:
            await interaction.response.send_message("指示実行エンジンが無効です", ephemeral=True)
            return

        engine = bot._instruction_engine
        inst = engine.get_queued(inst_id)

        if not inst:
            await interaction.response.send_message(
                f"`{inst_id}` が見つかりません。`/pending` で一覧を確認してください",
                ephemeral=True,
            )
            return

        if inst["status"] == "pending":
            # まだ実行前 → キューから取り消し
            inst["status"] = "cancelled"
            embed = discord.Embed(
                title=f"取り消し完了: {inst_id}",
                description=(
                    f"**タスク**: {inst['task'][:100]}\n"
                    f"**状態**: pending → cancelled（実行前に取り消し）"
                ),
                color=0xF39C12,
            )
            embed.set_footer(text="P-19 ロールバック")
            await interaction.response.send_message(embed=embed)
            return

        if inst["status"] == "completed":
            # 実行済み → git revertを提案（安全のためclaude -pで実行）
            await interaction.response.defer(thinking=True)
            rollback_task = (
                f"直前の指示 {inst_id} (タスク: {inst['task'][:80]}) の実行結果を確認し、"
                f"もし変更されたファイルがあれば git revert で元に戻してください。"
                f"変更がなければ「ロールバック不要」と報告してください。"
            )
            result = await engine.execute(rollback_task, inst["dept_id"])

            color = 0x2ECC71 if result["status"] == "success" else 0xE74C3C
            embed = discord.Embed(
                title=f"ロールバック結果: {inst_id}",
                description=(result.get("result", "") or "(結果なし)")[:1800],
                color=color,
            )
            embed.add_field(name="コスト", value=f"${result.get('cost', 0):.4f}", inline=True)
            embed.set_footer(text="P-19 ロールバック")
            await interaction.followup.send(embed=embed)
            return

        await interaction.response.send_message(
            f"`{inst_id}` の状態は `{inst['status']}` です。ロールバックできません。",
            ephemeral=True,
        )

    # ===================================================================
    # Phase 3: P-20 外部イベント受信 — /webhook
    # ===================================================================

    @tree.command(name="webhook", description="外部イベントを手動トリガー（GitHub/VPS/cron）")
    @app_commands.describe(
        source="イベントソース (github/vps/cron/custom)",
        event="イベント内容",
        dept="対応部門ID（省略時: セキュリティ部）",
    )
    async def slash_webhook(
        interaction: discord.Interaction,
        source: str,
        event: str,
        dept: str = "security",
    ) -> None:
        if interaction.user.id not in ALLOWED_USER_IDS:
            await interaction.response.send_message("このコマンドはどら（CEO）専用です。", ephemeral=True)
            return
        dept_clean = dept.strip().lower()

        # 通知先の自動判定
        source_to_dept: dict[str, str] = {
            "github": "security",
            "vps": "security",
            "cron": "commander",
            "custom": dept_clean,
        }
        target_dept = source_to_dept.get(source.lower(), dept_clean)

        # 部門チャンネルに通知
        if bot._guild:
            ch_name_map = {
                "commander": "司令塔",
                "security": "セキュリティ部",
                "research": "リサーチ部",
                "content": "コンテンツ部",
                "design": "デザイン部",
            }
            ch_name = ch_name_map.get(target_dept, target_dept)
            ch = discord.utils.get(bot._guild.text_channels, name=ch_name)
            if ch:
                ext_embed = discord.Embed(
                    title=f"外部イベント受信: {source}",
                    description=event[:1800],
                    color=0x9B59B6,
                )
                ext_embed.add_field(name="ソース", value=source, inline=True)
                ext_embed.add_field(name="対応部門", value=target_dept, inline=True)
                ext_embed.set_footer(text="P-20 外部イベント連携")
                await ch.send(embed=ext_embed)

        # NotificationController経由で司令塔ログにも記録
        if bot._notification_controller:
            await bot._notification_controller.notify(
                level="normal",
                title=f"外部イベント: {source}",
                message=f"**内容**: {event[:500]}\n**対応部門**: {target_dept}",
                dept_id=target_dept,
            )

        # auto_respond: safeタスクなら自動実行
        if bot._instruction_engine:
            classification = bot._instruction_engine.classify(event)
            if classification == "safe":
                await interaction.response.defer(thinking=True)
                result = await bot._instruction_engine.execute(event, target_dept)

                color = 0x2ECC71 if result["status"] == "success" else 0xE74C3C
                embed = discord.Embed(
                    title=f"外部イベント自動対応: {source}",
                    description=(result.get("result", "") or "(結果なし)")[:1800],
                    color=color,
                )
                embed.add_field(name="コスト", value=f"${result.get('cost', 0):.4f}", inline=True)
                embed.set_footer(text="P-20 外部イベント連携 (auto)")
                await interaction.followup.send(embed=embed)
                return

        embed = discord.Embed(
            title=f"外部イベント登録: {source}",
            description=f"**内容**: {event[:300]}\n**対応部門**: {target_dept}\n\n通知を該当チャンネルに送信しました。",
            color=0x9B59B6,
        )
        embed.set_footer(text="P-20 外部イベント連携")
        await interaction.response.send_message(embed=embed)


# ---------------------------------------------------------------------------
# P-14: エラー即座報告ヘルパー
# ---------------------------------------------------------------------------

async def _report_error_to_discord(
    bot: "CommanderBridgeBot",
    error_source: str,
    error_msg: str,
    dept_id: str | None = None,
) -> None:
    """エラーをDiscordの司令塔ログチャンネルに即座報告する。"""
    if not bot._guild:
        return
    cmd_log_ch = discord.utils.get(bot._guild.text_channels, name="司令塔ログ")
    if not cmd_log_ch:
        return

    embed = discord.Embed(
        title=f"エラー: {error_source}",
        description=error_msg[:1800],
        color=0xE74C3C,
    )
    if dept_id:
        embed.add_field(name="部門", value=dept_id, inline=True)
    embed.set_footer(text="自動エラー報告 (P-14)")

    try:
        await cmd_log_ch.send(embed=embed)
    except Exception:
        pass  # エラー報告自体が失敗しても無視


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

        # P-17: 成果物ファイルをDiscordにアップロード
        if result["status"] == "success":
            full_result_text = result.get("result", "")
            _FILE_PATH_PATTERN = re.compile(
                r"/Users/[^\s\"']+\.(md|html|pdf|py|txt|csv|json)"
            )
            found_paths = _FILE_PATH_PATTERN.findall(
                full_result_text  # type: ignore[arg-type]
            )
            # findall でグループ付きパターンはサフィックスだけ返るので全マッチを取り直す
            found_full_paths = _FILE_PATH_PATTERN.finditer(full_result_text)
            upload_files: list[discord.File] = []
            warned_paths: list[str] = []
            # セキュリティ: アップロード許可ディレクトリのallowlist
            _UPLOAD_ALLOWED_PREFIXES = (
                "/Users/satts924/Downloads/",
                "/Users/satts924/Desktop/",
                str(Path.home() / "Desktop") + "/",
                str(Path.home() / "Downloads") + "/",
                "/tmp/claude_office",
            )
            for m in found_full_paths:
                fpath = Path(m.group(0)).resolve()
                if not fpath.is_file():
                    continue
                # パストラバーサル防止: allowlist外のファイルはスキップ
                fpath_str = str(fpath)
                if not any(fpath_str.startswith(prefix) for prefix in _UPLOAD_ALLOWED_PREFIXES):
                    log.warning("アップロード拒否（allowlist外）: %s", fpath_str)
                    warned_paths.append(f"{fpath.name} (許可ディレクトリ外のためスキップ)")
                    continue
                # .env等の機密ファイルは絶対にアップロードしない
                if fpath.name.startswith(".env") or fpath.suffix in (".key", ".pem", ".p12"):
                    log.warning("アップロード拒否（機密ファイル）: %s", fpath.name)
                    warned_paths.append(f"{fpath.name} (機密ファイルのためスキップ)")
                    continue
                size_mb = fpath.stat().st_size / (1024 * 1024)
                if size_mb >= 25:
                    warned_paths.append(f"{fpath.name} ({size_mb:.1f}MB — 25MB超のためスキップ)")
                    continue
                try:
                    upload_files.append(discord.File(str(fpath), filename=fpath.name))
                except Exception as fe:
                    log.warning("ファイルアップロード準備エラー %s: %s", fpath, fe)

            if upload_files:
                # ファイルパスを事前に保持（discord.File は send() 後に消費される）
                _file_paths = [(str(Path(f.fp.name).resolve()), f.filename) for f in upload_files if f.fp]
                try:
                    await channel.send(
                        f"`{inst_id}` 成果物ファイル ({len(upload_files)}件):",
                        files=upload_files,
                    )
                    # 📦部門の成果物チャンネルにも投稿
                    if bot._guild:
                        deliv_ch_name_candidates = [
                            f"📦{dept_id}-成果物",
                            f"📦{dept_id}の成果物",
                            f"{dept_id}-deliverables",
                        ]
                        for cname in deliv_ch_name_candidates:
                            deliv_ch = discord.utils.get(
                                bot._guild.text_channels, name=cname,
                            )
                            if deliv_ch and deliv_ch != channel:
                                # 保持したパスから新しい File オブジェクトを作成
                                reupload = []
                                for fpath_str, fname in _file_paths:
                                    try:
                                        if Path(fpath_str).is_file():
                                            reupload.append(
                                                discord.File(fpath_str, filename=fname)
                                            )
                                    except Exception:
                                        pass
                                if reupload:
                                    await deliv_ch.send(
                                        f"`{inst_id}` [{char_name}] 成果物:",
                                        files=reupload,
                                    )
                                break
                except Exception as ue:
                    log.error("ファイルアップロードエラー: %s", ue)

            if warned_paths:
                await channel.send(
                    "サイズ超過のためアップロードをスキップしたファイル:\n"
                    + "\n".join(f"- {p}" for p in warned_paths)
                )

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
        # P-14: エラーを司令塔ログに即座報告
        await _report_error_to_discord(
            bot=bot,
            error_source=f"_run_and_report ({inst_id})",
            error_msg=str(exc),
            dept_id=dept_id,
        )
        try:
            await channel.send(
                f"`{inst_id}` 実行中にエラーが発生しました: {exc}"
            )
        except Exception:
            pass


# ---------------------------------------------------------------------------
# エントリポイント
# ---------------------------------------------------------------------------

def _acquire_pid_lock() -> bool:
    """PIDファイルロックを取得する。既に別プロセスが起動中なら False を返す。"""
    if _PID_FILE.exists():
        try:
            old_pid = int(_PID_FILE.read_text().strip())
            # プロセスが生きているかチェック (signal 0 = 生存確認)
            os.kill(old_pid, 0)
            # 生きている → 二重起動
            log.error(
                "伝書鳩は既に起動中です (PID %d)。二重起動はAPIコスト倍増の原因になるため拒否します。\n"
                "強制起動: rm %s && python3 scripts/discord_bot.py",
                old_pid, _PID_FILE,
            )
            return False
        except (ProcessLookupError, PermissionError):
            # プロセスは死んでいる → PIDファイルが残骸。上書きOK
            log.info("古いPIDファイル検出 (PID %s は既に終了)。上書きします。", old_pid)
        except ValueError:
            # PIDファイルの中身が壊れている
            log.warning("PIDファイルの内容が不正。上書きします。")

    _PID_FILE.write_text(str(os.getpid()), encoding="utf-8")
    log.info("PIDファイル作成: %s (PID %d)", _PID_FILE, os.getpid())
    return True


def _release_pid_lock() -> None:
    """PIDファイルロックを解放する。"""
    try:
        if _PID_FILE.exists():
            stored_pid = int(_PID_FILE.read_text().strip())
            if stored_pid == os.getpid():
                _PID_FILE.unlink()
                log.info("PIDファイル削除: %s", _PID_FILE)
            else:
                log.warning(
                    "PIDファイルのPID (%d) が自分 (%d) と異なる。削除スキップ。",
                    stored_pid, os.getpid(),
                )
    except (ValueError, OSError) as exc:
        log.warning("PIDファイル削除エラー: %s", exc)


async def _main() -> None:
    global _bot

    # --- 二重起動防止 (商品化必須) ---
    if not _acquire_pid_lock():
        sys.exit(1)

    if not BOT_TOKEN:
        log.error(
            "DISCORD_BOT_TOKEN が設定されていません。.env を確認してください。\n"
            "ファイルパス: %s",
            _ENV_FILE,
        )
        _release_pid_lock()
        sys.exit(1)

    if not HMAC_SECRET:
        log.error("EXTERNAL_EVENT_SECRET が設定されていません。")
        _release_pid_lock()
        sys.exit(1)

    if not GUILD_ID:
        log.error(
            "DISCORD_SERVER_ID (または DISCORD_GUILD_ID) が設定されていません。.env を確認してください。"
        )
        _release_pid_lock()
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
        # PIDファイル解放
        _release_pid_lock()


if __name__ == "__main__":
    try:
        asyncio.run(_main())
    except KeyboardInterrupt:
        log.info("伝書鳩: Keyboard interrupt. Bye.")
        _release_pid_lock()
