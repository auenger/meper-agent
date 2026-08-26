"""API tests for /api/v1/agents endpoints (mock-based).

Uses ``unittest.mock`` to mock the AgentService layer so tests
run without a real MongoDB connection.

NOTE: Mock-based tests cannot detect interface-contract mismatches
(e.g. wrong keyword argument names passed from API to Service).
The contract tests at the bottom of this file cover that gap.
"""
import inspect
from unittest.mock import AsyncMock, patch

import pytest
from app.core.errors import ConflictError, NotFoundError
from app.core.security import get_current_user
from app.main import app
from app.schemas.user import UserResponse, UserStatus
from app.services.agent_service import AgentService
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def auth_admin():
    """Override get_current_user for admin-level authentication."""
    user = UserResponse(
        id="user_01HTEST",
        username="admin",
        email="admin@example.com",
        role="admin",
        status=UserStatus.ACTIVE,
        created_at="2026-01-01T00:00:00",
        updated_at="2026-01-01T00:00:00",
        permissions=[],
    )
    app.dependency_overrides[get_current_user] = lambda: user
    yield
    app.dependency_overrides.clear()


@pytest.fixture
def auth_viewer():
    """Override get_current_user for viewer-level authentication."""
    user = UserResponse(
        id="user_02HTEST",
        username="viewer",
        email="viewer@example.com",
        role="viewer",
        status=UserStatus.ACTIVE,
        created_at="2026-01-01T00:00:00",
        updated_at="2026-01-01T00:00:00",
        permissions=[],
    )
    app.dependency_overrides[get_current_user] = lambda: user
    yield
    app.dependency_overrides.clear()


@pytest.fixture
def auth_role():
    """Override get_current_user with an arbitrary role (factory).

    用于自定义角色/其他系统角色的鉴权用例；权限集由 conftest 的
    ``_perm_overrides``（默认矩阵 or 用例注入）解析。
    """

    def _make(role: str, user_id: str = "user_03HTEST") -> None:
        user = UserResponse(
            id=user_id,
            username=role,
            email=f"{role}@example.com",
            role=role,
            status=UserStatus.ACTIVE,
            created_at="2026-01-01T00:00:00",
            updated_at="2026-01-01T00:00:00",
            permissions=[],
        )
        app.dependency_overrides[get_current_user] = lambda: user

    yield _make
    app.dependency_overrides.clear()


def _fake_doc(agent_id: str = "agent_01HTEST", name: str = "Test Agent") -> dict:
    return {
        "_id": agent_id,
        "name": name,
        "description": "A test agent",
        "prompt_slots": {},
        "skill_ids": [],
        "mcp_connection_ids": [],
        "builtin_config": [],
        "workflow_ids": [],
        "knowledge_base_ids": [],
        "default_model": "gpt-4",
        "max_retry": 3,
        "status": "draft",
        "created_at": "2026-01-01T00:00:00",
        "updated_at": "2026-01-01T00:00:00",
    }


