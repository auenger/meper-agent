"""harness execution — stream / invoke / resume entry points.

Each function assembles the harness context (via ``resolve_harness_context``),
builds the graph + config, and executes. ErrorEvent fields are remapped to
match the frontend contract (``message`` → ``content``).
"""
from __future__ import annotations

import contextlib
import time
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from app.engine.harness_integration.adapters.app_event import AppEvent

from app.core.config import settings
from app.core.perf import timed_phase
from app.engine.harness_integration.context import (
    _maybe_migrate_legacy,
    get_checkpointer,
    release_harness_context,
    resolve_harness_context,
)


class _PhaseClock:
    """DEBUG 阶段计时（stream/invoke/resume 共用）：build + exec 两段。

    关闭（settings.DEBUG=false）时 ``phases`` 为 None，所有方法直通，
    ``as_dict()`` 返回 None —— 调用方据此跳过 timing 字段与日志。
    """

    def __init__(self) -> None:
        self._build_t0 = time.perf_counter()
        self._exec_t0: float | None = None
        self.phases: dict[str, int] | None = None
        self.build_ms: int | None = None
        self.exec_ms: int | None = None

    def attach(self, hctx: dict) -> None:
        """resolve_harness_context 之后接入其 _timing_phases（None = 关闭）。"""
        self.phases = hctx.get("_timing_phases")

    def build_done(self) -> None:
        if self.phases is not None:
            self.build_ms = int((time.perf_counter() - self._build_t0) * 1000)

    def exec_start(self) -> None:
        if self.phases is not None:
            self._exec_t0 = time.perf_counter()

    def exec_done(self) -> None:
        if self.phases is not None and self._exec_t0 is not None:
            self.exec_ms = int((time.perf_counter() - self._exec_t0) * 1000)

    def as_dict(self) -> dict | None:
        if self.phases is None:
            return None
        return {
            "build_ms": self.build_ms or 0,
            "exec_ms": self.exec_ms or 0,
            "phases": dict(self.phases),
        }


def _make_event_callback(on_event):
    """Create an adapter that converts AppEvent → dict and remaps error fields."""

    async def _on_event_dict(app_event: AppEvent) -> None:
        data = app_event.model_dump()
        # ErrorEvent 字段是 {message, source},前端契约用 {content}。
        if data.get("type") == "error":
            data["content"] = data.pop("message", "")
        await on_event(data)

    return _on_event_dict


async def _emit_load_errors(hctx, on_event) -> None:
    """把 context 收集的工具/MCP 加载失败作为 error 事件发给前端。

    每条 load_error 发一个 ErrorEvent(source="tool")，前端据此知道
    某个工具因故不可用（如 MCP server 离线、自定义工具构建失败）。
    """
    from app.engine.harness_integration.adapters.app_event import ErrorEvent

    callback = _make_event_callback(on_event)
    for err in hctx.get("load_errors", []):
        evt = ErrorEvent(
            message=f"[{err['tool_name']}] {err['error']}",
            source="tool",
        )
        await callback(evt)


