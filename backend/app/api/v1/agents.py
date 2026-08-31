"""Agent API endpoints — CRUD operations + execution routing for Agent lifecycle."""
import json
import pathlib
import re

from fastapi import APIRouter, Depends, File, Header, Query, UploadFile
from fastapi.responses import StreamingResponse

from app.core.config import settings
from app.core.errors import (
    ConflictError,
    ForbiddenError,
    NotFoundError,
    ValidationError,
)
from app.core.security import get_current_user, require_permission
from app.models.agent import AgentStatus
from app.models.compat import (
    resolve_default_model,
    resolve_max_retry,
    resolve_skill_ids,
)
from app.schemas.agent import (
    AgentCreate,
    AgentListResponse,
    AgentResponse,
    AgentUpdate,
)
from app.schemas.execution import (
    DismissRequest,
    ExecutionRequest,
    ExecutionResponse,
    PreviewRequest,
    PreviewResponse,
    ResumeRequest,
    StopRequest,
)
from app.schemas.user import UserResponse
from app.services.agent_execution_service import AgentExecutionService
from app.services.agent_service import AgentService
from app.services.run_registry import find_latest_run, find_run, set_cancel_flag

router = APIRouter(
    prefix="/agents",
    tags=["agents"],
    dependencies=[Depends(get_current_user)],
)


def _doc_to_response(doc: dict) -> AgentResponse:
    """Convert a raw MongoDB document to AgentResponse.

    custom_tools 直接返回存储原文(敏感字段为 ``enc:xxx`` 加密形态)。
    前端负责脱敏展示(``****``),并在用户未改动时原样回传 ``enc:xxx``,
    后端据此识别"未更改"(enc: 前缀不重复加密) vs "新值"(加密)。
    """
    default_model = resolve_default_model(doc)
    max_retry = resolve_max_retry(doc)
    max_tokens = doc.get("max_tokens", 0)

    custom_tools_raw = doc.get("custom_tools") or []
    return AgentResponse(
        id=doc["_id"],
        name=doc["name"],
        description=doc.get("description", ""),
        avatar=doc.get("avatar", ""),
        welcome_message=doc.get("welcome_message", ""),
        recommended_items=doc.get("recommended_items", []),
        prompt_slots=doc.get("prompt_slots", {}),
        skill_ids=resolve_skill_ids(doc),
        mcp_connection_ids=doc.get("mcp_connection_ids", []),
        builtin_config=doc.get("builtin_config", []),
        workflow_ids=doc.get("workflow_ids", []),
        custom_tool_ids=[b.get("tool_id", "") for b in custom_tools_raw if b.get("tool_id")],
        custom_tools=[
            {"tool_id": b.get("tool_id", ""), "user_args": b.get("user_args") or {}}
            for b in custom_tools_raw
        ],
        knowledge_base_ids=doc.get("knowledge_base_ids", []),
        default_model=default_model,
        voice_enabled=bool(doc.get("voice_enabled", False)),
        max_retry=max_retry,
        max_tokens=max_tokens,
        status=AgentStatus(doc["status"]),
        created_at=doc["created_at"],
        updated_at=doc["updated_at"],
    )


def _parse_call_chain(x_call_chain: str | None) -> list[str]:
    """Parse the optional external call chain from the X-Call-Chain header."""
    if not x_call_chain:
        return []
    try:
        parsed = json.loads(x_call_chain)
        if isinstance(parsed, list):
            return [str(e) for e in parsed if isinstance(e, str)]
    except (json.JSONDecodeError, TypeError):
        pass
    return []


# ---------------------------------------------------------------------------
# CRUD endpoints
# ---------------------------------------------------------------------------

@router.get(
    "",
    response_model=AgentListResponse,
    summary="List all Agents",
)
async def list_agents(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    name: str | None = Query(None),
    status: str | None = Query(
        None,
        description="Filter by status (draft/published/archived). "
        'Defaults to "published". Use "all" to return every status.',
    ),
    _: UserResponse = Depends(require_permission("agent:read")),
) -> AgentListResponse:
    # Default to published so external consumers (workflow editor, etc.)
    # only see production-ready agents. Management pages pass "all".
    effective_status = None if status == "all" else (status or "published")
    items, total = await AgentService.list_agents(
        page=page, page_size=page_size,
        name=name, status=effective_status,
    )
    return AgentListResponse(
        items=[_doc_to_response(doc) for doc in items],
        total=total, page=page, page_size=page_size,
    )


