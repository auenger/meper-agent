"""Tests for external API — Workflow and Task endpoints."""
from datetime import UTC
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
    """API Key principal with all scopes and no resource bindings."""
    return ApiKeyPrincipal(
        key_id="apikey_test",
        owner_user_id="user_owner",
        scopes=["agents:read", "agents:invoke", "workflows:read", "workflows:invoke", "executions:read"],
        bindings={"agents": [], "workflows": []},
        rate_limit=60,
    )


@pytest.fixture
def limited_principal():
    """API Key principal with limited scope and workflow binding."""
    return ApiKeyPrincipal(
        key_id="apikey_limited",
        owner_user_id="user_owner",
        scopes=["workflows:read", "workflows:invoke"],
        bindings={"agents": [], "workflows": ["wf_allowed"]},
        rate_limit=60,
    )


def _override_auth(principal):
    """Override the API Key auth dependency."""
    app.dependency_overrides[auth_and_rate_limit] = lambda: principal
    return lambda: app.dependency_overrides.clear()


def _make_workflow_doc(wf_id="wf_01", name="Test Workflow", status="published"):
    return {
        "_id": wf_id,
        "name": name,
        "description": "A test workflow",
        "status": status,
        "version": 2,
        "nodes": [
            {
                "node_id": "start_1",
                "type": "start",
                "label": "Start",
                "config": {
                    "input_schema": {
                        "type": "object",
                        "properties": {
                            "batch_id": {"type": "string", "description": "批次号"},
                        },
                        "required": ["batch_id"],
                    },
                },
                "position": {"x": 0, "y": 0},
            },
            {
                "node_id": "end_1",
                "type": "end",
                "label": "End",
                "config": {},
                "position": {"x": 200, "y": 0},
            },
        ],
        "edges": [],
        "tags": ["test"],
        "created_by": "user_admin",
        "created_at": "2026-01-01T00:00:00",
        "updated_at": "2026-01-01T00:00:00",
    }


def _make_task_doc(task_id="task_01", wf_id="wf_01", status="pending"):
    from datetime import datetime

    now = datetime(2026, 1, 1, tzinfo=UTC)
    return {
        "_id": task_id,
        "workflow_id": wf_id,
        "workflow_version": "2",
        "status": status,
        "input": {"batch_id": "A23"},
        "output": {"result": "done"} if status == "completed" else None,
        "error": None,
        "created_by": "user_owner",
        "created_by_type": "api_key",
        "version": 1,
        "created_at": now,
        "updated_at": now,
    }


# ---------------------------------------------------------------------------
# AC-1: List accessible Workflows
# ---------------------------------------------------------------------------


class TestListWorkflows:
    """GET /api/v1/ext/workflows"""

    def test_list_workflows_success(self, client, full_principal) -> None:
        cleanup = _override_auth(full_principal)
        try:
            wfs = [_make_workflow_doc(), _make_workflow_doc("wf_02", "WF 2")]
            with patch(
                "app.services.workflow_service.WorkflowService.list",
                new=AsyncMock(return_value=(wfs, 2)),
            ):
                resp = client.get("/api/v1/ext/workflows")
            assert resp.status_code == 200
            data = resp.json()
            assert data["total"] == 2
            assert len(data["items"]) == 2
            assert data["items"][0]["id"] == "wf_01"
            assert data["items"][0]["name"] == "Test Workflow"
            assert data["items"][0]["status"] == "published"
            assert data["items"][0]["version"] == 2
            # input_schema should be extracted from start node
            assert "batch_id" in data["items"][0]["input_schema"].get("properties", {})
        finally:
            cleanup()

    def test_list_workflows_filtered_by_bindings(self, client, limited_principal) -> None:
        cleanup = _override_auth(limited_principal)
        try:
            wfs = [
                _make_workflow_doc("wf_allowed", "Allowed WF"),
                _make_workflow_doc("wf_other", "Other WF"),
            ]
            with patch(
                "app.services.workflow_service.WorkflowService.list",
                new=AsyncMock(return_value=(wfs, 2)),
            ):
                resp = client.get("/api/v1/ext/workflows")
            assert resp.status_code == 200
            data = resp.json()
            assert len(data["items"]) == 1
            assert data["items"][0]["id"] == "wf_allowed"
        finally:
            cleanup()

    def test_list_workflows_scope_denied(self, client) -> None:
        principal = ApiKeyPrincipal(
            key_id="k", owner_user_id="u",
            scopes=["agents:read"],
            bindings={},
        )
        cleanup = _override_auth(principal)
        try:
            resp = client.get("/api/v1/ext/workflows")
            assert resp.status_code == 403
        finally:
            cleanup()


