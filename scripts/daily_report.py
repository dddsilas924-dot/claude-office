"""
daily_report.py - 日報エンジン
================================
フィルの日報（AI側）とどらの日報（人間側）を管理する。

フィルの日報:
- 毎日22:00 JSTに自動生成
- 15部門の活動ログ + shared_state から集計
- 📝日報チャンネルに投稿

どらの日報:
- /nippo コマンドで手動投稿
- 📝日報チャンネルに投稿
- shared_stateにも記録
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from dept_brain import DeptBrain

log = logging.getLogger("daily_report")

JST = timezone(timedelta(hours=9))

# フィルの日報生成プロンプト
_PHIL_REPORT_SYSTEM = """あなたはフィル（司令塔AI）。
今日の15部門の活動を簡潔にまとめた日報を書く。

ルール:
- 「どらさん」と呼ぶ。「えむ」とは呼ばない
- 事実ベース。盛らない
- 各部門1行で要約（動きがなかった部門は省略）
- 各部門の「最終更新」日時を確認し、3日以上前のデータには「(X日前の情報)」と明示する
- 最後に「明日の注目ポイント」を1-2行
- 全体で400字以内
- 絵文字なし
"""


class DailyReportEngine:
    """日報の自動生成と手動投稿を管理する。"""

    def __init__(
        self,
        brain: DeptBrain,
        post_callback,   # async def(text: str, title: str, is_phil: bool)
        report_hour: int = 22,  # フィル日報の投稿時刻 (JST)
    ):
        self._brain = brain
        self._post_callback = post_callback
        self._report_hour = report_hour
        self._task: asyncio.Task | None = None
        self._running = False

    def start(self) -> None:
        """日報スケジューラーを開始する。"""
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._loop(), name="daily_report")
        log.info("日報スケジューラー開始: 毎日 %d:00 JST にフィル日報を自動投稿", self._report_hour)

    def stop(self) -> None:
        """日報スケジューラーを停止する。"""
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
        log.info("日報スケジューラー停止")

    async def _loop(self) -> None:
        """メインループ。毎日指定時刻にフィル日報を生成する。"""
        while self._running:
            try:
                now = datetime.now(JST)
                # 次の投稿時刻を計算
                target = now.replace(
                    hour=self._report_hour, minute=0, second=0, microsecond=0,
                )
                if now >= target:
                    target += timedelta(days=1)

                wait_seconds = (target - now).total_seconds()
                log.info(
                    "次のフィル日報: %s JST (%d秒後)",
                    target.strftime("%Y-%m-%d %H:%M"),
                    int(wait_seconds),
                )
                await asyncio.sleep(wait_seconds)

                # フィル日報を生成・投稿
                await self.generate_phil_report()

            except asyncio.CancelledError:
                break
            except Exception as exc:
                log.exception("日報スケジューラーエラー: %s", exc)
                await asyncio.sleep(60)

    async def generate_phil_report(self) -> str | None:
        """フィルの日報を生成して投稿する。"""
        today = datetime.now(JST).strftime("%Y-%m-%d")

        # shared_stateから今日の活動を集める
        activity_summary = self._collect_today_activity()

        prompt = (
            f"今日は{today}。以下が各部門の活動ログ:\n\n"
            f"{activity_summary}\n\n"
            "これをもとに、どらさんへの日報を書いて。"
        )

        try:
            response = await self._brain._call_api(
                _PHIL_REPORT_SYSTEM,
                [{"role": "user", "content": prompt}],
                "commander",
                "daily_report",
            )

            if response:
                title = f"フィルの日報 — {today}"
                await self._post_callback(response, title, True)
                log.info("フィル日報を投稿しました: %s", today)

                # shared_stateにも記録
                await self._save_report(today, "phil", response)

            return response

        except Exception as exc:
            log.error("フィル日報生成エラー: %s", exc)
            return None

    async def post_dora_report(self, text: str) -> None:
        """どらの日報を投稿する（/nippo コマンドから呼ばれる）。"""
        today = datetime.now(JST).strftime("%Y-%m-%d")
        title = f"どらの日報 — {today}"

        await self._post_callback(text, title, False)
        await self._save_report(today, "dora", text)
        log.info("どらの日報を投稿しました: %s", today)

    def _collect_today_activity(self) -> str:
        """shared_stateから今日のdiscord_activity_logを集める。"""
        env_path = os.environ.get("SHARED_STATE_PATH", "")
        if not env_path:
            return "(shared_state未設定)"

        state_path = Path(env_path)
        if not state_path.is_file():
            return "(shared_stateファイルなし)"

        try:
            with state_path.open(encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError):
            return "(読み込みエラー)"

        today = datetime.now(JST).strftime("%Y-%m-%d")
        lines = []

        departments = data.get("departments", {})
        for dept_id, dept_data in departments.items():
            if not isinstance(dept_data, dict):
                continue

            name = dept_data.get("name", dept_id)
            status = dept_data.get("status", "不明")
            updated_at = dept_data.get("updated_at", "不明")
            logs = dept_data.get("discord_activity_log", [])

            # 今日のログだけ抽出
            today_logs = [l for l in logs if today in str(l)]

            if today_logs:
                latest = today_logs[-1] if today_logs else ""
                lines.append(f"- {name}: {status} / 本日{len(today_logs)}件活動 / 最新: {latest} / 最終更新: {updated_at}")
            else:
                lines.append(f"- {name}: {status} / 本日活動なし / 最終更新: {updated_at}")

        return "\n".join(lines) if lines else "(部門データなし)"

    async def _save_report(self, date: str, author: str, text: str) -> None:
        """日報をshared_stateに記録する。"""
        env_path = os.environ.get("SHARED_STATE_PATH", "")
        if not env_path:
            return

        state_path = Path(env_path)
        if not state_path.is_file():
            return

        try:
            async with self._brain._state_write_lock:
                import fcntl
                lock_path = state_path.with_suffix(".lock")
                with open(lock_path, "w") as lock_file:
                    fcntl.flock(lock_file, fcntl.LOCK_EX)
                    try:
                        with state_path.open(encoding="utf-8") as f:
                            data = json.load(f)

                        # daily_reports セクションを確保
                        if "daily_reports" not in data:
                            data["daily_reports"] = {}
                        if date not in data["daily_reports"]:
                            data["daily_reports"][date] = {}

                        data["daily_reports"][date][author] = {
                            "text": text[:500],  # 500字で切る
                            "posted_at": datetime.now(JST).strftime("%Y-%m-%d %H:%M"),
                        }

                        # 古い日報は7日分だけ保持
                        reports = data["daily_reports"]
                        if len(reports) > 7:
                            sorted_dates = sorted(reports.keys())
                            for old_date in sorted_dates[:-7]:
                                del reports[old_date]

                        tmp_path = state_path.with_suffix(".tmp")
                        with tmp_path.open("w", encoding="utf-8") as f:
                            json.dump(data, f, ensure_ascii=False, indent=2)
                        tmp_path.replace(state_path)
                    finally:
                        fcntl.flock(lock_file, fcntl.LOCK_UN)

        except Exception as exc:
            log.debug("日報保存エラー: %s", exc)
