"""AC1-AC3 cover: messages_to_app_events — history reconstruction + symmetry."""

from __future__ import annotations

import pytest
from app.engine.harness_integration.adapters import messages_to_app_events
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage


def _types(events):
    return [e.type for e in events]


# ---------------------------------------------------------------------------
# Basic reconstruction
# ---------------------------------------------------------------------------


def test_plain_text_answer():
    msgs = [HumanMessage(content="hi"), AIMessage(content="hello")]
    events = messages_to_app_events(msgs)
    assert len(events) == 1
    assert events[0].type == "text"
    assert events[0].content == "hello"


def test_human_message_produces_no_event():
    msgs = [HumanMessage(content="hi")]
    assert messages_to_app_events(msgs) == []


def test_empty_messages():
    assert messages_to_app_events([]) == []


def test_system_message_ignored():
    msgs = [SystemMessage(content="you are helpful"), AIMessage(content="ok")]
    events = messages_to_app_events(msgs)
    assert _types(events) == ["text"]


def test_list_content_blocks_text():
    """content as list-of-blocks is concatenated into text."""
    msg = AIMessage(content=[{"type": "text", "text": "hello"}, {"type": "text", "text": "!"}])
    events = messages_to_app_events([msg])
    assert events[0].type == "text"
    assert events[0].content == "hello!"


# ---------------------------------------------------------------------------
# tool_call + tool_result association
# ---------------------------------------------------------------------------


def test_tool_call_then_tool_result():
    msgs = [
        AIMessage(content="", tool_calls=[{"name": "read", "args": {"p": "x"}, "id": "c1"}]),
        ToolMessage(content="file", tool_call_id="c1", name="read"),
    ]
    events = messages_to_app_events(msgs)
    assert _types(events) == ["tool_call", "tool_result"]
    assert events[0].tool_name == "read"
    assert events[0].id == "c1"
    assert events[1].tool_name == "read"
    assert events[1].content == "file"
    # tool_result 持久化了 tool_call_id(recall_tool_result 回溯的前提)。
    assert events[1].tool_call_id == "c1"


def test_multiple_tool_calls_in_one_message():
    msg = AIMessage(
        content="",
        tool_calls=[
            {"name": "a", "args": {}, "id": "1"},
            {"name": "b", "args": {"k": 1}, "id": "2"},
        ],
    )
    events = messages_to_app_events([msg])
    assert _types(events) == ["tool_call", "tool_call"]
    assert {e.tool_name for e in events} == {"a", "b"}


def test_tool_call_missing_id_synthesized():
    msg = AIMessage(content="", tool_calls=[{"name": "x", "args": {}, "id": ""}])
    events = messages_to_app_events([msg])
    assert events[0].type == "tool_call"
    assert events[0].id  # non-empty synthetic id


def test_tool_result_without_preceding_call():
    """A standalone ToolMessage still produces a tool_result (graceful)."""
    msg = ToolMessage(content="orphan", tool_call_id="c1", name="read")
    events = messages_to_app_events([msg])
    assert _types(events) == ["tool_result"]
    assert events[0].content == "orphan"


def test_tool_message_no_name():
    """ToolMessage without .name yields empty tool_name."""
    msg = ToolMessage(content="data", tool_call_id="c1")
    events = messages_to_app_events([msg])
    assert events[0].tool_name == ""


# ---------------------------------------------------------------------------
# Intermediate text persisted (symmetry with stream adapter)
# ---------------------------------------------------------------------------


def test_intermediate_text_persisted_with_tool_calls():
    """AIMessage with content AND tool_calls → text + tool_call."""
    msg = AIMessage(
        content="let me check",
        tool_calls=[{"name": "read", "args": {}, "id": "c1"}],
    )
    events = messages_to_app_events([msg])
    assert _types(events) == ["text", "tool_call"]
    assert events[0].content == "let me check"
    assert events[1].tool_name == "read"


def test_intermediate_text_emitted_before_tool_call():
    """Order: text (text) precedes tool_call."""
    msg = AIMessage(
        content="thinking...",
        tool_calls=[{"name": "x", "args": {}, "id": "c1"}],
    )
    events = messages_to_app_events([msg])
    assert events[0].type == "text"
    assert events[1].type == "tool_call"


# ---------------------------------------------------------------------------
# Thinking
# ---------------------------------------------------------------------------


def test_thinking_suppressed_by_default():
    msg = AIMessage(content="answer")
    msg.additional_kwargs = {"reasoning_content": "secret"}
    msg.reasoning_content = "secret"
    events = messages_to_app_events([msg], enable_thinking=False)
    assert _types(events) == ["text"]


