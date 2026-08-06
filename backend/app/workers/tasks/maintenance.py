"""Celery tasks — workspace lifecycle and maintenance."""
from loguru import logger

from app.workers.celery_app import celery_app


@celery_app.task(name="app.workers.tasks.maintenance.cleanup_expired_workspaces")
def cleanup_expired_workspaces() -> dict:
    """Periodic task: remove expired workspace files.

    Strategy:
      - ``tmp/`` cleaned after ``WORKSPACE_RETENTION_DAYS / 2`` days.
      - Full workspace removed after ``WORKSPACE_RETENTION_DAYS`` days.

    Scheduled via Celery beat (see celery_app.conf.beat_schedule).
    """
    from app.engine.tool.workspace import WorkspaceManager

    logger.info("celery_task_cleanup_workspaces_start")
    result = WorkspaceManager.cleanup_expired_workspaces()
    logger.info(
        "celery_task_cleanup_workspaces_done",
        **result,
    )
    return result
