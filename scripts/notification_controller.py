"""
notification_controller.py - 通知粒度制御
===========================================
3段階の通知レベルで、適切なチャンネル / DM に振り分ける。

レベル定義:
    CRITICAL  — エラー・承認要求・ブロッカー検知
                 → ドラのDM + 司令塔ログ
    NORMAL    — 実行結果・状態変更・自律活動
                 → 該当部門チャンネル（dept_id 指定時）+ 司令塔ログ
    QUIET     — 定期ログ・デバッグ情報
                 → 司令塔ログのみ

呼び出し方:
    nc = NotificationController(bot)
    await nc.notify(
        level=NotificationLevel.CRITICAL,
        title="接続エラー",
        message="Anthropic API がタイムアウトしました",
    )
    await nc.notify(
        level=NotificationLevel.NORMAL,
        title="自発活動完了",
        message="リサーチ部が週次レポートを作成しました",
        dept_id="research",
    )
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Final

import discord

log = logging.getLogger("notification_controller")

# ---------------------------------------------------------------------------
# 通知レベル定数
# ---------------------------------------------------------------------------


class NotificationLevel:
    """通知レベル定数。文字列なので条件分岐も読みやすい。"""

    CRITICAL: Final[str] = "critical"
    """エラー・承認要求・ブロッカー → ドラDM + 司令塔ログ"""

    NORMAL: Final[str] = "normal"
    """実行結果・状態変更 → 部門チャンネル（+ 司令塔ログ）"""

    QUIET: Final[str] = "quiet"
    """定期ログ・デバッグ → 司令塔ログのみ"""


# レベル別デフォルトカラー
_LEVEL_COLORS: dict[str, int] = {
    NotificationLevel.CRITICAL: 0xE74C3C,   # 赤
    NotificationLevel.NORMAL:   0x3498DB,   # 青
    NotificationLevel.QUIET:    0x95A5A6,   # グレー
}

# 部門ID → チャンネル名
_DEPT_TO_CHANNEL: dict[str, str] = {
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
    "takumi_x": "タクミX部",
    "real_estate": "不動産部",
    "doradora_sns": "どらどらSNS部",
    "origin_story": "コピーロボット部",
}

# ドラ（どらどら）のDiscord表示名（DM検索用）
_DORA_DISPLAY_NAME: Final[str] = "どらどら"


class NotificationController:
    """
    3段階の通知粒度制御クラス。

    Parameters
    ----------
    bot:
        CommanderBridgeBot インスタンス（discord.Client サブクラス）
    """

    def __init__(self, bot: discord.Client) -> None:
        self._bot = bot

    # ------------------------------------------------------------------
    # 公開 API
    # ------------------------------------------------------------------

    async def notify(
        self,
        level: str,
        title: str,
        message: str,
        dept_id: str | None = None,
        color: int | None = None,
    ) -> None:
        """
        通知を送信する。

        Parameters
        ----------
        level:
            通知レベル。NotificationLevel.{CRITICAL, NORMAL, QUIET} を使う。
        title:
            embed のタイトル。
        message:
            embed のメッセージ本文。
        dept_id:
            部門 ID（NORMAL 時に部門チャンネルへ送るために使う）。
            省略可。
        color:
            embed カラー（省略時はレベル別デフォルト）。
        """
        embed = self._build_embed(level, title, message, color)

        if level == NotificationLevel.CRITICAL:
            await self._send_to_channel("司令塔ログ", embed)
            await self._send_dm_to_dora(embed)

        elif level == NotificationLevel.NORMAL:
            if dept_id:
                ch_name = _DEPT_TO_CHANNEL.get(dept_id)
                if ch_name:
                    await self._send_to_channel(ch_name, embed)
                else:
                    log.warning(
                        "NotificationController: 部門ID %s のチャンネルが不明。司令塔ログに送ります。",
                        dept_id,
                    )
                    await self._send_to_channel("司令塔ログ", embed)
            else:
                await self._send_to_channel("司令塔ログ", embed)

        elif level == NotificationLevel.QUIET:
            await self._send_to_channel("司令塔ログ", embed)

        else:
            log.warning(
                "NotificationController: 未知のレベル '%s'。司令塔ログに送ります。", level
            )
            await self._send_to_channel("司令塔ログ", embed)

    # ------------------------------------------------------------------
    # 内部実装
    # ------------------------------------------------------------------

    def _build_embed(
        self,
        level: str,
        title: str,
        message: str,
        color: int | None,
    ) -> discord.Embed:
        """embed を組み立てる。"""
        resolved_color = color if color is not None else _LEVEL_COLORS.get(level, 0x3498DB)
        embed = discord.Embed(
            title=f"[{level.upper()}] {title}",
            description=message,
            color=resolved_color,
            timestamp=datetime.now(timezone.utc),
        )
        embed.set_footer(text=f"NotificationController / level={level}")
        return embed

    async def _send_to_channel(
        self, channel_name: str, embed: discord.Embed
    ) -> None:
        """名前でチャンネルを探して embed を送信する。"""
        channel = self._find_text_channel(channel_name)
        if channel is None:
            log.warning(
                "NotificationController: チャンネル「%s」が見つかりません。スキップ。",
                channel_name,
            )
            return
        try:
            await channel.send(embed=embed)
        except discord.HTTPException as exc:
            log.error(
                "NotificationController: 「%s」への投稿失敗: %s", channel_name, exc
            )

    async def _send_dm_to_dora(self, embed: discord.Embed) -> None:
        """ドラ（どらどら）に DM を送る。"""
        dora = self._find_member(_DORA_DISPLAY_NAME)
        if dora is None:
            log.warning(
                "NotificationController: ドラ（%s）がサーバーに見つかりません。DM をスキップ。",
                _DORA_DISPLAY_NAME,
            )
            return
        try:
            dm = await dora.create_dm()
            await dm.send(embed=embed)
            log.debug("NotificationController: ドラへ DM 送信完了")
        except discord.HTTPException as exc:
            log.error("NotificationController: ドラへの DM 送信失敗: %s", exc)

    def _find_text_channel(self, name: str) -> discord.TextChannel | None:
        """全 guild からチャンネル名で TextChannel を探す。"""
        for guild in self._bot.guilds:
            for ch in guild.channels:
                if isinstance(ch, discord.TextChannel) and ch.name == name:
                    return ch
        return None

    def _find_member(self, display_name: str) -> discord.Member | None:
        """全 guild から表示名でメンバーを探す（部分一致）。"""
        for guild in self._bot.guilds:
            for member in guild.members:
                if display_name in (member.display_name, member.name):
                    return member
        return None
