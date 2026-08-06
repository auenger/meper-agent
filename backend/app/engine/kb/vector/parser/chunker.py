"""Chunker — split cleaned text blocks into retrieval chunks.

Two strategies are supported, selected per-document at upload time:

- ``recursive`` (default, backward-compatible): token-based recursive
  character split via ``RecursiveCharacterTextSplitter.from_tiktoken_encoder``
  with CJK-friendly separators. Applies to every file type.
- ``structure``: split by the document's own structure first, then apply the
  recursive splitter to any oversized section (two-stage split) so chunks
  never exceed the token budget. The structure splitter is chosen from the
  file type:

      md/markdown → MarkdownHeaderTextSplitter  (# / ## / ###)
      html/htm    → HTMLHeaderTextSplitter      (<h1>/<h2>/<h3>)
      docx        → TextBlock.section carried up by parse_word_structured
                    (heading-style aware, no langchain splitter needed)

  File types without recognizable structure (txt/csv/xlsx/pptx/pdf) fall back
  to the plain ``recursive`` path transparently.

Each produced chunk carries source metadata (page, source_file, section) so
retrieval results can cite their origin. Chunk size/overlap are platform-level
fixed defaults (``settings.KB_VECTOR_CHUNK_SIZE`` / ``KB_VECTOR_CHUNK_OVERLAP``).
"""
from __future__ import annotations

from langchain_text_splitters import (
    HTMLHeaderTextSplitter,
    MarkdownHeaderTextSplitter,
    RecursiveCharacterTextSplitter,
)

from app.core.config import settings
from app.engine.kb.vector.parser.base import ParseResult

# CJK-friendly separator priority: paragraphs → lines → Chinese sentence
# enders → comma → space → (last resort) any character.
_SEPARATORS = ["\n\n", "\n", "。", "！", "？", "；", "，", " ", ""]

# Header levels recognized by the structure splitters (title path depth).
_MD_HEADERS: list[tuple[str, str]] = [
    ("#", "H1"),
    ("##", "H2"),
    ("###", "H3"),
    ("####", "H4"),
]
_HTML_HEADERS: list[tuple[str, str]] = [
    ("h1", "H1"),
    ("h2", "H2"),
    ("h3", "H3"),
    ("h4", "H4"),
]


def _make_splitter() -> RecursiveCharacterTextSplitter:
    return RecursiveCharacterTextSplitter.from_tiktoken_encoder(
        model_name="text-embedding-3-small",
        chunk_size=settings.KB_VECTOR_CHUNK_SIZE,
        chunk_overlap=settings.KB_VECTOR_CHUNK_OVERLAP,
        separators=_SEPARATORS,
    )


def split(
    result: ParseResult,
    source_file: str = "",
    *,
    strategy: str = "recursive",
    file_type: str = "",
) -> list[dict]:
    """Split a ParseResult into a list of chunk dicts.

    Args:
        result: cleaned parse result (one or more page/section-scoped TextBlocks).
        source_file: original filename, attached to every chunk for citation.
        strategy: ``recursive`` (default) or ``structure``.
        file_type: lowercased file extension (e.g. "md", "html", "docx").
            Used by the ``structure`` strategy to pick a splitter; ignored by
            ``recursive``.

    Returns:
        List of ``{text, source_file, page, chunk_index, section}``.
    """
    if strategy == "structure":
        return _split_by_structure(result, source_file, file_type)
    return _split_recursive(result, source_file)


# ── recursive strategy (the historical default) ────────────────────────


def _split_recursive(result: ParseResult, source_file: str) -> list[dict]:
    splitter = _make_splitter()
    chunks: list[dict] = []
    idx = 0
    for block in result.blocks:
        pieces = splitter.split_text(block.text)
        for piece in pieces:
            piece = piece.strip()
            if not piece:
                continue
            chunks.append(
                {
                    "text": piece,
                    "source_file": source_file,
                    "page": block.page,
                    "chunk_index": idx,
                    "section": block.section,
                    "image_ref_ids": block.image_ref_ids,
                }
            )
            idx += 1
    return chunks


