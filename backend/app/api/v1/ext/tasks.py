"""External API — Task status query."""
from fastapi import APIRouter, Depends

from app.api.v1.ext import auth_and_rate_limit
from app.core.auth_apikey import ApiKeyPrincipal
from app.core.errors import NotFoundError
from app.schemas.ext_api import ExtTaskResponse
from app.services.task_service import TaskService

router = APIRouter(tags=["external-tasks"])


def _doc_to_ext_task(doc: dict) -> ExtTaskResponse:
    """Convert a Task document to external response format."""
    return ExtTaskResponse(
        id=doc["_id"],
        workflow_id=doc["workflow_id"],
        workflow_version=doc.get("workflow_version", ""),
        status=doc["status"],
        input=doc.get("input", {}),
        output=doc.get("output"),
        error=doc.get("error"),
        created_at=doc.get("created_at", ""),
        updated_at=doc.get("updated_at", ""),
    )


@router.get(
    "/tasks/{task_id}",
    response_model=ExtTaskResponse,
    summary="Query Task status",
)
async def get_task(
    task_id: str,
    principal: ApiKeyPrincipal = Depends(auth_and_rate_limit),
) -> ExtTaskResponse:
    """Query the status of a Task.

    Requires ``executions:read`` scope.
    Only tasks created by this API Key's owner are accessible.
    """
    principal.require_scope("executions:read")

    doc = await TaskService.get_task(task_id)
    if doc is None:
        raise NotFoundError(code="TASK_NOT_FOUND", message="Task not found")

    # Only allow access to tasks created by this API Key's owner. Tasks
    # created directly (Workflow invoke) carry the bare owner id; tasks
    # created by an Agent on a user's behalf carry ``owner:sub`` /
    # ``owner:visitor_id`` — both belong to this owner.
    if not principal.owns_resource(doc.get("created_by")):
        raise NotFoundError(code="TASK_NOT_FOUND", message="Task not found")

    return _doc_to_ext_task(doc)
