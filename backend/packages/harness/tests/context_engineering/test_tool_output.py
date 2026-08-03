"""工具输出压缩测试 — 结构感知截断 + 配对完整性保护。"""
from __future__ import annotations

import json

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from agent_flow_harness.context_engineering.tool_output import (
    compress_tool_outputs,
    summarize_tool_content,
)


def _kb_hits(n: int, text_len: int = 500) -> str:
    """构造 kb_search 风格的 JSON 结果:n 条命中,每条 text 长 text_len。"""
    hits = [
        {
            "kb_id": "kb_1",
            "text": "检索片段内容" * (text_len // 6),
            "score": 0.9 - i * 0.05,
            "source_file": f"doc_{i}.md",
            "page": i,
        }
        for i in range(n)
    ]
    return json.dumps(hits, ensure_ascii=False)


# ---------------------------------------------------------------------------
# summarize_tool_content — 单条工具结果内容压缩
# ---------------------------------------------------------------------------


def test_small_content_unchanged() -> None:
    """未超阈值的内容原样返回(同一对象,不复制)。"""
    content = "很短的工具结果"
    assert summarize_tool_content(content) is content


def test_large_json_array_compressed_to_minimal_hint() -> None:
    """大 JSON 数组极简化成一行提示(不保留残缺内容,细节靠 recall)。"""
    content = _kb_hits(10, text_len=600)  # 10 条 × 600 字 → 很大
    result = summarize_tool_content(content, max_output=800)
    assert len(result) < len(content)
    # 极简:只剩一行提示,告知命中数。
    assert "10 条结果" in result
    assert "已压缩" in result
    # 不再保留 JSON 结构(残缺 JSON 反而误导)。
    assert len(result) < 50  # 一行提示,几十字符


def test_plain_text_compressed_to_minimal_hint() -> None:
    """非 JSON 多行文本极简化成行数提示。"""
    lines = [f"第{i}行内容" * 10 for i in range(50)]
    content = "\n".join(lines)
    result = summarize_tool_content(content, max_output=200)
    assert len(result) < 50
    assert "50 行" in result
    assert "已压缩" in result


# ---------------------------------------------------------------------------
# compress_tool_outputs — 消息列表级别,配对完整
# ---------------------------------------------------------------------------


def test_compress_tool_output_preserves_pairing_and_ids() -> None:
    """已消费的工具结果截断后:条数不变,tool_call_id 保留,tool_calls 不动。"""
    big_result = _kb_hits(8, text_len=800)
    msgs = [
        AIMessage(
            content="",
            tool_calls=[{"name": "kb_search", "args": {"query": "x"}, "id": "call_1"}],
        ),
        ToolMessage(content=big_result, tool_call_id="call_1"),
        AIMessage(content="基于结果的回答"),  # 消费了该工具结果 → 可截断
        HumanMessage(content="追问"),
    ]
    # 不传 formatter:harness 纯截断,不加任何引用标记(分层纯净)。
    result = compress_tool_outputs(msgs)
    assert len(result) == 4  # 条数不变
    # AIMessage(tool_calls) 完全不动(配对另一半)。
    assert result[0] is msgs[0]
    assert result[0].tool_calls == msgs[0].tool_calls
    # ToolMessage 内容变小,但 tool_call_id 保留。
    assert isinstance(result[1], ToolMessage)
    assert result[1].tool_call_id == "call_1"
    assert len(result[1].content) < len(big_result)
    # 无 formatter → 不含工具名标记。
    assert "recall_tool_result" not in result[1].content
    # 消费它的 AIMessage 和 HumanMessage 不动。
    assert result[2] is msgs[2]
    assert result[3] is msgs[3]

    # 传 formatter:被压缩结果末尾带应用层提供的引用标记。
    def fmt(tcid: str) -> str:
        return f"\n[REF:{tcid}]"

    result2 = compress_tool_outputs(msgs, reference_formatter=fmt)
    assert "[REF:call_1]" in result2[1].content


def test_compress_tool_output_noop_when_all_small() -> None:
    """所有 ToolMessage 都没超限时原样返回(同一列表对象)。"""
    msgs = [
        AIMessage(content="", tool_calls=[{"name": "t", "args": {}, "id": "c1"}]),
        ToolMessage(content="小结果", tool_call_id="c1"),
    ]
    result = compress_tool_outputs(msgs)
    assert result is msgs


def test_compress_tool_output_only_truncates_oversized() -> None:
    """只有超限且已消费的 ToolMessage 被改,小的保留原对象。"""
    msgs = [
        ToolMessage(content="小", tool_call_id="small"),
        ToolMessage(content=_kb_hits(5, text_len=700), tool_call_id="big"),
        AIMessage(content="已消费以上工具结果"),  # 消费 → 可截断
    ]
    result = compress_tool_outputs(msgs)
    assert result[0] is msgs[0]  # 小的不动
    assert result[1] is not msgs[1]  # 大的被替换
    assert len(result[1].content) < len(msgs[1].content)


def test_compress_tool_output_protects_unconsumed() -> None:
    """未被 AI 消费的最新工具结果绝对不截断(AI 还没基于它生成回复)。"""
    big = _kb_hits(8, text_len=800)
    # REACT 进行中:刚调完工具拿到结果,AI 还没用它(后面无 AIMessage)。
    msgs = [
        AIMessage(content="", tool_calls=[{"name": "kb_search", "args": {}, "id": "new"}]),
        ToolMessage(content=big, tool_call_id="new"),
    ]
    result = compress_tool_outputs(msgs)
    assert result is msgs  # 完全不动(未消费 → 受保护)


def test_compress_tool_output_mixed_consumed_and_unconsumed() -> None:
    """新旧工具混合:旧的(已消费)截断,新的(未消费)保护。"""
    big = _kb_hits(8, text_len=800)
    msgs = [
        AIMessage(content="", tool_calls=[{"name": "t", "args": {}, "id": "old"}]),
        ToolMessage(content=big, tool_call_id="old"),
        AIMessage(content="用了old"),  # 消费 old
        AIMessage(content="", tool_calls=[{"name": "t", "args": {}, "id": "new"}]),
        ToolMessage(content=big, tool_call_id="new"),  # 未消费 → 保护
    ]
    result = compress_tool_outputs(msgs)
    old_msg = [m for m in result if isinstance(m, ToolMessage) and m.tool_call_id == "old"][0]
    new_msg = [m for m in result if isinstance(m, ToolMessage) and m.tool_call_id == "new"][0]
    assert len(old_msg.content) < len(big)  # old 已消费 → 截断
    assert new_msg.content == big  # new 未消费 → 完整保留