class TestListAgents:
    """GET /api/v1/agents"""

    def test_list_agents_200(self, client, auth_admin) -> None:
        with patch(
            "app.api.v1.agents.AgentService.list_agents",
            new=AsyncMock(return_value=([_fake_doc()], 1)),
        ):
            resp = client.get("/api/v1/agents")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 1
        assert len(data["items"]) == 1
        assert data["items"][0]["name"] == "Test Agent"

    def test_list_agents_empty(self, client, auth_admin) -> None:
        with patch(
            "app.api.v1.agents.AgentService.list_agents",
            new=AsyncMock(return_value=([], 0)),
        ):
            resp = client.get("/api/v1/agents")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 0
        assert data["items"] == []

    def test_list_agents_with_filters(self, client, auth_admin) -> None:
        with patch(
            "app.api.v1.agents.AgentService.list_agents",
            new=AsyncMock(return_value=([_fake_doc(name="Filtered")], 1)),
        ) as mock_list:
            resp = client.get("/api/v1/agents?name=Filtered&status=draft")
        assert resp.status_code == 200
        assert resp.json()["items"][0]["name"] == "Filtered"
        _, kwargs = mock_list.call_args
        assert kwargs["name"] == "Filtered"
        assert kwargs["status"] == "draft"

    def test_list_agents_pagination(self, client, auth_admin) -> None:
        docs = [_fake_doc(agent_id=f"agent_{i:02d}", name=f"Agent {i}") for i in range(5)]
        with patch(
            "app.api.v1.agents.AgentService.list_agents",
            new=AsyncMock(return_value=(docs[:2], 5)),
        ):
            resp = client.get("/api/v1/agents?page=1&page_size=2")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["items"]) == 2
        assert data["total"] == 5
        assert data["page"] == 1
        assert data["page_size"] == 2

    def test_list_agents_401_unauthorized(self, client) -> None:
        resp = client.get("/api/v1/agents")
        assert resp.status_code == 401

    def test_list_agents_viewer_allowed(self, client, auth_viewer) -> None:
        with patch(
            "app.api.v1.agents.AgentService.list_agents",
            new=AsyncMock(return_value=([_fake_doc()], 1)),
        ):
            resp = client.get("/api/v1/agents")
        assert resp.status_code == 200


class TestCreateAgent:
    """POST /api/v1/agents"""

    def test_create_agent_201(self, client, auth_admin) -> None:
        with patch(
            "app.api.v1.agents.AgentService.create_agent",
            new=AsyncMock(return_value=_fake_doc()),
        ):
            resp = client.post(
                "/api/v1/agents",
                json={"name": "New Agent", "description": "A new agent"},
            )
        assert resp.status_code == 201
        data = resp.json()
        assert data["name"] == "Test Agent"
        assert data["status"] == "draft"

    def test_create_agent_409_conflict(self, client, auth_admin) -> None:
        with patch(
            "app.api.v1.agents.AgentService.create_agent",
            new=AsyncMock(
                side_effect=ConflictError(
                    code="AGENT_NAME_CONFLICT",
                    message="Agent 名称 'New Agent' 已被占用",
                )
            ),
        ):
            resp = client.post(
                "/api/v1/agents",
                json={"name": "New Agent"},
            )
        assert resp.status_code == 409
        assert resp.json()["error"]["code"] == "AGENT_NAME_CONFLICT"

    def test_create_agent_422_validation(self, client, auth_admin) -> None:
        resp = client.post("/api/v1/agents", json={"name": ""})
        assert resp.status_code == 422

    def test_create_agent_401_unauthorized(self, client) -> None:
        resp = client.post(
            "/api/v1/agents",
            json={"name": "New Agent"},
        )
        assert resp.status_code == 401

    def test_create_agent_developer_201(self, client, auth_role) -> None:
        """系统 developer 角色默认含 agent:write，可创建。"""
        auth_role("developer")
        with patch(
            "app.api.v1.agents.AgentService.create_agent",
            new=AsyncMock(return_value=_fake_doc()),
        ):
            resp = client.post("/api/v1/agents", json={"name": "New Agent"})
        assert resp.status_code == 201

    def test_create_agent_operator_403(self, client, auth_role) -> None:
        """operator 只有 agent:invoke/agent:read，无 agent:write。"""
        auth_role("operator")
        resp = client.post("/api/v1/agents", json={"name": "New Agent"})
        assert resp.status_code == 403

    def test_create_agent_custom_role_with_write_201(
        self, client, auth_role, _perm_overrides
    ) -> None:
        """自定义角色被授予 agent:write 即可创建——权限驱动，不认角色名。"""
        _perm_overrides["custom_dev"] = {"agent:read", "agent:write"}
        auth_role("custom_dev")
        with patch(
            "app.api.v1.agents.AgentService.create_agent",
            new=AsyncMock(return_value=_fake_doc()),
        ):
            resp = client.post("/api/v1/agents", json={"name": "New Agent"})
        assert resp.status_code == 201

    def test_create_agent_custom_role_without_write_403(
        self, client, auth_role, _perm_overrides
    ) -> None:
        """自定义角色仅有 agent:read 时创建被拒。"""
        _perm_overrides["custom_reader"] = {"agent:read"}
        auth_role("custom_reader")
        resp = client.post("/api/v1/agents", json={"name": "New Agent"})
        assert resp.status_code == 403


