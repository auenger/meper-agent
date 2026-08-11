"""UserCredentialResolver — app 层实现的 MCP 凭证兑换器。

harness 的 _user_token_interceptor（外部路径）调本类的 resolve()，按
(token_record_id, server_name) 查出该用户在该 MCP 的绑定凭证并解密。

两种绑定形态：
- token 型：解密后直接返回（凭证就是目标 MCP 的 token）。
- 账密型：先查 Redis 缓存的 session；miss 则按 connection.login_config
  POST login_url 换 session，写缓存后返回。

server_name → mcp_connection_id 的映射：interceptor 给的是 MCPToolCallRequest
.server_name（= MCP connection 的 name），绑定 key 是 mcp_connection_id，
所以这里先按 name 查 mcp_connections 拿 id，再查绑定。

详见 docs/planning-artifacts/mcp-credential-broker-design.md。
"""
from __future__ import annotations

from typing import Any

import httpx
from app.db.mongodb import get_database
from app.services.mcp_token_credential_service import (
    get_cached_session,
    set_cached_session,
)
from loguru import logger


class UserCredentialResolver:
    """app 层凭证兑换器实现（注入 harness 的 CredentialResolver 位）。"""

    async def resolve(
        self,
        token_record_id: str,
        server_name: str,
    ) -> dict[str, Any] | None:
        """查 + 解密当前用户在目标 MCP 的绑定凭证。

        Returns:
            解密后的凭证对象（含 auth_type + 凭证字段），或 None（未绑定）。
        """
        if not token_record_id or not server_name:
            return None

        # 1. server_name → mcp_connection_id（按 name 查 mcp_connections）
        conn_id = await self._get_connection_id_by_name(server_name)
        if not conn_id:
            logger.warning(
                "mcp_credential_resolve_conn_not_found",
                server_name=server_name,
                record_id=token_record_id,
            )
            return None

        # 2. 查 + 解密绑定（延迟导入避免循环依赖）
        from app.services.mcp_token_credential_service import (
            McpTokenCredentialService,
        )

        binding = await McpTokenCredentialService.resolve_binding(
            token_record_id, conn_id
        )
        if not binding:
            return None  # 未绑定该 MCP

        # 3. 按形态兑换
        credential_type = binding.get("credential_type", "token")
        if credential_type == "token":
            return self._resolve_token(binding)
        elif credential_type == "password":
            return await self._resolve_password(
                token_record_id, conn_id, binding, server_name
            )
        else:
            logger.warning(
                "mcp_credential_unknown_type",
                credential_type=credential_type,
                server_name=server_name,
            )
            return None

    # ------------------------------------------------------------------
    # token 型：直接返回解密后的凭证
    # ------------------------------------------------------------------

    @staticmethod
    def _resolve_token(binding: dict[str, Any]) -> dict[str, Any]:
        """token 型：凭证就是目标 MCP 的 token，直接返回。"""
        return {
            "auth_type": binding.get("auth_type", "bearer_token"),
            "token": binding.get("token", ""),
            "api_key": binding.get("api_key", ""),
            "header_name": binding.get("header_name", "X-API-Key"),
        }

    # ------------------------------------------------------------------
    # 账密型：查/换 session（带 Redis 缓存）
    # ------------------------------------------------------------------

    async def _resolve_password(
        self,
        record_id: str,
        conn_id: str,
        binding: dict[str, Any],
        server_name: str,
    ) -> dict[str, Any]:
        """账密型：先查 Redis 缓存，miss 则 POST login_url 换 session。"""
        # 1. 查缓存
        cached = await get_cached_session(record_id, conn_id)
        if cached:
            return {
                "auth_type": binding.get("auth_type", "bearer_token"),
                "token": cached,
            }

        # 2. miss → 取 connection.login_config + 解密账密 → POST login
        login_config = await self._get_login_config(conn_id)
        if not login_config or not login_config.get("login_url"):
            logger.warning(
                "mcp_credential_login_config_missing",
                conn_id=conn_id,
                server_name=server_name,
            )
            return None

        username = binding.get("username", "")
        password = binding.get("password", "")
        session_token = await self._do_login(login_config, username, password)

        # 3. 写缓存
        ttl = int(login_config.get("session_ttl", 3600))
        await set_cached_session(record_id, conn_id, session_token, ttl)

        return {
            "auth_type": binding.get("auth_type", "bearer_token"),
            "token": session_token,
        }

    @staticmethod
    async def _do_login(
        login_config: dict[str, Any],
        username: str,
        password: str,
    ) -> str:
        """按 login_config POST 登录端点，按 token_jsonpath 取 session token。

        请求体自动构造：用 username_field/password_field 作为字段名（默认
        username/password），填入当前用户的用户名和密码。不需要手写模板。
        """
        login_url = login_config["login_url"]
        method = login_config.get("method", "POST").upper()
        # 字段名可配（默认 username/password），兼容 name/account 等不同系统
        username_field = login_config.get("username_field", "username")
        password_field = login_config.get("password_field", "password")
        token_jsonpath = login_config.get("token_jsonpath", "data.token")

        # 自动构造请求体
        body = {username_field: username, password_field: password}

        async with httpx.AsyncClient(timeout=30) as client:
            if isinstance(body, str):
                resp = await client.request(
                    method, login_url, content=body, headers={"Content-Type": "application/json"}
                )
            else:
                resp = await client.request(method, login_url, json=body)
            resp.raise_for_status()
            data = resp.json()

        # 先校验登录是否成功：只看 success 显式为 false 或有 error/message 含失败关键词。
        # 不按 code 值判断——不同系统的成功 code 含义不同（0/1/200/1000 都可能是成功）。
        if isinstance(data, dict) and data.get("success") is False:
                msg = data.get("message") or data.get("msg") or data.get("error") or "未知错误"
                raise PermissionError(f"MCP 登录失败：{msg}")

        # 按 jsonpath 取 session token（支持 data.access_token 这种点路径）
        current: Any = data
        for part in token_jsonpath.split("."):
            if isinstance(current, dict) and part in current:
                current = current[part]
            else:
                raise PermissionError(
                    f"MCP 登录响应未找到 token 路径 {token_jsonpath}"
                )
        if not current:
            raise PermissionError("MCP 登录响应 token 为空")
        return str(current)

    # ------------------------------------------------------------------
    # DB 辅助查询
    # ------------------------------------------------------------------

    @staticmethod
    async def _get_connection_id_by_name(server_name: str) -> str | None:
        """按 name 查 mcp_connections 的 _id。"""
        db = get_database()
        doc = await db["mcp_connections"].find_one({"name": server_name}, {"_id": 1})
        return doc["_id"] if doc else None

    @staticmethod
    async def _get_login_config(conn_id: str) -> dict[str, Any] | None:
        """按 _id 查 mcp_connections 的 login_config。"""
        db = get_database()
        doc = await db["mcp_connections"].find_one(
            {"_id": conn_id}, {"login_config": 1}
        )
        return doc.get("login_config") if doc else None
