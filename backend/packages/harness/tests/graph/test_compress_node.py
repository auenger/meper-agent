"""compress_node / llm_node 测试 — system 连续性与历史替换语义。

回归 ``Received multiple non-consecutive system messages``：压缩后历史必须
整体替换（而非追加），且所有 SystemMessage 连续在最前。
"""
from __future__ import annotations

import pytest
from langchain_core.messages import AIMessage, HumanMessage, RemoveMessage, SystemMessage, ToolMessage

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


# ---------------------------------------------------------------------------
# 按轮分区:四种情况
# ---------------------------------------------------------------------------

import json as _json  # noqa: E402


def _big_tool_result(tcid: str) -> ToolMessage:
    """构造一个超限(>1500字)的已消费工具结果。"""
    content = _json.dumps(
        [{"text": "片段" * 300, "source": "doc.md"} for _ in range(6)],
        ensure_ascii=False,
    )
    return ToolMessage(content=content, tool_call_id=tcid)


def _seven_turns_with_outer_tool() -> list:
    """7轮历史,第1轮有个已消费的大工具结果(5轮之外)。"""
    msgs = [
        SystemMessage(content="sys", id="sys"),
        AIMessage(content="", tool_calls=[{"name": "kb", "args": {}, "id": "old_tc"}]),
        _big_tool_result("old_tc"),
    ]
    for i in range(7):
        msgs.append(HumanMessage(content=f"q{i}", id=f"h{i}"))
        msgs.append(AIMessage(content=f"a{i}", id=f"a{i}"))
    return msgs


def _patch_msgs(patch: dict | None) -> list:
    """从 compress_node 的 patch 里取出实际消息(去掉 RemoveMessage)。"""
    if not patch:
        return []
    return [m for m in patch["messages"] if getattr(m, "id", "") != "__remove_all__"]


@pytest.mark.asyncio
async def test_case1_insufficient_turns_nothing_done() -> None:
    """情况1:不足5轮 + 未达阈值 → 什么都不做。"""
    msgs = [SystemMessage(content="sys", id="sys")] + [
        HumanMessage(content="q" * 100, id=f"h{i}") if i % 2 == 0
        else AIMessage(content="a" * 100, id=f"a{i}")
        for i in range(6)
    ]
    config = _config(context_window=1_000_000)  # 大窗口,不达阈值
    patch = await compress_node({"messages": msgs, "agent_id": "a", "request_id": "r"}, config)
    assert patch == {}


@pytest.mark.asyncio
async def test_case2_under_threshold_does_nothing() -> None:
    """新规则:未达阈值 → 什么都不做(即使有5轮外大工具也不压)。"""
    msgs = _seven_turns_with_outer_tool()
    config = {
        "configurable": {
            "llm": None,
            "context_window": 1_000_000,  # 大窗口,不达阈值
            "tool_output_reference_formatter": lambda t: f"[REF:{t}]",
        }
    }
    patch = await compress_node({"messages": msgs, "agent_id": "a", "request_id": "r"}, config)
    # 未达阈值 → compress_skipped,什么都不做。
    assert patch == {}


@pytest.mark.asyncio
async def test_level1_outer_tools_compressed() -> None:
    """达阈值 + 5轮外大工具:第1级工具压缩够 → "5轮外工具压缩"。"""
    msgs = _seven_turns_with_outer_tool()
    config = {
        "configurable": {
            "llm": None,
            "context_window": 1200,  # 阈值≈840,工具结果951tok > 阈值 → 触发压缩
            "tool_output_reference_formatter": lambda t: f"[REF:{t}]",
        }
    }
    patch = await compress_node({"messages": msgs, "agent_id": "a", "request_id": "r"}, config)
    result = _patch_msgs(patch)
    # 5轮外的 old_tc 工具被压缩(带 formatter 标记)。
    tool = [m for m in result if isinstance(m, ToolMessage) and m.tool_call_id == "old_tc"]
    assert tool, "5轮外工具应被压缩保留(不是删除)"
    assert "[REF:old_tc]" in tool[0].content
    # 没有走摘要(第1级就够)。
    summaries = [m for m in result if isinstance(m, SystemMessage) and m.id == "summary"]
    assert not summaries, "第1级够,不应触发摘要"


