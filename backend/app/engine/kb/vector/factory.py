"""Vector model factories — embedding + reranker + vision/OCR clients.

All clients are configured directly via environment variables
(``KB_EMBEDDING_*`` / ``KB_RERANKER_*`` / ``KB_VISION_*`` / ``KB_OCR_*``),
since they are platform-global singletons — they don't need Model table
entries. This keeps deployment simple: set base_url + model + api_key in
``.env`` and vector KBs work, with no UI-side model configuration step.

Embedding is REQUIRED for vector KBs to function. Reranker, vision, and OCR
are all OPTIONAL — ``get_reranker()`` / ``get_vision_client()`` /
``get_ocr_engine()`` return ``None`` when unconfigured, and the pipeline
degrades gracefully.
"""
from __future__ import annotations

import httpx
from langchain_core.embeddings import Embeddings
from langchain_core.language_models.chat_models import BaseChatModel
from loguru import logger

from app.core.config import settings
from app.engine.kb.vector.ocr_engine import RapidOCREngine


class _NotLoaded:
    """Sentinel meaning 'singleton not yet attempted' (distinct from None)."""


NotLoaded = _NotLoaded()


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

    Required for vector KBs. ``base_url`` and ``model`` are mandatory;
    ``api_key`` is optional (local providers like Ollama / LM Studio don't
    require one). Raises ValueError if unconfigured so callers fail loudly
    rather than silently producing broken indexes.
    """
    base_url = settings.KB_EMBEDDING_BASE_URL
    model = settings.KB_EMBEDDING_MODEL
    if not (base_url and model):
        raise ValueError(
            "KB_EMBEDDING_BASE_URL / KB_EMBEDDING_MODEL 未完整配置 — "
            "vector 知识库不可用（请在 .env 中填入 embedding 模型信息）"
        )
    from langchain_openai import OpenAIEmbeddings

    # api_key defaults to a dummy value when unset — required by the OpenAI
    # client lib even for local providers (Ollama ignores it). "not-required"
    # keeps the real key out of logs.
    api_key = settings.KB_EMBEDDING_API_KEY or "not-required"
    # check_embedding_ctx_length=False disables langchain's pre-tokenization
    # (which encodes text to token-id arrays before sending). The OpenAI API
    # accepts token ids, but Ollama's /v1/embeddings compatibility layer only
    # accepts string input — without this, Ollama returns
    # "invalid input type" (400).
    logger.debug("embedding_client_built", model=model, base_url=base_url)
    return OpenAIEmbeddings(
        model=model,
        base_url=base_url,
        api_key=api_key,
        check_embedding_ctx_length=False,
    )


def get_reranker() -> RerankerClient | None:
    """Build the reranker client from ``KB_RERANKER_*`` env vars.

    Optional. Returns None when unconfigured so the retrieval pipeline can
    degrade gracefully (skip rerank). ``api_key`` is optional (local
    providers may not need one).
    """
    base_url = settings.KB_RERANKER_BASE_URL
    model = settings.KB_RERANKER_MODEL
    if not (base_url and model):
        return None
    api_key = settings.KB_RERANKER_API_KEY or "not-required"
    logger.debug("reranker_client_built", model=model, base_url=base_url)
    return RerankerClient(base_url=base_url, model=model, api_key=api_key)


def get_vision_client() -> BaseChatModel | None:
    """Build the vision LLM client from ``KB_VISION_*`` env vars.

    Optional. Used for recognizing text in PDF images / scan pages via an
    OpenAI-compatible multimodal chat API (e.g. GPT-4o, Qwen-VL). Returns
    ``None`` when unconfigured so the parser falls back to OCR or skips.

    The returned client supports multimodal messages — callers construct
    ``HumanMessage(content=[{"type": "text", ...}, {"type": "image_url", ...}])``.
    """
    base_url = settings.KB_VISION_BASE_URL
    model = settings.KB_VISION_MODEL
    if not (base_url and model):
        return None
    from langchain_openai import ChatOpenAI

    api_key = settings.KB_VISION_API_KEY or "not-required"
    logger.debug("vision_client_built", model=model, base_url=base_url)
    return ChatOpenAI(
        model=model,
        base_url=base_url,
        api_key=api_key,
        temperature=0,  # recognition should be deterministic
    )


# ── OCR engine (RapidOCR, lazy-loaded singleton) ──────────────────────

_ocr_engine_singleton: RapidOCREngine | None | _NotLoaded = NotLoaded


def get_ocr_engine() -> RapidOCREngine | None:
    """Build the RapidOCR engine if ``KB_OCR_ENABLED``.

    Optional. Returns ``None`` when disabled or when RapidOCR fails to import
    (e.g. not installed). The engine is a lazy-loaded singleton so the model
    is only loaded on first use, not at app startup.

    Used as a fallback for ``get_vision_client()`` — when no vision model is
    configured, OCR recognizes text in PDF images / scan pages locally.
    """
    global _ocr_engine_singleton
    if _ocr_engine_singleton is not NotLoaded:
        return _ocr_engine_singleton  # may be None (already tried & failed)

    if not settings.KB_OCR_ENABLED:
        _ocr_engine_singleton = None
        return None

    try:
        _ocr_engine_singleton = RapidOCREngine(
            languages=settings.KB_OCR_LANGUAGES,
        )
        logger.info(
            "ocr_engine_loaded",
            languages=settings.KB_OCR_LANGUAGES,
        )
    except Exception as exc:
        logger.warning(
            "ocr_engine_unavailable",
            error=str(exc),
            hint="Install rapidocr-onnxruntime or set KB_OCR_ENABLED=false",
        )
        _ocr_engine_singleton = None
    return _ocr_engine_singleton


def validate_vector_model_config() -> tuple[bool, str]:
    """Startup-time validation of vector model config.

    Returns ``(ok, message)``. ``ok`` is False only when embedding (required)
    is missing; reranker (optional) missing is fine (ok=True).

    Called from lifespan startup — logs a warning instead of raising so the
    app still boots (lets admins configure the model after first deploy).
    """
    if not (settings.KB_EMBEDDING_BASE_URL and settings.KB_EMBEDDING_MODEL):
        return False, (
            "KB_EMBEDDING_BASE_URL / KB_EMBEDDING_MODEL 未完整配置 — "
            "vector 知识库不可用（api_key 对本地 Ollama 等可留空）"
        )
    return True, ""


__all__ = [
    "RerankerClient",
    "get_embedding_client",
    "get_reranker",
    "get_vision_client",
    "get_ocr_engine",
    "validate_vector_model_config",
]
