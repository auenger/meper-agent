"""HTML parser — extracts visible text via BeautifulSoup (``html.parser``).

HTML is treated as a single unpaginated :class:`TextBlock`. Non-content
elements (``script``, ``style``, ``noscript``, ``template``) are stripped
first so their source/markup doesn't pollute retrieval. The remaining text
is collapsed to one line per block element, keeping a readable transcript
without the original whitespace noise.

``parse_html_structured`` is the structure-aware variant: it keeps the
cleaned HTML markup (with ``<h1>..<h6>`` intact) so the chunker's
``HTMLHeaderTextSplitter`` can split by heading tags.

Uses the stdlib ``html.parser`` backend (no lxml dependency required).
Also handles ``.htm``.
"""
from __future__ import annotations

from app.engine.kb.vector.parser.base import ParseResult, TextBlock

# Tags that carry code/style/markup, not human-readable content.
_DROP_TAGS = ("script", "style", "noscript", "template", "head")


def parse_html(file_bytes: bytes) -> ParseResult:
    from bs4 import BeautifulSoup

    # errors="replace": tolerate malformed bytes instead of failing the doc.
    soup = BeautifulSoup(file_bytes, "html.parser")
    for name in _DROP_TAGS:
        for tag in soup.find_all(name):
            tag.decompose()
    # Prefer <body> if present; otherwise the whole soup.
    root = soup.body or soup
    # Separator="\n" gives one line per block element; get_text collapses runs.
    text = root.get_text(separator="\n", strip=True)
    # Collapse 2+ consecutive blank lines left by empty block elements.
    lines = [ln for ln in text.splitlines() if ln.strip()]
    body = "\n".join(lines)
    return ParseResult(blocks=[TextBlock(text=body)], file_type="html")


def parse_html_structured(file_bytes: bytes) -> ParseResult:
    """Structure-aware HTML parse: keep cleaned markup for header splitting.

    Strips non-content tags (script/style/head/...) exactly like
    :func:`parse_html`, but instead of flattening to text it returns the
    serialized HTML of ``<body>`` (or the whole soup) so the chunker can run
    ``HTMLHeaderTextSplitter`` on it and recover heading structure.
    """
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(file_bytes, "html.parser")
    for name in _DROP_TAGS:
        for tag in soup.find_all(name):
            tag.decompose()
    root = soup.body or soup
    # Preserve heading tags (h1-h6) so HTMLHeaderTextSplitter can split on them.
    markup = root.decode_contents() if hasattr(root, "decode_contents") else str(root)
    return ParseResult(blocks=[TextBlock(text=markup)], file_type="html")


__all__ = ["parse_html", "parse_html_structured"]
