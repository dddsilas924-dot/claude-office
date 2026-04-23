"""
discord_logger.py - Discord会話ログエンジン
============================================
Discordボットで発生する全会話・指示・アクションを記録する。

記録先:
- shared_state.json: departments[dept_id].discord_log（部門別、最大100件）
- logs/discord_{date}.jsonl: 日付別フラットログ（JSONLフォーマット）

命名規則:
- タイムスタンプはすべてJST（UTC+9）
- instruction_id: "INST-{YYYYMMDD}-{seq}"
"""
from __future__ import annotations

import asyncio
import fcntl
import json
import logging
import os
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Literal

log = logging.getLogger("discord_logger")

# JST定義
JST = timezone(timedelta(hours=9))

# ログタイプ定義
LogType = Literal["respond", "autonomous", "meeting", "instruction", "daily_report"]

# 各制限値
_MAX_DISCORD_LOG_PER_DEPT = 15     # 部門ごとのdiscord_log最大件数（shared_state肥大化防止）
_MAX_PENDING_INSTRUCTIONS = 50     # pending_instructionsの最大件数
_INSTRUCTION_ARCHIVE_DAYS = 7      # 完了済みinstructionのアーカイブ閾値（日）
_LOG_RETENTION_DAYS = 30           # ログファイルの保持日数


def _now_jst_str() -> str:
    """現在のJST時刻を "YYYY-MM-DD HH:MM" 形式で返す。"""
    return datetime.now(JST).strftime("%Y-%m-%d %H:%M")


def _today_jst() -> str:
    """今日の日付を "YYYY-MM-DD" 形式で返す。"""
    return datetime.now(JST).strftime("%Y-%m-%d")


