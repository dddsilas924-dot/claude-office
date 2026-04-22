"""
dept_meeting.py - 会議室ディスカッションエンジン
=================================================
会議室チャンネルで複数部門AIが議論するためのエンジン。

フロー:
1. /meeting [議題] or 自発活動からのトリガー or 定期(14:00 JST)
2. 関連部門を3-5自動選択
3. 3ラウンドの議論 (各部門が順番に発言)
4. フィルがまとめを投稿
5. アクションアイテムを各部門チャンネルにフォワード
"""
from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timezone, timedelta
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from dept_brain import DeptBrain

from dept_prompts import DEPT_PROMPTS

log = logging.getLogger("dept_meeting")

JST = timezone(timedelta(hours=9))


class MeetingEngine:
    """
    会議室ディスカッションを管理するエンジン。

    Usage:
        engine = MeetingEngine(brain, post_callback, forward_callback)
        await engine.start_meeting("AI投資の方向性")
    """

    def __init__(
        self,
        brain: DeptBrain,
        post_callback,    # async def(text: str, dept_id: str, embed_title: str | None)
        forward_callback, # async def(dept_id: str, action_text: str)
        canvas_callback,  # async def(dept_id: str, message: str, event_kind: str)
    ):
        self._brain = brain
        self._post = post_callback
        self._forward = forward_callback
        self._canvas = canvas_callback
        self._active = False
        self._current_topic: str | None = None

    @property
    def is_active(self) -> bool:
        return self._active

    async def start_meeting(
        self,
        topic: str,
        forced_participants: list[str] | None = None,
    ) -> bool:
        """
        会議を開始する。

        Args:
            topic: 議題
            forced_participants: 参加部門を強制指定 (Noneならキーワードで自動選択)

        Returns:
            True: 会議完了, False: エラーで中断
        """
        if self._active:
            log.warning("既に会議が進行中です: %s", self._current_topic)
            return False

        self._active = True
        self._current_topic = topic
        self._brain.set_meeting_active(True)

        try:
            # 参加者選択
            participants = forced_participants or self._brain.select_participants(topic)
            if not participants:
                log.error("会議参加者が0人です: topic=%s", topic)
                return False

            participant_names = [
                DEPT_PROMPTS.get(d, {}).get("name", d) for d in participants
            ]

            log.info(
                "=== 会議開始: 「%s」 参加: %s ===",
                topic, ", ".join(participant_names),
            )

            # 会議開始アナウンス
            announce = (
                f"**会議開始**\n"
                f"議題: {topic}\n"
                f"参加者: {', '.join(participant_names)}\n"
                f"ラウンド: 3回\n"
                f"まとめ: フィル（司令塔）"
            )
            await self._post(announce, "commander", "会議開始")
            await self._canvas("commander", f"会議開始: {topic}", "STATUS_UPDATE")

            # 3ラウンドの議論
            history: list[dict[str, str]] = []
            config = self._brain.config
            delay = config.get("meeting_turn_delay_sec", 3)
            rounds = config.get("meeting_rounds", 3)

            for round_num in range(1, rounds + 1):
                round_label = {1: "初見コメント", 2: "深掘り", 3: "アクション提案"}.get(
                    round_num, f"ラウンド{round_num}"
                )
                await self._post(
                    f"--- ラウンド {round_num}/{rounds}: {round_label} ---",
                    "commander",
                    None,
                )

                for dept_id in participants:
                    if not self._active:
                        log.info("会議が中断されました")
                        return False

                    dept_name = DEPT_PROMPTS.get(dept_id, {}).get("name", dept_id)

                    # AI発言生成
                    text = await self._brain.meeting_turn(
                        topic=topic,
                        participants=participants,
                        history=history,
                        current_dept=dept_id,
                        round_num=round_num,
                    )

                    if text:
                        history.append({
                            "dept_id": dept_id,
                            "name": dept_name,
                            "message": text,
                            "round": str(round_num),
                        })
                        await self._post(text, dept_id, None)
                        await self._canvas(dept_id, text[:200], "ASK_COMPLETED")
                    else:
                        log.warning("会議発言生成失敗: %s round=%d", dept_id, round_num)
                        history.append({
                            "dept_id": dept_id,
                            "name": dept_name,
                            "message": "(発言なし)",
                            "round": str(round_num),
                        })

                    # 部門間の間隔
                    await asyncio.sleep(delay)

            # フィルのまとめ
            log.info("=== 会議まとめ生成中 ===")
            summary = await self._brain.meeting_summary(topic, history)

            if summary:
                await self._post(summary, "commander", f"会議まとめ: {topic}")
                await self._canvas("commander", f"会議まとめ完了: {topic}", "ASK_COMPLETED")

                # アクションアイテムを各部門にフォワード
                await self._forward_actions(summary, participants)
            else:
                await self._post(
                    "会議まとめの生成に失敗しました。議論の記録は上記を参照してください。",
                    "commander",
                    None,
                )

            log.info("=== 会議完了: 「%s」 %d発言 ===", topic, len(history))
            return True

        except Exception as exc:
            log.exception("会議エラー: %s", exc)
            await self._post(
                f"会議中にエラーが発生しました: {exc}",
                "commander",
                None,
            )
            return False

        finally:
            self._active = False
            self._current_topic = None
            self._brain.set_meeting_active(False)

    def cancel(self) -> None:
        """進行中の会議を中断する。"""
        if self._active:
            self._active = False
            log.info("会議中断: %s", self._current_topic)

    async def _forward_actions(
        self,
        summary: str,
        participants: list[str],
    ) -> None:
        """まとめからアクションアイテムを抽出して各部門にフォワードする。"""
        # アクションアイテムセクションを抽出
        action_section = ""
        in_action = False
        for line in summary.split("\n"):
            if "アクションアイテム" in line:
                in_action = True
                continue
            if in_action:
                if line.startswith("■") or line.startswith("##"):
                    break
                action_section += line + "\n"

        if not action_section.strip():
            return

        # 部門名でフィルタリングしてフォワード
        for dept_id in participants:
            dept_name = DEPT_PROMPTS.get(dept_id, {}).get("name", dept_id)
            dept_role = DEPT_PROMPTS.get(dept_id, {}).get("role", "")

            # 部門名・役割名がアクションアイテムに含まれているか
            relevant_lines = []
            for line in action_section.strip().split("\n"):
                line_stripped = line.strip()
                if not line_stripped:
                    continue
                if (dept_name in line_stripped
                        or dept_id in line_stripped
                        or any(word in line_stripped for word in [dept_role])):
                    relevant_lines.append(line_stripped)

            if relevant_lines:
                action_msg = (
                    f"**会議アクション** (議題: {self._current_topic})\n"
                    + "\n".join(relevant_lines)
                )
                await self._forward(dept_id, action_msg)
                log.info("アクションフォワード: %s → %s", self._current_topic, dept_id)


