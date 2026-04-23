"""
dept_brain.py - 部門AI頭脳エンジン
===================================
Anthropic API を使って部門AIの思考・返答を生成する。
discord_bot.py から呼ばれる独立モジュール。

機能:
1. respond()          - メッセージへの返答生成
2. generate_autonomous() - 自発活動の内容生成
3. meeting_turn()     - 会議の1ターン発言生成
4. is_deliverable()   - 成果物判定
5. generate_deliverable_file() - 成果物をMDファイルとして保存
6. generate_report_html()      - MDテキストをHTMLに変換・保存
7. generate_summary_image()    - テキスト要約を画像化・保存

安全設計:
- レートリミット（10回/分）
- 月間コスト上限（$30/月）
- APIタイムアウト（15秒）
- エラー時リトライ1回
"""
from __future__ import annotations

import asyncio
import fcntl
import html as html_module
import io
import json
import logging
import os
import re
import textwrap
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

try:
    from PIL import Image, ImageDraw, ImageFont
    _PIL_AVAILABLE = True
except ImportError:
    _PIL_AVAILABLE = False

import anthropic

from dept_prompts import (
    build_system_prompt,
    build_autonomous_prompt,
    build_meeting_prompt,
    build_meeting_summary_prompt,
    select_meeting_participants,
    DEPT_PROMPTS,
)

log = logging.getLogger("dept_brain")

# ---------------------------------------------------------------------------
# 設定読み込み
# ---------------------------------------------------------------------------
_CONFIG_PATH = Path(__file__).parent.parent / "config" / "autonomous_office.json"


def _load_config() -> dict[str, Any]:
    """設定ファイルを読み込む。なければデフォルト値。"""
    defaults = {
        "api_model": "claude-sonnet-4-20250514",
        "max_response_tokens": 500,
        "monthly_cost_cap_usd": 30.0,
        "rate_limit_per_minute": 10,
        "autonomous_interval_hours": 4,
        "quiet_hours": {"start": 0, "end": 7},
        "meeting_schedule_hour": 14,
        "context_history_limit": 5,
        "deliverable_min_lines": 10,
        "meeting_rounds": 3,
        "meeting_turn_delay_sec": 3,
        "api_timeout_sec": 15,
        "cost_per_1m_input": 3.0,
        "cost_per_1m_output": 15.0,
    }
    if _CONFIG_PATH.is_file():
        try:
            with _CONFIG_PATH.open(encoding="utf-8") as f:
                loaded = json.load(f)
            defaults.update(loaded)
        except (json.JSONDecodeError, OSError) as exc:
            log.warning("設定ファイル読み込みエラー: %s (デフォルト値を使用)", exc)
    return defaults


# ---------------------------------------------------------------------------
# レートリミッター
# ---------------------------------------------------------------------------
class RateLimiter:
    """スライディングウィンドウ方式のレートリミッター。"""

    def __init__(self, max_per_minute: int = 10):
        self._max = max_per_minute
        self._timestamps: list[float] = []
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        """レートリミット内なら即座にreturn、超えていたらwait。"""
        wait_time = 0.0
        async with self._lock:
            now = time.monotonic()
            # 1分以上前のタイムスタンプを除去
            self._timestamps = [t for t in self._timestamps if now - t < 60.0]

            if len(self._timestamps) >= self._max:
                # 最も古いリクエストから60秒後まで待つ
                wait_time = 60.0 - (now - self._timestamps[0])

        # ロック外でsleep（ロック保持中にsleepすると他タスクをブロックする）
        if wait_time > 0:
            log.info("レートリミット到達。%.1f秒待機...", wait_time)
            await asyncio.sleep(wait_time)

        async with self._lock:
            now = time.monotonic()
            self._timestamps = [t for t in self._timestamps if now - t < 60.0]
            self._timestamps.append(now)


