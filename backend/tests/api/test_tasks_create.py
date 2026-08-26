"""Tests for POST /api/v1/tasks input validation (fail fast on missing required fields).

手动创建任务入口与 ext invoke 共用 validate_workflow_input：start 节点声明了
必填变量而 input 缺字段时，创建即返回 422 INPUT_VALIDATION_FAILED，
而不是任务创建成功后运行到 start 节点才 FAILED。
"""
from unittest.mock import AsyncMock, patch

import pytest
from app.api.v1 import tasks as tasks_module
from app.core.security import get_current_user
from app.schemas.user import UserResponse, UserStatus
from fastapi.testclient import TestClient

USER_ID = "user_01HTASKCREATE"
WORKFLOW_ID = "wf_01HCREATE"


@pytest.fixture
def current_user() -> UserResponse:
    return UserResponse(
        id=USER_ID,
        username="creator",
        email="creator@example.com",
        role="developer",
        status=UserStatus.ACTIVE,
        created_at="2026-06-01T00:00:00",
        updated_at="2026-06-01T00:00:00",
    )


def _override_auth(user: UserResponse):
    async def _fake():
        return user

    return _fake


def _make_workflow_doc(output_vars: list | None) -> dict:
    """Workflow doc；output_vars=None 表示 start 节点未声明任何输入变量。"""
    config: dict = {}
    if output_vars is not None:
        config["output_variables"] = output_vars
    return {
        "_id": WORKFLOW_ID,
        "name": "Test Workflow",
        "status": "published",
        "version": 1,
        "nodes": [
            {
                "node_id": "start_1",
                "type": "start",
                "label": "Start",
                "config": config,
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
        "created_at": "2026-06-01T00:00:00",
        "updated_at": "2026-06-01T00:00:00",
    }


def _make_task_doc(input_data: dict) -> dict:
    return {
        "_id": "task_01HNEW",
        "workflow_id": WORKFLOW_ID,
        "status": "pending",
        "input": input_data,
        "variables": {},
        "call_chain": [],
        "created_by": USER_ID,
        "created_by_type": "user",
        "version": 1,
        "timeline": [],
        "source": "manual",
        "total_tokens": 0,
        "created_at": "2026-06-01T00:00:00",
        "updated_at": "2026-06-01T00:00:00",
    }


REQUIRED_VARS = [
    {
        "name": "topic",
        "label": "主题",
        "type": "text",
        "constraints": {"required": True, "default_value": ""},
    },
    {
        "name": "style",
        "label": "风格",
        "type": "text",
        "constraints": {"required": False, "default_value": "formal"},
    },
]


def _build_client(current_user: UserResponse):
    from app.main import app

    app.dependency_overrides[get_current_user] = _override_auth(current_user)
    return TestClient(app), app


def _post_create(client: TestClient, body: dict) -> tuple[int, dict]:
    resp = client.post("/api/v1/tasks", json=body)
    return resp.status_code, resp.json()


def test_create_task_missing_required_input_returns_422(current_user: UserResponse) -> None:
    """必填变量缺失 → 422 INPUT_VALIDATION_FAILED，不创建任务。"""
    client, app = _build_client(current_user)
    try:
        create_mock = AsyncMock(return_value=_make_task_doc({"topic": "x"}))
        with (
            patch.object(
                tasks_module.WorkflowService,
                "get",
                AsyncMock(return_value=_make_workflow_doc(REQUIRED_VARS)),
            ),
            patch.object(tasks_module.TaskService, "create_task", create_mock),
        ):
            status_code, payload = _post_create(
                client, {"workflow_id": WORKFLOW_ID, "input": {"style": "casual"}}
            )

        assert status_code == 422
        assert payload["error"]["code"] == "INPUT_VALIDATION_FAILED"
        assert "topic" in payload["error"]["message"]
        assert create_mock.await_count == 0
    finally:
        app.dependency_overrides.clear()


def test_create_task_with_full_input_returns_201(current_user: UserResponse) -> None:
    """必填变量齐全 → 正常创建，input 原样传给 TaskService。"""
    client, app = _build_client(current_user)
    try:
        create_mock = AsyncMock(
            return_value=_make_task_doc({"topic": "AI", "style": "casual"})
        )
        with (
            patch.object(
                tasks_module.WorkflowService,
                "get",
                AsyncMock(return_value=_make_workflow_doc(REQUIRED_VARS)),
            ),
            patch.object(tasks_module.TaskService, "create_task", create_mock),
        ):
            status_code, payload = _post_create(
                client,
                {"workflow_id": WORKFLOW_ID, "input": {"topic": "AI", "style": "casual"}},
            )

        assert status_code == 201, payload
        assert create_mock.await_count == 1
        assert create_mock.await_args.kwargs["input_data"] == {
            "topic": "AI",
            "style": "casual",
        }
        assert payload["id"] == "task_01HNEW"
    finally:
        app.dependency_overrides.clear()


def test_create_task_without_input_schema_skips_validation(
    current_user: UserResponse,
) -> None:
    """start 节点未声明输入变量 → 不校验，空 input 也能创建。"""
    client, app = _build_client(current_user)
    try:
        create_mock = AsyncMock(return_value=_make_task_doc({}))
        with (
            patch.object(
                tasks_module.WorkflowService,
                "get",
                AsyncMock(return_value=_make_workflow_doc(output_vars=None)),
            ),
            patch.object(tasks_module.TaskService, "create_task", create_mock),
        ):
            status_code, payload = _post_create(
                client, {"workflow_id": WORKFLOW_ID, "input": {}}
            )

        assert status_code == 201, payload
        assert create_mock.await_count == 1
    finally:
        app.dependency_overrides.clear()


def test_create_task_workflow_not_found_skips_input_validation(
    current_user: UserResponse,
) -> None:
    """workflow 文档不存在 → 跳过入口校验，交由 TaskService 走原有报错路径。"""
    client, app = _build_client(current_user)
    try:
        create_mock = AsyncMock(return_value=_make_task_doc({}))
        with (
            patch.object(
                tasks_module.WorkflowService,
                "get",
                AsyncMock(return_value=None),
            ),
            patch.object(tasks_module.TaskService, "create_task", create_mock),
        ):
            status_code, _ = _post_create(
                client, {"workflow_id": WORKFLOW_ID, "input": {}}
            )

        assert status_code == 201
        assert create_mock.await_count == 1
    finally:
        app.dependency_overrides.clear()
