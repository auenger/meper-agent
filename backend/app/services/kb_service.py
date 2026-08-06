"""KnowledgeBase business logic — CRUD + Markdown file management.

Tree-style KB: each KB is a directory of ``.md`` files on disk
(:mod:`app.engine.kb.tree.fs`); MongoDB stores only metadata. Agents
bind KBs via ``Agent.knowledge_base_ids``.
"""
from __future__ import annotations

import re

from loguru import logger

from app.core.config import settings
from app.core.errors import ConflictError, NotFoundError, ValidationError
from app.db.mongodb import get_database
from app.engine.kb.tree import fs as kb_fs
from app.models.base import generate_id, utc_now
from app.services.tool_service import ToolService


class KnowledgeBaseService:
    """Service layer for KnowledgeBase operations."""

    COLLECTION = "knowledge_bases"

    @staticmethod
    def _collection():
        return get_database()[KnowledgeBaseService.COLLECTION]

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    @staticmethod
    async def create_kb(
        name: str,
        description: str,
        owner_user_id: str = "",
        type: str = "tree",
    ) -> dict:
        """Create a KB record.

        - tree: also creates the on-disk ``.md`` directory.
        - vector: records the configured embedding model name; no FS directory.
        """
        now = utc_now().isoformat()
        kb_type = type if type in ("tree", "vector") else "tree"
        doc = {
            "_id": generate_id("kb"),
            "name": name,
            "description": description,
            "type": kb_type,
            "embedding_model_id": (
                settings.KB_EMBEDDING_MODEL if kb_type == "vector" else ""
            ),
            "owner_user_id": owner_user_id,
            "status": "active",
            "file_count": 0,
            "total_size": 0,
            "created_at": now,
            "updated_at": now,
        }
        await KnowledgeBaseService._collection().insert_one(doc)
        if kb_type == "tree":
            kb_fs.ensure_kb_dir(doc["_id"])
        logger.info("kb_created", kb_id=doc["_id"], name=name, type=kb_type)
        return doc

    @staticmethod
    async def get_kb(kb_id: str) -> dict | None:
        return await KnowledgeBaseService._collection().find_one({"_id": kb_id})

    @staticmethod
    async def list_kbs(
        page: int = 1,
        page_size: int = 20,
        name: str | None = None,
        status: str | None = None,
    ) -> tuple[list[dict], int]:
        col = KnowledgeBaseService._collection()
        q: dict = {}
        if name:
            q["name"] = {"$regex": re.escape(name), "$options": "i"}
        if status:
            q["status"] = status
        total = await col.count_documents(q)
        cursor = (
            col.find(q)
            .sort("updated_at", -1)
            .skip((page - 1) * page_size)
            .limit(page_size)
        )
        items = await cursor.to_list(length=page_size)
        return items, total

    @staticmethod
    async def update_kb(
        kb_id: str,
        name: str | None = None,
        description: str | None = None,
    ) -> dict | None:
        col = KnowledgeBaseService._collection()
        if await col.find_one({"_id": kb_id}) is None:
            return None
        set_fields: dict = {"updated_at": utc_now().isoformat()}
        if name is not None:
            set_fields["name"] = name
        if description is not None:
            set_fields["description"] = description
        await col.update_one({"_id": kb_id}, {"$set": set_fields})
        return await KnowledgeBaseService.get_kb(kb_id)

    @staticmethod
    async def delete_kb(kb_id: str) -> bool:
        """Delete a KB. Refuses if any Agent references it.

        Raises ConflictError if referenced; returns True if deleted,
        False if not found.
        """
        col = KnowledgeBaseService._collection()
        existing = await col.find_one({"_id": kb_id})
        if existing is None:
            return False

        agents_col = get_database()["agents"]
        cursor = agents_col.find({"knowledge_base_ids": kb_id}, {"name": 1})
        referencing = await cursor.to_list(length=100)
        if referencing:
            names = [a.get("name", a.get("_id", "")) for a in referencing]
            raise ConflictError(
                code="KB_IN_USE",
                message=(
                    f"知识库 '{existing.get('name')}' 正在被以下 Agent 引用，"
                    f"无法删除：{', '.join(names)}"
                ),
                details={"agent_names": names},
            )

        result = await col.delete_one({"_id": kb_id})
        if result.deleted_count > 0:
            kb_type = existing.get("type", "tree")
            if kb_type == "vector":
                # Vector KB: purge Qdrant points + document metadata records.
                from app.engine.kb.vector import store as kb_vector_store
                from app.services.knowledge_document_service import (
                    KnowledgeDocumentService,
                )

                try:
                    await kb_vector_store.delete_by_kb(kb_id)
                except Exception as exc:  # Qdrant unreachable — still delete the KB
                    logger.warning("kb_qdrant_purge_failed", kb_id=kb_id, error=str(exc))
                await KnowledgeDocumentService.delete_by_kb(kb_id)
            else:
                kb_fs.delete_kb_dir(kb_id)
            logger.info("kb_deleted", kb_id=kb_id, type=kb_type)
            return True
        return False

    # ------------------------------------------------------------------
    # File operations (scan / read / write / delete on FS)
    # ------------------------------------------------------------------

    @staticmethod
    async def get_kb_files(kb_id: str) -> list[dict] | None:
        if await KnowledgeBaseService._collection().find_one({"_id": kb_id}) is None:
            return None
        files = kb_fs.list_kb_files(kb_id)
        return ToolService._build_file_tree(files)

    @staticmethod
    async def get_kb_file_content(kb_id: str, rel_path: str) -> dict | None:
        if await KnowledgeBaseService._collection().find_one({"_id": kb_id}) is None:
            return None
        content = kb_fs.read_kb_file(kb_id, rel_path)
        if content is None:
            return None
        return {
            "path": rel_path,
            "content": content,
            "size": len(content.encode("utf-8")),
        }

    @staticmethod
    async def update_kb_file(
        kb_id: str, rel_path: str, new_content: str
    ) -> dict | None:
        col = KnowledgeBaseService._collection()
        if await col.find_one({"_id": kb_id}) is None:
            return None
        # Edit only — file must already exist (creation goes through upload).
        if kb_fs.read_kb_file(kb_id, rel_path) is None:
            return None
        try:
            kb_fs.write_kb_file(kb_id, rel_path, new_content)
        except ValueError as exc:
            raise ValidationError(code="KB_INVALID_PATH", message=str(exc)) from exc
        await col.update_one({"_id": kb_id}, {"$set": {"updated_at": utc_now().isoformat()}})
        await KnowledgeBaseService.recompute_stats(kb_id)
        return {
            "path": rel_path,
            "content": new_content,
            "size": len(new_content.encode("utf-8")),
        }

    @staticmethod
    async def delete_kb_file(kb_id: str, rel_path: str) -> bool:
        col = KnowledgeBaseService._collection()
        if await col.find_one({"_id": kb_id}) is None:
            return False
        ok = kb_fs.delete_kb_file(kb_id, rel_path)
        if ok:
            await col.update_one({"_id": kb_id}, {"$set": {"updated_at": utc_now().isoformat()}})
            await KnowledgeBaseService.recompute_stats(kb_id)
        return ok

    @staticmethod
    async def upload_files(
        kb_id: str,
        files: list[tuple[str, bytes]],
        uploaded_by: str = "",
        chunk_strategy: str = "recursive",
    ) -> dict:
        """Upload a batch of files into a KB.

        Behavior depends on KB type:
        - tree: writes ``.md`` files to the on-disk directory (legacy logic).
        - vector: stores the original file via FileRef, creates a
          KnowledgeDocument (pending), and dispatches a Celery indexing task.

        ``chunk_strategy`` ("recursive" | "structure") is recorded on each
        vector document and consulted by the indexer (ignored for tree KBs).

        Returns ``{"created": [...], "errors": [...], "document_ids": [...]}``.
        For tree KB, ``document_ids`` is empty and ``created`` holds rel paths;
        for vector KB, ``created`` holds filenames and ``document_ids`` the
        created KnowledgeDocument ids.
        """
        col = KnowledgeBaseService._collection()
        kb_doc = await col.find_one({"_id": kb_id})
        if kb_doc is None:
            raise NotFoundError(
                code="KB_NOT_FOUND", message=f"知识库 {kb_id} 不存在"
            )

        kb_type = kb_doc.get("type", "tree")
        if kb_type == "vector":
            return await KnowledgeBaseService._upload_vector(
                kb_id, kb_doc, files, uploaded_by, chunk_strategy=chunk_strategy
            )
        return await KnowledgeBaseService._upload_tree(kb_id, files)

    @staticmethod
    async def _upload_tree(kb_id: str, files: list[tuple[str, bytes]]) -> dict:
        """Tree KB upload — write ``.md`` files to the FS (legacy logic)."""
        col = KnowledgeBaseService._collection()
        created: list[str] = []
        errors: list[dict] = []
        max_file = settings.KB_MAX_FILE_SIZE

        for rel_path, raw in files:
            if not rel_path:
                continue
            # Normalize Windows separators + strip leading slash.
            rel_path = rel_path.replace("\\", "/").lstrip("/")
            segments = rel_path.split("/")
            if not rel_path.lower().endswith(".md"):
                errors.append({"filename": rel_path, "error": "仅支持 .md 文件"})
                continue
            if rel_path.startswith("/") or ".." in segments:
                errors.append({"filename": rel_path, "error": "非法路径"})
                continue
            if len(raw) > max_file:
                errors.append(
                    {"filename": rel_path, "error": f"文件过大（>{max_file} bytes）"}
                )
                continue
            try:
                content = raw.decode("utf-8")
            except UnicodeDecodeError:
                errors.append({"filename": rel_path, "error": "非 UTF-8 文本文件"})
                continue
            try:
                kb_fs.write_kb_file(kb_id, rel_path, content)
                created.append(rel_path)
            except ValueError:
                errors.append({"filename": rel_path, "error": "非法路径（路径穿越）"})
            except Exception as exc:
                errors.append({"filename": rel_path, "error": f"写入失败: {exc}"})

        await KnowledgeBaseService.recompute_stats(kb_id)
        await col.update_one({"_id": kb_id}, {"$set": {"updated_at": utc_now().isoformat()}})
        logger.info(
            "kb_files_uploaded",
            kb_id=kb_id,
            created_count=len(created),
            error_count=len(errors),
        )
        return {"created": created, "errors": errors, "document_ids": []}

    @staticmethod
    async def _upload_vector(
        kb_id: str,
        kb_doc: dict,
        files: list[tuple[str, bytes]],
        uploaded_by: str,
        *,
        chunk_strategy: str = "recursive",
    ) -> dict:
        """Vector KB upload — store original + create pending doc + dispatch index task."""
        from app.models.file_library import FileConsumerKind
        from app.services.file_service import FileService
        from app.services.file_storage import LocalFileStorage
        from app.services.knowledge_document_service import (
            KnowledgeDocumentService,
        )

        owner = kb_doc.get("owner_user_id") or uploaded_by or "system"
        file_service = FileService(storage=LocalFileStorage())

        allowed = {t.strip().lstrip(".").lower()
                   for t in settings.KB_VECTOR_ALLOWED_TYPES.split(",")}
        max_file = settings.KB_VECTOR_MAX_FILE_SIZE

        created: list[str] = []
        errors: list[dict] = []
        document_ids: list[str] = []

        for rel_path, raw in files:
            filename = rel_path.replace("\\", "/").split("/")[-1] or rel_path
            if not filename:
                continue
            ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
            if ext not in allowed:
                errors.append(
                    {"filename": filename, "error": f"不支持的文件类型 .{ext}（支持 pdf/docx/pptx/xlsx/csv/md/txt/html）"}
                )
                continue
            if len(raw) > max_file:
                errors.append(
                    {"filename": filename, "error": f"文件过大（>{max_file} bytes）"}
                )
                continue
            try:
                # Store original file (FileRef, sha256-deduped).
                mime = _guess_mime(ext)
                fref = await file_service.create(
                    data=raw,
                    filename=filename,
                    mime_type=mime,
                    owner_user_id=owner,
                    origin_kind=FileConsumerKind.KNOWLEDGE_BASE,
                    origin_id=kb_id,
                )
                await file_service.add_usage(
                    fref.id, FileConsumerKind.KNOWLEDGE_BASE, kb_id
                )
                # Create pending document record.
                doc = await KnowledgeDocumentService.create(
                    knowledge_base_id=kb_id,
                    file_ref_id=fref.id,
                    name=filename,
                    file_type=ext,
                    file_size=len(raw),
                    uploaded_by=uploaded_by,
                    chunk_strategy=chunk_strategy,
                )
                document_ids.append(doc.id)
                created.append(filename)
                # Dispatch async indexing (Celery).
                _dispatch_index_task(doc.id)
            except Exception as exc:
                logger.exception("kb_vector_upload_failed", filename=filename)
                errors.append({"filename": filename, "error": f"上传失败: {exc}"})

        await KnowledgeBaseService.recompute_vector_stats(kb_id)
        logger.info(
            "kb_vector_uploaded",
            kb_id=kb_id,
            accepted=len(created),
            error_count=len(errors),
        )
        return {"created": created, "errors": errors, "document_ids": document_ids}

    @staticmethod
    async def recompute_stats(kb_id: str) -> dict:
        file_count, total_size = kb_fs.compute_stats(kb_id)
        await KnowledgeBaseService._collection().update_one(
            {"_id": kb_id},
            {"$set": {"file_count": file_count, "total_size": total_size}},
        )
        return {"file_count": file_count, "total_size": total_size}

    @staticmethod
    async def recompute_vector_stats(kb_id: str) -> None:
        """Refresh file_count/total_size for a vector KB from the
        ``knowledge_documents`` collection (vector docs live there, not on
        disk like tree KBs). Called after upload/delete to keep the list
        view's "X 文件 / Y MB" badge accurate.
        """
        from app.services.knowledge_document_service import (
            KnowledgeDocumentService,
        )

        count, size = await KnowledgeDocumentService.compute_kb_stats(kb_id)
        await KnowledgeBaseService._collection().update_one(
            {"_id": kb_id},
            {
                "$set": {
                    "file_count": count,
                    "total_size": size,
                    "updated_at": utc_now().isoformat(),
                }
            },
        )


# ── module-level helpers ────────────────────────────────────────────────

_MIME_MAP = {
    "pdf": "application/pdf",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "csv": "text/csv",
    "md": "text/markdown",
    "markdown": "text/markdown",
    "txt": "text/plain",
    "html": "text/html",
    "htm": "text/html",
}


def _guess_mime(ext: str) -> str:
    """Map a file extension to a MIME type (best-effort)."""
    return _MIME_MAP.get(ext.lower(), "application/octet-stream")


def _dispatch_index_task(doc_id: str) -> None:
    """Dispatch the Celery indexing task for a document.

    Imported lazily so the service module doesn't hard-depend on the worker
    at import time (and tests can monkeypatch this). If Celery isn't running
    the task is still enqueued (picked up when a worker is available).
    """
    try:
        from app.workers.tasks.kb_indexing import index_kb_document

        index_kb_document.delay(doc_id)
    except Exception as exc:  # Celery broker down — surface loudly.
        logger.error("kb_index_dispatch_failed", doc_id=doc_id, error=str(exc))
        raise RuntimeError(f"无法派发索引任务，请检查 Celery 是否运行: {exc}") from exc
