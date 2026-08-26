"""Tests for MCP tool cache — hit, expiry, invalidation, clear, default_params.

运行时为纯 DB 镜像加载（不连网）：mock Mongo（mcp_connections 版本查询 +
tools 镜像查询）验证构造 / 版本校验 / 降级路径。
"""
from __future__ import annotations

import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from app.engine.tool.mcp_tool_cache import (
    McpToolCache,
    _wrap_tool_with_defaults,
    get_cache,
    get_mcp_tools_cached,
    invalidate_cache,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_tool(name: str = "test_tool"):
    """Create a minimal mock StructuredTool."""
    from langchain_core.tools import StructuredTool
    from pydantic import BaseModel

    class _EmptyArgs(BaseModel):
        pass

    def _fn() -> str:
        """Mock tool function."""
        return "ok"

    return StructuredTool.from_function(
        _fn, name=name, description=f"Mock tool: {name}", args_schema=_EmptyArgs
    )


class _AsyncCursor:
    """Minimal async-iterable cursor mock for Mongo find()."""

    def __init__(self, docs):
        self._docs = list(docs)

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self._docs:
            raise StopAsyncIteration
        return self._docs.pop(0)


def _mock_db(conn_versions=None, mirrors=None):
    """Mock get_database()：mcp_connections.find → 版本行；tools.find → 镜像行。"""
    conns_col = MagicMock()
    conns_col.find = MagicMock(return_value=_AsyncCursor(conn_versions or []))
    tools_col = MagicMock()
    tools_col.find = MagicMock(return_value=_AsyncCursor(mirrors or []))
    db = MagicMock()
    db.__getitem__.side_effect = (
        lambda key: conns_col if key == "mcp_connections" else tools_col
    )
    return db


def _conn_doc(conn_id: str = "conn_1", name: str = "mes", **extra):
    return {
        "_id": conn_id,
        "name": name,
        "url": "http://localhost:8080/mcp",
        "protocol": "sse",
        "auth_type": "none",
        "auth_config": {},
        "timeout": 30,
        "updated_at": "v1",
        **extra,
    }


def _mirror(conn_id: str = "conn_1", name: str = "custom_table_create", **extra):
    return {
        "mcp_connection_id": conn_id,
        "name": name,
        "description": f"Mirror of {name}",
        "input_schema": {
            "type": "object",
            "properties": {"table": {"type": "string"}},
            "required": ["table"],
        },
        **extra,
    }


# ---------------------------------------------------------------------------
# McpToolCache unit tests
# ---------------------------------------------------------------------------


class TestMcpToolCache:
    """Tests for the McpToolCache data structure."""

    def test_miss_on_empty_cache(self):
        cache = McpToolCache()
        assert cache.get(frozenset(["conn_1"])) is None
        assert cache.size == 0

    def test_set_and_get_hit(self):
        cache = McpToolCache()
        key = frozenset(["conn_1", "conn_2"])
        tools = [_make_tool("a"), _make_tool("b")]

        cache.set(key, tools)
        assert cache.size == 1
        result = cache.get(key)
        assert result is not None
        assert len(result) == 2
        assert result[0].name == "a"

    def test_get_expired_returns_none(self):
        cache = McpToolCache(default_ttl=0.01)
        key = frozenset(["conn_1"])
        cache.set(key, [_make_tool()])

        # Wait for expiry
        time.sleep(0.02)
        assert cache.get(key) is None
        assert cache.size == 0

    def test_invalidate_by_connection_id(self):
        cache = McpToolCache()
        key1 = frozenset(["conn_1", "conn_2"])
        key2 = frozenset(["conn_2", "conn_3"])

        cache.set(key1, [_make_tool("a")])
        cache.set(key2, [_make_tool("b")])
        assert cache.size == 2

        # Invalidate conn_2 — should remove both entries
        removed = cache.invalidate("conn_2")
        assert removed == 2
        assert cache.size == 0
        assert cache.get(key1) is None
        assert cache.get(key2) is None

    def test_invalidate_partial_match(self):
        cache = McpToolCache()
        key1 = frozenset(["conn_1"])
        key2 = frozenset(["conn_2"])

        cache.set(key1, [_make_tool("a")])
        cache.set(key2, [_make_tool("b")])

        removed = cache.invalidate("conn_1")
        assert removed == 1
        assert cache.get(key1) is None
        assert cache.get(key2) is not None

    def test_invalidate_no_match(self):
        cache = McpToolCache()
        cache.set(frozenset(["conn_1"]), [_make_tool()])
        removed = cache.invalidate("conn_nonexistent")
        assert removed == 0
        assert cache.size == 1

    def test_clear(self):
        cache = McpToolCache()
        cache.set(frozenset(["a"]), [_make_tool()])
        cache.set(frozenset(["b"]), [_make_tool()])
        assert cache.size == 2

        cache.clear()
        assert cache.size == 0

    def test_set_with_custom_ttl(self):
        cache = McpToolCache(default_ttl=9999)
        key = frozenset(["conn_1"])

        # Set with very short TTL
        cache.set(key, [_make_tool()], ttl=0.01)
        time.sleep(0.02)

        assert cache.get(key) is None


# ---------------------------------------------------------------------------
# Module-level function tests
# ---------------------------------------------------------------------------


class TestModuleFunctions:
    """Tests for module-level convenience functions."""

    def test_get_cache_returns_singleton(self):
        c1 = get_cache()
        c2 = get_cache()
        assert c1 is c2


@pytest.mark.asyncio
async def test_get_mcp_tools_cached_empty_ids():
    """Empty connection list returns empty tools without hitting cache."""
    result = await get_mcp_tools_cached([])
    assert result == []


@pytest.mark.asyncio
async def test_get_mcp_tools_cached_hit():
    """Cache hit + 版本一致 → 返回缓存工具，不触达 DB 镜像构造。"""
    cache = get_cache()
    key = frozenset(["conn_1"])
    cached_tools = [_make_tool("cached_tool")]
    cache.set(key, cached_tools, conn_versions={"conn_1": "v1"})

    with patch(
        "app.db.mongodb.get_database",
        return_value=_mock_db(conn_versions=[{"_id": "conn_1", "updated_at": "v1"}]),
    ):
        result = await get_mcp_tools_cached(["conn_1"])
    assert len(result) == 1
    assert result[0].name == "cached_tool"

    # Cleanup
    cache.clear()


@pytest.mark.asyncio
async def test_get_mcp_tools_cached_version_change_rebuilds():
    """connection updated_at 变化（手动更新/重新 discover）→ 缓存失效重建。"""
    cache = get_cache()
    cache.clear()
    key = frozenset(["conn_1"])
    cache.set(key, [_make_tool("stale_tool")], conn_versions={"conn_1": "v1"})

    # DB 里 updated_at 已变为 v2 → 重建（从镜像构造）
    with patch(
        "app.services.mcp_connection_service.McpConnectionService"
    ) as mock_service, patch(
        "app.db.mongodb.get_database",
        return_value=_mock_db(
            conn_versions=[{"_id": "conn_1", "updated_at": "v2"}],
            mirrors=[_mirror()],
        ),
    ):
        mock_service.get_connection = AsyncMock(return_value=_conn_doc(updated_at="v2"))
        result = await get_mcp_tools_cached(["conn_1"])

    # 重建结果来自镜像（mcp__{server}__{tool} 命名），旧的 stale_tool 被丢弃
    assert [t.name for t in result] == ["mcp__mes__custom_table_create"]
    cache.clear()


@pytest.mark.asyncio
async def test_get_mcp_tools_cached_miss_builds_from_db_mirror():
    """Cache miss → 纯 DB 镜像构造，不连任何 MCP server。"""
    cache = get_cache()
    cache.clear()

    with patch(
        "app.services.mcp_connection_service.McpConnectionService"
    ) as mock_service, patch(
        "app.db.mongodb.get_database",
        return_value=_mock_db(mirrors=[_mirror(), _mirror(name="custom_table_query")]),
    ):
        mock_service.get_connection = AsyncMock(return_value=_conn_doc())
        result = await get_mcp_tools_cached(["conn_1"])

    assert sorted(t.name for t in result) == [
        "mcp__mes__custom_table_create",
        "mcp__mes__custom_table_query",
    ]
    # args_schema 来自镜像 input_schema（JSON Schema dict 形态）
    assert "table" in result[0].args_schema["properties"]

    # 验证已缓存（带版本快照）
    entry = cache.get_entry(frozenset(["conn_1"]))
    assert entry is not None and len(entry.tools) == 2
    assert entry.conn_versions == {"conn_1": "v1"}

    # 全程未触达网络客户端
    cache.clear()


@pytest.mark.asyncio
async def test_get_mcp_tools_cached_no_mirror_skips():
    """无镜像（从未 discover）→ 空工具列表，不抛错、不连网。"""
    cache = get_cache()
    cache.clear()

    with patch(
        "app.services.mcp_connection_service.McpConnectionService"
    ) as mock_service, patch(
        "app.db.mongodb.get_database",
        return_value=_mock_db(mirrors=[]),
    ):
        mock_service.get_connection = AsyncMock(return_value=_conn_doc())
        result = await get_mcp_tools_cached(["conn_1"])

    assert result == []
    # 空结果也缓存（版本一致时下次直接命中，避免反复查）
    assert cache.get(frozenset(["conn_1"])) == []
    cache.clear()


@pytest.mark.asyncio
async def test_get_mcp_tools_cached_connection_not_found():
    """Returns empty list when all connections are not found."""
    cache = get_cache()
    cache.clear()

    with patch(
        "app.services.mcp_connection_service.McpConnectionService"
    ) as mock_service:
        mock_service.get_connection = AsyncMock(return_value=None)

        result = await get_mcp_tools_cached(["conn_missing"])
        assert result == []

    cache.clear()


def test_invalidate_cache_delegates():
    """Module-level invalidate_cache delegates to singleton."""
    cache = get_cache()
    cache.set(frozenset(["conn_test"]), [_make_tool()])
    assert cache.size == 1

    removed = invalidate_cache("conn_test")
    assert removed == 1
    assert cache.size == 0


# ---------------------------------------------------------------------------
# _wrap_tool_with_defaults tests
# ---------------------------------------------------------------------------


def _make_tool_with_args(name: str = "test_tool"):
    """Create a StructuredTool that accepts keyword arguments and records them."""
    from langchain_core.tools import StructuredTool
    from pydantic import BaseModel, Field

    class _Args(BaseModel):
        query: str = Field(default="", description="Search query")
        token: str = Field(default="", description="Auth token")
        limit: int = Field(default=10, description="Max results")

    captured: dict = {}

    def _fn(**kwargs) -> str:
        captured.update(kwargs)
        return "ok"

    tool = StructuredTool.from_function(
        _fn, name=name, description=f"Tool: {name}", args_schema=_Args
    )
    return tool, captured


@pytest.mark.asyncio
async def test_wrap_tool_merges_default_params():
    """Default params are merged into tool invocation."""
    tool, captured = _make_tool_with_args("search")

    wrapped = _wrap_tool_with_defaults(
        tool, {"MyServer": {"token": "secret123", "limit": 5}}
    )

    await wrapped.ainvoke({"query": "hello"})
    assert captured["token"] == "secret123"
    assert captured["limit"] == 5
    assert captured["query"] == "hello"


@pytest.mark.asyncio
async def test_wrap_tool_user_args_override_defaults():
    """User-supplied args take precedence over defaults."""
    tool, captured = _make_tool_with_args("search")

    wrapped = _wrap_tool_with_defaults(
        tool, {"MyServer": {"token": "default_token", "limit": 5}}
    )

    await wrapped.ainvoke({"query": "hello", "token": "user_token"})
    assert captured["token"] == "user_token"
    assert captured["limit"] == 5
    assert captured["query"] == "hello"


def test_wrap_tool_empty_defaults_returns_original():
    """Empty default_params returns the original tool unchanged."""
    tool, _ = _make_tool_with_args("search")

    result = _wrap_tool_with_defaults(tool, {"MyServer": {}})
    assert result is tool

    result = _wrap_tool_with_defaults(tool, {})
    assert result is tool


@pytest.mark.asyncio
async def test_wrap_tool_preserves_metadata():
    """Wrapped tool preserves name, description, and args_schema."""
    tool, _ = _make_tool_with_args("search")

    wrapped = _wrap_tool_with_defaults(
        tool, {"MyServer": {"token": "abc"}}
    )

    assert wrapped.name == "search"
    assert "search" in wrapped.description.lower() or wrapped.description == tool.description
    # args_schema is a dynamic subclass with injected defaults
    assert issubclass(wrapped.args_schema, tool.args_schema)


@pytest.mark.asyncio
async def test_get_mcp_tools_cached_with_default_params():
    """连接配置 default_params → 烘焙进 DB 构造工具的 args_schema 默认值。

    （不实际 ainvoke —— DB 构造的工具执行时会懒连接远程 server，测试里
    只验证 schema 注入。）
    """
    cache = get_cache()
    cache.clear()

    with patch(
        "app.services.mcp_connection_service.McpConnectionService"
    ) as mock_service, patch(
        "app.db.mongodb.get_database",
        return_value=_mock_db(mirrors=[_mirror()]),
    ):
        mock_service.get_connection = AsyncMock(return_value=_conn_doc(
            default_params={"table": "default_table"},
        ))
        result = await get_mcp_tools_cached(["conn_1"])
        assert len(result) == 1

        # default_params 烘焙进 JSON Schema 的 property default
        assert result[0].args_schema["properties"]["table"].get("default") == "default_table"

    cache.clear()
