"""按轮分区测试 — split_by_turns 的边界与切分。"""
from __future__ import annotations

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from agent_flow_harness.context_engineering.turns import (
    DEFAULT_PROTECTED_TURNS,
    count_turns,
    split_by_turns,
)


def _history(n_turns: int) -> list:
    """构造 n_turns 轮历史(每轮 Human + AI),不含 system。"""
    msgs = []
    for i in range(n_turns):
        msgs.append(HumanMessage(content=f"q{i}", id=f"h{i}"))
        msgs.append(AIMessage(content=f"a{i}", id=f"a{i}"))
    return msgs


def test_count_turns_counts_human_messages() -> None:
    assert count_turns(_history(3)) == 3
    assert count_turns([]) == 0


def test_split_enough_turns() -> None:
    """7轮:倒数第5个 HumanMessage(含)之后是 recent(5轮),之前是 outer(2轮)。"""
    history = _history(7)
    outer, recent = split_by_turns(history, n_turns=5)
    # recent 应含最近5轮 = 10条;outer 含最早2轮 = 4条。
    assert len(recent) == 10
    assert len(outer) == 4
    # recent 第一个应是倒数第5个 HumanMessage(h2)。
    assert isinstance(recent[0], HumanMessage)
    assert recent[0].content == "q2"
    # outer 最后一个应是 a1(第2轮的AI)。
    assert outer[-1].content == "a1"


def test_split_insufficient_turns_all_recent() -> None:
    """不足5轮:全部归入 recent,outer 为空。"""
    history = _history(3)
    outer, recent = split_by_turns(history, n_turns=5)
    assert outer == []
    assert len(recent) == 6


def test_split_exactly_n_turns_all_recent() -> None:
    """正好5轮:全部是 recent(没有5轮之外)。"""
    history = _history(5)
    outer, recent = split_by_turns(history, n_turns=5)
    assert outer == []
    assert len(recent) == 10


def test_default_protected_turns_is_5() -> None:
    assert DEFAULT_PROTECTED_TURNS == 5


def test_split_with_tool_messages_in_turns() -> None:
    """工具消息跟在 AI 后,按 HumanMessage 切分时落在正确的段。"""
    history = [
        HumanMessage(content="q0", id="h0"),
        AIMessage(content="", tool_calls=[{"name": "t", "args": {}, "id": "c0"}]),
        ToolMessage(content="r0", tool_call_id="c0"),
        AIMessage(content="a0"),
        HumanMessage(content="q1", id="h1"),
        AIMessage(content="a1"),
    ]
    # n_turns=1: 只有最后一轮(q1,a1)是 recent,q0 那轮(含工具)在 outer。
    outer, recent = split_by_turns(history, n_turns=1)
    assert recent[0].content == "q1"
    assert any(isinstance(m, ToolMessage) for m in outer)
