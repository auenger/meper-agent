"""LLM 摘要模块测试:历史渲染 + 按ID回填 + 缓存。"""
from __future__ import annotations

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from agent_flow_harness.context_engineering.llm_summary import (
    apply_cached_summary,
    render_history_for_summary,
)
from agent_flow_harness.context_engineering.summary_cache import (
    SummaryCache,
    SummaryResult,
)


# ---------------------------------------------------------------------------
# render_history_for_summary
# ---------------------------------------------------------------------------


def test_render_keeps_user_text_original() -> None:
    """用户消息原文保留(不截断)。"""
    msgs = [HumanMessage(content="帮我查所有工艺路线的详细信息", id="h1")]
    result = render_history_for_summary(msgs)
    assert "帮我查所有工艺路线的详细信息" in result
    assert "[用户]" in result


def test_render_tool_result_only_reference() -> None:
    """工具结果不喂原始内容,只留引用(formatter 注入时带工具名)。"""
    big_content = '{"data": {"rows": [...]}}' * 100
    msgs = [
        AIMessage(content="", tool_calls=[{"name": "kb_search", "args": {"q": "x"}, "id": "c1"}]),
        ToolMessage(content=big_content, tool_call_id="c1"),
    ]
    # 传 formatter:工具结果带 recall 引用。
    result = render_history_for_summary(
        msgs, reference_formatter=lambda t: f"(recall:{t})",
    )
    assert big_content not in result  # 大结果内容不进摘要
    assert "(recall:c1)" in result   # formatter 标记

    # 不传 formatter:只留"已返回",不硬编码工具名。
    result2 = render_history_for_summary(msgs)
    assert "已返回" in result2
    assert "recall_tool_result" not in result2  # harness 不硬编码工具名


def test_render_tool_calls_as_summary() -> None:
    """工具调用渲染成「已调用 X(参数概要)」。"""
    msgs = [
        AIMessage(content="", tool_calls=[{"name": "process_route_list", "args": {"page": 1}, "id": "c1"}]),
    ]
    result = render_history_for_summary(msgs)
    assert "已调用 process_route_list" in result
    assert "page" in result  # 参数概要


def test_render_ai_text_original() -> None:
    """AI 文本回复原文保留。"""
    msgs = [AIMessage(content="根据查询,共有13条工艺路线", id="a1")]
    result = render_history_for_summary(msgs)
    assert "根据查询,共有13条工艺路线" in result


# ---------------------------------------------------------------------------
# apply_cached_summary
# ---------------------------------------------------------------------------


def test_apply_summary_replaces_covered_by_id() -> None:
    """按ID精确替换被覆盖的消息,插入摘要。"""
    messages = [
        SystemMessage(content="sys", id="sys"),
        HumanMessage(content="q1", id="h1"),   # covered
        AIMessage(content="a1", id="a1"),       # covered
        HumanMessage(content="q2", id="h2"),   # 新增,保留
    ]
    result = SummaryResult(summary_text="摘要内容", covered_ids=["h1", "a1"])
    rebuilt, inserted = apply_cached_summary(messages, result)
    assert inserted
    assert len(rebuilt) == 3  # sys + summary + h2
    assert isinstance(rebuilt[0], SystemMessage) and rebuilt[0].content == "sys"
    assert isinstance(rebuilt[1], SystemMessage) and rebuilt[1].id == "llm_summary"
    assert "摘要内容" in rebuilt[1].content
    assert rebuilt[2].content == "q2"


def test_apply_summary_preserves_new_messages() -> None:
    """压缩期间新增的消息(不在covered_ids)完整保留。"""
    messages = [
        SystemMessage(content="sys", id="sys"),
        HumanMessage(content="old", id="old"),      # covered
        AIMessage(content="new1", id="new1"),       # 新增
        HumanMessage(content="new2", id="new2"),     # 新增
    ]
    result = SummaryResult(summary_text="摘要", covered_ids=["old"])
    rebuilt, _ = apply_cached_summary(messages, result)
    ids = [getattr(m, "id", "") for m in rebuilt]
    assert "new1" in ids and "new2" in ids
    assert "old" not in ids


def test_apply_summary_no_match_returns_original() -> None:
    """covered_ids 不匹配任何消息 → 原样返回,inserted=False。"""
    messages = [SystemMessage(content="sys", id="sys")]
    result = SummaryResult(summary_text="摘要", covered_ids=["nonexistent"])
    rebuilt, inserted = apply_cached_summary(messages, result)
    assert not inserted
    assert len(rebuilt) == 1


def test_apply_summary_skips_no_id_messages() -> None:
    """无 id 的消息不被误匹配(covered_ids 里无 None/空串)。"""
    messages = [
        SystemMessage(content="sys", id="sys"),
        HumanMessage(content="no_id_msg"),  # 无 id
        HumanMessage(content="has_id", id="h1"),  # covered
    ]
    result = SummaryResult(summary_text="摘要", covered_ids=["h1"])
    rebuilt, inserted = apply_cached_summary(messages, result)
    assert inserted
    # 无 id 的消息保留(没被误匹配)。
    assert any(m.content == "no_id_msg" for m in rebuilt)


# ---------------------------------------------------------------------------
# SummaryCache
# ---------------------------------------------------------------------------


def test_cache_set_get_clear() -> None:
    cache = SummaryCache()
    cache.set("s1", SummaryResult("摘要", ["m1"]))
    result = cache.get("s1")
    assert result is not None
    assert result.summary_text == "摘要"
    assert result.covered_ids == ["m1"]
    cache.clear("s1")
    assert cache.get("s1") is None


def test_cache_running_prevents_duplicate() -> None:
    cache = SummaryCache()
    assert not cache.is_running("s1")
    cache.mark_running("s1")
    assert cache.is_running("s1")  # 正在跑
    cache.clear_running("s1")
    assert not cache.is_running("s1")


@pytest.mark.asyncio
async def test_compress_history_with_llm_mock() -> None:
    """后台压缩任务(mock LLM):完成→存缓存→清running。"""
    from agent_flow_harness.context_engineering.llm_summary import (
        compress_history_with_llm,
    )

    class _MockLLM:
        async def ainvoke(self, messages, _config=None):
            return AIMessage(content="这是LLM生成的语义摘要")

    cache = SummaryCache()
    cache.mark_running("test_session")
    outer = [HumanMessage(content="问题1", id="m1"), AIMessage(content="回答1", id="m2")]
    await compress_history_with_llm(_MockLLM(), outer, "test_session", cache)
    # 完成后缓存有结果。
    result = cache.get("test_session")
    assert result is not None
    assert "语义摘要" in result.summary_text
    assert result.covered_ids == ["m1", "m2"]
    # running 标记清除。
    assert not cache.is_running("test_session")
