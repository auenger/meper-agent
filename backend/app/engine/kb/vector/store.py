"""Qdrant vector store — dense + sparse (BM25) hybrid storage for vector KBs.

Single shared collection (``settings.KB_QDRANT_COLLECTION``) holds every
vector KB's chunks, isolated by a ``kb_id`` payload field. Each point stores
TWO named vectors:

- ``dense``  — semantic embedding (dims from the embedding model, cosine).
- ``sparse`` — BM25 keyword vector, generated **client-side** by
  :mod:`sparse_vector` (jieba tokenization + BM25-style weighting). Self-hosted
  Qdrant lacks the server-side sparse-inference service (a Cloud feature), so
  we pre-compute sparse vectors and store them alongside the dense ones.

Hybrid retrieval uses ``query_points`` with two prefetches (dense + sparse)
fused by RRF, all in one round-trip.

The ``QdrantClient`` is created lazily and cached per process. Collection
creation is idempotent — ``ensure_collection()`` creates it with the right
vector config on first use.
"""
from __future__ import annotations

import uuid

from loguru import logger
from qdrant_client import AsyncQdrantClient
from qdrant_client.http import models as qmodels

from app.core.config import settings
from app.engine.kb.vector import sparse_vector

# ── client singleton ────────────────────────────────────────────────────

_client: AsyncQdrantClient | None = None


def get_qdrant_client() -> AsyncQdrantClient:
    """Return the process-wide async Qdrant client (lazy singleton)."""
    global _client
    if _client is None:
        _client = AsyncQdrantClient(
            url=settings.QDRANT_URL,
            api_key=settings.QDRANT_API_KEY or None,
            prefer_grpc=False,  # REST is simpler for mixed sparse/dense ops
        )
    return _client


# ── collection setup ────────────────────────────────────────────────────


async def ensure_collection(dense_dim: int) -> None:
    """Create the shared collection with dense + sparse vector fields.

    Idempotent: if the collection exists, this is a no-op. The dense vector
    field is sized to ``dense_dim`` (the embedding model's output dimension).
    The sparse field uses Qdrant's built-in BM25 modifier, so no dim is
    specified — Qdrant infers sparse vectors from tokenized text.

    Args:
        dense_dim: dimensionality of the dense embedding model output.
    """
    client = get_qdrant_client()
    name = settings.KB_QDRANT_COLLECTION
    if await client.collection_exists(name):
        return

    await client.create_collection(
        collection_name=name,
        vectors_config={
            "dense": qmodels.VectorParams(
                size=dense_dim,
                distance=qmodels.Distance.COSINE,
            )
        },
        sparse_vectors_config={
            "sparse": qmodels.SparseVectorParams(
                index=qmodels.SparseIndexParams(),
            )
        },
    )
    # Payload index on kb_id for fast filtered retrieval.
    await client.create_payload_index(
        collection_name=name,
        field_name="kb_id",
        field_schema=qmodels.PayloadSchemaType.KEYWORD,
    )
    logger.info("qdrant_collection_created", collection=name, dense_dim=dense_dim)


async def get_dense_dim() -> int:
    """Probe the embedding model to discover its dense output dimension.

    Embeds a throwaway token and returns the resulting vector length. Used
    once at collection-creation time to size the dense vector field.
    """
    from app.engine.kb.vector.factory import get_embedding_client

    embeddings = get_embedding_client()
    vec = await embeddings.aembed_query("dimension probe")
    return len(vec)


# ── chunk ingestion ─────────────────────────────────────────────────────


async def add_chunks(
    kb_id: str,
    doc_id: str,
    chunks: list[dict],
    dense_vectors: list[list[float]],
    batch_size: int | None = None,
) -> int:
    """Upsert chunk points (dense + sparse) into the shared collection.

    Args:
        kb_id: owning knowledge base id (used as payload filter).
        doc_id: owning document id (used for deletion by doc).
        chunks: list of ``{text, source_file, page, chunk_index}``.
        dense_vectors: parallel list of dense embedding vectors (one per chunk).
        batch_size: upsert batch size (defaults to settings.KB_VECTOR_EMBED_BATCH).

    Returns:
        Number of points upserted.
    """
    if not chunks:
        return 0
    assert len(chunks) == len(dense_vectors), "chunks and dense_vectors length mismatch"

    client = get_qdrant_client()
    batch_size = batch_size or settings.KB_VECTOR_EMBED_BATCH
    total = 0

    for start in range(0, len(chunks), batch_size):
        batch = chunks[start : start + batch_size]
        points: list[qmodels.PointStruct] = []
        for i, ch in enumerate(batch):
            global_idx = start + i
            # Client-side BM25 sparse vector (self-hosted Qdrant has no
            # server-side inference service, so we pre-compute it).
            sparse = sparse_vector.encode(ch["text"])
            vector_field: dict = {"dense": dense_vectors[start + i]}
            if sparse is not None:
                vector_field["sparse"] = sparse
            points.append(
                qmodels.PointStruct(
                    id=_point_id(kb_id, doc_id, global_idx),
                    vector=vector_field,
                    payload={
                        "kb_id": kb_id,
                        "doc_id": doc_id,
                        "chunk_index": global_idx,
                        "text": ch["text"],
                        "source_file": ch.get("source_file", ""),
                        "page": ch.get("page"),
                    },
                )
            )
        await client.upsert(collection_name=settings.KB_QDRANT_COLLECTION, points=points)
        total += len(points)

    logger.info("qdrant_chunks_added", kb_id=kb_id, doc_id=doc_id, count=total)
    return total


