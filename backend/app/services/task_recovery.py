"""Task recovery service — restores waiting_human tasks after server restart.

On startup, scans for tasks stuck in waiting_human status and executes
``timeout_action`` for already-timed-out tasks. Tasks not yet timed out are
left for the periodic beat task ``sweep_timed_out_human_tasks`` to handle
(see ``app/workers/tasks/human_timeout.py``) — there is no longer a process-
local monitor, because the Celery worker's event loop is only driven for the
duration of ``run_until_complete(engine)`` and cannot keep an asyncio.sleep
alive past a Human-node pause.

Also cleans up orphan ``running`` tasks left behind by a process restart:
workflow execution runs in a Celery worker process, so a worker crash/restart
orphans any task still in ``running``. Only ``human`` nodes persist a
checkpoint, so a ``running`` task interrupted mid-``agent``/``gateway``/``tool``
node has no resumable state and is marked ``failed``.
"""
from __future__ import annotations

from typing import Any

from loguru import logger

from app.db.mongodb import get_database
from app.models.task import TaskStatus, utc_now

# running 任务要被视为孤儿，其 updated_at 必须早于「现在 - 此阈值」。
# 阈值留出余量，避免误伤本进程刚启动时正在执行的任务（例如 lifespan 之前
# 由定时调度触发、或上一进程退出与本进程启动几乎同时发生的边界情况）。
ORPHAN_RUNNING_GRACE_SECONDS = 60


async def recover_waiting_human_tasks() -> None:
    """Recover tasks stuck in waiting_human status after server restart.

    Called during application startup (lifespan).

    Steps:
    1. Find all tasks with status=waiting_human that have a checkpoint
    2. For each task already past its timeout_deadline: execute timeout_action
       immediately. Tasks not yet timed out are left for the periodic beat
       task ``sweep_timed_out_human_tasks`` to handle.
    """
    db = get_database()
    now = utc_now()

    cursor = db["tasks"].find({
        "status": TaskStatus.WAITING_HUMAN.value,
        "checkpoint": {"$exists": True, "$ne": None},
    })

    tasks_to_recover: list[dict[str, Any]] = []
    async for doc in cursor:
        tasks_to_recover.append(doc)

    if not tasks_to_recover:
        logger.debug("recover_waiting_human_tasks_none_found")
        return

    logger.info("recover_waiting_human_tasks_starting", count=len(tasks_to_recover))

    for doc in tasks_to_recover:
        task_id = doc["_id"]
        checkpoint = doc.get("checkpoint", {})
        timeout_deadline = checkpoint.get("timeout_deadline")
        timeout_action = checkpoint.get("timeout_action", "fail")

        try:
            if timeout_deadline:
                # Parse deadline (might be string from MongoDB)
                if isinstance(timeout_deadline, str):
                    from datetime import datetime as dt
                    deadline = dt.fromisoformat(timeout_deadline.replace("Z", "+00:00"))
                else:
                    deadline = timeout_deadline

                if deadline <= now:
                    # Already timed out — execute timeout_action immediately
                    logger.warning(
                        "recover_task_already_timed_out",
                        task_id=task_id,
                        timeout_action=timeout_action,
                    )
                    await execute_timeout_action(
                        task_id=task_id,
                        timeout_action=timeout_action,
                        checkpoint=checkpoint if isinstance(checkpoint, dict) else None,
                    )
                else:
                    # Not yet timed out — leave it to the periodic sweep task.
                    logger.info(
                        "recover_task_leave_to_sweep",
                        task_id=task_id,
                        timeout_action=timeout_action,
                    )
            else:
                # No timeout configured — just log
                logger.info(
                    "recover_task_no_timeout",
                    task_id=task_id,
                    paused_node=checkpoint.get("paused_at_node", ""),
                )

        except Exception as exc:
            logger.error(
                "recover_task_failed",
                task_id=task_id,
                error=str(exc),
            )


