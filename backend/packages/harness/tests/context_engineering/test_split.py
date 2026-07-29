"""split_system_history 测试 — system / history 分离工具。"""
from __future__ import annotations

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from agent_flow_harness.context_engineering.split import split_system_history


def test_collects_leading_system() -> None:
    """开头连续的 system 被收集到 system_msgs，其余进 history。"""
    msgs = [
        SystemMessage(content="AGENT_PROMPT", id="sys"),
        HumanMessage(content="hi", id="h1"),
        AIMessage(content="hello", id="a1"),
    ]
    system_msgs, history = split_system_history(msgs)
    assert len(system_msgs) == 1
    assert system_msgs[0].content == "AGENT_PROMPT"
    assert [m.content for m in history] == ["hi", "hello"]


def test_collects_system_scattered_in_middle_and_tail() -> None:
    """散布在中段/末尾的 system 也全部被收集，history 中不含 system。"""
    msgs = [
        SystemMessage(content="sys0", id="s0"),
        HumanMessage(content="h1", id="h1"),
        SystemMessage(content="sys-mid", id="sm"),
        AIMessage(content="a1", id="a1"),
        HumanMessage(content="h2", id="h2"),
        SystemMessage(content="sys-tail", id="st"),
    ]
    system_msgs, history = split_system_history(msgs)
    # 保持原相对顺序。
    assert [m.content for m in system_msgs] == ["sys0", "sys-mid", "sys-tail"]
    # history 里一个 system 都没有。
    assert all(not isinstance(m, SystemMessage) for m in history)
    assert [m.content for m in history] == ["h1", "a1", "h2"]


def test_no_system_returns_empty_and_full_history() -> None:
    """没有 system 时 system_msgs 为空，history 为原列表。"""
    msgs = [HumanMessage(content="h"), AIMessage(content="a")]
    system_msgs, history = split_system_history(msgs)
    assert system_msgs == []
    assert len(history) == 2


def test_preserves_tool_messages_in_history() -> None:
    """ToolMessage 留在 history，不被误判为 system。"""
    msgs = [
        SystemMessage(content="sys", id="s"),
        AIMessage(content="", tool_calls=[{"name": "t", "args": {}, "id": "c1"}]),
        ToolMessage(content="r", tool_call_id="c1"),
    ]
    system_msgs, history = split_system_history(msgs)
    assert len(system_msgs) == 1
    assert len(history) == 2
    assert isinstance(history[1], ToolMessage)
