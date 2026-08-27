"""Message conversion utilities — LangChain messages ↔ SSE/timeline dicts.

Shared by stream / invoke / resume endpoints for persistence and response
formatting. Extracted from agents.py to keep the API layer thin.
"""
from __future__ import annotations

import json

from app.engine.harness_integration.adapters.content import extract_answer_text


def safe_json(obj) -> str:
    """Safely serialize to JSON, falling back to ``str()``."""
    try:
        return json.dumps(obj, default=str, ensure_ascii=False)
    except Exception:
        return str(obj)


def safe_str(value) -> str:
    """Coerce any LangChain scalar/value into a plain string."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, bytes):
        try:
            return value.decode("utf-8")
        except (UnicodeDecodeError, AttributeError):
            return repr(value)
    if isinstance(value, (dict, list)):
        try:
            return json.dumps(value, ensure_ascii=False, default=str)
        except (TypeError, ValueError):
            return str(value)
    for field in ("text", "content", "thinking", "value"):
        v = getattr(value, field, None)
        if isinstance(v, str):
            return v
    return str(value)


def extract_final_answer(messages: list) -> str:
    """Extract the final answer text from the message list.

    Finds the last AIMessage with textual content (no tool_calls)
    and returns its content as a plain string.
    """
    from langchain_core.messages import AIMessage

    for msg in reversed(messages):
        if isinstance(msg, AIMessage) and msg.content and not msg.tool_calls:
            # content 可能是块列表（thinking+text）或 GLM quirk 单块 dict，
            # 统一提取正文，避免把 signature 等元数据 json.dumps 进答案。
            return extract_answer_text(msg.content)
    return str(messages[-1]) if messages else ""


def messages_to_timeline_entries(
    messages: list,
    enable_thinking: bool = False,
    *,
    include_user: bool = False,
) -> list[dict]:
    """Build structured timeline entries for message persistence.

    Args:
        include_user: When True, emit ``{"type": "user"}`` entries for
            ``HumanMessage`` objects. Defaults to ``False`` so the chat path
            (which stores user input as a separate ``role="user"`` Message
            document) is unaffected. Workflow node-timeline callers set this
            to ``True`` so the user query appears in the trace.
    """
    return messages_to_sse_events(
        messages, enable_thinking=enable_thinking, include_user=include_user,
    )


def messages_to_sse_events(
    messages: list,
    enable_thinking: bool = False,
    *,
    include_user: bool = False,
) -> list[dict]:
    """Convert a list of LangChain messages into structured SSE event dicts."""
    from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

    events: list[dict] = []
    for msg in messages:
        if isinstance(msg, HumanMessage):
            # 用户输入（SystemMessage 始终跳过）。
            # 仅当 include_user=True 时输出，避免影响 chat 路径（该路径
            # 将用户输入作为独立 Message 文档存储）。
            if include_user:
                content = safe_str(msg.content)
                if content:
                    events.append({"type": "user", "content": content})

        elif isinstance(msg, AIMessage):
            thinking_parts: list[str] = []
            text_parts: list[str] = []

            for piece in _iter_content_blocks(msg):
                ptype = piece.get("type")
                if ptype == "thinking":
                    if enable_thinking:
                        t = piece.get("thinking") or ""
                        if t:
                            thinking_parts.append(safe_str(t))
                    # GLM anthropic-compat quirk：最终回答藏在 thinking 块的
                    # 额外 text 字段里——正文不受 enable_thinking 开关影响，
                    # 必须并入正文，否则 timeline 会整块丢失回答。
                    answer = piece.get("text") or ""
                    if answer:
                        text_parts.append(safe_str(answer))
                elif ptype == "text":
                    t = piece.get("text") or ""
                    if t:
                        text_parts.append(safe_str(t))

            for t in thinking_parts:
                events.append({"type": "thinking", "content": t})

            if text_parts:
                events.append({"type": "text", "content": "\n".join(text_parts)})
            elif isinstance(msg.content, str) and msg.content.strip() and not msg.tool_calls:
                events.append({"type": "text", "content": msg.content})

            if msg.tool_calls:
                for tc in msg.tool_calls:
                    events.append({
                        "type": "tool_call",
                        "tool_name": tc.get("name", ""),
                        "args": tc.get("args", {}),
                        "id": tc.get("id", ""),
                    })

        elif isinstance(msg, ToolMessage):
            entry: dict = {
                "type": "tool_result",
                "tool_name": msg.name or "",
                "content": safe_str(msg.content),
                "tool_call_id": msg.tool_call_id or "",
            }
            # 保留错误标记:ToolMessage.status == "error" 表示工具执行失败
            # (ToolException / 校验错误 / 连接错误等)。前端据此在历史消息里显示
            # 红叉而非绿勾;旧数据没有该字段,前端按 falsy 处理为正常完成。
            if getattr(msg, "status", None) == "error":
                entry["is_error"] = True
            events.append(entry)

    return events


def _iter_content_blocks(msg) -> list[dict]:
    """Yield content pieces as dicts with a ``type`` key."""
    blocks: list[dict] = []

    content = getattr(msg, "content", None)

    if isinstance(content, str):
        if content.strip():
            blocks.append({"type": "text", "text": content})
    elif isinstance(content, list):
        for item in content:
            if isinstance(item, dict):
                blocks.append(item)
            elif isinstance(item, str) and item.strip():
                blocks.append({"type": "text", "text": item})
            else:
                btype = getattr(item, "type", None)
                if btype == "thinking":
                    blocks.append({
                        "type": "thinking",
                        "thinking": getattr(item, "thinking", ""),
                        "signature": getattr(item, "signature", ""),
                    })
                elif btype == "text":
                    blocks.append({
                        "type": "text",
                        "text": getattr(item, "text", ""),
                    })
                elif btype:
                    blocks.append({"type": btype, **{
                        k: getattr(item, k) for k in ("text", "thinking", "content")
                        if hasattr(item, k)
                    }})

    cb = getattr(msg, "content_blocks", None)
    if cb and not any(b.get("type") == "thinking" for b in blocks):
        for item in cb:
            btype = getattr(item, "type", None)
            if btype == "thinking":
                blocks.insert(0, {
                    "type": "thinking",
                    "thinking": getattr(item, "thinking", ""),
                })
            elif btype == "text" and not any(b.get("type") == "text" for b in blocks):
                blocks.append({
                    "type": "text",
                    "text": getattr(item, "text", ""),
                })

    return blocks


__all__ = [
    "extract_final_answer",
    "messages_to_sse_events",
    "messages_to_timeline_entries",
    "safe_json",
    "safe_str",
]
