"""Chunker — split cleaned text blocks into token-sized chunks.

Uses ``RecursiveCharacterTextSplitter.from_tiktoken_encoder`` so chunk size
is measured in tokens (matching embedding cost), with CJK-friendly
separators that avoid cutting mid-sentence in Chinese text.

Each produced chunk carries source metadata (page, source_file) so retrieval
results can cite their origin. Chunk size/overlap are platform-level fixed
defaults (``settings.KB_VECTOR_CHUNK_SIZE`` / ``KB_VECTOR_CHUNK_OVERLAP``) —
KB-level configuration is deliberately deferred to keep the single shared
Qdrant collection dimensionally consistent.
"""
from __future__ import annotations

from langchain_text_splitters import RecursiveCharacterTextSplitter

from app.core.config import settings
from app.engine.tool.kb_parser.base import ParseResult

# CJK-friendly separator priority: paragraphs → lines → Chinese sentence
# enders → comma → space → (last resort) any character.
_SEPARATORS = ["\n\n", "\n", "。", "！", "？", "；", "，", " ", ""]


def _make_splitter() -> RecursiveCharacterTextSplitter:
    return RecursiveCharacterTextSplitter.from_tiktoken_encoder(
        model_name="text-embedding-3-small",
        chunk_size=settings.KB_VECTOR_CHUNK_SIZE,
        chunk_overlap=settings.KB_VECTOR_CHUNK_OVERLAP,
        separators=_SEPARATORS,
    )


def split(result: ParseResult, source_file: str = "") -> list[dict]:
    """Split a ParseResult into a list of chunk dicts.

    Args:
        result: cleaned parse result (one or more page-scoped TextBlocks).
        source_file: original filename, attached to every chunk for citation.

    Returns:
        List of ``{text, source_file, page, chunk_index}``.
    """
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
                }
            )
            idx += 1
    return chunks


__all__ = ["split"]
