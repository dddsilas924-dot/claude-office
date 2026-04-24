"""
dept_request.py - 部門間依頼フロー管理
========================================
部門AI同士が依頼→受領→実行→完了報告する仕組み。
依頼チェーンの深さは最大3段（無限ループ防止）。

使い方:
    manager = DeptRequestManager(instruction_engine, bot)
    req_id = await manager.create_request("research", "design", "調査結果をLP化して")
    # → design部門がclaude -pで実行 → 結果をDiscordに投稿
"""
from __future__ import annotations

import logging
import re
import uuid
from datetime import datetime, timezone, timedelta
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    import discord
    from instruction_engine import InstructionEngine

log = logging.getLogger("dept_request")

JST = timezone(timedelta(hours=9))

# 依頼チェーンの最大深さ（無限ループ防止）
MAX_CHAIN_DEPTH = 3

# AIレスポンス内の依頼パターン: [依頼: 部門名: タスク内容]
_REQUEST_PATTERN = re.compile(
    r"\[依頼:\s*(?P<to_dept>[^:：\]]+?)\s*[:：]\s*(?P<task>[^\]]+?)\]",
)


class DeptRequest:
    """1件の部門間依頼を表すデータクラス。"""

    def __init__(
        self,
        req_id: str,
        from_dept: str,
        to_dept: str,
        task: str,
        chain_depth: int = 0,
        parent_req_id: str | None = None,
    ) -> None:
        self.req_id = req_id
        self.from_dept = from_dept
        self.to_dept = to_dept
        self.task = task
        self.chain_depth = chain_depth
        self.parent_req_id = parent_req_id
        self.status: str = "pending"   # pending | running | completed | failed | rejected
        self.result: str = ""
        self.created_at: str = datetime.now(JST).isoformat()
        self.completed_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "req_id": self.req_id,
            "from_dept": self.from_dept,
            "to_dept": self.to_dept,
            "task": self.task,
            "chain_depth": self.chain_depth,
            "parent_req_id": self.parent_req_id,
            "status": self.status,
            "result": self.result,
            "created_at": self.created_at,
            "completed_at": self.completed_at,
        }


