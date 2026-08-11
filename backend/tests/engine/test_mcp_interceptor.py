"""Tests for the MCP _user_token_interceptor — 凭证兑换分流逻辑。

覆盖三条路径：
1. 内部路径（token_record_id 空）→ 透传，用静态凭证
2. 外部路径 + 有绑定 → 兑换凭证，override headers
3. 外部路径 + 未绑定 → 抛 PermissionError（不降级）
"""
from unittest.mock import AsyncMock

import pytest
from agent_flow_harness.mcp import loader as mcp_loader
from agent_flow_harness.mcp.loader import (
    _cred_to_headers,
    _user_token_interceptor,
    set_credential_resolver,
)
from agent_flow_harness.mcp.user_token_context import (
    reset_token_record_id_context,
    set_token_record_id_context,
)


class _FakeRequest:
    """模拟 MCPToolCallRequest（含 server_name + override）。"""

    def __init__(self, server_name: str = "github", headers: dict | None = None):
        self.server_name = server_name
        self.headers = headers or {}

    def override(self, **kwargs):
        # 简单模拟：返回带新 headers 的新对象
        new = _FakeRequest(self.server_name, kwargs.get("headers", self.headers))
        return new


@pytest.fixture(autouse=True)
def _reset_resolver_and_ctx():
    """每个测试前后清理全局 resolver 和 ContextVar，避免互相污染。"""
    original_resolver = mcp_loader._resolver
    yield
    mcp_loader._resolver = original_resolver


class TestInterceptorInternalPath:
    """内部路径（token_record_id 空）→ 透传，不调 resolver。"""

    async def test_no_record_id_passes_through(self) -> None:
        """内部路径：ContextVar 未 set → 直接透传到 handler。"""
        # 确保没有 set token_record_id（内部路径）
        handler = AsyncMock(return_value="result")
        req = _FakeRequest("github")
        result = await _user_token_interceptor(req, handler)
        assert result == "result"
        handler.assert_awaited_once_with(req)

    async def test_no_resolver_passes_through(self) -> None:
        """有 record_id 但无 resolver（未注入）→ 透传。"""
        set_credential_resolver(None)
        token = set_token_record_id_context("mcptok_01")
        try:
            handler = AsyncMock(return_value="result")
            result = await _user_token_interceptor(_FakeRequest(), handler)
            assert result == "result"
        finally:
            reset_token_record_id_context(token)


class _MockResolver:
    """实现 CredentialResolver Protocol 的可控 mock（resolve 是真 async 方法）。"""

    def __init__(self, return_value=None, side_effect=None):
        self._return_value = return_value
        self._side_effect = side_effect
        self.resolve_calls = []

    async def resolve(self, token_record_id, server_name):
        self.resolve_calls.append((token_record_id, server_name))
        if self._side_effect:
            raise self._side_effect
        return self._return_value


class TestInterceptorExternalPath:
    """外部路径（token_record_id 有值）→ 兑换绑定凭证。"""

    async def test_bound_credential_overrides_headers(self) -> None:
        """有绑定 → resolver 返回凭证 → override headers。"""
        resolver = _MockResolver(
            return_value={"auth_type": "bearer_token", "token": "ghp_bound_token"}
        )
        set_credential_resolver(resolver)

        token = set_token_record_id_context("mcptok_01")
        try:
            captured_req = []

            async def _handler(req):
                captured_req.append(req)
                return "ok"

            await _user_token_interceptor(_FakeRequest("github"), _handler)

            # resolver 被调用，参数正确
            assert resolver.resolve_calls == [("mcptok_01", "github")]
            # handler 收到的 request headers 被覆盖
            assert captured_req[0].headers["Authorization"] == "Bearer ghp_bound_token"
        finally:
            reset_token_record_id_context(token)

    async def test_unbound_mcp_returns_error_result(self) -> None:
        """未绑定该 MCP（resolver 返回 None）→ 返回 isError 结果，不调 handler。"""
        resolver = _MockResolver(return_value=None)
        set_credential_resolver(resolver)

        token = set_token_record_id_context("mcptok_01")
        try:
            handler_called = False

            async def _handler(req):
                nonlocal handler_called
                handler_called = True
                return "should_not_reach"

            result = await _user_token_interceptor(_FakeRequest("unknown_mcp"), _handler)
            assert not handler_called  # handler 没被调用
            # 返回 isError 的 CallToolResult
            assert getattr(result, "isError", False) is True
            assert "未绑定" in str(result.content)
        finally:
            reset_token_record_id_context(token)

    async def test_resolver_exception_returns_error_result(self) -> None:
        """resolver 异常（如 DB 故障）→ 返回 isError 结果，不调 handler。"""
        resolver = _MockResolver(side_effect=RuntimeError("DB down"))
        set_credential_resolver(resolver)

        token = set_token_record_id_context("mcptok_01")
        try:
            handler_called = False

            async def _handler(req):
                nonlocal handler_called
                handler_called = True

            result = await _user_token_interceptor(_FakeRequest("github"), _handler)
            assert not handler_called
            assert getattr(result, "isError", False) is True
            assert "兑换失败" in str(result.content)
        finally:
            reset_token_record_id_context(token)


class TestCredToHeaders:
    """凭证 → HTTP headers 的构造（三种 auth_type）。"""

    def test_bearer_token(self) -> None:
        h = _cred_to_headers({"auth_type": "bearer_token", "token": "xxx"})
        assert h == {"Authorization": "Bearer xxx"}

    def test_bearer_short_alias(self) -> None:
        h = _cred_to_headers({"auth_type": "bearer", "token": "xxx"})
        assert h == {"Authorization": "Bearer xxx"}

    def test_api_key_default_header(self) -> None:
        h = _cred_to_headers({"auth_type": "api_key", "api_key": "yyy"})
        assert h == {"X-API-Key": "yyy"}

    def test_api_key_custom_header(self) -> None:
        h = _cred_to_headers(
            {"auth_type": "api_key", "api_key": "yyy", "header_name": "X-Custom"}
        )
        assert h == {"X-Custom": "yyy"}

    def test_basic(self) -> None:
        import base64

        h = _cred_to_headers(
            {"auth_type": "basic", "username": "u", "password": "p"}
        )
        expected = base64.b64encode(b"u:p").decode()
        assert h == {"Authorization": f"Basic {expected}"}

    def test_none_returns_empty(self) -> None:
        assert _cred_to_headers({"auth_type": "none"}) == {}

    def test_empty_token_no_authorization(self) -> None:
        """bearer_token 但 token 空 → 不加 Authorization 头。"""
        h = _cred_to_headers({"auth_type": "bearer_token", "token": ""})
        assert h == {}
