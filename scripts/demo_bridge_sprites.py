#!/usr/bin/env python3
"""Seed the Office canvas with 12 Commander Bridge sprites.

Mirrors what the real Commander Bridge
(``scripts/commander_bridge_public/app/office_client.py``) posts to
``/api/v1/external_event`` when ドラ fires a slash command on Discord —
but without needing the VPS. Useful for:

* **Local smoke test.** Boot the Office backend + frontend, run this
  script, verify all 12 sprites render with Japanese labels, emoji
  badges, and dept-specific colors.
* **Branding screenshot.** Populates the canvas with a known, visually
  consistent lineup so FB/press screenshots don't catch it mid-empty.

The script signs every request with the exact same HMAC shape as the
producer on the VPS — swapping this for the real Bridge is a drop-in.

Environment
-----------
``OFFICE_BASE_URL``
    Defaults to ``http://localhost:8000``. Point at a live Office
    deployment if you want to seed a remote canvas (you'll need the
    matching ``EXTERNAL_EVENT_SECRET``).

``EXTERNAL_EVENT_SECRET``
    Shared secret the Office backend was started with. Must match or
    every POST gets 401.

Usage
-----
    # Default: seed sim_session_123 against http://localhost:8000
    EXTERNAL_EVENT_SECRET=localdev python scripts/demo_bridge_sprites.py

    # Custom session
    python scripts/demo_bridge_sprites.py --session my_canvas

    # Staggered so you can watch sprites appear one by one
    python scripts/demo_bridge_sprites.py --delay 0.5

    # Only a subset
    python scripts/demo_bridge_sprites.py --depts commander,research,content

    # Short bubble text so ラベル/吹き出し contrast is obvious
    python scripts/demo_bridge_sprites.py --kind ASK_COMPLETED
"""
from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any


# ---------------------------------------------------------------------------
# Canonical 12-dept persona lineup
# ---------------------------------------------------------------------------
# Inlined on purpose — the Bridge's ``personas.py`` lives in a different
# repo (empire_monitor) and importing across repo boundaries would make
# this demo brittle. If the Bridge persona sheet changes, update here.
# Values mirror ``app/personas.py`` in the Bridge repo as of 2026-04-21.
@dataclass(frozen=True)
class Persona:
    dept_id: str
    emoji: str
    division_jp: str
    character: str
    color: str  # 0xRRGGBB → "#RRGGBB" for Office's agent_color field


PERSONAS: list[Persona] = [
    Persona("commander", "🧠", "司令塔", "フィル", "#F1C40F"),
    Persona("research", "🔬", "リサーチ事業部", "リョウ", "#3498DB"),
    Persona("content", "🎬", "コンテンツ事業部", "ハル", "#E74C3C"),
    Persona("writing", "✍️", "ライティング事業部", "カイ", "#F39C12"),
    Persona("design", "🎨", "デザイン事業部", "リック", "#9B59B6"),
    Persona("advertising", "📣", "広告部", "エナ", "#E67E22"),
    Persona("sales", "💼", "営業事業部", "レイ", "#2ECC71"),
    Persona("new_biz", "🚀", "新規事業開発部", "タダシ", "#1ABC9C"),
    Persona("phil", "🎓", "フィルコンサル事業部", "フィル", "#F1C40F"),
    Persona("ai_inv", "💰", "AI投資エージェント部", "アキ", "#27AE60"),
    Persona("security", "🔒", "セキュリティ体制整備", "モード", "#34495E"),
    Persona("doradora_sns", "📱", "どらどらSNS運用", "ソラ", "#E91E63"),
]


# Short status messages per dept — chosen so the bubble text reads like
# a real working day's worth of chatter rather than test filler. Keeps
# the FB screenshot authentic.
DEMO_MESSAGES: dict[str, str] = {
    "commander": "全部門の朝ブリーフ、出揃った。優先は広告部のクリエイティブ仕上げ。",
    "research": "FBグループ312件、参加リクエスト今日30件ずつ投げる。",
    "content": "台本v2、小3テスト通過。次はスライド化をデザイン部へ。",
    "writing": "LPの見出し案3パターン、taiyo-analyzer 88点で納品可。",
    "design": "メカニック基調のスライド、14部門分のキャラ差分を詰める。",
    "advertising": "FB広告コンセプト3本、CPA想定とあわせて共有済み。",
    "sales": "商談プレイブック、3件分けて出した。火曜にデモ1本あり。",
    "new_biz": "新規事業3案、市場規模と参入タイミングの比較表まとめた。",
    "phil": "受講生のMD、今週7本到着。業種別タグ貼って保管した。",
    "ai_inv": "新プロトコルAPY、昨日比+4.2%。Telegramで速報流す。",
    "security": "50MB超ファイル、commit前に検知してブロックした件あり。",
    "doradora_sns": "どらどら X、過去30件の重複チェック通過。下書き2本。",
}


