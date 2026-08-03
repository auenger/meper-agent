"""AC10 cover: migrated context helpers behave correctly inside the harness."""

from __future__ import annotations

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from agent_flow_harness.engine.context import (
    compress_messages,
    estimate_context_tokens,
    estimate_message_tokens,
    estimate_tokens,
    extract_model_name,
    should_compress,
)


class _NamedLLM:
    """Tiny stand-in exposing ``model_name`` like a LangChain chat model."""

    def __init__(self, name: str) -> None:
        self.model_name = name


def test_estimate_tokens_positive_for_text() -> None:
    assert estimate_tokens("") >= 0
    assert estimate_tokens("hello world") > 0
    assert estimate_tokens("a" * 1000) > estimate_tokens("a")


def test_estimate_message_tokens_handles_message_and_dict() -> None:
    msg = HumanMessage(content="hello")
    assert estimate_message_tokens(msg) > 0
    assert estimate_message_tokens({"role": "user", "content": "hello"}) > 0


def test_extract_model_name_reads_attribute() -> None:
    assert extract_model_name(_NamedLLM("gpt-4o-mini")) == "gpt-4o-mini"


def test_should_compress_respects_context_window_override() -> None:
    """A tiny context_window forces compression; a huge one does not."""
    big = [HumanMessage(content=f"line {i}") for i in range(20)]
    assert should_compress(big, "x", context_window=64) is True
    assert should_compress(big, "x", context_window=1_000_000) is False


def test_compress_messages_shrinks_and_keeps_recent() -> None:
    """Compression produces fewer messages and preserves the last N verbatim."""
    messages = [SystemMessage(content="sys")] + [
        HumanMessage(content=f"h{i}") for i in range(30)
    ]
    compressed = compress_messages(messages, "gpt-4o-mini", context_window=64)
    assert len(compressed) < len(messages)
    # The most recent messages are kept untouched at the tail.
    assert compressed[-1].content == messages[-1].content


def test_compress_messages_noop_under_threshold() -> None:
    """When nothing exceeds the threshold the list is returned unchanged in length."""
    small = [HumanMessage(content="hi")]
    out = compress_messages(small, "gpt-4o-mini", context_window=1_000_000)
    assert out == small


def test_compress_messages_preserves_ai_message_shape() -> None:
    """An AIMessage in the kept tail round-trips with its content intact."""
    messages = [HumanMessage(content=f"m{i}") for i in range(40)]
    messages.append(AIMessage(content="final"))
    out = compress_messages(messages, "gpt-4o-mini", context_window=64)
    assert isinstance(out[-1], AIMessage)
    assert out[-1].content == "final"


def _assert_system_continuous(compressed: list) -> None:
    """所有 SystemMessage 必须连续出现在列表最前，否则被模型拒绝。"""
    system_count = sum(1 for m in compressed if isinstance(m, SystemMessage))
    assert system_count >= 1
    assert all(isinstance(m, SystemMessage) for m in compressed[:system_count])
    assert not any(isinstance(m, SystemMessage) for m in compressed[system_count:])


def test_compress_messages_keeps_system_continuous() -> None:
    """压缩后所有 SystemMessage 必须连续出现在列表最前。

    回归 ``Received multiple non-consecutive system messages``：旧实现把
    摘要 SystemMessage 追加到末尾，与开头的原始 system 被历史隔开，触发
    langchain-anthropic 校验失败。
    """
    messages = [SystemMessage(content="AGENT_PROMPT", id="sys")] + [
        HumanMessage(content=f"h{i}")
        if i % 2 == 0
        else AIMessage(content=f"a{i}")
        for i in range(40)
    ]
    compressed = compress_messages(messages, "gpt-4o-mini", context_window=64)

    # 原始 system 必须保留在最前面。
    assert compressed[0].content == "AGENT_PROMPT"
    _assert_system_continuous(compressed)


def test_compress_messages_summary_has_stable_id() -> None:
    """非触底场景下，只有一条摘要 SystemMessage，且固定 id="summary"。

    构造「待总结段长 + 保留段短」的历史，使一轮压缩即可收敛（不触底），
    验证摘要唯一 & 幂等 id。
    """
    messages = [SystemMessage(content="sys", id="sys")] + [
        HumanMessage(content="x" * 40)
        if i % 2 == 0
        else AIMessage(content="x" * 40)
        for i in range(40)
    ]
    # keep_last=3：把 37 条旧消息压成 1 条摘要 + 保留 3 条，体积骤降到阈值下。
    compressed = compress_messages(messages, "gpt-4o-mini", keep_last=3, context_window=300)
    summary_msgs = [m for m in compressed if "对话历史摘要" in str(m.content)]
    assert len(summary_msgs) == 1
    assert summary_msgs[0].id == "summary"