# ---------------------------------------------------------------------------
# AC-2: Get Workflow details
# ---------------------------------------------------------------------------


class TestGetWorkflow:
    """GET /api/v1/ext/workflows/{workflow_id}"""

    def test_get_workflow_success(self, client, full_principal) -> None:
        cleanup = _override_auth(full_principal)
        try:
            with patch(
                "app.services.workflow_service.WorkflowService.get",
                new=AsyncMock(return_value=_make_workflow_doc()),
            ):
                resp = client.get("/api/v1/ext/workflows/wf_01")
            assert resp.status_code == 200
            data = resp.json()
            assert data["id"] == "wf_01"
            assert data["version"] == 2
            assert len(data["nodes"]) == 2
            assert "batch_id" in data["input_schema"].get("properties", {})
        finally:
            cleanup()

    def test_get_workflow_not_found(self, client, full_principal) -> None:
        cleanup = _override_auth(full_principal)
        try:
            with patch(
                "app.services.workflow_service.WorkflowService.get",
                new=AsyncMock(return_value=None),
            ):
                resp = client.get("/api/v1/ext/workflows/nonexistent")
            assert resp.status_code == 404
        finally:
            cleanup()

    def test_get_workflow_binding_denied(self, client, limited_principal) -> None:
        cleanup = _override_auth(limited_principal)
        try:
            resp = client.get("/api/v1/ext/workflows/wf_not_allowed")
            assert resp.status_code == 403
        finally:
            cleanup()

    def test_get_workflow_draft_not_visible(self, client, full_principal) -> None:
        cleanup = _override_auth(full_principal)
        try:
            with patch(
                "app.services.workflow_service.WorkflowService.get",
                new=AsyncMock(return_value=_make_workflow_doc(status="draft")),
            ):
                resp = client.get("/api/v1/ext/workflows/wf_01")
            assert resp.status_code == 404
        finally:
            cleanup()


# ---------------------------------------------------------------------------
# AC-3: Invoke Workflow
# ---------------------------------------------------------------------------


