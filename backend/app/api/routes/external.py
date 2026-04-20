"""`/api/v1/external_event` — HMAC-signed event ingress from Commander Bridge.

This endpoint exists to let an **external** system (namely the Commander
Bridge Discord bot) push events into a Claude Office canvas without
running inside a Claude Code hook. It's the door for two kinds of signal
the hook-based `/events` endpoint can't carry:

1. **Conversational lifecycle** (``/ask``, ``/consult``, ``/meet``) —
   when えむ fires a slash command on Discord, a matching AgentSprite in
   the canvas should light up, walk, speak. The Bridge sends us that
   intent here; the existing WebSocket fan-out ships it to the browser.
2. **Cross-machine broadcasts** — the Bridge runs on a VPS, the canvas
   runs on えむ's laptop. The hook channel assumes co-located Claude
   Code; this channel doesn't.

Design choices:
- **HMAC-SHA256 with timestamp.** The shared secret lives in
  ``EXTERNAL_EVENT_SECRET`` and is the only auth. No tokens, no OAuth —
  the Bridge is the sole client, and we want a stateless door. The
  timestamp header blocks replay (±5min skew).
- **Single secret, not per-client.** There's exactly one legitimate
  caller. Key rotation is a redeploy on both sides.
- **Early 503 when secret unset.** Shipping with an empty secret is the
  default — if the operator hasn't wired it up, we refuse outright
  rather than silently accepting unsigned traffic.
- **Re-emits via existing WebSocket manager.** We don't run any state
  machine on these events. The frontend `AgentSprite` consumer handles
  the visual — keeps the backend dumb enough to debug fast.

Payload schema lives in this module (not ``app.models``) because it's a
thin transport shim for the Bridge. If more external producers ever
show up, the type moves to ``app.models.external``.
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import logging
import time
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Header, HTTPException, Request, status
from pydantic import BaseModel, Field

from app.api.websocket import manager
from app.config import get_settings

log = logging.getLogger(__name__)
router = APIRouter()


# ---------------------------------------------------------------------------
# Threat model (quick reference)
# ---------------------------------------------------------------------------
# Trust boundary:
#   - Single legitimate caller: the Commander Bridge running on a VPS.
#   - Secret ``EXTERNAL_EVENT_SECRET`` is a pre-shared key, rotated by
#     redeploy of both Bridge and Office.
#
# Defenses (in order):
#   1. TLS terminates at the ingress (Cloudflare/NGINX) — body and
#      signature are not observable on the wire.
#   2. HMAC-SHA256 over ``<timestamp>.<body>`` — unless the attacker has
#      the secret, forging a valid request requires a successful TLS MITM
#      AND knowledge of the secret.
#   3. Timestamp window (±5min) — a captured request can only replay
#      during that window. Chosen to match GitHub/Stripe/AWS SigV4
#      conventions so operators' intuitions carry over.
#   4. In-memory nonce cache keyed by signature — the SAME signed bytes
#      cannot replay twice within the skew window.
#
# Out of scope (deploy-layer / future hardening):
#   - Multi-tenant auth. If we ever serve >1 client, switch to
#     per-client secrets and a Key-ID header.
#   - OAuth / API-key on top of HMAC. HMAC with a pre-shared secret IS
#     the authorization mechanism for this service-to-service channel
#     (same pattern as Stripe, GitHub, Slack webhooks). Adding OAuth
#     client-credentials would reduce to a shared secret with extra
#     round-trips — architecturally net-neutral.
#   - Rate limiting. Belongs at the ingress (Cloudflare / NGINX /
#     Traefik) where 4xx on unsigned requests can be absorbed before
#     reaching Python. An in-process limiter would compete with our own
#     fast-fail 401 path.
#   - Persistent replay log. The in-memory cache loses state on restart;
#     worst case an attacker replays during a narrow window straddling a
#     reboot. The timestamp check still bounds that at 5 minutes.


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
# Replay window for the ``X-Bridge-Timestamp`` header. 5 minutes matches
# the Anthropic prompt-cache TTL on the parent system, which is a nice
# coincidence but also a sensible default — enough slack for clock drift
# (NTP-synced VPS vs laptop) without letting a captured request live for
# hours on somebody's proxy.
_MAX_TIMESTAMP_SKEW_SEC = 300

# Header names. Lowercase because FastAPI normalises both sides, but the
# Bridge will quote these exactly in its sender.
_SIGNATURE_HEADER = "x-bridge-signature"
_TIMESTAMP_HEADER = "x-bridge-timestamp"

# The ``sha256=`` prefix matches GitHub webhooks — a well-known pattern so
# future clients don't invent their own envelope.
_SIGNATURE_PREFIX = "sha256="


# ---------------------------------------------------------------------------
# Replay cache
# ---------------------------------------------------------------------------
# Exact-duplicate guard: even inside the 5-minute skew window, a
# previously-accepted signature must not be replayable. We store
# signatures paired with their acceptance time and prune on insert.
#
# An LRU-by-time with hard cap keeps memory bounded even under attack.
# Cap 10_000 entries ≈ 6 bytes × 64-char sig ≈ 1MB, which is fine.
# Under normal Bridge volume (a few /ask per minute) the cache is tiny.
#
# The lock is an asyncio.Lock so we don't race under concurrent requests;
# contention is trivial because the critical section is O(log n) dict ops.
_NONCE_CAP = 10_000
_nonce_cache: dict[str, float] = {}
_nonce_lock = asyncio.Lock()


async def _check_and_record_nonce(signature: str, now: float | None = None) -> bool:
    """True if ``signature`` is new (and recorded); False if a replay.

    ``now`` is exposed for tests. The cleanup pass inline is O(n) worst
    case but only runs when cap is exceeded, which for legitimate Bridge
    traffic will basically never happen.
    """
    current = time.time() if now is None else now
    cutoff = current - _MAX_TIMESTAMP_SKEW_SEC
    async with _nonce_lock:
        # Prune aged-out entries opportunistically on every call. The
        # skew window means anything older than cutoff couldn't have
        # authenticated anyway, so dropping them is safe.
        expired = [k for k, t in _nonce_cache.items() if t < cutoff]
        for k in expired:
            _nonce_cache.pop(k, None)

        # Hard cap — if the cache somehow blows past _NONCE_CAP under
        # pathological load, drop the oldest half. Prevents OOM if an
        # attacker is spraying unique signatures at us.
        if len(_nonce_cache) >= _NONCE_CAP:
            sorted_items = sorted(_nonce_cache.items(), key=lambda kv: kv[1])
            half = _NONCE_CAP // 2
            for k, _ in sorted_items[:half]:
                _nonce_cache.pop(k, None)

        if signature in _nonce_cache:
            return False
        _nonce_cache[signature] = current
        return True


def _reset_nonce_cache() -> None:
    """Test hook — not exported."""
    _nonce_cache.clear()


# ---------------------------------------------------------------------------
# Payload schema
# ---------------------------------------------------------------------------
class ExternalEventPayload(BaseModel):
    """One semantic event pushed by the Commander Bridge.

    Kept deliberately flat (no nested ``data`` block) because the Bridge
    doesn't have the rich hook context Claude Code hooks have — it just
    has "a dept said/did something". If we ever add image attachments,
    they'll go in ``attachments`` alongside ``message``.
    """

    session_id: str = Field(
        ..., min_length=1, max_length=128, description="Target canvas session id."
    )
    dept_id: str = Field(
        ...,
        min_length=1,
        max_length=64,
        description="Canonical dept id (e.g. ``research``, ``commander``).",
    )
    display_name: str = Field(
        ...,
        min_length=1,
        max_length=128,
        description="Human label shown by AgentSprite, e.g. ``🔬 リサーチ事業部 リョウ``.",
    )
    agent_color: str = Field(
        default="#95A5A6",
        pattern=r"^#[0-9A-Fa-f]{6}$",
        description="Hex color used for speech-bubble / sprite tint.",
    )
    event_kind: str = Field(
        ...,
        min_length=1,
        max_length=64,
        description="Bridge-side audit event name (e.g. ``ASK_COMPLETED``).",
    )
    message: str = Field(
        default="",
        max_length=4000,
        description="Human-readable body to render in the speech bubble.",
    )
    model: str | None = Field(
        default=None,
        max_length=32,
        description="``haiku`` / ``sonnet`` / ``opus`` / None. Lets the sprite pick a badge.",
    )
    duration_s: float | None = Field(
        default=None, ge=0, description="Seconds the underlying call took, if known."
    )
    status: str = Field(
        default="ok",
        max_length=32,
        description="Bridge relay status (``ok`` / ``timeout`` / ``auth_error`` / etc.).",
    )
    timestamp: datetime | None = Field(
        default=None,
        description="Event wall-clock timestamp. Server fills in if omitted.",
    )
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="Bridge-side free-form context (meeting_id, topic, etc.).",
    )


# ---------------------------------------------------------------------------
# Signature helpers
# ---------------------------------------------------------------------------
def _verify_signature(body: bytes, timestamp: str, provided_sig: str, secret: str) -> bool:
    """Constant-time verify ``sha256=<hex>`` of ``<timestamp>.<body>``.

    The timestamp is prefixed into the signed material so a replay of the
    same body with a fresh timestamp isn't valid — the signature only
    lines up when the signer had both parts at the same time.
    """
    if not provided_sig.startswith(_SIGNATURE_PREFIX):
        return False
    provided_hex = provided_sig[len(_SIGNATURE_PREFIX) :]
    mac = hmac.new(
        secret.encode("utf-8"),
        msg=f"{timestamp}.".encode("utf-8") + body,
        digestmod=hashlib.sha256,
    )
    expected_hex = mac.hexdigest()
    # compare_digest tolerates different lengths safely — returns False
    # instead of leaking via a short-circuit.
    return hmac.compare_digest(expected_hex, provided_hex)


def _timestamp_fresh(timestamp: str, now: float | None = None) -> bool:
    """True when ``timestamp`` is within ±``_MAX_TIMESTAMP_SKEW_SEC`` of now."""
    try:
        ts_num = float(timestamp)
    except (TypeError, ValueError):
        return False
    current = time.time() if now is None else now
    return abs(current - ts_num) <= _MAX_TIMESTAMP_SKEW_SEC


# ---------------------------------------------------------------------------
# Broadcast
# ---------------------------------------------------------------------------
async def _broadcast_external(payload: ExternalEventPayload) -> None:
    """Push the event onto the session's WebSocket fan-out.

    Uses a dedicated ``type: external_event`` wire shape so the frontend
    doesn't confuse Bridge traffic with hook-driven ``event`` / ``state``
    messages. AgentSprite's consumer (Phase 2c) keys off ``dept_id`` and
    ``event_kind`` for visual routing.
    """
    ts = payload.timestamp or datetime.now(tz=UTC)
    wire = {
        "type": "external_event",
        "timestamp": ts.isoformat(),
        "event": {
            "session_id": payload.session_id,
            "dept_id": payload.dept_id,
            "display_name": payload.display_name,
            "agent_color": payload.agent_color,
            "event_kind": payload.event_kind,
            "message": payload.message,
            "model": payload.model,
            "duration_s": payload.duration_s,
            "status": payload.status,
            "metadata": payload.metadata,
        },
    }
    try:
        await manager.broadcast(wire, payload.session_id)
    except Exception:
        log.exception(
            "external_event broadcast failed session_id=%s dept=%s kind=%s",
            payload.session_id,
            payload.dept_id,
            payload.event_kind,
        )


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------
@router.post(
    "/external_event",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Ingest an HMAC-signed event from the Commander Bridge.",
)
async def receive_external_event(
    request: Request,
    background_tasks: BackgroundTasks,
    x_bridge_signature: str = Header(
        default="",
        alias="X-Bridge-Signature",
        description="``sha256=<hex>`` HMAC over ``<timestamp>.<body>``.",
    ),
    x_bridge_timestamp: str = Header(
        default="",
        alias="X-Bridge-Timestamp",
        description="Unix seconds (float ok) when the request was signed.",
    ),
) -> dict[str, str]:
    """Entry point for external events.

    Order of checks (each returns a specific HTTP code so the Bridge can
    alert on the real failure mode):

    1. Secret configured? → 503 otherwise (feature disabled)
    2. Signature + timestamp headers present? → 401 otherwise
    3. Timestamp within ±5min? → 401 otherwise (replay guard)
    4. HMAC matches? → 401 otherwise
    5. Body parses as ``ExternalEventPayload``? → 422 via FastAPI
    6. Queue broadcast as a background task. Return 202.

    We read the raw request body (not a Pydantic-bound arg) so the HMAC
    runs over the exact bytes the client signed — any model-level
    re-serialisation would shift the bytes and break the signature.
    """
    settings = get_settings()
    secret = settings.EXTERNAL_EVENT_SECRET
    if not secret:
        # 503 is intentional — the secret must be wired on purpose, and
        # sending 401 here would hide a real config bug behind an auth
        # error. Operators need to tell these apart.
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="external_event endpoint disabled — EXTERNAL_EVENT_SECRET unset.",
        )

    if not x_bridge_signature or not x_bridge_timestamp:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="missing X-Bridge-Signature / X-Bridge-Timestamp.",
        )

    if not _timestamp_fresh(x_bridge_timestamp):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="X-Bridge-Timestamp outside allowed skew.",
        )

    raw_body = await request.body()
    if not _verify_signature(raw_body, x_bridge_timestamp, x_bridge_signature, secret):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="signature verification failed.",
        )

    # Replay guard: identical signed bytes cannot be accepted twice
    # within the skew window. We use the full signature header as the
    # nonce; since HMAC output depends on secret + timestamp + body, any
    # legitimate distinct request yields a distinct signature.
    if not await _check_and_record_nonce(x_bridge_signature):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="duplicate signature — replay rejected.",
        )

    # Defer JSON parsing to Pydantic for consistent error shapes with the
    # rest of the API. model_validate_json raises ValidationError which
    # FastAPI surfaces as 422.
    # Prefer the ``HTTP_422_UNPROCESSABLE_CONTENT`` alias added in
    # FastAPI/Starlette 0.50+; fall back to the older name on older
    # installs so the module imports either way.
    _UNPROCESSABLE = getattr(
        status, "HTTP_422_UNPROCESSABLE_CONTENT", status.HTTP_422_UNPROCESSABLE_ENTITY
    )
    try:
        payload = ExternalEventPayload.model_validate_json(raw_body)
    except ValueError as exc:  # pydantic raises ValueError subclasses
        raise HTTPException(
            status_code=_UNPROCESSABLE,
            detail=f"invalid payload: {exc}",
        ) from exc

    background_tasks.add_task(_broadcast_external, payload)

    log.info(
        "external_event accepted session=%s dept=%s kind=%s status=%s",
        payload.session_id,
        payload.dept_id,
        payload.event_kind,
        payload.status,
    )

    return {
        "status": "accepted",
        "session_id": payload.session_id,
        "dept_id": payload.dept_id,
        "event_kind": payload.event_kind,
    }
