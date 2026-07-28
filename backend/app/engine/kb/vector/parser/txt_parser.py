"""Plain-text parser — returns the raw text as a single block."""
from __future__ import annotations

from app.engine.kb.vector.parser.base import ParseResult, TextBlock


def parse_txt(file_bytes: bytes) -> ParseResult:
    text = file_bytes.decode("utf-8", errors="replace")
    if text and text[0] == "\ufeff":
        text = text[1:]
    return ParseResult(blocks=[TextBlock(text=text)], file_type="txt")


__all__ = ["parse_txt"]