class TestInvokeWorkflow:
    """POST /api/v1/ext/workflows/{workflow_id}/invoke"""

    def test_invoke_success(self, client, full_principal) -> None:
        cleanup = _override_auth(full_principal)
        try:
            task_doc = _make_task_doc()
            with (
                patch(
                    "app.services.workflow_service.WorkflowService.get",
                    new=AsyncMock(return_value=_make_workflow_doc()),
                ),
                patch(
                    "app.services.task_service.TaskService.create_task",
                    new=AsyncMock(return_value=task_doc),
                ),
            ):
                resp = client.post(
                    "/api/v1/ext/workflows/wf_01/invoke",
                    json={"input": {"batch_id": "A23"}},
                )
            assert resp.status_code == 201
            data = resp.json()
            assert data["task_id"] == "task_01"
            assert data["status"] == "pending"
            assert data["workflow_id"] == "wf_01"
            assert data["workflow_version"] == 2
        finally:
            cleanup()

    def test_invoke_with_callback_url(self, client, full_principal) -> None:
        cleanup = _override_auth(full_principal)
        try:
            task_doc = _make_task_doc()
            with (
                patch(
                    "app.services.workflow_service.WorkflowService.get",
                    new=AsyncMock(return_value=_make_workflow_doc()),
                ),
                patch(
                    "app.services.task_service.TaskService.create_task",
                    new=AsyncMock(return_value=task_doc),
                ) as mock_create,
            ):
                resp = client.post(
                    "/api/v1/ext/workflows/wf_01/invoke",
                    json={
                        "input": {"batch_id": "A23"},
                        "callback_url": "https://mes.example.com/webhooks/result",
                    },
                )
            assert resp.status_code == 201
            # Verify ext_metadata passed to create_task includes callback_url
            mock_create.assert_called_once()
            call_kwargs = mock_create.call_args.kwargs
            assert call_kwargs["ext_metadata"]["ext_callback_url"] == "https://mes.example.com/webhooks/result"
            assert call_kwargs["ext_metadata"]["ext_api_key_id"] == "apikey_test"
        finally:
            cleanup()

    def test_invoke_missing_required_field(self, client, full_principal) -> None:
        cleanup = _override_auth(full_principal)
        try:
            with patch(
                "app.services.workflow_service.WorkflowService.get",
                new=AsyncMock(return_value=_make_workflow_doc()),
            ):
                resp = client.post(
                    "/api/v1/ext/workflows/wf_01/invoke",
                    json={"input": {}},  # missing batch_id
                )
            assert resp.status_code == 422
        finally:
            cleanup()

    def test_invoke_scope_denied(self, client) -> None:
        principal = ApiKeyPrincipal(
            key_id="k", owner_user_id="u",
            scopes=["workflows:read"],  # no workflows:invoke
            bindings={},
        )
        cleanup = _override_auth(principal)
        try:
            resp = client.post(
                "/api/v1/ext/workflows/wf_01/invoke",
                json={"input": {"batch_id": "A23"}},
            )
            assert resp.status_code == 403
        finally:
            cleanup()

    def test_invoke_binding_denied(self, client, limited_principal) -> None:
        cleanup = _override_auth(limited_principal)
        try:
            resp = client.post(
                "/api/v1/ext/workflows/wf_not_allowed/invoke",
                json={"input": {"batch_id": "A23"}},
            )
            assert resp.status_code == 403
        finally:
            cleanup()

    def test_invoke_workflow_not_found(self, client, full_principal) -> None:
        cleanup = _override_auth(full_principal)
        try:
            with patch(
                "app.services.workflow_service.WorkflowService.get",
                new=AsyncMock(return_value=None),
            ):
                resp = client.post(
                    "/api/v1/ext/workflows/nonexistent/invoke",
                    json={"input": {"batch_id": "A23"}},
                )
            assert resp.status_code == 404
        finally:
            cleanup()


# ---------------------------------------------------------------------------
# AC-4: Query Task status
# ---------------------------------------------------------------------------


