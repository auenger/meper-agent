"""Tests for per-user data isolation on /api/v1/tasks.

任务协作看板按用户维度隔离（与 triggers 同一模式）：
- 普通用户 list 只见自己的任务（created_by 强制覆盖，query 参数被忽略）
- 普通用户对他人任务的 get/delete/intervene/audit-logs/outputs/node-timeline
  一律 404（不泄露存在性），且写操作不得被执行
- admin 默认看全部，可传 created_by 筛选；可访问/干预他人任务
- stats 普通用户只统计自己，admin 全局

所有 TaskService 方法均 mock，不触达真实 MongoDB。
"""
from unittest.mock import AsyncMock, patch

import pytest
from app.api.v1 import tasks as tasks_module
from app.core.security import get_current_user
from app.schemas.user import UserResponse, UserStatus
from fastapi.testclient import TestClient

USER_A = "user_01HISOAA"
USER_B = "user_01HISOBB"
ADMIN = "user_01HISOADMIN"
WORKFLOW_ID = "wf_01HISO"
TASK_A = "task_01HISOTA"  # USER_A 的任务
TASK_B = "task_01HISOTB"  # USER_B 的任务


def _user(user_id: str, role: str = "developer") -> UserResponse:
    return UserResponse(
        id=user_id,
        username=f"user-{user_id[-6:]}",
        email=f"{user_id}@example.com",
        role=role,
        status=UserStatus.ACTIVE,
        created_at="2026-06-01T00:00:00",
        updated_at="2026-06-01T00:00:00",
    )


@pytest.fixture
def user_a() -> UserResponse:
    return _user(USER_A)


@pytest.fixture
def user_b() -> UserResponse:
    return _user(USER_B)


@pytest.fixture
def admin() -> UserResponse:
    return _user(ADMIN, role="admin")


def _override_auth(user: UserResponse):
    async def _fake():
        return user

    return _fake


def _build_client(user: UserResponse) -> tuple[TestClient, object]:
    from app.main import app

    app.dependency_overrides[get_current_user] = _override_auth(user)
    return TestClient(app), app


def _task_doc(task_id: str, owner: str, status: str = "completed") -> dict:
    return {
        "_id": task_id,
        "workflow_id": WORKFLOW_ID,
        "status": status,
        "input": {},
        "variables": {},
        "call_chain": [],
        "created_by": owner,
        "created_by_type": "user",
        "version": 1,
        "timeline": [],
        "source": "manual",
        "total_tokens": 0,
        "created_at": "2026-06-01T00:00:00",
        "updated_at": "2026-06-01T00:00:00",
    }


# ── list ──


def test_list_non_admin_forced_to_own_tasks(user_a: UserResponse) -> None:
    """普通用户：query 里传他人 id 也被强制覆盖为自己的 id。"""
    client, app = _build_client(user_a)
    try:
        list_mock = AsyncMock(return_value=([], 0))
        with patch.object(tasks_module.TaskService, "list_tasks", list_mock):
            resp = client.get(
                "/api/v1/tasks",
                params={"created_by": USER_B, "page": 1, "page_size": 20},
            )

        assert resp.status_code == 200, resp.text
        assert list_mock.await_args.kwargs["created_by"] == USER_A
    finally:
        app.dependency_overrides.clear()


def test_list_admin_defaults_to_all() -> None:
    """admin：不传 created_by 看全部（created_by=None）。"""
    client, app = _build_client(_user(ADMIN, role="admin"))
    try:
        list_mock = AsyncMock(return_value=([], 0))
        with patch.object(tasks_module.TaskService, "list_tasks", list_mock):
            resp = client.get("/api/v1/tasks")

        assert resp.status_code == 200, resp.text
        assert list_mock.await_args.kwargs["created_by"] is None
    finally:
        app.dependency_overrides.clear()


