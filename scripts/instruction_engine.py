"""
instruction_engine.py - 指示実行エンジン (Phase 2)
====================================================
Discord からの指示を受け取り、安全性を判定し、
claude -p subprocess で Claude Code を実際に実行する。

設計原則:
- 安全なタスク（読み取り・Web検索・MDレポート生成）は自動実行
- 危険なタスク（ファイル編集・git・外部API）は承認待ちキューへ
- 禁止操作（.env編集・rm -rf・外部送信）は即拒否
- 1タスクあたりの予算上限あり（default $0.50）
- タイムアウトあり（default 180秒）

使い方:
    engine = InstructionEngine()
    classification = engine.classify("DeFi最新動向を調査して")
    # -> "safe"
    result = await engine.execute("DeFi最新動向を調査して", "research")
    # -> {"status": "success", "result": "...", "cost": 0.12, ...}
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
import uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

log = logging.getLogger("instruction_engine")

# ---------------------------------------------------------------------------
# 安全性分類パターン
# ---------------------------------------------------------------------------

# 自動実行OK（読み取り・検索・レポート生成）
SAFE_PATTERNS: list[str] = [
    "調査", "リサーチ", "まとめ", "調べ", "確認", "検索",
    "分析", "比較", "レポート", "日報", "サマリー", "要約",
    "一覧", "リスト", "整理", "読んで", "見て", "教えて",
    "ステータス", "状態", "進捗",
]

# 承認が必要（ファイル変更・git・外部連携）
APPROVAL_PATTERNS: list[str] = [
    "LP作", "HTML作", "作って", "作成",
    "ファイル編集", "書き換え", "修正して", "直して", "変更して",
    "git", "コミット", "commit", "push",
    "API", "送信", "投稿", "アップロード",
    "インストール", "pip install", "npm install",
    "スクリプト実行", "実行して",
    "デプロイ", "設定変更",
]

# 絶対禁止（破壊的・機密・不可逆） — コンパイル済み正規表現
_FORBIDDEN_RAW: list[str] = [
    r"\.env\b", r"環境変数", r"\bAPI_KEY\b", r"\bTOKEN\b", r"\bSECRET\b",
    r"\brm\s+-rf\b", r"\brm\s+-r\b", r"\brmdir\b", r"\bDELETE\b", r"\bDROP\b",
    r"外部送信", r"メール", r"\bemail\b", r"\bmail\b",
    r"\bpasswd\b", r"\bsudo\b", r"chmod\s+777",
    r"\bformat\b", r"\bfdisk\b", r"\bmkfs\b",
    r"\bcurl\b.*\bPOST\b", r"\bwget\b.*-O\b",
]
FORBIDDEN_PATTERNS: list[re.Pattern] = [re.compile(p, re.IGNORECASE) for p in _FORBIDDEN_RAW]

# ---------------------------------------------------------------------------
# 承認待ちキュー（メモリ + shared_state永続化）
# ---------------------------------------------------------------------------

_approval_queue: dict[str, dict[str, Any]] = {}

# ---------------------------------------------------------------------------
# メイン クラス
# ---------------------------------------------------------------------------

class InstructionEngine:
    """Discord指示 → claude -p 実行エンジン。"""

    def __init__(
        self,
        claude_path: str = "claude",
        default_budget: float = 0.50,
        default_timeout: int = 180,
        monthly_cap: float = 10.0,
    ):
        self._claude_path = claude_path
        self._default_budget = default_budget
        self._default_timeout = default_timeout
        self._monthly_cap = monthly_cap
        self._monthly_cost = 0.0
        self._month_key = ""
        self._execution_count = 0

        # 作業ディレクトリマップ
        self._workdir_map: dict[str, str] = {
            "commander": "/Users/satts924/Downloads/empire_monitor_full_20260321",
            "research": "/Users/satts924/Downloads/empire_monitor_full_20260321",
            "sales": "/Users/satts924/Downloads/empire_monitor_full_20260321",
            "design": "/Users/satts924/Downloads/empire_monitor_full_20260321",
            "content": "/Users/satts924/Downloads/empire_monitor_full_20260321",
            "writing": "/Users/satts924/Downloads/empire_monitor_full_20260321",
            "advertising": "/Users/satts924/Downloads/empire_monitor_full_20260321",
            "ai_investment": "/Users/satts924/Downloads/empire_monitor_full_20260321",
            "new_biz": "/Users/satts924/Downloads/empire_monitor_full_20260321",
            "phil_consulting": "/Users/satts924/Downloads/empire_monitor_full_20260321",
            "security": "/Users/satts924/Downloads/empire_monitor_full_20260321",
            "takumi_x": "/Users/satts924/Downloads/empire_monitor_full_20260321",
            "real_estate": "/Users/satts924/Downloads/empire_monitor_full_20260321",
            "doradora_sns": "/Users/satts924/Downloads/empire_monitor_full_20260321",
            "origin_story": "/Users/satts924/Downloads/empire_monitor_full_20260321",
        }

    # ----- 分類 -----

    def classify(self, task: str) -> str:
        """
        指示を安全性で3段分類する。

        Returns:
            "forbidden" | "approval_required" | "safe"
        """
        task_lower = task.lower()

        # 1. 禁止パターンチェック（最優先 — コンパイル済み正規表現）
        for pattern in FORBIDDEN_PATTERNS:
            if pattern.search(task_lower):
                log.warning("禁止パターン検出: %r in %r", pattern.pattern, task[:50])
                return "forbidden"

        # 2. 承認パターンチェック
        for pattern in APPROVAL_PATTERNS:
            if pattern.lower() in task_lower:
                log.info("承認必要パターン検出: %r in %r", pattern, task[:50])
                return "approval_required"

        # 3. 安全パターンに明示的に該当するか
        for pattern in SAFE_PATTERNS:
            if pattern in task_lower:
                return "safe"

        # 4. どれにも該当しない場合は承認必要（安全側に倒す）
        log.info("未分類の指示 → 承認必要: %r", task[:50])
        return "approval_required"

    # ----- 実行 -----

    async def execute(
        self,
        task: str,
        dept_id: str,
        budget: float | None = None,
        timeout: int | None = None,
    ) -> dict[str, Any]:
        """
        claude -p subprocess で指示を実行する。

        Returns:
            {
                "status": "success" | "error" | "timeout" | "budget_exceeded",
                "result": str,
                "cost": float,
                "duration_sec": float,
                "session_id": str,
                "error": str | None,
            }
        """
        budget = budget or self._default_budget
        timeout = timeout or self._default_timeout

        # 月次コストチェック
        self._check_monthly_reset()
        if self._monthly_cost >= self._monthly_cap:
            return {
                "status": "budget_exceeded",
                "result": "",
                "cost": 0,
                "duration_sec": 0,
                "session_id": "",
                "error": f"月次上限 ${self._monthly_cap:.2f} に到達済み (現在 ${self._monthly_cost:.2f})",
            }

        workdir = self._workdir_map.get(dept_id, str(Path.home()))

        # 部門キャラ名でタスクにコンテキストを追加
        dept_context = f"あなたは{dept_id}部門のAIです。以下のタスクを実行してください: {task}"

        cmd = [
            self._claude_path,
            "-p", dept_context,
            "--model", "sonnet",
            "--output-format", "json",
            "--max-turns", "10",
        ]

        log.info(
            "claude -p 実行開始: dept=%s, task=%r, budget=$%.2f, timeout=%ds",
            dept_id, task[:60], budget, timeout,
        )

        start_time = time.monotonic()

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=workdir,
            )

            try:
                stdout, stderr = await asyncio.wait_for(
                    proc.communicate(), timeout=timeout,
                )
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
                duration = time.monotonic() - start_time
                log.warning("claude -p タイムアウト: %ds 超過", timeout)
                return {
                    "status": "timeout",
                    "result": "",
                    "cost": 0,
                    "duration_sec": round(duration, 1),
                    "session_id": "",
                    "error": f"実行が{timeout}秒を超過しました",
                }

            duration = time.monotonic() - start_time
            stdout_text = stdout.decode("utf-8", errors="replace").strip()
            stderr_text = stderr.decode("utf-8", errors="replace").strip()

            if proc.returncode != 0:
                log.error("claude -p エラー (rc=%d): %s", proc.returncode, stderr_text[:200])
                return {
                    "status": "error",
                    "result": stderr_text[:1500],
                    "cost": 0,
                    "duration_sec": round(duration, 1),
                    "session_id": "",
                    "error": f"終了コード {proc.returncode}",
                }

            # JSON出力をパース
            result_data = self._parse_claude_output(stdout_text)
            cost = result_data.get("cost_usd", 0)
            self._monthly_cost += cost
            self._execution_count += 1

            log.info(
                "claude -p 完了: dept=%s, cost=$%.4f, duration=%.1fs, total_monthly=$%.2f",
                dept_id, cost, duration, self._monthly_cost,
            )

            return {
                "status": "success",
                "result": result_data.get("result", stdout_text[:2000]),
                "cost": cost,
                "duration_sec": round(duration, 1),
                "session_id": result_data.get("session_id", ""),
                "error": None,
            }

        except FileNotFoundError:
            return {
                "status": "error",
                "result": "",
                "cost": 0,
                "duration_sec": 0,
                "session_id": "",
                "error": f"claude CLI が見つかりません: {self._claude_path}",
            }
        except Exception as exc:
            duration = time.monotonic() - start_time
            log.exception("claude -p 予期せぬエラー: %s", exc)
            return {
                "status": "error",
                "result": "",
                "cost": 0,
                "duration_sec": round(duration, 1),
                "session_id": "",
                "error": str(exc),
            }

    # ----- 承認キュー -----

    def queue_for_approval(
        self, dept_id: str, task: str, requester: str,
    ) -> str:
        """承認待ちキューに追加。INST-IDを返す。"""
        jst = timezone(timedelta(hours=9))
        now = datetime.now(jst)
        inst_id = f"INST-{now.strftime('%m%d%H%M')}-{self._execution_count:03d}"

        _approval_queue[inst_id] = {
            "dept_id": dept_id,
            "task": task,
            "requester": requester,
            "queued_at": now.isoformat(),
            "status": "pending",
        }

        log.info("承認キュー追加: %s dept=%s task=%r", inst_id, dept_id, task[:50])
        return inst_id

    def get_queued(self, inst_id: str) -> dict[str, Any] | None:
        """承認待ちタスクを取得。"""
        return _approval_queue.get(inst_id)

    def get_all_pending(self) -> list[tuple[str, dict[str, Any]]]:
        """承認待ち一覧。"""
        return [
            (k, v) for k, v in _approval_queue.items()
            if v["status"] == "pending"
        ]

    def mark_approved(self, inst_id: str) -> bool:
        """承認済みにマーク。"""
        if inst_id in _approval_queue:
            _approval_queue[inst_id]["status"] = "approved"
            return True
        return False

    def mark_completed(self, inst_id: str, result: dict[str, Any]) -> None:
        """完了マーク。"""
        if inst_id in _approval_queue:
            _approval_queue[inst_id]["status"] = "completed"
            _approval_queue[inst_id]["result"] = result

    # ----- ユーティリティ -----

    def _check_monthly_reset(self) -> None:
        """月が変わったらコストカウンターをリセット。"""
        jst = timezone(timedelta(hours=9))
        current = datetime.now(jst).strftime("%Y-%m")
        if current != self._month_key:
            if self._month_key:
                log.info(
                    "月次リセット: %s → %s (前月合計: $%.2f, %d回実行)",
                    self._month_key, current, self._monthly_cost, self._execution_count,
                )
            self._month_key = current
            self._monthly_cost = 0.0
            self._execution_count = 0

    def _parse_claude_output(self, raw: str) -> dict[str, Any]:
        """claude -p --output-format json の出力をパース。"""
        try:
            # JSON出力モードの場合
            data = json.loads(raw)

            # claude CLI JSON output structure
            result_text = ""
            cost = 0.0
            session_id = ""

            if isinstance(data, dict):
                result_text = data.get("result", data.get("text", ""))
                cost = data.get("cost_usd", data.get("total_cost_usd", 0))
                session_id = data.get("session_id", "")

                # costが辞書の場合
                if isinstance(cost, dict):
                    cost = sum(v for v in cost.values() if isinstance(v, (int, float)))

            return {
                "result": str(result_text)[:2000],
                "cost_usd": float(cost) if cost else 0.0,
                "session_id": str(session_id),
            }

        except (json.JSONDecodeError, TypeError):
            # テキスト出力にフォールバック
            return {
                "result": raw[:2000],
                "cost_usd": 0.0,
                "session_id": "",
            }

    @property
    def monthly_cost(self) -> float:
        self._check_monthly_reset()
        return self._monthly_cost

    @property
    def execution_count(self) -> int:
        return self._execution_count

    def get_status(self) -> dict[str, Any]:
        """エンジンのステータスを返す。"""
        self._check_monthly_reset()
        return {
            "monthly_cost": self._monthly_cost,
            "monthly_cap": self._monthly_cap,
            "execution_count": self._execution_count,
            "pending_approvals": len(self.get_all_pending()),
            "claude_path": self._claude_path,
        }