class MeetingScheduler:
    """
    定期会議のスケジューラー。
    毎日14:00 JSTに自動で会議を開始する。
    """

    def __init__(
        self,
        meeting_engine: MeetingEngine,
        schedule_hour: int = 14,
    ):
        self._engine = meeting_engine
        self._hour = schedule_hour
        self._task: asyncio.Task | None = None
        self._running = False

        # 定期会議の議題プール
        self._topics = [
            "今週の各部門の成果と来週の方針",
            "部門間の連携で改善できること",
            "えむへの提案事項まとめ",
            "各部門の課題とボトルネック",
            "新しいビジネスチャンスの共有",
            "コスト削減と効率化のアイデア",
            "コンテンツ戦略の方向性",
            "顧客からのフィードバック活用",
        ]
        self._topic_index = 0

    def start(self) -> None:
        """定期会議スケジューラーを開始。"""
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._loop(), name="meeting_scheduler")
        log.info("定期会議スケジューラー開始: 毎日 %02d:00 JST", self._hour)

    def stop(self) -> None:
        """定期会議スケジューラーを停止。"""
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
        log.info("定期会議スケジューラー停止")

    async def _loop(self) -> None:
        """毎日指定時刻に会議を自動開始する。"""
        while self._running:
            try:
                now_jst = datetime.now(JST)
                # 次の実行時刻を計算
                target = now_jst.replace(
                    hour=self._hour, minute=0, second=0, microsecond=0,
                )
                if now_jst >= target:
                    target += timedelta(days=1)

                wait_sec = (target - now_jst).total_seconds()
                log.info(
                    "次の定期会議: %s JST (%.0f秒後)",
                    target.strftime("%Y-%m-%d %H:%M"), wait_sec,
                )

                await asyncio.sleep(wait_sec)

                if not self._running:
                    break

                # 議題を順繰りに選択
                topic = self._topics[self._topic_index % len(self._topics)]
                self._topic_index += 1

                log.info("定期会議トリガー: 「%s」", topic)
                await self._engine.start_meeting(topic)

            except asyncio.CancelledError:
                break
            except Exception as exc:
                log.exception("定期会議スケジューラーエラー: %s", exc)
                await asyncio.sleep(60)  # エラー時は1分待って再試行