def test_list_admin_can_filter_by_user() -> None:
    """admin：显式传 created_by 按值筛选。"""
    client, app = _build_client(_user(ADMIN, role="admin"))
    try:
        list_mock = AsyncMock(return_value=([], 0))
        with patch.object(tasks_module.TaskService, "list_tasks", list_mock):
            resp = client.get("/api/v1/tasks", params={"created_by": USER_B})

        assert resp.status_code == 200, resp.text
        assert list_mock.await_args.kwargs["created_by"] == USER_B
    finally:
        app.dependency_overrides.clear()


# ── single-task endpoints: cross-user access → 404 ──


def test_get_other_users_task_returns_404(user_a: UserResponse) -> None:
    client, app = _build_client(user_a)
    try:
        with patch.object(
            tasks_module.TaskService,
            "get_task_or_404",
            AsyncMock(return_value=_task_doc(TASK_B, USER_B)),
        ):
            resp = client.get(f"/api/v1/tasks/{TASK_B}")

        assert resp.status_code == 404
        assert resp.json()["error"]["code"] == "TASK_NOT_FOUND"
    finally:
        app.dependency_overrides.clear()


def test_get_own_task_succeeds(user_a: UserResponse) -> None:
    client, app = _build_client(user_a)
    try:
        with patch.object(
            tasks_module.TaskService,
            "get_task_or_404",
            AsyncMock(return_value=_task_doc(TASK_A, USER_A)),
        ):
            resp = client.get(f"/api/v1/tasks/{TASK_A}")

        assert resp.status_code == 200, resp.text
        assert resp.json()["id"] == TASK_A
    finally:
        app.dependency_overrides.clear()


def test_admin_can_get_others_task() -> None:
    client, app = _build_client(_user(ADMIN, role="admin"))
    try:
        with patch.object(
            tasks_module.TaskService,
            "get_task_or_404",
            AsyncMock(return_value=_task_doc(TASK_B, USER_B)),
        ):
            resp = client.get(f"/api/v1/tasks/{TASK_B}")

        assert resp.status_code == 200, resp.text
    finally:
        app.dependency_overrides.clear()


def test_delete_other_users_task_returns_404_and_skips(user_a: UserResponse) -> None:
    """越权删除：404 且 TaskService.delete_task 不得被调用。"""
    client, app = _build_client(user_a)
    try:
        delete_mock = AsyncMock(return_value=None)
        with (
            patch.object(
                tasks_module.TaskService,
                "get_task_or_404",
                AsyncMock(return_value=_task_doc(TASK_B, USER_B)),
            ),
            patch.object(tasks_module.TaskService, "delete_task", delete_mock),
        ):
            resp = client.delete(f"/api/v1/tasks/{TASK_B}")

        assert resp.status_code == 404
        assert delete_mock.await_count == 0
    finally:
        app.dependency_overrides.clear()


def test_intervene_other_users_task_returns_404_and_skips(
    user_a: UserResponse,
) -> None:
    """越权干预（审批）：404 且 TaskService.intervene 不得被调用。"""
    client, app = _build_client(user_a)
    try:
        intervene_mock = AsyncMock(
            return_value={**_task_doc(TASK_B, USER_B), "status": "cancelled"}
        )
        with (
            patch.object(
                tasks_module.TaskService,
                "get_task_or_404",
                AsyncMock(return_value=_task_doc(TASK_B, USER_B, status="running")),
            ),
            patch.object(tasks_module.TaskService, "intervene", intervene_mock),
        ):
            resp = client.post(
                f"/api/v1/tasks/{TASK_B}/intervene",
                json={"action": "cancel", "version": 1},
            )

        assert resp.status_code == 404
        assert intervene_mock.await_count == 0
    finally:
        app.dependency_overrides.clear()


