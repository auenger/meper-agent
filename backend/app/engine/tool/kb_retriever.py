"""Vector KB retrieval orchestration — hybrid recall → rerank → filter.

Single entry point :func:`retrieve` used by:
    - the Agent ``kb_search`` tool (Phase 4)
    - the Workflow ``kb_search`` node (Phase 5)
    - the retrieval-test API endpoint (Phase 3b)

Three-stage pipeline:

1. **Hybrid recall** (``kb_vector_store.hybrid_search``): one Qdrant
   ``query_points`` call fusing dense (semantic) + sparse (BM25) results
   via RRF, filtered to ``kb_id``. Returns ``recall_k`` candidates.
2. **Rerank** (optional): when ``settings.KB_RERANKER_MODEL_ID`` is set,
   a cross-encoder re-scores the recalled candidates. When unset, this
   stage is skipped and the RRF-fused scores are used directly (graceful
   degradation — rerank is a quality optimization, not a correctness need).
3. **Filter + truncate**: drop results below ``score_threshold`` and
   return the top ``top_k``.
"""
from __future__ import annotations

from loguru import logger

from app.core.config import settings


async def retrieve(
    kb_id: str,
    query: str,
    top_k: int | None = None,
    recall_k: int | None = None,
    score_threshold: float | None = None,
) -> list[dict]:
    """Retrieve relevant chunks from one vector KB.

    Args:
        kb_id: target knowledge base id (payload-filtered).
        query: raw query text (used for both dense embedding and BM25).
        top_k: final number of results (default settings.KB_VECTOR_TOP_K).
        recall_k: candidates fetched before rerank (default KB_VECTOR_RECALL_K).
        score_threshold: minimum score to keep (default KB_VECTOR_SCORE_THRESHOLD).

    Returns:
        List of ``{text, score, doc_id, source_file, page}`` sorted by
        score descending. Empty list if KB has no indexed content or all
        candidates fall below threshold.
    """
    top_k = top_k or settings.KB_VECTOR_TOP_K
    recall_k = recall_k or settings.KB_VECTOR_RECALL_K
    score_threshold = (
        score_threshold if score_threshold is not None else settings.KB_VECTOR_SCORE_THRESHOLD
    )

    if not query.strip():
        return []

    # 1. Embed the query (dense) — sparse uses the raw text server-side.
    from app.engine.vector_factory import get_embedding_client

    embeddings = get_embedding_client()
    query_vector = await embeddings.aembed_query(query)

    # 2. Hybrid recall (dense + sparse, RRF-fused) via Qdrant.
    from app.engine.tool import kb_vector_store

    recalled = await kb_vector_store.hybrid_search(kb_id, query, query_vector, k=recall_k)
    if not recalled:
        return []

    # 3. Optional rerank.
    from app.engine.vector_factory import get_reranker

    reranker = get_reranker()
    if reranker is not None:
        try:
            recalled = await _rerank(reranker, query, recalled)
        except Exception as exc:
            # Rerank failure must not break retrieval — fall back to RRF order.
            logger.warning("kb_rerank_failed_degraded", kb_id=kb_id, error=str(exc))

    # 4. Threshold filter + truncate.
    out = [r for r in recalled if r.get("score", 0) >= score_threshold][:top_k]
    logger.info(
        "kb_retrieval_done",
        kb_id=kb_id,
        recalled=len(recalled),
        returned=len(out),
        reranked=reranker is not None,
    )
    return out


async def _rerank(
    reranker, query: str, candidates: list[dict]
) -> list[dict]:
    """Re-score candidates with the cross-encoder reranker.

    The rerank API returns relevance scores on its own scale (often 0-1),
    which replace the RRF scores so the subsequent threshold/truncation
    reflects cross-encoder relevance rather than rank fusion.
    """
    documents = [c.get("text", "") for c in candidates]
    if not documents:
        return candidates
    ranked = await reranker.rerank(query, documents)
    # Map original index → new score, preserving candidate payload.
    score_by_idx = dict(ranked)
    for i, c in enumerate(candidates):
        if i in score_by_idx:
            c["score"] = score_by_idx[i]
    # Re-sort by the new (rerank) score.
    candidates.sort(key=lambda c: c.get("score", 0), reverse=True)
    return candidates


__all__ = ["retrieve"]
