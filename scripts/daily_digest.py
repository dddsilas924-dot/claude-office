"""
daily_digest.py - デイリーダイジェスト（ドラ向け1日まとめ）
=============================================================
AIたちが1日かけて出した提案・承認待ちタスク・成果物を
夜21:00 JSTに「📋デイリーダイジェスト」チャンネルにまとめて投稿する。

設計思想:
  Discordは「ホワイトボード（アイデアの場）」。
  AIが勝手に話した内容はClaude Code側に自動で入れない。
  ドラがこのダイジェストを見て「これ採用」と判断したものだけ
  /approve で実行 or Claude Codeセッションで手動記録する。

呼び出し方:
  collector = DigestCollector()
  scheduler = DigestScheduler(bot, collector)
  scheduler.start()

  # 各所から記録を追加
  collector.add_proposal(dept_id, text, message_url)
  collector.add_pending(inst_id, dept_id, task, message_url)
  collector.add_deliverable(dept_id, text, message_url)
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import discord

log = logging.getLogger("daily_digest")

JST = timezone(timedelta(hours=9))

# 部門ID → キャラ名
_DEPT_NAMES: dict[str, str] = {
    "commander": "フィル",
    "research": "リョウ",
    "sales": "レイ",
    "design": "リック",
    "content": "ユウキ",
    "writing": "カイ",
    "advertising": "エナ",
    "ai_investment": "アキ",
    "new_biz": "タダシ",
    "phil_consulting": "フィルコンサル",
    "security": "セキュリティ",
    "takumi_x": "タクミ",
    "real_estate": "アイリ",
    "doradora_sns": "どらどらSNS",
    "origin_story": "コピーロボット",
}

# 部門ID → 部門表示名
_DEPT_LABELS: dict[str, str] = {
    "commander": "司令塔",
    "research": "リサーチ部",
    "sales": "営業部",
    "design": "デザイン部",
    "content": "コンテンツ部",
    "writing": "ライティング部",
    "advertising": "広告部",
    "ai_investment": "AI投資部",
    "new_biz": "新規事業部",
    "phil_consulting": "フィルコンサル部",
    "security": "セキュリティ部",
    "takumi_x": "タクミX部",
    "real_estate": "不動産部",
    "doradora_sns": "どらどらSNS部",
    "origin_story": "コピーロボット部",
}


# ---------------------------------------------------------------------------
# データクラス
# ---------------------------------------------------------------------------

@dataclass
class DigestItem:
    """ダイジェスト1項目。"""
    dept_id: str
    text: str
    message_url: str = ""        # Discord メッセージへのジャンプリンク
    inst_id: str = ""            # 承認待ちの場合のID
    timestamp: datetime = field(default_factory=lambda: datetime.now(JST))

    @property
    def char_name(self) -> str:
        return _DEPT_NAMES.get(self.dept_id, self.dept_id)

    @property
    def dept_label(self) -> str:
        return _DEPT_LABELS.get(self.dept_id, self.dept_id)


# ---------------------------------------------------------------------------
# Collector: 1日分の活動を溜める
# ---------------------------------------------------------------------------

class DigestCollector:
    """
    各所から呼ばれて1日分の活動を記録する。
    毎日21:00のダイジェスト投稿後にリセットされる。
    """

    def __init__(self) -> None:
        self._proposals: list[DigestItem] = []
        self._pendings: list[DigestItem] = []
        self._deliverables: list[DigestItem] = []
        self._meeting_notes: list[DigestItem] = []

    # --- 記録API ---

    def add_proposal(
        self, dept_id: str, text: str, message_url: str = "",
    ) -> None:
        """提案・アイデアを記録する。"""
        self._proposals.append(DigestItem(
            dept_id=dept_id, text=text[:300], message_url=message_url,
        ))
        log.debug("ダイジェスト: 提案追加 dept=%s", dept_id)

    def add_pending(
        self, inst_id: str, dept_id: str, task: str, message_url: str = "",
    ) -> None:
        """承認待ちタスクを記録する。"""
        self._pendings.append(DigestItem(
            dept_id=dept_id, text=task[:300],
            message_url=message_url, inst_id=inst_id,
        ))
        log.debug("ダイジェスト: 承認待ち追加 id=%s dept=%s", inst_id, dept_id)

    def add_deliverable(
        self, dept_id: str, text: str, message_url: str = "",
    ) -> None:
        """成果物を記録する。"""
        self._deliverables.append(DigestItem(
            dept_id=dept_id, text=text[:300], message_url=message_url,
        ))
        log.debug("ダイジェスト: 成果物追加 dept=%s", dept_id)

    def add_meeting_note(
        self, dept_id: str, text: str, message_url: str = "",
    ) -> None:
        """会議で出たアクション・提案を記録する。"""
        self._meeting_notes.append(DigestItem(
            dept_id=dept_id, text=text[:300], message_url=message_url,
        ))
        log.debug("ダイジェスト: 会議ノート追加 dept=%s", dept_id)

    # --- 集計 ---

    @property
    def total_count(self) -> int:
        return (
            len(self._proposals) + len(self._pendings)
            + len(self._deliverables) + len(self._meeting_notes)
        )

    def build_embed_fields(self) -> list[dict[str, str]]:
        """Embed用のフィールドリストを組み立てる。"""
        fields: list[dict[str, str]] = []

        # 提案・アイデア
        if self._proposals:
            lines = []
            for i, item in enumerate(self._proposals, 1):
                link = f" [📎詳細]({item.message_url})" if item.message_url else ""
                lines.append(
                    f"**{i}. {item.char_name}**（{item.dept_label}）\n"
                    f"　{item.text[:150]}{link}"
                )
            fields.append({
                "name": f"💡 提案・アイデア ({len(self._proposals)}件)",
                "value": "\n\n".join(lines)[:1024],
            })

        # 承認待ち
        if self._pendings:
            lines = []
            for i, item in enumerate(self._pendings, 1):
                link = f" [📎詳細]({item.message_url})" if item.message_url else ""
                lines.append(
                    f"**{i}. {item.char_name}**（{item.dept_label}）\n"
                    f"　{item.text[:120]}\n"
                    f"　→ `/approve {item.inst_id}` で実行{link}"
                )
            fields.append({
                "name": f"⏳ 承認待ち ({len(self._pendings)}件)",
                "value": "\n\n".join(lines)[:1024],
            })

        # 会議ノート
        if self._meeting_notes:
            lines = []
            for i, item in enumerate(self._meeting_notes, 1):
                link = f" [📎詳細]({item.message_url})" if item.message_url else ""
                lines.append(
                    f"**{i}. {item.char_name}**（{item.dept_label}）\n"
                    f"　{item.text[:150]}{link}"
                )
            fields.append({
                "name": f"🏛 会議で出たアクション ({len(self._meeting_notes)}件)",
                "value": "\n\n".join(lines)[:1024],
            })

        # 成果物
        if self._deliverables:
            lines = []
            for i, item in enumerate(self._deliverables, 1):
                link = f" [📎詳細]({item.message_url})" if item.message_url else ""
                lines.append(
                    f"**{i}. {item.char_name}**（{item.dept_label}）\n"
                    f"　{item.text[:150]}{link}"
                )
            fields.append({
                "name": f"📦 成果物 ({len(self._deliverables)}件)",
                "value": "\n\n".join(lines)[:1024],
            })

        return fields

    def reset(self) -> None:
        """1日分の記録をリセットする。"""
        count = self.total_count
        self._proposals.clear()
        self._pendings.clear()
        self._deliverables.clear()
        self._meeting_notes.clear()
        log.info("ダイジェスト: %d件の記録をリセット", count)


# ---------------------------------------------------------------------------
# Scheduler: 毎日21:00 JSTに投稿
# ---------------------------------------------------------------------------

class DigestScheduler:
    """
    毎日21:00 JSTにダイジェストを投稿するスケジューラー。

    Parameters
    ----------
    bot:
        Discord Botインスタンス
    collector:
        DigestCollector（各所から記録が追加される）
    channel_name:
        投稿先チャンネル名
    hour:
        投稿時刻（JST）。デフォルト21時
    """

    def __init__(
        self,
        bot: "discord.Client",
        collector: DigestCollector,
        channel_name: str = "📋デイリーダイジェスト",
        hour: int = 21,
    ) -> None:
        self._bot = bot
        self._collector = collector
        self._channel_name = channel_name
        self._hour = hour
        self._task: asyncio.Task | None = None

    def start(self) -> None:
        """スケジューラーを開始する。"""
        self._task = asyncio.create_task(self._loop(), name="digest_scheduler")
        log.info(
            "デイリーダイジェストスケジューラー開始: 毎日 %d:00 JST → %s",
            self._hour, self._channel_name,
        )

    def stop(self) -> None:
        """スケジューラーを停止する。"""
        if self._task and not self._task.done():
            self._task.cancel()
        log.info("デイリーダイジェストスケジューラー停止")

    async def _loop(self) -> None:
        """メインループ: 毎日指定時刻に投稿する。"""
        while True:
            now = datetime.now(JST)
            target = now.replace(hour=self._hour, minute=0, second=0, microsecond=0)
            if now >= target:
                target += timedelta(days=1)

            wait_sec = (target - now).total_seconds()
            log.info(
                "次のダイジェスト投稿: %s JST (%d秒後)",
                target.strftime("%Y-%m-%d %H:%M"), int(wait_sec),
            )

            try:
                await asyncio.sleep(wait_sec)
                await self._post_digest()
            except asyncio.CancelledError:
                log.info("ダイジェストスケジューラーがキャンセルされました")
                break
            except Exception as exc:
                log.exception("ダイジェスト投稿エラー: %s", exc)
                # エラーでも止まらない。翌日にリトライ
                await asyncio.sleep(60)

    async def _post_digest(self) -> None:
        """ダイジェストembedを組み立てて投稿する。"""
        import discord as _discord

        if self._collector.total_count == 0:
            log.info("ダイジェスト: 本日のアクティビティなし。投稿スキップ")
            self._collector.reset()
            return

        now = datetime.now(JST)
        date_str = now.strftime("%m/%d")

        # embed組み立て
        embed = _discord.Embed(
            title=f"📋 {date_str} のダイジェスト",
            description=(
                f"本日の活動まとめ: **{self._collector.total_count}件**\n"
                f"採用したいものは `/approve ID` で実行できます。"
            ),
            color=0xF39C12,  # オレンジ
            timestamp=datetime.now(timezone.utc),
        )

        for f in self._collector.build_embed_fields():
            embed.add_field(
                name=f["name"],
                value=f["value"],
                inline=False,
            )

        embed.set_footer(text="ミスターDオフィス デイリーダイジェスト")

        # チャンネル検索して投稿
        channel = self._find_channel(self._channel_name)
        if channel is None:
            log.warning(
                "ダイジェスト: チャンネル「%s」が見つかりません。投稿スキップ",
                self._channel_name,
            )
            self._collector.reset()
            return

        try:
            await channel.send(embed=embed)
            log.info(
                "ダイジェスト投稿完了: %d件 → %s",
                self._collector.total_count, self._channel_name,
            )
        except _discord.HTTPException as exc:
            log.error("ダイジェスト投稿エラー: %s", exc)

        # リセット
        self._collector.reset()

    def _find_channel(self, name: str):
        """名前でテキストチャンネルを検索する。"""
        import discord as _discord
        for guild in self._bot.guilds:
            for ch in guild.channels:
                if isinstance(ch, _discord.TextChannel) and ch.name == name:
                    return ch
        return None

    async def trigger_now(self) -> None:
        """手動トリガー（デバッグ/テスト用）。"""
        await self._post_digest()