def test_intervene_own_task_executes(user_a: UserResponse) -> None:
    """本人干预：校验通过后正常执行。"""
    client, app = _build_client(user_a)
    try:
        intervene_mock = AsyncMock(
            return_value={**_task_doc(TASK_A, USER_A), "status": "cancelled"}
        )
        with (
            patch.object(
                tasks_module.TaskService,
                "get_task_or_404",
                AsyncMock(return_value=_task_doc(TASK_A, USER_A, status="running")),
            ),
            patch.object(tasks_module.TaskService, "intervene", intervene_mock),
        ):
            resp = client.post(
                f"/api/v1/tasks/{TASK_A}/intervene",
                json={"action": "cancel", "version": 1},
            )

        assert resp.status_code == 200, resp.text
        assert intervene_mock.await_count == 1
    finally:
        app.dependency_overrides.clear()


def test_audit_logs_other_users_task_returns_404(user_a: UserResponse) -> None:
    client, app = _build_client(user_a)
    try:
        logs_mock = AsyncMock(return_value=[])
        with (
            patch.object(
                tasks_module.TaskService,
                "get_task_or_404",
                AsyncMock(return_value=_task_doc(TASK_B, USER_B)),
            ),
            patch.object(tasks_module.TaskService, "list_audit_logs", logs_mock),
        ):
            resp = client.get(f"/api/v1/tasks/{TASK_B}/audit-logs")

        assert resp.status_code == 404
        assert logs_mock.await_count == 0
    finally:
        app.dependency_overrides.clear()


def test_outputs_other_users_task_returns_404(user_a: UserResponse) -> None:
    """越权 outputs：在触达 FileService 之前即被 404 拦截。"""
    client, app = _build_client(user_a)
    try:
        with patch.object(
            tasks_module.TaskService,
            "get_task_or_404",
            AsyncMock(return_value=_task_doc(TASK_B, USER_B)),
        ):
            resp = client.get(f"/api/v1/tasks/{TASK_B}/outputs")

        assert resp.status_code == 404
        assert resp.json()["error"]["code"] == "TASK_NOT_FOUND"
    finally:
        app.dependency_overrides.clear()


def test_node_timeline_other_users_task_returns_404(user_a: UserResponse) -> None:
    """越权 node timeline：在触达 checkpointer 之前即被 404 拦截。"""
    client, app = _build_client(user_a)
    try:
        with patch.object(
            tasks_module.TaskService,
            "get_task_or_404",
            AsyncMock(return_value=_task_doc(TASK_B, USER_B)),
        ):
            resp = client.get(f"/api/v1/tasks/{TASK_B}/nodes/agent_1/timeline")

        assert resp.status_code == 404
        assert resp.json()["error"]["code"] == "TASK_NOT_FOUND"
    finally:
        app.dependency_overrides.clear()


# ── stats ──


def test_stats_non_admin_scoped_to_own_tasks(user_a: UserResponse) -> None:
    """普通用户 stats 只统计自己的任务。"""
    client, app = _build_client(user_a)
    try:
        stats_mock = AsyncMock(
            return_value={
                "global_running": 0,
                "global_pending": 0,
                "global_max": 50,
                "user_limit": 5,
                "user_stats": [],
            }
        )
        with patch.object(tasks_module.TaskService, "get_stats", stats_mock):
            resp = client.get("/api/v1/tasks/stats")

        assert resp.status_code == 200, resp.text
        assert stats_mock.await_args.kwargs["created_by"] == USER_A
    finally:
        app.dependency_overrides.clear()


def test_stats_admin_gets_global_view() -> None:
    """admin stats：全局视角（created_by=None）。"""
    client, app = _build_client(_user(ADMIN, role="admin"))
    try:
        stats_mock = AsyncMock(
            return_value={
                "global_running": 1,
                "global_pending": 2,
                "global_max": 50,
                "user_limit": 5,
                "user_stats": [{"user_id": USER_A, "running": 1}],
            }
        )
        with patch.object(tasks_module.TaskService, "get_stats", stats_mock):
            resp = client.get("/api/v1/tasks/stats")

        assert resp.status_code == 200, resp.text
        assert stats_mock.await_args.kwargs["created_by"] is None
    finally:
        app.dependency_overrides.clear()
