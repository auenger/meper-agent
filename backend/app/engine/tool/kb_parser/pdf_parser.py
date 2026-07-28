"""PDF parser — extracts text page-by-page via PyMuPDF (``fitz``).

Streaming one page at a time avoids loading an entire large PDF into memory.
Each page becomes a :class:`TextBlock` carrying its 1-based page number so
citations can point back to the source page.
"""
from __future__ import annotations

from app.engine.tool.kb_parser.base import ParseResult, TextBlock


def parse_pdf(file_bytes: bytes) -> ParseResult:
    import fitz  # PyMuPDF

    blocks: list[TextBlock] = []
    # ``stream`` opens from bytes without touching the filesystem.
    with fitz.open(stream=file_bytes, filetype="pdf") as doc:
        for page_num, page in enumerate(doc, start=1):
            text = page.get_text("text") or ""
            text = text.strip()
            if text:
                blocks.append(TextBlock(text=text, page=page_num))
        total = doc.page_count
    return ParseResult(blocks=blocks, file_type="pdf", total_pages=total)


__all__ = ["parse_pdf"]
