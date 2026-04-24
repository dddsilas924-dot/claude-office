"""
memory_bridge.py - Discord会話→memory自動保存
===============================================
ドラがDiscordで部門AIと交わした重要な会話を、
memory/discord_conversations.md に追記する。
次のClaude Codeセッションで自動参照される。

呼び出し方:
    bridge = MemoryBridge()
    await bridge.save_conversation(dept_id, user_msg, ai_response)

保存先:
    /Users/satts924/Downloads/claude-office-trial/claude-office/memory/discord_conversations.md

フォーマット:
    ## YYYY-MM-DD HH:MM [部門名]
    どら: <user_msg>
    AI: <ai_response>
    ---

サイズ制限:
    100KB 超えたら古いエントリから削除して上位 100 件を保持する。
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Final

log = logging.getLogger("memory_bridge")

# 保存先ファイル
_MEMORY_DIR: Final[Path] = (
    Path(__file__).parent.parent / "memory"
)
_MEMORY_FILE: Final[Path] = _MEMORY_DIR / "discord_conversations.md"

# サイズ上限（バイト）
_MAX_SIZE_BYTES: Final[int] = 100 * 1024  # 100 KB

# 保持する最大エントリ数（サイズ超過時に削除するベースライン）
_MAX_ENTRIES: Final[int] = 100

# エントリ区切り文字列
_SEPARATOR: Final[str] = "---"

# 部門ID → 表示名
_DEPT_DISPLAY: dict[str, str] = {
    "commander": "フィル（司令塔）",
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

# 同時書き込み防止用ロック
_write_lock = asyncio.Lock()


class MemoryBridge:
    """
    Discord の会話を memory/discord_conversations.md に追記するブリッジ。

    discord_bot.py の _handle_dept_ai_response() 完了後に呼ぶ。
    ファイル I/O はブロッキングなので run_in_executor でオフロードする。
    """

    def __init__(self) -> None:
        _MEMORY_DIR.mkdir(parents=True, exist_ok=True)
        if not _MEMORY_FILE.exists():
            _MEMORY_FILE.write_text(
                "# Discord会話ログ\n\n"
                "<!-- このファイルは memory_bridge.py が自動生成します -->\n\n",
                encoding="utf-8",
            )
        log.info("MemoryBridge 初期化完了: %s", _MEMORY_FILE)

    # ------------------------------------------------------------------
    # 公開 API
    # ------------------------------------------------------------------

    async def save_conversation(
        self,
        dept_id: str,
        user_msg: str,
        ai_response: str,
    ) -> None:
        """
        会話を markdown に追記する。

        Parameters
        ----------
        dept_id:
            部門 ID（例: "commander"）
        user_msg:
            ドラの発言
        ai_response:
            部門 AI の返答
        """
        entry = self._build_entry(dept_id, user_msg, ai_response)
        async with _write_lock:
            await asyncio.to_thread(self._append_entry, entry)

    # ------------------------------------------------------------------
    # 内部実装
    # ------------------------------------------------------------------

    def _build_entry(
        self, dept_id: str, user_msg: str, ai_response: str
    ) -> str:
        """1エントリの markdown 文字列を組み立てる。"""
        # JST = UTC+9
        jst = timezone(timedelta(hours=9))
        now_jst = datetime.now(jst).strftime("%Y-%m-%d %H:%M")
        dept_label = _DEPT_DISPLAY.get(dept_id, dept_id)

        # 長すぎるメッセージはトリム（1エントリあたり上限 500 文字）
        user_trimmed = _trim(user_msg, 500)
        ai_trimmed = _trim(ai_response, 500)

        return (
            f"\n## {now_jst} [{dept_label}]\n"
            f"どら: {user_trimmed}\n"
            f"AI: {ai_trimmed}\n"
            f"{_SEPARATOR}\n"
        )

    def _append_entry(self, entry: str) -> None:
        """ファイルにエントリを追記し、必要に応じてサイズ削減する。"""
        try:
            # 既存内容 + 新エントリを連結
            existing = _MEMORY_FILE.read_text(encoding="utf-8")
            updated = existing + entry

            # サイズ超過チェック
            if len(updated.encode("utf-8")) > _MAX_SIZE_BYTES:
                updated = self._trim_old_entries(updated)

            _MEMORY_FILE.write_text(updated, encoding="utf-8")
            log.debug("MemoryBridge: エントリ追記完了 (%d bytes)", len(updated.encode("utf-8")))

        except OSError as exc:
            log.error("MemoryBridge: ファイル書き込みエラー: %s", exc)

    @staticmethod
    def _trim_old_entries(content: str) -> str:
        """
        古いエントリを削除して上位 _MAX_ENTRIES 件のみ保持する。

        ファイル先頭のヘッダー（## で始まらない行）は保持する。
        """
        lines = content.splitlines(keepends=True)

        # ヘッダー部分（最初の "## " が来る前の行）を切り出す
        header_lines: list[str] = []
        body_start = 0
        for i, line in enumerate(lines):
            if line.startswith("## "):
                body_start = i
                break
            header_lines.append(line)
        else:
            # "## " が一つもない場合はヘッダーのみ
            return "".join(header_lines)

        body_lines = lines[body_start:]

        # "## " 行でエントリを分割
        entries: list[list[str]] = []
        current: list[str] = []
        for line in body_lines:
            if line.startswith("## ") and current:
                entries.append(current)
                current = [line]
            else:
                current.append(line)
        if current:
            entries.append(current)

        # 古い順に並んでいるので末尾 _MAX_ENTRIES 件を保持
        kept = entries[-_MAX_ENTRIES:]
        log.info(
            "MemoryBridge: %d エントリ → %d エントリに削減",
            len(entries),
            len(kept),
        )

        return "".join(header_lines) + "".join("".join(e) for e in kept)


# ---------------------------------------------------------------------------
# ユーティリティ
# ---------------------------------------------------------------------------

def _trim(text: str, max_chars: int) -> str:
    """テキストを max_chars 文字に切り詰める。"""
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + "…(省略)"
