"""Markdown parser — returns the raw text as a single block.

Markdown is treated as plain text (no structural splitting at parse time;
structure-aware splitting is a post-MVP enhancement via MarkdownHeaderTextSplitter).
"""
from __future__ import annotations

from app.engine.kb.vector.parser.base import ParseResult


def parse_markdown(file_bytes: bytes) -> ParseResult:
    text = file_bytes.decode("utf-8", errors="replace")
    # Drop a BOM if present.
    if text and text[0] == "\ufeff":
        text = text[1:]
    from app.engine.kb.vector.parser.base import TextBlock

    return ParseResult(blocks=[TextBlock(text=text)], file_type="md")


__all__ = ["parse_markdown"]
