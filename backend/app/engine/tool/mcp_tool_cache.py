"""MCP tool cache — 运行时纯 DB 镜像加载 + 版本校验缓存。

运行时**不再连接 MCP server**：StructuredTool 从 ``tools`` 镜像集合构造
（``convert_mcp_tool_to_langchain_tool``，懒连接——真正调用时才建立会话）。
镜像由 ``discover_tools`` 写入（创建连接时自动 + 管理界面手动触发），
因此 DB 里的镜像即"验证过的工具白名单"。连接失联只影响工具调用
（调用时报错），不再拖慢对话加载。

缓存失效（三层）：
- 版本校验（主）：命中时重查各 connection 的 ``updated_at``，任一变化即
  重建 —— 手动更新连接 / 重新 discover 后立即生效，且跨进程安全
  （celery worker 的 workflow 路径同样受益）。
- TTL（兜底）：默认 1 小时（镜像衍生数据的正确性由版本校验保证）。
- ``invalidate_cache(connection_id)``：connection update / delete /
  discover、tools 镜像删除时显式失效。
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from langchain_core.tools import StructuredTool
from loguru import logger

# Default TTL in seconds (1 hour — version check is the primary invalidation)
_DEFAULT_TTL = 3600


@dataclass
class _CacheEntry:
    """A single cache entry with expiry + connection-version metadata."""

    tools: list[StructuredTool]
    # connection_id -> updated_at 快照（构造时）；命中时重查比对，变化即失效
    conn_versions: dict[str, str] = field(default_factory=dict)
    created_at: float = 0.0
    ttl: float = 0.0

    @property
    def is_expired(self) -> bool:
        return (time.monotonic() - self.created_at) > self.ttl


class McpToolCache:
    """Process-level singleton cache for MCP tools.

    Key = ``frozenset`` of MCP connection IDs.
    Value = ``_CacheEntry`` holding the DB-built ``StructuredTool`` list and
    the ``updated_at`` snapshot of every participating connection.
    """

    def __init__(self, default_ttl: float = _DEFAULT_TTL) -> None:
        self._store: dict[frozenset[str], _CacheEntry] = {}
        self._default_ttl = default_ttl

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_entry(self, connection_ids: frozenset[str]) -> _CacheEntry | None:
        """Return the cache entry (tools + versions), or None on miss/expiry."""
        entry = self._store.get(connection_ids)
        if entry is None:
            return None
        if entry.is_expired:
            del self._store[connection_ids]
            logger.debug("mcp_tool_cache_expired", key=_key_repr(connection_ids))
            return None
        return entry

    def get(self, connection_ids: frozenset[str]) -> list[StructuredTool] | None:
        """Return cached tools for *connection_ids*, or ``None`` on miss / expiry."""
        entry = self.get_entry(connection_ids)
        return entry.tools if entry is not None else None

    def set(
        self,
        connection_ids: frozenset[str],
        tools: list[StructuredTool],
        conn_versions: dict[str, str] | None = None,
        ttl: float | None = None,
    ) -> None:
        """Store tools for *connection_ids* with optional TTL override."""
        effective_ttl = ttl if ttl is not None else self._default_ttl
        self._store[connection_ids] = _CacheEntry(
            tools=tools,
            conn_versions=dict(conn_versions or {}),
            created_at=time.monotonic(),
            ttl=effective_ttl,
        )
        logger.debug(
            "mcp_tool_cache_set",
            key=_key_repr(connection_ids),
            tool_count=len(tools),
            ttl=effective_ttl,
        )

    def drop(self, connection_ids: frozenset[str]) -> None:
        """Remove a single cache key (version mismatch / rebuild path)."""
        if self._store.pop(connection_ids, None) is not None:
            logger.debug("mcp_tool_cache_dropped", key=_key_repr(connection_ids))

    def invalidate(self, connection_id: str) -> int:
        """Invalidate all cache entries that include *connection_id*.

        Returns the number of entries removed.
        """
        removed = 0
        keys_to_remove = [
            key for key in self._store if connection_id in key
        ]
        for key in keys_to_remove:
            del self._store[key]
            removed += 1

        if removed:
            logger.debug(
                "mcp_tool_cache_invalidated",
                connection_id=connection_id,
                entries_removed=removed,
            )
        return removed

    def clear(self) -> None:
        """Clear all cached entries."""
        count = len(self._store)
        self._store.clear()
        if count:
            logger.debug("mcp_tool_cache_cleared", entries_cleared=count)

    @property
    def size(self) -> int:
        """Number of active cache entries."""
        return len(self._store)


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_cache = McpToolCache()


def get_cache() -> McpToolCache:
    """Return the process-level ``McpToolCache`` singleton."""
    return _cache


async def _fetch_conn_versions(connection_ids: list[str]) -> dict[str, str]:
    """Fetch ``{connection_id: updated_at}`` for version validation."""
    from app.db.mongodb import get_database

    cursor = get_database()["mcp_connections"].find(
        {"_id": {"$in": connection_ids}}, {"updated_at": 1}
    )
    return {str(doc["_id"]): str(doc.get("updated_at") or "") async for doc in cursor}


async def _build_tools_from_db(
    connection_ids: list[str],
) -> tuple[list[StructuredTool], dict[str, str]]:
    """Construct tools purely from DB mirrors — no network access.

    Mirrors lack ``outputSchema`` / annotations (discover doesn't collect
    them), so DB-built tools just won't carry ``structuredContent`` artifacts;
    name / description / input schema are identical to the remote truth at
    discover time.
    """
    from app.db.mongodb import get_database
    from app.engine.tool.mcp_client import _build_connection_config
    from app.services.mcp_connection_service import McpConnectionService

    connections: dict[str, dict] = {}  # name -> connection config
    conn_versions: dict[str, str] = {}
    conn_default_params: dict[str, dict] = {}
    id_to_name: dict[str, str] = {}
    for conn_id in connection_ids:
        conn_doc = await McpConnectionService.get_connection(conn_id)
        if conn_doc is None:
            logger.warning("mcp_connection_not_found", connection_id=conn_id)
            continue
        conn_versions[conn_id] = str(conn_doc.get("updated_at") or "")
        name = conn_doc.get("name", conn_id)
        id_to_name[conn_id] = name
        connections[name] = _build_connection_config(
            url=conn_doc["url"],
            protocol=conn_doc.get("protocol", "streamable-http"),
            auth_type=conn_doc.get("auth_type", "none"),
            auth_config=conn_doc.get("auth_config", {}),
            timeout=conn_doc.get("timeout", 30),
        )
        dp = conn_doc.get("default_params", {})
        if dp:
            conn_default_params[name] = dp

    tools: list[StructuredTool] = []
    if connections:
        # converter 是 langchain_mcp_adapters 0.2.2 的模块内函数（未在包顶层
        # 导出）——升级库版本时需复核此调用点。
        # 注入 harness 的 _user_token_interceptor —— 外部路径凭证兑换 /
        # 内部路径静态凭证降级，与既有工具调用链路行为一致。
        from agent_flow_harness.mcp.loader import _user_token_interceptor
        from langchain_mcp_adapters.tools import convert_mcp_tool_to_langchain_tool
        from mcp.types import Tool as MCPTool

        mirrors_by_conn: dict[str, list[dict]] = {}
        cursor = get_database()["tools"].find(
            {"mcp_connection_id": {"$in": list(id_to_name)}, "source": "mcp"},
            {"name": 1, "description": 1, "input_schema": 1, "mcp_connection_id": 1},
        )
        async for doc in cursor:
            mirrors_by_conn.setdefault(doc["mcp_connection_id"], []).append(doc)

        for conn_id, conn_name in id_to_name.items():
            mirrors = mirrors_by_conn.get(conn_id) or []
            if not mirrors:
                # 从未 discover（或镜像被清空）——本轮对话该连接工具不可用。
                logger.warning("mcp_no_tools_mirror", connection_id=conn_id, server=conn_name)
                continue
            config = connections[conn_name]
            for m in mirrors:
                mcp_tool = MCPTool(
                    name=m["name"],
                    description=m.get("description") or "",
                    inputSchema=m.get("input_schema") or {"type": "object", "properties": {}},
                )
                tools.append(convert_mcp_tool_to_langchain_tool(
                    None, mcp_tool,
                    connection=config,
                    server_name=conn_name,
                    tool_name_prefix=True,
                    tool_interceptors=[_user_token_interceptor],
                ))

    # Rename to mcp__{server}__{tool} — 与旧运行时命名一致，消费方零改动。
    server_names = set(connections.keys())
    tools = [_rename_tool_to_mcp_prefix(t, server_names) for t in tools]

    # Wrap tools with default_params if any connection has them
    if conn_default_params:
        tools = [_wrap_tool_with_defaults(t, conn_default_params) for t in tools]

    return tools, conn_versions


async def get_mcp_tools_cached(
    connection_ids: list[str],
) -> list[StructuredTool]:
    """Resolve MCP tools from DB mirrors with version-checked caching.

    Never touches the network — tools are built from the ``tools`` mirror
    collection and connect lazily on first invocation. Connections without
    mirrors (never discovered) are skipped with a warning.

    If a connection has ``default_params``, the tools are wrapped to
    automatically merge those defaults on every invocation (user args
    override defaults).
    """
    if not connection_ids:
        return []

    key = frozenset(connection_ids)
    cache = get_cache()

    # Cache hit — validate connection versions before trusting the entry.
    entry = cache.get_entry(key)
    if entry is not None:
        current = await _fetch_conn_versions(connection_ids)
        if current == entry.conn_versions:
            logger.debug(
                "mcp_tool_cache_hit",
                key=_key_repr(key),
                tool_count=len(entry.tools),
            )
            return entry.tools
        # connection updated / re-discovered / deleted elsewhere → rebuild
        cache.drop(key)

    tools, conn_versions = await _build_tools_from_db(connection_ids)
    cache.set(key, tools, conn_versions=conn_versions)
    return tools


def invalidate_cache(connection_id: str) -> int:
    """Convenience wrapper — invalidate cache entries for a connection ID."""
    return get_cache().invalidate(connection_id)


def _rename_tool_to_mcp_prefix(tool: StructuredTool, server_names: set[str]) -> StructuredTool:
    """Rename a tool from ``{server}_{tool}`` to ``mcp__{server}__{tool}``.

    The library's ``tool_name_prefix=True`` gives us ``github_search``.
    This function renames it to ``mcp__github__search`` (matching Claude Code).
    """
    original_name = tool.name

    # Skip if already in mcp__ format
    if original_name.startswith("mcp__"):
        return tool
    if original_name.startswith("mcp_"):
        return tool

    # Find which server this tool belongs to by matching the prefix
    for sn in server_names:
        prefix = f"{sn}_"
        if original_name.startswith(prefix):
            bare_name = original_name[len(prefix):]
            prefixed_name = f"mcp__{sn}__{bare_name}"

            original_func = tool.func
            original_coroutine = tool.coroutine

            def _sync(
                _func: Any = original_func,
                **kwargs: Any,
            ) -> Any:
                return _func(**kwargs)

            async def _async(
                _coro: Any = original_coroutine,
                _func: Any = original_func,
                **kwargs: Any,
            ) -> Any:
                if _coro is not None:
                    return await _coro(**kwargs)
                return _func(**kwargs)

            return StructuredTool.from_function(
                func=_sync,
                coroutine=_async,
                name=prefixed_name,
                description=tool.description,
                args_schema=tool.args_schema,
                response_format=tool.response_format,
            )

    # No matching server prefix found — return unchanged
    return tool


def _wrap_tool_with_defaults(
    tool: StructuredTool,
    conn_default_params: dict[str, dict],
) -> StructuredTool:
    """Wrap a StructuredTool so that ``default_params`` are merged on invoke.

    Strategy: create a new ``args_schema`` whose field defaults already
    contain the injected ``default_params``.  This way LangChain's own
    validation fills them in correctly, and user-supplied values still
    take precedence (they are explicit and override the schema default).
    """
    # Flatten all default_params into a single dict
    merged_defaults: dict[str, Any] = {}
    for dp in conn_default_params.values():
        merged_defaults.update(dp)

    if not merged_defaults:
        return tool

    # Build a new args_schema with default_params baked into field defaults
    new_schema = _inject_schema_defaults(tool.args_schema, merged_defaults)

    original_func = tool.func
    original_coroutine = tool.coroutine

    def _wrapped_sync(**kwargs: Any) -> Any:
        return original_func(**kwargs)

    async def _wrapped_async(**kwargs: Any) -> Any:
        if original_coroutine is not None:
            return await original_coroutine(**kwargs)
        return original_func(**kwargs)

    return StructuredTool.from_function(
        func=_wrapped_sync,
        coroutine=_wrapped_async,
        name=tool.name,
        description=tool.description,
        args_schema=new_schema,
        response_format=tool.response_format,
    )


def _inject_schema_defaults(
    schema: Any,
    defaults: dict[str, Any],
) -> Any:
    """Return a schema with field defaults from *defaults* injected.

    Supports both schema shapes a StructuredTool can carry:
    - JSON Schema dict（DB 镜像 / MCP converter 的原生形态）→ 写进
      ``properties.<field>.default``（拷贝后修改，不污染共享镜像 dict）；
    - pydantic model class → 动态子类覆盖字段默认值。

    Only fields that exist in the schema and are present in *defaults*
    will have their defaults overridden.
    """
    if isinstance(schema, dict):
        new_schema = dict(schema)
        props = dict(schema.get("properties") or {})
        for field_name, default in defaults.items():
            if field_name in props and isinstance(props[field_name], dict):
                field_def = dict(props[field_name])
                field_def["default"] = default
                props[field_name] = field_def
        new_schema["properties"] = props
        return new_schema

    if schema is None:
        return schema

    # Collect field overrides
    field_overrides: dict[str, Any] = {}
    for field_name, _field_info in schema.model_fields.items():
        if field_name in defaults:
            field_overrides[field_name] = defaults[field_name]

    if not field_overrides:
        return schema

    # Create a dynamic subclass with updated defaults
    return type(
        schema.__name__,
        (schema,),
        {
            "__annotations__": getattr(schema, "__annotations__", {}),
            **field_overrides,
        },
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _key_repr(key: frozenset[str]) -> str:
    """Human-readable representation of a cache key."""
    return ",".join(sorted(key))
