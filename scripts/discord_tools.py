"""
discord_tools.py - Discord Bot用ツール統合
============================================
部門AIがDiscord上で実際にAPI呼び出しできるようにするツール群。
Anthropic tool_use 形式の定義 + 実行関数。
"""
from __future__ import annotations

import asyncio
import base64
import logging
import os
import re
from pathlib import Path

import httpx

log = logging.getLogger("discord_tools")

# ---------------------------------------------------------------------------
# 環境変数
# ---------------------------------------------------------------------------
_XAI_API_KEY = os.getenv("XAI_API_KEY", "")
_OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
_ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
_MEXC_API_KEY = os.getenv("MEXC_API_KEY", "")
_MEXC_API_SECRET = os.getenv("MEXC_API_SECRET", "")

# ---------------------------------------------------------------------------
# ツール定義（Anthropic tool_use format）
# ---------------------------------------------------------------------------
TOOL_DEFINITIONS: list[dict] = [
    {
        "name": "search_x",
        "description": (
            "X(Twitter)の投稿を検索する。最新のX投稿・トレンド・特定アカウントの発言を調べる時に使う。"
            "Grok API経由でリアルタイム検索。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "検索クエリ（日本語・英語どちらでも可）",
                }
            },
            "required": ["query"],
        },
    },
    {
        "name": "search_web",
        "description": (
            "Webを検索する。最新ニュース・情報・企業情報・市場動向を調べる時に使う。"
            "Grok API経由でリアルタイムWeb検索。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "検索クエリ（日本語・英語どちらでも可）",
                }
            },
            "required": ["query"],
        },
    },
    {
        "name": "get_crypto_price",
        "description": (
            "仮想通貨のリアルタイム価格を取得する。MEXC取引所の公開APIを使用。"
            "シンボルは 'BTC/USDT' 形式。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "symbol": {
                    "type": "string",
                    "description": "仮想通貨シンボル（例: 'BTC/USDT', 'ETH/USDT', 'SOL/USDT'）",
                }
            },
            "required": ["symbol"],
        },
    },
    {
        "name": "fetch_url",
        "description": (
            "URLのWebページ内容を取得する。企業サイト・ニュース記事・LP等の内容を読む時に使う。"
            "HTMLタグは除去してテキストのみ返す。最大2000文字。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "取得するURL（https://から始まる完全なURL）",
                }
            },
            "required": ["url"],
        },
    },
    {
        "name": "generate_image",
        "description": (
            "プロンプトから画像を生成する。OpenAI GPT-Image-1を使用。"
            "生成された画像はDiscordに添付ファイルとして送信される。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "prompt": {
                    "type": "string",
                    "description": "画像生成プロンプト（日本語・英語どちらでも可）",
                },
                "size": {
                    "type": "string",
                    "description": "画像サイズ。'1024x1024'（正方形）/ '1536x1024'（横長バナー向け）/ '1024x1536'（縦長ポスター向け）",
                    "enum": ["1024x1024", "1536x1024", "1024x1536"],
                },
            },
            "required": ["prompt"],
        },
    },
    {
        "name": "generate_copy",
        "description": (
            "コピーライティングを生成する。見出し・広告文・メルマガ・LP・SNS投稿など。"
            "taiyo-styleに基づいた高品質なコピーを複数パターン生成。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "copy_type": {
                    "type": "string",
                    "description": "コピーの種類",
                    "enum": ["headline", "ad_copy", "email", "lp_section", "sns_post"],
                },
                "brief": {
                    "type": "string",
                    "description": "コピーのブリーフ（商品名・ターゲット・訴求ポイント・トーン等を記載）",
                },
                "count": {
                    "type": "integer",
                    "description": "生成するパターン数（デフォルト: 3）",
                    "minimum": 1,
                    "maximum": 5,
                },
            },
            "required": ["copy_type", "brief"],
        },
    },
    # --- 開発部ツール ---
    {
        "name": "git_log",
        "description": (
            "Gitの最新コミット履歴を表示する。開発進捗の確認や他部門への状況報告に使う。"
            "リポジトリのルートで実行される。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "count": {
                    "type": "integer",
                    "description": "表示するコミット数（デフォルト: 10、最大: 30）",
                    "minimum": 1,
                    "maximum": 30,
                },
                "path_filter": {
                    "type": "string",
                    "description": "特定パスのコミットだけ表示（例: 'scripts/' や 'frontend/'）。省略で全体。",
                },
            },
            "required": [],
        },
    },
    {
        "name": "git_status",
        "description": (
            "Gitワーキングツリーの現在状態を表示する。"
            "未コミットの変更・ステージ済み・未追跡ファイルを一覧で返す。"
            "現在のブランチ名とリモートとの差分も含む。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    {
        "name": "dev_request",
        "description": (
            "他部門からの開発依頼を記録する。依頼はJSONファイルに蓄積され、"
            "Claude Code側で参照・着手できる。優先度とカテゴリを自動付与。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {
                    "type": "string",
                    "description": "依頼タイトル（1行で簡潔に）",
                },
                "description": {
                    "type": "string",
                    "description": "依頼の詳細（何をどうしてほしいか）",
                },
                "from_dept": {
                    "type": "string",
                    "description": "依頼元の部門名（例: 'デザイン部', '営業部'）",
                },
                "priority": {
                    "type": "string",
                    "description": "優先度",
                    "enum": ["high", "medium", "low"],
                },
                "category": {
                    "type": "string",
                    "description": "依頼カテゴリ",
                    "enum": ["bug_fix", "new_feature", "improvement", "investigation", "infra"],
                },
            },
            "required": ["title", "description", "from_dept"],
        },
    },
]

