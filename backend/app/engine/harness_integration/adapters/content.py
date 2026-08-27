"""Content-block text extraction — single source of truth.

LLM 回复的 ``content`` 有三种形态，所有「取正文 / 取思考」的消费方都应经由
这里，而不是各自散落 isinstance 分支：

* 纯字符串（多数 provider / 未开思考）。
* 标准块列表 ``[{"type": "thinking", ...}, {"type": "text", "text": ...}]``
  （Anthropic extended thinking，thinking 与 text 各自成块）。
* GLM anthropic-compat quirk：无视 ``thinking={"type": "disabled"}`` 仍返回
  单个 thinking 块，且把最终回答塞进额外 ``text`` 字段（anthropic SDK
  ``extra="allow"`` 放行，langchain 仅在 text 为 None 时清理）→
  ``{"signature": ..., "thinking": ..., "type": "thinking", "text": 正文}``。
"""

from __future__ import annotations

from typing import Any


def extract_answer_text(content: Any) -> str:
    """Pull the answer text from a message ``content`` value.

    Tolerant of plain-string content, list-of-blocks content, and a single
    dict block. Collects the ``text`` field of every block regardless of its
    ``type`` — standard ``text`` blocks and the GLM quirk block (answer hidden
    in a thinking block's extra ``text`` field) both yield the answer, while
    ``thinking`` / ``signature`` metadata never leaks into the result.
    """
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, dict):
        content = [content]
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict):
                text = block.get("text")
                if isinstance(text, str) and text:
                    parts.append(text)
        return "".join(parts)
    return ""


def extract_thinking_text(message: Any) -> str:
    """Pull reasoning text from a chunk / message.

    Looks first at ``additional_kwargs.reasoning_content`` (OpenAI-style),
    then falls back to ``reasoning_content`` attribute, then to
    ``type="thinking"`` blocks in ``content`` (Anthropic-style).
    """
    additional = getattr(message, "additional_kwargs", None) or {}
    reasoning = additional.get("reasoning_content")
    if isinstance(reasoning, str) and reasoning:
        return reasoning

    direct = getattr(message, "reasoning_content", None)
    if isinstance(direct, str) and direct:
        return direct

    content = getattr(message, "content", None)
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "thinking":
                parts.append(block.get("thinking") or "")
        joined = "".join(parts)
        if joined:
            return joined
    return ""


__all__ = ["extract_answer_text", "extract_thinking_text"]
