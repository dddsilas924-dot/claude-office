"""
dept_scheduler.py - 部門自発活動スケジューラー
================================================
各部門AIが定期的に自発的に発言するためのスケジューラー。
discord.ext.tasks を使ってループ実行する。

機能:
- 4時間ごとに全部門が順番に自発活動
- 深夜0-7時は静音モード
- /silence コマンドで一時停止
- コスト上限到達で自動停止
- 会議中は一時停止
"""
from __future__ import annotations

import asyncio
import logging
import random
from datetime import datetime, timezone, timedelta
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from dept_brain import DeptBrain

log = logging.getLogger("dept_scheduler")

JST = timezone(timedelta(hours=9))

# 自発活動のタイプ (時間帯で変わる)
_ACTIVITY_BY_HOUR: dict[range, list[str]] = {
    range(7, 10): ["morning"],           # 朝: 朝会報告
    range(10, 14): ["idea", "insight"],   # 午前: アイデア・インサイト
    range(14, 18): ["status", "question"],# 午後: 進捗・質問
    range(18, 24): ["idea", "status"],    # 夜: アイデア・進捗
}

# bridge は自発活動しない
_EXCLUDED_DEPTS = {"bridge"}


class DeptScheduler:
    """
    部門自発活動のスケジューラー。

    Usage:
        scheduler = DeptScheduler(brain, post_callback)
        scheduler.start()
        ...
        scheduler.stop()
    """

    def __init__(
        self,
        brain: DeptBrain,
        post_callback,  # async def(dept_id: str, text: str, is_deliverable: bool)
        interval_hours: float = 4.0,
    ):
        self._brain = brain
        self._post_callback = post_callback
        self._interval = interval_hours * 3600  # 秒に変換
        self._task: asyncio.Task | None = None
        self._running = False

        # 部門リスト (bridge除外)
        self._dept_ids = [
            d for d in brain.config.get("_dept_ids", list(self._brain._config.keys()))
            if d not in _EXCLUDED_DEPTS
        ]
        # dept_prompts から取得
        from dept_prompts import DEPT_PROMPTS
        self._dept_ids = [d for d in DEPT_PROMPTS if d not in _EXCLUDED_DEPTS]

    def start(self) -> None:
        """スケジューラーを開始する。"""
        if self._running:
            log.warning("スケジューラーは既に実行中です")
            return
        self._running = True
        self._task = asyncio.create_task(self._loop(), name="dept_scheduler")
        log.info(
            "部門自発活動スケジューラー開始: %d部門, %.1f時間間隔",
            len(self._dept_ids), self._interval / 3600,
        )

    def stop(self) -> None:
        """スケジューラーを停止する。"""
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
        log.info("部門自発活動スケジューラー停止")

    async def _loop(self) -> None:
        """メインループ。interval_hoursごとに全部門を巡回する。"""
        # 初回は起動後30秒待ってから開始 (Bot起動直後の安定待ち)
        await asyncio.sleep(30)

        while self._running:
            try:
                await self._run_cycle()
            except asyncio.CancelledError:
                log.info("スケジューラーループがキャンセルされました")
                break
            except Exception as exc:
                log.exception("スケジューラーエラー: %s", exc)

            # 次のサイクルまで待機
            await asyncio.sleep(self._interval)

    async def _run_cycle(self) -> None:
        """1サイクル: 全部門が順番に自発活動する。"""
        now_jst = datetime.now(JST)

        # 深夜チェック (brain側でもチェックするが、ループ自体もスキップ)
        quiet = self._brain.config.get("quiet_hours", {"start": 0, "end": 7})
        if quiet["start"] <= now_jst.hour < quiet["end"]:
            log.info("静音時間帯 (%02d:00 JST): サイクルスキップ", now_jst.hour)
            return

        # 会議中チェック
        if self._brain._meeting_active:
            log.info("会議中: 自発活動サイクルスキップ")
            return

        # 静音モードチェック
        if self._brain._silence_mode:
            log.info("静音モード: 自発活動サイクルスキップ")
            return

        log.info("=== 自発活動サイクル開始 (%s JST) ===", now_jst.strftime("%H:%M"))

        # 部門順をシャッフル (毎回ランダム)
        dept_order = list(self._dept_ids)
        random.shuffle(dept_order)

        # 時間帯に応じた活動タイプを決定
        activity_types = _get_activity_types(now_jst.hour)

        success_count = 0
        for dept_id in dept_order:
            if not self._running:
                break

            activity_type = random.choice(activity_types)

            try:
                text = await self._brain.generate_autonomous(dept_id, activity_type)
                if text:
                    is_deliv = self._brain.is_deliverable(text)
                    await self._post_callback(dept_id, text, is_deliv)
                    success_count += 1
                    log.info(
                        "自発活動: %s [%s] %s%s",
                        dept_id, activity_type,
                        text[:50] + "..." if len(text) > 50 else text,
                        " [成果物]" if is_deliv else "",
                    )
                else:
                    log.debug("自発活動: %s [%s] → 生成スキップ", dept_id, activity_type)
            except Exception as exc:
                log.error("自発活動エラー (%s): %s", dept_id, exc)

            # 部門間に少し間隔を空ける (レートリミット対策)
            await asyncio.sleep(5)

        log.info(
            "=== 自発活動サイクル完了: %d/%d 部門が発言 ===",
            success_count, len(dept_order),
        )

    async def trigger_single(self, dept_id: str, activity_type: str = "idea") -> str | None:
        """特定部門の自発活動を手動トリガーする (デバッグ/テスト用)。"""
        text = await self._brain.generate_autonomous(dept_id, activity_type)
        if text:
            is_deliv = self._brain.is_deliverable(text)
            await self._post_callback(dept_id, text, is_deliv)
        return text


def _get_activity_types(hour: int) -> list[str]:
    """時間帯に応じた活動タイプのリストを返す。"""
    for hour_range, types in _ACTIVITY_BY_HOUR.items():
        if hour in hour_range:
            return types
    return ["idea", "status"]  # デフォルト
