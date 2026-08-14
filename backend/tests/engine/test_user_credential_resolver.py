"""Tests for UserCredentialResolver — 账密型换 session（带 Redis 缓存）。

v4 模型（mcp-credential-broker）：纯账密型绑定。resolve() 流程：
server_name → conn → 反查 app（find_by_mcp_connection）→
get_binding(platform_user_id, app_id) → _get_or_exchange_session
（Redis 缓存 / POST login_url 换 session）→ 按 conn.auth_type + auth_config 注入。
token 型绑定已废弃。
"""
from unittest.mock import AsyncMock, patch

import pytest
from app.engine.mcp.user_credential_resolver import UserCredentialResolver

# 复用的 conn / app / binding 夹具
_CONN = {
    "_id": "mcp_conn_01",
    "auth_type": "bearer_token",
    "auth_config": {},
}
_APP = {
    "_id": "app_01",
    "login_config": {
        "login_url": "https://oa.example.com/login",
        "method": "POST",
        "username_field": "username",
        "password_field": "password",
        "token_jsonpath": "data.token",
        "session_ttl": 1800,
    },
}
_BINDING = {"username": "admin", "password": "pass"}


class TestResolve:
    """resolve 主流程：conn → app → binding → session。"""

    async def test_empty_args_returns_none(self) -> None:
        """platform_user_id 或 server_name 为空 → None。"""
        resolver = UserCredentialResolver()
        assert await resolver.resolve("", "oa_system") is None
        assert await resolver.resolve("user_platform_01", "") is None

    async def test_unknown_server_returns_none(self) -> None:
        """server_name 找不到对应 connection → None。"""
        resolver = UserCredentialResolver()
        with patch.object(
            UserCredentialResolver,
            "_get_connection_by_name",
            AsyncMock(return_value=None),
        ):
            result = await resolver.resolve("user_platform_01", "unknown")
        assert result is None

    async def test_no_application_returns_none(self) -> None:
        """MCP 不在任何应用 → None（不能被外部用户调用）。"""
        resolver = UserCredentialResolver()
        with patch.object(
            UserCredentialResolver,
            "_get_connection_by_name",
            AsyncMock(return_value=_CONN),
        ), patch(
            "app.services.application_service.ApplicationService.find_by_mcp_connection",
            AsyncMock(return_value=None),
        ):
            result = await resolver.resolve("user_platform_01", "oa_system")
        assert result is None

    async def test_unbound_returns_none(self) -> None:
        """用户未绑定该应用（get_binding 返回 None）→ None。"""
        resolver = UserCredentialResolver()
        with patch.object(
            UserCredentialResolver,
            "_get_connection_by_name",
            AsyncMock(return_value=_CONN),
        ), patch(
            "app.services.application_service.ApplicationService.find_by_mcp_connection",
            AsyncMock(return_value=_APP),
        ), patch(
            "app.services.user_mcp_credential_service.UserMcpCredentialService.get_binding",
            AsyncMock(return_value=None),
        ):
            result = await resolver.resolve("user_platform_01", "oa_system")
        assert result is None

    async def test_cached_session_returned_directly(self) -> None:
        """Redis 缓存命中 → 直接返回缓存的 session，不调 login。"""
        resolver = UserCredentialResolver()
        with patch.object(
            UserCredentialResolver,
            "_get_connection_by_name",
            AsyncMock(return_value=_CONN),
        ), patch(
            "app.services.application_service.ApplicationService.find_by_mcp_connection",
            AsyncMock(return_value=_APP),
        ), patch(
            "app.services.user_mcp_credential_service.UserMcpCredentialService.get_binding",
            AsyncMock(return_value=_BINDING),
        ), patch(
            "app.services.user_mcp_credential_service.get_cached_session",
            AsyncMock(return_value="cached_session_token"),
        ), patch.object(
            UserCredentialResolver,
            "_do_login",
            AsyncMock(return_value="should_not_be_called"),
        ):
            result = await resolver.resolve("user_platform_01", "oa_system")

        assert result is not None
        assert result["token"] == "cached_session_token"
        assert result["auth_type"] == "bearer_token"
        assert result["header_name"] == "X-API-Key"

    async def test_cache_miss_calls_login_and_caches(self) -> None:
        """缓存 miss → 取 login_config → POST login → 写缓存 → 返回。"""
        resolver = UserCredentialResolver()
        with patch.object(
            UserCredentialResolver,
            "_get_connection_by_name",
            AsyncMock(return_value=_CONN),
        ), patch(
            "app.services.application_service.ApplicationService.find_by_mcp_connection",
            AsyncMock(return_value=_APP),
        ), patch(
            "app.services.user_mcp_credential_service.UserMcpCredentialService.get_binding",
            AsyncMock(return_value=_BINDING),
        ), patch(
            "app.services.user_mcp_credential_service.get_cached_session",
            AsyncMock(return_value=None),  # cache miss
        ), patch(
            "app.services.user_mcp_credential_service.set_cached_session",
            AsyncMock(),
        ) as mock_set_cache, patch.object(
            UserCredentialResolver,
            "_do_login",
            AsyncMock(return_value="fresh_session_token"),
        ) as mock_login:
            result = await resolver.resolve("user_platform_01", "oa_system")

        assert result is not None
        assert result["token"] == "fresh_session_token"
        mock_login.assert_awaited_once()
        mock_set_cache.assert_awaited_once()
        args = mock_set_cache.call_args.args
        assert args[0] == "user_platform_01"
        assert args[1] == "app_01"
        assert args[2] == "fresh_session_token"
        assert args[3] == 1800

    async def test_no_login_config_returns_none(self) -> None:
        """应用没配 login_config（无 login_url）→ None（无法换 session）。"""
        resolver = UserCredentialResolver()
        app = {"_id": "app_01", "login_config": {}}
        with patch.object(
            UserCredentialResolver,
            "_get_connection_by_name",
            AsyncMock(return_value=_CONN),
        ), patch(
            "app.services.application_service.ApplicationService.find_by_mcp_connection",
            AsyncMock(return_value=app),
        ), patch(
            "app.services.user_mcp_credential_service.UserMcpCredentialService.get_binding",
            AsyncMock(return_value=_BINDING),
        ), patch(
            "app.services.user_mcp_credential_service.get_cached_session",
            AsyncMock(return_value=None),
        ):
            result = await resolver.resolve("user_platform_01", "oa_system")
        assert result is None

    async def test_injects_header_name_from_auth_config(self) -> None:
        """auth_config.header_name 决定注入头。"""
        resolver = UserCredentialResolver()
        conn = {
            **_CONN,
            "auth_config": {"header_name": "X-Auth-Token"},
        }
        with patch.object(
            UserCredentialResolver,
            "_get_connection_by_name",
            AsyncMock(return_value=conn),
        ), patch(
            "app.services.application_service.ApplicationService.find_by_mcp_connection",
            AsyncMock(return_value=_APP),
        ), patch(
            "app.services.user_mcp_credential_service.UserMcpCredentialService.get_binding",
            AsyncMock(return_value=_BINDING),
        ), patch(
            "app.services.user_mcp_credential_service.get_cached_session",
            AsyncMock(return_value="sess"),
        ):
            result = await resolver.resolve("user_platform_01", "oa_system")

        assert result is not None
        assert result["header_name"] == "X-Auth-Token"


