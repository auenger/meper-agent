"""PowerPoint (.pptx) parser — extracts text from slides via python-pptx.

Each slide becomes one :class:`TextBlock` carrying its 1-based slide number,
so retrieval results can cite the source slide. Text is pulled from text
frames and table cells (tables often hold key data in slides).

``.ppt`` (legacy binary) is NOT supported by python-pptx; only ``.pptx``.
"""
from __future__ import annotations

import io

from app.engine.kb.vector.parser.base import ParseResult, TextBlock


def parse_pptx(file_bytes: bytes) -> ParseResult:
    from pptx import Presentation

    prs = Presentation(io.BytesIO(file_bytes))
    blocks: list[TextBlock] = []
    for slide_no, slide in enumerate(prs.slides, start=1):
        parts: list[str] = []
        for shape in slide.shapes:
            # Text frames (titles, body text, text boxes).
            if shape.has_text_frame:
                t = shape.text_frame.text.strip()
                if t:
                    parts.append(t)
            # Tables — emit one pipe-joined line per row (matches word_parser).
            if shape.has_table:
                for row in shape.table.rows:
                    cells = [(c.text or "").strip() for c in row.cells]
                    line = " | ".join(c for c in cells if c)
                    if line:
                        parts.append(line)
        if parts:
            blocks.append(TextBlock(text="\n".join(parts), page=slide_no))
    return ParseResult(
        blocks=blocks, file_type="pptx", total_pages=len(blocks)
    )


__all__ = ["parse_pptx"]