# ツール名 → 定義の高速参照
_TOOL_DEF_MAP: dict[str, dict] = {t["name"]: t for t in TOOL_DEFINITIONS}

# ---------------------------------------------------------------------------
# 部門 → 使用可能ツールのマッピング
# ---------------------------------------------------------------------------
DEPT_TOOLS: dict[str, list[str]] = {
    "research": ["search_x", "search_web", "fetch_url"],
    "ai_investment": ["get_crypto_price", "search_x", "search_web"],
    "design": ["generate_image", "search_web"],
    "writing": ["generate_copy", "search_web"],
    "advertising": ["generate_copy", "search_web", "search_x"],
    "sales": ["search_web", "fetch_url"],
    "content": ["search_web", "search_x"],
    "new_biz": ["search_web", "search_x", "fetch_url"],
    "takumi_x": ["search_x", "search_web"],
    "commander": ["search_x", "search_web", "fetch_url", "get_crypto_price"],
    "development": ["git_log", "git_status", "dev_request", "search_web", "fetch_url"],
}
_DEFAULT_TOOLS: list[str] = ["search_web"]


def get_tools_for_dept(dept_id: str) -> list[dict]:
    """部門に応じたツール定義のリストを返す。"""
    tool_names = DEPT_TOOLS.get(dept_id, _DEFAULT_TOOLS)
    return [_TOOL_DEF_MAP[name] for name in tool_names if name in _TOOL_DEF_MAP]


# ---------------------------------------------------------------------------
# ツール実行関数
# ---------------------------------------------------------------------------

async def _search_grok(query: str, tool_type: str) -> str:
    """Grok API (v1/responses) を使ってX投稿またはWebを検索する内部関数。

    Args:
        query: 検索クエリ
        tool_type: "x_search" or "web_search"
    """
    if not _XAI_API_KEY:
        return "エラー: XAI_API_KEY が設定されていません。"

    try:
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(
                "https://api.x.ai/v1/responses",
                headers={
                    "Authorization": f"Bearer {_XAI_API_KEY}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": "grok-4",
                    "input": [{"role": "user", "content": query}],
                    "tools": [{"type": tool_type}],
                },
            )
        resp.raise_for_status()
        data = resp.json()

        # v1/responses の出力構造: output[] → message → content[] → output_text
        content = ""
        for item in data.get("output", []):
            if item.get("type") == "message":
                for block in item.get("content", []):
                    if block.get("type") == "output_text":
                        content += block.get("text", "")

        # 2000文字で切り詰め
        if len(content) > 2000:
            content = content[:1997] + "..."

        return content.strip() if content.strip() else "検索結果が見つかりませんでした。"

    except httpx.HTTPStatusError as exc:
        log.warning("Grok API HTTPエラー: %s %s", exc, exc.response.text[:200])
        return f"検索エラー: HTTP {exc.response.status_code}"
    except Exception as exc:
        log.exception("Grok API 予期しないエラー: %s", exc)
        return f"検索エラー: {exc}"


