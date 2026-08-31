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

from agent_flow_harness.context_engineering.interruption import annotate_interruptions
from agent_flow_harness.context_engineering.pairing import ensure_tool_pairing
from agent_flow_harness.engine.context import extract_model_name
from agent_flow_harness.engine.depth_guard import check_depth
from agent_flow_harness.middleware.chain import MiddlewareChain

if TYPE_CHECKING:
    from agent_flow_harness.state import AgentState

logger = structlog.get_logger(__name__)

# 持有后台压缩任务的强引用(防 GC 回收未完成的 task)。
_background_tasks: set[Any] = set()


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
    # 压缩配置(全局可配,有默认值)。
    protected_turns: int = configurable.get("protected_turns", 5)
    compression_threshold: float = configurable.get("compression_threshold", 0.7)
    hard_limit_ratio: float = configurable.get("hard_limit_ratio", 0.9)
    session_id: str = state.get("session_id", "")

    current_messages: list[Any] = list(state.get("messages", []))

    # ── token 评估:优先用 input_tokens(模型真实值),fallback len//4 ──
    from agent_flow_harness.engine.context import (
        estimate_context_tokens,
        get_context_window,
    )

    model_name = extract_model_name(llm) if llm is not None else ""
    window = context_window or get_context_window(model_name)
    threshold_tokens = int(window * compression_threshold)
    before_tokens = estimate_context_tokens(current_messages)

    # ── 单个工具结果超过 LLM 阈值 → 直接报错(无法压缩,继续只会超窗口失败)。
    _check_oversized_tool_result(current_messages, window, state)

    # ── 中断标注：新一轮开始时检测上一轮是否被中断，注入显式标记。
    #    在所有路径分叉之前执行；标注变更必须强制产生替换补丁，
    #    否则未达压缩阈值时标记不会写入 thread。──
    annotate_changed = False
    current_messages, intr = annotate_interruptions(current_messages)
    if intr.get("changed"):
        annotate_changed = True
        logger.info(
            "interruption_annotated",
            agent_id=state.get("agent_id"),
            request_id=state.get("request_id"),
            case=intr.get("case", ""),
        )

    # ── 可插拔 ContextStrategy 路径(高级用法,生产默认不走) ──
    if context_strategy is not None:
        before = len(current_messages)
        current_messages = await context_strategy.select(
            current_messages, max_tokens=context_window or 128000,
        )
        if len(current_messages) < before:
            logger.info(
                "compress_summarised",
                agent_id=state.get("agent_id"),
                request_id=state.get("request_id"),
                strategy=context_strategy.name,
                messages_before=before,
                messages_after=len(current_messages),
                tokens_before=before_tokens,
                tokens_after=estimate_context_tokens(current_messages),
                threshold_tokens=threshold_tokens,
            )
            return _pack_replace(_trim_tool_outputs(current_messages, config))
        if annotate_changed:
            return _pack_replace(_trim_tool_outputs(current_messages, config))
        return {}

    # ── 内置路径:工具压缩 + 后台LLM压缩 + 丢弃兜底 ──
    result, detail = _compress_by_turns(
        current_messages, window, protected_turns, compression_threshold,
        hard_limit_ratio, llm, session_id, config, state,
    )

    if detail["changed"] or annotate_changed:
        logger.info(
            "compress_done",
            agent_id=state.get("agent_id"),
            request_id=state.get("request_id"),
            actions=detail.get("actions", ""),
            tokens_before=before_tokens,
            tokens_after=estimate_context_tokens(result),
            threshold_tokens=threshold_tokens,
            messages_before=len(current_messages),
            messages_after=len(result),
        )
        return _pack_replace(result)

    logger.info(
        "compress_skipped",
        agent_id=state.get("agent_id"),
        request_id=state.get("request_id"),
        tokens=before_tokens,
        threshold_tokens=threshold_tokens,
        messages=len(current_messages),
    )
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