# ---------------------------------------------------------------------------
# Signing — byte-for-byte identical to Bridge's ``office_client._sign``.
# ---------------------------------------------------------------------------
SIGNATURE_HEADER = "X-Bridge-Signature"
TIMESTAMP_HEADER = "X-Bridge-Timestamp"
SIGNATURE_PREFIX = "sha256="
ENDPOINT_PATH = "/api/v1/external_event"


def sign(secret: bytes, timestamp: str, body: bytes) -> str:
    mac = hmac.new(
        secret,
        msg=f"{timestamp}.".encode("utf-8") + body,
        digestmod=hashlib.sha256,
    )
    return SIGNATURE_PREFIX + mac.hexdigest()


def post_event(
    *,
    base_url: str,
    secret: bytes,
    session_id: str,
    persona: Persona,
    event_kind: str,
    message: str,
    model: str | None,
) -> tuple[int, str]:
    """POST a single signed event. Returns (status, body_first_200chars)."""
    payload: dict[str, Any] = {
        "session_id": session_id,
        "dept_id": persona.dept_id,
        "display_name": f"{persona.emoji} {persona.division_jp} {persona.character}",
        "agent_color": persona.color,
        "event_kind": event_kind,
        "message": message,
        "status": "ok",
    }
    if model is not None:
        payload["model"] = model
    # ensure_ascii=True matches the producer's encoding. Japanese chars
    # become \\uXXXX escapes so the signed bytes are locale-independent.
    body = json.dumps(payload, ensure_ascii=True).encode("utf-8")
    ts = str(int(time.time()))
    headers = {
        "Content-Type": "application/json",
        SIGNATURE_HEADER: sign(secret, ts, body),
        TIMESTAMP_HEADER: ts,
    }
    url = f"{base_url.rstrip('/')}{ENDPOINT_PATH}"
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=8) as resp:
            return resp.status, resp.read(200).decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read(200).decode("utf-8", errors="replace")
    except urllib.error.URLError as exc:
        return 0, f"transport: {exc.reason}"


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Seed Office canvas with 12 Commander Bridge sprites.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument(
        "--base-url",
        default=os.environ.get("OFFICE_BASE_URL", "http://localhost:8000"),
        help="Office backend base URL (default: env OFFICE_BASE_URL or http://localhost:8000).",
    )
    p.add_argument(
        "--session",
        default="sim_session_123",
        help="Target session_id. Must match what the browser WebSocket is subscribed to.",
    )
    p.add_argument(
        "--depts",
        default="",
        help="Comma-separated dept_ids to emit (default: all 12).",
    )
    p.add_argument(
        "--kind",
        default="ASK_COMPLETED",
        help=(
            "event_kind for the payload (default: ASK_COMPLETED). "
            "ASK_STARTED makes sprites show the 'typing' animation."
        ),
    )
    p.add_argument(
        "--delay",
        type=float,
        default=0.0,
        help="Seconds to wait between events (default: 0 — fire all at once).",
    )
    p.add_argument(
        "--model",
        default="sonnet",
        help="Model badge on each sprite (haiku|sonnet|opus). Empty string to omit.",
    )
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    secret_env = os.environ.get("EXTERNAL_EVENT_SECRET", "").strip()
    if not secret_env:
        print(
            "error: EXTERNAL_EVENT_SECRET is not set. "
            "Start the Office backend with the same value and export it here.",
            file=sys.stderr,
        )
        return 2
    secret = secret_env.encode("utf-8")

    wanted_ids = (
        {d.strip() for d in args.depts.split(",") if d.strip()}
        if args.depts
        else None
    )
    targets = [p for p in PERSONAS if wanted_ids is None or p.dept_id in wanted_ids]
    if not targets:
        print(
            f"error: no personas matched --depts='{args.depts}'. "
            f"Available: {', '.join(p.dept_id for p in PERSONAS)}",
            file=sys.stderr,
        )
        return 2

    model = args.model.strip() or None

    print(
        f"Seeding {len(targets)} bridge sprites → {args.base_url}{ENDPOINT_PATH} "
        f"(session={args.session}, kind={args.kind}, delay={args.delay}s)"
    )
    failures = 0
    for persona in targets:
        message = DEMO_MESSAGES.get(persona.dept_id, "稼働中。")
        status, body = post_event(
            base_url=args.base_url,
            secret=secret,
            session_id=args.session,
            persona=persona,
            event_kind=args.kind,
            message=message,
            model=model,
        )
        mark = "✓" if 200 <= status < 300 else "✗"
        print(
            f"  {mark} {persona.dept_id:<14} {persona.emoji} {persona.character:<6} "
            f"→ HTTP {status}{f' — {body.strip()}' if status >= 400 or status == 0 else ''}"
        )
        if not (200 <= status < 300):
            failures += 1
        if args.delay > 0 and persona is not targets[-1]:
            time.sleep(args.delay)

    if failures:
        print(
            f"\nDone with {failures}/{len(targets)} failures. "
            f"Check the backend log + that EXTERNAL_EVENT_SECRET matches.",
            file=sys.stderr,
        )
        return 1
    print(f"\nDone. {len(targets)} sprites seeded on session '{args.session}'.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