class DeptRequestManager:
    """
    部門間依頼フローを管理するマネージャー。

    - create_request() で依頼を作成しキューに積む
    - process_request() で InstructionEngine 経由で実行
    - get_pending_requests() で未処理依頼一覧を取得
    - scan_and_dispatch() で AIレスポンスから依頼を自動検出・発行
    """

    def __init__(
        self,
        engine: "InstructionEngine",
        bot: Any,  # CommanderBridgeBot (循環インポート回避のため Any)
    ) -> None:
        self._engine = engine
        self._bot = bot
        self._requests: dict[str, DeptRequest] = {}

    # ------------------------------------------------------------------
    # 依頼作成
    # ------------------------------------------------------------------

    async def create_request(
        self,
        from_dept: str,
        to_dept: str,
        task: str,
        chain_depth: int = 0,
        parent_req_id: str | None = None,
    ) -> str | None:
        """
        部門間依頼を作成してキューに追加する。

        Returns:
            req_id (str) — 成功
            None — チェーン深さ超過または禁止タスク
        """
        if chain_depth >= MAX_CHAIN_DEPTH:
            log.warning(
                "依頼チェーン深さ上限(%d)到達。依頼をスキップ: %s → %s '%s'",
                MAX_CHAIN_DEPTH, from_dept, to_dept, task[:40],
            )
            return None

        req_id = f"REQ-{datetime.now(JST).strftime('%m%d%H%M')}-{uuid.uuid4().hex[:4].upper()}"

        req = DeptRequest(
            req_id=req_id,
            from_dept=from_dept,
            to_dept=to_dept,
            task=task,
            chain_depth=chain_depth,
            parent_req_id=parent_req_id,
        )
        self._requests[req_id] = req

        log.info(
            "依頼作成: %s [depth=%d] %s → %s: %s",
            req_id, chain_depth, from_dept, to_dept, task[:60],
        )

        # 依頼先チャンネルに通知
        await self._notify_request_created(req)

        return req_id

    # ------------------------------------------------------------------
    # 依頼実行
    # ------------------------------------------------------------------

    async def process_request(self, req_id: str) -> dict[str, Any]:
        """
        依頼を InstructionEngine 経由で実行し、結果を返す。
        完了後は依頼先チャンネルに結果を投稿する。

        Returns:
            engine.execute() と同じ dict 構造
        """
        req = self._requests.get(req_id)
        if req is None:
            return {
                "status": "error",
                "result": "",
                "cost": 0,
                "duration_sec": 0,
                "session_id": "",
                "error": f"依頼 {req_id} が見つかりません",
            }

        req.status = "running"
        log.info("依頼実行開始: %s (dept=%s, task=%r)", req_id, req.to_dept, req.task[:60])

        result = await self._engine.execute(req.task, req.to_dept)

        if result["status"] == "success":
            req.status = "completed"
            req.result = result.get("result", "")
        else:
            req.status = "failed"
            req.result = result.get("error", "不明なエラー")

        req.completed_at = datetime.now(JST).isoformat()

        log.info(
            "依頼完了: %s status=%s cost=$%.4f",
            req_id, req.status, result.get("cost", 0),
        )

        # 結果をDiscordに投稿
        await self._post_result(req, result)

        # 完了結果のAIテキストに新たな依頼が含まれていれば自動発行
        if req.status == "completed" and req.result:
            await self.scan_and_dispatch(
                text=req.result,
                from_dept=req.to_dept,
                chain_depth=req.chain_depth + 1,
                parent_req_id=req_id,
            )

        return result

    # ------------------------------------------------------------------
    # 依頼一覧
    # ------------------------------------------------------------------

    def get_pending_requests(self, dept_id: str) -> list[DeptRequest]:
        """
        指定部門の未処理依頼一覧を返す。

        Args:
            dept_id: 対象部門ID

        Returns:
            status == "pending" の DeptRequest リスト（古い順）
        """
        return [
            req for req in self._requests.values()
            if req.to_dept == dept_id and req.status == "pending"
        ]

    def get_all_requests(self) -> list[DeptRequest]:
        """全依頼の一覧を返す（デバッグ用）。"""
        return list(self._requests.values())

    # ------------------------------------------------------------------
    # AIレスポンスからの自動依頼検出
    # ------------------------------------------------------------------

    async def scan_and_dispatch(
        self,
        text: str,
        from_dept: str,
        chain_depth: int = 0,
        parent_req_id: str | None = None,
    ) -> list[str]:
        """
        AIのレスポンステキストから [依頼: 部門名: タスク内容] を検出し、
        自動で create_request() → process_request() を呼ぶ。

        Returns:
            作成された req_id のリスト
        """
        matches = _REQUEST_PATTERN.findall(text)
        req_ids: list[str] = []

        for to_dept_raw, task in matches:
            to_dept = to_dept_raw.strip()
            task = task.strip()

            if not to_dept or not task:
                continue

            req_id = await self.create_request(
                from_dept=from_dept,
                to_dept=to_dept,
                task=task,
                chain_depth=chain_depth,
                parent_req_id=parent_req_id,
            )
            if req_id:
                req_ids.append(req_id)
                # バックグラウンドで実行（結果を待たない）
                import asyncio
                asyncio.create_task(
                    self.process_request(req_id),
                    name=f"dept_req_{req_id}",
                )

        return req_ids

    # ------------------------------------------------------------------
    # Discord 通知ヘルパー
    # ------------------------------------------------------------------

    async def _notify_request_created(self, req: DeptRequest) -> None:
        """依頼作成をDiscordの依頼先チャンネルに通知する。"""
        if not hasattr(self._bot, "_guild") or not self._bot._guild:
            return

        ch = self._find_dept_channel(req.to_dept)
        if ch is None:
            log.debug("依頼先チャンネルが見つかりません: dept=%s", req.to_dept)
            return

        try:
            import discord as _discord
            embed = _discord.Embed(
                title=f"部門間依頼受信: {req.req_id}",
                description=req.task[:1800],
                color=0x3498DB,
            )
            embed.add_field(name="依頼元", value=req.from_dept, inline=True)
            embed.add_field(name="依頼先", value=req.to_dept, inline=True)
            embed.add_field(name="チェーン深さ", value=str(req.chain_depth), inline=True)
            if req.parent_req_id:
                embed.add_field(name="親依頼", value=req.parent_req_id, inline=False)
            embed.set_footer(text="P-08 部門間依頼フロー | 処理中...")
            await ch.send(embed=embed)
        except Exception as exc:
            log.error("依頼通知送信エラー: %s", exc)

    async def _post_result(self, req: DeptRequest, result: dict[str, Any]) -> None:
        """実行結果をDiscordの依頼先チャンネルに投稿する。"""
        if not hasattr(self._bot, "_guild") or not self._bot._guild:
            return

        ch = self._find_dept_channel(req.to_dept)
        if ch is None:
            return

        try:
            import discord as _discord
            color = 0x2ECC71 if req.status == "completed" else 0xE74C3C
            status_label = "完了" if req.status == "completed" else "失敗"

            embed = _discord.Embed(
                title=f"部門間依頼{status_label}: {req.req_id}",
                description=(req.result or "(結果なし)")[:1800],
                color=color,
            )
            embed.add_field(name="依頼元", value=req.from_dept, inline=True)
            embed.add_field(name="コスト", value=f"${result.get('cost', 0):.4f}", inline=True)
            embed.add_field(
                name="所要時間", value=f"{result.get('duration_sec', 0):.1f}秒", inline=True,
            )
            embed.set_footer(text="P-08 部門間依頼フロー")
            await ch.send(embed=embed)

            # 成果物チャンネルにも投稿
            deliverable_ch = self._find_deliverable_channel(req.to_dept)
            if deliverable_ch and req.status == "completed":
                await deliverable_ch.send(
                    f"`{req.req_id}` [{req.from_dept}→{req.to_dept}] {req.task[:60]}\n"
                    f"**結果**: {req.result[:300] if req.result else '(なし)'}",
                )

        except Exception as exc:
            log.error("依頼結果投稿エラー: %s", exc)

    def _find_dept_channel(self, dept_id: str) -> Any | None:
        """部門チャンネルを検索する（完全一致 → 部分一致）。"""
        if not hasattr(self._bot, "_guild") or not self._bot._guild:
            return None

        import discord as _discord
        guild = self._bot._guild

        # 完全一致
        ch = _discord.utils.get(guild.text_channels, name=dept_id)
        if ch:
            return ch

        # 部分一致（dept_id が含まれているチャンネル名）
        for ch in guild.text_channels:
            if dept_id in ch.name:
                return ch

        return None

    def _find_deliverable_channel(self, dept_id: str) -> Any | None:
        """📦部門の成果物チャンネルを検索する。"""
        if not hasattr(self._bot, "_guild") or not self._bot._guild:
            return None

        import discord as _discord
        guild = self._bot._guild

        # "📦部門の成果物" または "dept_id-deliverables" 形式
        for ch in guild.text_channels:
            if "成果物" in ch.name and dept_id in ch.name:
                return ch
            if f"{dept_id}-deliverables" in ch.name:
                return ch

        return None
