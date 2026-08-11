"""MCP connection Pydantic schemas for API request/response."""
from __future__ import annotations

from pydantic import BaseModel, Field

from app.models.mcp_connection import AuthType, ConnectionStatus


class McpConnectionBase(BaseModel):
    """Base fields for MCP connection create/update."""

    name: str = Field(..., min_length=1, max_length=100, description="连接名称")
    description: str = Field(default="", max_length=500, description="连接描述")
    url: str = Field(..., min_length=1, max_length=500, description="MCP 服务地址")
    protocol: str = Field(
        default="streamable-http",
        description="传输协议：sse / streamable-http",
    )
    auth_type: AuthType = Field(default=AuthType.NONE, description="认证方式")
    auth_config: dict = Field(
        default_factory=dict,
        description="认证配置（如 api_key/bearer_token/username+password）",
    )
    timeout: int = Field(default=30, ge=1, le=300, description="超时秒数")
    default_params: dict = Field(
        default_factory=dict,
        description="工具调用时自动注入的默认参数（如 token, api_key）",
    )
    login_config: dict = Field(
        default_factory=dict,
        description=(
            "账密型绑定的登录端点配置。字段：login_url/method(默认POST)/"
            "body_template(用 {{username}}/{{password}} 占位)/"
            "token_jsonpath(从响应取 session 的路径)/session_ttl(缓存秒，默认3600)。"
            "仅当用户用 username/password 绑定该 MCP 时用到。"
        ),
    )


class McpConnectionCreate(McpConnectionBase):
    """Schema for creating a new MCP connection."""


class McpConnectionUpdate(McpConnectionBase):
    """Schema for updating an existing MCP connection (full PUT)."""


class McpConnectionResponse(BaseModel):
    """MCP connection data returned in API responses.

    ``auth_config`` 中的敏感字段会被脱敏（用 ``***`` 替换），
    以避免 API Key / Token 等密钥泄露到前端。
    """

    id: str
    name: str
    description: str
    url: str
    protocol: str
    auth_type: AuthType
    auth_config: dict
    timeout: int
    default_params: dict
    login_config: dict = Field(default_factory=dict)
    status: ConnectionStatus
    status_message: str
    last_connected_at: str
    tool_count: int
    created_at: str
    updated_at: str


class McpConnectionListResponse(BaseModel):
    """Paginated MCP connection list response."""

    items: list[McpConnectionResponse]
    total: int
    page: int
    page_size: int


class McpTestResult(BaseModel):
    """连接测试结果."""

    success: bool
    server_info: dict = Field(default_factory=dict)
    tool_count: int = 0
    error: str = ""


class McpDiscoverResult(BaseModel):
    """工具发现结果."""

    connection_id: str
    discovered: int = 0
    created: int = 0
    updated: int = 0
    deactivated: int = 0
    tools: list[str] = Field(default_factory=list, description="发现的工具名称列表")
    error: str = ""