# ── structure strategy ─────────────────────────────────────────────────


def _split_by_structure(
    result: ParseResult, source_file: str, file_type: str
) -> list[dict]:
    ft = (file_type or "").lower().lstrip(".")
    if ft in ("md", "markdown"):
        return _split_structured_langchain(result, source_file, _md_splitter)
    if ft in ("html", "htm"):
        return _split_structured_langchain(result, source_file, _html_splitter)
    # docx is handled by parse_word_structured: each block already carries a
    # ``section`` (heading path), so the generic section-aware split below
    # honors it. txt/csv/xlsx/pptx/pdf have no structure → fall back.
    if ft == "docx" and any(b.section for b in result.blocks):
        return _split_blocks_with_section(result, source_file)
    # Unsupported file type for structure splitting → transparent fallback.
    return _split_recursive(result, source_file)


def _md_splitter() -> MarkdownHeaderTextSplitter:
    return MarkdownHeaderTextSplitter(
        headers_to_split_on=_MD_HEADERS,
        strip_headers=True,
    )


def _html_splitter() -> HTMLHeaderTextSplitter:
    return HTMLHeaderTextSplitter(
        headers_to_split_on=_HTML_HEADERS,
    )


def _join_header_path(meta: dict) -> str:
    """Join a langchain header metadata dict into "H1 > H2 > ..." path."""
    parts = [meta.get(key) for key in ("H1", "H2", "H3", "H4")]
    return " > ".join(p for p in parts if p)


def _split_structured_langchain(
    result: ParseResult,
    source_file: str,
    make_splitter,
) -> list[dict]:
    """Structure split for md/html: split by headers, then token-bucket.

    Each block (typically a single block of raw md/html text) is split by the
    header-aware splitter into Documents carrying a header-path metadata dict.
    Each such section is then run through the recursive token splitter (so an
    oversized section never exceeds the embedding budget). The section label
    is attached to every resulting chunk.
    """
    splitter = _make_splitter()
    struct = make_splitter()
    chunks: list[dict] = []
    idx = 0
    for block in result.blocks:
        try:
            docs = struct.split_text(block.text)
        except Exception:
            # Malformed markup etc. → fall back to plain recursive for this block.
            for piece in splitter.split_text(block.text):
                piece = piece.strip()
                if piece:
                    chunks.append(
                        {
                            "text": piece,
                            "source_file": source_file,
                            "page": block.page,
                            "chunk_index": idx,
                            "section": block.section,
                            "image_ref_ids": block.image_ref_ids,
                        }
                    )
                    idx += 1
            continue
        for d in docs:
            section = _join_header_path(d.metadata or {}) or block.section
            for piece in splitter.split_text(d.page_content):
                piece = piece.strip()
                if not piece:
                    continue
                chunks.append(
                    {
                        "text": piece,
                        "source_file": source_file,
                        "page": block.page,
                        "chunk_index": idx,
                        "section": section,
                        "image_ref_ids": block.image_ref_ids,
                    }
                )
                idx += 1
    return chunks


def _split_blocks_with_section(result: ParseResult, source_file: str) -> list[dict]:
    """Split blocks whose ``section`` is already populated (docx structured).

    Two-stage: the recursive splitter enforces the token budget per block,
    while the block's ``section`` (heading path from parse_word_structured)
    is carried onto every chunk produced from it.
    """
    splitter = _make_splitter()
    chunks: list[dict] = []
    idx = 0
    for block in result.blocks:
        for piece in splitter.split_text(block.text):
            piece = piece.strip()
            if not piece:
                continue
            chunks.append(
                {
                    "text": piece,
                    "source_file": source_file,
                    "page": block.page,
                    "chunk_index": idx,
                    "section": block.section,
                    "image_ref_ids": block.image_ref_ids,
                }
            )
            idx += 1
    return chunks


__all__ = ["split"]
