"""External API schemas — public-facing responses for API Key consumers."""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field, model_validator

from app.models.agent import RecommendedItem
from app.schemas.file_library import FileRefResponse


class ExtAgentCapabilities(BaseModel):
    """Agent capabilities visible to external systems."""

    tools: list[str] = Field(default_factory=list, description="绑定的工具名称列表")
    workflow_ids: list[str] = Field(default_factory=list, description="绑定的工作流 ID")


class ExtAgentResponse(BaseModel):
    """Agent summary for external list/detail endpoints."""

    id: str
    name: str
    description: str
    capabilities: ExtAgentCapabilities
    default_model: str = ""
    status: str
    welcome_message: str = Field(default="", description="终端用户首屏欢迎词（Markdown）")
    recommended_items: list[RecommendedItem] = Field(
        default_factory=list, description="终端用户首屏推荐问题/操作快捷项"
    )
    voice_enabled: bool = Field(
        default=False, description="该 Agent 是否开启实时语音对话"
    )


class ExtAgentListResponse(BaseModel):
    """Paginated agent list for external API."""

    items: list[ExtAgentResponse]
    total: int
    page: int
    page_size: int


class ExtInvokeRequest(BaseModel):
    """Request body for external Agent invocation."""

    # message 允许为空——"仅附件"轮次(与内部 ExecutionRequest 语义一致)。
    message: str = Field(
        ...,
        max_length=50000,
        description="发送给 Agent 的消息；仅附件轮次可为空",
    )
    display_text: str | None = Field(
        default=None,
        max_length=500,
        description="展示文案（快捷指令 label）。Agent 收到的仍是 message；"
        "气泡/历史/会话标题优先展示该字段",
    )
    session_id: str | None = Field(
        default=None,
        description="会话 ID（不传则自动创建新会话）",
    )
    enable_thinking: bool = Field(
        default=False,
        description="启用 LLM 原生推理（与内部 /v1/agents/*/stream 一致）",
    )
    file_paths: list[str] | None = Field(
        default=None,
        description="本次上传文件相对路径列表（相对 workspace input/ 目录）",
    )
    file_ids: list[str] | None = Field(
        default=None,
        description="本次上传文件 ID 列表",
    )

    @model_validator(mode="after")
    def _require_message_or_files(self) -> ExtInvokeRequest:
        if not self.message.strip() and not self.file_ids and not self.file_paths:
            raise ValueError("message 与 file_ids/file_paths 至少提供其一（仅附件轮次 message 可为空）")
        return self


class ExtInvokeResponse(BaseModel):
    """Response from synchronous Agent invocation via external API."""

    session_id: str
    request_id: str
    reply: str = Field(..., description="Agent 回复文本")
    task_ids: list[str] = Field(default_factory=list, description="触发的 Workflow Task ID 列表")
    files: list[dict] = Field(default_factory=list, description="产出文件引用")


class ExtResumeRequest(BaseModel):
    """Request body for resuming an interrupted Agent."""

    session_id: str = Field(..., description="被中断的会话 ID")
    answer: str = Field(
        ...,
        min_length=1,
        max_length=50000,
        description="对 Agent 追问的回答",
    )
    enable_thinking: bool = Field(
        default=False,
        description="启用 LLM 推理模式（与内部 /v1/agents/*/resume 一致）",
    )


class ExtDismissRequest(BaseModel):
    """Request body for dismissing a pending clarification card.

    关闭待答的 ask_clarification 卡片而不作答：不恢复执行，之后发送的
    消息走普通 invoke 新一轮（与内部 /v1/agents/*/interrupt/dismiss 一致）。
    """

    session_id: str = Field(..., description="待忽略澄清卡片所属的会话 ID")


# ---------------------------------------------------------------------------
# Workflow schemas
# ---------------------------------------------------------------------------


class ExtWorkflowResponse(BaseModel):
    """Workflow summary for external list/detail endpoints."""

    id: str
    name: str
    description: str
    input_schema: dict = Field(default_factory=dict, description="Workflow 输入 Schema")
    status: str
    version: int


class ExtWorkflowDetailResponse(ExtWorkflowResponse):
    """Full Workflow detail including nodes and edges."""

    nodes: list[dict] = Field(default_factory=list)
    edges: list[dict] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)


class ExtWorkflowListResponse(BaseModel):
    """Paginated workflow list for external API."""

    items: list[ExtWorkflowResponse]
    total: int
    page: int
    page_size: int


class ExtWorkflowInvokeRequest(BaseModel):
    """Request body for external Workflow invocation."""

    input: dict = Field(default_factory=dict, description="Workflow 输入数据")
    callback_url: str | None = Field(
        default=None,
        description="完成回调 URL（单次有效）",
    )


class ExtWorkflowInvokeResponse(BaseModel):
    """Response from async Workflow invocation."""

    task_id: str
    status: str
    workflow_id: str
    workflow_version: int


# ---------------------------------------------------------------------------
# Task schemas
# ---------------------------------------------------------------------------


class ExtTaskResponse(BaseModel):
    """Task status for external query."""

    id: str
    workflow_id: str
    workflow_version: str
    status: str
    version: int = 1
    input: dict = Field(default_factory=dict)
    output: dict | None = None
    error: dict | None = None
    checkpoint: dict | None = None
    created_by: str = ""
    created_by_type: str = ""
    created_at: datetime
    updated_at: datetime


# ---------------------------------------------------------------------------
# Session schemas
# ---------------------------------------------------------------------------


class ExtSessionResponse(BaseModel):
    """Session summary for external listing."""

    id: str
    title: str
    created_at: str
    updated_at: str
    message_count: int


class ExtSessionListResponse(BaseModel):
    """Paginated session list for external API."""

    items: list[ExtSessionResponse]
    total: int


class ExtMessageResponse(BaseModel):
    """Message in a session for external API."""

    id: str
    role: str
    content: str
    display_text: str = Field(default="", description="展示文案（快捷指令 label）；空则前端回退 content")
    timeline_entries: list[dict] = Field(default_factory=list)
    created_at: str
    files: list[FileRefResponse] | None = Field(
        default=None,
        description="用户消息携带的上传文件详情（由 file_ids 水合）；无附件时为 None",
    )


class ExtSessionDetailResponse(BaseModel):
    """Session detail with messages for external API."""

    id: str
    title: str
    created_at: str
    updated_at: str
    messages: list[ExtMessageResponse]