async def stream(
    agent: dict,
    state: dict,
    on_event,
    *,
    enable_thinking: bool = False,
    legacy_records: list[dict] | None = None,
    cancel_checker: Callable[[], Awaitable[bool]] | None = None,
    user_token: str | None = None,
) -> dict:
    """流式执行 harness graph,通过 on_event 推送 AppEvent dict。

    Args:
        cancel_checker: 可选的异步取消检查器（与 invoke 对齐）。传入后
            compress_node 每轮 REACT 迭代检查一次；task.cancel() 打断
            await 链是主取消路径，本检查器是迭代边界的兜底闸门。
        user_token: 外部终端用户 token(回调验证模式),透传给 MCP server。
    """
    from agent_flow_harness import build_agent_graph, build_config

    from app.engine.harness_integration.adapters import stream_events_to_app_events

    clock = _PhaseClock()
    hctx = await resolve_harness_context(
        agent, state, enable_thinking=enable_thinking, user_token=user_token,
    )
    clock.attach(hctx)
    usage_summary: dict = {}
    try:
        # 先发出工具/MCP 加载失败，让用户尽早知道哪些工具不可用
        await _emit_load_errors(hctx, on_event)

        session_id = state.get("session_id", "")
        with timed_phase(clock.phases, "build_compile"):
            graph = build_agent_graph(
                hctx["agent_doc"], checkpointer=get_checkpointer(),
                middleware=hctx["middlewares"], tools=hctx["tools"],
            )
            config = build_config(
                hctx["agent_doc"],
                hctx["llm"],
                tools=hctx["tools"],
                context_window=hctx["context_window"],
                middlewares=hctx["middlewares"],
                thread_id=session_id,
                cancel_checker=cancel_checker,
                tool_output_reference_formatter=hctx["tool_output_reference_formatter"],
                protected_turns=hctx["protected_turns"],
                compression_threshold=hctx["compression_threshold"],
                hard_limit_ratio=hctx["hard_limit_ratio"],
                recursion_limit=settings.AGENT_RECURSION_LIMIT,
            )
        await _maybe_migrate_legacy(graph, config, legacy_records)
        clock.build_done()

        clock.exec_start()
        event_stream = graph.astream_events(state, config=config, version="v2")
        try:
            await stream_events_to_app_events(
                event_stream,
                _make_event_callback(on_event),
                enable_thinking=enable_thinking,
            )
        finally:
            # 消费端被取消（用户 stop）时显式关闭迭代器，让 LangGraph
            # 同步取消图任务（含进行中的 LLM 调用），不等 GC 兜底。
            with contextlib.suppress(Exception):
                await event_stream.aclose()
        clock.exec_done()
        # Extract token usage before hctx is released
        for mw in hctx["middlewares"]:
            if hasattr(mw, "summary"):
                usage_summary = mw.summary
    finally:
        release_harness_context(hctx)

    result = {"step_count": 0, "usage": usage_summary}
    timing = clock.as_dict()
    if timing is not None:
        result["timing"] = timing
    return result


async def invoke(
    agent: dict,
    state: dict,
    *,
    enable_thinking: bool = False,
    workspace: Any | None = None,
    legacy_records: list[dict] | None = None,
    cancel_checker: Callable[[], Awaitable[bool]] | None = None,
    user_token: str | None = None,
    execution_context: str = "chat",
) -> dict:
    """非流式执行 harness graph(供 invoke 端点 / workflow agent 节点使用)。

    Args:
        cancel_checker: 可选的异步取消检查器。传入后 compress_node 每轮
            REACT 迭代会检查它，返回 True 时 interrupt() 优雅挂起 agent。
        user_token: 可选,外部终端用户 token(回调验证模式),透传给 MCP server。
        execution_context: "chat"(默认)或 "workflow"(工作流 agent 节点,
            无人值守——剥离交互式/任务编排工具,注入 abort_workflow)。
    """
    from agent_flow_harness import build_agent_graph, build_config

    clock = _PhaseClock()
    hctx = await resolve_harness_context(
        agent, state, enable_thinking=enable_thinking, workspace=workspace,
        user_token=user_token, execution_context=execution_context,
    )
    clock.attach(hctx)
    try:
        session_id = state.get("session_id", "")
        with timed_phase(clock.phases, "build_compile"):
            graph = build_agent_graph(
                hctx["agent_doc"], checkpointer=get_checkpointer(),
                middleware=hctx["middlewares"], tools=hctx["tools"],
            )
            config = build_config(
                hctx["agent_doc"],
                hctx["llm"],
                tools=hctx["tools"],
                context_window=hctx["context_window"],
                middlewares=hctx["middlewares"],
                thread_id=session_id,
                cancel_checker=cancel_checker,
                tool_output_reference_formatter=hctx["tool_output_reference_formatter"],
                protected_turns=hctx["protected_turns"],
                compression_threshold=hctx["compression_threshold"],
                hard_limit_ratio=hctx["hard_limit_ratio"],
                recursion_limit=settings.AGENT_RECURSION_LIMIT,
            )
        await _maybe_migrate_legacy(graph, config, legacy_records)
        clock.build_done()

        clock.exec_start()
        result = await graph.ainvoke(state, config=config)
        clock.exec_done()
        # Extract token usage before hctx is released
        for mw in hctx["middlewares"]:
            if hasattr(mw, "summary"):
                result["usage"] = mw.summary
        timing = clock.as_dict()
        if timing is not None:
            result["timing"] = timing
        return result
    finally:
        release_harness_context(hctx)


