"""Thread messages → application-layer events (history reconstruction).

:func:`messages_to_app_events` is the batch counterpart of
:func:`~agent_flow_harness.adapters.stream_events.stream_events_to_app_events`:
both turn a LangChain execution representation into the same eight
:class:`AppEvent` types. The streaming adapter consumes a live
``astream_events`` iterator; this module consumes the persisted ``messages``
list from a thread checkpoint. Using one over the other, the frontend
observes an identical event sequence.

Only six of the eight AppEvent types are reconstructable from messages —
``tool_call_start`` / ``*_delta`` / ``error`` are purely transient and are
never emitted here (see module-level rules).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .app_event import (
    TextEvent,
    ThinkingEvent,
    ToolCallEvent,
    ToolResultEvent,
)
from .stream_events import (
    _extract_text_content,
    _extract_thinking_content,
    _iter_tool_calls,
)

if TYPE_CHECKING:
    from langchain_core.messages import BaseMessage

    from .app_event import AppEvent


def messages_to_app_events(
    messages: list[BaseMessage],
    *,
    enable_thinking: bool = False,
) -> list[AppEvent]:
    """Convert a thread's LangChain messages into a list of AppEvents.

    This is the **default** history-reconstruction implementation. An
    application that needs a different event shape supplies its own converter
    and ignores this function — the harness never calls it implicitly.

    Reconstruction rules (symmetric with ``stream_events_to_app_events``):

    * ``HumanMessage`` → no event (it is a turn separator).
    * ``AIMessage`` with text **and** ``tool_calls`` → ``TextEvent``
      (intermediate text persisted) followed by one ``ToolCallEvent`` per call.
    * ``AIMessage`` with only ``tool_calls`` → one ``ToolCallEvent`` per call.
    * ``AIMessage`` with only text → ``TextEvent`` (the final answer).
    * ``AIMessage`` with thinking blocks (``enable_thinking``) →
      ``ThinkingEvent`` (full reasoning, emitted before the answer).
    * ``ToolMessage`` → ``ToolResultEvent``.

    Events that are **never** reconstructed (purely streaming/transient):
    ``tool_call_start``, ``thinking_delta``, ``text_delta``, ``error``.

    Args:
        messages: The ``messages`` list from a thread checkpoint state.
        enable_thinking: When ``True``, ``ThinkingEvent`` is emitted for
            reasoning content; when ``False`` reasoning is suppressed.

    Returns:
        A flat list of AppEvents in chronological order.
    """
    events: list[AppEvent] = []

    for idx, msg in enumerate(messages):
        type_name = type(msg).__name__

        if type_name == "HumanMessage":
            # Turn separator — no event produced.
            continue

        if type_name == "AIMessage":
            _emit_ai_message(msg, idx, events, enable_thinking=enable_thinking)
            continue

        if type_name == "ToolMessage":
            _emit_tool_message(msg, events)

        # SystemMessage / other message types are ignored.

    return events


def _emit_ai_message(
    msg: BaseMessage,
    idx: int,
    events: list[AppEvent],
    *,
    enable_thinking: bool,
) -> None:
    """Emit thinking / final-answer / tool-call events for an AIMessage."""
    # 1. Thinking (full, only when enabled).
    if enable_thinking:
        reasoning = _extract_thinking_content(msg)
        if reasoning:
            events.append(ThinkingEvent(content=reasoning))

    # 2. Text — emitted whenever there is text content, *including*
    #    the "intermediate text persisted" case (content + tool_calls), so the
    #    output matches stream_events_to_app_events exactly.
    text = _extract_text_content(msg)
    if text:
        events.append(TextEvent(content=text))

    # 3. One tool_call per resolved call.
    for j, tc in enumerate(_iter_tool_calls(msg)):
        call_id = tc.get("id") or f"msg_{idx}_call_{j}"
        events.append(
            ToolCallEvent(
                tool_name=tc.get("name", ""),
                args=tc.get("args") or {},
                id=call_id,
            )
        )


def _emit_tool_message(msg: BaseMessage, events: list[AppEvent]) -> None:
    """Emit a ToolResultEvent for a ToolMessage.

    ``tool_name`` falls back to an empty string when the ToolMessage carries
    no ``name`` (older LangChain versions); the result content is always
    stringified. ``tool_call_id`` is carried over so recall_tool_result can
    retrieve the original content after compression.
    """
    tool_name = getattr(msg, "name", "") or ""
    content = _extract_text_content(msg) or str(getattr(msg, "content", "") or "")
    events.append(
        ToolResultEvent(
            tool_name=tool_name,
            content=content,
            tool_call_id=getattr(msg, "tool_call_id", "") or "",
        )
    )


__all__ = ["messages_to_app_events"]
