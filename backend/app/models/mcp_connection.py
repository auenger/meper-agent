"""MCP connection data model for MongoDB."""
from enum import StrEnum

from pydantic import BaseModel, Field
from pydantic.config import ConfigDict

from app.models.base import generate_id, utc_now


class ConnectionStatus(StrEnum):
    """MCP connection lifecycle status."""

    CONNECTING = "connecting"
    CONNECTED = "connected"
    DISCONNECTED = "disconnected"
    ERROR = "error"


class AuthType(StrEnum):
    """MCP server authentication type."""

    NONE = "none"
    API_KEY = "api_key"
    BEARER_TOKEN = "bearer_token"
    BASIC = "basic"


class McpConnection(BaseModel):
    """MongoDB mcp_connections document model.

    Stores MCP server connection configuration and runtime status.
    Discovered MCP tools are stored in the ``tools`` collection with
    ``source="mcp"`` and ``mcp_connection_id`` linking back here.
    """

    model_config = ConfigDict(populate_by_name=True)

    id: str = Field(default_factory=lambda: generate_id("mcp"), alias="_id")
    name: str = Field(..., min_length=1, max_length=100)
    description: str = Field(default="", max_length=500)
    url: str = Field(..., min_length=1, max_length=500)
    protocol: str = Field(default="streamable-http", description="sse / streamable-http")
    auth_type: AuthType = Field(default=AuthType.NONE)
    auth_config: dict = Field(
        default_factory=dict,
        description="认证配置（api_key/bearer_token/basic 等），敏感信息",
    )
    timeout: int = Field(default=30, ge=1, le=300, description="超时秒数")
    default_params: dict = Field(
        default_factory=dict,
        description="工具调用时自动注入的默认参数（如 token, api_key），LLM 传入的同名参数会覆盖",
    )
    login_config: dict = Field(
        default_factory=dict,
        description=(
            "账密型绑定的登录端点配置（仅当用户用 username/password 绑定时用到）。"
            "含 login_url/method/body_template/token_jsonpath/session_ttl。"
            "详见 docs/planning-artifacts/mcp-credential-broker-design.md。"
        ),
    )
    status: ConnectionStatus = Field(default=ConnectionStatus.DISCONNECTED)
    status_message: str = Field(default="", description="状态详情/错误信息")
    last_connected_at: str = Field(default="")
    tool_count: int = Field(default=0, ge=0)
    created_at: str = Field(default_factory=lambda: utc_now().isoformat())
    updated_at: str = Field(default_factory=lambda: utc_now().isoformat())