async def resume_agent(
    agent: dict,
    state: dict,
    *,
    thread_id: str,
    resume_value: str = "continue",
    enable_thinking: bool = False,
    workspace: Any | None = None,
    cancel_checker: Callable[[], Awaitable[bool]] | None = None,
    user_token: str | None = None,
    execution_context: str = "chat",
) -> dict:
    """恢复被 interrupt() 挂起的 agent（非流式，供工作流恢复使用）。

    用 ``Command(resume=resume_value)`` + 相同 ``thread_id`` 续接 LangGraph
    checkpointer 中的状态，REACT 循环从断点继续，完整上下文（messages /
    tool 结果 / step_count）不丢失。
    """
    from agent_flow_harness import build_agent_graph, build_config
    from langgraph.types import Command

    clock = _PhaseClock()
    hctx = await resolve_harness_context(
        agent, state, enable_thinking=enable_thinking, workspace=workspace,
        user_token=user_token, execution_context=execution_context,
    )
    clock.attach(hctx)
    try:
        with timed_phase(clock.phases, "build_compile"):
            graph = build_agent_graph(
                hctx["agent_doc"], checkpointer=get_checkpointer(),
                middleware=hctx["middlewares"], tools=hctx["tools"],
            )
            config = build_config(
                hctx["agent_doc"],
                hctx["llm"],
                tools=hctx["tools"],
                context_window=hctx["context_window"],
                middlewares=hctx["middlewares"],
                thread_id=thread_id,
                cancel_checker=cancel_checker,
                tool_output_reference_formatter=hctx["tool_output_reference_formatter"],
                protected_turns=hctx["protected_turns"],
                compression_threshold=hctx["compression_threshold"],
                hard_limit_ratio=hctx["hard_limit_ratio"],
                recursion_limit=settings.AGENT_RECURSION_LIMIT,
            )
        clock.build_done()

        clock.exec_start()
        result = await graph.ainvoke(Command(resume=resume_value), config=config)
        clock.exec_done()
        timing = clock.as_dict()
        if timing is not None:
            result["timing"] = timing
        return result
    finally:
        release_harness_context(hctx)


async def resume(
    agent: dict,
    state: dict,
    on_event,
    answer: str,
    *,
    enable_thinking: bool = False,
    cancel_checker: Callable[[], Awaitable[bool]] | None = None,
    user_token: str | None = None,
) -> dict:
    """恢复被 interrupt 挂起的 graph,用 Command(resume=answer) 继续。"""
    from agent_flow_harness import build_agent_graph, build_config
    from langgraph.types import Command

    from app.engine.harness_integration.adapters import stream_events_to_app_events

    clock = _PhaseClock()
    hctx = await resolve_harness_context(
        agent, state, enable_thinking=enable_thinking, user_token=user_token,
    )
    clock.attach(hctx)
    usage_summary: dict = {}
    try:
        # 先发出工具/MCP 加载失败，让用户尽早知道哪些工具不可用
        await _emit_load_errors(hctx, on_event)

        session_id = state.get("session_id", "")
        with timed_phase(clock.phases, "build_compile"):
            graph = build_agent_graph(
                hctx["agent_doc"], checkpointer=get_checkpointer(),
                middleware=hctx["middlewares"], tools=hctx["tools"],
            )
            config = build_config(
                hctx["agent_doc"],
                hctx["llm"],
                tools=hctx["tools"],
                context_window=hctx["context_window"],
                middlewares=hctx["middlewares"],
                thread_id=session_id,
                cancel_checker=cancel_checker,
                tool_output_reference_formatter=hctx["tool_output_reference_formatter"],
                protected_turns=hctx["protected_turns"],
                compression_threshold=hctx["compression_threshold"],
                hard_limit_ratio=hctx["hard_limit_ratio"],
                recursion_limit=settings.AGENT_RECURSION_LIMIT,
            )
        clock.build_done()

        clock.exec_start()
        event_stream = graph.astream_events(
            Command(resume=answer), config=config, version="v2",
        )
        try:
            await stream_events_to_app_events(
                event_stream,
                _make_event_callback(on_event),
                enable_thinking=enable_thinking,
            )
        finally:
            # 与 stream() 相同：消费端被取消时显式关闭迭代器，
            # 让 LangGraph 同步取消图任务（含进行中的 LLM 调用）。
            with contextlib.suppress(Exception):
                await event_stream.aclose()
        clock.exec_done()
        # Extract token usage before hctx is released
        for mw in hctx["middlewares"]:
            if hasattr(mw, "summary"):
                usage_summary = mw.summary
    finally:
        release_harness_context(hctx)

    result = {"step_count": 0, "usage": usage_summary}
    timing = clock.as_dict()
    if timing is not None:
        result["timing"] = timing
    return result
