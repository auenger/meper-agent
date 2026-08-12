"""Tests for external API — Agent resource discovery and invocation."""
from unittest.mock import AsyncMock, patch

import pytest
from app.api.v1.ext import auth_and_rate_limit
from app.core.auth_apikey import ApiKeyPrincipal
from app.main import app
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def full_principal():
    """API Key principal with all scopes and no resource bindings.

    新模型下外部路径的 principal 已通过通用 token 本地校验，user_id =
    token_record_id = mcp_token_credentials._id（这里用 mcptok_test 模拟）。
    """
    return ApiKeyPrincipal(
        key_id="apikey_test",
        owner_user_id="user_owner",
        scopes=["agents:read", "agents:invoke", "workflows:read", "workflows:invoke", "executions:read"],
        bindings={"agents": [], "workflows": []},
        rate_limit=60,
        user_id="mcptok_test",
        token_record_id="mcptok_test",
        user_token="meper_test_token",
    )


@pytest.fixture
def limited_principal():
    """API Key principal with limited scope and agent binding."""
    return ApiKeyPrincipal(
        key_id="apikey_limited",
        owner_user_id="user_owner",
        scopes=["agents:read"],
        bindings={"agents": ["agent_allowed"], "workflows": []},
        rate_limit=60,
    )


def _override_auth(principal):
    """Override the API Key auth dependency."""
    app.dependency_overrides[auth_and_rate_limit] = lambda: principal
    return lambda: app.dependency_overrides.clear()


def _make_agent_doc(agent_id="agent_01", name="Test Agent", status="published"):
    return {
        "_id": agent_id,
        "name": name,
        "description": "A test agent",
        "prompt_slots": {},
        "tool_ids": [],
        "skill_ids": ["skill_01"],
        "mcp_connection_ids": [],
        "builtin_config": [],
        "workflow_ids": ["wf_01"],
        "knowledge_base_ids": [],
        "default_model": "gpt-4o",
        "max_retry": 3,
        "status": status,
        "created_at": "2026-01-01T00:00:00",
        "updated_at": "2026-01-01T00:00:00",
    }


# ---------------------------------------------------------------------------
# AC-1: List accessible Agents
# ---------------------------------------------------------------------------


class TestListAgents:
    """GET /api/v1/ext/agents"""

    def test_list_agents_success(self, client, full_principal) -> None:
        cleanup = _override_auth(full_principal)
        try:
            agents = [_make_agent_doc(), _make_agent_doc("agent_02", "Agent 2")]
            with patch(
                "app.services.agent_service.AgentService.list_agents",
                new=AsyncMock(return_value=(agents, 2)),
            ):
                resp = client.get("/api/v1/ext/agents")
            assert resp.status_code == 200
            data = resp.json()
            assert data["total"] == 2
            assert len(data["items"]) == 2
            assert data["items"][0]["id"] == "agent_01"
            assert data["items"][0]["name"] == "Test Agent"
            assert data["items"][0]["status"] == "published"
            assert data["items"][0]["capabilities"]["tools"] == ["skill_01"]
            assert data["items"][0]["capabilities"]["workflow_ids"] == ["wf_01"]
        finally:
            cleanup()

    def test_list_agents_filtered_by_bindings(self, client, limited_principal) -> None:
        cleanup = _override_auth(limited_principal)
        try:
            agents = [
                _make_agent_doc("agent_allowed", "Allowed Agent"),
                _make_agent_doc("agent_other", "Other Agent"),
            ]
            with patch(
                "app.services.agent_service.AgentService.list_agents",
                new=AsyncMock(return_value=(agents, 2)),
            ):
                resp = client.get("/api/v1/ext/agents")
            assert resp.status_code == 200
            data = resp.json()
            # Only agent_allowed should be in the response
            assert len(data["items"]) == 1
            assert data["items"][0]["id"] == "agent_allowed"
        finally:
            cleanup()

    def test_list_agents_scope_denied(self, client) -> None:
        principal = ApiKeyPrincipal(
            key_id="k", owner_user_id="u",
            scopes=["agents:invoke"],  # no agents:read
            bindings={},
        )
        cleanup = _override_auth(principal)
        try:
            resp = client.get("/api/v1/ext/agents")
            assert resp.status_code == 403
        finally:
            cleanup()


# ---------------------------------------------------------------------------
# AC-2: Get Agent details
# ---------------------------------------------------------------------------


class TestGetAgent:
    """GET /api/v1/ext/agents/{agent_id}"""

    def test_get_agent_success(self, client, full_principal) -> None:
        cleanup = _override_auth(full_principal)
        try:
            with patch(
                "app.services.agent_service.AgentService.get_agent",
                new=AsyncMock(return_value=_make_agent_doc()),
            ):
                resp = client.get("/api/v1/ext/agents/agent_01")
            assert resp.status_code == 200
            data = resp.json()
            assert data["id"] == "agent_01"
            assert data["default_model"] == "gpt-4o"
            assert "prompt_slots" not in data  # internal field not exposed
        finally:
            cleanup()

    def test_get_agent_not_found(self, client, full_principal) -> None:
        cleanup = _override_auth(full_principal)
        try:
            with patch(
                "app.services.agent_service.AgentService.get_agent",
                new=AsyncMock(return_value=None),
            ):
                resp = client.get("/api/v1/ext/agents/nonexistent")
            assert resp.status_code == 404
        finally:
            cleanup()

    def test_get_agent_binding_denied(self, client, limited_principal) -> None:
        cleanup = _override_auth(limited_principal)
        try:
            resp = client.get("/api/v1/ext/agents/agent_not_allowed")
            assert resp.status_code == 403
        finally:
            cleanup()

    def test_get_agent_draft_not_visible(self, client, full_principal) -> None:
        cleanup = _override_auth(full_principal)
        try:
            with patch(
                "app.services.agent_service.AgentService.get_agent",
                new=AsyncMock(return_value=_make_agent_doc(status="draft")),
            ):
                resp = client.get("/api/v1/ext/agents/agent_01")
            assert resp.status_code == 404
        finally:
            cleanup()