async def execute_timeout_action(
    task_id: str,
    timeout_action: str,
    checkpoint: dict[str, Any] | None = None,
) -> None:
    """Execute the configured timeout action for a waiting_human task.

    Shared by startup recovery (``recover_waiting_human_tasks``) and the
    periodic sweep task (``sweep_timed_out_human_tasks``).

    Args:
        task_id: The Task ID.
        timeout_action: One of auto_approve, auto_reject, auto_skip, fail.
        checkpoint: The task's checkpoint (callers already hold the task doc).
            Its ``paused_at_node`` is attached to the timeline event so the
            frontend can attribute the timeout decision to the human node.
    """
    from app.services.task_service import TaskService

    action_map = {
        "auto_approve": TaskStatus.RUNNING,
        "auto_reject": TaskStatus.FAILED,
        "auto_skip": TaskStatus.RUNNING,
        "fail": TaskStatus.FAILED,
    }

    target_status = action_map.get(timeout_action, TaskStatus.FAILED)

    _ckpt = checkpoint or {}
    decision_node_data: dict[str, Any] = {}
    if _ckpt.get("paused_at_node"):
        decision_node_data = {"node_id": str(_ckpt["paused_at_node"]), "node_type": "human"}

    await TaskService.transition_task(
        task_id=task_id,
        to_status=target_status,
        triggered_by="system",
        triggered_by_type="system",
        timeline_event_type="timeout",
        timeline_data={
            "timeout_action": timeout_action,
            "message": f"Recovery: 任务超时，执行 {timeout_action}",
            **decision_node_data,
        },
    )

    # For auto_approve / auto_skip, also resume execution
    if target_status == TaskStatus.RUNNING:
        TaskService.resume_task_execution(task_id)


async def recover_orphan_running_tasks() -> None:
    """Mark ``running`` tasks orphaned by a previous process as ``failed``.

    On startup, any task still in ``running`` is an orphan: its execution
    coroutine lived in the previous process and is gone. Only ``human`` nodes
    persist a checkpoint (see ``engine._execute_node``), so a ``running`` task
    interrupted mid-``agent``/``gateway``/``tool`` node has no resumable state
    and would otherwise sit in ``running`` forever.

    To avoid racing with tasks that the *current* process legitimately started
    during/just before lifespan, only tasks whose ``updated_at`` is older than
    ``ORPHAN_RUNNING_GRACE_SECONDS`` are swept.
    """
    db = get_database()
    from datetime import timedelta

    cutoff = utc_now() - timedelta(seconds=ORPHAN_RUNNING_GRACE_SECONDS)

    cursor = db["tasks"].find(
        {"status": TaskStatus.RUNNING.value, "updated_at": {"$lt": cutoff}},
        {"_id": 1, "timeline": {"$slice": -3}, "updated_at": 1},
    )
    orphans = await cursor.to_list(length=None)
    if not orphans:
        logger.debug("recover_orphan_running_tasks_none_found")
        return

    logger.warning("recover_orphan_running_tasks_starting", count=len(orphans))

    for doc in orphans:
        task_id = doc["_id"]
        # 从 timeline 末尾推断卡住的节点（最后一条 node_start 无对应 node_complete）
        stuck_node_id = ""
        stuck_node_type = ""
        timeline = doc.get("timeline") or []
        for ev in reversed(timeline):
            data = ev.get("data") or {}
            if ev.get("event_type") == "node_start":
                stuck_node_id = data.get("node_id", "")
                stuck_node_type = data.get("node_type", "")
                break

        await _mark_orphan_running_failed(
            task_id=task_id,
            node_id=stuck_node_id,
            node_type=stuck_node_type,
        )


async def _mark_orphan_running_failed(
    task_id: str,
    node_id: str,
    node_type: str,
) -> None:
    """Transition an orphaned running task to ``failed`` with a clear error."""
    now = utc_now()
    db = get_database()
    res = await db["tasks"].update_one(
        {"_id": task_id, "status": TaskStatus.RUNNING.value},
        {
            "$set": {
                "status": TaskStatus.FAILED.value,
                "error": {
                    "node_id": node_id,
                    "node_type": node_type,
                    "error_message": "执行中断：后端进程重启导致运行时协程丢失（僵尸任务自动恢复）",
                    "error_code": "ORPHAN_RUNNING_TASK",
                    "timestamp": now,
                },
                "updated_at": now,
            },
            "$inc": {"version": 1},
            "$push": {
                "timeline": {
                    "timestamp": now,
                    "event_type": "failed",
                    "data": {
                        "message": "僵尸任务清理：进程重启后 running 状态无协程推进，标记为失败",
                        "node_id": node_id,
                        "node_type": node_type,
                        "recovery": True,
                    },
                    "actor": "system",
                }
            },
        },
    )
    if res.modified_count:
        logger.warning(
            "orphan_running_task_marked_failed",
            task_id=task_id,
            node_id=node_id,
            node_type=node_type,
        )
    else:
        # 状态已被其他路径推进（例如人工/超时已转移），跳过
        logger.info("orphan_running_task_skipped", task_id=task_id)
