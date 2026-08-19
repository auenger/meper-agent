"""Tests for sweep_timed_out_human_tasks — the periodic Human-node timeout sweep.

Verifies the scan picks up only waiting_human tasks past their deadline and
hands them to execute_timeout_action, leaving non-timed-out / unconfigured /
other-state tasks alone.
"""
from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock, patch

from app.models.task import TaskStatus, utc_now
from app.workers.tasks.human_timeout import _sweep_async


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


class TestSweepTimedOutHumanTasks:
    """Test _sweep_async — the periodic sweep body."""

    async def test_sweep_executes_timeout_action_for_timed_out_task(self) -> None:
        """A waiting_human task past its deadline → execute_timeout_action."""
        past_deadline = (utc_now() - timedelta(minutes=5)).isoformat()
        task_doc = {
            "_id": "task_timeout",
            "status": TaskStatus.WAITING_HUMAN.value,
            "checkpoint": {
                "paused_at_node": "human_1",
                "timeout_deadline": past_deadline,
                "timeout_action": "fail",
            },
        }

        with patch("app.workers.tasks.human_timeout.get_database") as mock_db:
            mock_collection = MagicMock()
            mock_db.return_value = {"tasks": mock_collection}
            mock_collection.find = MagicMock(return_value=AsyncCursorMock([task_doc]))

            with patch(
                "app.workers.tasks.human_timeout.execute_timeout_action",
                new_callable=AsyncMock,
            ) as mock_action:
                result = await _sweep_async()

                mock_action.assert_awaited_once_with("task_timeout", "fail", checkpoint=task_doc["checkpoint"])
                assert result["swept"] == 1

    async def test_sweep_skips_not_yet_timed_out_task(self) -> None:
        """A waiting_human task with a future deadline → not swept."""
        future_deadline = (utc_now() + timedelta(minutes=5)).isoformat()
        task_doc = {
            "_id": "task_pending",
            "status": TaskStatus.WAITING_HUMAN.value,
            "checkpoint": {
                "paused_at_node": "human_2",
                "timeout_deadline": future_deadline,
                "timeout_action": "fail",
            },
        }

        with patch("app.workers.tasks.human_timeout.get_database") as mock_db:
            mock_collection = MagicMock()
            mock_db.return_value = {"tasks": mock_collection}
            mock_collection.find = MagicMock(return_value=AsyncCursorMock([task_doc]))

            with patch(
                "app.workers.tasks.human_timeout.execute_timeout_action",
                new_callable=AsyncMock,
            ) as mock_action:
                result = await _sweep_async()

                mock_action.assert_not_called()
                assert result["swept"] == 0

    async def test_sweep_skips_task_without_deadline(self) -> None:
        """A waiting_human task with no timeout_deadline → left for a human."""
        task_doc = {
            "_id": "task_no_timeout",
            "status": TaskStatus.WAITING_HUMAN.value,
            "checkpoint": {
                "paused_at_node": "human_3",
                "timeout_deadline": None,
                "timeout_action": "fail",
            },
        }

        with patch("app.workers.tasks.human_timeout.get_database") as mock_db:
            mock_collection = MagicMock()
            mock_db.return_value = {"tasks": mock_collection}
            mock_collection.find = MagicMock(return_value=AsyncCursorMock([task_doc]))

            with patch(
                "app.workers.tasks.human_timeout.execute_timeout_action",
                new_callable=AsyncMock,
            ) as mock_action:
                result = await _sweep_async()

                mock_action.assert_not_called()
                assert result["swept"] == 0

    async def test_sweep_continues_past_failure(self) -> None:
        """One task erroring should not stop the sweep from processing the rest."""
        past = (utc_now() - timedelta(minutes=1)).isoformat()
        docs = [
            {
                "_id": "task_a",
                "status": TaskStatus.WAITING_HUMAN.value,
                "checkpoint": {"timeout_deadline": past, "timeout_action": "fail"},
            },
            {
                "_id": "task_b",
                "status": TaskStatus.WAITING_HUMAN.value,
                "checkpoint": {"timeout_deadline": past, "timeout_action": "auto_reject"},
            },
        ]

        with patch("app.workers.tasks.human_timeout.get_database") as mock_db:
            mock_collection = MagicMock()
            mock_db.return_value = {"tasks": mock_collection}
            mock_collection.find = MagicMock(return_value=AsyncCursorMock(docs))

            with patch(
                "app.workers.tasks.human_timeout.execute_timeout_action",
                new_callable=AsyncMock,
                side_effect=[RuntimeError("boom"), None],
            ) as mock_action:
                result = await _sweep_async()

                # Both attempted; the failure was swallowed, second still counted
                assert mock_action.await_count == 2
                assert result["swept"] == 1  # only the non-failing one

    async def test_sweep_no_candidates(self) -> None:
        """No waiting_human tasks → swept 0, no error."""
        with patch("app.workers.tasks.human_timeout.get_database") as mock_db:
            mock_collection = MagicMock()
            mock_db.return_value = {"tasks": mock_collection}
            mock_collection.find = MagicMock(return_value=AsyncCursorMock([]))

            with patch(
                "app.workers.tasks.human_timeout.execute_timeout_action",
                new_callable=AsyncMock,
            ) as mock_action:
                result = await _sweep_async()

                mock_action.assert_not_called()
                assert result["swept"] == 0