class TestGetTask:
    """GET /api/v1/ext/tasks/{task_id}"""

    def test_get_task_success(self, client, full_principal) -> None:
        cleanup = _override_auth(full_principal)
        try:
            with patch(
                "app.services.task_service.TaskService.get_task",
                new=AsyncMock(return_value=_make_task_doc(status="completed")),
            ):
                resp = client.get("/api/v1/ext/tasks/task_01")
            assert resp.status_code == 200
            data = resp.json()
            assert data["id"] == "task_01"
            assert data["workflow_id"] == "wf_01"
            assert data["status"] == "completed"
            assert data["output"] == {"result": "done"}
            # datetime fields must serialize to ISO strings (regression: was 500
            # when ExtTaskResponse declared created_at/updated_at as str).
            assert data["created_at"].startswith("2026-01-01")
            assert data["updated_at"].startswith("2026-01-01")
        finally:
            cleanup()

    def test_get_task_not_found(self, client, full_principal) -> None:
        cleanup = _override_auth(full_principal)
        try:
            with patch(
                "app.services.task_service.TaskService.get_task",
                new=AsyncMock(return_value=None),
            ):
                resp = client.get("/api/v1/ext/tasks/nonexistent")
            assert resp.status_code == 404
        finally:
            cleanup()

    def test_get_task_other_user_denied(self, client, full_principal) -> None:
        """Tasks created by a different user are not accessible."""
        cleanup = _override_auth(full_principal)
        try:
            task_doc = _make_task_doc()
            task_doc["created_by"] = "other_user"  # not user_owner
            with patch(
                "app.services.task_service.TaskService.get_task",
                new=AsyncMock(return_value=task_doc),
            ):
                resp = client.get("/api/v1/ext/tasks/task_01")
            assert resp.status_code == 404
        finally:
            cleanup()

    def test_get_task_created_by_agent_callback_mode(self, client, full_principal) -> None:
        """Tasks created by an Agent in callback mode carry ``owner:sub``.

        These must still be accessible to the owner's API Key — the agent
        writes the resolved user_id as created_by so NotificationService
        can address the real end-user.
        """
        cleanup = _override_auth(full_principal)
        try:
            task_doc = _make_task_doc(status="running")
            task_doc["created_by"] = "user_owner:partner_user_42"
            task_doc["created_by_type"] = "agent"
            with patch(
                "app.services.task_service.TaskService.get_task",
                new=AsyncMock(return_value=task_doc),
            ):
                resp = client.get("/api/v1/ext/tasks/task_01")
            assert resp.status_code == 200
            data = resp.json()
            assert data["id"] == "task_01"
            assert data["status"] == "running"
        finally:
            cleanup()

    def test_get_task_created_by_agent_legacy_visitor(self, client, full_principal) -> None:
        """Tasks created by an Agent in legacy mode carry ``owner:visitor_id``."""
        cleanup = _override_auth(full_principal)
        try:
            task_doc = _make_task_doc()
            task_doc["created_by"] = "user_owner:visitor_abc"
            task_doc["created_by_type"] = "agent"
            with patch(
                "app.services.task_service.TaskService.get_task",
                new=AsyncMock(return_value=task_doc),
            ):
                resp = client.get("/api/v1/ext/tasks/task_01")
            assert resp.status_code == 200
        finally:
            cleanup()

    def test_get_task_owner_prefix_mismatch_denied(self, client, full_principal) -> None:
        """A different owner's prefixed id must still be rejected."""
        cleanup = _override_auth(full_principal)
        try:
            task_doc = _make_task_doc()
            task_doc["created_by"] = "other_owner:some_sub"
            with patch(
                "app.services.task_service.TaskService.get_task",
                new=AsyncMock(return_value=task_doc),
            ):
                resp = client.get("/api/v1/ext/tasks/task_01")
            assert resp.status_code == 404
        finally:
            cleanup()

    def test_get_task_scope_denied(self, client) -> None:
        principal = ApiKeyPrincipal(
            key_id="k", owner_user_id="u",
            scopes=["workflows:read"],  # no executions:read
            bindings={},
        )
        cleanup = _override_auth(principal)
        try:
            resp = client.get("/api/v1/ext/tasks/task_01")
            assert resp.status_code == 403
        finally:
            cleanup()


# ---------------------------------------------------------------------------
# Task outputs (ext)
# ---------------------------------------------------------------------------


