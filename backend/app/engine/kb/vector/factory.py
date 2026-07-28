"""Vector model factories — embedding + reranker clients for vector KBs.

Both clients are configured directly via environment variables
(``KB_EMBEDDING_*`` / ``KB_RERANKER_*``), since embedding and reranker are
platform-global singletons — they don't need Model table entries. This keeps
deployment simple: set base_url + model + api_key in ``.env`` and vector KBs
work, with no UI-side model configuration step.

Embedding is REQUIRED for vector KBs to function. Reranker is OPTIONAL —
``get_reranker()`` returns ``None`` when unconfigured, and the retrieval
pipeline degrades gracefully (skips the rerank stage).
"""
from __future__ import annotations

import httpx
from langchain_core.embeddings import Embeddings
from loguru import logger

from app.core.config import settings


class RerankerClient:
    """Cross-encoder rerank client over an OpenAI-compatible-ish ``/v1/rerank`` API.

    Targets the SiliconFlow / Jina / Cohere-compatible rerank schema:
        POST {base_url}/rerank
        body: {"model": ..., "query": ..., "documents": [...], "top_n": ...}
        resp: {"results": [{"index": i, "relevance_score": s}, ...]}

    The base_url is expected to end with ``/v1`` (matching how embedding
    clients are configured). We strip a trailing slash and append ``/rerank``.
    """

    def __init__(self, base_url: str, model: str, api_key: str) -> None:
        self._url = base_url.rstrip("/") + "/rerank"
        self._model = model
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
        payload: dict = {
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


def get_embedding_client() -> Embeddings:
    """Build the embedding client from ``KB_EMBEDDING_*`` env vars.

    Required for vector KBs. Raises ValueError if unconfigured so callers
    fail loudly rather than silently producing broken indexes.
    """
    base_url = settings.KB_EMBEDDING_BASE_URL
    model = settings.KB_EMBEDDING_MODEL
    api_key = settings.KB_EMBEDDING_API_KEY
    if not (base_url and model and api_key):
        raise ValueError(
            "KB_EMBEDDING_BASE_URL / KB_EMBEDDING_MODEL / KB_EMBEDDING_API_KEY "
            "未完整配置 — vector 知识库不可用（请在 .env 中填入 embedding 模型信息）"
        )
    from langchain_openai import OpenAIEmbeddings

    logger.debug("embedding_client_built", model=model)
    return OpenAIEmbeddings(model=model, base_url=base_url, api_key=api_key)


def get_reranker() -> RerankerClient | None:
    """Build the reranker client from ``KB_RERANKER_*`` env vars.

    Optional. Returns None when unconfigured so the retrieval pipeline can
    degrade gracefully (skip rerank).
    """
    base_url = settings.KB_RERANKER_BASE_URL
    model = settings.KB_RERANKER_MODEL
    api_key = settings.KB_RERANKER_API_KEY
    if not (base_url and model and api_key):
        return None
    logger.debug("reranker_client_built", model=model)
    return RerankerClient(base_url=base_url, model=model, api_key=api_key)


def validate_vector_model_config() -> tuple[bool, str]:
    """Startup-time validation of vector model config.

    Returns ``(ok, message)``. ``ok`` is False only when embedding (required)
    is missing; reranker (optional) missing is fine (ok=True).

    Called from lifespan startup — logs a warning instead of raising so the
    app still boots (lets admins configure the model after first deploy).
    """
    if not (
        settings.KB_EMBEDDING_BASE_URL
        and settings.KB_EMBEDDING_MODEL
        and settings.KB_EMBEDDING_API_KEY
    ):
        return False, (
            "KB_EMBEDDING_BASE_URL / KB_EMBEDDING_MODEL / KB_EMBEDDING_API_KEY "
            "未完整配置 — vector 知识库不可用（请在 .env 中填入 embedding 模型信息）"
        )
    return True, ""


__all__ = [
    "RerankerClient",
    "get_embedding_client",
    "get_reranker",
    "validate_vector_model_config",
]
