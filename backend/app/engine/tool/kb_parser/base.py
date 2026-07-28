"""Document parser layer — extract structured text blocks from raw files.

Each parser turns raw file bytes into a list of :class:`TextBlock`
(page-scoped text segments). Markdown/TXT yield a single block; PDF/Word
yield one block per page/section so chunking can preserve page metadata
for citation.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class TextBlock:
    """A page/section-scoped chunk of extracted text."""

    text: str
    page: int | None = None  # 1-based page number (PDF); None for unpaginated
    section: str = ""  # optional heading/section label


@dataclass
class ParseResult:
    """Output of parsing one document."""

    blocks: list[TextBlock] = field(default_factory=list)
    file_type: str = ""
    total_pages: int | None = None


def parse(file_bytes: bytes, file_type: str) -> ParseResult:
    """Dispatch to the right parser by file extension/type.

    ``file_type`` is normalized to lowercase without leading dot
    (e.g. "pdf", "docx", "md", "txt").
    """
    ft = (file_type or "").lower().lstrip(".")
    # Alias common variants.
    if ft in ("md", "markdown"):
        from app.engine.tool.kb_parser.markdown_parser import parse_markdown

        return parse_markdown(file_bytes)
    if ft == "txt":
        from app.engine.tool.kb_parser.txt_parser import parse_txt

        return parse_txt(file_bytes)
    if ft == "pdf":
        from app.engine.tool.kb_parser.pdf_parser import parse_pdf

        return parse_pdf(file_bytes)
    if ft in ("docx", "doc"):
        from app.engine.tool.kb_parser.word_parser import parse_word

        return parse_word(file_bytes)
    raise ValueError(f"不支持的文件类型: {file_type}（支持 pdf/docx/md/txt）")


__all__ = ["TextBlock", "ParseResult", "parse"]