# ---------------------------------------------------------------------------
# コストトラッカー
# ---------------------------------------------------------------------------
class CostTracker:
    """月間APIコストを追跡する。永続化ファイルで再起動時もコストを引き継ぐ。"""

    _PERSIST_FILE = Path("/tmp/claude_office_cost.json")

    def __init__(self, monthly_cap: float = 30.0):
        self._monthly_cap = monthly_cap
        self._monthly_cost = 0.0
        self._current_month: str = ""
        self._lock = asyncio.Lock()
        self._load_persisted()

    def _load_persisted(self) -> None:
        """永続化ファイルからコストを復元する。"""
        if not self._PERSIST_FILE.is_file():
            return
        try:
            data = json.loads(self._PERSIST_FILE.read_text(encoding="utf-8"))
            month_key = datetime.now(timezone.utc).strftime("%Y-%m")
            if data.get("month") == month_key:
                self._monthly_cost = data.get("cost", 0.0)
                self._current_month = month_key
                log.info("コスト復元: %s = $%.4f", month_key, self._monthly_cost)
        except (json.JSONDecodeError, OSError):
            pass

    def _persist(self) -> None:
        """コストを永続化ファイルに保存する。"""
        try:
            self._PERSIST_FILE.write_text(
                json.dumps({"month": self._current_month, "cost": self._monthly_cost}),
                encoding="utf-8",
            )
        except OSError:
            pass

    async def add_cost(self, input_tokens: int, output_tokens: int, config: dict) -> None:
        """APIコールのコストを加算する。"""
        async with self._lock:
            month_key = datetime.now(timezone.utc).strftime("%Y-%m")
            if month_key != self._current_month:
                self._monthly_cost = 0.0
                self._current_month = month_key
                log.info("新しい月 (%s): コストカウンターリセット", month_key)

            cost_input = (input_tokens / 1_000_000) * config.get("cost_per_1m_input", 3.0)
            cost_output = (output_tokens / 1_000_000) * config.get("cost_per_1m_output", 15.0)
            self._monthly_cost += cost_input + cost_output
            self._persist()

    async def can_proceed(self) -> bool:
        """コスト上限に達していないか確認する。"""
        async with self._lock:
            month_key = datetime.now(timezone.utc).strftime("%Y-%m")
            if month_key != self._current_month:
                return True
            return self._monthly_cost < self._monthly_cap

    @property
    def monthly_cost(self) -> float:
        return self._monthly_cost


