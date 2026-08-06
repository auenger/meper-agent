"""Periodic sweep for timed-out Human-node tasks.

Human nodes pause a Task in ``waiting_human`` and persist a
``checkpoint.timeout_deadline``. Previously a process-local ``asyncio`` monitor
was started in the Celery worker to fire the timeout — but the worker's event
loop is only driven for the duration of ``run_until_complete(engine)``, so
once the engine paused and the Celery task returned, the monitor's
``asyncio.sleep`` was never scheduled and the timeout never fired (only the
startup ``recover_waiting_human_tasks`` caught up on restarts).

This module replaces that mechanism with a Celery beat task that periodically
scans for ``waiting_human`` tasks past their ``timeout_deadline`` and executes
the configured ``timeout_action``. Reliability no longer depends on a specific
worker process staying alive — any worker consuming the beat tick will do.
"""
from typing import Any

from loguru import logger

from app.db.mongodb import get_database
from app.models.task import TaskStatus, utc_now
from app.services.task_recovery import execute_timeout_action
from app.workers.celery_app import celery_app
from app.workers.loop import run_async


@celery_app.task(name="app.workers.tasks.human_timeout.sweep_timed_out_human_tasks")
def sweep_timed_out_human_tasks() -> dict[str, Any]:
    """Periodic task: execute timeout_action for timed-out waiting_human tasks.

    Scheduled via Celery beat (see ``celery_app.conf.beat_schedule``). The
    sweep frequency sets the timeout granularity (default 60s).
    """
    return run_async(_sweep_async())


async def _sweep_async() -> dict[str, Any]:
    """Scan waiting_human tasks past their deadline and apply timeout_action."""
    db = get_database()
    now = utc_now()

    # 只过滤 status + 有 checkpoint，deadline 的判断在 Python 层做：因为
    # checkpoint 是经 model_dump(mode="json") 写入的，timeout_deadline 存成
    # ISO 字符串，MongoDB 的 $lte 在字符串/日期混存时不稳；统一在应用层比较。
    cursor = db["tasks"].find({
        "status": TaskStatus.WAITING_HUMAN.value,
        "checkpoint": {"$exists": True, "$ne": None},
    })

    swept = 0
    async for doc in cursor:
        cp = doc.get("checkpoint") or {}
        deadline_raw = cp.get("timeout_deadline")
        if not deadline_raw:
            continue  # 没配超时，等人处理

        if isinstance(deadline_raw, str):
            from datetime import datetime as dt
            deadline = dt.fromisoformat(deadline_raw.replace("Z", "+00:00"))
        else:
            deadline = deadline_raw

        if deadline > now:
            continue  # 还没到点

        task_id = doc["_id"]
        timeout_action = cp.get("timeout_action", "fail")
        try:
            logger.warning(
                "human_timeout_sweep_executing",
                task_id=task_id,
                timeout_action=timeout_action,
                deadline=deadline_raw,
            )
            await execute_timeout_action(task_id, timeout_action)
            swept += 1
        except Exception as exc:
            logger.error(
                "human_timeout_sweep_failed",
                task_id=task_id,
                error=str(exc),
            )

    if swept:
        logger.info("human_timeout_sweep_done", swept=swept)
    return {"swept": swept}
