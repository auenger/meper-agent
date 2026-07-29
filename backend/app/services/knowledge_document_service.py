"""KnowledgeDocument business logic — CRUD for vector KB document metadata.

Each KnowledgeDocument tracks the async indexing lifecycle of one uploaded
file in a vector KB. The chunk text + vectors live in Qdrant; this service
only manages the document-level metadata record.
"""
from __future__ import annotations

from loguru import logger

from app.db.mongodb import get_database
from app.models.base import utc_now
from app.models.knowledge_document import (
    COMPLETED,
    FAILED,
    PENDING,
    KnowledgeDocument,
)


class KnowledgeDocumentService:
    """Service layer for KnowledgeDocument operations."""

    COLLECTION = "knowledge_documents"

    @staticmethod
    def _collection():
        return get_database()[KnowledgeDocumentService.COLLECTION]

    @staticmethod
    def _serialize(doc: dict) -> KnowledgeDocument:
        return KnowledgeDocument.model_validate(doc)

    @staticmethod
    async def create(
        knowledge_base_id: str,
        file_ref_id: str,
        name: str,
        file_type: str,
        file_size: int,
        uploaded_by: str = "",
    ) -> KnowledgeDocument:
        """Create a pending document record (pre-indexing)."""
        from app.models.base import generate_id

        now = utc_now().isoformat()
        doc = {
            "_id": generate_id("kbdoc"),
            "knowledge_base_id": knowledge_base_id,
            "file_ref_id": file_ref_id,
            "name": name,
            "file_type": file_type,
            "file_size": file_size,
            "parse_status": PENDING,
            "parse_progress": 0,
            "parse_error": "",
            "chunk_count": 0,
            "uploaded_by": uploaded_by,
            "created_at": now,
            "updated_at": now,
        }
        await KnowledgeDocumentService._collection().insert_one(doc)
        logger.info(
            "kb_document_created",
            kb_id=knowledge_base_id,
            doc_id=doc["_id"],
            name=name,
        )
        return KnowledgeDocumentService._serialize(doc)

    @staticmethod
    async def get(doc_id: str) -> KnowledgeDocument | None:
        doc = await KnowledgeDocumentService._collection().find_one({"_id": doc_id})
        return KnowledgeDocumentService._serialize(doc) if doc else None

    @staticmethod
    async def list_by_kb(
        knowledge_base_id: str, page: int = 1, page_size: int = 50
    ) -> tuple[list[KnowledgeDocument], int]:
        """List documents in a KB with pagination."""
        col = KnowledgeDocumentService._collection()
        flt = {"knowledge_base_id": knowledge_base_id}
        total = await col.count_documents(flt)
        cursor = (
            col.find(flt)
            .sort("created_at", -1)
            .skip((page - 1) * page_size)
            .limit(page_size)
        )
        docs = await cursor.to_list(length=page_size)
        return [KnowledgeDocumentService._serialize(d) for d in docs], total

    @staticmethod
    async def update_status(
        doc_id: str,
        status: str,
        *,
        progress: int | None = None,
        chunk_count: int | None = None,
        error: str | None = None,
    ) -> None:
        """Update indexing status (and optional progress/chunk_count/error)."""
        set_fields: dict = {
            "parse_status": status,
            "updated_at": utc_now().isoformat(),
        }
        if progress is not None:
            set_fields["parse_progress"] = max(0, min(100, progress))
        if chunk_count is not None:
            set_fields["chunk_count"] = chunk_count
        if error is not None:
            set_fields["parse_error"] = error
        await KnowledgeDocumentService._collection().update_one(
            {"_id": doc_id}, {"$set": set_fields}
        )

    @staticmethod
    async def mark_failed(doc_id: str, error: str) -> None:
        await KnowledgeDocumentService.update_status(
            doc_id, FAILED, progress=0, error=error
        )

    @staticmethod
    async def mark_completed(doc_id: str, chunk_count: int) -> None:
        await KnowledgeDocumentService.update_status(
            doc_id, COMPLETED, progress=100, chunk_count=chunk_count, error=""
        )

    @staticmethod
    async def replace_file(
        doc_id: str,
        file_ref_id: str,
        name: str,
        file_type: str,
        file_size: int,
    ) -> None:
        """Swap the underlying file of a document and reset its indexing state.

        Used when a user replaces a document's source file: the old Qdrant
        points are deleted by the caller, then this resets the record to
        pending so the indexing task re-processes the new file.
        """
        await KnowledgeDocumentService._collection().update_one(
            {"_id": doc_id},
            {
                "$set": {
                    "file_ref_id": file_ref_id,
                    "name": name,
                    "file_type": file_type,
                    "file_size": file_size,
                    "parse_status": PENDING,
                    "parse_progress": 0,
                    "parse_error": "",
                    "chunk_count": 0,
                    "updated_at": utc_now().isoformat(),
                }
            },
        )

    @staticmethod
    async def delete(doc_id: str) -> bool:
        """Delete a document record (does NOT touch Qdrant — caller handles that)."""
        result = await KnowledgeDocumentService._collection().delete_one({"_id": doc_id})
        return result.deleted_count > 0

    @staticmethod
    async def delete_by_kb(knowledge_base_id: str) -> int:
        """Delete all document records for a KB (KB deletion)."""
        col = KnowledgeDocumentService._collection()
        result = await col.delete_many({"knowledge_base_id": knowledge_base_id})
        return result.deleted_count

    @staticmethod
    async def count_by_kb(knowledge_base_id: str) -> int:
        col = KnowledgeDocumentService._collection()
        return await col.count_documents({"knowledge_base_id": knowledge_base_id})


__all__ = ["KnowledgeDocumentService"]