# ---------------------------------------------------------------------------
# DeptBrain 本体
# ---------------------------------------------------------------------------
class DeptBrain:
    """
    部門AIの頭脳。Anthropic APIで思考し返答を生成する。

    使い方:
        brain = DeptBrain(api_key="sk-ant-...")
        response = await brain.respond("research", "市場調査して", [...], "ドラ")
    """

    def __init__(self, api_key: str | None = None):
        key = api_key or os.environ.get("ANTHROPIC_API_KEY", "")
        if not key:
            raise ValueError("ANTHROPIC_API_KEY が設定されていません")

        self._client = anthropic.Anthropic(api_key=key)
        self._config = _load_config()
        self._rate_limiter = RateLimiter(
            max_per_minute=self._config["rate_limit_per_minute"]
        )
        self._cost_tracker = CostTracker(
            monthly_cap=self._config["monthly_cost_cap_usd"]
        )
        self._silence_mode = False  # /silence で一時停止
        self._meeting_active = False  # 会議中フラグ
        self._state_write_lock = asyncio.Lock()  # shared_state書き込み排他制御
        self._memory_cache: dict[str, tuple[float, str]] = {}  # dept_id → (timestamp, content)
        self._MEMORY_CACHE_TTL = 300  # 5分キャッシュ（ファイルI/O削減）

        log.info(
            "DeptBrain 初期化完了: model=%s, cap=$%.0f/月, rate=%d/分",
            self._config["api_model"],
            self._config["monthly_cost_cap_usd"],
            self._config["rate_limit_per_minute"],
        )

    # ------------------------------------------------------------------
    # サニタイズ（Prompt Injection対策）
    # ------------------------------------------------------------------
    _INJECTION_PATTERNS = re.compile(
        r"(?:system\s*:|<\s*system|ignore\s+(?:previous|above)|"
        r"you\s+are\s+now|pretend\s+(?:you|to)|override\s+instructions|"
        r"new\s+instructions|admin\s+mode|developer\s+mode)",
        re.IGNORECASE,
    )

    @classmethod
    def _sanitize_context(cls, context: list[dict[str, str]]) -> list[dict[str, str]]:
        """コンテキスト履歴からインジェクション試行を無害化する。"""
        clean = []
        for msg in context:
            content = msg.get("content", "")
            if cls._INJECTION_PATTERNS.search(content):
                log.warning("Prompt injection候補を検出・除去: %s", content[:80])
                content = "[ユーザーメッセージ（安全上省略）]"
            clean.append({"role": msg.get("role", "user"), "content": content})
        return clean

    # ------------------------------------------------------------------
    # 公開API
    # ------------------------------------------------------------------

    async def respond(
        self,
        dept_id: str,
        message: str,
        context: list[dict[str, str]],
        sender: str,
    ) -> str | None:
        """
        部門AIとしてメッセージに返答する。

        Args:
            dept_id: 部門ID (例: "research")
            message: 受信メッセージ
            context: 直近の会話履歴 [{"role": "user"/"assistant", "content": "..."}]
            sender: 送信者名

        Returns:
            AI返答テキスト。エラー/上限到達時はNone。
        """
        if dept_id not in DEPT_PROMPTS:
            log.warning("未知の部門ID: %s", dept_id)
            return None

        # shared_state + memory から実データ・ドラの判断を取得して返答に反映
        dept_state = self._load_dept_state(dept_id)
        org_summary = self.load_all_depts_summary()
        memory_context = self.load_dept_memory(dept_id)
        system_prompt = build_system_prompt(dept_id, dept_state, org_summary, memory_context)

        # コンテキスト+新メッセージを組み立て（サニタイズ済み）
        sanitized = self._sanitize_context(context[-self._config["context_history_limit"]:])
        messages = list(sanitized)
        messages.append({
            "role": "user",
            "content": f"[{sender}]: {message}",
        })

        result = await self._call_api(system_prompt, messages, dept_id, "respond")
        if result:
            await self._save_dept_activity(dept_id, "respond", result)
        return result

    async def generate_autonomous(
        self,
        dept_id: str,
        activity_type: str,
    ) -> str | None:
        """
        自発活動の内容を生成する。

        Args:
            dept_id: 部門ID
            activity_type: "morning" | "idea" | "status" | "question" | "insight"

        Returns:
            生成テキスト。静音/上限時はNone。
        """
        if self._silence_mode:
            log.debug("静音モード中: %s の自発活動をスキップ", dept_id)
            return None

        if self._meeting_active:
            log.debug("会議中: %s の自発活動をスキップ", dept_id)
            return None

        # 深夜チェック（日またぎ対応: start=23, end=7 なら 23:00-07:00 が静音）
        jst = timezone(timedelta(hours=9))
        now_jst = datetime.now(jst)
        quiet = self._config["quiet_hours"]
        q_start, q_end = quiet["start"], quiet["end"]
        if q_start <= q_end:
            is_quiet = q_start <= now_jst.hour < q_end
        else:
            # 日またぎ: start > end → start..24 OR 0..end
            is_quiet = now_jst.hour >= q_start or now_jst.hour < q_end
        if is_quiet:
            log.debug("静音時間帯 (%02d:00 JST): %s をスキップ", now_jst.hour, dept_id)
            return None

        # shared_state + memory からドラの最新判断を取得
        dept_state = self._load_dept_state(dept_id)
        org_summary = self.load_all_depts_summary()
        memory_context = self.load_dept_memory(dept_id)

        system_prompt = build_autonomous_prompt(dept_id, activity_type, dept_state, org_summary, memory_context)
        messages = [{"role": "user", "content": "自発的に発言してください。"}]

        result = await self._call_api(system_prompt, messages, dept_id, "autonomous")
        if result:
            await self._save_dept_activity(dept_id, "autonomous", result)
        return result

    _DEPT_MAP = {
        "commander": "commander",
        "research": "research",
        "sales": "sales",
        "design": "design",
        "content": "content",
        "writing": "writing",
        "advertising": "advertising",
        "ai_investment": "ai_investment",
        "new_biz": "new_biz",
        "phil_consulting": "phil_consulting",
        "security": "security",
        "takumi_x": "takumi_x",
        "real_estate": "real_estate",
        "doradora_sns": "doradora_sns",
        "origin_story": "origin_story",
    }

    def _get_state_path(self) -> Path | None:
        """shared_state.json のパスを返す。"""
        env_path = os.environ.get("SHARED_STATE_PATH", "")
        if env_path:
            p = Path(env_path)
        else:
            p = Path(__file__).parent.parent.parent / "empire_monitor_full_20260321" / ".claude" / "shared_state.json"
        return p if p.is_file() else None

    def _load_full_state(self) -> dict[str, Any] | None:
        """shared_state.json 全体を読み込む。"""
        state_path = self._get_state_path()
        if not state_path:
            return None
        try:
            with state_path.open(encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError) as exc:
            log.debug("shared_state.json読み込みエラー: %s", exc)
            return None

    def _load_dept_state(self, dept_id: str) -> dict[str, Any] | None:
        """shared_state.json から該当部門の実データを読み込む。"""
        data = self._load_full_state()
        if not data:
            return None
        key = self._DEPT_MAP.get(dept_id, dept_id)
        return data.get("departments", {}).get(key)

    # ------------------------------------------------------------------
    # 部門ごとの関連memoryファイル（Claude Code側のドラの判断・フィードバック）
    # ------------------------------------------------------------------
    _DEPT_MEMORY_MAP: dict[str, list[str]] = {
        "commander": [
            "user_vision_core.md",
            "project_org_structure.md",
            "project_sales_map.md",
            "feedback_dev_decisions_delegate.md",
        ],
        "research": [
            "feedback_genius_research_output.md",
            "feedback_no_inflation_to_em.md",
            "project_research_engine_design.md",
            "feedback_em_research_method.md",
        ],
        "sales": [
            "project_sales_map.md",
            "project_offline_sales_insight.md",
            "feedback_responsibility_escape.md",
        ],
        "design": [
            "project_design_system.md",
            "feedback_html_design_template.md",
        ],
        "content": [
            "project_content_v2.md",
            "project_three_channels.md",
            "feedback_script_design_rules.md",
            "feedback_grade3_literacy.md",
        ],
        "writing": [
            "project_writing_reception_framework.md",
            "feedback_grade3_literacy.md",
            "feedback_writing_skill_selection.md",
        ],
        "advertising": [
            "project_sales_map.md",
        ],
        "ai_investment": [
            "project_ai_em_investment_agent.md",
            "project_ai_investment_telegram_bot.md",
            "feedback_ai_news_stock_pattern.md",
        ],
        "new_biz": [
            "user_vision_core.md",
            "project_em_product_vision_20260417.md",
            "feedback_niche_is_ai_template.md",
        ],
        "phil_consulting": [
            "project_phil_consulting.md",
            "project_real_estate_school.md",
            "feedback_phil_learning_method.md",
        ],
        "security": [],
        "takumi_x": [
            "feedback_ai_bot_frozen.md",
        ],
        "real_estate": [
            "project_real_estate_ai.md",
            "project_hermes_fudosan.md",
            "project_mariko_dora_strategy.md",
        ],
        "doradora_sns": [
            "feedback_persona_split.md",
        ],
        "origin_story": [
            "user_identity_story.md",
        ],
    }

    def _get_memory_dir(self) -> Path | None:
        """Claude Codeのプロジェクトmemoryディレクトリを返す。"""
        mem_dir = Path.home() / ".claude" / "projects" / "-Users-satts924-Downloads-empire-monitor-full-20260321" / "memory"
        return mem_dir if mem_dir.is_dir() else None

    def load_dept_memory(self, dept_id: str) -> str:
        """
        部門に関連するmemoryファイルを読み込んで文字列として返す。

        Claude Code側でドラが記録した判断・フィードバック・方針を
        Discord側のAIに注入するための仕組み。

        各ファイルは先頭500文字に制限する（トークン爆発防止）。
        5分間のキャッシュで不要なファイルI/Oを防止。
        """
        # キャッシュチェック（5分以内なら再読み込みしない）
        cached = self._memory_cache.get(dept_id)
        if cached:
            cache_time, cache_content = cached
            if time.monotonic() - cache_time < self._MEMORY_CACHE_TTL:
                return cache_content

        mem_dir = self._get_memory_dir()
        if not mem_dir:
            return ""

        refs = self._DEPT_MEMORY_MAP.get(dept_id, [])
        if not refs:
            return ""

        parts: list[str] = []
        for fname in refs:
            fpath = mem_dir / fname
            if not fpath.is_file():
                continue
            try:
                text = fpath.read_text(encoding="utf-8")[:500].strip()
                if text:
                    label = fname.replace(".md", "").replace("_", " ")
                    parts.append(f"[{label}]\n{text}")
            except OSError:
                continue

        if not parts:
            self._memory_cache[dept_id] = (time.monotonic(), "")
            return ""

        result = "\n\n".join(parts)
        self._memory_cache[dept_id] = (time.monotonic(), result)
        return result

    def load_all_depts_summary(self) -> str:
        """全部門の状況サマリーを1つの文字列にまとめて返す。"""
        data = self._load_full_state()
        if not data:
            return ""
        depts = data.get("departments", {})
        lines = []
        for dept_id, dept_data in sorted(depts.items()):
            if not isinstance(dept_data, dict):
                continue
            name = dept_data.get("name", dept_id)
            status = dept_data.get("status", "不明")
            updated = dept_data.get("updated_at", "不明")
            lines.append(f"- {name}: {status} (更新: {updated})")
        return "\n".join(lines)

    async def _save_dept_activity(
        self,
        dept_id: str,
        call_type: str,
        response_summary: str | None = None,
    ) -> None:
        """
        Discord活動の結果を shared_state.json に書き戻す（自動更新）。

        - respond: ユーザーとの会話があったことを記録
        - autonomous: 自発活動の内容を記録
        - meeting/meeting_summary: 会議参加を記録

        書き込み失敗しても例外は飛ばさない（Botの動作に影響させない）。
        """
        env_path = os.environ.get("SHARED_STATE_PATH", "")
        if not env_path:
            return
        state_path = Path(env_path)
        if not state_path.is_file():
            return

        # Discord部門ID → shared_stateキー（クラス定数 _DEPT_MAP を再利用）
        key = self._DEPT_MAP.get(dept_id, dept_id)

        jst = timezone(timedelta(hours=9))
        now_jst = datetime.now(jst).strftime("%Y-%m-%d %H:%M")

        # 活動種別ラベル
        activity_labels = {
            "respond": "Discord会話応答",
            "autonomous": "Discord自発活動",
            "meeting": "Discord定例会議発言",
            "meeting_summary": "Discord会議まとめ",
        }
        label = activity_labels.get(call_type, call_type)

        async with self._state_write_lock:
            try:
                # fcntl.flock でプロセス間排他制御（他プロセスの上書きを防止）
                lock_path = state_path.with_suffix(".lock")
                with open(lock_path, "w") as lock_file:
                    fcntl.flock(lock_file, fcntl.LOCK_EX)
                    try:
                        with state_path.open(encoding="utf-8") as f:
                            data = json.load(f)

                        dept_data = data.get("departments", {}).get(key)
                        if dept_data is None:
                            log.debug("shared_state に部門 %s が存在しないため書き戻しスキップ", key)
                            return

                        # updated_at を更新
                        dept_data["updated_at"] = now_jst

                        # Discord活動ログを追記
                        activity_entry = f"{now_jst} [{label}]"
                        if response_summary:
                            # 最初の50文字だけ記録（長すぎるとJSONが肥大化する）
                            short = response_summary[:50].replace("\n", " ")
                            activity_entry += f" {short}"

                        # discord_activity_log に最新5件を保持（FIFO）
                        activity_log = dept_data.get("discord_activity_log", [])
                        activity_log.append(activity_entry)
                        dept_data["discord_activity_log"] = activity_log[-5:]

                        # JSONを書き戻し（一時ファイル→リネームで原子的書き込み）
                        tmp_path = state_path.with_suffix(".tmp")
                        with tmp_path.open("w", encoding="utf-8") as f:
                            json.dump(data, f, ensure_ascii=False, indent=2)
                        tmp_path.replace(state_path)
                    finally:
                        fcntl.flock(lock_file, fcntl.LOCK_UN)

                log.info(
                    "shared_state 書き戻し完了: %s/%s → %s (log=%d件)",
                    dept_id, call_type, now_jst, len(dept_data.get("discord_activity_log", [])),
                )
            except (json.JSONDecodeError, OSError, KeyError) as exc:
                log.error(
                    "shared_state 書き戻し失敗: %s - %s",
                    dept_id, exc,
                )
            except Exception as exc:
                log.error(
                    "shared_state 書き戻し予期しないエラー: %s - %s",
                    dept_id, exc,
                )

    async def meeting_turn(
        self,
        topic: str,
        participants: list[str],
        history: list[dict[str, str]],
        current_dept: str,
        round_num: int,
    ) -> str | None:
        """
        会議の1ターンの発言を生成する。

        Args:
            topic: 会議の議題
            participants: 参加部門IDリスト
            history: これまでの発言 [{"dept_id": ..., "name": ..., "message": ...}]
            current_dept: 現在発言する部門ID
            round_num: ラウンド番号 (1-3)

        Returns:
            発言テキスト。
        """
        # 会議発言にも実データ + memory + 全部門状況を注入
        dept_state = self._load_dept_state(current_dept)
        org_summary = self.load_all_depts_summary()
        memory_context = self.load_dept_memory(current_dept)
        system_prompt = build_meeting_prompt(current_dept, topic, round_num, history, dept_state, org_summary, memory_context)
        messages = [{"role": "user", "content": f"会議議題「{topic}」について発言してください。"}]

        result = await self._call_api(system_prompt, messages, current_dept, "meeting")
        if result:
            await self._save_dept_activity(current_dept, "meeting", result)
        return result

    async def meeting_summary(
        self,
        topic: str,
        history: list[dict[str, str]],
    ) -> str | None:
        """会議のまとめをフィルとして生成する。全部門状況 + ドラのmemoryも参照する。"""
        org_summary = self.load_all_depts_summary()
        memory_context = self.load_dept_memory("commander")
        system_prompt = build_meeting_summary_prompt(topic, history, org_summary, memory_context)
        messages = [{"role": "user", "content": "会議をまとめてください。"}]

        result = await self._call_api(
            system_prompt, messages, "commander", "meeting_summary",
            max_tokens=800,  # まとめは長め
        )
        if result:
            await self._save_dept_activity("commander", "meeting_summary", result)
        return result

    def select_participants(self, topic: str) -> list[str]:
        """議題から会議参加部門を選択する。"""
        return select_meeting_participants(topic)

    # ------------------------------------------------------------------
    # 成果物判定
    # ------------------------------------------------------------------

    @staticmethod
    def is_deliverable(text: str) -> bool:
        """テキストが成果物かどうかを判定する。"""
        if not text:
            return False
        # 明示的タグ
        if "【成果物】" in text:
            return True
        # 10行以上のコードブロック
        code_blocks = re.findall(r"```[\s\S]*?```", text)
        for block in code_blocks:
            if block.count("\n") >= 10:
                return True
        # 長いレポート形式 (改行数で判定)
        if text.count("\n") >= 10 and any(
            marker in text for marker in ["■", "###", "---", "レポート", "提案", "計画"]
        ):
            return True
        return False

    # ------------------------------------------------------------------
    # 成果物ファイル生成
    # ------------------------------------------------------------------

    @staticmethod
    def _deliverable_dir() -> Path:
        """成果物保存ディレクトリを取得し、なければ作成する。"""
        d = Path("/tmp/claude_office_deliverables")
        d.mkdir(parents=True, exist_ok=True)
        return d

    @staticmethod
    def _jst_stamp() -> tuple[str, str]:
        """JST現在時刻の (YYYYMMDD, HHMM) を返す。"""
        jst = timezone(timedelta(hours=9))
        now = datetime.now(jst)
        return now.strftime("%Y%m%d"), now.strftime("%H%M")

    def generate_deliverable_file(self, text: str, dept_id: str) -> Path | None:
        """
        テキストからMDファイルを生成して /tmp/claude_office_deliverables/ に保存する。

        Args:
            text: 保存するテキスト（Markdown形式）
            dept_id: 部門ID（ファイル名に使用）

        Returns:
            保存したファイルのパス。失敗時は None。
        """
        if not text:
            return None
        try:
            date_str, time_str = self._jst_stamp()
            filename = f"成果物_{dept_id}_{date_str}_{time_str}.md"
            out_path = self._deliverable_dir() / filename
            out_path.write_text(text, encoding="utf-8")
            log.info("成果物MDファイル生成: %s (%d bytes)", out_path, len(text.encode("utf-8")))
            return out_path
        except OSError as exc:
            log.error("generate_deliverable_file 失敗: %s", exc)
            return None

    def generate_report_html(self, text: str, dept_id: str) -> Path | None:
        """
        MDテキストをシンプルなHTMLに変換して保存する。
        BIZUDPGothicフォント、ダークテーマ付き。

        Args:
            text: 変換するMarkdownテキスト
            dept_id: 部門ID（ファイル名・タイトルに使用）

        Returns:
            保存したHTMLファイルのパス。失敗時は None。
        """
        if not text:
            return None
        try:
            date_str, time_str = self._jst_stamp()

            # Markdownをシンプルなhtmlへ変換（markdownライブラリがあれば使用）
            try:
                import markdown as md_lib
                body_html = md_lib.markdown(
                    text,
                    extensions=["tables", "fenced_code"],
                )
            except ImportError:
                # フォールバック: 最低限の変換のみ
                escaped = html_module.escape(text)
                lines = escaped.split("\n")
                converted = []
                for line in lines:
                    if line.startswith("### "):
                        converted.append(f"<h3>{line[4:]}</h3>")
                    elif line.startswith("## "):
                        converted.append(f"<h2>{line[3:]}</h2>")
                    elif line.startswith("# "):
                        converted.append(f"<h1>{line[2:]}</h1>")
                    elif line.startswith("- ") or line.startswith("* "):
                        converted.append(f"<li>{line[2:]}</li>")
                    elif line.strip() == "":
                        converted.append("<br>")
                    else:
                        converted.append(f"<p>{line}</p>")
                body_html = "\n".join(converted)

            title = f"{dept_id} レポート {date_str}_{time_str}"
            html_content = f"""<!DOCTYPE html>
<html lang="ja">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{html_module.escape(title)}</title>
  <style>
    @font-face {{
      font-family: 'BIZUDPGothic';
      src: url('/Users/satts924/Downloads/empire_monitor_full_20260321/reports/assets/fonts/japanese/BIZUDPGothic-Regular.ttf');
      font-weight: normal;
    }}
    @font-face {{
      font-family: 'BIZUDPGothic';
      src: url('/Users/satts924/Downloads/empire_monitor_full_20260321/reports/assets/fonts/japanese/BIZUDPGothic-Bold.ttf');
      font-weight: bold;
    }}
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{
      font-family: 'BIZUDPGothic', 'Hiragino Sans', 'Yu Gothic', sans-serif;
      font-size: 16px;
      line-height: 1.8;
      background: #1a1a2e;
      color: #e0e0e0;
      padding: 2rem;
      max-width: 900px;
      margin: 0 auto;
    }}
    h1 {{ font-size: 2rem; color: #7ec8e3; border-bottom: 2px solid #7ec8e3; padding-bottom: 0.5rem; margin: 1.5rem 0 1rem; }}
    h2 {{ font-size: 1.5rem; color: #a8d8ea; margin: 1.5rem 0 0.8rem; }}
    h3 {{ font-size: 1.2rem; color: #c8e6f5; margin: 1.2rem 0 0.6rem; }}
    p {{ margin: 0.8rem 0; }}
    li {{ margin: 0.3rem 0 0.3rem 1.5rem; }}
    ul, ol {{ margin: 0.5rem 0; }}
    code {{
      font-family: 'Courier New', monospace;
      background: #16213e;
      border: 1px solid #0f3460;
      padding: 0.1rem 0.4rem;
      border-radius: 3px;
      font-size: 0.9rem;
      color: #e94560;
    }}
    pre {{
      background: #16213e;
      border: 1px solid #0f3460;
      padding: 1rem;
      border-radius: 6px;
      overflow-x: auto;
      margin: 1rem 0;
    }}
    pre code {{ background: none; border: none; color: #e0e0e0; padding: 0; }}
    table {{ border-collapse: collapse; width: 100%; margin: 1rem 0; }}
    th {{ background: #0f3460; color: #7ec8e3; padding: 0.6rem 1rem; text-align: left; }}
    td {{ border: 1px solid #0f3460; padding: 0.5rem 1rem; }}
    tr:nth-child(even) td {{ background: #16213e; }}
    blockquote {{
      border-left: 4px solid #7ec8e3;
      padding: 0.5rem 1rem;
      margin: 1rem 0;
      color: #b0b0b0;
      font-style: italic;
    }}
    .meta {{
      font-size: 0.85rem;
      color: #666;
      margin-bottom: 2rem;
      padding-bottom: 1rem;
      border-bottom: 1px solid #0f3460;
    }}
    hr {{ border: none; border-top: 1px solid #0f3460; margin: 1.5rem 0; }}
  </style>
</head>
<body>
  <div class="meta">部門: {html_module.escape(dept_id)} | 生成日時: {date_str[:4]}-{date_str[4:6]}-{date_str[6:]} {time_str[:2]}:{time_str[2:]}</div>
  {body_html}
</body>
</html>"""

            filename = f"レポート_{dept_id}_{date_str}_{time_str}.html"
            out_path = self._deliverable_dir() / filename
            out_path.write_text(html_content, encoding="utf-8")
            log.info("レポートHTMLファイル生成: %s (%d bytes)", out_path, len(html_content.encode("utf-8")))
            return out_path
        except OSError as exc:
            log.error("generate_report_html 失敗: %s", exc)
            return None
        except Exception as exc:
            log.error("generate_report_html 予期しないエラー: %s", exc)
            return None

    def generate_summary_image(self, text: str, dept_id: str) -> Path | None:
        """
        テキストの要約を画像化（PIL/Pillow使用）。
        800x400px、ダーク背景、白文字。

        Args:
            text: 画像化するテキスト（先頭部分を使用）
            dept_id: 部門ID（ファイル名・ヘッダーに使用）

        Returns:
            保存したPNGファイルのパス。PILがない/失敗時は None。
        """
        if not _PIL_AVAILABLE:
            log.warning("generate_summary_image: PIL/Pillowが未インストールのためスキップ")
            return None
        if not text:
            return None

        try:
            date_str, time_str = self._jst_stamp()
            W, H = 800, 400

            # キャンバス生成
            img = Image.new("RGB", (W, H), color=(26, 26, 46))  # #1a1a2e
            draw = ImageDraw.Draw(img)

            # フォント読み込み（BIZUDPGothic優先 → デフォルトフォールバック）
            font_path = Path(
                "/Users/satts924/Downloads/empire_monitor_full_20260321"
                "/reports/assets/fonts/japanese/BIZUDPGothic-Regular.ttf"
            )
            try:
                font_title = ImageFont.truetype(str(font_path), 22) if font_path.is_file() else ImageFont.load_default()
                font_body  = ImageFont.truetype(str(font_path), 16) if font_path.is_file() else ImageFont.load_default()
                font_meta  = ImageFont.truetype(str(font_path), 13) if font_path.is_file() else ImageFont.load_default()
            except (OSError, IOError):
                font_title = ImageFont.load_default()
                font_body  = ImageFont.load_default()
                font_meta  = ImageFont.load_default()

            # 上部アクセントライン
            draw.rectangle([0, 0, W, 4], fill=(126, 200, 227))  # #7ec8e3

            # 部門ヘッダー
            header = f"{dept_id}  |  {date_str[:4]}-{date_str[4:6]}-{date_str[6:]} {time_str[:2]}:{time_str[2:]}"
            draw.text((24, 18), header, font=font_meta, fill=(126, 200, 227))

            # セパレータ
            draw.line([24, 46, W - 24, 46], fill=(15, 52, 96), width=1)

            # 本文（先頭380文字を折り返して表示）
            # Markdownマーカー（#, *, -, 【 等）は簡易除去
            clean = re.sub(r"[#*`>~\[\]]+", "", text).strip()
            # 複数空行を1行に
            clean = re.sub(r"\n{2,}", "\n", clean)
            # 先頭380文字
            snippet = clean[:380]

            # textwrapで折り返し（全角28文字/行が800px時の目安）
            wrapped_lines: list[str] = []
            for raw_line in snippet.splitlines():
                if raw_line.strip():
                    wrapped_lines.extend(textwrap.wrap(raw_line, width=38) or [raw_line])
                else:
                    wrapped_lines.append("")

            y = 58
            line_h = 26
            max_lines = (H - 80) // line_h  # 最大表示行数
            for i, line in enumerate(wrapped_lines[:max_lines]):
                draw.text((24, y + i * line_h), line, font=font_body, fill=(224, 224, 224))

            # 末尾ラベル
            label = "Claude Office 成果物サマリー"
            draw.text((24, H - 24), label, font=font_meta, fill=(102, 102, 102))

            # 下部アクセントライン
            draw.rectangle([0, H - 4, W, H], fill=(126, 200, 227))

            filename = f"サマリー_{dept_id}_{date_str}_{time_str}.png"
            out_path = self._deliverable_dir() / filename
            img.save(str(out_path), format="PNG")
            log.info("サマリー画像ファイル生成: %s (%dx%d)", out_path, W, H)
            return out_path

        except OSError as exc:
            log.error("generate_summary_image 保存失敗: %s", exc)
            return None
        except Exception as exc:
            log.error("generate_summary_image 予期しないエラー: %s", exc)
            return None

    # ------------------------------------------------------------------
    # 制御
    # ------------------------------------------------------------------

    def set_silence(self, enabled: bool) -> None:
        """静音モードの切り替え。"""
        self._silence_mode = enabled
        log.info("静音モード: %s", "ON" if enabled else "OFF")

    def set_meeting_active(self, active: bool) -> None:
        """会議中フラグの切り替え。"""
        self._meeting_active = active
        log.info("会議中フラグ: %s", "ON" if active else "OFF")

    @property
    def monthly_cost(self) -> float:
        return self._cost_tracker.monthly_cost

    @property
    def config(self) -> dict[str, Any]:
        return self._config

    # ------------------------------------------------------------------
    # 内部: API呼び出し
    # ------------------------------------------------------------------

    async def _call_api(
        self,
        system_prompt: str,
        messages: list[dict[str, str]],
        dept_id: str,
        call_type: str,
        max_tokens: int | None = None,
    ) -> str | None:
        """共通のAPI呼び出しロジック。レートリミット・コスト管理・リトライ込み。"""

        # コスト上限チェック
        if not await self._cost_tracker.can_proceed():
            log.warning(
                "月間コスト上限到達 ($%.2f/$%.0f)。API呼び出しを停止。",
                self._cost_tracker.monthly_cost,
                self._config["monthly_cost_cap_usd"],
            )
            return None

        # レートリミット
        await self._rate_limiter.acquire()

        tokens = max_tokens or self._config["max_response_tokens"]
        timeout = self._config["api_timeout_sec"]

        for attempt in range(2):  # 最大2回 (初回 + リトライ1回)
            try:
                # Anthropic SDK は同期だが、asyncio.to_thread で非同期化
                response = await asyncio.wait_for(
                    asyncio.to_thread(
                        self._client.messages.create,
                        model=self._config["api_model"],
                        max_tokens=tokens,
                        system=system_prompt,
                        messages=messages,
                    ),
                    timeout=timeout,
                )

                # コスト記録
                usage = response.usage
                await self._cost_tracker.add_cost(
                    usage.input_tokens, usage.output_tokens, self._config,
                )

                # テキスト抽出
                text = ""
                for block in response.content:
                    if block.type == "text":
                        text += block.text

                log.info(
                    "DeptBrain [%s/%s]: %d in / %d out / $%.4f (累計$%.2f)",
                    dept_id, call_type,
                    usage.input_tokens, usage.output_tokens,
                    (usage.input_tokens / 1_000_000 * self._config["cost_per_1m_input"]
                     + usage.output_tokens / 1_000_000 * self._config["cost_per_1m_output"]),
                    self._cost_tracker.monthly_cost,
                )

                return text.strip() if text.strip() else None

            except asyncio.TimeoutError:
                log.warning(
                    "DeptBrain [%s/%s]: タイムアウト (%ds) - attempt %d/2",
                    dept_id, call_type, timeout, attempt + 1,
                )
                if attempt == 0:
                    continue
                return None

            except anthropic.RateLimitError as exc:
                log.warning(
                    "DeptBrain [%s/%s]: APIレートリミット - %s. 30秒待機...",
                    dept_id, call_type, exc,
                )
                await asyncio.sleep(30)
                if attempt == 0:
                    continue
                return None

            except anthropic.APIError as exc:
                log.error(
                    "DeptBrain [%s/%s]: APIエラー - %s (attempt %d/2)",
                    dept_id, call_type, exc, attempt + 1,
                )
                if attempt == 0:
                    await asyncio.sleep(2)
                    continue
                return None

            except Exception as exc:
                log.exception(
                    "DeptBrain [%s/%s]: 予期しないエラー - %s",
                    dept_id, call_type, exc,
                )
                return None

        return None
