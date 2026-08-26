"""API Key Pydantic schemas for API request/response."""
from __future__ import annotations

from pydantic import BaseModel, Field

from app.models.api_key import ALL_SCOPES, ApiKeyStatus


class ApiKeyBindingsSchema(BaseModel):
    """Resource bindings for API request."""

    agents: list[str] = Field(default_factory=list)
    workflows: list[str] = Field(default_factory=list)


class ApiKeyCreate(BaseModel):
    """Schema for creating a new API Key."""

    name: str = Field(
        ...,
        min_length=1,
        max_length=100,
        description="API Key 显示名称",
        examples=["MES 产线 A"],
    )
    scopes: list[str] = Field(
        ...,
        min_length=1,
        description=f"权限列表，可选值: {', '.join(ALL_SCOPES)}",
        examples=[["agents:invoke", "agents:read", "executions:read"]],
    )
    bindings: ApiKeyBindingsSchema = Field(
        default_factory=ApiKeyBindingsSchema,
        description="资源绑定，空列表表示不限制",
    )
    rate_limit: int = Field(
        default=60,
        ge=1,
        le=10000,
        description="每分钟请求上限",
    )
    introspect_url: str | None = Field(
        default=None,
        description="接入方 token introspection 端点（RFC 7662），用于外部用户身份识别",
    )
    app_id: str = Field(default="", description="绑定的应用 ID")
    expires_at: str | None = Field(
        default=None,
        description="过期时间（ISO 格式），null 表示永不过期",
    )


class ApiKeyUpdate(BaseModel):
    """Schema for updating an API Key."""

    name: str | None = Field(default=None, min_length=1, max_length=100)
    scopes: list[str] | None = None
    bindings: ApiKeyBindingsSchema | None = None
    rate_limit: int | None = Field(default=None, ge=1, le=10000)
    introspect_url: str | None = None
    app_id: str | None = None
    expires_at: str | None = None


class ApiKeyResponse(BaseModel):
    """API Key returned in list/detail responses (no raw key)."""

    id: str
    name: str
    key_prefix: str
    owner_user_id: str
    scopes: list[str]
    bindings: ApiKeyBindingsSchema
    rate_limit: int
    status: ApiKeyStatus
    introspect_url: str | None = None
    app_id: str = ""
    expires_at: str | None
    last_used_at: str | None
    created_at: str
    updated_at: str


class ApiKeyCreateResponse(BaseModel):
    """Response after creating an API Key — includes the raw key (one-time)."""

    id: str
    name: str
    key: str
    key_prefix: str
    owner_user_id: str
    scopes: list[str]
    bindings: ApiKeyBindingsSchema
    rate_limit: int
    status: ApiKeyStatus
    introspect_url: str | None = None
    app_id: str = ""
    expires_at: str | None
    created_at: str


class ApiKeyListResponse(BaseModel):
    """Paginated API Key list response."""

    items: list[ApiKeyResponse]
    total: int
    page: int
    page_size: int


class ApiKeyStatsResponse(BaseModel):
    """Aggregated call statistics for an API Key."""

    api_key_id: str
    total_requests: int
    successful: int
    failed: int
    by_endpoint: dict[str, int]
    last_used_at: str | None
    # Token consumption (from execution_logs aggregate, api_key channel).
    total_tokens: int = 0
    input_tokens: int = 0
    output_tokens: int = 0


class ApiKeyLogItem(BaseModel):
    """Single call log entry (execution_logs row, no _id)."""

    source: str = ""
    user_id: str = ""
    api_key_id: str = ""
    endpoint: str = ""
    agent_id: str = ""
    session_id: str = ""
    request_id: str = ""
    status: str = ""
    status_code: int = 0
    latency_ms: int = 0
    llm_duration_ms: int = 0
    tool_duration_ms: int = 0
    other_duration_ms: int = 0
    ttft_ms: int = 0
    total_tokens: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    llm_calls: int = 0
    timestamp: str = ""


class ApiKeyLogsResponse(BaseModel):
    """Paginated call log list."""

    items: list[ApiKeyLogItem]
    total: int
    page: int
    page_size: int
