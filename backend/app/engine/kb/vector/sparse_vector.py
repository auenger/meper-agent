"""Client-side BM25-style sparse vector generation.

Self-hosted Qdrant doesn't ship the server-side sparse inference service (that
is a Qdrant Cloud feature), so we generate BM25 sparse vectors on the client
and store them pre-computed — the same vectors are produced for both documents
(at index time) and queries (at retrieval time), keeping the hash→term mapping
consistent.

Scheme:
    text → jieba tokens → term frequencies → BM25-style weights
    each term hashed (stable, via blake2b → int) to a sparse vector index.

Weights use a simplified BM25 term-frequency saturation (``1 + log(tf)``)
without a global IDF (the corpus isn't known at encode time). This still gives
keyword matching a strong signal for exact terms like model numbers / IDs,
which complements dense semantic retrieval.
"""
from __future__ import annotations

import hashlib
from collections import Counter

import jieba  # type: ignore[import-untyped]
from qdrant_client.http import models as qmodels

# Cap the vocabulary dimension (hash modulo). 2^20 (~1M slots) keeps collision
# rate low for natural language; collisions are additionally merged in encode().
_HASH_SPACE = 1 << 20


def _term_index(term: str) -> int:
    """Stable non-random hash of a term into the sparse index space."""
    h = hashlib.blake2b(term.encode("utf-8"), digest_size=8).digest()
    return int.from_bytes(h, "big") % _HASH_SPACE


def _tokenize(text: str) -> list[str]:
    """Tokenize for BM25: jieba for CJK, lowercase, drop whitespace/punct."""
    tokens: list[str] = []
    for raw in jieba.cut(text):
        t = raw.strip().lower()
        if not t:
            continue
        # Keep alphanumerics + CJK; drop pure punctuation tokens.
        if any(c.isalnum() for c in t):
            tokens.append(t)
    return tokens


def encode(text: str) -> qmodels.SparseVector | None:
    """Encode text into a BM25-style SparseVector.

    Returns ``None`` for empty input (caller should skip sparse search).

    Different terms may hash to the same index (collision); their weights are
    summed so the resulting indices list is always unique (Qdrant requires it).
    """
    tokens = _tokenize(text)
    if not tokens:
        return None
    freq = Counter(tokens)
    # Aggregate by hash index (merge collisions).
    by_index: dict[int, float] = {}
    for term, tf in freq.items():
        idx = _term_index(term)
        # BM25-style saturated TF weight (k1=1.2, simplified — no doc-length
        # normalization since corpus stats aren't known at encode time).
        weight = (1 + 1.2) * tf / (1.2 + tf)
        by_index[idx] = by_index.get(idx, 0.0) + weight
    indices = list(by_index.keys())
    values = [float(v) for v in by_index.values()]
    return qmodels.SparseVector(indices=indices, values=values)


__all__ = ["encode"]
