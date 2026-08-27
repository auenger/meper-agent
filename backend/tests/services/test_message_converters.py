"""Regression: tool_result timeline entry must carry is_error for failed tools.

工具执行失败(ToolMessage.status="error")持久化到 timeline 时必须写入
``is_error=True``,前端历史消息据此显示红叉而非绿勾。
"""
from __future__ import annotations

from app.services.message_converters import (
    extract_final_answer,
    messages_to_timeline_entries,
)
from langchain_core.messages import AIMessage, ToolMessage


def _tool_result_entries(messages: list) -> list[dict]:
    entries = messages_to_timeline_entries(messages)
    return [e for e in entries if e.get("type") == "tool_result"]


def test_error_tool_message_marks_is_error() -> None:
    """ToolMessage(status='error') → tool_result entry 带 is_error=True。"""
    messages = [
        AIMessage(
            content="",
            tool_calls=[{"name": "boom", "args": {}, "id": "c1"}],
        ),
        ToolMessage(
            content="Error executing tool: connection refused",
            name="boom",
            tool_call_id="c1",
            status="error",
        ),
    ]
    entries = _tool_result_entries(messages)
    assert len(entries) == 1
    assert entries[0]["is_error"] is True
    assert "connection refused" in entries[0]["content"]


def test_success_tool_message_has_no_is_error() -> None:
    """正常 ToolMessage(status 默认) → tool_result entry 不带 is_error(或非 True),
    旧数据 / 旧前端按 falsy 处理为正常完成。"""
    messages = [
        AIMessage(
            content="",
            tool_calls=[{"name": "ok", "args": {}, "id": "c2"}],
        ),
        ToolMessage(content="done", name="ok", tool_call_id="c2"),
    ]
    entries = _tool_result_entries(messages)
    assert len(entries) == 1
    assert entries[0].get("is_error") is not True


def test_mixed_success_and_error_tools() -> None:
    """一轮里多个工具:一个成功一个失败,各自 entry 的 is_error 互不影响。"""
    messages = [
        AIMessage(
            content="",
            tool_calls=[
                {"name": "ok", "args": {}, "id": "c1"},
                {"name": "boom", "args": {}, "id": "c2"},
            ],
        ),
        ToolMessage(content="done", name="ok", tool_call_id="c1"),
        ToolMessage(
            content="Error executing tool: boom",
            name="boom",
            tool_call_id="c2",
            status="error",
        ),
    ]
    entries = _tool_result_entries(messages)
    assert len(entries) == 2
    by_id = {e["tool_call_id"]: e for e in entries}
    assert by_id["c1"].get("is_error") is not True
    assert by_id["c2"]["is_error"] is True


# ---------------------------------------------------------------------------
# GLM anthropic-compat quirk：正文藏在 thinking 块的额外 text 字段
# ---------------------------------------------------------------------------

_GLM_QUIRK_BLOCK = {
    "signature": "b6ff287f29a4407e9b68ebee",
    "thinking": "Vague but can proceed.",
    "type": "thinking",
    "text": "好的！这是最终回答。",
}


def test_glm_quirk_timeline_keeps_answer_when_thinking_disabled() -> None:
    """enable_thinking=False 时 quirk 块的正文不能被整块丢弃。"""
    messages = [AIMessage(content=[dict(_GLM_QUIRK_BLOCK)])]
    entries = messages_to_timeline_entries(messages, enable_thinking=False)

    texts = [e for e in entries if e.get("type") == "text"]
    thinks = [e for e in entries if e.get("type") == "thinking"]
    assert len(texts) == 1
    assert texts[0]["content"] == "好的！这是最终回答。"
    # 思考过程按开关抑制
    assert thinks == []


def test_glm_quirk_timeline_shows_thinking_when_enabled() -> None:
    """enable_thinking=True 时思考与正文并存。"""
    messages = [AIMessage(content=[dict(_GLM_QUIRK_BLOCK)])]
    entries = messages_to_timeline_entries(messages, enable_thinking=True)

    texts = [e for e in entries if e.get("type") == "text"]
    thinks = [e for e in entries if e.get("type") == "thinking"]
    assert len(texts) == 1
    assert texts[0]["content"] == "好的！这是最终回答。"
    assert len(thinks) == 1
    assert thinks[0]["content"] == "Vague but can proceed."


def test_standard_blocks_timeline_unchanged() -> None:
    """标准 Anthropic thinking+text 两块：thinking 随开关、正文恒在。"""
    messages = [AIMessage(content=[
        {"type": "thinking", "thinking": "推理", "signature": "sig"},
        {"type": "text", "text": "回答"},
    ])]
    entries = messages_to_timeline_entries(messages, enable_thinking=False)
    assert [e["content"] for e in entries if e["type"] == "text"] == ["回答"]
    assert [e for e in entries if e["type"] == "thinking"] == []


def test_extract_final_answer_from_glm_quirk_block() -> None:
    """chat invoke 路径：quirk 块只取正文，signature 不进答案。"""
    messages = [AIMessage(content=[dict(_GLM_QUIRK_BLOCK)])]
    answer = extract_final_answer(messages)
    assert answer == "好的！这是最终回答。"
    assert "signature" not in answer


def test_extract_final_answer_from_standard_blocks() -> None:
    messages = [AIMessage(content=[
        {"type": "thinking", "thinking": "推理", "signature": "sig"},
        {"type": "text", "text": "正文"},
    ])]
    assert extract_final_answer(messages) == "正文"


def test_extract_final_answer_plain_string() -> None:
    messages = [AIMessage(content="纯文本回答")]
    assert extract_final_answer(messages) == "纯文本回答"