async def search_x(query: str) -> str:
    """X(Twitter)投稿を検索する。"""
    return await _search_grok(query, "x_search")


async def search_web(query: str) -> str:
    """Webを検索する。"""
    return await _search_grok(query, "web_search")


async def get_crypto_price(symbol: str) -> str:
    """仮想通貨のリアルタイム価格をMEXCから取得する。"""
    try:
        import ccxt.async_support as ccxt_async

        # パブリックAPI（価格取得）は認証不要。キーを渡すと無効キーでエラーになる
        exchange = ccxt_async.mexc()
        try:
            ticker = await exchange.fetch_ticker(symbol)
        finally:
            await exchange.close()

        last = ticker.get("last")
        change_pct = ticker.get("percentage")
        high = ticker.get("high")
        low = ticker.get("low")
        volume = ticker.get("quoteVolume")

        lines = [f"【{symbol} リアルタイム価格】"]
        if last is not None:
            lines.append(f"現在値: ${last:,.4f}" if last < 1 else f"現在値: ${last:,.2f}")
        if change_pct is not None:
            sign = "+" if change_pct >= 0 else ""
            lines.append(f"24h変動: {sign}{change_pct:.2f}%")
        if high is not None and low is not None:
            lines.append(
                f"24h高値/安値: ${high:,.2f} / ${low:,.2f}"
                if high >= 1
                else f"24h高値/安値: ${high:,.4f} / ${low:,.4f}"
            )
        if volume is not None:
            lines.append(f"24h出来高(USDT): ${volume:,.0f}")

        return "\n".join(lines)

    except Exception as exc:
        log.warning("crypto price取得エラー (%s): %s", symbol, exc)
        return f"価格取得エラー ({symbol}): {exc}"


async def fetch_url(url: str) -> str:
    """URLのページ内容を取得してHTMLタグ除去・2000文字に切り詰めて返す。"""
    try:
        async with httpx.AsyncClient(
            timeout=20,
            follow_redirects=True,
        ) as client:
            resp = await client.get(
                url,
                headers={
                    "User-Agent": (
                        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/124.0.0.0 Safari/537.36"
                    )
                },
            )
        resp.raise_for_status()

        text = resp.text
        # HTMLタグ除去
        text = re.sub(r"<[^>]+>", " ", text)
        # スクリプト・スタイルブロックの残骸除去
        text = re.sub(r"(function|var |const |let )\s*\w+", " ", text)
        # 連続空白・改行を圧縮
        text = re.sub(r"\s+", " ", text).strip()

        if len(text) > 2000:
            text = text[:1997] + "..."

        return text if text else "ページ内容が取得できませんでした。"

    except httpx.HTTPStatusError as exc:
        return f"URLアクセスエラー: HTTP {exc.response.status_code}"
    except Exception as exc:
        log.warning("fetch_url エラー (%s): %s", url, exc)
        return f"URLアクセスエラー: {exc}"