class TestGetAgent:
    """GET /api/v1/agents/{agent_id}"""

    def test_get_agent_200(self, client, auth_admin) -> None:
        with patch(
            "app.api.v1.agents.AgentService.get_agent",
            new=AsyncMock(return_value=_fake_doc()),
        ):
            resp = client.get("/api/v1/agents/agent_01HTEST")
        assert resp.status_code == 200
        assert resp.json()["id"] == "agent_01HTEST"

    def test_get_agent_404(self, client, auth_admin) -> None:
        with patch(
            "app.api.v1.agents.AgentService.get_agent",
            new=AsyncMock(return_value=None),
        ):
            resp = client.get("/api/v1/agents/agent_NONEXIST")
        assert resp.status_code == 404
        assert resp.json()["error"]["code"] == "AGENT_NOT_FOUND"


class TestUpdateAgent:
    """PUT /api/v1/agents/{agent_id}"""

    def test_update_agent_200(self, client, auth_admin) -> None:
        updated = _fake_doc(name="Updated Agent")
        with patch(
            "app.api.v1.agents.AgentService.update_agent",
            new=AsyncMock(return_value=updated),
        ):
            resp = client.put(
                "/api/v1/agents/agent_01HTEST",
                json={"name": "Updated Agent", "description": "Updated"},
            )
        assert resp.status_code == 200
        assert resp.json()["name"] == "Updated Agent"

    def test_update_agent_404(self, client, auth_admin) -> None:
        with patch(
            "app.api.v1.agents.AgentService.update_agent",
            new=AsyncMock(return_value=None),
        ):
            resp = client.put(
                "/api/v1/agents/agent_NONEXIST",
                json={"name": "Ghost", "description": ""},
            )
        assert resp.status_code == 404
        assert resp.json()["error"]["code"] == "AGENT_NOT_FOUND"

    def test_update_agent_409_conflict(self, client, auth_admin) -> None:
        with patch(
            "app.api.v1.agents.AgentService.update_agent",
            new=AsyncMock(
                side_effect=ConflictError(
                    code="AGENT_NAME_CONFLICT",
                    message="Agent 名称 'Dup' 已被占用",
                )
            ),
        ):
            resp = client.put(
                "/api/v1/agents/agent_01HTEST",
                json={"name": "Dup", "description": ""},
            )
        assert resp.status_code == 409

    def test_update_published_409(self, client, auth_admin) -> None:
        """Published agent should return 409."""
        with patch(
            "app.api.v1.agents.AgentService.update_agent",
            new=AsyncMock(
                side_effect=ConflictError(
                    code="AGENT_PUBLISHED_IMMUTABLE",
                    message="Agent 'Published' 已发布，不可直接编辑。",
                )
            ),
        ):
            resp = client.put(
                "/api/v1/agents/agent_01HTEST",
                json={"name": "Published", "description": ""},
            )
        assert resp.status_code == 409
        assert resp.json()["error"]["code"] == "AGENT_PUBLISHED_IMMUTABLE"

    def test_update_agent_viewer_forbidden(self, client, auth_viewer) -> None:
        resp = client.put(
            "/api/v1/agents/agent_01HTEST",
            json={"name": "Hacked", "description": ""},
        )
        assert resp.status_code == 403

    def test_update_strips_xss_in_name(self, client, auth_admin) -> None:
        """问题3：name 中的 XSS 载荷被清洗后再下发给 service。"""
        updated = _fake_doc(name="助手")
        with patch(
            "app.api.v1.agents.AgentService.update_agent",
            new=AsyncMock(return_value=updated),
        ) as mock_svc:
            resp = client.put(
                "/api/v1/agents/agent_01HTEST",
                json={"name": "<script>alert(1)</script>助手"},
            )
        assert resp.status_code == 200, resp.text
        # 传给 service 的 name 已剥离 <script> 块
        assert mock_svc.call_args.kwargs["name"] == "助手"

    def test_update_strips_xss_in_prompt_slots(self, client, auth_admin) -> None:
        """问题3：prompt_slots 中的 XSS 被清洗，但保留 {{ }} 模板与 < 文本。"""
        updated = _fake_doc()
        with patch(
            "app.api.v1.agents.AgentService.update_agent",
            new=AsyncMock(return_value=updated),
        ) as mock_svc:
            resp = client.put(
                "/api/v1/agents/agent_01HTEST",
                json={
                    "name": "x",
                    "prompt_slots": {
                        "role": "<script>x</script>你是助手",
                        "task": "当 a<b 时执行 {{ step }}",
                    },
                },
            )
        assert resp.status_code == 200, resp.text
        slots = mock_svc.call_args.kwargs["prompt_slots"]
        assert "script" not in slots["role"].lower()
        assert slots["role"] == "你是助手"
        # 模板与普通 < 比较保留
        assert "{{ step }}" in slots["task"]
        assert "a<b" in slots["task"]

    def test_update_rejects_oversized_prompt_slot(self, client, auth_admin) -> None:
        """问题2：单个 prompt_slot value 超过 10000 字符 → 422。"""
        with patch(
            "app.api.v1.agents.AgentService.update_agent",
            new=AsyncMock(return_value=_fake_doc()),
        ):
            resp = client.put(
                "/api/v1/agents/agent_01HTEST",
                json={"name": "x", "prompt_slots": {"role": "a" * 10001}},
            )
        assert resp.status_code == 422, resp.text

    def test_update_rejects_bad_prompt_slot_key(self, client, auth_admin) -> None:
        """prompt_slots 的 key 必须匹配 ^[a-zA-Z0-9_]+$，否则 422。"""
        with patch(
            "app.api.v1.agents.AgentService.update_agent",
            new=AsyncMock(return_value=_fake_doc()),
        ):
            resp = client.put(
                "/api/v1/agents/agent_01HTEST",
                json={"name": "x", "prompt_slots": {"bad key!": "v"}},
            )
        assert resp.status_code == 422, resp.text


