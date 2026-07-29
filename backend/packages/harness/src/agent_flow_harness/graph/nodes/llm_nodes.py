"""LLM + compress graph nodes for the node-based agent graph.

These replace the monolithic ``react_node`` for循环 with two independent
nodes wired by LangGraph edges:

* ``compress_node`` — context-window compression (runs before each LLM call).
* ``llm_node`` — single LLM invocation (binds tools, runs middleware hooks).

Tool execution is handled by the native ``langgraph.prebuilt.ToolNode``;
this module only owns the LLM-side and compression-side nodes.
"""
from typing import TYPE_CHECKING, Any, cast

import structlog
from langchain_core.messages import AIMessage, RemoveMessage
from langchain_core.runnables import RunnableConfig
from langgraph.graph.message import REMOVE_ALL_MESSAGES

from agent_flow_harness.engine.context import (
    compress_messages,
    extract_model_name,
    should_compress,
)
from agent_flow_harness.engine.depth_guard import check_depth
from agent_flow_harness.middleware.chain import MiddlewareChain

if TYPE_CHECKING:
    from agent_flow_harness.state import AgentState

logger = structlog.get_logger(__name__)


def _configurable(config: RunnableConfig | None) -> dict[str, Any]:
    """Return ``config["configurable"]`` as a dict, raising a clear error."""
    if config is None:
        msg = "node requires a RunnableConfig with a 'configurable' mapping."
        raise ValueError(msg)
    configurable = config.get("configurable")
    if not isinstance(configurable, dict):
        msg = "config['configurable'] must be a dict."
        raise ValueError(msg)
    return configurable


async def compress_node(
    state: "AgentState", config: RunnableConfig,
) -> dict[str, Any]:
    """Compress conversation history when approaching the context-window limit.

    Runs before every LLM call so the model never exceeds its token budget.
    Supports both the pluggable ``ContextStrategy`` (v0.2-5) and the built-in
    ``compress_messages`` fallback.

    Cancel gate: when an optional ``cancel_checker`` async callable is provided
    in ``configurable`` and returns ``True``, this node calls LangGraph
    ``interrupt()`` to gracefully suspend the REACT loop. The full
    conversation context (messages, tool results, step_count) is persisted
    by the checkpointer; resuming with ``Command(resume=...)`` replays this
    node, ``interrupt()`` returns the resume value, and the loop continues
    with full context continuity.
    """
    configurable = _configurable(config)

    # ── Cancel gate — runs at the top of every REACT iteration ──
    cancel_checker = configurable.get("cancel_checker")
    if cancel_checker is not None and await cancel_checker():
        from langgraph.types import interrupt

        interrupt({"reason": "cancelled"})
        # interrupt() returns the resume value on resume; fall through to
        # normal compression (the REACT loop continues with full context).

    context_window: int | None = configurable.get("context_window")
    context_strategy = configurable.get("context_strategy")
    llm = configurable.get("llm")

    current_messages: list[Any] = list(state.get("messages", []))

    if context_strategy is not None:
        before = len(current_messages)
        current_messages = await context_strategy.select(
            current_messages, max_tokens=context_window or 128000,
        )
        if len(current_messages) < before:
            logger.info(
                "compress_node_strategy",
                strategy=context_strategy.name,
                agent_id=state.get("agent_id"),
                request_id=state.get("request_id"),
                before=before,
                after=len(current_messages),
            )
            # Replace the whole message history rather than appending. The
            # ``add_messages`` reducer is append-by-default; without
            # ``RemoveMessage(REMOVE_ALL_MESSAGES)`` the trimmed history would
            # pile up after the old one, and a summary SystemMessage produced
            # by the strategy would land mid-list → non-consecutive system
            # messages rejected by langchain-anthropic.
            return {
                "messages": [
                    RemoveMessage(id=REMOVE_ALL_MESSAGES), *current_messages,
                ],
            }
        return {}

    # Fallback: built-in compress_messages
    model_name = extract_model_name(llm) if llm is not None else ""
    if should_compress(current_messages, model_name, context_window=context_window):
        before = len(current_messages)
        current_messages = compress_messages(
            current_messages, model_name, context_window=context_window,
        )
        logger.info(
            "compress_node_builtin",
            agent_id=state.get("agent_id"),
            request_id=state.get("request_id"),
            before=before,
            after=len(current_messages),
        )
        # Same replace-not-append rationale as the strategy branch above.
        return {
            "messages": [
                RemoveMessage(id=REMOVE_ALL_MESSAGES), *current_messages,
            ],
        }

    # No compression needed — return empty patch (state unchanged).
    return {}


