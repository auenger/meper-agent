"""Session and Message API schemas (request/response validation)."""
from pydantic import BaseModel, Field

from app.schemas.file_library import FileRefResponse


class SessionCreate(BaseModel):
    """Request body for creating a new session."""

    agent_id: str = Field(..., min_length=1, description="Agent ID to bind to this session")
    title: str | None = Field(default=None, max_length=200, description="Optional session title")


class SessionResponse(BaseModel):
    """Session data returned in API responses."""

    id: str = Field(..., alias="_id")
    user_id: str
    agent_id: str
    title: str
    status: str
    message_count: int
    total_tokens: int = 0
    created_at: str
    updated_at: str

    model_config = {"populate_by_name": True}


class SessionListResponse(BaseModel):
    """Paginated session list response."""

    items: list[SessionResponse]
    total: int
    page: int
    page_size: int


class MessageResponse(BaseModel):
    """Message data returned in API responses."""

    id: str = Field(..., alias="_id")
    session_id: str
    role: str
    content: str = ""
    # 终端用户看到的展示文本（如推荐问题按钮文案）；空则前端回退展示 content
    display_content: str = ""
    timeline_entries: list[dict] = []
    file_ids: list[str] = []
    files: list[FileRefResponse] = []
    token_usage: dict = Field(default_factory=dict)
    created_at: str
    # 本轮执行请求 id——消息级反馈（§8.2）的轮次键；缺它刷新后前端拿不到按钮
    request_id: str = ""

    model_config = {"populate_by_name": True}


class ChatFileUploadResponse(BaseModel):
    """Response for chat file upload endpoint."""

    file: FileRefResponse
    message: MessageResponse | None = None
    workspace_path: str


class SessionDetailResponse(BaseModel):
    """Session detail with messages."""

    session: SessionResponse
    messages: list[MessageResponse]