def _pack_replace(messages: list[Any]) -> dict[str, Any]:
    """Build a state patch that *replaces* the whole message history.

    Uses ``RemoveMessage(REMOVE_ALL_MESSAGES)`` so the ``add_messages`` reducer
    discards the old history and installs ``messages`` verbatim. Returns ``{}``
    (no-op) when there is nothing to change.
    """
    return {"messages": [RemoveMessage(id=REMOVE_ALL_MESSAGES), *messages]}


def _trim_tool_outputs(
    messages: list[Any], config: RunnableConfig,
) -> list[Any]:
    """Shrink oversized ToolMessage contents, applying the app-supplied
    reference formatter (so truncated results hint how to recall the original).

    Returns the original list when nothing was trimmed.
    """
    formatter = _configurable(config).get("tool_output_reference_formatter")
    from agent_flow_harness.context_engineering.tool_output import compress_tool_outputs

    result: list[Any] = compress_tool_outputs(messages, reference_formatter=formatter)
    return result


def _check_oversized_tool_result(
    messages: list[Any], window: int, state: "AgentState",
) -> None:
    """「未消费的」单个工具结果放不下模型窗口时抛 ValueError(转 ErrorEvent 发前端)。

    只检测**未消费**的工具结果。用实际剩余预算判断:
      剩余 = window - 其它消息占用 - 预留回复(4000)
    如果工具结果 > 剩余 → 即使不压缩也放不下 → 报错。

    比之前用固定 70% 阈值更准确:一个占 75% 窗口的工具,如果其它消息很少,
    可能完全放得下,不该报错。
    """
    from langchain_core.messages import ToolMessage

    from agent_flow_harness.context_engineering.tool_output import (
        find_unconsumed_tool_call_ids,
    )
    from agent_flow_harness.engine.context import (
        estimate_context_tokens,
        estimate_message_tokens,
    )

    RESERVED_RESPONSE = 4000
    unconsumed = find_unconsumed_tool_call_ids(messages)
    for m in messages:
        if isinstance(m, ToolMessage) and (m.tool_call_id or "") in unconsumed:
            tool_tokens = estimate_message_tokens(m)
            # 其它消息的 token(排除这个工具结果本身)。
            other_tokens = estimate_context_tokens(messages) - tool_tokens
            budget = window - other_tokens - RESERVED_RESPONSE
            if tool_tokens > budget:
                tcid = m.tool_call_id or "?"
                tool_name = getattr(m, "name", "") or "未知工具"
                msg = (
                    f"工具 {tool_name} 的返回结果过大(约 {tool_tokens} tokens),"
                    f"剩余上下文空间不足以容纳(剩余约 {budget} tokens)。"
                    f"该工具结果尚未被处理、无法压缩,请减少返回内容"
                    f"(如调小 top_k、缩小查询范围),或使用上下文窗口更大的模型。"
                    f"(tool_call_id={tcid})"
                )
                logger.error(
                    "compress_tool_result_oversized",
                    agent_id=state.get("agent_id"),
                    request_id=state.get("request_id"),
                    tool_name=tool_name,
                    tool_call_id=tcid,
                    tool_tokens=tool_tokens,
                    budget=budget,
                    window=window,
                )
                raise ValueError(msg)


