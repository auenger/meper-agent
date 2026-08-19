"""External-channel gates on VoiceSession: agent access / session ownership /
rate limit / usage stats / audit context. JWT (no-principal) path regression."""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest
from app.voice import protocol as P  # noqa: N812

from tests.voice.helpers import FakeASR, FakeWebSocket, make_principal, make_session

# ── agent-level gate (B6) ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_principal_rejects_missing_invoke_scope() -> None:
    ws, session, created_asr = _collector_session(principal=make_principal(scopes=["agents:read"]))

    with patch(
        "app.services.agent_service.AgentService.get_agent",
        new=AsyncMock(return_value={"_id": "agent-1", "voice_enabled": True}),
    ):
        await session.on_control(
            json.dumps({"type": P.CLIENT_VOICE_START, "agent_id": "agent-1"})
        )

    assert created_asr == []
    assert {
        "type": P.SERVER_ERROR,
        "content": "Agent 不存在或无权访问",
    } in ws.messages
    await session.close()


@pytest.mark.asyncio
async def test_principal_rejects_agent_outside_bindings() -> None:
    ws, session, created_asr = _collector_session(principal=make_principal(agent_bindings=["agent_allowed"]))

    with patch(
        "app.services.agent_service.AgentService.get_agent",
        new=AsyncMock(return_value={"_id": "agent-1", "voice_enabled": True}),
    ):
        await session.on_control(
            json.dumps({"type": P.CLIENT_VOICE_START, "agent_id": "agent-1"})
        )

    assert created_asr == []
    assert {
        "type": P.SERVER_ERROR,
        "content": "Agent 不存在或无权访问",
    } in ws.messages
    await session.close()


@pytest.mark.asyncio
async def test_principal_rejects_unpublished_agent() -> None:
    ws, session, created_asr = _collector_session(principal=make_principal())

    with patch(
        "app.services.agent_service.AgentService.get_agent",
        new=AsyncMock(
            return_value={"_id": "agent-1", "voice_enabled": True, "status": "draft"}
        ),
    ):
        await session.on_control(
            json.dumps({"type": P.CLIENT_VOICE_START, "agent_id": "agent-1"})
        )

    assert created_asr == []
    assert {
        "type": P.SERVER_ERROR,
        "content": "Agent 不存在或无权访问",
    } in ws.messages
    await session.close()


@pytest.mark.asyncio
async def test_jwt_path_skips_ext_agent_checks() -> None:
    """No principal → status/scope/bindings checks don't apply (regression)."""
    ws, session, created_asr = _collector_session()  # JWT mode: no principal

    with patch(
        "app.services.agent_service.AgentService.get_agent",
        new=AsyncMock(
            return_value={"_id": "agent-1", "voice_enabled": True, "status": "draft"}
        ),
    ):
        await session.on_control(
            json.dumps({"type": P.CLIENT_VOICE_START, "agent_id": "agent-1"})
        )

    assert len(created_asr) == 1
    assert created_asr[0].opened is True
    await session.close()


# ── session ownership (B7) ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_principal_cannot_reuse_foreign_session() -> None:
    """voice.start with someone else's session_id must be rejected before
    _resolve_session() — which add_message()s into the target session."""
    ws = FakeWebSocket()
    session = make_session(ws, principal=make_principal(), user_id="ext_user_1")
    session.agent_id = "agent-1"
    session.session_id = "session_foreign"

    resolve_session = AsyncMock()

    async def fake_resolve(agent_id, body, user_id):
        resolve_session(agent_id, body, user_id)

    with (
        patch(
            "app.services.agent_service.AgentService.get_agent",
            new=AsyncMock(
                return_value={"_id": "agent-1", "voice_enabled": True, "status": "published"}
            ),
        ),
        patch(
            "app.services.agent_execution_service._resolve_session",
            new=fake_resolve,
        ),
        patch(
            "app.services.session_service.SessionService.get_session",
            new=AsyncMock(return_value={"_id": "session_foreign", "user_id": "user_victim"}),
        ),
    ):
        from app.voice.turn import TurnContext

        await session._exec_brain(TurnContext(transcript="hi"))

    assert resolve_session.await_count == 0
    assert {
        "type": P.SERVER_ERROR,
        "content": "会话不存在或无权访问",
    } in ws.messages
    await session.close()


@pytest.mark.asyncio
async def test_principal_own_session_passes_ownership() -> None:
    ws = FakeWebSocket()
    session = make_session(ws, principal=make_principal(), user_id="ext_user_1")
    session.agent_id = "agent-1"
    session.session_id = "session_own"

    harness_stream = AsyncMock(return_value={"usage": {}})

    with (
        patch(
            "app.services.agent_service.AgentService.get_agent",
            new=AsyncMock(
                return_value={"_id": "agent-1", "voice_enabled": True, "status": "published"}
            ),
        ),
        patch(
            "app.services.session_service.SessionService.get_session",
            new=AsyncMock(return_value={"_id": "session_own", "user_id": "ext_user_1"}),
        ),
        patch(
            "app.services.agent_execution_service._resolve_session",
            new=AsyncMock(return_value="session_own"),
        ),
        patch(
            "app.services.agent_execution_service._build_initial_state",
            new=AsyncMock(return_value={}),
        ),
        patch(
            "app.services.agent_execution_service._assemble_messages",
            new=AsyncMock(return_value=[]),
        ),
        patch(
            "app.services.agent_execution_service._build_system_prompt_checked",
            new=AsyncMock(return_value="sys"),
        ),
        patch(
            "app.services.agent_execution_service._persist_agent_message",
            new=AsyncMock(),
        ),
        patch(
            "app.services.agent_execution_service._record_execution_log",
            new=AsyncMock(),
        ),
        patch(
            "app.engine.harness_integration.stream",
            new=harness_stream,
        ),
    ):
        from app.voice.turn import TurnContext

        await session._exec_brain(TurnContext(transcript="hi"))

    assert harness_stream.await_count == 1
    await session.close()