@router.post(
    "",
    response_model=AgentResponse,
    status_code=201,
    summary="Create a new Agent",
)
async def create_agent(
    body: AgentCreate,
    _: UserResponse = Depends(require_permission("agent:write")),
) -> AgentResponse:
    # 新建 Agent 默认启用全部文件类内建工具(bash/read/write/glob/grep)。
    # 白名单语义不变:这里只是给创建入口一个"默认全选"的初值。
    # 注意默认值收敛在端点层而非 model/schema,避免 duplicate_agent 复制
    # 老 Agent 时把空 builtin_config 意外填满(违背"不迁移老数据"的意图)。
    from app.engine.harness_integration.context import DEFAULT_BUILTIN_CONFIG

    doc = await AgentService.create_agent(
        name=body.name,
        description=body.description,
        builtin_config=list(DEFAULT_BUILTIN_CONFIG),
    )
    return _doc_to_response(doc)


@router.get("/{agent_id}", response_model=AgentResponse, summary="Get Agent details")
async def get_agent(
    agent_id: str,
    _: UserResponse = Depends(require_permission("agent:read")),
) -> AgentResponse:
    doc = await AgentService.get_agent(agent_id)
    if doc is None:
        raise NotFoundError(code="AGENT_NOT_FOUND", message=f"Agent {agent_id} 不存在")
    return _doc_to_response(doc)


@router.put("/{agent_id}", response_model=AgentResponse, summary="Update an Agent")
async def update_agent(
    agent_id: str,
    body: AgentUpdate,
    _: UserResponse = Depends(require_permission("agent:write")),
) -> AgentResponse:
    doc = await AgentService.update_agent(
        agent_id=agent_id,
        name=body.name,
        description=body.description,
        prompt_slots=body.prompt_slots,
        skill_ids=body.skill_ids,
        mcp_connection_ids=body.mcp_connection_ids,
        builtin_config=body.builtin_config,
        workflow_ids=body.workflow_ids,
        custom_tool_ids=body.custom_tool_ids,
        custom_tools=[b.model_dump() for b in body.custom_tools],
        knowledge_base_ids=body.knowledge_base_ids,
        default_model=body.default_model,
        voice_enabled=body.voice_enabled,
        max_retry=body.max_retry,
        max_tokens=body.max_tokens,
        welcome_message=body.welcome_message,
        recommended_items=[item.model_dump() for item in body.recommended_items],
        avatar=body.avatar,
    )
    if doc is None:
        raise NotFoundError(code="AGENT_NOT_FOUND", message=f"Agent {agent_id} 不存在")
    return _doc_to_response(doc)


# 头像上传：MIME/大小校验 + 落盘 + set_avatar（绕开 published 守卫）
_AVATAR_MAX_BYTES = 2 * 1024 * 1024
_AVATAR_ALLOWED_MIME = {"image/png", "image/jpeg", "image/webp"}
_AGENT_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,128}$")


@router.post("/{agent_id}/avatar", summary="Upload Agent avatar image")
async def upload_avatar(
    agent_id: str,
    file: UploadFile = File(...),
    _: UserResponse = Depends(require_permission("agent:write")),
) -> dict:
    # agent 存在性
    if await AgentService.get_agent(agent_id) is None:
        raise NotFoundError(code="AGENT_NOT_FOUND", message=f"Agent {agent_id} 不存在")
    # agent_id 白名单（防路径穿越）
    if not _AGENT_ID_RE.fullmatch(agent_id):
        raise ValidationError(code="INVALID_AGENT_ID", message="非法 agent_id")
    # MIME + 大小
    mime = (file.content_type or "").lower()
    if mime not in _AVATAR_ALLOWED_MIME:
        raise ValidationError(code="AVATAR_TYPE", message="仅支持 PNG / JPEG / WEBP")
    content = await file.read()
    if not content:
        raise ValidationError(code="AVATAR_EMPTY", message="图片内容为空")
    if len(content) > _AVATAR_MAX_BYTES:
        raise ValidationError(code="AVATAR_TOO_LARGE", message="图片不超过 2MB")
    # 落盘（覆盖写，固定 {agent_id}.png；前端已裁剪为 PNG）
    avatars_dir = pathlib.Path(settings.AVATARS_CONTAINER_DIR)
    avatars_dir.mkdir(parents=True, exist_ok=True)
    target = (avatars_dir / f"{agent_id}.png").resolve()
    if avatars_dir.resolve() not in target.parents:
        raise ValidationError(code="AVATAR_PATH", message="文件路径越界")
    target.write_bytes(content)
    # 写回 agent.avatar（相对 URL，前端 <img src> 直接用）
    avatar_url = f"/api/v1/agent-avatars/{agent_id}.png"
    await AgentService.set_avatar(agent_id, avatar_url)
    return {"avatar": avatar_url}


