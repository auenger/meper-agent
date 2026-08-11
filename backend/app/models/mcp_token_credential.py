"""MCP token credential data model — 终端用户的通用 token + 各 MCP 绑定凭证。

admin 为终端用户创建一条记录：填用户名称 + 绑定 N 个 MCP（每个填 token 或
用户名密码）。MEPER 生成一个通用 token（``meper_`` 前缀），用户拿它通过 client
调 agent；agent 调 MCP 前，兑换器用记录 id 查出该用户对该 MCP 的绑定凭证，
替换 MCP 调用的凭证。

凭证值用 ``enc:`` 前缀 + AES-256-GCM 加密（复用 ``app.core.crypto``）。
``auth_type`` 决定凭证怎么进 HTTP 头（复用 ``mcp_client._build_headers``）。

详见 docs/planning-artifacts/mcp-credential-broker-design.md。
"""
from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.models.base import generate_id, utc_now


class McpTokenStatus(StrEnum):
    """通用 token 记录的生命周期状态。"""

    ACTIVE = "active"
    DISABLED = "disabled"


class McpCredentialType(StrEnum):
    """单个 MCP 绑定的凭证形态。"""

    TOKEN = "token"        # 用户填的是目标 MCP 的 token，运行时直传
    PASSWORD = "password"  # 用户填的是账密，运行时先 POST login_url 换 session


class McpAuthType(StrEnum):
    """兑换出的凭证怎么放进 HTTP 头（对齐 MCP connection 的 auth_type）。"""

    NONE = "none"
    API_KEY = "api_key"
    BEARER_TOKEN = "bearer_token"
    BASIC = "basic"


# 通用 token 的前缀（对齐 API Key 的 af_live_ 风格）
TOKEN_PREFIX = "meper_"


class McpTokenCredential(BaseModel):
    """MongoDB ``mcp_token_credentials`` 文档模型。

    admin 每创建一个通用 token = 一条记录，直接挂该用户的全部 MCP 绑定。
    通用 token 本身就是查绑定的 key（经记录 _id），无中间 user_id 映射。
    """

    model_config = ConfigDict(populate_by_name=True)

    id: str = Field(default_factory=lambda: generate_id("mcptok"), alias="_id")
    name: str = Field(..., min_length=1, max_length=100, description="admin 填的用户名称")
    token: str = Field(
        ...,
        min_length=1,
        description=f"MEPER 生成的通用 token（{TOKEN_PREFIX} 前缀），全局唯一",
    )
    api_key_id: str = Field(default="", description="归属接入方 API Key（可选，隔离/审计）")
    status: McpTokenStatus = Field(default=McpTokenStatus.ACTIVE)
    mcp_bindings: dict[str, dict[str, Any]] = Field(
        default_factory=dict,
        description=(
            "key = mcp_connection_id，value = 绑定凭证对象。两种形态：\n"
            "token 型: {credential_type:'token', auth_type:'bearer', token:'enc:xxx'}\n"
            "账密型:   {credential_type:'password', auth_type:'bearer', "
            "username:'enc:x', password:'enc:y'}"
        ),
    )
    created_at: str = Field(default_factory=lambda: utc_now().isoformat())
    updated_at: str = Field(default_factory=lambda: utc_now().isoformat())
    created_by: str = Field(default="", description="创建该记录的平台 admin user_id")
