"""Vector model factories — embedding + reranker clients for vector KBs.

Both factories resolve a Model table record by its ``_id`` (referenced via
``settings.KB_EMBEDDING_MODEL_ID`` / ``settings.KB_RERANKER_MODEL_ID``),
decrypt the stored API key, and build the appropriate client.

Embedding is REQUIRED for vector KBs to function. Reranker is OPTIONAL —
``get_reranker()`` returns ``None`` when unconfigured, and the retrieval
pipeline degrades gracefully (skips the rerank stage).

All clients target OpenAI-compatible endpoints (``/v1/embeddings``) for
embedding and SiliconFlow/Jina-style ``/v1/rerank`` for rerank, which the
majority of Chinese/foreign providers expose.
"""
from __future__ import annotations

from typing import Any

import httpx
from langchain_core.embeddings import Embeddings
from loguru import logger

from app.core.config import settings
from app.models.model import ModelTaskType


class RerankerClient:
    """Cross-encoder rerank client over an OpenAI-compatible-ish ``/v1/rerank`` API.

    Targets the SiliconFlow / Jina / Cohere-compatible rerank schema:
        POST {base_url}/rerank
        body: {"model": ..., "query": ..., "documents": [...], "top_n": ...}
        resp: {"results": [{"index": i, "relevance_score": s}, ...]}

    The base_url stored on the Model record is expected to end with ``/v1``
    (matching how embedding/chat clients are configured). We strip a trailing
    slash and append ``/rerank``.
    """

    def __init__(self, base_url: str, model_id: str, api_key: str) -> None:
        self._url = base_url.rstrip("/") + "/rerank"
        self._model = model_id
        self._api_key = api_key

    async def rerank(
        self,
        query: str,
        documents: list[str],
        top_n: int | None = None,
        timeout: float = 30.0,
    ) -> list[tuple[int, float]]:
        """Re-rank ``documents`` against ``query``.

        Returns a list of ``(original_index, relevance_score)`` sorted
        descending by score. The ``original_index`` points back into the
        input ``documents`` list so callers can map back to source chunks.
        """
        payload: dict[str, Any] = {
            "model": self._model,
            "query": query,
            "documents": documents,
        }
        if top_n is not None:
            payload["top_n"] = top_n

        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }

        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(self._url, headers=headers, json=payload)
            resp.raise_for_status()
            data = resp.json()

        results = data.get("results", [])
        ranked = [
            (int(r["index"]), float(r["relevance_score"]))
            for r in results
            if "index" in r and "relevance_score" in r
        ]
        # Sort descending by score (best first). Defensive — most APIs already
        # return sorted, but we don't rely on it.
        ranked.sort(key=lambda x: x[1], reverse=True)
        return ranked


async def _resolve_model_doc(model_ref: str, expected_task: ModelTaskType) -> dict | None:
    """Resolve a Model table record by _id and validate its task_type.

    Returns the doc with a decrypted ``api_key`` (for internal use only —
    must never be returned through the API layer), or None if not found /
    misconfigured.
    """
    if not model_ref or not model_ref.startswith("model_"):
        return None
    try:
        from app.services.model_service import ModelService

        doc = await ModelService.get_model_config_by_id(model_ref)
    except Exception as exc:
        logger.error("vector_factory_resolve_failed", model_ref=model_ref, error=str(exc))
        return None
    if doc is None:
        return None
    if doc.get("task_type") != expected_task.value:
        logger.warning(
            "vector_factory_task_type_mismatch",
            model_ref=model_ref,
            expected=expected_task.value,
            actual=doc.get("task_type"),
        )
        return None
    return doc


async def get_embedding_client() -> Embeddings:
    """Build the embedding client from ``settings.KB_EMBEDDING_MODEL_ID``.

    Required for vector KBs. Raises ValueError if unconfigured or
    misconfigured so callers fail loudly rather than silently producing
    broken indexes.
    """
    model_ref = settings.KB_EMBEDDING_MODEL_ID
    doc = await _resolve_model_doc(model_ref, ModelTaskType.EMBEDDING)
    if doc is None:
        raise ValueError(
            "KB_EMBEDDING_MODEL_ID 未配置或指向的模型不是 embedding 类型 "
            "(需在 Model 表配置一个 task_type=embedding 的模型)"
        )
    from langchain_openai import OpenAIEmbeddings

    logger.debug("embedding_client_built", model=doc["model_id"])
    return OpenAIEmbeddings(
        model=doc["model_id"],
        base_url=doc["base_url"],
        api_key=doc["api_key"],
    )


async def get_reranker() -> RerankerClient | None:
    """Build the reranker client from ``settings.KB_RERANKER_MODEL_ID``.

    Optional. Returns None when unconfigured so the retrieval pipeline can
    degrade gracefully (skip rerank). Also returns None (with a warning) if
    misconfigured, rather than raising — rerank is a quality optimization,
    not a correctness requirement.
    """
    model_ref = settings.KB_RERANKER_MODEL_ID
    if not model_ref:
        return None
    doc = await _resolve_model_doc(model_ref, ModelTaskType.RERANK)
    if doc is None:
        logger.warning("reranker_unavailable_degrading", model_ref=model_ref)
        return None
    logger.debug("reranker_client_built", model=doc["model_id"])
    return RerankerClient(
        base_url=doc["base_url"],
        model_id=doc["model_id"],
        api_key=doc["api_key"],
    )


def validate_vector_model_config() -> tuple[bool, str]:
    """Startup-time validation of vector model config.

    Returns ``(ok, message)``. ``ok`` is False only when embedding (required)
    is missing; reranker (optional) missing is reported but ok=True.

    Called from lifespan startup — logs a warning instead of raising so the
    app still boots (lets admins configure the model after first deploy).
    """
    emb = settings.KB_EMBEDDING_MODEL_ID
    if not emb or not emb.startswith("model_"):
        return False, (
            "KB_EMBEDDING_MODEL_ID 未配置 — vector 知识库不可用 "
            "(需在 Model 表配置 task_type=embedding 的模型并填入其 _id)"
        )
    rr = settings.KB_RERANKER_MODEL_ID
    if rr and not rr.startswith("model_"):
        # Not fatal — we just won't rerank.
        logger.warning(
            "reranker_config_invalid_ignored",
            value=rr,
            hint="应为 model_ 前缀的 Model _id；当前值将被忽略，检索降级跳过 rerank",
        )
    return True, ""


__all__ = [
    "RerankerClient",
    "get_embedding_client",
    "get_reranker",
    "validate_vector_model_config",
]
