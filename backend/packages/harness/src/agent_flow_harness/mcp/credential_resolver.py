"""Credential resolver interface — 由 app 层实现并注入 harness。

harness 调 MCP 工具前调 ``resolve()``，拿当前用户在该 MCP 的绑定凭证。
harness 不 import 任何 app 模块（保持可独立发布），app 层在启动时通过
``set_credential_resolver`` 注入实现。

详见 docs/planning-artifacts/mcp-credential-broker-design.md。
"""
from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class CredentialResolver(Protocol):
    """凭证解析器接口 —— app 层实现，运行时按用户 + 目标 MCP 兑换凭证。"""

    async def resolve(
        self,
        platform_user_id: str,
        server_name: str,
    ) -> dict[str, Any] | None:
        """查当前用户（platform_user_id）在目标 MCP（server_name）的绑定凭证。

        Args:
            platform_user_id: 平台用户 ID（外部路径才有值）。
            server_name: 目标 MCP 的 server name（来自 MCPToolCallRequest.server_name,
                对应 MCP connection 的 name 字段）。

        Returns:
            解密后的凭证对象，如：
            - {"auth_type":"bearer_token","token":"xxx"}
            - {"auth_type":"basic","username":"u","password":"p"}
            - {"auth_type":"api_key","api_key":"xxx","header_name":"X-API-Key"}
            返回 None 表示该用户未绑定该 MCP（由 interceptor 决定报错）。
        """
        ...
