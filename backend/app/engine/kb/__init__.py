"""Knowledge Base engine module — tree + vector KBs coexist.

Layout:
    tree/    — Markdown file-tree KB (agent explores via kb_glob/grep/read)
    vector/  — RAG KB (Qdrant dense+sparse hybrid retrieval + rerank)

High-level symbols are re-exported here for convenience:
    ``from app.engine.kb import retrieve, KbManager, KbSearchManager, ...``
"""
from app.engine.kb.tree.fs import get_kb_base_path
from app.engine.kb.tree.manager import KbManager
from app.engine.kb.vector.factory import (
    RerankerClient,
    get_embedding_client,
    get_reranker,
    validate_vector_model_config,
)
from app.engine.kb.vector.retriever import retrieve
from app.engine.kb.vector.search_manager import KbSearchManager

__all__ = [
    "KbManager",
    "KbSearchManager",
    "RerankerClient",
    "get_embedding_client",
    "get_kb_base_path",
    "get_reranker",
    "retrieve",
    "validate_vector_model_config",
]
