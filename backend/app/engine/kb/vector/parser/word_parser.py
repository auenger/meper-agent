"""Word (.docx) parser — extracts paragraph text via python-docx.

``.doc`` (legacy binary) is NOT supported by python-docx; only ``.docx``.
Word documents have no reliable page concept, so all paragraphs are merged
into a single block with ``page=None``.

``parse_word_structured`` is the structure-aware variant: it splits the
document on ``Heading 1/2/3`` paragraph styles, producing one TextBlock per
section with ``section`` set to the heading path (e.g. "章A > 节B").
"""
from __future__ import annotations

import io
import re

from app.engine.kb.vector.parser.base import ParseResult, TextBlock

# python-docx heading style names look like "Heading 1", "Heading 2", ...
# (localized docs may use "标题 1" etc., so we match on the numeric level).
_HEADING_RE = re.compile(r"heading\s*(\d+)", re.IGNORECASE)


def _heading_level(style_name: str) -> int | None:
    """Return 1-9 if ``style_name`` denotes a heading, else None."""
    if not style_name:
        return None
    m = _HEADING_RE.search(style_name)
    if not m:
        return None
    lvl = int(m.group(1))
    return lvl if 1 <= lvl <= 9 else None


def parse_word(file_bytes: bytes) -> ParseResult:
    from docx import Document

    doc = Document(io.BytesIO(file_bytes))
    # Collect non-empty paragraphs. python-docx does not segment by page.
    parts: list[str] = []
    for para in doc.paragraphs:
        text = (para.text or "").strip()
        if text:
            parts.append(text)
    # Also extract table cell text (tables often hold key data).
    for table in doc.tables:
        for row in table.rows:
            cells = [(c.text or "").strip() for c in row.cells]
            line = " | ".join(c for c in cells if c)
            if line:
                parts.append(line)
    return ParseResult(blocks=[TextBlock(text="\n".join(parts))], file_type="docx")


def parse_word_structured(file_bytes: bytes) -> ParseResult:
    """Structure-aware Word parse: one TextBlock per heading section.

    Walks paragraphs in order, tracking the current heading path (like a
    directory cursor). On a Heading N paragraph, the path is truncated to
    depth N-1 and the new heading appended. Non-heading paragraphs accumulate
    into the current section's text. Each section becomes a TextBlock whose
    ``section`` is the joined heading path.

    Tables are appended to the section they appear under (or a top-level
    section when before any heading).
    """
    from docx import Document

    doc = Document(io.BytesIO(file_bytes))

    blocks: list[TextBlock] = []
    # Heading path as a list of (level, title). Truncated/replaced as new
    # headings arrive so it always reflects the nesting of the current section.
    path: list[tuple[int, str]] = []
    # Accumulated text lines for the current section.
    current_lines: list[str] = []

    def _section_label() -> str:
        return " > ".join(title for _, title in path)

    def _flush() -> None:
        if current_lines:
            text = "\n".join(current_lines).strip()
            if text:
                blocks.append(TextBlock(text=text, section=_section_label()))
            current_lines.clear()

    # Iterate body elements in document order so tables interleave correctly
    # with paragraphs (doc.paragraphs / doc.tables are separate lists).
    body = doc.element.body
    # Map XML elements back to python-docx objects.
    para_by_elem = {p._element: p for p in doc.paragraphs}
    table_by_elem = {t._element: t for t in doc.tables}

    for child in body.iterchildren():
        if child in para_by_elem:
            para = para_by_elem[child]
            text = (para.text or "").strip()
            style_name = para.style.name if para.style is not None else ""
            lvl = _heading_level(style_name)
            if lvl is not None and text:
                # New heading: flush previous section, update path.
                _flush()
                # Drop deeper-or-equal levels, then append this heading.
                path = [(pl, pt) for (pl, pt) in path if pl < lvl]
                path.append((lvl, text))
            elif text:
                current_lines.append(text)
        elif child in table_by_elem:
            table = table_by_elem[child]
            for row in table.rows:
                cells = [(c.text or "").strip() for c in row.cells]
                line = " | ".join(c for c in cells if c)
                if line:
                    current_lines.append(line)

    _flush()
    # Fallback: if the document had no headings, emit a single flat block so
    # the chunker still has content to split.
    if not blocks:
        parts: list[str] = []
        for para in doc.paragraphs:
            text = (para.text or "").strip()
            if text:
                parts.append(text)
        for table in doc.tables:
            for row in table.rows:
                cells = [(c.text or "").strip() for c in row.cells]
                line = " | ".join(c for c in cells if c)
                if line:
                    parts.append(line)
        joined = "\n".join(parts)
        if joined.strip():
            blocks.append(TextBlock(text=joined))
    return ParseResult(blocks=blocks, file_type="docx")


__all__ = ["parse_word", "parse_word_structured"]
