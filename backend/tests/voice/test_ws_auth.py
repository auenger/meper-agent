"""Voice WS auth: ticket lifecycle + endpoint dual-mode branches."""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from app.core.auth_apikey import ApiKeyPrincipal
from app.services import voice_ticket_service as vts
from fastapi.testclient import TestClient

from tests.voice.helpers import make_principal


class FakeRedis:
    """Minimal async Redis stub: set(nx, ex) + getdel over a plain dict."""

    def __init__(self) -> None:
        self.store: dict[str, str] = {}

    async def set(self, key: str, value: str, ex: int | None = None, nx: bool = False) -> bool:
        if nx and key in self.store:
            return False
        self.store[key] = value
        return True

    async def getdel(self, key: str) -> str | None:
        return self.store.pop(key, None)


# ── ticket service ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_ticket_roundtrip_restores_principal() -> None:
    redis = FakeRedis()
    principal = make_principal(user_token="tok_x", introspect_url="https://idp/x")

    with patch.object(vts, "get_redis_client", AsyncMock(return_value=redis)):
        ticket = await vts.issue_ticket(principal)
        restored = await vts.consume_ticket(ticket)

    assert isinstance(restored, ApiKeyPrincipal)
    assert restored.key_id == principal.key_id
    assert restored.user_id == principal.user_id
    assert restored.scopes == principal.scopes
    assert restored.bindings == principal.bindings
    assert restored.user_token == principal.user_token
    assert restored.introspect_url == principal.introspect_url


@pytest.mark.asyncio
async def test_ticket_is_single_use() -> None:
    redis = FakeRedis()
    with patch.object(vts, "get_redis_client", AsyncMock(return_value=redis)):
        ticket = await vts.issue_ticket(make_principal())
        first = await vts.consume_ticket(ticket)
        second = await vts.consume_ticket(ticket)

    assert first is not None
    assert second is None  # GETDEL — replay rejected


@pytest.mark.asyncio
async def test_unknown_or_empty_ticket_rejected() -> None:
    redis = FakeRedis()
    with patch.object(vts, "get_redis_client", AsyncMock(return_value=redis)):
        assert await vts.consume_ticket("bogus") is None
        assert await vts.consume_ticket("") is None


@pytest.mark.asyncio
async def test_malformed_ticket_payload_rejected() -> None:
    redis = FakeRedis()
    redis.store[f"{vts._TICKET_PREFIX}:broken"] = "not-json{"
    with patch.object(vts, "get_redis_client", AsyncMock(return_value=redis)):
        assert await vts.consume_ticket("broken") is None


# ── WS endpoint auth branches ──────────────────────────────────────────────


def _ws_connect_close(client: TestClient, query: str) -> dict:
    """Connect and return the first WS message — the server's close frame
    when auth rejects (accept → close(4401)) before any payload."""
    with client.websocket_connect(f"/api/v1/voice/realtime?{query}") as ws:
        return ws.receive()


def test_ws_without_credentials_gets_4401():
    from app.main import app

    client = TestClient(app)
    msg = _ws_connect_close(client, "")
    assert msg["type"] == "websocket.close"
    assert msg["code"] == 4401


def test_ws_with_invalid_ticket_gets_4401():
    from app.main import app

    client = TestClient(app)
    with patch(
        "app.voice.api.consume_ticket",
        AsyncMock(return_value=None),
    ):
        msg = _ws_connect_close(client, "ticket=whatever")
    assert msg["type"] == "websocket.close"
    assert msg["code"] == 4401


def test_ws_prefers_jwt_and_does_not_fallback_to_ticket():
    """token present → JWT-only; a failing JWT must NOT silently try the ticket."""
    from app.main import app

    client = TestClient(app)
    consume = AsyncMock(
        return_value=make_principal()
    )  # would succeed if (wrongly) consulted
    with (
        patch("app.api.v1.ws.verify_ws_token", return_value=None),
        patch("app.voice.api.consume_ticket", consume),
    ):
        msg = _ws_connect_close(client, "token=bad-jwt&ticket=valid-looking")

    assert msg["type"] == "websocket.close"
    assert msg["code"] == 4401
    consume.assert_not_awaited()


def test_ws_with_valid_ticket_passes_auth_then_fails_on_missing_config():
    """Valid ticket clears auth; the runtime-config error frame proves we got
    past the 4401 handshake rejection (voice config is unset in tests)."""
    from app.main import app

    client = TestClient(app)
    with patch(
        "app.voice.api.consume_ticket",
        AsyncMock(return_value=make_principal()),
    ):
        msg = _ws_connect_close(client, "ticket=t")

    assert msg["type"] == "websocket.send"  # the error JSON frame, not a 4401


