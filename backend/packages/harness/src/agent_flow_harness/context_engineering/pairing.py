"""ToolMessage 配对保护 — 保证压缩后 tool_call 都有对应 tool_result。

核心安全约束：压缩/滑动后不能出现 tool_call 没有对应 tool_result（会让 LLM
报错或幻觉），也不能出现孤儿 tool_result（没有对应 tool_call）。
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from langchain_core.messages import BaseMessage


def ensure_tool_pairing(messages: "list[BaseMessage]") -> "list[BaseMessage]":
    """清理 messages，保证 tool_call/result 配对完整。

    - 丢弃没有 result 的 tool_call（纯 tool_call 且 content 空的 AIMessage 整条丢）
    - 丢弃没有 call 的孤儿 ToolMessage
    """
    from langchain_core.messages import AIMessage, ToolMessage

    # 收集所有 call_id
    call_ids: set[str] = set()
    result_ids: set[str] = set()

    for m in messages:
        if isinstance(m, ToolMessage):
            result_ids.add(m.tool_call_id)
        elif isinstance(m, AIMessage):
            for tc in getattr(m, "tool_calls", []) or []:
                if isinstance(tc, dict):
                    call_ids.add(tc.get("id", ""))

    # 有效配对：同时有 call 和 result
    valid_pairs = call_ids & result_ids

    result: list[BaseMessage] = []
    for m in messages:
        if isinstance(m, ToolMessage):
            if m.tool_call_id in valid_pairs:
                result.append(m)
        elif isinstance(m, AIMessage):
            tool_calls = getattr(m, "tool_calls", []) or []
            if tool_calls:
                valid_tcs = [
                    tc for tc in tool_calls
                    if isinstance(tc, dict) and tc.get("id") in valid_pairs
                ]
                if valid_tcs and len(valid_tcs) == len(tool_calls):
                    # 全部 tool_call 都有配对 → 原样保留。
                    result.append(m)
                elif valid_tcs:
                    # 部分配对 → 重建 AIMessage,只保留有效 tool_call(剥离孤儿)。
                    result.append(AIMessage(content=m.content, tool_calls=valid_tcs))
                elif m.content:
                    # 无有效 tool_call 但有文本 → 保留文本,丢弃所有 tool_call。
                    result.append(AIMessage(content=m.content))
                # 无 valid_tcs 且无 content → 丢弃整条
            else:
                result.append(m)
        else:
            result.append(m)

    return result


__all__ = ["ensure_tool_pairing"]