def test_thinking_emitted_when_enabled():
    msg = AIMessage(content="answer")
    msg.additional_kwargs = {"reasoning_content": "full reasoning"}
    msg.reasoning_content = "full reasoning"
    events = messages_to_app_events([msg], enable_thinking=True)
    assert _types(events) == ["thinking", "text"]
    assert events[0].content == "full reasoning"


def test_thinking_from_blocks():
    """Anthropic-style thinking blocks in content are extracted."""
    msg = AIMessage(content=[{"type": "thinking", "thinking": "block thought"}])
    events = messages_to_app_events([msg], enable_thinking=True)
    assert any(e.type == "thinking" and e.content == "block thought" for e in events)


def test_thinking_emitted_before_text():
    msg = AIMessage(content="answer")
    msg.reasoning_content = "reasons"
    events = messages_to_app_events([msg], enable_thinking=True)
    assert events[0].type == "thinking"
    assert events[1].type == "text"


# ---------------------------------------------------------------------------
# Multi-turn order
# ---------------------------------------------------------------------------


def test_multi_turn_order():
    msgs = [
        HumanMessage(content="turn1"),
        AIMessage(content="answer1"),
        HumanMessage(content="turn2"),
        AIMessage(content="", tool_calls=[{"name": "bash", "args": {}, "id": "c1"}]),
        ToolMessage(content="ok", tool_call_id="c1", name="bash"),
        AIMessage(content="answer2"),
    ]
    events = messages_to_app_events(msgs)
    assert _types(events) == [
        "text",
        "tool_call",
        "tool_result",
        "text",
    ]
    assert events[0].content == "answer1"
    assert events[-1].content == "answer2"


def test_full_conversation_with_thinking_and_tools():
    msgs = [
        HumanMessage(content="do it"),
        AIMessage(content="checking", tool_calls=[{"name": "read", "args": {"p": "f"}, "id": "c1"}]),
        ToolMessage(content="data", tool_call_id="c1", name="read"),
        AIMessage(content="done"),
    ]
    events = messages_to_app_events(msgs, enable_thinking=True)
    # thinking suppressed because AIMessages have no reasoning content.
    assert _types(events) == ["text", "tool_call", "tool_result", "text"]


# ---------------------------------------------------------------------------
# Symmetry: messages→events matches stream→events for the same logical sequence
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_symmetry_with_stream_adapter():
    """For the same AIMessage, messages_to_app_events and the stream adapter's
    on_chat_model_end path produce the same event set (AC3)."""
    from app.engine.harness_integration.adapters.stream_events import (
        stream_events_to_app_events,
    )

    # The "ground truth" AIMessage that both paths observe.
    ai_msg = AIMessage(
        content="let me check",
        tool_calls=[{"name": "read", "args": {"p": "x"}, "id": "c1"}],
    )

    # Path 1: history reconstruction.
    batch_events = messages_to_app_events([ai_msg])
    batch_dicts = [e.model_dump() for e in batch_events]

    # Path 2: stream adapter (on_chat_model_end + on_tool_start).
    # Note: stream adapter buffers tool_call until on_tool_start, so we
    # feed both events to get the tool_call emitted.
    streamed: list[dict] = []

    async def on_event(ev):
        streamed.append(ev.model_dump())

    end_event = {"event": "on_chat_model_end", "data": {"output": ai_msg}}
    tool_start = {"event": "on_tool_start", "name": "read", "data": {}}
    _aiter = _async_iter([end_event, tool_start])
    await stream_events_to_app_events(_aiter, on_event)

    # Batch produces [text, tool_call]; stream produces [text, tool_call_start, tool_call].
    # Compare only text + tool_call (skip tool_call_start which is stream-only).
    streamed_no_start = [e for e in streamed if e.get("type") != "tool_call_start"]
    assert batch_dicts == streamed_no_start


def _async_iter(items):
    async def gen():
        for x in items:
            yield x

    return gen()


# ---------------------------------------------------------------------------
# Non-reconstructable event types
# ---------------------------------------------------------------------------


def test_no_transient_events_reconstructed():
    """tool_call_start / *_delta / error are never emitted from messages."""
    msgs = [
        AIMessage(content="x", tool_calls=[{"name": "t", "args": {}, "id": "c1"}]),
        ToolMessage(content="r", tool_call_id="c1", name="t"),
    ]
    events = messages_to_app_events(msgs)
    forbidden = {"tool_call_start", "thinking_delta", "text_delta", "error"}
    assert forbidden.isdisjoint({e.type for e in events})
