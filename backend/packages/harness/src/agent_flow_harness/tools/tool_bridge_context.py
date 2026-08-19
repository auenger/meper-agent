"""ToolBridgeContext + ContextVar — run_code 工具的依赖注入协议。

与 sandbox_context 同模式：宿主在每次主 Agent 执行前
``set_tool_bridge_context()`` 注入当前 Agent 可桥接调用的工具表，
``run_code`` 工具内部 ``get_tool_bridge_context()`` 读取。ContextVar
保证异步任务隔离。

为什么 run_code 需要它：run_code 让 LLM 写一段 Python 代码批量/编排
调用其他工具。工具表在宿主装配（resolve_all_tools）后才存在，而
BUILTIN_TOOLS 是进程级静态单例，无法按 Agent 定制 —— 所以走 ContextVar
在每次执行时动态注入 name → BaseTool 映射。
"""
from __future__ import annotations

import contextvars
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from langchain_core.tools import BaseTool


@dataclass
class ToolBridgeContext:
    """run_code 执行时的工具桥接依赖。

    Attributes:
        tools_map: 可被代码内 ``tools.call`` 桥接调用的工具表
            （name → BaseTool）。宿主应预先排除危险工具
            （run_code 自身 / HITL / 子代理 / 文件 shell 类）。
        restricted: True 时代码跑在受限 globals（builtins/import 白名单）。
        call_timeout: 单次内层工具调用的超时秒数。
        stats: 内层调用统计（tool_name → {"ok": n, "error": n}），
            由 run_code 执行引擎在调用过程中累加，用于结果摘要。
    """

    tools_map: dict[str, "BaseTool"]
    restricted: bool = True
    call_timeout: float = 60.0
    overall_timeout: float = 180.0
    max_output_bytes: int = 50_000
    stats: dict[str, dict[str, int]] = field(default_factory=dict)


_tool_bridge_ctx: contextvars.ContextVar[ToolBridgeContext | None] = (
    contextvars.ContextVar("tool_bridge_ctx", default=None)
)


def set_tool_bridge_context(
    ctx: ToolBridgeContext,
) -> "contextvars.Token[ToolBridgeContext | None]":
    return _tool_bridge_ctx.set(ctx)


def reset_tool_bridge_context(
    token: "contextvars.Token[ToolBridgeContext | None]",
) -> None:
    _tool_bridge_ctx.reset(token)


def get_tool_bridge_context() -> ToolBridgeContext:
    """读取当前工具桥接 context。未设置 raise RuntimeError。"""
    ctx = _tool_bridge_ctx.get()
    if ctx is None:
        msg = (
            "ToolBridgeContext not set: call set_tool_bridge_context() before "
            "invoking the run_code tool."
        )
        raise RuntimeError(msg)
    return ctx


def record_call_stat(ctx: ToolBridgeContext, tool_name: str, ok: bool) -> None:
    """累加一次内层调用统计（仅事件循环侧调用，无需加锁）。"""
    stat = ctx.stats.setdefault(tool_name, {"ok": 0, "error": 0})
    stat["ok" if ok else "error"] += 1


def format_call_stats(ctx: ToolBridgeContext) -> str:
    """把内层调用统计格式化为单行摘要（无调用时返回空串）。"""
    if not ctx.stats:
        return ""
    parts: list[str] = []
    for name, stat in ctx.stats.items():
        ok, err = stat["ok"], stat["error"]
        if err:
            parts.append(f"{name} x{ok + err} ({ok} ok, {err} failed)")
        else:
            parts.append(f"{name} x{ok} (ok)")
    return "; ".join(parts)


__all__ = [
    "ToolBridgeContext",
    "set_tool_bridge_context",
    "reset_tool_bridge_context",
    "get_tool_bridge_context",
    "record_call_stat",
    "format_call_stats",
]
