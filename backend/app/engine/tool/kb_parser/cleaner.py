"""Document cleaner — heuristic noise removal before chunking.

Removes common extraction artifacts that hurt chunking/retrieval quality:
- Repeated header/footer lines (PDF page edges, often identical across pages)
- Excessive blank lines
- Isolated control characters / replacement chars

This is rule-based (no LLM) to keep the indexing pipeline cheap and
deterministic. Markdown/TXT input is largely passed through since it is
already clean human-authored text; the heavy lifting targets PDF output.
"""
from __future__ import annotations

import re
from collections import Counter

from app.engine.tool.kb_parser.base import ParseResult, TextBlock

# Lines that are almost certainly page furniture, not content.
_FURNITURE_RE = re.compile(
    r"^(?:page\s+\d+\s*(?:of|/)\s*\d+|\d+\s*/\s*\d+|-\s*\d+\s*-)$",
    re.IGNORECASE,
)
# Runs of 3+ whitespace chars (collapsed later).
_WS_RUN_RE = re.compile(r"[ \t]{3,}")
# Replacement/control chars PyMuPDF may emit.
_CTRL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def _clean_line(line: str) -> str:
    line = _CTRL_RE.sub("", line)
    line = _WS_RUN_RE.sub(" ", line)
    return line.rstrip()


def _detect_furniture_lines(blocks: list[TextBlock]) -> set[str]:
    """Find lines repeated on many pages — likely headers/footers.

    Only meaningful for multi-page (PDF) input where the same short line
    appears on a high fraction of pages. A line qualifies if it shows up on
    >= 40% of pages (and >= 3 pages) and is short (<= 80 chars).
    """
    if len(blocks) < 3:
        return set()
    page_count = len(blocks)
    per_page_first_last: list[set[str]] = []
    for b in blocks:
        lines = [ln.strip() for ln in b.text.splitlines() if ln.strip()]
        if not lines:
            per_page_first_last.append(set())
            continue
        # Headers/footers live at page top/bottom; sample first 2 + last 2 lines.
        sample = lines[:2] + lines[-2:]
        per_page_first_last.append({ln.lower() for ln in sample if len(ln) <= 80})

    # Count on how many distinct pages each candidate appears.
    appearances: Counter[str] = Counter()
    for s in per_page_first_last:
        appearances.update(s)

    threshold = max(3, int(page_count * 0.4))
    return {ln for ln, cnt in appearances.items() if cnt >= threshold}


def clean(result: ParseResult) -> ParseResult:
    """Return a new ParseResult with noise removed from each block."""
    furniture = _detect_furniture_lines(result.blocks)
    cleaned_blocks: list[TextBlock] = []
    for b in result.blocks:
        raw_lines = b.text.splitlines()
        out_lines: list[str] = []
        blank_run = 0
        for ln in raw_lines:
            stripped = ln.strip()
            if not stripped:
                blank_run += 1
                # Collapse 2+ consecutive blanks into 1.
                if blank_run <= 1:
                    out_lines.append("")
                continue
            blank_run = 0
            if _FURNITURE_RE.match(stripped):
                continue
            if stripped.lower() in furniture:
                continue
            out_lines.append(_clean_line(ln))
        text = "\n".join(out_lines).strip()
        if text:
            cleaned_blocks.append(TextBlock(text=text, page=b.page, section=b.section))
    return ParseResult(blocks=cleaned_blocks, file_type=result.file_type,
                       total_pages=result.total_pages)


__all__ = ["clean"]
