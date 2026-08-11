"""MCP token credential request/response schemas."""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from app.models.mcp_token_credential import (
    McpAuthType,
    McpCredentialType,
    McpTokenStatus,
)


class McpBindingInput(BaseModel):
    """创建/更新时单个 MCP 绑定的输入（明文凭证）。"""

    credential_type: McpCredentialType = Field(
        ..., description="token=直传凭证；password=账密换 session"
    )
    auth_type: McpAuthType = Field(
        default=McpAuthType.BEARER_TOKEN,
        description="兑换出的凭证怎么进 HTTP 头（bearer/api_key/basic）",
    )
    # token 型
    token: str | None = Field(default=None, description="目标 MCP 的 token（token 型必填）")
    # 账密型
    username: str | None = Field(default=None, description="用户名（账密型必填）")
    password: str | None = Field(default=None, description="密码（账密型必填）")
    # api_key 型可选自定义 header 名
    header_name: str | None = Field(
        default=None, description="auth_type=api_key 时的自定义 header 名，默认 X-API-Key"
    )


class McpTokenCreate(BaseModel):
    """创建通用 token 记录的请求体。"""

    name: str = Field(..., min_length=1, max_length=100, description="用户名称")
    api_key_id: str = Field(default="", description="归属接入方 API Key（可选）")
    mcp_bindings: dict[str, McpBindingInput] = Field(
        default_factory=dict,
        description="key=mcp_connection_id，value=绑定凭证（明文）",
    )


class McpBindingUpdate(McpBindingInput):
    """更新时的绑定项（与创建同结构，便于复用）。"""

    pass


class McpTokenUpdate(BaseModel):
    """更新通用 token 记录的请求体。"""

    name: str | None = Field(default=None, min_length=1, max_length=100)
    status: McpTokenStatus | None = Field(default=None)
    mcp_bindings: dict[str, McpBindingUpdate] | None = Field(
        default=None,
        description="传入则全量替换（明文凭证）。None=不改",
    )


class McpBindingMasked(BaseModel):
    """脱敏后的绑定（API 响应）。"""

    credential_type: McpCredentialType
    auth_type: McpAuthType
    token: str | None = Field(default=None, description="脱敏后的 token")
    username: str | None = Field(default=None, description="用户名（账密型，通常不脱敏）")
    password: str | None = Field(default=None, description="脱敏后的密码")
    header_name: str | None = Field(default=None)


class McpTokenResponse(BaseModel):
    """通用 token 记录响应（凭证脱敏，token 部分脱敏）。"""

    id: str
    name: str
    token: str = Field(..., description="脱敏后的通用 token")
    api_key_id: str = ""
    status: McpTokenStatus
    mcp_bindings: dict[str, dict[str, Any]] = Field(default_factory=dict)
    created_at: str = ""
    updated_at: str = ""
    created_by: str = ""


class McpTokenCreateResponse(McpTokenResponse):
    """创建专用响应：附带一次性的明文通用 token。"""

    token_plaintext: str = Field(
        ..., description="明文通用 token（仅创建/轮换时返回，请妥善保存）"
    )


class McpTokenListResponse(BaseModel):
    items: list[McpTokenResponse]
    total: int


class McpTokenRotateResponse(BaseModel):
    """轮换响应：返回新 token 明文。"""

    id: str
    token_plaintext: str = Field(..., description="新的明文通用 token")
