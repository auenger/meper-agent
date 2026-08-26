"""Execution-related Pydantic schemas for invoke/stream API."""
from __future__ import annotations

from pydantic import BaseModel, Field


class ExecutionRequest(BaseModel):
    """Request body for agent invoke/stream endpoints."""

    input: str = Field(..., min_length=1, max_length=50000, description="User input text")
    display_text: str | None = Field(
        default=None,
        max_length=500,
        description="展示文案（快捷指令 label）。AI 收到的仍是 input；"
        "气泡/历史/会话标题优先展示该字段，避免后台指令暴露给终端用户",
    )
    session_id: str | None = Field(default=None, description="Optional session ID for context continuity")
    enable_thinking: bool = Field(
        default=False,
        description="启用 LLM 原生推理（Claude extended thinking / OpenAI o-series reasoning_effort）。"
        "不支持的模型会静默降级到普通模式。",
    )
    file_paths: list[str] | None = Field(
        default=None,
        description="本次上传的文件相对路径列表（相对于 workspace input/ 目录）",
    )
    file_ids: list[str] | None = Field(
        default=None,
        description="本次上传的文件 ID 列表",
    )


class ExecutionResponse(BaseModel):
    """Response from a synchronous agent invocation."""

    output: str = Field(..., description="Agent response text")
    execution_path: str = Field(..., description="Selected execution path")
    request_id: str = Field(..., description="Trace ID for this execution")
    agent_id: str = Field(..., description="Agent ID")
    session_id: str = Field(..., description="Associated session ID for this conversation")
    step_count: int = Field(default=0, description="Number of execution steps taken")


class ResumeRequest(BaseModel):
    """Request body for resuming an interrupted agent (ask_clarification)."""

    session_id: str = Field(..., description="被中断的 session ID")
    answer: str = Field(..., min_length=1, max_length=50000, description="用户的回答")
    enable_thinking: bool = Field(default=False, description="启用 LLM 推理模式")


class StopRequest(BaseModel):
    """Request body for stopping an in-flight streaming agent run.

    mid-stream abort：取消进行中的 LLM 生成/工具执行，被取消的轮次不进
    会话历史，用户可直接开始新一轮对话。
    """

    request_id: str | None = Field(
        default=None,
        description="要停止的运行 ID（SSE 响应头 X-Request-Id）。缺省时停止该用户在该 Agent 上的最新活跃运行。",
    )


# ---------------------------------------------------------------------------
# Preview / Dry-run
# ---------------------------------------------------------------------------


class PreviewRequest(BaseModel):
    """Request body for agent preview (dry-run) endpoint."""

    input: str = Field(
        default="Hello",
        max_length=50000,
        description="模拟用户输入（用于组装 messages，不实际调用 LLM）",
    )
    enable_thinking: bool = Field(
        default=False,
        description="是否启用 thinking 模式（影响 LLM 配置预览）",
    )


class ToolPreview(BaseModel):
    """单个工具的预览信息。"""

    name: str = Field(..., description="工具名称")
    type: str = Field(..., description="工具类型: skill / mcp / builtin / workflow")
    description: str = Field(default="", description="工具描述")
    source: str = Field(default="", description="来源标识（skill 名称 / MCP 连接名 / builtin 名称）")
    input_schema: dict = Field(default_factory=dict, description="输入参数 JSON Schema")


class KnowledgeBasePreview(BaseModel):
    """预览中展示的已绑定知识库摘要。"""

    id: str = Field(..., description="知识库 ID")
    name: str = Field(default="", description="知识库名称")
    type: str = Field(default="tree", description="知识库类型: tree / vector")
    description: str = Field(default="", description="知识库描述")


class PreviewResponse(BaseModel):
    """Agent 执行预览 — 组装完成的 prompt 和 tools 快照。

    不实际调用 LLM，仅返回发送请求前的完整组装结果，
    用于调试和验证 Agent 配置是否正确。
    """

    agent_id: str = Field(..., description="Agent ID")
    agent_name: str = Field(..., description="Agent 名称")
    model: str = Field(default="", description="LLM 模型标识")
    system_prompt: str = Field(default="", description="组装完成的完整系统提示词")
    messages: list[dict] = Field(
        default_factory=list,
        description="组装完成的消息列表（发送给 LLM 前的快照）",
    )
    tools: list[ToolPreview] = Field(
        default_factory=list,
        description="解析完成的所有工具列表",
    )
    tool_summary: dict = Field(
        default_factory=dict,
        description="工具统计摘要，如 {total: 3, skill: 1, mcp: 1, builtin: 1}",
    )
    knowledge_bases: list[KnowledgeBasePreview] = Field(
        default_factory=list,
        description="已绑定的知识库列表（运行时据此注入 kb_search / kb_glob 等检索工具）",
    )
