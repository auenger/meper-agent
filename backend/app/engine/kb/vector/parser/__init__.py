"""KB document processing pipeline: parse → clean → chunk.

Public entry points:
- :func:`parse` — raw bytes → :class:`ParseResult`
- :func:`clean` — remove extraction noise
- :func:`split` — ParseResult → chunk dicts (with token sizing)
"""
from app.engine.kb.vector.parser.base import ParseResult, TextBlock, parse
from app.engine.kb.vector.parser.chunker import split
from app.engine.kb.vector.parser.cleaner import clean

__all__ = ["ParseResult", "TextBlock", "parse", "clean", "split"]