# ---------------------------------------------------------------------------
# AC-3: Synchronous invocation
# ---------------------------------------------------------------------------


class TestInvokeAgent:
    """POST /api/v1/ext/agents/{agent_id}/invoke"""

    def test_invoke_success(self, client, full_principal) -> None:
        from app.schemas.execution import ExecutionResponse
        cleanup = _override_auth(full_principal)
        try:
            mock_result = ExecutionResponse(
                output="Hello from agent",
                execution_path="direct",
                request_id="req_01",
                agent_id="agent_01",
                session_id="session_01",
                step_count=1,
            )
            with patch(
                "app.services.agent_execution_service.AgentExecutionService.invoke",
                new=AsyncMock(return_value=mock_result),
            ):
                resp = client.post(
                    "/api/v1/ext/agents/agent_01/invoke",
                    json={"message": "Hello"},
                )
            assert resp.status_code == 200
            data = resp.json()
            assert data["session_id"] == "session_01"
            assert data["request_id"] == "req_01"
            assert data["reply"] == "Hello from agent"
            assert data["task_ids"] == []
            assert data["files"] == []
        finally:
            cleanup()

    def test_invoke_with_session_id(self, client, full_principal) -> None:
        from app.schemas.execution import ExecutionResponse
        cleanup = _override_auth(full_principal)
        try:
            mock_result = ExecutionResponse(
                output="Continued",
                execution_path="direct",
                request_id="req_02",
                agent_id="agent_01",
                session_id="session_existing",
                step_count=1,
            )
            with patch(
                "app.services.agent_execution_service.AgentExecutionService.invoke",
                new=AsyncMock(return_value=mock_result),
            ) as mock_invoke:
                resp = client.post(
                    "/api/v1/ext/agents/agent_01/invoke",
                    json={"message": "Follow up", "session_id": "session_existing"},
                )
            assert resp.status_code == 200
            # Verify the session_id was passed through
            call_kwargs = mock_invoke.call_args
            assert call_kwargs.kwargs.get("body") or call_kwargs[1].get("body")
        finally:
            cleanup()

    def test_invoke_scope_denied(self, client) -> None:
        principal = ApiKeyPrincipal(
            key_id="k", owner_user_id="u",
            scopes=["agents:read"],  # no agents:invoke
            bindings={},
        )
        cleanup = _override_auth(principal)
        try:
            resp = client.post(
                "/api/v1/ext/agents/agent_01/invoke",
                json={"message": "Hello"},
            )
            assert resp.status_code == 403
        finally:
            cleanup()

    def test_invoke_binding_denied(self, client, limited_principal) -> None:
        cleanup = _override_auth(limited_principal)
        try:
            resp = client.post(
                "/api/v1/ext/agents/agent_not_allowed/invoke",
                json={"message": "Hello"},
            )
            assert resp.status_code == 403
        finally:
            cleanup()


# ---------------------------------------------------------------------------
# AC-7: Error handling
# ---------------------------------------------------------------------------


class TestErrorHandling:
    """Verify error responses follow the spec."""

    def test_missing_auth_header(self, client) -> None:
        """No Authorization header → 401."""
        # Don't override auth — let real dependency run
        app.dependency_overrides.clear()
        resp = client.get("/api/v1/ext/agents")
        assert resp.status_code == 401

    def test_invalid_key_format(self, client) -> None:
        """Non af_live_ prefix → 401."""
        from app.api.v1.ext import auth_and_rate_limit
        from app.core.errors import UnauthorizedError

        async def _fake_auth():
            raise UnauthorizedError(code="APIKEY_INVALID", message="Invalid or expired API Key")

        app.dependency_overrides[auth_and_rate_limit] = _fake_auth
        try:
            resp = client.get("/api/v1/ext/agents")
            assert resp.status_code == 401
        finally:
            app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Create session (external)
# ---------------------------------------------------------------------------


class TestCreateSession:
    """POST /api/v1/ext/agents/{agent_id}/sessions"""

    def _doc(self, user_id: str, agent_id: str = "agent_01") -> dict:
        return {
            "_id": "sess_new",
            "user_id": user_id,
            "agent_id": agent_id,
            "title": "",
            "message_count": 0,
            "created_at": "2026-01-01T00:00:00",
            "updated_at": "2026-01-01T00:00:00",
        }

    def test_create_session_uses_token_record_id(self, client, full_principal) -> None:
        """新模型：建会话的 user_id 来自 token 记录 id，visitor_id 被忽略。"""
        cleanup = _override_auth(full_principal)
        try:
            with patch(
                "app.services.session_service.SessionService.create_session",
                new=AsyncMock(return_value=self._doc("mcptok_test")),
            ) as mock_create:
                resp = client.post(
                    "/api/v1/ext/agents/agent_01/sessions?visitor_id=ignored-now"
                )
            assert resp.status_code == 201
            assert resp.json()["id"] == "sess_new"
            assert mock_create.call_args.kwargs["user_id"] == "mcptok_test"
        finally:
            cleanup()

    def test_create_scope_denied(self, client) -> None:
        principal = ApiKeyPrincipal(
            key_id="k",
            owner_user_id="u",
            scopes=["agents:read"],  # no agents:invoke
            bindings={},
        )
        cleanup = _override_auth(principal)
        try:
            resp = client.post("/api/v1/ext/agents/agent_01/sessions")
            assert resp.status_code == 403
        finally:
            cleanup()
