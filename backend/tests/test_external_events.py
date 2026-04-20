"""Tests for the `/api/v1/external_event` HMAC-signed ingress.

Covers:
- 503 when EXTERNAL_EVENT_SECRET is unset (default)
- 401 on missing signature / timestamp headers
- 401 on stale timestamp (replay)
- 401 on bad signature
- 422 on malformed body
- 202 on the happy path + signature validation helper behaviour

We mutate settings on a *copy* via dependency-clear then ``lru_cache``
reset so the production ``get_settings()`` singleton isn't poisoned
across test modules.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import time
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.api.routes.external import (
    _MAX_TIMESTAMP_SKEW_SEC,
    _check_and_record_nonce,
    _reset_nonce_cache,
    _timestamp_fresh,
    _verify_signature,
)
from app.config import get_settings
from app.main import app

_SECRET = "test-secret-abc123"


def _sign(body: bytes, timestamp: str, secret: str = _SECRET) -> str:
    mac = hmac.new(
        secret.encode("utf-8"),
        msg=f"{timestamp}.".encode("utf-8") + body,
        digestmod=hashlib.sha256,
    )
    return "sha256=" + mac.hexdigest()


@pytest.fixture
def secret_enabled() -> Iterator[None]:
    """Temporarily enable the endpoint by pushing a secret into settings."""
    get_settings.cache_clear()
    settings = get_settings()
    original = settings.EXTERNAL_EVENT_SECRET
    settings.EXTERNAL_EVENT_SECRET = _SECRET  # type: ignore[misc]
    # Start every test with a clean replay cache so stale signatures
    # from a prior test can't 409 on the happy path.
    _reset_nonce_cache()
    try:
        yield
    finally:
        settings.EXTERNAL_EVENT_SECRET = original  # type: ignore[misc]
        get_settings.cache_clear()
        _reset_nonce_cache()


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


# ---------------------------------------------------------------------------
# Signature helpers — tested directly so a regression here is obvious
# ---------------------------------------------------------------------------
def test_verify_signature_accepts_valid() -> None:
    body = b'{"hello": "world"}'
    ts = str(int(time.time()))
    sig = _sign(body, ts)
    assert _verify_signature(body, ts, sig, _SECRET) is True


def test_verify_signature_rejects_tampered_body() -> None:
    body = b'{"hello": "world"}'
    ts = str(int(time.time()))
    sig = _sign(body, ts)
    assert _verify_signature(b'{"hello": "evil"}', ts, sig, _SECRET) is False


def test_verify_signature_rejects_wrong_secret() -> None:
    body = b'{"hello": "world"}'
    ts = str(int(time.time()))
    sig = _sign(body, ts)
    assert _verify_signature(body, ts, sig, "different-secret") is False


def test_verify_signature_rejects_missing_prefix() -> None:
    body = b'{"hello": "world"}'
    ts = str(int(time.time()))
    raw_hex = hmac.new(
        _SECRET.encode(), msg=f"{ts}.".encode() + body, digestmod=hashlib.sha256
    ).hexdigest()
    assert _verify_signature(body, ts, raw_hex, _SECRET) is False  # no sha256= prefix


def test_timestamp_fresh_within_window() -> None:
    now = 1_000_000.0
    assert _timestamp_fresh(str(now), now=now) is True
    assert _timestamp_fresh(str(now - 10), now=now) is True
    assert _timestamp_fresh(str(now + 10), now=now) is True


def test_timestamp_fresh_outside_window() -> None:
    now = 1_000_000.0
    assert _timestamp_fresh(str(now - _MAX_TIMESTAMP_SKEW_SEC - 1), now=now) is False
    assert _timestamp_fresh(str(now + _MAX_TIMESTAMP_SKEW_SEC + 1), now=now) is False


def test_timestamp_fresh_rejects_non_numeric() -> None:
    assert _timestamp_fresh("not-a-number") is False
    assert _timestamp_fresh("") is False


# ---------------------------------------------------------------------------
# Nonce replay cache
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_nonce_cache_accepts_new_then_rejects_replay() -> None:
    _reset_nonce_cache()
    sig = "sha256=" + "a" * 64
    first = await _check_and_record_nonce(sig, now=1_000_000.0)
    second = await _check_and_record_nonce(sig, now=1_000_000.5)
    assert first is True
    assert second is False


@pytest.mark.asyncio
async def test_nonce_cache_prunes_stale_entries() -> None:
    _reset_nonce_cache()
    old_sig = "sha256=" + "b" * 64
    new_sig = "sha256=" + "c" * 64
    # Record old at t0
    assert await _check_and_record_nonce(old_sig, now=0.0) is True
    # Much later — old_sig should have aged out, allowing re-use.
    future = _MAX_TIMESTAMP_SKEW_SEC * 2 + 10.0
    assert await _check_and_record_nonce(new_sig, now=future) is True
    # The prune pass should have dropped old_sig — verify by re-adding.
    assert await _check_and_record_nonce(old_sig, now=future) is True


# ---------------------------------------------------------------------------
# Endpoint — disabled by default
# ---------------------------------------------------------------------------
def test_endpoint_503_when_secret_unset(client: TestClient) -> None:
    get_settings.cache_clear()
    # Ensure default Settings.EXTERNAL_EVENT_SECRET is empty string
    assert get_settings().EXTERNAL_EVENT_SECRET == ""
    body = {
        "session_id": "s1",
        "dept_id": "research",
        "display_name": "🔬 リサーチ事業部 リョウ",
        "event_kind": "ASK_COMPLETED",
    }
    resp = client.post(
        "/api/v1/external_event",
        json=body,
        headers={
            "X-Bridge-Signature": "sha256=deadbeef",
            "X-Bridge-Timestamp": str(int(time.time())),
        },
    )
    assert resp.status_code == 503, resp.text


# ---------------------------------------------------------------------------
# Endpoint — auth failure modes
# ---------------------------------------------------------------------------
def test_endpoint_401_missing_headers(
    client: TestClient, secret_enabled: None
) -> None:
    body = {
        "session_id": "s1",
        "dept_id": "research",
        "display_name": "🔬 リサーチ事業部 リョウ",
        "event_kind": "ASK_COMPLETED",
    }
    resp = client.post("/api/v1/external_event", json=body)
    assert resp.status_code == 401


def test_endpoint_401_stale_timestamp(
    client: TestClient, secret_enabled: None
) -> None:
    stale_ts = str(int(time.time()) - _MAX_TIMESTAMP_SKEW_SEC - 60)
    body = {
        "session_id": "s1",
        "dept_id": "research",
        "display_name": "🔬 リサーチ事業部 リョウ",
        "event_kind": "ASK_COMPLETED",
    }
    raw = json.dumps(body).encode()
    resp = client.post(
        "/api/v1/external_event",
        content=raw,
        headers={
            "Content-Type": "application/json",
            "X-Bridge-Signature": _sign(raw, stale_ts),
            "X-Bridge-Timestamp": stale_ts,
        },
    )
    assert resp.status_code == 401


def test_endpoint_401_bad_signature(
    client: TestClient, secret_enabled: None
) -> None:
    ts = str(int(time.time()))
    body = {
        "session_id": "s1",
        "dept_id": "research",
        "display_name": "🔬 リサーチ事業部 リョウ",
        "event_kind": "ASK_COMPLETED",
    }
    raw = json.dumps(body).encode()
    resp = client.post(
        "/api/v1/external_event",
        content=raw,
        headers={
            "Content-Type": "application/json",
            "X-Bridge-Signature": "sha256=" + "0" * 64,  # wrong hex of correct length
            "X-Bridge-Timestamp": ts,
        },
    )
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Endpoint — validation failure
# ---------------------------------------------------------------------------
def test_endpoint_422_invalid_payload(
    client: TestClient, secret_enabled: None
) -> None:
    ts = str(int(time.time()))
    # Missing required fields + invalid agent_color pattern.
    body = {"session_id": "s1", "agent_color": "not-a-hex"}
    raw = json.dumps(body).encode()
    resp = client.post(
        "/api/v1/external_event",
        content=raw,
        headers={
            "Content-Type": "application/json",
            "X-Bridge-Signature": _sign(raw, ts),
            "X-Bridge-Timestamp": ts,
        },
    )
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Endpoint — happy path
# ---------------------------------------------------------------------------
def test_endpoint_202_happy_path(
    client: TestClient, secret_enabled: None
) -> None:
    ts = str(int(time.time()))
    body = {
        "session_id": "canvas-abc",
        "dept_id": "research",
        "display_name": "🔬 リサーチ事業部 リョウ",
        "agent_color": "#3498DB",
        "event_kind": "ASK_COMPLETED",
        "message": "今日のリサーチは3本仕上がってる。",
        "model": "haiku",
        "duration_s": 8.4,
        "status": "ok",
        "metadata": {"source_command": "/ask"},
    }
    raw = json.dumps(body).encode()
    resp = client.post(
        "/api/v1/external_event",
        content=raw,
        headers={
            "Content-Type": "application/json",
            "X-Bridge-Signature": _sign(raw, ts),
            "X-Bridge-Timestamp": ts,
        },
    )
    assert resp.status_code == 202, resp.text
    payload = resp.json()
    assert payload["status"] == "accepted"
    assert payload["session_id"] == "canvas-abc"
    assert payload["dept_id"] == "research"
    assert payload["event_kind"] == "ASK_COMPLETED"


def test_endpoint_409_on_replay(client: TestClient, secret_enabled: None) -> None:
    """The exact same signed request twice within the window → 409."""
    ts = str(int(time.time()))
    body = {
        "session_id": "canvas-abc",
        "dept_id": "research",
        "display_name": "🔬 リサーチ事業部 リョウ",
        "event_kind": "ASK_COMPLETED",
    }
    raw = json.dumps(body).encode()
    headers = {
        "Content-Type": "application/json",
        "X-Bridge-Signature": _sign(raw, ts),
        "X-Bridge-Timestamp": ts,
    }
    first = client.post("/api/v1/external_event", content=raw, headers=headers)
    second = client.post("/api/v1/external_event", content=raw, headers=headers)
    assert first.status_code == 202
    assert second.status_code == 409, second.text
