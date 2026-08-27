"""astream_events → application-layer event adapter.

:func:`stream_events_to_app_events` subscribes to a LangGraph
``astream_events(version="v2")`` iterator and translates the native events into
the eight application-layer :data:`AppEvent` types consumed by the frontend SSE
client. The adapter owns no LLM / tool / IO coupling — it is pure event
plumbing, so the same code serves any backend that drives the agent graph.

Event mapping (see Story v0.1-3 §1):

* ``on_chat_model_stream`` → ``text_delta`` (text) / ``thinking_delta``
  (reasoning, only when enabled); ``tool_call_chunks`` are *accumulated*.
* ``on_chat_model_end`` → ``thinking`` + ``text`` (incl. intermediate
  text persisted before tool calls) + one ``tool_call`` per resolved call.
* ``on_tool_start`` → ``tool_call_start`` placeholder.
* ``on_tool_end`` → ``tool_result``.
* ``on_llm_error`` / ``on_tool_error`` → ``error``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import structlog

from .app_event import (
    ErrorEvent,
    InterruptEvent,
    TextDeltaEvent,
    TextEvent,
    ThinkingDeltaEvent,
    ThinkingEvent,
    ToolCallEvent,
    ToolCallStartEvent,
    ToolResultEvent,
)
from .content import extract_answer_text, extract_thinking_text

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Awaitable, Callable

    from .app_event import AppEvent

logger = structlog.get_logger(__name__)

OnEventCallback = "Callable[[AppEvent], Awaitable[None]]"

# Native event kinds we translate; everything else is ignored.
_LLM_ERROR_KINDS = ("on_llm_error", "on_chat_model_error")
_TOOL_ERROR_KINDS = ("on_tool_error",)

# run_id → tool_call_id 映射（on_tool_start 时记录，on_tool_error 时查回）。
# 每次 stream 开始时清空（见函数体开头的 _run_id_to_tool_call_id.clear()）。
_run_id_to_tool_call_id: dict[str, str] = {}
# 待完成的 tool_call 列表（on_chat_model_end 时记录，on_tool_end 时移除）。
# on_tool_error 时按工具名取最近的一个作为 fallback。
_pending_tool_calls: list[dict[str, str]] = []
# 已发出 ToolResultEvent 的 tool_call_id（防 on_tool_error + on_tool_end 重复发）
_sent_tool_results: set[str] = set()


def _extract_tool_call_id_from_event(event: dict[str, Any]) -> str:
    """从 on_tool_error 事件里尽量提取 tool_call_id。

    LangGraph 的 on_tool_error 事件结构不保证有 tool_call_id，
    尝试多种路径：data.input.tool_calls / event metadata / tags。
    """
    data = event.get("data") or {}
    # 路径1：data.input 是 AIMessage，其 tool_calls 有 id
    inp = data.get("input")
    if inp is not None:
        # ToolCallRequest 对象
        tc = getattr(inp, "tool_call", None)
        if isinstance(tc, dict) and tc.get("id"):
            return tc["id"]
        # 可能是 dict 形式
        if isinstance(inp, dict):
            calls = inp.get("tool_calls") or inp.get("tool_call")
            if isinstance(calls, list):
                for c in calls:
                    if isinstance(c, dict) and c.get("id"):
                        return c["id"]
            if isinstance(calls, dict) and calls.get("id"):
                return calls["id"]
    # 路径2：metadata 里可能有
    meta = event.get("metadata") or {}
    for v in meta.values():
        if isinstance(v, dict) and v.get("tool_call_id"):
            return v["tool_call_id"]
    return ""


def _extract_interrupt(error: Any) -> dict[str, Any] | None:
    """Check if *error* is a GraphInterrupt carrying an interrupt payload.

    LangGraph 1.2.x surfaces interrupt() inside a tool as ``on_tool_error``
    with the ``GraphInterrupt`` object in ``data["error"]``.
    ``GraphInterrupt.args[0]`` is a tuple of ``Interrupt`` objects,
    each carrying a ``value`` dict with the interrupt payload.

    Recognised payloads (identified by their ``type`` field):
    - ``ask_clarification`` (no explicit type, carries ``question``)
    - ``workflow_confirmation`` (``confirm_workflow`` tool)
    """
    # GraphInterrupt stores interrupts tuple in args[0]
    raw = None
    if hasattr(error, "args") and error.args:
        raw = error.args[0]
    elif isinstance(error, tuple):
        raw = error
    if raw is None:
        return None
    # raw is typically a tuple of Interrupt objects
    items = raw if isinstance(raw, tuple) else (raw,)
    for intr in items:
        value = getattr(intr, "value", None)
        if not isinstance(value, dict):
            continue
        # ask_clarification carries "question"; confirm_workflow carries
        # type="workflow_confirmation". Accept either.
        if "question" in value or value.get("type") == "workflow_confirmation":
            return value
    return None


def _build_interrupt_event(payload: dict[str, Any], *, interrupt_id: str = "") -> InterruptEvent:
    """Build an :class:`InterruptEvent` from a raw interrupt ``payload``.

    Dispatches on ``payload["type"]``: ``workflow_confirmation`` (from
    ``confirm_workflow``) fills the workflow fields; anything else is
    treated as an ``ask_clarification`` payload and fills the question/
    options/fields fields.
    """
    if payload.get("type") == "workflow_confirmation":
        return InterruptEvent(
            kind="workflow_confirmation",
            workflow_name=payload.get("workflow_name", ""),
            workflow_description=payload.get("workflow_description", ""),
            input_preview=payload.get("input_preview"),
            interrupt_id=interrupt_id,
        )
    return InterruptEvent(
        kind="clarification",
        question=payload.get("question", ""),
        clarification_type=payload.get("type", "missing_info"),
        context=payload.get("context"),
        options=payload.get("options"),
        fields=payload.get("fields"),
        interrupt_id=interrupt_id,
    )


async def stream_events_to_app_events(
    astream_iter: AsyncIterator[dict[str, Any]],
    on_event: Callable[[AppEvent], Awaitable[None]],
    *,
    enable_thinking: bool = False,
) -> None:
    """Translate a native ``astream_events`` stream into app-layer events.

    Args:
        astream_iter: The async iterator returned by
            ``graph.astream_events(..., version="v2")``.
        on_event: Async callback invoked once per emitted :class:`AppEvent`.
        enable_thinking: When ``False`` (default) reasoning deltas / the
            complete ``thinking`` event are suppressed even if the model
            returns reasoning content.
    """
    accumulator = _StreamingAccumulator(enable_thinking=enable_thinking)
    _run_id_to_tool_call_id.clear()
    _pending_tool_calls.clear()
    _sent_tool_results.clear()

    async for event in astream_iter:
        kind = event.get("event")
        data = event.get("data") or {}

        if kind == "on_chat_model_stream":
            chunk = data.get("chunk")
            if chunk is None:
                continue

            text = extract_answer_text(getattr(chunk, "content", None))
            if text:
                await on_event(TextDeltaEvent(content=text))

            if enable_thinking:
                thinking = extract_thinking_text(chunk)
                if thinking:
                    await on_event(ThinkingDeltaEvent(content=thinking))

            accumulator.accumulate_tool_call_chunk(chunk)
            # 检测新的 tool_call — 第一个 chunk 到达时发 tool_call_start
            #（流式占位符），前端据此显示加载动画。
            # tool_call（完整数据）仍只在 on_chat_model_end 发出。
            new_starts = accumulator.pop_new_starts()
            if new_starts:
                logger.info("emit_tool_call_starts", count=len(new_starts), names=[s.get("name", "") for s in new_starts])
            for start in new_starts:
                await on_event(ToolCallStartEvent(tool_name=start.get("name") or ""))

        elif kind == "on_chat_model_end":
            output = data.get("output")
            if output is not None:
                if enable_thinking:
                    reasoning = extract_thinking_text(output)
                    if reasoning:
                        await on_event(ThinkingEvent(content=reasoning))
                content = extract_answer_text(getattr(output, "content", None))
                if content:
                    await on_event(TextEvent(content=content))
                # 直接发出所有 tool_call 事件（不再缓冲到 on_tool_start）。
                # on_chat_model_end 是 tool_call 的唯一来源，保证每个工具调用
                # 只发出一次。LangGraph 的 astream_events(v2) 会在多个嵌套层级
                # 冒泡 on_tool_start 事件，如果从 on_tool_start 发 tool_call 就会
                # 产生重复。
                for tc in _iter_tool_calls(output):
                    tc_id = tc.get("id", "")
                    tc_name = tc.get("name", "")
                    if tc_id:
                        _pending_tool_calls.append({"id": tc_id, "name": tc_name})
                    await on_event(
                        ToolCallEvent(
                            tool_name=tc_name,
                            args=tc.get("args") or {},
                            id=tc_id,
                        )
                    )
            accumulator.reset()

        elif kind == "on_tool_start":
            # 不发事件。tool_call 已在 on_chat_model_end 中发出（含 id），
            # 并记录到 _pending_tool_calls。on_tool_error 时按工具名从那里查回。
            pass

        elif kind == "on_tool_end":
            output = data.get("output")
            tool_name = event.get("name") or "unknown"
            # Extract content: ToolMessage may stringify with metadata if we
            # naively str() it; use .content when available.
            if output is None:
                content = ""
            elif hasattr(output, "content"):
                content = str(output.content)
            else:
                content = str(output)
            # 检查 ToolMessage 的 status：tool_wrapper 把 ToolException 转成
            # status="error" 的 ToolMessage 回传 LLM。这里同步透传给前端，
            # 让前端能结构化区分工具成功/失败，不再靠正则嗅探文本。
            status = "error" if getattr(output, "status", None) == "error" else "success"
            tcid = getattr(output, "tool_call_id", "")
            # 去重：on_tool_error 可能已经发过这个 tool_call_id 的 result
            if tcid and tcid in _sent_tool_results:
                logger.debug("on_tool_end_skipped_duplicate", tool_call_id=tcid)
            else:
                if tcid:
                    _sent_tool_results.add(tcid)
                logger.info(
                    "on_tool_end_emit",
                    tool_name=tool_name,
                    status=status,
                    tool_call_id=tcid,
                    output_type=type(output).__name__,
                )
                await on_event(
                    ToolResultEvent(
                        tool_name=tool_name,
                        content=content,
                        status=status,
                        tool_call_id=tcid,
                    )
                )
            # 正常完成的工具从 pending 列表移除
            tcid = getattr(output, "tool_call_id", "")
            if tcid:
                _pending_tool_calls[:] = [
                    tc for tc in _pending_tool_calls if tc.get("id") != tcid
                ]

        elif kind in _LLM_ERROR_KINDS:
            await on_event(
                ErrorEvent(message=_error_message(data), source="llm")
            )

        elif kind in _TOOL_ERROR_KINDS:
            # Check if the "error" is actually a GraphInterrupt (from
            # ask_clarification's / confirm_workflow's interrupt() call
            # inside a tool). LangGraph 1.2.x surfaces this as
            # on_tool_error with the GraphInterrupt object in
            # data["error"], rather than as __interrupt__ in on_chain_end
            # (which only ainvoke does).
            error = data.get("error")
            interrupt_payload = _extract_interrupt(error)
            if interrupt_payload is not None:
                await on_event(_build_interrupt_event(interrupt_payload))
            else:
                tool_name = event.get("name") or ""
                # 查 tool_call_id：优先用 run_id 映射，fallback 用工具名从 pending 列表找
                rid = str(event.get("run_id", "") or "")
                tool_call_id = _run_id_to_tool_call_id.get(rid, "")
                if not tool_call_id and tool_name:
                    # fallback：从 pending tool_calls 里按名字找最近的
                    for tc in reversed(_pending_tool_calls):
                        if tc.get("name") == tool_name and tc.get("id"):
                            tool_call_id = tc["id"]
                            break
                logger.info(
                    "on_tool_error_resolved",
                    tool_name=tool_name,
                    run_id=rid,
                    tool_call_id=tool_call_id,
                )
                if tool_call_id:
                    # 去重：如果 on_tool_end 已经发过这个 id 的 result，跳过
                    if tool_call_id in _sent_tool_results:
                        logger.debug("on_tool_error_skipped_duplicate", tool_call_id=tool_call_id)
                    else:
                        _sent_tool_results.add(tool_call_id)
                        # 移除已处理的 pending
                        _pending_tool_calls[:] = [
                            tc for tc in _pending_tool_calls if tc.get("id") != tool_call_id
                        ]
                        await on_event(
                            ToolResultEvent(
                                tool_name=tool_name,
                                content=_error_message(data),
                                status="error",
                                tool_call_id=tool_call_id,
                            )
                        )
                else:
                    await on_event(
                        ErrorEvent(message=_error_message(data), source="tool")
                    )

        elif kind == "on_chain_end":
            # Detect graph-level interrupt (ask_clarification /
            # confirm_workflow etc.). LangGraph surfaces an interrupt as a
            # top-level on_chain_end whose output dict carries a
            # ``__interrupt__`` key.
            output = data.get("output")
            if isinstance(output, dict):
                interrupts = output.get("__interrupt__")
                if interrupts:
                    for intr in interrupts:
                        payload = getattr(intr, "value", intr) or {}
                        if isinstance(payload, dict):
                            await on_event(_build_interrupt_event(
                                payload, interrupt_id=getattr(intr, "id", "") or "",
                            ))


# ---------------------------------------------------------------------------
# on_chat_model_end emission (shared logic)
# ---------------------------------------------------------------------------


async def _emit_model_end(
    output: Any,
    on_event: Callable[[AppEvent], Awaitable[None]],
    *,
    enable_thinking: bool,
) -> None:
    """Emit the complete thinking / text / tool-call events."""
    if output is None:
        return

    # 1. Complete thinking (only when enabled).
    if enable_thinking:
        reasoning = extract_thinking_text(output)
        if reasoning:
            await on_event(ThinkingEvent(content=reasoning))

    # 2. Text — emitted whenever there is content, including the
    #    "intermediate text persisted" case (content + tool_calls together).
    content = extract_answer_text(getattr(output, "content", None))
    if content:
        await on_event(TextEvent(content=content))

    # 3. One tool_call event per resolved call.
    for tc in _iter_tool_calls(output):
        await on_event(
            ToolCallEvent(
                tool_name=tc.get("name", ""),
                args=tc.get("args") or {},
                id=tc.get("id", ""),
            )
        )


# ---------------------------------------------------------------------------
# Streaming accumulator (tool_call_chunks → resolved calls)
# ---------------------------------------------------------------------------


class _StreamingAccumulator:
    """Accumulate ``tool_call_chunks`` across stream events.

    LangGraph streams a single tool call as multiple chunks (the ``id`` /
    ``name`` arrive early; ``args`` JSON is fragmented). This accumulates them
    per-call. When the first chunk with a ``name`` arrives for a given index,
    :meth:`pop_new_starts` returns it so the adapter can emit a
    ``tool_call_start`` event (showing a loading indicator before the full
    args are available). The resolved ``tool_call`` events come from
    ``output.tool_calls`` at ``on_chat_model_end``.
    """

    def __init__(self, *, enable_thinking: bool = False) -> None:
        self.enable_thinking = enable_thinking
        self._calls: dict[str, dict[str, Any]] = {}
        # Indices for which a tool_call_start has already been emitted.
        self._starts_emitted: set[str] = set()

    def accumulate_tool_call_chunk(self, chunk: Any) -> None:
        chunks = getattr(chunk, "tool_call_chunks", None)
        if not chunks:
            return
        logger.debug("accumulate_tool_call_chunk", count=len(chunks))
        for piece in chunks:
            index = getattr(piece, "index", 0)
            key = str(index)
            entry = self._calls.setdefault(
                key, {"name": "", "args": "", "id": ""}
            )
            name = getattr(piece, "name", None)
            if name:
                entry["name"] = name
            piece_id = getattr(piece, "id", None)
            if piece_id:
                entry["id"] = piece_id
            args = getattr(piece, "args", None)
            if args:
                entry["args"] += args

    def pop_new_starts(self) -> list[dict[str, str]]:
        """Return entries for newly-detected tool calls (for tool_call_start).

        Detects as soon as ANY chunk arrives for a given index — the ``name``
        may arrive in a later chunk. The frontend shows a loading placeholder
        immediately; the ``tool_call`` event at model-end fills in the real
        name/args. Each index is returned exactly once.
        """
        result: list[dict[str, str]] = []
        for key, entry in self._calls.items():
            if key not in self._starts_emitted:
                self._starts_emitted.add(key)
                result.append({"name": entry.get("name") or "", "id": entry.get("id", "")})
        return result

    def reset(self) -> None:
        self._calls.clear()
        self._starts_emitted.clear()

    def resolved_calls(self) -> list[dict[str, Any]]:
        """Return accumulated calls (best-effort; args kept as raw string)."""
        return list(self._calls.values())


# ---------------------------------------------------------------------------
# Content extraction helpers — 迁移至 content.py（str / 块列表 / GLM quirk
# 单块 dict 统一容忍），此处仅保留 tool_calls 提取。
# ---------------------------------------------------------------------------


def _iter_tool_calls(message: Any) -> list[dict[str, Any]]:
    """Return the resolved tool_calls list from a message (empty if none)."""
    calls = getattr(message, "tool_calls", None)
    if not calls:
        return []
    return [c if isinstance(c, dict) else dict(c) for c in calls]


def _error_message(data: dict[str, Any]) -> str:
    err = data.get("error")
    if isinstance(err, BaseException):
        return str(err)
    if err is not None:
        return str(err)
    return "unknown error"


__all__ = ["OnEventCallback", "stream_events_to_app_events"]
