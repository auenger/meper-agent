"""compress_node / llm_node 测试 — system 连续性与历史替换语义。

回归 ``Received multiple non-consecutive system messages``：压缩后历史必须
整体替换（而非追加），且所有 SystemMessage 连续在最前。
"""
from __future__ import annotations

import pytest
from langchain_core.messages import AIMessage, HumanMessage, RemoveMessage, SystemMessage

from agent_flow_harness.graph.nodes.llm_nodes import (
    _system_messages_first,
    compress_node,
)


def _config(llm=None, *, context_window=None, context_strategy=None) -> dict:
    configurable: dict = {}
    if llm is not None:
        configurable["llm"] = llm
    if context_window is not None:
        configurable["context_window"] = context_window
    if context_strategy is not None:
        configurable["context_strategy"] = context_strategy
    return {"configurable": configurable}


class _RecordingLLM:
    """记录每次 ainvoke 收到的 messages，返回固定 AIMessage。"""

    def __init__(self, reply: str = "done") -> None:
        self._reply = reply
        self.calls: list[list] = []

    @property
    def model_name(self) -> str:
        return "gpt-4o-mini"

    def bind_tools(self, _tools):  # noqa: ANN001, ANN202
        return self

    async def ainvoke(self, messages, _config=None):  # noqa: ANN001
        self.calls.append(list(messages))
        return AIMessage(content=self._reply)


# ---------------------------------------------------------------------------
# compress_node — 替换语义 + system 连续
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_compress_node_replaces_not_appends(base_state) -> None:
    """压缩分支返回的 patch 以 RemoveMessage(REMOVE_ALL) 开头 → 整体替换。"""
    llm = _RecordingLLM()
    # 构造超阈值的长历史（小 context_window 强制压缩）。
    base_state["messages"] = [SystemMessage(content="sys", id="sys")] + [
        HumanMessage(content="h" * 200) if i % 2 == 0
        else AIMessage(content="a" * 200)
        for i in range(40)
    ]
    patch = await compress_node(
        base_state, _config(llm, context_window=64),
    )
    messages_patch = patch["messages"]
    # patch 第一条必须是清空指令。
    assert isinstance(messages_patch[0], RemoveMessage)
    # 后面接压缩结果（条数远小于原 41 条）。
    rebuilt = messages_patch[1:]
    assert len(rebuilt) < len(base_state["messages"])


@pytest.mark.asyncio
async def test_compress_node_keeps_system_continuous(base_state) -> None:
    """compress_node 输出的消息序列里，所有 system 连续在最前。"""
    llm = _RecordingLLM()
    base_state["messages"] = [SystemMessage(content="AGENT_PROMPT", id="sys")] + [
        HumanMessage(content="h" * 200) if i % 2 == 0
        else AIMessage(content="a" * 200)
        for i in range(40)
    ]
    patch = await compress_node(base_state, _config(llm, context_window=64))
    msgs = patch["messages"][1:]  # 去掉 RemoveMessage

    # 原始 system 在最前，且所有 system 连续（触底压缩可能只剩原始 system）。
    assert msgs[0].content == "AGENT_PROMPT"
    system_count = sum(1 for m in msgs if isinstance(m, SystemMessage))
    assert all(isinstance(m, SystemMessage) for m in msgs[:system_count])
    assert not any(isinstance(m, SystemMessage) for m in msgs[system_count:])


@pytest.mark.asyncio
async def test_compress_node_noop_when_under_threshold(base_state) -> None:
    """未超阈值时返回空 patch（不改动 state）。"""
    llm = _RecordingLLM()
    base_state["messages"] = [
        SystemMessage(content="sys", id="sys"),
        HumanMessage(content="hi"),
    ]
    patch = await compress_node(
        base_state, _config(llm, context_window=1_000_000),
    )
    assert patch == {}


# ---------------------------------------------------------------------------
# _system_messages_first — llm_node 防御层
# ---------------------------------------------------------------------------


def test_system_messages_first_already_leading_is_noop() -> None:
    """system 已在最前时原样返回，不分配新列表。"""
    msgs = [
        SystemMessage(content="sys", id="sys"),
        HumanMessage(content="hi", id="h"),
        AIMessage(content="yo", id="a"),
    ]
    assert _system_messages_first(msgs) is msgs


def test_system_messages_first_moves_scattered_to_front() -> None:
    """散落的 system 被移到最前，相对顺序不变，history 顺序不变。"""
    msgs = [
        HumanMessage(content="h1", id="h1"),
        SystemMessage(content="sys-mid", id="sm"),
        AIMessage(content="a1", id="a1"),
        SystemMessage(content="sys-tail", id="st"),
        HumanMessage(content="h2", id="h2"),
    ]
    result = _system_messages_first(msgs)
    # 前两条是 system，按原相对顺序。
    assert [m.content for m in result[:2]] == ["sys-mid", "sys-tail"]
    # history 顺序保持。
    assert [m.content for m in result[2:]] == ["h1", "a1", "h2"]