async def generate_image(
    prompt: str, size: str = "1024x1024"
) -> tuple[str, bytes | None]:
    """
    プロンプトから画像を生成する。

    Returns:
        (説明テキスト, 画像バイト列) のタプル。
        失敗時は (エラーメッセージ, None)。
    """
    if not _OPENAI_API_KEY:
        return "エラー: OPENAI_API_KEY が設定されていません。", None

    # gpt-image-1 対応サイズ (dall-e-3 の 1792x1024 等は非対応)
    valid_sizes = {"1024x1024", "1536x1024", "1024x1536", "auto"}
    if size not in valid_sizes:
        size = "1536x1024"  # バナー用途はデフォルト横長

    try:
        import openai

        client = openai.AsyncOpenAI(api_key=_OPENAI_API_KEY)
        result = await client.images.generate(
            model="gpt-image-1",
            prompt=prompt,
            size=size,  # type: ignore[arg-type]
            n=1,
        )
        # gpt-image-1 は b64_json をデフォルトで返す
        b64_data = result.data[0].b64_json  # type: ignore[index]
        if b64_data:
            image_bytes = base64.b64decode(b64_data)
        elif result.data[0].url:
            # fallback: URL返却の場合はダウンロード
            async with httpx.AsyncClient(timeout=30) as dl:
                img_resp = await dl.get(result.data[0].url)
                img_resp.raise_for_status()
                image_bytes = img_resp.content
        else:
            return "画像生成に失敗しました（空のレスポンス）。", None
        desc = f"画像生成完了: {prompt[:80]}"
        log.info("generate_image 完了: %d bytes", len(image_bytes))
        return desc, image_bytes

    except Exception as exc:
        log.warning("generate_image エラー: %s", exc)
        return f"画像生成エラー: {exc}", None


async def generate_copy(
    copy_type: str,
    brief: str,
    count: int = 3,
) -> str:
    """
    コピーライティングを生成する。

    Args:
        copy_type: "headline" | "ad_copy" | "email" | "lp_section" | "sns_post"
        brief: 商品・ターゲット・訴求ポイント等のブリーフ
        count: 生成パターン数 (1-5)

    Returns:
        生成されたコピーテキスト。
    """
    if not _ANTHROPIC_API_KEY:
        return "エラー: ANTHROPIC_API_KEY が設定されていません。"

    count = max(1, min(5, count))

    type_configs: dict[str, dict] = {
        "headline": {
            "label": "ヘッドライン",
            "system": (
                "あなたはプロのコピーライター。刺さるヘッドライン職人。"
                "taiyo-styleに基づき、読んだ瞬間に止まるヘッドラインを書く。"
                "15〜50字以内。数字・具体性・感情を必ず入れる。絵文字禁止。"
            ),
            "user_template": (
                "以下のブリーフから、読者が思わず止まるヘッドラインを{count}パターン生成してください。\n\n"
                "ブリーフ:\n{brief}\n\n"
                "出力形式:\n"
                "【パターン1】\n(ヘッドライン)\n\n"
                "【パターン2】\n..."
            ),
        },
        "ad_copy": {
            "label": "広告コピー",
            "system": (
                "あなたはFB/Instagram広告のコピーライター。CPA重視の実践派。"
                "PAS（問題→煽り→解決）構造で書く。改行多め・話し言葉・スクロールを止める冒頭が命。絵文字禁止。"
            ),
            "user_template": (
                "以下のブリーフから、FB/Instagram広告コピーを{count}パターン生成してください。\n\n"
                "ブリーフ:\n{brief}\n\n"
                "各パターンに: 冒頭フック(1行) + 本文(3〜5行) + CTA(1行)\n"
                "出力形式:\n【パターン1】\n..."
            ),
        },
        "email": {
            "label": "メールコピー",
            "system": (
                "あなたはメールマーケティングのプロ。件名で開封率が決まると知っている。"
                "件名15〜30字・本文は関係構築型・CTAは具体的。絵文字禁止。"
            ),
            "user_template": (
                "以下のブリーフから、メールコピーを{count}パターン生成してください。\n\n"
                "ブリーフ:\n{brief}\n\n"
                "各パターンに: 件名 + 本文(300〜500字) + CTA\n"
                "出力形式:\n【パターン1】\n件名: ...\n本文: ...\nCTA: ..."
            ),
        },
        "lp_section": {
            "label": "LPセクション",
            "system": (
                "あなたはLP制作の達人。taiyo-style-lpに精通。"
                "読者の感情曲線を意識した構成で書く。小学3年生が読んでもわかる言葉を使う。絵文字禁止。"
            ),
            "user_template": (
                "以下のブリーフから、LPセクションのコピーを{count}パターン生成してください。\n\n"
                "ブリーフ:\n{brief}\n\n"
                "各パターンに: 見出し + サブ見出し + 本文(100〜200字)\n"
                "出力形式:\n【パターン1】\n見出し: ...\nサブ: ...\n本文: ..."
            ),
        },
        "sns_post": {
            "label": "SNS投稿",
            "system": (
                "あなたはSNS投稿のプロ。Xなら140字・改行多め・煽り。"
                "FBなら300〜800字・ストーリー型・共感重視。"
                "読者が「シェアしたい」と思う投稿を書く。絵文字禁止。"
            ),
            "user_template": (
                "以下のブリーフから、SNS投稿を{count}パターン生成してください。\n\n"
                "ブリーフ:\n{brief}\n\n"
                "出力形式:\n【パターン1】\n..."
            ),
        },
    }

    config = type_configs.get(copy_type, type_configs["headline"])
    user_msg = config["user_template"].format(count=count, brief=brief)

    try:
        import anthropic

        client = anthropic.Anthropic(api_key=_ANTHROPIC_API_KEY)
        response = await asyncio.to_thread(
            client.messages.create,
            model="claude-haiku-4-5-20251001",  # コスト効率のいいモデルを使用
            max_tokens=1500,
            system=config["system"],
            messages=[{"role": "user", "content": user_msg}],
        )

        text = ""
        for block in response.content:
            if block.type == "text":
                text += block.text

        label = config["label"]
        result = f"【{label} {count}パターン生成完了】\n\n{text.strip()}"

        if len(result) > 2000:
            result = result[:1997] + "..."

        return result

    except Exception as exc:
        log.warning("generate_copy エラー: %s", exc)
        return f"コピー生成エラー: {exc}"