@router.post("/{agent_id}/publish", response_model=AgentResponse, summary="Publish an Agent")
async def publish_agent(
    agent_id: str,
    _: UserResponse = Depends(require_permission("agent:write")),
) -> AgentResponse:
    doc = await AgentService.publish_agent(agent_id)
    if doc is None:
        raise NotFoundError(code="AGENT_NOT_FOUND", message=f"Agent {agent_id} 不存在")
    return _doc_to_response(doc)


@router.post("/{agent_id}/archive", response_model=AgentResponse, summary="Archive an Agent")
async def archive_agent(
    agent_id: str,
    _: UserResponse = Depends(require_permission("agent:write")),
) -> AgentResponse:
    doc = await AgentService.archive_agent(agent_id)
    if doc is None:
        raise NotFoundError(code="AGENT_NOT_FOUND", message=f"Agent {agent_id} 不存在")
    return _doc_to_response(doc)


@router.post(
    "/{agent_id}/duplicate",
    response_model=AgentResponse,
    status_code=201,
    summary="Duplicate an Agent",
)
async def duplicate_agent(
    agent_id: str,
    _: UserResponse = Depends(require_permission("agent:write")),
) -> AgentResponse:
    doc = await AgentService.duplicate_agent(agent_id)
    return _doc_to_response(doc)


@router.delete("/{agent_id}", status_code=204, summary="Delete an Agent")
async def delete_agent(
    agent_id: str,
    _: UserResponse = Depends(require_permission("agent:write")),
) -> None:
    deleted = await AgentService.delete_agent(agent_id)
    if not deleted:
        raise NotFoundError(code="AGENT_NOT_FOUND", message=f"Agent {agent_id} 不存在")


# ---------------------------------------------------------------------------
# Preview (dry-run, no LLM call)
# ---------------------------------------------------------------------------

@router.post(
    "/{agent_id}/preview",
    response_model=PreviewResponse,
    summary="Preview Agent prompt & tools (dry-run)",
)
async def preview_agent(
    agent_id: str,
    body: PreviewRequest | None = None,
    _: UserResponse = Depends(require_permission("agent:write")),
) -> PreviewResponse:
    from app.engine.agent.builder import preview_agent as _preview_agent
    from app.schemas.execution import ToolPreview

    if body is None:
        body = PreviewRequest()

    doc = await AgentService.get_agent(agent_id)
    if doc is None:
        raise NotFoundError(code="AGENT_NOT_FOUND", message=f"Agent {agent_id} 不存在")

    result = await _preview_agent(
        agent=doc, user_input=body.input, enable_thinking=body.enable_thinking,
    )
    return PreviewResponse(
        agent_id=agent_id,
        agent_name=doc.get("name", ""),
        model=result["model"],
        system_prompt=result["system_prompt"],
        messages=result["messages"],
        tools=[ToolPreview(**t) for t in result["tools"]],
        tool_summary=result["tool_summary"],
    )


# ---------------------------------------------------------------------------
# Execution endpoints (delegate to AgentExecutionService)
# ---------------------------------------------------------------------------

@router.post(
    "/{agent_id}/invoke",
    response_model=ExecutionResponse,
    summary="Invoke an Agent (sync)",
)
async def invoke_agent(
    agent_id: str,
    body: ExecutionRequest,
    x_call_chain: str | None = Header(None, alias="X-Call-Chain"),
    user: UserResponse = Depends(require_permission("agent:invoke")),
) -> ExecutionResponse:
    """Invoke an Agent synchronously."""
    return await AgentExecutionService.invoke(
        agent_id, body, user.id,
        external_call_chain=_parse_call_chain(x_call_chain),
    )


