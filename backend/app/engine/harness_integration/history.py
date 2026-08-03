"""Legacy history reconstruction — MessageRecord → LangChain messages.

Used by the ``MIGRATE_LEGACY_SESSIONS`` path to serialize an old session's
persisted messages into a thread checkpoint on first access. New sessions
never call this — their history lives entirely in the checkpointer thread.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    # Optional async callback that renders file_ids → attachment text block.
    FileRenderFn = Callable[[list[str]], Awaitable[str | None]]


async def rebuild_messages_from_records(
    records: list[dict],
    *,
    render_files: FileRenderFn | None = None,
) -> list:
    """Convert persisted session messages to LangChain message objects.

    Reconstructs the LangChain message sequence from ``timeline_entries``
    so the LLM sees full multi-turn context including previous tool
    invocations and workflow previews.

    **Merging rule**: a ``text`` block and the ``tool_call``(s) immediately
    following it belong to the same LLM call and are merged into **one**
    ``AIMessage(content=text, tool_calls=[...])``.

    Args:
        records: MessageRecord dicts (from ``messages`` collection).
        render_files: Optional async callback to render file attachments
            for user messages with ``file_ids``. Returns appended text.
    """
    result: list = []

    for record in records:
        role = record.get("role", "")
        content = record.get("content", "")
        timeline = record.get("timeline_entries", [])

        if role == "user":
            # Re-inject file content for multi-turn context
            file_ids = record.get("file_ids") or []
            if file_ids and render_files is not None:
                try:
                    rendered = await render_files(file_ids)
                    if rendered:
                        content = (content or "") + rendered
                except Exception:
                    pass  # best-effort
            if content:
                result.append(HumanMessage(content=content))
        elif role == "agent":
            # Walk the timeline entries, merging text + adjacent tool_calls
            # into a single AIMessage per LLM call.
            i = 0
            while i < len(timeline):
                entry = timeline[i]
                etype = entry.get("type", "")

                if etype == "text":
                    text_content = entry.get("content", "")
                    tool_calls: list[dict] = []
                    j = i + 1
                    while j < len(timeline) and timeline[j].get("type") == "tool_call":
                        tc = timeline[j]
                        tool_calls.append({
                            "name": tc.get("tool_name", ""),
                            "args": tc.get("args", {}),
                            "id": tc.get("id", "") or f"call_{j}",
                        })
                        j += 1
                    if tool_calls:
                        result.append(AIMessage(
                            content=text_content,
                            tool_calls=tool_calls,
                        ))
                    else:
                        result.append(AIMessage(content=text_content))
                    i = j
                elif etype == "tool_call":
                    tool_calls = [{
                        "name": entry.get("tool_name", ""),
                        "args": entry.get("args", {}),
                        "id": entry.get("id", "") or f"call_{i}",
                    }]
                    j = i + 1
                    while j < len(timeline) and timeline[j].get("type") == "tool_call":
                        tc = timeline[j]
                        tool_calls.append({
                            "name": tc.get("tool_name", ""),
                            "args": tc.get("args", {}),
                            "id": tc.get("id", "") or f"call_{j}",
                        })
                        j += 1
                    result.append(AIMessage(content="", tool_calls=tool_calls))
                    i = j
                elif etype == "tool_result":
                    result.append(ToolMessage(
                        content=entry.get("content", ""),
                        # 优先用持久化的 tool_call_id(新版);回退到旧版的 id 字段;
                        # 都没有时合成一个(与上方 tool_call 重建的 fallback 对齐)。
                        tool_call_id=entry.get("tool_call_id")
                        or entry.get("id", "")
                        or f"call_{i}",
                    ))
                    i += 1
                else:
                    # thinking / unknown — skip
                    i += 1

    return result


__all__ = ["rebuild_messages_from_records"]
