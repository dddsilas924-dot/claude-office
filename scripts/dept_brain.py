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

安全設計:
- レートリミット（10回/分）
- 月間コスト上限（$30/月）
- APIタイムアウト（15秒）
- エラー時リトライ1回
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

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
        async with self._lock:
            now = time.monotonic()
            # 1分以上前のタイムスタンプを除去
            self._timestamps = [t for t in self._timestamps if now - t < 60.0]

            if len(self._timestamps) >= self._max:
                # 最も古いリクエストから60秒後まで待つ
                wait_time = 60.0 - (now - self._timestamps[0])
                if wait_time > 0:
                    log.info("レートリミット到達。%.1f秒待機...", wait_time)
                    await asyncio.sleep(wait_time)
                    # 待機後に再クリーン
                    now = time.monotonic()
                    self._timestamps = [t for t in self._timestamps if now - t < 60.0]

            self._timestamps.append(time.monotonic())


# ---------------------------------------------------------------------------
# コストトラッカー
# ---------------------------------------------------------------------------
class CostTracker:
    """月間APIコストを追跡する。"""

    def __init__(self, monthly_cap: float = 30.0):
        self._monthly_cap = monthly_cap
        self._monthly_cost = 0.0
        self._current_month: str = ""
        self._lock = asyncio.Lock()

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
        response = await brain.respond("research", "市場調査して", [...], "えむ")
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

        log.info(
            "DeptBrain 初期化完了: model=%s, cap=$%.0f/月, rate=%d/分",
            self._config["api_model"],
            self._config["monthly_cost_cap_usd"],
            self._config["rate_limit_per_minute"],
        )

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

        # shared_state から実データを取得して返答にも反映
        dept_state = self._load_dept_state(dept_id)
        system_prompt = build_system_prompt(dept_id, dept_state)

        # コンテキスト+新メッセージを組み立て
        messages = []
        for ctx in context[-self._config["context_history_limit"]:]:
            messages.append({
                "role": ctx.get("role", "user"),
                "content": ctx.get("content", ""),
            })
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

        # 深夜チェック
        jst = timezone(timedelta(hours=9))
        now_jst = datetime.now(jst)
        quiet = self._config["quiet_hours"]
        if quiet["start"] <= now_jst.hour < quiet["end"]:
            log.debug("静音時間帯 (%02d:00 JST): %s をスキップ", now_jst.hour, dept_id)
            return None

        # shared_state.json から部門の実データを取得
        dept_state = self._load_dept_state(dept_id)

        system_prompt = build_autonomous_prompt(dept_id, activity_type, dept_state)
        messages = [{"role": "user", "content": "自発的に発言してください。"}]

        result = await self._call_api(system_prompt, messages, dept_id, "autonomous")
        if result:
            await self._save_dept_activity(dept_id, "autonomous", result)
        return result

    def _load_dept_state(self, dept_id: str) -> dict[str, Any] | None:
        """shared_state.json から該当部門の実データを読み込む。"""
        # 環境変数を最優先
        env_path = os.environ.get("SHARED_STATE_PATH", "")
        if env_path:
            state_path = Path(env_path)
        else:
            state_path = Path(__file__).parent.parent.parent / "empire_monitor_full_20260321" / ".claude" / "shared_state.json"
        if not state_path.is_file():
            log.debug("shared_state.json が見つかりません: %s", state_path)
            return None
        try:
            with state_path.open(encoding="utf-8") as f:
                data = json.load(f)
            # dept_id のマッピング (Discord部門ID → shared_state のキー)
            dept_map = {
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
            key = dept_map.get(dept_id, dept_id)
            return data.get("departments", {}).get(key)
        except (json.JSONDecodeError, OSError) as exc:
            log.debug("shared_state.json読み込みエラー: %s", exc)
            return None

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

        # Discord部門ID → shared_stateキー
        dept_map = {
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
        key = dept_map.get(dept_id, dept_id)

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

                # JSONを書き戻し
                with state_path.open("w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)

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
        # 会議発言にも実データを注入
        dept_state = self._load_dept_state(current_dept)
        system_prompt = build_meeting_prompt(current_dept, topic, round_num, history, dept_state)
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
        """会議のまとめをフィルとして生成する。"""
        system_prompt = build_meeting_summary_prompt(topic, history)
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