class DiscordLogger:
    """
    Discord会話ログエンジン。

    shared_state.json と 日付別JSONLファイルの両方に記録する。
    ファイル書き込みは fcntl ロック + asyncio.Lock で二重保護する。
    """

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._inst_seq: dict[str, int] = {}  # date -> 連番カウンター

        # logsディレクトリを確保する
        logs_dir = self._logs_dir()
        if logs_dir:
            logs_dir.mkdir(parents=True, exist_ok=True)

    # --------------------------------------------------------------------------
    # パスユーティリティ
    # --------------------------------------------------------------------------

    def _state_path(self) -> Path | None:
        """shared_state.jsonのパスを返す。未設定・存在しない場合はNone。"""
        env_path = os.environ.get("SHARED_STATE_PATH", "")
        if not env_path:
            return None
        p = Path(env_path)
        return p if p.is_file() else None

    def _logs_dir(self) -> Path | None:
        """ログディレクトリを返す。SHARED_STATE_PATHの隣に logs/ を置く。"""
        env_path = os.environ.get("SHARED_STATE_PATH", "")
        if not env_path:
            return None
        return Path(env_path).parent / "logs"

    def _jsonl_path(self, date: str | None = None) -> Path | None:
        """日付別JSONLファイルのパスを返す。"""
        logs_dir = self._logs_dir()
        if not logs_dir:
            return None
        d = date or _today_jst()
        return logs_dir / f"discord_{d}.jsonl"

    # --------------------------------------------------------------------------
    # shared_state.json の読み書き（fcntl + asyncio.Lock）
    # --------------------------------------------------------------------------

    async def _read_state(self) -> dict | None:
        """shared_state.jsonを読み込む。失敗時はNoneを返す。"""
        state_path = self._state_path()
        if not state_path:
            return None
        try:
            with state_path.open(encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError) as exc:
            log.error("shared_state読み込みエラー: %s", exc)
            return None

    async def _write_state(self, data: dict) -> bool:
        """
        shared_state.jsonに書き込む。

        fcntlロックを使い、tmpファイル経由でアトミックに置換する。
        Returns True on success.
        """
        state_path = self._state_path()
        if not state_path:
            return False

        try:
            async with self._lock:
                lock_path = state_path.with_suffix(".lock")
                with open(lock_path, "w") as lock_file:
                    fcntl.flock(lock_file, fcntl.LOCK_EX)
                    try:
                        tmp_path = state_path.with_suffix(".tmp")
                        with tmp_path.open("w", encoding="utf-8") as f:
                            json.dump(data, f, ensure_ascii=False, indent=2)
                        tmp_path.replace(state_path)
                    finally:
                        fcntl.flock(lock_file, fcntl.LOCK_UN)
            return True
        except Exception as exc:
            log.error("shared_state書き込みエラー: %s", exc)
            return False

    # --------------------------------------------------------------------------
    # JSONLファイルへの追記
    # --------------------------------------------------------------------------

    async def _append_jsonl(self, entry: dict) -> None:
        """JSONLファイルにエントリを1行追記する。"""
        jsonl_path = self._jsonl_path()
        if not jsonl_path:
            return

        try:
            # logsディレクトリを確保
            jsonl_path.parent.mkdir(parents=True, exist_ok=True)

            async with self._lock:
                with jsonl_path.open("a", encoding="utf-8") as f:
                    f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except Exception as exc:
            log.error("JSONLファイル追記エラー: %s", exc)

    # --------------------------------------------------------------------------
    # パブリックAPI
    # --------------------------------------------------------------------------

    async def log_conversation(
        self,
        dept_id: str,
        channel: str,
        sender: str,
        message: str,
        response: str,
        log_type: LogType,
    ) -> None:
        """
        会話ログを記録する。

        Args:
            dept_id:  部門ID（例: "commander", "research"）
            channel:  Discordチャンネル名
            sender:   発言者（ユーザー名またはbot名）
            message:  受信したメッセージ
            response: botの返答
            log_type: ログ種別（"respond"|"autonomous"|"meeting"|"instruction"|"daily_report"）
        """
        now = _now_jst_str()
        entry: dict = {
            "timestamp": now,
            "dept_id": dept_id,
            "channel": channel,
            "sender": sender,
            "message": message[:500],    # 長文は切り詰め
            "response": response[:1000],
            "log_type": log_type,
        }

        # 1. JSONLファイルに追記（先にファイルへ。shared_stateより軽い処理）
        await self._append_jsonl(entry)

        # 2. shared_state.jsonを更新
        data = await self._read_state()
        if data is None:
            return

        departments = data.setdefault("departments", {})
        dept = departments.setdefault(dept_id, {})
        discord_log: list = dept.setdefault("discord_log", [])

        discord_log.append(entry)

        # 最大件数を超えた分は古い順に削除
        if len(discord_log) > _MAX_DISCORD_LOG_PER_DEPT:
            dept["discord_log"] = discord_log[-_MAX_DISCORD_LOG_PER_DEPT:]

        await self._write_state(data)
        log.debug("会話ログ記録: dept=%s type=%s", dept_id, log_type)

    async def log_instruction(
        self,
        dept_id: str,
        sender: str,
        instruction: str,
        instruction_id: str | None = None,
    ) -> str:
        """
        指示をpending_instructionsに登録する。

        Args:
            dept_id:        指示先の部門ID
            sender:         指示を出した人（ユーザー名）
            instruction:    指示内容
            instruction_id: 指定がなければ自動採番する

        Returns:
            採番されたinstruction_id（例: "INST-20260423-001"）
        """
        today = _today_jst()

        # 連番を採番
        if instruction_id is None:
            seq = self._inst_seq.get(today, 0) + 1
            self._inst_seq = {today: seq}  # 他の日付は不要なので置き換え
            instruction_id = f"INST-{today.replace('-', '')}-{seq:03d}"

        now = _now_jst_str()
        inst_entry: dict = {
            "instruction_id": instruction_id,
            "dept_id": dept_id,
            "sender": sender,
            "instruction": instruction[:500],
            "status": "queued",
            "created_at": now,
            "completed_at": None,
            "result": None,
        }

        # JSONLにも記録
        await self._append_jsonl({
            **inst_entry,
            "log_type": "instruction",
            "timestamp": now,
            "channel": "instruction",
            "message": instruction[:500],
            "response": "",
        })

        # shared_stateに保存
        data = await self._read_state()
        if data is None:
            return instruction_id

        departments = data.setdefault("departments", {})
        dept = departments.setdefault(dept_id, {})
        pending: list = dept.setdefault("pending_instructions", [])

        pending.append(inst_entry)

        # 上限を超えた場合はクリーンアップ後に保持
        if len(pending) > _MAX_PENDING_INSTRUCTIONS:
            dept["pending_instructions"] = pending[-_MAX_PENDING_INSTRUCTIONS:]

        await self._write_state(data)
        log.info("指示登録: %s → %s", instruction_id, dept_id)
        return instruction_id

    async def update_instruction(
        self,
        dept_id: str,
        instruction_id: str,
        status: Literal["queued", "executing", "completed", "failed", "cancelled"],
        result: str | None = None,
    ) -> bool:
        """
        指示のステータスを更新する。

        遷移: queued → executing → completed | failed | cancelled

        Args:
            dept_id:        部門ID
            instruction_id: 更新対象のID
            status:         新しいステータス
            result:         完了・失敗時の結果テキスト（任意）

        Returns:
            更新に成功した場合True
        """
        data = await self._read_state()
        if data is None:
            return False

        departments = data.get("departments", {})
        dept = departments.get(dept_id, {})
        pending: list = dept.get("pending_instructions", [])

        found = False
        now = _now_jst_str()
        terminal_statuses = {"completed", "failed", "cancelled"}

        for inst in pending:
            if inst.get("instruction_id") == instruction_id:
                inst["status"] = status
                if result is not None:
                    inst["result"] = result[:500]
                if status in terminal_statuses:
                    inst["completed_at"] = now
                found = True
                break

        if not found:
            log.warning("指示が見つかりません: %s / %s", dept_id, instruction_id)
            return False

        data["departments"][dept_id]["pending_instructions"] = pending
        success = await self._write_state(data)
        if success:
            log.info("指示ステータス更新: %s → %s", instruction_id, status)
        return success

    async def get_dept_log(self, dept_id: str, limit: int = 20) -> list[dict]:
        """
        部門の最近のdiscord_logエントリを返す。

        Args:
            dept_id: 部門ID
            limit:   返す件数の上限

        Returns:
            最新のエントリを先頭にした最大limit件のリスト
        """
        data = await self._read_state()
        if data is None:
            return []

        dept = data.get("departments", {}).get(dept_id, {})
        discord_log: list = dept.get("discord_log", [])

        # 新しい順で返す
        return discord_log[-limit:][::-1]

    async def get_instruction_log(self, limit: int = 10) -> list[dict]:
        """
        全部門の指示ログを新しい順で返す。

        Args:
            limit: 返す件数の上限

        Returns:
            タイムスタンプ降順のリスト
        """
        data = await self._read_state()
        if data is None:
            return []

        all_insts: list[dict] = []
        departments = data.get("departments", {})

        for dept_data in departments.values():
            if not isinstance(dept_data, dict):
                continue
            pending = dept_data.get("pending_instructions", [])
            all_insts.extend(pending)

        # created_at で降順ソート
        all_insts.sort(key=lambda x: x.get("created_at", ""), reverse=True)
        return all_insts[:limit]

    async def get_today_summary(self, dept_id: str) -> str:
        """
        今日の部門活動サマリーをテキストで返す。

        Returns:
            例: "5件の活動: 返答3件, 自発1件, 指示1件"
        """
        data = await self._read_state()
        if data is None:
            return "データ取得不可"

        dept = data.get("departments", {}).get(dept_id, {})
        discord_log: list = dept.get("discord_log", [])
        today = _today_jst()

        # 今日のエントリのみ抽出
        today_logs = [
            e for e in discord_log
            if isinstance(e, dict) and e.get("timestamp", "").startswith(today)
        ]

        if not today_logs:
            return "本日の活動なし"

        # log_typeごとにカウント
        counts: dict[str, int] = {}
        for e in today_logs:
            t = e.get("log_type", "unknown")
            counts[t] = counts.get(t, 0) + 1

        total = len(today_logs)
        respond_n = counts.get("respond", 0)
        autonomous_n = counts.get("autonomous", 0)
        instruction_n = counts.get("instruction", 0)

        # 主要3種以外の件数
        other_n = total - respond_n - autonomous_n - instruction_n

        parts = []
        if respond_n:
            parts.append(f"返答{respond_n}件")
        if autonomous_n:
            parts.append(f"自発{autonomous_n}件")
        if instruction_n:
            parts.append(f"指示{instruction_n}件")
        if other_n:
            parts.append(f"その他{other_n}件")

        detail = ", ".join(parts) if parts else "詳細不明"
        return f"{total}件の活動: {detail}"

    # --------------------------------------------------------------------------
    # 自動クリーンアップ
    # --------------------------------------------------------------------------

    async def cleanup(self) -> None:
        """
        古いデータを自動クリーンアップする。

        - 完了済み指示で7日以上前のものをアーカイブ（削除）
        - 30日以上前のJSONLファイルを削除
        """
        try:
            await self._cleanup_old_instructions()
            self._cleanup_old_log_files()
        except Exception as exc:
            log.error("クリーンアップエラー: %s", exc)

    async def _cleanup_old_instructions(self) -> None:
        """7日以上前に完了した指示をpending_instructionsから除去する。"""
        data = await self._read_state()
        if data is None:
            return

        now_jst = datetime.now(JST)
        threshold_str = (now_jst - timedelta(days=_INSTRUCTION_ARCHIVE_DAYS)).strftime("%Y-%m-%d %H:%M")
        terminal_statuses = {"completed", "failed", "cancelled"}

        modified = False
        departments = data.get("departments", {})

        for dept_id, dept_data in departments.items():
            if not isinstance(dept_data, dict):
                continue

            pending: list = dept_data.get("pending_instructions", [])
            filtered = [
                inst for inst in pending
                if not (
                    inst.get("status") in terminal_statuses
                    and (inst.get("completed_at") or "") < threshold_str
                )
            ]

            removed = len(pending) - len(filtered)
            if removed > 0:
                dept_data["pending_instructions"] = filtered
                modified = True
                log.debug("指示アーカイブ: %s から%d件削除", dept_id, removed)

        if modified:
            await self._write_state(data)
            log.info("古い完了済み指示のクリーンアップ完了")

    def _cleanup_old_log_files(self) -> None:
        """30日以上前のJSONLログファイルを削除する。"""
        logs_dir = self._logs_dir()
        if not logs_dir or not logs_dir.is_dir():
            return

        now_jst = datetime.now(JST)
        cutoff = now_jst - timedelta(days=_LOG_RETENTION_DAYS)

        for log_file in logs_dir.glob("discord_*.jsonl"):
            # ファイル名から日付を抽出: discord_YYYY-MM-DD.jsonl
            stem = log_file.stem  # "discord_YYYY-MM-DD"
            date_part = stem.removeprefix("discord_")
            try:
                file_date = datetime.strptime(date_part, "%Y-%m-%d").replace(tzinfo=JST)
                if file_date < cutoff:
                    log_file.unlink()
                    log.info("古いログファイルを削除: %s", log_file.name)
            except ValueError:
                # 日付パースに失敗したファイルは無視
                continue
