"""CSV parser — extracts row text via the standard ``csv`` module (no deps).

CSV is treated as a single unpaginated :class:`TextBlock`. Rows are emitted
as pipe-joined cells (matching the table convention in word/xlsx parsers) so
the transcript stays line-oriented for chunking and retrieval.

Encoding: Excel exports CSVs as UTF-8-with-BOM or GBK on Chinese systems.
We try UTF-8 first (``utf-8-sig`` transparently strips a BOM), then fall back
to GBK, finally to UTF-8 with replacement chars — so a mojibake file still
indexes instead of failing the whole document.
"""
from __future__ import annotations

import csv
import io

from app.engine.kb.vector.parser.base import ParseResult, TextBlock


def _decode(file_bytes: bytes) -> str:
    """Decode CSV bytes, tolerating BOM and GBK encodings."""
    for enc in ("utf-8-sig", "gbk"):
        try:
            return file_bytes.decode(enc)
        except UnicodeDecodeError:
            continue
    # Last resort: never raise on a bad file — index something usable.
    return file_bytes.decode("utf-8", errors="replace")


def parse_csv(file_bytes: bytes) -> ParseResult:
    text = _decode(file_bytes)
    reader = csv.reader(io.StringIO(text))
    lines: list[str] = []
    for row in reader:
        cells = [(c or "").strip() for c in row]
        if not any(cells):
            continue
        line = " | ".join(c for c in cells if c)
        if line:
            lines.append(line)
    body = "\n".join(lines)
    return ParseResult(blocks=[TextBlock(text=body)], file_type="csv")


__all__ = ["parse_csv"]
