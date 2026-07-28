"""Vector KB document indexing — async Celery task.

Pipeline per document:
    load original (FileRef) → parse → clean → chunk → embed (dense)
    → upsert to Qdrant (dense+sparse) → mark completed.

This is the only place that ties the parser/chunker/embedding/qdrant
layers together. It runs in the Celery worker process, so all async
work is wrapped via ``workers.loop.run_async`` to share the worker's
event loop (required for motor/qdrant clients — see workers/loop.py).

Idempotent: point ids are deterministic (``kb:doc:chunk_index``), so
re-indexing a document overwrites rather than duplicates.
"""
from typing import Any

from loguru import logger

from app.workers.celery_app import celery_app
from app.workers.loop import run_async


@celery_app.task(name="app.workers.tasks.kb_indexing.index_kb_document")
def index_kb_document(doc_id: str) -> dict[str, Any]:
    """Index a single KnowledgeDocument (parse → chunk → embed → store).

    Celery task entry point. Delegates to the async implementation.
    """
    return run_async(_index_async(doc_id))


async def _index_async(doc_id: str) -> dict[str, Any]:
    from app.engine.tool import kb_vector_store
    from app.engine.tool.kb_parser import clean, parse, split
    from app.engine.vector_factory import get_embedding_client
    from app.models.knowledge_document import EMBEDDING, PARSING
    from app.services.file_service import FileService
    from app.services.file_storage import LocalFileStorage
    from app.services.knowledge_document_service import (
        KnowledgeDocumentService,
    )

    doc = await KnowledgeDocumentService.get(doc_id)
    if doc is None:
        logger.error("kb_index_doc_not_found", doc_id=doc_id)
        return {"status": "not_found", "doc_id": doc_id}

    kb_id = doc.knowledge_base_id
    try:
        # 1. Load original file bytes via FileRef.
        file_service = FileService(storage=LocalFileStorage())
        loaded = await file_service.load_content(doc.file_ref_id)
        if loaded is None:
            raise RuntimeError(f"原始文件不存在: {doc.file_ref_id}")
        _fref, raw = loaded

        # 2. Parse → clean → chunk.
        await KnowledgeDocumentService.update_status(doc_id, PARSING)
        parse_result = parse(raw, doc.file_type)
        cleaned = clean(parse_result)
        chunks = split(cleaned, source_file=doc.name)
        if not chunks:
            raise RuntimeError("文档解析后无有效内容（可能为空文件或纯图片）")

        # 3. Ensure Qdrant collection exists (sized to embedding dim).
        dense_dim = await kb_vector_store.get_dense_dim()
        await kb_vector_store.ensure_collection(dense_dim)

        # 4. Embed chunks in batches (dense vectors).
        await KnowledgeDocumentService.update_status(doc_id, EMBEDDING, progress=10)
        embeddings = get_embedding_client()
        from app.core.config import settings

        batch = settings.KB_VECTOR_EMBED_BATCH
        dense_vectors: list[list[float]] = []
        total = len(chunks)
        for start in range(0, total, batch):
            slice_ = chunks[start : start + batch]
            texts = [c["text"] for c in slice_]
            dense_vectors.extend(await embeddings.aembed_documents(texts))
            progress = 10 + int(80 * (start + len(slice_)) / total)
            await KnowledgeDocumentService.update_status(doc_id, EMBEDDING, progress=progress)

        # 5. Upsert to Qdrant (sparse/BM25 built server-side from text).
        await kb_vector_store.add_chunks(kb_id, doc_id, chunks, dense_vectors, batch)

        # 6. Done.
        await KnowledgeDocumentService.mark_completed(doc_id, chunk_count=total)
        logger.info(
            "kb_index_completed", doc_id=doc_id, kb_id=kb_id, chunk_count=total
        )
        return {"status": "completed", "doc_id": doc_id, "chunk_count": total}

    except Exception as exc:
        logger.exception("kb_index_failed", doc_id=doc_id, kb_id=kb_id)
        await KnowledgeDocumentService.mark_failed(doc_id, str(exc))
        return {"status": "failed", "doc_id": doc_id, "error": str(exc)}
