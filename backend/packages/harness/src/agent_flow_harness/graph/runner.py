"""Graph runners — synchronous and streaming entry points.

v0.1-1 provides thin wrappers that build the graph and delegate to LangGraph's
``ainvoke`` / ``astream_events``. The streaming adapter (``run_agent_streaming``
piping through :mod:`agent_flow_harness.adapters`) is fleshed out in v0.1-3.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from agent_flow_harness.graph.builder import build_agent_graph
from agent_flow_harness.tools.registry import TOOL_REGISTRY, ToolRegistry

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from langchain_core.language_models.chat_models import BaseChatModel
    from langchain_core.runnables import RunnableConfig

    from agent_flow_harness.state import AgentState


async def run_agent(
    agent_doc: dict[str, Any],
    input_state: AgentState,
    *,
    config: RunnableConfig | None = None,
) -> dict[str, Any]:
    """Non-streaming entry — invoke the compiled graph.

    Args:
        agent_doc: Agent configuration document.
        input_state: Initial :class:`AgentState`.
        config: Optional LangGraph runnable config.

    Returns:
        The final state dict produced by the graph.
    """
    graph = build_agent_graph(agent_doc)
    return await graph.ainvoke(input_state, config=config)


async def run_agent_streaming(
    agent_doc: dict[str, Any],
    input_state: AgentState,
    *,
    on_event: Callable[[dict[str, Any]], Awaitable[None]],
    config: RunnableConfig | None = None,
) -> dict[str, Any]:
    """Streaming entry — run the graph and forward app-layer events.

    v0.1-1 is a stub that simply invokes the graph (no event translation yet).
    v0.1-3 wires this through :func:`stream_events_to_app_events` so
    ``on_event`` receives the 7 application-layer event types.
    """
    _ = on_event
    graph = build_agent_graph(agent_doc)
    return await graph.ainvoke(input_state, config=config)


def build_config(
    agent_doc: dict[str, Any],
    llm: BaseChatModel,
    *,
    thread_id: str | None = None,
    context_window: int | None = None,
    workspace: Any | None = None,
    tools: Any | None = None,
    registry: ToolRegistry | None = None,
    middlewares: Any | None = None,
    recursion_limit: int = 75,
    cancel_checker: Callable[[], Awaitable[bool]] | None = None,
    tool_output_reference_formatter: Callable[[str], str] | None = None,
    image_reference_formatter: Callable[[str, str], str] | None = None,
    image_keep_recent: int | None = None,
    protected_turns: int | None = None,
    compression_threshold: float | None = None,
    hard_limit_ratio: float | None = None,
) -> dict[str, Any]:
    """Build a ``RunnableConfig`` for the agent graph.

    Tools are resolved from ``agent_doc["tools"]`` via the registry (the global
    :data:`TOOL_REGISTRY` by default), exactly as :func:`react_node` expects
    under ``config["configurable"]["tools"]``. Pass ``tools=`` to bypass the
    registry (handy for tests / custom tool sets).

    Middlewares are resolved from ``agent_doc["middleware"]`` unless
    ``middlewares=`` is given (an explicit empty list disables middleware);
    they flow to the node under ``config["configurable"]["middlewares"]``.

    Args:
        agent_doc: Agent configuration document (``tools``/``middleware``
            entries read here).
        llm: Configured chat model injected into the node.
        thread_id: Checkpointer thread id (set when resuming a session).
        context_window: Optional model context-window override.
        workspace: Optional host-supplied workspace object (duck-typed).
        tools: Optional pre-resolved tool list/mapping; when ``None`` the
            registry resolves them from ``agent_doc``.
        registry: Registry to resolve tools from (defaults to the global one).
        middlewares: Optional pre-resolved middleware list; when ``None`` the
            harness resolves them from ``agent_doc["middleware"]``.
        recursion_limit: LangGraph recursion limit.
        cancel_checker: Optional async callable that returns ``True`` when the
            agent should be cancelled. When provided, ``compress_node`` polls
            it at the top of every REACT iteration and calls ``interrupt()``
            to gracefully suspend. On resume (``Command(resume=...)``) the loop
            continues with full context continuity.
        tool_output_reference_formatter: Optional callable that turns a
            ``tool_call_id`` into a reference note appended to truncated tool
            outputs (so the agent knows how to recall the full original).
            Application-supplied — harness never hardcodes the recall mechanism.
        image_reference_formatter: Optional callable ``(file_id, name) -> str``
            producing the placeholder shown when stale image blocks are
            downgraded out of context (multimodal history management).
            Application-supplied — harness never hardcodes the recall tool.
        image_keep_recent: Keep the most recent N images un-degraded when
            downgrading stale images (0 = degrade all; None = feature off).

    Returns:
        A ``RunnableConfig`` dict ready to pass to ``graph.ainvoke``.
    """
    if tools is None:
        reg = registry if registry is not None else TOOL_REGISTRY
        tools = reg.resolve(agent_doc)

    if middlewares is None:
        from agent_flow_harness.middleware import resolve_middleware

        middlewares = resolve_middleware(agent_doc.get("middleware"))

    configurable: dict[str, Any] = {"llm": llm, "tools": tools}
    if middlewares:
        configurable["middlewares"] = middlewares
    if thread_id is not None:
        configurable["thread_id"] = thread_id
    if context_window is not None:
        configurable["context_window"] = context_window
    if workspace is not None:
        configurable["workspace"] = workspace
    if cancel_checker is not None:
        configurable["cancel_checker"] = cancel_checker
    if tool_output_reference_formatter is not None:
        configurable["tool_output_reference_formatter"] = (
            tool_output_reference_formatter
        )
    if image_reference_formatter is not None:
        configurable["image_reference_formatter"] = image_reference_formatter
    if image_keep_recent is not None:
        configurable["image_keep_recent"] = image_keep_recent
    if protected_turns is not None:
        configurable["protected_turns"] = protected_turns
    if compression_threshold is not None:
        configurable["compression_threshold"] = compression_threshold
    if hard_limit_ratio is not None:
        configurable["hard_limit_ratio"] = hard_limit_ratio

    return {"configurable": configurable, "recursion_limit": recursion_limit}
