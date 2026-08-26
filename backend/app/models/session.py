"""Session and Message data models for chat conversation persistence."""
from enum import StrEnum

from pydantic import BaseModel, Field
from pydantic.config import ConfigDict

from app.models.base import generate_id, utc_now


class SessionStatus(StrEnum):
    """Session lifecycle status."""

    ACTIVE = "active"
    ARCHIVED = "archived"


class Session(BaseModel):
    """MongoDB session document model.

    A session represents a conversation between a user and an agent.
    Messages are stored in a separate collection (linked by session_id)
    to avoid document size bloat.
    """

    model_config = ConfigDict(populate_by_name=True)

    id: str = Field(default_factory=lambda: generate_id("session"), alias="_id")
    user_id: str = Field(..., description="Owner user ID")
    agent_id: str = Field(..., description="Associated agent ID")
    title: str = Field(default="", max_length=200, description="Session title (first message preview)")
    status: SessionStatus = Field(default=SessionStatus.ACTIVE)
    message_count: int = Field(default=0, ge=0)
    total_tokens: int = Field(default=0, ge=0, description="Cumulative token usage across all agent messages")
    created_at: str = Field(default_factory=lambda: utc_now().isoformat())
    updated_at: str = Field(default_factory=lambda: utc_now().isoformat())


class Message(BaseModel):
    """MongoDB message document model.

    Each message belongs to a session. User messages store plain text
    in ``content``. Agent messages do not use ``content`` — their full
    execution trace (text blocks, tool calls, thinking) lives in
    ``timeline_entries`` (list of structured event dicts).
    """

    model_config = ConfigDict(populate_by_name=True)

    id: str = Field(default_factory=lambda: generate_id("msg"), alias="_id")
    session_id: str = Field(..., description="Parent session ID")
    role: str = Field(..., description="Message role: 'user' or 'agent'")
    content: str = Field(default="", description="Message text content (user messages only)")
    display_content: str = Field(
        default="",
        description="终端用户看到的展示文本（如推荐问题按钮文案）。空则前端回退展示 content；"
        "发给 LLM 的上下文始终用 content，展示文本不参与执行。",
    )
    timeline_entries: list[dict] = Field(
        default_factory=list,
        description="Structured timeline events (thinking/tool_call/tool_result/text) for agent messages",
    )
    token_usage: dict = Field(
        default_factory=dict,
        description="Token metrics for this agent message (total_tokens, input_tokens, output_tokens, llm_calls, etc.)",
    )
    file_ids: list[str] = Field(
        default_factory=list,
        description="Associated FileRef IDs for uploaded attachments",
    )
    created_at: str = Field(default_factory=lambda: utc_now().isoformat())
