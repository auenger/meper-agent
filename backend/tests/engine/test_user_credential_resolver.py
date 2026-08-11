"""Tests for UserCredentialResolver — token 型直传 + 账密型换 session（带缓存）。"""
from unittest.mock import AsyncMock, patch

import pytest
from app.engine.mcp.user_credential_resolver import UserCredentialResolver


class TestResolveTokenType:
    """token 型绑定：解密后直接返回。"""

    async def test_resolve_token_returns_decrypted(self) -> None:
        """token 型：service 已解密，resolver 直接返回。"""
        resolver = UserCredentialResolver()
        with patch.object(
            UserCredentialResolver, "_get_connection_id_by_name",
            AsyncMock(return_value="mcp_conn_01"),
        ), patch(
            "app.services.mcp_token_credential_service.McpTokenCredentialService"
        ) as mock_svc:
            mock_svc.resolve_binding = AsyncMock(return_value={
                "credential_type": "token",
                "auth_type": "bearer_token",
                "token": "ghp_secret",  # service 已解密
            })
            result = await resolver.resolve("mcptok_01", "github")

        assert result is not None
        assert result["auth_type"] == "bearer_token"
        assert result["token"] == "ghp_secret"

    async def test_resolve_unknown_server_returns_none(self) -> None:
        """server_name 找不到对应 connection → None。"""
        resolver = UserCredentialResolver()
        with patch.object(
            UserCredentialResolver, "_get_connection_id_by_name",
            AsyncMock(return_value=None),
        ):
            result = await resolver.resolve("mcptok_01", "unknown")
        assert result is None

    async def test_resolve_unbound_returns_none(self) -> None:
        """用户未绑定该 MCP → None。"""
        resolver = UserCredentialResolver()
        with patch.object(
            UserCredentialResolver, "_get_connection_id_by_name",
            AsyncMock(return_value="mcp_conn_01"),
        ), patch(
            "app.services.mcp_token_credential_service.McpTokenCredentialService"
        ) as mock_svc:
            mock_svc.resolve_binding = AsyncMock(return_value=None)
            result = await resolver.resolve("mcptok_01", "github")
        assert result is None


class TestResolvePasswordType:
    """账密型绑定：查 Redis 缓存 / POST login_url 换 session。"""

    async def test_cached_session_returned_directly(self) -> None:
        """Redis 缓存命中 → 直接返回缓存的 session，不调 login。"""
        resolver = UserCredentialResolver()
        with patch.object(
            UserCredentialResolver, "_get_connection_id_by_name",
            AsyncMock(return_value="mcp_conn_01"),
        ), patch(
            "app.services.mcp_token_credential_service.McpTokenCredentialService"
        ) as mock_svc, patch(
            "app.engine.mcp.user_credential_resolver.get_cached_session",
            AsyncMock(return_value="cached_session_token"),
        ):
            mock_svc.resolve_binding = AsyncMock(return_value={
                "credential_type": "password",
                "auth_type": "bearer_token",
                "username": "admin",
                "password": "pass",
            })
            result = await resolver.resolve("mcptok_01", "oa_system")

        assert result is not None
        assert result["token"] == "cached_session_token"

    async def test_cache_miss_calls_login_and_caches(self) -> None:
        """缓存 miss → 取 login_config → POST login → 写缓存 → 返回。"""
        resolver = UserCredentialResolver()
        with patch.object(
            UserCredentialResolver, "_get_connection_id_by_name",
            AsyncMock(return_value="mcp_conn_01"),
        ), patch(
            "app.services.mcp_token_credential_service.McpTokenCredentialService"
        ) as mock_svc, patch(
            "app.engine.mcp.user_credential_resolver.get_cached_session",
            AsyncMock(return_value=None),  # cache miss
        ), patch(
            "app.engine.mcp.user_credential_resolver.set_cached_session",
            AsyncMock(),
        ) as mock_set_cache, patch.object(
            UserCredentialResolver, "_get_login_config",
            AsyncMock(return_value={
                "login_url": "https://oa.example.com/login",
                "method": "POST",
                "body_template": '{"username":"{{username}}","password":"{{password}}"}',
                "token_jsonpath": "data.token",
                "session_ttl": 1800,
            }),
        ), patch.object(
            UserCredentialResolver, "_do_login",
            AsyncMock(return_value="fresh_session_token"),
        ) as mock_login:
            mock_svc.resolve_binding = AsyncMock(return_value={
                "credential_type": "password",
                "auth_type": "bearer_token",
                "username": "admin",
                "password": "pass",
            })
            result = await resolver.resolve("mcptok_01", "oa_system")

        assert result is not None
        assert result["token"] == "fresh_session_token"
        mock_login.assert_awaited_once()
        mock_set_cache.assert_awaited_once()
        args = mock_set_cache.call_args.args
        assert args[0] == "mcptok_01"
        assert args[1] == "mcp_conn_01"
        assert args[2] == "fresh_session_token"

    async def test_no_login_config_returns_none(self) -> None:
        """账密型但 connection 没配 login_config → None（无法换 session）。"""
        resolver = UserCredentialResolver()
        with patch.object(
            UserCredentialResolver, "_get_connection_id_by_name",
            AsyncMock(return_value="mcp_conn_01"),
        ), patch(
            "app.services.mcp_token_credential_service.McpTokenCredentialService"
        ) as mock_svc, patch(
            "app.engine.mcp.user_credential_resolver.get_cached_session",
            AsyncMock(return_value=None),
        ), patch.object(
            UserCredentialResolver, "_get_login_config",
            AsyncMock(return_value=None),
        ):
            mock_svc.resolve_binding = AsyncMock(return_value={
                "credential_type": "password",
                "username": "admin",
                "password": "pass",
            })
            result = await resolver.resolve("mcptok_01", "oa_system")
        assert result is None


class TestDoLogin:
    """_do_login 的 HTTP 调用 + jsonpath 解析。"""

    async def test_successful_login_extracts_token(self) -> None:
        """正常登录：POST 成功 → 按 token_jsonpath 取 token。"""
        resolver = UserCredentialResolver()
        login_config = {
            "login_url": "https://oa.example.com/login",
            "method": "POST",
            "body_template": '{"username":"{{username}}","password":"{{password}}"}',
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
            "body_template": '{"name":"{{username}}","password":"{{password}}"}}',
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
            "body_template": '{"name":"{{username}}","password":"{{password}}"}}',
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