class TestListTaskOutputs:
    """GET /api/v1/ext/tasks/{task_id}/outputs"""

    def test_list_outputs_success(self, client, full_principal) -> None:
        cleanup = _override_auth(full_principal)
        try:
            fake_files = [
                {
                    "_id": "file_1",
                    "owner_user_id": "user_owner",
                    "storage_key": "tasks/task_01/report.xlsx",
                    "name": "report.xlsx",
                    "size": 1024,
                    "mime_type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    "sha256": "abc123",
                    "origin_kind": "workflow_run",
                    "origin_id": "task_01",
                    "status": "ready",
                    "created_at": "2026-01-01T00:00:00",
                    "updated_at": "2026-01-01T00:00:00",
                }
            ]
            # _get_owned_task -> get_task returns an owned doc
            with patch(
                "app.services.task_service.TaskService.get_task",
                new=AsyncMock(return_value=_make_task_doc()),
            ), patch(
                "app.services.file_service.FileService"
            ) as file_service_mock, patch(
                "app.services.file_storage.LocalFileStorage"
            ):
                # chain: FileService(LocalFileStorage())._file_refs().find(...).sort(...).to_list
                cursor = file_service_mock.return_value._file_refs.return_value.find.return_value.sort.return_value
                cursor.to_list = AsyncMock(return_value=fake_files)
                resp = client.get("/api/v1/ext/tasks/task_01/outputs")
            assert resp.status_code == 200
            data = resp.json()
            assert len(data) == 1
            # FileRefResponse serializes with alias "_id" (matches frontend TaskOutputFile._id)
            assert data[0]["_id"] == "file_1"
            assert data[0]["name"] == "report.xlsx"
        finally:
            cleanup()

    def test_list_outputs_task_not_found(self, client, full_principal) -> None:
        cleanup = _override_auth(full_principal)
        try:
            with patch(
                "app.services.task_service.TaskService.get_task",
                new=AsyncMock(return_value=None),
            ):
                resp = client.get("/api/v1/ext/tasks/nonexistent/outputs")
            assert resp.status_code == 404
        finally:
            cleanup()

    def test_list_outputs_other_user_denied(self, client, full_principal) -> None:
        cleanup = _override_auth(full_principal)
        try:
            task_doc = _make_task_doc()
            task_doc["created_by"] = "other_user"
            with patch(
                "app.services.task_service.TaskService.get_task",
                new=AsyncMock(return_value=task_doc),
            ):
                resp = client.get("/api/v1/ext/tasks/task_01/outputs")
            assert resp.status_code == 404
        finally:
            cleanup()

    def test_list_outputs_scope_denied(self, client) -> None:
        principal = ApiKeyPrincipal(
            key_id="k", owner_user_id="u",
            scopes=["workflows:read"],  # no executions:read
            bindings={},
        )
        cleanup = _override_auth(principal)
        try:
            resp = client.get("/api/v1/ext/tasks/task_01/outputs")
            assert resp.status_code == 403
        finally:
            cleanup()


# ---------------------------------------------------------------------------
# Task node timeline (ext)
# ---------------------------------------------------------------------------


class TestGetNodeTimeline:
    """GET /api/v1/ext/tasks/{task_id}/nodes/{node_id}/timeline"""

    def test_get_timeline_success(self, client, full_principal) -> None:
        cleanup = _override_auth(full_principal)
        try:
            fake_tuple = type(
                "Tuple",
                (),
                {
                    "checkpoint": {
                        "channel_values": {
                            "messages": [{"type": "ai", "content": "hello"}],
                        }
                    }
                },
            )()
            with patch(
                "app.services.task_service.TaskService.get_task",
                new=AsyncMock(return_value=_make_task_doc(status="running")),
            ), patch(
                "app.engine.harness_integration.get_checkpointer"
            ) as get_ckpt, patch(
                "app.services.message_converters.messages_to_timeline_entries",
                return_value=[{"type": "text", "content": "step1"}],
            ):
                get_ckpt.return_value.aget_tuple = AsyncMock(return_value=fake_tuple)
                resp = client.get("/api/v1/ext/tasks/task_01/nodes/agent_1/timeline")
            assert resp.status_code == 200
            data = resp.json()
            assert data["task_id"] == "task_01"
            assert data["node_id"] == "agent_1"
            # NodeTimelineEntry serializes with default fields (tool_name/args/id)
            assert len(data["timeline"]) == 1
            assert data["timeline"][0]["type"] == "text"
            assert data["timeline"][0]["content"] == "step1"
        finally:
            cleanup()

    def test_get_timeline_no_checkpoint_404(self, client, full_principal) -> None:
        cleanup = _override_auth(full_principal)
        try:
            with patch(
                "app.services.task_service.TaskService.get_task",
                new=AsyncMock(return_value=_make_task_doc()),
            ), patch(
                "app.engine.harness_integration.get_checkpointer"
            ) as get_ckpt:
                get_ckpt.return_value.aget_tuple = AsyncMock(return_value=None)
                resp = client.get("/api/v1/ext/tasks/task_01/nodes/agent_1/timeline")
            assert resp.status_code == 404
        finally:
            cleanup()

    def test_get_timeline_other_user_denied(self, client, full_principal) -> None:
        cleanup = _override_auth(full_principal)
        try:
            task_doc = _make_task_doc()
            task_doc["created_by"] = "other_user"
            with patch(
                "app.services.task_service.TaskService.get_task",
                new=AsyncMock(return_value=task_doc),
            ):
                resp = client.get("/api/v1/ext/tasks/task_01/nodes/agent_1/timeline")
            assert resp.status_code == 404
        finally:
            cleanup()

    def test_get_timeline_scope_denied(self, client) -> None:
        principal = ApiKeyPrincipal(
            key_id="k", owner_user_id="u",
            scopes=["workflows:read"],  # no executions:read
            bindings={},
        )
        cleanup = _override_auth(principal)
        try:
            resp = client.get("/api/v1/ext/tasks/task_01/nodes/agent_1/timeline")
            assert resp.status_code == 403
        finally:
            cleanup()