class TestPublishAgent:
    """POST /api/v1/agents/{agent_id}/publish"""

    def test_publish_agent_200(self, client, auth_admin) -> None:
        published = _fake_doc()
        published["status"] = "published"
        with patch(
            "app.api.v1.agents.AgentService.publish_agent",
            new=AsyncMock(return_value=published),
        ):
            resp = client.post("/api/v1/agents/agent_01HTEST/publish")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "published"

    def test_publish_agent_404(self, client, auth_admin) -> None:
        with patch(
            "app.api.v1.agents.AgentService.publish_agent",
            new=AsyncMock(return_value=None),
        ):
            resp = client.post("/api/v1/agents/agent_NONEXIST/publish")
        assert resp.status_code == 404
        assert resp.json()["error"]["code"] == "AGENT_NOT_FOUND"

    def test_publish_agent_viewer_forbidden(self, client, auth_viewer) -> None:
        resp = client.post("/api/v1/agents/agent_01HTEST/publish")
        assert resp.status_code == 403

    def test_publish_agent_401_unauthorized(self, client) -> None:
        resp = client.post("/api/v1/agents/agent_01HTEST/publish")
        assert resp.status_code == 401


class TestArchiveAgent:
    """POST /api/v1/agents/{agent_id}/archive"""

    def test_archive_agent_200(self, client, auth_admin) -> None:
        archived = _fake_doc()
        archived["status"] = "archived"
        with patch(
            "app.api.v1.agents.AgentService.archive_agent",
            new=AsyncMock(return_value=archived),
        ):
            resp = client.post("/api/v1/agents/agent_01HTEST/archive")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "archived"

    def test_archive_agent_404(self, client, auth_admin) -> None:
        with patch(
            "app.api.v1.agents.AgentService.archive_agent",
            new=AsyncMock(return_value=None),
        ):
            resp = client.post("/api/v1/agents/agent_NONEXIST/archive")
        assert resp.status_code == 404
        assert resp.json()["error"]["code"] == "AGENT_NOT_FOUND"

    def test_archive_agent_viewer_forbidden(self, client, auth_viewer) -> None:
        resp = client.post("/api/v1/agents/agent_01HTEST/archive")
        assert resp.status_code == 403