def test_compress_messages_does_not_swallow_system_into_summary() -> None:
    """原始 system 不被揉进摘要文本 —— 它原样保留在列表里。"""
    messages = [SystemMessage(content="NEVER_SUMMARISE_ME", id="sys")] + [
        HumanMessage(content=f"h{i}")
        if i % 2 == 0
        else AIMessage(content=f"a{i}")
        for i in range(40)
    ]
    compressed = compress_messages(messages, "gpt-4o-mini", context_window=64)
    # 原始 system 原样存在（content 完整、未进摘要）。
    assert any(m.content == "NEVER_SUMMARISE_ME" for m in compressed)
    # 摘要文本里不应出现 system 原文（旧 bug 会把 system 卷进 _build_summary）。
    summary_text = " ".join(
        str(m.content) for m in compressed
        if isinstance(m, SystemMessage) and "对话历史摘要" in str(m.content)
    )
    assert "NEVER_SUMMARISE_ME" not in summary_text


def test_compress_messages_no_orphan_tool_pairs() -> None:
    """压缩后保留段(recent)无孤儿 tool_call / tool_result。

    回归：旧实现按 history[:-keep_last] 切片，切点可能落在配对中间，导致
    recent 段出现孤儿 → API 400 "tool_use ids ... no tool_use block"。
    """
    from langchain_core.messages import ToolMessage

    # 构造含工具调用回合的长历史，让切片点可能落在配对中间。
    messages = [SystemMessage(content="sys", id="sys"), HumanMessage(content="start")]
    for i in range(12):
        messages.extend([
            AIMessage(
                content="",
                tool_calls=[{"name": "kb_search", "args": {"q": f"t{i}"}, "id": f"c{i}"}],
                id=f"a{i}",
            ),
            ToolMessage(content=f"结果{i}", tool_call_id=f"c{i}"),
            HumanMessage(content=f"追问{i}", id=f"h{i}"),
        ])

    compressed = compress_messages(messages, "gpt-4o-mini", keep_last=5, context_window=200)

    # 收集 recent 段（摘要之后的非 system 消息）里的 call/result 配对。
    non_system = [m for m in compressed if not isinstance(m, SystemMessage)]
    call_ids = set()
    for m in non_system:
        if isinstance(m, AIMessage):
            for tc in (getattr(m, "tool_calls", None) or []):
                if isinstance(tc, dict):
                    call_ids.add(tc.get("id", ""))
    result_ids = {
        m.tool_call_id for m in non_system if isinstance(m, ToolMessage)
    }
    # 每个 tool_result 必须有对应的 tool_call（无孤儿 result）。
    orphans = result_ids - call_ids
    assert not orphans, f"孤儿 tool_result: {orphans}"
    # 每个 tool_call 若 content 为空则必须有 result（无孤儿 call）。
    for m in non_system:
        if isinstance(m, AIMessage) and not m.content:
            for tc in (m.tool_calls or []):
                tcid = tc.get("id", "") if isinstance(tc, dict) else ""
                assert tcid in result_ids, f"孤儿 tool_call: {tcid}"


# ---------------------------------------------------------------------------
# estimate_context_tokens — 用 input_tokens 优先,fallback len//4
# ---------------------------------------------------------------------------


def test_estimate_context_tokens_uses_input_tokens() -> None:
    """有 AIMessage(带 usage_metadata)时,用 input_tokens + 新增消息估算。"""
    ai = AIMessage(
        content="回答",
        usage_metadata={"input_tokens": 1000, "output_tokens": 50, "total_tokens": 1050},
    )
    # AIMessage 之后新增了一条 HumanMessage("新问题"*2 = 6字符)
    new_content = "新问题" * 2
    msgs = [SystemMessage(content="sys"), HumanMessage(content="hi"), ai,
            HumanMessage(content=new_content)]
    result = estimate_context_tokens(msgs)
    # 1000(input_tokens) + 新增消息估算(6字符//4 + 4元数据 = 5)
    assert result == 1000 + estimate_message_tokens(HumanMessage(content=new_content))


def test_estimate_context_tokens_no_ai_fallback() -> None:
    """无 AIMessage(首轮) → fallback 全量 len//4。"""
    msgs = [SystemMessage(content="sys"), HumanMessage(content="hello world")]
    result = estimate_context_tokens(msgs)
    assert result == estimate_message_tokens(msgs[0]) + estimate_message_tokens(msgs[1])


def test_estimate_context_tokens_ai_no_usage_fallback() -> None:
    """AIMessage 无 usage_metadata → fallback 全量估算。"""
    ai = AIMessage(content="回答")  # 无 usage_metadata
    msgs = [SystemMessage(content="sys"), ai]
    result = estimate_context_tokens(msgs)
    assert result == estimate_message_tokens(msgs[0]) + estimate_message_tokens(ai)


def test_estimate_context_tokens_uses_last_ai() -> None:
    """多条 AIMessage 时,用最后一条的 input_tokens。"""
    ai1 = AIMessage(content="r1", usage_metadata={"input_tokens": 500, "output_tokens": 10, "total_tokens": 510})
    ai2 = AIMessage(content="r2", usage_metadata={"input_tokens": 2000, "output_tokens": 20, "total_tokens": 2020})
    msgs = [SystemMessage(content="sys"), ai1, HumanMessage(content="q"), ai2]
    result = estimate_context_tokens(msgs)
    # 用 ai2 的 2000,ai2 之后无新增消息
    assert result == 2000