async def llm_node(
    state: "AgentState", config: RunnableConfig,
) -> dict[str, Any]:
    """Single LLM invocation with tool-binding and middleware hooks.

    Reads ``llm`` / ``tools`` / ``middlewares`` from ``config["configurable"]``
    (never from global state) so the non-serialisable LLM object stays out of
    the checkpointer. Returns a state patch that appends the AIMessage and
    increments ``step_count``.

    Depth / cycle guard is checked *after* the LLM call; if the guard trips,
    ``error`` is set and the graph routes to END via ``tools_condition`` (no
    tool calls → END).
    """
    configurable = _configurable(config)
    llm = configurable["llm"]
    tools = configurable.get("tools") or []
    chain = MiddlewareChain(configurable.get("middlewares") or [])

    # Bind tools to the LLM.
    tool_list = list(tools.values()) if isinstance(tools, dict) else list(tools)
    llm_with_tools = llm.bind_tools(tool_list) if tool_list else llm

    # Middleware: before_llm (may rewrite messages).
    call_state: AgentState = cast("AgentState", {**state})
    call_state = await chain.run_before_llm(call_state)

    # Defence-in-depth: guarantee all SystemMessages are contiguous at the
    # front before hitting the model. langchain-anthropic rejects any
    # message list whose system messages are split by non-system turns
    # ("Received multiple non-consecutive system messages."). Compression,
    # middleware, or future system-injection points could otherwise scatter
    # them; normalise here as a final guarantee. Only reorders when needed.
    call_state["messages"] = _system_messages_first(call_state["messages"])

    # LLM call.
    response: AIMessage = await llm_with_tools.ainvoke(call_state["messages"])
    step_count: int = state.get("step_count", 0) + 1

    # Middleware: after_llm.
    await chain.run_after_llm(
        cast("AgentState", {**call_state, "step_count": step_count}), response,
    )

    # Depth / cycle guard — checked after the call so the AIMessage is still
    # appended (the graph will route to END because there are no tool_calls
    # on the error path... actually we set error and route explicitly).
    depth_result = check_depth(state)
    if not depth_result.allowed:
        logger.warning(
            "circular_call_detected" if depth_result.cycle is not None
            else "depth_limit_exceeded",
            agent_id=state.get("agent_id"),
            request_id=state.get("request_id"),
            current_depth=depth_result.current_depth,
            max_depth=depth_result.max_depth,
            call_chain=state.get("call_chain", []),
            reason=depth_result.reason,
            cycle=depth_result.cycle,
        )
        return {
            "messages": [response],
            "step_count": step_count,
            "error": depth_result.reason,
        }

    return {"messages": [response], "step_count": step_count}


def _system_messages_first(messages: list[Any]) -> list[Any]:
    """Move every SystemMessage to the front, keeping relative order.

    Returns the original list unchanged when the system messages are already
    leading and contiguous (the common case), avoiding a needless copy on the
    hot path.
    """
    from langchain_core.messages import SystemMessage

    from agent_flow_harness.context_engineering.split import split_system_history

    system_msgs, history = split_system_history(messages)
    n = len(system_msgs)
    if n == 0:
        return messages
    # Already leading and contiguous? Skip the rebuild.
    if n <= len(messages) and all(isinstance(m, SystemMessage) for m in messages[:n]):
        return messages
    return [*system_msgs, *history]


__all__ = ["compress_node", "llm_node"]