# ---------------------------------------------------------------------------
# 開発部ツール
# ---------------------------------------------------------------------------

# プロジェクトルート（claude-office）
_PROJECT_ROOT = Path(__file__).resolve().parent.parent


async def git_log(count: int = 10, path_filter: str = "") -> str:
    """Gitの最新コミット履歴を取得する。"""
    count = max(1, min(30, count))
    cmd = [
        "git", "log",
        f"--max-count={count}",
        "--format=%h %ad %an | %s",
        "--date=format:%m/%d %H:%M",
    ]
    if path_filter:
        cmd.append("--")
        cmd.append(path_filter)

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(_PROJECT_ROOT),
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=10)
        output = stdout.decode().strip()
        if not output:
            return "コミット履歴が見つかりません。"

        # ブランチ名も取得
        branch_proc = await asyncio.create_subprocess_exec(
            "git", "branch", "--show-current",
            stdout=asyncio.subprocess.PIPE,
            cwd=str(_PROJECT_ROOT),
        )
        branch_out, _ = await asyncio.wait_for(branch_proc.communicate(), timeout=5)
        branch = branch_out.decode().strip() or "不明"

        header = f"ブランチ: {branch} | 最新{count}件\n{'─' * 40}\n"
        result = header + output
        if len(result) > 2000:
            result = result[:1997] + "..."
        return result

    except asyncio.TimeoutError:
        return "エラー: git log がタイムアウトしました。"
    except Exception as exc:
        log.warning("git_log エラー: %s", exc)
        return f"git logエラー: {exc}"


async def git_status() -> str:
    """Gitワーキングツリーの状態を取得する。"""
    try:
        # ブランチ + ステータス
        proc = await asyncio.create_subprocess_exec(
            "git", "status", "--short", "--branch",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(_PROJECT_ROOT),
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=10)
        status_output = stdout.decode().strip()

        # リモートとの差分
        diff_proc = await asyncio.create_subprocess_exec(
            "git", "diff", "--stat", "HEAD",
            stdout=asyncio.subprocess.PIPE,
            cwd=str(_PROJECT_ROOT),
        )
        diff_out, _ = await asyncio.wait_for(diff_proc.communicate(), timeout=10)
        diff_output = diff_out.decode().strip()

        result = f"【ワーキングツリー状態】\n{status_output}"
        if diff_output:
            result += f"\n\n【変更サマリー】\n{diff_output}"

        if len(result) > 2000:
            result = result[:1997] + "..."
        return result if result.strip() else "ワーキングツリーはクリーンです。変更なし。"

    except asyncio.TimeoutError:
        return "エラー: git status がタイムアウトしました。"
    except Exception as exc:
        log.warning("git_status エラー: %s", exc)
        return f"git statusエラー: {exc}"


