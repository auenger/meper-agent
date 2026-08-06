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
    # Heading/section path of this block (e.g. "章节A > 子节B"). Filled by
    # structure-aware parsers (e.g. parse_word_structured); empty for flat
    # parsers. Carried into chunk dicts so retrieval results can cite the
    # section a chunk belongs to.
    section: str = ""
    # FileRef ids of images extracted from this block's page (PDF image
    # extraction / scan-page renders). Empty for text-only sources. Carried
    # into chunk dicts so the UI can display the associated images.
    image_ref_ids: list[str] = field(default_factory=list)


@dataclass
class ParseResult:
    """Output of parsing one document."""

    blocks: list[TextBlock] = field(default_factory=list)
    file_type: str = ""
    total_pages: int | None = None


def parse(file_bytes: bytes, file_type: str, *, structured: bool = False) -> ParseResult:
    """Dispatch to the right parser by file extension/type.

    ``file_type`` is normalized to lowercase without leading dot
    (e.g. "pdf", "docx", "md", "txt").

    When ``structured`` is True, structure-aware file types use a parser that
    preserves document structure (e.g. Word heading styles become ``section``
    on each TextBlock; HTML keeps its heading tags for the header splitter).
    File types without a dedicated structured parser fall back to their
    regular flat parser; the chunker handles graceful degradation for them.
    Currently ``docx`` (``parse_word_structured``) and ``html/htm``
    (``parse_html_structured``) provide structured variants; md/markdown keeps
    raw text (structure splitting is done inside the chunker via LangChain
    splitters) and does not need a separate parser.
    """
    ft = (file_type or "").lower().lstrip(".")
    # Alias common variants.
    if ft in ("md", "markdown"):
        from app.engine.kb.vector.parser.markdown_parser import parse_markdown

        return parse_markdown(file_bytes)
    if ft == "txt":
        from app.engine.kb.vector.parser.txt_parser import parse_txt

        return parse_txt(file_bytes)
    if ft == "pdf":
        from app.engine.kb.vector.parser.pdf_parser import parse_pdf

        return parse_pdf(file_bytes)
    if ft == "docx":
        if structured:
            from app.engine.kb.vector.parser.word_parser import parse_word_structured

            return parse_word_structured(file_bytes)
        from app.engine.kb.vector.parser.word_parser import parse_word

        return parse_word(file_bytes)
    if ft == "pptx":
        from app.engine.kb.vector.parser.pptx_parser import parse_pptx

        return parse_pptx(file_bytes)
    if ft == "xlsx":
        from app.engine.kb.vector.parser.xlsx_parser import parse_xlsx

        return parse_xlsx(file_bytes)
    if ft == "csv":
        from app.engine.kb.vector.parser.csv_parser import parse_csv

        return parse_csv(file_bytes)
    if ft in ("html", "htm"):
        if structured:
            from app.engine.kb.vector.parser.html_parser import parse_html_structured

            return parse_html_structured(file_bytes)
        from app.engine.kb.vector.parser.html_parser import parse_html

        return parse_html(file_bytes)
    raise ValueError(
        f"不支持的文件类型: {file_type}（支持 pdf/docx/pptx/xlsx/csv/md/txt/html）"
    )


__all__ = ["TextBlock", "ParseResult", "parse"]
