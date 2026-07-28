"""KB document processing pipeline: parse → clean → chunk.

Public entry points:
- :func:`parse` — raw bytes → :class:`ParseResult`
- :func:`clean` — remove extraction noise
- :func:`split` — ParseResult → chunk dicts (with token sizing)
"""
from app.engine.tool.kb_parser.base import ParseResult, TextBlock, parse
from app.engine.tool.kb_parser.chunker import split
from app.engine.tool.kb_parser.cleaner import clean

__all__ = ["ParseResult", "TextBlock", "parse", "clean", "split"]