async def dev_request(
    title: str,
    description: str,
    from_dept: str,
    priority: str = "medium",
    category: str = "new_feature",
) -> str:
    """他部門からの開発依頼を記録する。"""
    import json as _json
    from datetime import datetime, timezone, timedelta

    JST = timezone(timedelta(hours=9))
    now = datetime.now(JST)

    request_file = _PROJECT_ROOT / "memory" / "dev_requests.json"
    request_file.parent.mkdir(parents=True, exist_ok=True)

    # 既存データ読み込み
    requests: list[dict] = []
    if request_file.exists():
        try:
            requests = _json.loads(request_file.read_text(encoding="utf-8"))
        except Exception:
            requests = []

    # 新規依頼を追加
    new_request = {
        "id": f"DEV-{now.strftime('%m%d%H%M')}-{len(requests) + 1:03d}",
        "title": title,
        "description": description,
        "from_dept": from_dept,
        "priority": priority,
        "category": category,
        "status": "pending",
        "created_at": now.isoformat(),
    }
    requests.append(new_request)

    # 保存
    request_file.write_text(
        _json.dumps(requests, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    priority_label = {"high": "🔴 高", "medium": "🟡 中", "low": "🟢 低"}.get(priority, priority)
    category_label = {
        "bug_fix": "バグ修正",
        "new_feature": "新機能",
        "improvement": "改善",
        "investigation": "調査",
        "infra": "インフラ",
    }.get(category, category)

    log.info("dev_request 登録: %s (from=%s, priority=%s)", new_request["id"], from_dept, priority)

    return (
        f"開発依頼を受け付けました。\n\n"
        f"ID: {new_request['id']}\n"
        f"タイトル: {title}\n"
        f"依頼元: {from_dept}\n"
        f"優先度: {priority_label}\n"
        f"カテゴリ: {category_label}\n"
        f"ステータス: 待機中\n\n"
        f"Claude Code側で着手します。進捗はこのチャンネルで報告します。"
    )


# ---------------------------------------------------------------------------
# ディスパッチ関数
# ---------------------------------------------------------------------------

async def execute_tool(
    name: str,
    arguments: dict,
) -> str | tuple[str, bytes | None]:
    """
    ツール名と引数を受け取り、結果を返す。

    Returns:
        str: テキスト結果（search_x / search_web / get_crypto_price / fetch_url / generate_copy）
        tuple[str, bytes | None]: (テキスト, 画像バイト) (generate_image)
    """
    log.info("execute_tool: %s args=%s", name, list(arguments.keys()))

    if name == "search_x":
        return await search_x(arguments.get("query", ""))

    elif name == "search_web":
        return await search_web(arguments.get("query", ""))

    elif name == "get_crypto_price":
        return await get_crypto_price(arguments.get("symbol", "BTC/USDT"))

    elif name == "fetch_url":
        return await fetch_url(arguments.get("url", ""))

    elif name == "generate_image":
        return await generate_image(
            prompt=arguments.get("prompt", ""),
            size=arguments.get("size", "1024x1024"),
        )

    elif name == "generate_copy":
        return await generate_copy(
            copy_type=arguments.get("copy_type", "headline"),
            brief=arguments.get("brief", ""),
            count=int(arguments.get("count", 3)),
        )

    # --- 開発部ツール ---
    elif name == "git_log":
        return await git_log(
            count=int(arguments.get("count", 10)),
            path_filter=arguments.get("path_filter", ""),
        )

    elif name == "git_status":
        return await git_status()

    elif name == "dev_request":
        return await dev_request(
            title=arguments.get("title", ""),
            description=arguments.get("description", ""),
            from_dept=arguments.get("from_dept", "不明"),
            priority=arguments.get("priority", "medium"),
            category=arguments.get("category", "new_feature"),
        )

    else:
        log.warning("未知のツール: %s", name)
        return f"エラー: 未知のツール '{name}'"
