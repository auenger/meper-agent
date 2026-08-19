"""External API — Task status query, outputs, node timeline, intervention."""
from fastapi import APIRouter, Depends

from app.api.v1.ext import auth_and_rate_limit, resolve_user_id
from app.core.auth_apikey import ApiKeyPrincipal
from app.core.errors import NotFoundError
from app.models.file_library import FileConsumerKind
from app.models.task import TaskStatus
from app.schemas.ext_api import ExtTaskResponse
from app.schemas.file_library import FileRefResponse
from app.schemas.task import (
    NodeTimelineEntry,
    NodeTimelineResponse,
    TaskIntervene,
    TaskInterveneResponse,
)
from app.services.task_service import TaskService

router = APIRouter(tags=["external-tasks"])


def _doc_to_ext_task(doc: dict) -> ExtTaskResponse:
    """Convert a Task document to external response format."""
    return ExtTaskResponse(
        id=doc["_id"],
        workflow_id=doc["workflow_id"],
        workflow_version=doc.get("workflow_version", ""),
        status=doc["status"],
        version=doc.get("version", 1),
        input=doc.get("input", {}),
        output=doc.get("output"),
        error=doc.get("error"),
        checkpoint=doc.get("checkpoint"),
        created_by=doc.get("created_by", ""),
        created_by_type=doc.get("created_by_type", ""),
        created_at=doc["created_at"],
        updated_at=doc["updated_at"],
    )


async def _get_task_or_404(task_id: str) -> dict:
    """Load a Task by id, raising 404 when it does not exist.

    Shared by all task sub-resources (detail / outputs / node timeline /
    intervention). Access is gated by the API Key + X-User-Token
    authentication and the endpoint's scope requirement; no per-task
    ownership filter is applied (created_by may be a platform user, an
    external end-user's platform_user_id, or a channel-encoded id,
    depending on the entry point).
    """
    doc = await TaskService.get_task(task_id)
    if doc is None:
        raise NotFoundError(code="TASK_NOT_FOUND", message="Task not found")
    return doc


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
    """
    principal.require_scope("executions:read")
    doc = await _get_task_or_404(task_id)
    return _doc_to_ext_task(doc)


@router.get(
    "/tasks/{task_id}/outputs",
    response_model=list[FileRefResponse],
    summary="List Task output files",
)
async def list_task_outputs(
    task_id: str,
    principal: ApiKeyPrincipal = Depends(auth_and_rate_limit),
) -> list[dict]:
    """List files produced by an Agent node during Task execution.

    Requires ``executions:read`` scope.
    """
    principal.require_scope("executions:read")
    await _get_task_or_404(task_id)

    from app.services.file_service import FileService
    from app.services.file_storage import LocalFileStorage

    file_service = FileService(LocalFileStorage())
    cursor = file_service._file_refs().find(
        {
            "origin_kind": FileConsumerKind.WORKFLOW_RUN.value,
            "origin_id": task_id,
        },
    ).sort("created_at", -1)
    docs = await cursor.to_list(length=None)
    # Return as dicts so FastAPI can serialize them with the FileRefResponse
    # schema (Pydantic handles the _id → id alias from MongoDB).
    return [FileRefResponse.model_validate(doc).model_dump(mode="json") for doc in docs]


@router.get(
    "/tasks/{task_id}/nodes/{node_id}/timeline",
    response_model=NodeTimelineResponse,
    summary="Get Agent node execution detail",
)
async def get_node_timeline(
    task_id: str,
    node_id: str,
    principal: ApiKeyPrincipal = Depends(auth_and_rate_limit),
) -> NodeTimelineResponse:
    """Return the full execution trace (thinking/tool_call/tool_result/text) of
    an Agent node, read on demand from the LangGraph checkpointer thread.

    Requires ``executions:read`` scope.
    Returns 404 when the node has no checkpoint yet.
    """
    principal.require_scope("executions:read")
    await _get_task_or_404(task_id)

    from app.engine.harness_integration import get_checkpointer
    from app.services.message_converters import messages_to_timeline_entries

    thread_id = f"{task_id}_{node_id}"
    checkpointer = get_checkpointer()
    tuple_ = await checkpointer.aget_tuple({"configurable": {"thread_id": thread_id}})

    if tuple_ is None or not tuple_.checkpoint:
        raise NotFoundError(
            code="NODE_TIMELINE_NOT_FOUND",
            message=f"节点 {node_id} 无执行记录",
        )

    messages = tuple_.checkpoint.get("channel_values", {}).get("messages", [])
    timeline = messages_to_timeline_entries(messages, include_user=True)

    return NodeTimelineResponse(
        task_id=task_id,
        node_id=node_id,
        thread_id=thread_id,
        timeline=[NodeTimelineEntry(**e) for e in timeline],
        message_count=len(messages),
    )


@router.post(
    "/tasks/{task_id}/intervene",
    response_model=TaskInterveneResponse,
    summary="Intervene a Task (approve/reject/skip/cancel/resume/retry)",
)
async def intervene_task(
    task_id: str,
    body: TaskIntervene,
    principal: ApiKeyPrincipal = Depends(auth_and_rate_limit),
) -> TaskInterveneResponse:
    """Intervene in a Task: approve, reject, skip, cancel, resume, retry.

    Requires ``workflows:invoke`` scope (write operation on an execution).

    Core logic is shared with the internal JWT endpoint via
    ``TaskService.intervene``. The actor identity is resolved from the
    API-Key principal (the end-user's platform_user_id).
    """
    principal.require_scope("workflows:invoke")
    await _get_task_or_404(task_id)

    # Resolve end-user identity for attribution (timeline / variables).
    triggered_by = resolve_user_id(principal)

    doc = await TaskService.intervene(
        task_id=task_id,
        action=body.action,
        comment=body.comment,
        version=body.version,
        reason=body.reason,
        target_node_id=body.target_node_id,
        variables=body.variables,
        triggered_by=triggered_by,
        triggered_by_type="api_key",
    )

    action_messages = {
        "approve": "审批通过",
        "reject": "已驳回",
        "skip": "已跳过",
        "cancel": "已取消",
        "resume": "已恢复",
        "retry": "重试中",
        "rewind": "已退回重跑",
    }

    return TaskInterveneResponse(
        task_id=task_id,
        status=TaskStatus(doc["status"]),
        version=doc.get("version", 1),
        message=action_messages.get(body.action, "操作成功"),
    )
