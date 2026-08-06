"""Tests for task recovery service — recover_waiting_human_tasks."""
from __future__ import annotations

from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from app.models.task import TaskStatus, utc_now
from app.services.task_recovery import (
    execute_timeout_action,
    recover_orphan_running_tasks,
    recover_waiting_human_tasks,
)


class AsyncCursorMock:
    """Async iterator mock that supports `async for`."""

    def __init__(self, items: list):
        self._items = iter(items)

    def __aiter__(self):
        return self

    async def __anext__(self):
        try:
            return next(self._items)
        except StopIteration:
            raise StopAsyncIteration from None


class TestRecoverWaitingHumanTasks:
    """Test recover_waiting_human_tasks function."""

    @pytest.mark.asyncio
    async def test_no_tasks_to_recover(self) -> None:
        """No tasks found → no recovery needed."""
        mock_cursor = AsyncCursorMock([])

        with patch("app.services.task_recovery.get_database") as mock_db:
            mock_collection = MagicMock()
            mock_db.return_value = {"tasks": mock_collection}
            mock_collection.find = MagicMock(return_value=mock_cursor)

            # Should not raise
            await recover_waiting_human_tasks()

    @pytest.mark.asyncio
    async def test_recover_timed_out_task(self) -> None:
        """Timed-out task → execute timeout_action immediately."""
        now = utc_now()
        past_deadline = now - timedelta(minutes=10)

        task_doc = {
            "_id": "task_timeout",
            "status": TaskStatus.WAITING_HUMAN.value,
            "checkpoint": {
                "paused_at_node": "human_1",
                "timeout_deadline": past_deadline.isoformat(),
                "timeout_action": "auto_approve",
            },
        }

        mock_cursor = AsyncCursorMock([task_doc])

        with patch("app.services.task_recovery.get_database") as mock_db:
            mock_collection = MagicMock()
            mock_db.return_value = {"tasks": mock_collection}
            mock_collection.find = MagicMock(return_value=mock_cursor)

            with patch("app.services.task_recovery.execute_timeout_action", new_callable=AsyncMock) as mock_action:
                await recover_waiting_human_tasks()
                mock_action.assert_called_once_with(
                    task_id="task_timeout",
                    timeout_action="auto_approve",
                )

    @pytest.mark.asyncio
    async def test_recover_non_timed_out_task(self) -> None:
        """Non-timed-out task → left for the periodic sweep task, no action here."""
        now = utc_now()
        future_deadline = now + timedelta(minutes=5)

        task_doc = {
            "_id": "task_pending",
            "status": TaskStatus.WAITING_HUMAN.value,
            "checkpoint": {
                "paused_at_node": "human_2",
                "timeout_deadline": future_deadline.isoformat(),
                "timeout_action": "fail",
            },
        }

        mock_cursor = AsyncCursorMock([task_doc])

        with patch("app.services.task_recovery.get_database") as mock_db:
            mock_collection = MagicMock()
            mock_db.return_value = {"tasks": mock_collection}
            mock_collection.find = MagicMock(return_value=mock_cursor)

            with patch("app.services.task_recovery.execute_timeout_action", new_callable=AsyncMock) as mock_action:
                await recover_waiting_human_tasks()
                # 未超时 → 不立即执行 timeout_action（交给周期巡检）
                mock_action.assert_not_called()

    @pytest.mark.asyncio
    async def test_recover_no_timeout_configured(self) -> None:
        """Task without timeout_deadline → just log, no action."""
        task_doc = {
            "_id": "task_no_timeout",
            "status": TaskStatus.WAITING_HUMAN.value,
            "checkpoint": {
                "paused_at_node": "human_3",
                "timeout_deadline": None,
                "timeout_action": "fail",
            },
        }

        mock_cursor = AsyncCursorMock([task_doc])

        with patch("app.services.task_recovery.get_database") as mock_db:
            mock_collection = MagicMock()
            mock_db.return_value = {"tasks": mock_collection}
            mock_collection.find = MagicMock(return_value=mock_cursor)

            with patch("app.services.task_recovery.execute_timeout_action", new_callable=AsyncMock) as mock_action:
                await recover_waiting_human_tasks()
                # No timeout configured → no action
                mock_action.assert_not_called()