# ── per-turn gate (B5) ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_rate_limited_turn_is_skipped_without_disconnect() -> None:
    ws = FakeWebSocket()
    session = make_session(ws, principal=make_principal())
    session.agent_id = "agent-1"

    brain = AsyncMock()
    with (
        patch("app.voice.session.VoiceSession._exec_brain", new=brain),
        patch(
            "app.core.rate_limiter.check_rate_limit",
            new=AsyncMock(return_value=(False, 0, 0)),
        ),
    ):
        await session._run_turn("hi")

    assert brain.await_count == 0
    assert {
        "type": P.SERVER_ERROR,
        "code": "RATE_LIMIT_EXCEEDED",
        "content": "请求频率超限，请稍后重试",
    } in ws.messages
    assert ws.closed == []  # connection kept
    await session.close()


@pytest.mark.asyncio
async def test_revoked_user_token_closes_connection() -> None:
    ws = FakeWebSocket()
    principal = make_principal(user_token="tok", introspect_url="https://idp/x")
    session = make_session(ws, principal=principal)
    session.agent_id = "agent-1"

    brain = AsyncMock()
    with (
        patch("app.voice.session.VoiceSession._exec_brain", new=brain),
        patch(
            "app.core.rate_limiter.check_rate_limit",
            new=AsyncMock(return_value=(True, 59, 0)),
        ),
        patch(
            "app.services.user_auth_service.UserAuthService.introspect",
            new=AsyncMock(side_effect=Exception("idp down")),
        ),
    ):
        await session._run_turn("hi")

    assert brain.await_count == 0
    assert ws.closed and ws.closed[0]["code"] == 4401
    await session.close()


@pytest.mark.asyncio
async def test_successful_turn_sets_audit_context_and_records_usage() -> None:
    ws = FakeWebSocket()
    session = make_session(ws, principal=make_principal())
    session.agent_id = "agent-1"

    record_request = AsyncMock()
    with (
        patch("app.voice.session.VoiceSession._exec_brain", new=AsyncMock()),
        patch(
            "app.core.rate_limiter.check_rate_limit",
            new=AsyncMock(return_value=(True, 59, 0)),
        ),
        patch(
            "app.services.api_key_stats_service.record_request", new=record_request
        ),
    ):
        await session._run_turn("hi")

    from app.services.ext_api_call_log_service import (
        get_ext_call_context,
        set_ext_call_context,
    )

    ctx = get_ext_call_context()
    assert ctx is not None
    assert ctx.api_key_id == "apikey_test"
    assert ctx.endpoint == "voice:realtime"
    record_request.assert_awaited_once_with("apikey_test", "voice:realtime", 200)
    set_ext_call_context(None)  # don't leak the ContextVar into other tests
    await session.close()


# ── user_token passthrough (B8) ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_harness_receives_principal_user_token() -> None:
    ws = FakeWebSocket()
    principal = make_principal(user_token="tok_x")
    session = make_session(ws, principal=principal, user_id="ext_user_1")
    session.agent_id = "agent-1"
    session.session_id = "session_own"

    harness_stream = AsyncMock(return_value={"usage": {}})

    with (
        patch(
            "app.services.agent_service.AgentService.get_agent",
            new=AsyncMock(
                return_value={"_id": "agent-1", "voice_enabled": True, "status": "published"}
            ),
        ),
        patch(
            "app.services.session_service.SessionService.get_session",
            new=AsyncMock(return_value={"_id": "session_own", "user_id": "ext_user_1"}),
        ),
        patch(
            "app.services.agent_execution_service._resolve_session",
            new=AsyncMock(return_value="session_own"),
        ),
        patch(
            "app.services.agent_execution_service._build_initial_state",
            new=AsyncMock(return_value={}),
        ),
        patch(
            "app.services.agent_execution_service._assemble_messages",
            new=AsyncMock(return_value=[]),
        ),
        patch(
            "app.services.agent_execution_service._build_system_prompt_checked",
            new=AsyncMock(return_value="sys"),
        ),
        patch(
            "app.services.agent_execution_service._persist_agent_message",
            new=AsyncMock(),
        ),
        patch(
            "app.services.agent_execution_service._record_execution_log",
            new=AsyncMock(),
        ),
        patch("app.engine.harness_integration.stream", new=harness_stream),
    ):
        from app.voice.turn import TurnContext

        await session._exec_brain(TurnContext(transcript="hi"))

    assert harness_stream.await_count == 1
    assert harness_stream.await_args.kwargs.get("user_token") == "tok_x"
    await session.close()


# ── helpers ────────────────────────────────────────────────────────────────


def _collector_session(*, principal=None):
    """(ws, session, created_asr) — session whose ASR factory records clients."""
    ws = FakeWebSocket()
    created: list[FakeASR] = []

    def factory() -> FakeASR:
        client = FakeASR()
        created.append(client)
        return client

    session = make_session(ws, principal=principal)
    session._asr_factory = factory  # type: ignore[assignment]
    return ws, session, created
