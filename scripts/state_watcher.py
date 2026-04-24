"""
state_watcher.py - shared_state変更監視・Discord自動報告
=========================================================
shared_state.jsonを60秒ごとにポーリングし、
部門のstatusやupdated_atが変わったらDiscordの該当チャンネルに報告する。

呼び出し方:
    watcher = StateWatcher(bot, state_path=Path(...), interval=60)
    await watcher.start()   # on_ready 内で呼ぶ
    await watcher.stop()    # shutdown 時に呼ぶ
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import discord

log = logging.getLogger("state_watcher")

# 部門ID → Discord チャンネル名の対応（discord_bot.py の RELAY_DEPT_TO_CHANNEL と同じ構造）
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

# 変更として検知するキー（updated_at は単独では通知しない — 保存のたびに変わって爆撃になる）
_WATCH_KEYS = ("status", "blockers", "last_output")

# 部門ごとの通知クールダウン（秒） — 同じ部門の通知を連続で出さない
_COOLDOWN_SECONDS = 300  # 5分


class _Change:
    """1部門の1変更を保持するデータクラス相当。"""

    __slots__ = ("dept_id", "key", "old_val", "new_val")

    def __init__(self, dept_id: str, key: str, old_val: Any, new_val: Any) -> None:
        self.dept_id = dept_id
        self.key = key
        self.old_val = old_val
        self.new_val = new_val

    @property
    def is_blocker_change(self) -> bool:
        return self.key == "blockers" and bool(self.new_val)

    def __repr__(self) -> str:
        return f"<_Change dept={self.dept_id} key={self.key}>"


class StateWatcher:
    """
    shared_state.json を定期ポーリングして Discord に変更を投稿するウォッチャー。

    Parameters
    ----------
    bot:
        CommanderBridgeBot インスタンス（discord.Client のサブクラス）
    state_path:
        shared_state.json の絶対パス
    interval:
        ポーリング間隔（秒）。デフォルト 60。
    """

    def __init__(
        self,
        bot: discord.Client,
        state_path: Path,
        interval: int = 60,
    ) -> None:
        self._bot = bot
        self._state_path = state_path
        self._interval = interval
        self._previous_state: dict[str, Any] = {}
        self._task: asyncio.Task[None] | None = None
        self._last_notified: dict[str, float] = {}  # dept_id → last notification timestamp

    # ------------------------------------------------------------------
    # 公開 API
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """ウォッチャーを起動する。on_ready 内から呼ぶ。"""
        if not self._state_path.is_file():
            log.warning(
                "StateWatcher: shared_state.json が見つかりません: %s — ウォッチャー無効",
                self._state_path,
            )
            return

        self._previous_state = self._load_state()
        self._task = asyncio.create_task(self._watch_loop(), name="state_watcher")
        log.info(
            "StateWatcher 起動: %s (interval=%ds)", self._state_path, self._interval
        )

    async def stop(self) -> None:
        """ウォッチャーを停止する。shutdown 時から呼ぶ。"""
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        log.info("StateWatcher 停止")

    # ------------------------------------------------------------------
    # 内部実装
    # ------------------------------------------------------------------

    def _load_state(self) -> dict[str, Any]:
        """shared_state.json を読み込んで返す。失敗時は空 dict。"""
        try:
            with self._state_path.open(encoding="utf-8") as f:
                return json.load(f)
        except Exception as exc:  # noqa: BLE001
            log.warning("StateWatcher: ファイル読み込みエラー: %s", exc)
            return {}

    def _detect_changes(self, current: dict[str, Any]) -> list[_Change]:
        """前回との差分を検出して _Change のリストを返す。"""
        changes: list[_Change] = []
        depts_current: dict[str, Any] = current.get("departments", {})
        depts_prev: dict[str, Any] = self._previous_state.get("departments", {})

        for dept_id, dept_data in depts_current.items():
            prev_dept = depts_prev.get(dept_id, {})
            for key in _WATCH_KEYS:
                new_val = dept_data.get(key)
                old_val = prev_dept.get(key)
                if new_val != old_val:
                    changes.append(_Change(dept_id, key, old_val, new_val))

        return changes

    async def _watch_loop(self) -> None:
        """メインループ: interval ごとに差分チェックして報告する。"""
        while True:
            await asyncio.sleep(self._interval)
            try:
                current = self._load_state()
                changes = self._detect_changes(current)
                if changes:
                    await self._report_changes(changes)
                self._previous_state = current
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                log.error("StateWatcher ループエラー: %s", exc)

    async def _report_changes(self, changes: list[_Change]) -> None:
        """変更リストを受けて各チャンネルに embed 投稿する。クールダウン付き。"""
        import time as _time

        now = _time.time()

        # 部門ごとにグルーピング
        by_dept: dict[str, list[_Change]] = {}
        for chg in changes:
            by_dept.setdefault(chg.dept_id, []).append(chg)

        for dept_id, dept_changes in by_dept.items():
            # ブロッカー追加はクールダウン無視で即通知
            critical = [c for c in dept_changes if c.is_blocker_change]
            if critical:
                await self._post_to_commander_log(dept_id, critical)

            # 通常変更はクールダウンチェック
            last = self._last_notified.get(dept_id, 0.0)
            if now - last < _COOLDOWN_SECONDS:
                log.debug(
                    "StateWatcher: %s のクールダウン中（残り%d秒）。通知スキップ。",
                    dept_id,
                    int(_COOLDOWN_SECONDS - (now - last)),
                )
                continue

            # 部門チャンネルへの通知
            await self._post_to_dept_channel(dept_id, dept_changes)
            self._last_notified[dept_id] = now

    async def _post_to_dept_channel(
        self, dept_id: str, changes: list[_Change]
    ) -> None:
        """該当部門チャンネルに状態変更を embed 投稿する。"""
        channel_name = _DEPT_TO_CHANNEL.get(dept_id)
        if not channel_name:
            log.debug("StateWatcher: 部門 %s のチャンネル名が不明。スキップ。", dept_id)
            return

        channel = self._find_channel(channel_name)
        if channel is None:
            log.debug("StateWatcher: チャンネル「%s」が見つかりません。", channel_name)
            return

        lines = []
        for chg in changes:
            old_str = self._val_to_str(chg.old_val)
            new_str = self._val_to_str(chg.new_val)
            lines.append(f"**{chg.key}**: {old_str} → {new_str}")

        embed = discord.Embed(
            title=f"状態更新: {dept_id}",
            description="\n".join(lines),
            color=0x2ECC71,  # 緑
            timestamp=datetime.now(timezone.utc),
        )
        embed.set_footer(text="StateWatcher / shared_state.json")

        try:
            await channel.send(embed=embed)
        except discord.HTTPException as exc:
            log.error(
                "StateWatcher: チャンネル「%s」への投稿失敗: %s", channel_name, exc
            )

    async def _post_to_commander_log(
        self, dept_id: str, critical_changes: list[_Change]
    ) -> None:
        """重大変更（ブロッカー追加等）を司令塔ログに投稿する。"""
        channel = self._find_channel("司令塔ログ")
        if channel is None:
            log.debug("StateWatcher: 司令塔ログチャンネルが見つかりません。")
            return

        lines = []
        for chg in critical_changes:
            new_str = self._val_to_str(chg.new_val)
            lines.append(f"**[{dept_id}] {chg.key}**: {new_str}")

        embed = discord.Embed(
            title="重大変更アラート",
            description="\n".join(lines),
            color=0xE74C3C,  # 赤
            timestamp=datetime.now(timezone.utc),
        )
        embed.set_footer(text="StateWatcher / ブロッカー検知")

        try:
            await channel.send(embed=embed)
        except discord.HTTPException as exc:
            log.error("StateWatcher: 司令塔ログへの投稿失敗: %s", exc)

    def _find_channel(self, name: str) -> discord.TextChannel | None:
        """名前でテキストチャンネルを検索する。guild が未設定なら None。
        Discord はチャンネル名を小文字正規化するため、大文字小文字を無視して比較する。"""
        name_lower = name.lower()
        for guild in self._bot.guilds:
            for ch in guild.channels:
                if isinstance(ch, discord.TextChannel) and ch.name.lower() == name_lower:
                    return ch
        return None

    @staticmethod
    def _val_to_str(val: Any) -> str:
        """値を表示用文字列に変換する。"""
        if val is None:
            return "(なし)"
        if isinstance(val, list):
            if not val:
                return "(空)"
            return ", ".join(str(v) for v in val[:3]) + ("…" if len(val) > 3 else "")
        return str(val)[:80]