class TestDoLogin:
    """_do_login 的 HTTP 调用 + jsonpath 解析。"""

    async def test_successful_login_extracts_token(self) -> None:
        """正常登录：POST 成功 → 按 token_jsonpath 取 token。"""
        resolver = UserCredentialResolver()
        login_config = {
            "login_url": "https://oa.example.com/login",
            "method": "POST",
            "token_jsonpath": "data.access_token",
        }

        class _FakeResp:
            def raise_for_status(self):
                pass

            def json(self):
                return {"data": {"access_token": "sess_abc"}, "status": "ok"}

        class _FakeClient:
            def __init__(self, *a, **kw):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                pass

            async def request(self, method, url, **kw):
                return _FakeResp()

        with patch(
            "app.engine.mcp.user_credential_resolver.httpx.AsyncClient", _FakeClient
        ):
            token = await resolver._do_login(login_config, "admin", "pass123")

        assert token == "sess_abc"

    async def test_missing_token_path_raises(self) -> None:
        """响应成功但找不到 token_jsonpath → 抛 PermissionError。"""
        resolver = UserCredentialResolver()
        login_config = {
            "login_url": "https://oa.example.com/login",
            "token_jsonpath": "data.token",
        }

        class _FakeResp:
            def raise_for_status(self):
                pass

            def json(self):
                # 成功响应但没有 data.token（路径不存在）
                return {"success": True, "data": {"other": "value"}}

        class _FakeClient:
            def __init__(self, *a, **kw):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                pass

            async def request(self, method, url, **kw):
                return _FakeResp()

        with patch(
            "app.engine.mcp.user_credential_resolver.httpx.AsyncClient", _FakeClient
        ), pytest.raises(PermissionError, match="未找到 token 路径"):
            await resolver._do_login(login_config, "admin", "pass123")

    async def test_login_failure_success_false_raises(self) -> None:
        """登录失败（success:false）→ 抛 PermissionError 带原始错误信息。"""
        resolver = UserCredentialResolver()
        login_config = {
            "login_url": "https://oa.example.com/api/admin/login",
            "token_jsonpath": "data.accessToken",
        }

        class _FakeResp:
            def raise_for_status(self):
                pass

            def json(self):
                return {"success": False, "code": 500, "message": "User Name or Password is Invalid!", "data": {}}

        class _FakeClient:
            def __init__(self, *a, **kw):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                pass

            async def request(self, method, url, **kw):
                return _FakeResp()

        with patch(
            "app.engine.mcp.user_credential_resolver.httpx.AsyncClient", _FakeClient
        ), pytest.raises(PermissionError, match="登录失败.*Invalid"):
            await resolver._do_login(login_config, "admin", "pass123")