# ── hybrid retrieval ────────────────────────────────────────────────────


async def hybrid_search(
    kb_id: str,
    query: str,
    dense_vector: list[float],
    k: int = 20,
) -> list[dict]:
    """Hybrid dense+sparse retrieval with RRF fusion (single query_points call).

    Args:
        kb_id: restricts the search to this KB via payload filter.
        query: the raw query text (used as sparse/BM25 input).
        dense_vector: pre-computed dense embedding of ``query``.
        k: number of candidates to return (before rerank).

    Returns:
        List of ``{text, score, doc_id, source_file, page}`` sorted by score.
    """
    client = get_qdrant_client()
    flt = qmodels.Filter(
        must=[qmodels.FieldCondition(key="kb_id", match=qmodels.MatchValue(value=kb_id))]
    )

    # Client-side BM25 sparse vector for the query (same encoder as indexing).
    sparse_query = sparse_vector.encode(query)

    prefetch: list[qmodels.Prefetch] = [
        # Dense (semantic) recall.
        qmodels.Prefetch(query=dense_vector, using="dense", limit=k, filter=flt),
    ]
    if sparse_query is not None:
        # Sparse (BM25 keyword) recall — fused with dense via RRF.
        prefetch.append(
            qmodels.Prefetch(query=sparse_query, using="sparse", limit=k, filter=flt)
        )

    result = await client.query_points(
        collection_name=settings.KB_QDRANT_COLLECTION,
        prefetch=prefetch,
        # RRF fusion of the recall sets (1 or 2 depending on sparse availability).
        query=qmodels.FusionQuery(fusion=qmodels.Fusion.RRF),
        limit=k,
        with_payload=True,
    )

    points = result.points if hasattr(result, "points") else []
    out: list[dict] = []
    for p in points:
        pl = p.payload or {}
        out.append(
            {
                "text": pl.get("text", ""),
                "score": float(p.score),
                "doc_id": pl.get("doc_id", ""),
                "source_file": pl.get("source_file", ""),
                "page": pl.get("page"),
            }
        )
    return out


# ── deletion ────────────────────────────────────────────────────────────


async def delete_by_doc(doc_id: str) -> None:
    """Delete all points belonging to a document."""
    client = get_qdrant_client()
    flt = qmodels.Filter(
        must=[qmodels.FieldCondition(key="doc_id", match=qmodels.MatchValue(value=doc_id))]
    )
    await client.delete(collection_name=settings.KB_QDRANT_COLLECTION, points_selector=flt)
    logger.info("qdrant_doc_deleted", doc_id=doc_id)


async def delete_by_kb(kb_id: str) -> None:
    """Delete all points belonging to a knowledge base."""
    client = get_qdrant_client()
    flt = qmodels.Filter(
        must=[qmodels.FieldCondition(key="kb_id", match=qmodels.MatchValue(value=kb_id))]
    )
    await client.delete(collection_name=settings.KB_QDRANT_COLLECTION, points_selector=flt)
    logger.info("qdrant_kb_deleted", kb_id=kb_id)


async def count_by_kb(kb_id: str) -> int:
    """Count chunks (points) for a knowledge base."""
    client = get_qdrant_client()
    flt = qmodels.Filter(
        must=[qmodels.FieldCondition(key="kb_id", match=qmodels.MatchValue(value=kb_id))]
    )
    resp = await client.count(
        collection_name=settings.KB_QDRANT_COLLECTION, count_filter=flt, exact=True
    )
    return resp.count


# ── helpers ─────────────────────────────────────────────────────────────


# Fixed namespace for deterministic point-id generation (uuid5).
_POINT_NS = uuid.uuid5(uuid.NAMESPACE_DNS, "agent-flow.kb.chunk")


def _point_id(kb_id: str, doc_id: str, chunk_index: int) -> str:
    """Deterministic UUID for a chunk point.

    Qdrant point ids must be unsigned ints or UUIDs (string ids like
    "kb:doc:0" are rejected by the server). uuid5 gives a stable UUID per
    (kb, doc, chunk) so re-indexing overwrites the same point (idempotent).
    """
    name = f"{kb_id}:{doc_id}:{chunk_index}"
    return str(uuid.uuid5(_POINT_NS, name))


__all__ = [
    "get_qdrant_client",
    "ensure_collection",
    "get_dense_dim",
    "add_chunks",
    "hybrid_search",
    "delete_by_doc",
    "delete_by_kb",
    "count_by_kb",
]