class TestDuplicateAgent:
    """POST /api/v1/agents/{agent_id}/duplicate"""

    def test_duplicate_agent_201(self, client, auth_admin) -> None:
        dup_doc = _fake_doc(agent_id="agent_02HDUP", name="Test Agent_copy")
        dup_doc["status"] = "draft"
        with patch(
            "app.api.v1.agents.AgentService.duplicate_agent",
            new=AsyncMock(return_value=dup_doc),
        ):
            resp = client.post("/api/v1/agents/agent_01HTEST/duplicate")
        assert resp.status_code == 201
        data = resp.json()
        assert data["name"] == "Test Agent_copy"
        assert data["status"] == "draft"

    def test_duplicate_agent_404(self, client, auth_admin) -> None:
        with patch(
            "app.api.v1.agents.AgentService.duplicate_agent",
            new=AsyncMock(side_effect=NotFoundError(
                code="AGENT_NOT_FOUND",
                message="Agent agent_NONEXIST 不存在",
            )),
        ):
            resp = client.post("/api/v1/agents/agent_NONEXIST/duplicate")
        assert resp.status_code == 404

    def test_duplicate_agent_409_name_conflict(self, client, auth_admin) -> None:
        with patch(
            "app.api.v1.agents.AgentService.duplicate_agent",
            new=AsyncMock(side_effect=ConflictError(
                code="AGENT_DUPLICATE_NAME_CONFLICT",
                message="无法生成唯一名称，请手动创建",
            )),
        ):
            resp = client.post("/api/v1/agents/agent_01HTEST/duplicate")
        assert resp.status_code == 409
        assert resp.json()["error"]["code"] == "AGENT_DUPLICATE_NAME_CONFLICT"

    def test_duplicate_agent_viewer_forbidden(self, client, auth_viewer) -> None:
        resp = client.post("/api/v1/agents/agent_01HTEST/duplicate")
        assert resp.status_code == 403


class TestDeleteAgent:
    """DELETE /api/v1/agents/{agent_id}"""

    def test_delete_agent_204(self, client, auth_admin) -> None:
        with patch(
            "app.api.v1.agents.AgentService.delete_agent",
            new=AsyncMock(return_value=True),
        ):
            resp = client.delete("/api/v1/agents/agent_01HTEST")
        assert resp.status_code == 204

    def test_delete_agent_404(self, client, auth_admin) -> None:
        with patch(
            "app.api.v1.agents.AgentService.delete_agent",
            new=AsyncMock(return_value=False),
        ):
            resp = client.delete("/api/v1/agents/agent_NONEXIST")
        assert resp.status_code == 404

    def test_delete_agent_viewer_forbidden(self, client, auth_viewer) -> None:
        resp = client.delete("/api/v1/agents/agent_01HTEST")
        assert resp.status_code == 403


class TestVersionEndpointsRemoved:
    """Version endpoints should return 404 (removed)."""

    def test_list_versions_not_found(self, client, auth_admin) -> None:
        resp = client.get("/api/v1/agents/agent_01HTEST/versions")
        assert resp.status_code == 404

    def test_get_version_not_found(self, client, auth_admin) -> None:
        resp = client.get("/api/v1/agents/agent_01HTEST/versions/1")
        assert resp.status_code == 404


# =========================================================================
# Contract tests — verify API→Service parameter name alignment
# =========================================================================