class TestExecuteTimeoutAction:
    """Test execute_timeout_action helper."""

    @pytest.mark.asyncio
    async def test_auto_approve_transitions_to_running(self) -> None:
        """auto_approve → RUNNING + resume."""
        with (
            patch("app.services.task_service.TaskService.transition_task", new_callable=AsyncMock) as mock_transition,
            patch("app.services.task_service.TaskService.resume_task_execution") as mock_resume,
        ):
            await execute_timeout_action("task_1", "auto_approve")

            mock_transition.assert_called_once()
            call_kwargs = mock_transition.call_args.kwargs
            assert call_kwargs["to_status"] == TaskStatus.RUNNING
            mock_resume.assert_called_once_with("task_1")

    @pytest.mark.asyncio
    async def test_fail_transitions_to_failed(self) -> None:
        """fail → FAILED (no resume)."""
        with (
            patch("app.services.task_service.TaskService.transition_task", new_callable=AsyncMock) as mock_transition,
            patch("app.services.task_service.TaskService.resume_task_execution") as mock_resume,
        ):
            await execute_timeout_action("task_1", "fail")

            mock_transition.assert_called_once()
            call_kwargs = mock_transition.call_args.kwargs
            assert call_kwargs["to_status"] == TaskStatus.FAILED
            mock_resume.assert_not_called()

    @pytest.mark.asyncio
    async def test_auto_skip_transitions_to_running(self) -> None:
        """auto_skip → RUNNING + resume."""
        with (
            patch("app.services.task_service.TaskService.transition_task", new_callable=AsyncMock) as mock_transition,
            patch("app.services.task_service.TaskService.resume_task_execution") as mock_resume,
        ):
            await execute_timeout_action("task_1", "auto_skip")

            mock_transition.assert_called_once()
            call_kwargs = mock_transition.call_args.kwargs
            assert call_kwargs["to_status"] == TaskStatus.RUNNING
            mock_resume.assert_called_once_with("task_1")


class TestRecoverOrphanRunningTasks:
    """Test recover_orphan_running_tasks function."""

    @pytest.mark.asyncio
    async def test_no_orphans(self) -> None:
        """No running tasks → nothing to do."""
        # to_list 是 async 方法，用 AsyncMock 返回空列表
        mock_cursor = MagicMock()
        mock_cursor.to_list = AsyncMock(return_value=[])

        with patch("app.services.task_recovery.get_database") as mock_db:
            mock_collection = MagicMock()
            mock_db.return_value = {"tasks": mock_collection}
            mock_collection.find = MagicMock(return_value=mock_cursor)
            with patch("app.services.task_recovery._mark_orphan_running_failed", new_callable=AsyncMock) as mock_mark:
                await recover_orphan_running_tasks()
                mock_mark.assert_not_called()

    @pytest.mark.asyncio
    async def test_orphan_marked_failed_with_stuck_node(self) -> None:
        """running 孤儿任务被标记 failed，且从 timeline 推断卡住节点。"""
        old_time = utc_now() - timedelta(minutes=30)
        task_doc = {
            "_id": "task_orphan",
            "status": TaskStatus.RUNNING.value,
            "updated_at": old_time,
            "timeline": [
                {"event_type": "node_complete", "data": {"node_id": "node_a", "node_type": "agent"}},
                {"event_type": "node_start", "data": {"node_id": "node_b", "node_type": "gateway"}},
            ],
        }
        mock_cursor = MagicMock()
        mock_cursor.to_list = AsyncMock(return_value=[task_doc])

        with patch("app.services.task_recovery.get_database") as mock_db:
            mock_collection = MagicMock()
            mock_db.return_value = {"tasks": mock_collection}
            mock_collection.find = MagicMock(return_value=mock_cursor)
            with patch("app.services.task_recovery._mark_orphan_running_failed", new_callable=AsyncMock) as mock_mark:
                await recover_orphan_running_tasks()
                mock_mark.assert_called_once()
                call_kwargs = mock_mark.call_args.kwargs
                assert call_kwargs["task_id"] == "task_orphan"
                # 最后一条是 node_start node_b → 它就是卡住的节点
                assert call_kwargs["node_id"] == "node_b"
                assert call_kwargs["node_type"] == "gateway"

    @pytest.mark.asyncio
    async def test_recent_running_not_swept(self) -> None:
        """updated_at 在宽限期内（刚启动）的 running 任务不被清理。"""
        # to_list 返回空（因为查询条件 updated_at < cutoff 过滤掉了近期任务）
        mock_cursor = MagicMock()
        mock_cursor.to_list = AsyncMock(return_value=[])

        with patch("app.services.task_recovery.get_database") as mock_db:
            mock_collection = MagicMock()
            mock_db.return_value = {"tasks": mock_collection}
            mock_collection.find = MagicMock(return_value=mock_cursor)
            with patch("app.services.task_recovery._mark_orphan_running_failed", new_callable=AsyncMock) as mock_mark:
                await recover_orphan_running_tasks()
                mock_mark.assert_not_called()
                # 验证查询带了 updated_at < cutoff 条件
                find_kwargs = mock_collection.find.call_args.args[0]
                assert "updated_at" in find_kwargs
                assert "$lt" in find_kwargs["updated_at"]
