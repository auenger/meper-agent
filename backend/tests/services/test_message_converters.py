"""Regression: tool_result timeline entry must carry is_error for failed tools.

工具执行失败(ToolMessage.status="error")持久化到 timeline 时必须写入
``is_error=True``,前端历史消息据此显示红叉而非绿勾。
"""
from __future__ import annotations

from app.services.message_converters import messages_to_timeline_entries
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