@router.post(
    "/{agent_id}/stream",
    summary="Invoke an Agent (SSE stream)",
)
async def stream_agent(
    agent_id: str,
    body: ExecutionRequest,
    x_call_chain: str | None = Header(None, alias="X-Call-Chain"),
    user: UserResponse = Depends(require_permission("agent:invoke")),
) -> StreamingResponse:
    """Invoke an Agent and stream results via Server-Sent Events."""
    event_queue, request_id, session_id = await AgentExecutionService.stream(
        agent_id, body, user.id,
        external_call_chain=_parse_call_chain(x_call_chain),
    )

    async def _event_stream():
        try:
            while True:
                item = await event_queue.get()
                if item is None:
                    break
                yield item
        finally:
            # 断连 ≠ 取消：客户端断开（刷新/关页）只结束本渲染管道，
            # 后台 _run() 继续执行并落库，结果不丢。要停止生成请显式
            # 调用 POST /{agent_id}/stop（mid-stream abort）。
            pass

    return StreamingResponse(
        _event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Request-Id": request_id,
            "X-Session-Id": session_id,
        },
    )


@router.post(
    "/{agent_id}/stop",
    summary="Stop an in-flight streaming run (mid-stream abort)",
)
async def stop_agent(
    agent_id: str,
    body: StopRequest,
    user: UserResponse = Depends(require_permission("agent:invoke")),
) -> dict:
    """Stop an in-flight streaming run for this agent.

    两级取消（belt-and-suspenders）：
    1. ``task.cancel()`` —— 立即打断当前 await（LLM token 流 / 工具执行），
       httpx 连接关闭、供应商停止生成；半截回复不进会话历史，checkpoint
       停在上一个完成的 superstep，下一轮对话带完整干净历史重新开始。
    2. Redis 取消标志 —— harness ``cancel_checker`` 在每轮 REACT 迭代边界
       检查，兜住取消信号落在两个 await 之间的竞态窗口。
    """
    run = (
        find_run(body.request_id)
        if body.request_id
        else find_latest_run(agent_id, user.id)
    )

    # 进程内注册表查不到活跃运行（多 uvicorn worker 部署时注册表在别的
    # 进程，或运行刚结束）：只要有明确的 request_id，仍写 Redis 取消标志
    # 做跨 worker 兜底——运行方的 cancel_checker 在迭代边界拾取，取消信号
    # 不依赖进程内句柄。缺省定位（无 request_id）时无法跨 worker，诚实 409。
    if run is None:
        if body.request_id:
            # 跨 worker 兜底：注册表在别的进程，无法校验属主（主路径的
            # run.user_id 校验不可用）。request_id 是服务端生成、不可猜测
            # 的 UUID，作为隐式授权边界——调用方只能取消自己拿到的那个运行。
            await set_cancel_flag(body.request_id)
            return {"stopped": True, "request_id": body.request_id}
        raise ConflictError(
            code="RUN_NOT_ACTIVE",
            message="没有可停止的活跃运行（可能已结束）",
        )
    if run.user_id != user.id:
        # 只允许发起者停止自己的运行（admin 也一样——避免跨用户干扰）。
        raise ForbiddenError(code="RUN_NOT_OWNED", message="只能停止自己发起的运行")

    await set_cancel_flag(run.request_id)  # 迭代边界兜底闸
    run.task.cancel()  # 主取消路径：立即生效
    return {"stopped": True, "request_id": run.request_id}


@router.post(
    "/{agent_id}/resume",
    summary="Resume an interrupted Agent (SSE stream)",
)
async def resume_agent(
    agent_id: str,
    body: ResumeRequest,
    user: UserResponse = Depends(require_permission("agent:invoke")),
) -> StreamingResponse:
    """Resume an agent paused via interrupt (ask_clarification)."""
    event_queue, request_id, session_id = await AgentExecutionService.resume(
        agent_id, body, user.id,
    )

    async def _event_stream():
        try:
            while True:
                item = await event_queue.get()
                if item is None:
                    break
                yield item
        finally:
            # 断连 ≠ 取消（与 stream 端点语义一致）：停止生成请显式调 stop 端点。
            pass

    return StreamingResponse(
        _event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Request-Id": request_id,
            "X-Session-Id": session_id,
        },
    )


@router.post(
    "/{agent_id}/interrupt/dismiss",
    summary="Dismiss the pending clarification card (ask_clarification)",
)
async def dismiss_interrupt(
    agent_id: str,
    body: DismissRequest,
    user: UserResponse = Depends(require_permission("agent:invoke")),
) -> dict:
    """关闭待答的 ask_clarification/confirm_workflow 卡片（不恢复执行）。

    用户不想回答追问（问题不对 / 想直接重新输入或传文件）时使用：
    卡片进入已忽略态，下一次发送走普通 stream 新一轮。
    """
    dismissed = await AgentExecutionService.dismiss_interrupt(agent_id, body, user.id)
    return {"dismissed": dismissed, "session_id": body.session_id}