def _compress_by_turns(
    messages: list[Any],
    window: int,
    protected_turns: int,
    threshold_ratio: float,
    hard_limit_ratio: float,
    llm: Any,
    session_id: str,
    config: RunnableConfig,
    state: "AgentState",
) -> "tuple[list[Any], dict[str, Any]]":
    """工具压缩 + 后台LLM压缩 + 丢弃兜底。

    返回 (结果消息列表, 详情 dict)。详情字段: changed (bool) / actions (str)。

    规则:
      ① 检查缓存:有后台压缩好的LLM摘要?有则按ID精确回填。
      ② 未达阈值 → 什么都不做(可能带了回填的摘要)。
      ③ 达阈值 → 工具压缩(同步,快):
         第1级: 5轮外已消费工具压;第2级: 5轮内已消费工具压。
         压完够了就停。
      ④ 工具压完仍超阈值 → 触发后台LLM压缩(异步,不阻塞当前轮)。
      ⑤ 逼近硬上限(hard_limit_ratio)且后台没压完 → 丢弃最早原始消息防崩溃。
         (临时有损,后台摘要回填后恢复。)

    绝不生成机械摘要 → 无累积退化。
    """
    import asyncio

    from agent_flow_harness.context_engineering.llm_summary import (
        apply_cached_summary,
        compress_history_with_llm,
    )
    from agent_flow_harness.context_engineering.split import split_system_history
    from agent_flow_harness.context_engineering.summary_cache import summary_cache
    from agent_flow_harness.context_engineering.tool_output import compress_tool_outputs
    from agent_flow_harness.context_engineering.turns import split_by_turns
    from agent_flow_harness.engine.context import estimate_context_tokens

    formatter = _configurable(config).get("tool_output_reference_formatter")
    threshold = int(window * threshold_ratio)
    hard_limit = int(window * hard_limit_ratio)
    changed = False
    actions_parts: list[str] = []

    # ① 检查缓存:有后台压缩好的摘要?
    # ⓪ 防御性配对安全网：compress_node 入口的中断标注已把尾部孤儿转为
    # "被取消"的 ToolMessage（完整配对）；此处再无条件跑一遍 ensure_tool_pairing，
    # 兜住压缩路径自身可能引入的孤儿（正常对话零改动）。
    paired = ensure_tool_pairing(messages)
    if len(paired) != len(messages):
        logger.info(
            "orphan_tool_calls_stripped",
            session_id=session_id,
            before=len(messages),
            after=len(paired),
            note="state contained unanswered tool_calls (mid-stream cancel / crash)",
        )
        messages = paired
        changed = True
        actions_parts.append("孤儿tool_call清理")
    else:
        messages = paired

    # ⓪-b 旧图降级:protected_turns 外的 image 块换成可回取占位。独立于
    # token 阈值——图片按张计费视觉 token,每轮 LLM 调用都在烧钱,尽早回收。
    # formatter 由应用层注入(harness 不硬编码回看工具名);未注入=功能关闭。
    image_formatter = _configurable(config).get("image_reference_formatter")
    if image_formatter is not None:
        from agent_flow_harness.context_engineering.images import (
            downgrade_stale_images,
        )

        keep_recent = int(_configurable(config).get("image_keep_recent", 0) or 0)
        messages, degraded_images = downgrade_stale_images(
            messages,
            protected_turns=protected_turns,
            keep_recent=keep_recent,
            formatter=image_formatter,
        )
        if degraded_images:
            changed = True
            actions_parts.append(f"旧图降级×{degraded_images}")
            logger.info(
                "stale_images_downgraded",
                session_id=session_id,
                count=degraded_images,
                keep_recent=keep_recent,
            )

    cached = summary_cache.get(session_id) if session_id else None
    if cached:
        messages, inserted = apply_cached_summary(messages, cached)
        summary_cache.clear(session_id)
        if inserted:
            changed = True
            actions_parts.append("缓存摘要回填")

    current = estimate_context_tokens(messages)

    # ② 未达阈值 → 什么都不做。
    if current <= threshold:
        return messages, {"changed": changed, "actions": "+".join(actions_parts)}

    # ③ 达阈值 → 工具压缩(同步)。
    system_msgs, history = split_system_history(messages)

    # 摘要迁移（B.3-3）：新的 llm_summary 是 HumanMessage——天然在历史区，
    # 下次压缩自然重吸收（render 以 [此前摘要] 分支喂给摘要 LLM，不再硬丢失）。
    # 存量 SystemMessage 形态的旧摘要仍会被 split_system_history 收进 system 区
    # ——在此迁移为 HumanMessage（保留 id 与内容），交还历史区参与再压缩。
    from langchain_core.messages import HumanMessage as _HumanMessage

    migrated: list[Any] = []
    kept_system: list[Any] = []
    for m in system_msgs:
        if getattr(m, "id", "") in ("llm_summary", "summary"):
            migrated.append(_HumanMessage(
                content=str(m.content),
                id=getattr(m, "id", "") or "llm_summary",
            ))
        else:
            kept_system.append(m)
    if migrated:
        system_msgs = kept_system
        history = [*migrated, *history]

    outer, recent = split_by_turns(history, protected_turns)

    # 在完整 history 上算一次 unconsumed_ids,传给后续切片的 compress_tool_outputs
    # (切片后 find_unconsumed_tool_call_ids 丢失全局上下文会误判,见 B2 修复)。
    from agent_flow_harness.context_engineering.tool_output import (
        find_unconsumed_tool_call_ids,
    )

    unconsumed_ids = find_unconsumed_tool_call_ids(history)

    # 第1级:5轮外已消费工具压。
    outer = compress_tool_outputs(outer, reference_formatter=formatter, unconsumed_ids=unconsumed_ids)
    combined = [*system_msgs, *outer, *recent]
    if estimate_context_tokens(combined) <= threshold:
        return ensure_tool_pairing(combined), {"changed": True, "actions": "5轮外工具压缩"}

    # 第2级:5轮内已消费工具压(未消费保护内置)。
    recent = compress_tool_outputs(recent, reference_formatter=formatter, unconsumed_ids=unconsumed_ids)
    combined = [*system_msgs, *outer, *recent]
    after_tools = estimate_context_tokens(combined)
    if after_tools <= threshold:
        return ensure_tool_pairing(combined), {"changed": True, "actions": "5轮内工具压缩"}

    # ④ 工具压完仍超阈值 → 触发后台LLM压缩(如果没在跑 + 有outer + 有LLM)。
    if (
        session_id
        and llm is not None
        and not summary_cache.is_running(session_id)
        and outer
    ):
        summary_cache.mark_running(session_id)
        task = asyncio.create_task(compress_history_with_llm(
            llm, outer, session_id, summary_cache,
            reference_formatter=formatter,
        ))
        _background_tasks.add(task)
        task.add_done_callback(_background_tasks.discard)
        logger.info(
            "compress_background_triggered",
            agent_id=state.get("agent_id"),
            request_id=state.get("request_id"),
            session_id=session_id,
            outer_count=len(outer),
        )
        actions_parts.append("后台LLM压缩已触发")

    # ⑤ 逼近硬上限 → 丢弃最早原始消息防崩溃(临时有损,等后台摘要回填)。
    if after_tools > hard_limit:
        safe_limit = int(window * 0.85)
        # 差量计算:base = system + recent(不变),循环只减 outer 的 token。
        from agent_flow_harness.engine.context import estimate_message_tokens

        base_tokens = estimate_context_tokens([*system_msgs, *recent])
        discard_outer = list(outer)
        outer_tokens = sum(estimate_message_tokens(m) for m in discard_outer)
        while base_tokens + outer_tokens > safe_limit and discard_outer:
            outer_tokens -= estimate_message_tokens(discard_outer.pop(0))
        combined = [*system_msgs, *discard_outer, *recent]
        logger.warning(
            "compress_discard_emergency",
            agent_id=state.get("agent_id"),
            request_id=state.get("request_id"),
            discarded=len(outer) - len(discard_outer),
            note="逼近硬上限,丢弃早期历史防崩溃(后台摘要回填后恢复)",
        )
        actions_parts.append("丢弃早期历史(防崩溃)")
        return ensure_tool_pairing(combined), {"changed": True, "actions": "+".join(actions_parts)}

    # 工具压完仍超阈值但没到硬上限:后台在压,本轮用当前消息正常答。
    return ensure_tool_pairing(combined), {"changed": True, "actions": "+".join(actions_parts)}


__all__ = ["compress_node", "llm_node"]