@pytest.mark.asyncio
async def test_oversize_triggers_background_or_discard() -> None:
    """达阈值 + 工具压缩不够 → 触发后台LLM压缩 + 可能丢弃(不再同步机械摘要)。"""
    msgs = [SystemMessage(content="sys", id="sys")]
    for i in range(7):
        msgs.append(HumanMessage(content=f"问题{i}" + "x" * 500, id=f"h{i}"))
        msgs.append(AIMessage(content=f"回答{i}" + "y" * 500, id=f"a{i}"))
    config = _config(context_window=800)  # 阈值≈560,普通历史超了
    patch = await compress_node({"messages": msgs, "agent_id": "a", "request_id": "r"}, config)
    # 新逻辑:不生成同步机械摘要(id="summary"),而是触发后台压缩/丢弃兜底。
    result = _patch_msgs(patch)
    # 不应有同步 summary(机械摘要已移除)。
    summaries = [m for m in result if isinstance(m, SystemMessage) and m.id == "summary"]
    assert not summaries, "不应生成同步机械摘要"
    # 但应该有处理(patch 非空)。
    assert patch != {}


@pytest.mark.asyncio
async def test_recent_turns_protected_when_under_threshold() -> None:
    """5轮内的内容(含大工具)在未达阈值时不被压缩。"""
    # 3轮(不足5),最后一轮有未消费大工具。
    big = _big_tool_result("recent_tc")
    msgs = [
        SystemMessage(content="sys", id="sys"),
        HumanMessage(content="q0", id="h0"),
        AIMessage(content="a0"),
        HumanMessage(content="q1", id="h1"),
        AIMessage(content="a1"),
        HumanMessage(content="q2", id="h2"),
        AIMessage(content="", tool_calls=[{"name": "kb", "args": {}, "id": "recent_tc"}]),
        big,
    ]
    config = _config(context_window=1_000_000)
    patch = await compress_node({"messages": msgs, "agent_id": "a", "request_id": "r"}, config)
    # 不足5轮 → 全保护,不处理。
    assert patch == {}


@pytest.mark.asyncio
async def test_unconsumed_oversized_tool_raises() -> None:
    """未消费的单个工具结果超阈值 → 报错(无法压缩,留着必超窗口)。"""
    big = _big_tool_result("big_tc")  # ~950 tokens
    msgs = [
        SystemMessage(content="sys", id="sys"),
        HumanMessage(content="q", id="h0"),
        AIMessage(content="", tool_calls=[{"name": "kb", "args": {}, "id": "big_tc"}]),
        big,  # 未消费(后面无 AIMessage)
    ]
    # 小窗口:阈值 = 200*0.7 = 140,工具 950 > 140 → 报错。
    config = _config(context_window=200)
    with pytest.raises(ValueError, match="返回结果过大"):
        await compress_node({"messages": msgs, "agent_id": "a", "request_id": "r"}, config)


@pytest.mark.asyncio
async def test_consumed_oversized_tool_not_raises() -> None:
    """已消费的单个工具结果超阈值 → 不报错(会被正常压缩截断)。"""
    big = _big_tool_result("consumed_tc")
    msgs = [
        SystemMessage(content="sys", id="sys"),
        AIMessage(content="", tool_calls=[{"name": "kb", "args": {}, "id": "consumed_tc"}]),
        big,
        AIMessage(content="基于结果回答"),  # 消费了 → 可压缩,不报错
    ]
    config = _config(context_window=200)  # 阈值 140,工具 950 > 140,但已消费
    # 不应抛异常(已消费的工具会被压缩,不是报错)。
    patch = await compress_node({"messages": msgs, "agent_id": "a", "request_id": "r"}, config)
    assert patch != {}  # 正常返回压缩 patch