class TestAgentApiServiceContract:
    """Verify API handlers call service methods with correct kwarg names."""

    def test_create_agent_kwargs_match_service(self) -> None:
        sig = inspect.signature(AgentService.create_agent)
        valid_params = set(sig.parameters.keys())
        api_kwargs = {
            "name", "description", "prompt_slots",
            "skill_ids", "mcp_connection_ids", "builtin_config",
            "workflow_ids", "knowledge_base_ids",
            "default_model", "max_retry",
        }
        unknown = api_kwargs - valid_params
        assert not unknown, (
            f"API passes unknown kwarg(s) to AgentService.create_agent: "
            f"{unknown}. Valid params: {valid_params}"
        )

    def test_update_agent_kwargs_match_service(self) -> None:
        sig = inspect.signature(AgentService.update_agent)
        valid_params = set(sig.parameters.keys())
        api_kwargs = {
            "agent_id", "name", "description", "prompt_slots",
            "skill_ids", "mcp_connection_ids", "builtin_config",
            "workflow_ids", "knowledge_base_ids",
            "default_model", "max_retry",
        }
        unknown = api_kwargs - valid_params
        assert not unknown, (
            f"API passes unknown kwarg(s) to AgentService.update_agent: "
            f"{unknown}. Valid params: {valid_params}"
        )

    def test_update_agent_no_status_param(self) -> None:
        sig = inspect.signature(AgentService.update_agent)
        assert "status" not in sig.parameters, (
            "AgentService.update_agent should not accept 'status' parameter. "
            "Status changes must go through publish_agent / archive_agent."
        )


# ---------------------------------------------------------------------------
# Stop (mid-stream abort)
# ---------------------------------------------------------------------------


class _FakeTask:
    """Sync stand-in for asyncio.Task — done()/cancel() without an event loop.

    TestClient 在独立事件循环里跑 app，跨循环注册真任务会 flaky；
    stop 端点只依赖 done()/cancel() 两个动作，替身即可覆盖全部分支。
    """

    def __init__(self) -> None:
        self.cancelled = False

    def done(self) -> bool:
        return self.cancelled

    def cancel(self) -> None:
        self.cancelled = True


class TestStopAgent:
    """POST /api/v1/agents/{agent_id}/stop — mid-stream abort 端点."""

    @staticmethod
    def _register(request_id: str, *, agent_id: str, user_id: str, task: _FakeTask | None = None):
        from app.services.run_registry import ActiveRun, register_run

        run = ActiveRun(
            task=task or _FakeTask(), request_id=request_id,
            agent_id=agent_id, user_id=user_id, session_id="session_test",
        )
        register_run(run)
        return run

    @pytest.fixture(autouse=True)
    def _clean_registry(self):
        from app.services import run_registry

        yield
        run_registry._ACTIVE_RUNS.clear()

    def test_stop_no_active_run_returns_409(self, client, auth_admin):
        """没有注册的活跃运行 → 409 RUN_NOT_ACTIVE."""
        with patch("app.api.v1.agents.set_cancel_flag", new_callable=AsyncMock):
            resp = client.post("/api/v1/agents/agent_01HTEST/stop", json={})
        assert resp.status_code == 409
        assert resp.json()["error"]["code"] == "RUN_NOT_ACTIVE"

    def test_stop_run_owned_by_other_user_returns_403(self, client, auth_admin):
        """运行属于其他用户 → 403，且不动对方的任务."""
        task = _FakeTask()
        self._register("req_other", agent_id="agent_01HTEST", user_id="user_other", task=task)
        with patch("app.api.v1.agents.set_cancel_flag", new_callable=AsyncMock):
            resp = client.post(
                "/api/v1/agents/agent_01HTEST/stop",
                json={"request_id": "req_other"},
            )
        assert resp.status_code == 403
        assert resp.json()["error"]["code"] == "RUN_NOT_OWNED"
        assert task.cancelled is False

    def test_stop_by_request_id_cancels_task(self, client, auth_admin):
        """按 request_id 停止自己的运行 → 202 + task.cancel() 被调用."""
        task = _FakeTask()
        self._register("req_mine", agent_id="agent_01HTEST", user_id="user_01HTEST", task=task)
        with patch("app.api.v1.agents.set_cancel_flag", new_callable=AsyncMock) as mock_flag:
            resp = client.post(
                "/api/v1/agents/agent_01HTEST/stop",
                json={"request_id": "req_mine"},
            )
        assert resp.status_code == 200
        assert resp.json() == {"stopped": True, "request_id": "req_mine"}
        assert task.cancelled is True
        mock_flag.assert_awaited_once_with("req_mine")  # 迭代边界兜底闸也设置了

    def test_stop_without_request_id_targets_latest_run(self, client, auth_admin):
        """不传 request_id → 停该用户在该 Agent 上的最新活跃运行."""
        old_task = _FakeTask()
        new_task = _FakeTask()
        run_old = self._register("req_old", agent_id="agent_01HTEST", user_id="user_01HTEST", task=old_task)
        run_new = self._register("req_new", agent_id="agent_01HTEST", user_id="user_01HTEST", task=new_task)
        run_old.started_at = 100.0
        run_new.started_at = 200.0
        with patch("app.api.v1.agents.set_cancel_flag", new_callable=AsyncMock):
            resp = client.post("/api/v1/agents/agent_01HTEST/stop", json={})
        assert resp.status_code == 200
        assert resp.json()["request_id"] == "req_new"
        assert new_task.cancelled is True
        assert old_task.cancelled is False