# ---------------------------------------------------------------------------
# Task intervention (ext)
# ---------------------------------------------------------------------------


class TestInterveneTask:
    """POST /api/v1/ext/tasks/{task_id}/intervene"""

    def test_intervene_success(self, client, full_principal) -> None:
        cleanup = _override_auth(full_principal)
        try:
            # TaskService.intervene returns the updated doc (running after approve)
            updated_doc = _make_task_doc(status="running", )
            updated_doc["version"] = 2
            with patch(
                "app.services.task_service.TaskService.get_task",
                new=AsyncMock(return_value=_make_task_doc(status="waiting_human")),
            ), patch(
                "app.services.task_service.TaskService.intervene",
                new=AsyncMock(return_value=updated_doc),
            ) as intervene_mock:
                resp = client.post(
                    "/api/v1/ext/tasks/task_01/intervene",
                    json={"action": "approve", "comment": "ok", "version": 1},
                )
            assert resp.status_code == 200
            data = resp.json()
            assert data["task_id"] == "task_01"
            assert data["status"] == "running"
            assert data["version"] == 2
            assert data["message"] == "审批通过"
            # intervene called with api_key attribution
            intervene_mock.assert_awaited_once()
            kwargs = intervene_mock.call_args.kwargs
            assert kwargs["action"] == "approve"
            assert kwargs["triggered_by_type"] == "api_key"
        finally:
            cleanup()

    def test_intervene_other_user_denied(self, client, full_principal) -> None:
        cleanup = _override_auth(full_principal)
        try:
            task_doc = _make_task_doc(status="waiting_human")
            task_doc["created_by"] = "other_user"
            with patch(
                "app.services.task_service.TaskService.get_task",
                new=AsyncMock(return_value=task_doc),
            ):
                resp = client.post(
                    "/api/v1/ext/tasks/task_01/intervene",
                    json={"action": "approve", "version": 1},
                )
            assert resp.status_code == 404
        finally:
            cleanup()

    def test_intervene_scope_denied(self, client) -> None:
        # executions:read is NOT enough — intervene needs workflows:invoke
        principal = ApiKeyPrincipal(
            key_id="k", owner_user_id="u",
            scopes=["executions:read"],  # no workflows:invoke
            bindings={},
        )
        cleanup = _override_auth(principal)
        try:
            resp = client.post(
                "/api/v1/ext/tasks/task_01/intervene",
                json={"action": "approve", "version": 1},
            )
            assert resp.status_code == 403
        finally:
            cleanup()