# ---------------------------------------------------------------------------
# Cancelled-turn timeline finalization（取消轮展示层持久化）
# ---------------------------------------------------------------------------


class TestFinalizeCancelledTimeline:
    """_finalize_cancelled_timeline — 半截文本合成 + 停止标记 + 空内容跳过。"""

    def test_synthesizes_partial_text_and_marker(self):
        from app.services.agent_execution_service import _finalize_cancelled_timeline

        timeline = [
            {"type": "tool_call", "tool_name": "bash", "args": {"command": "date"}},
            {"type": "tool_result", "tool_name": "bash", "content": "Mon Aug 24", "status": "success"},
            {"type": "text_delta", "content": "根据结果"},
            {"type": "text_delta", "content": "，今天是……"},
        ]
        finalized = _finalize_cancelled_timeline(timeline)
        types = [e["type"] for e in finalized]
        # 原始事件保留 + 合成 text + 末尾标记
        assert types[-2:] == ["text", "text"]
        assert finalized[-2]["content"] == "根据结果，今天是……"
        assert finalized[-1]["content"] == "⏹ 已停止生成"

    def test_partial_only_after_last_complete_text(self):
        """多轮对话：只合成最后一个完整 text 之后的 delta。"""
        from app.services.agent_execution_service import _finalize_cancelled_timeline

        timeline = [
            {"type": "text", "content": "第一轮完整回复"},
            {"type": "text_delta", "content": "第二轮的半截"},
        ]
        finalized = _finalize_cancelled_timeline(timeline)
        assert finalized[-2]["content"] == "第二轮的半截"

    def test_tool_only_cancel_still_persisted_with_marker(self):
        """取消发生在工具后、文本开始前：工具事件 + 标记落库（无合成文本）。"""
        from app.services.agent_execution_service import _finalize_cancelled_timeline

        timeline = [{"type": "tool_call", "tool_name": "bash"}, {"type": "tool_result", "content": "ok"}]
        finalized = _finalize_cancelled_timeline(timeline)
        assert [e["type"] for e in finalized] == ["tool_call", "tool_result", "text"]
        assert finalized[-1]["content"] == "⏹ 已停止生成"

    def test_instant_cancel_returns_empty(self):
        """完全无内容（连 delta 都没流出）→ 不落库。"""
        from app.services.agent_execution_service import _finalize_cancelled_timeline

        assert _finalize_cancelled_timeline([]) == []

    def test_tiny_partial_still_persisted(self):
        """只要流过 delta（哪怕一个字）就是用户看过的半截回复 → 持久化。"""
        from app.services.agent_execution_service import _finalize_cancelled_timeline

        finalized = _finalize_cancelled_timeline([
            {"type": "text_delta", "content": "在"},
        ])
        assert finalized[-2]["content"] == "在"
        assert finalized[-1]["content"] == "⏹ 已停止生成"
