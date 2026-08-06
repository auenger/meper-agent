"""KnowledgeDocument data model — per-document metadata for vector KBs.

A KnowledgeDocument is created when a user uploads a file to a vector KB.
It tracks the async indexing lifecycle (parse → clean → chunk → embed →
store in Qdrant) and links back to the original file via :class:`FileRef`.

The actual chunk text + vectors live in Qdrant (filtered by ``kb_id`` /
``doc_id``); this collection only stores document-level metadata.
"""
from pydantic import BaseModel, Field
from pydantic.config import ConfigDict

from app.models.base import generate_id, utc_now

# Indexing lifecycle states.
PENDING = "pending"
PARSING = "parsing"
EMBEDDING = "embedding"
COMPLETED = "completed"
FAILED = "failed"


class KnowledgeDocument(BaseModel):
    """MongoDB knowledge_document document model (vector KB only)."""

    model_config = ConfigDict(populate_by_name=True)

    id: str = Field(default_factory=lambda: generate_id("kbdoc"), alias="_id")
    knowledge_base_id: str = Field(..., description="所属 vector 知识库 _id")
    file_ref_id: str = Field(..., description="关联 FileRef._id（原始文件）")
    name: str = Field(..., description="原始文件名")
    file_type: str = Field(..., description="pdf / docx / md / txt")
    file_size: int = Field(default=0, ge=0, description="文件大小（字节）")
    # Indexing lifecycle: pending → parsing → embedding → completed | failed
    parse_status: str = Field(default=PENDING)
    parse_progress: int = Field(default=0, ge=0, le=100, description="0-100")
    parse_error: str = Field(default="", description="失败原因（parse_status=failed 时填充）")
    chunk_count: int = Field(default=0, ge=0, description="切片数量（completed 后填充）")
    # Chunking strategy chosen at upload time: "recursive" (token-based
    # recursive split, default) or "structure" (split by document structure
    # — Markdown headers / HTML tags / Word heading styles; unsupported file
    # types gracefully fall back to "recursive").
    chunk_strategy: str = Field(default="recursive", description="切分策略: recursive / structure")
    uploaded_by: str = Field(default="", description="上传者 user_id")
    created_at: str = Field(default_factory=lambda: utc_now().isoformat())
    updated_at: str = Field(default_factory=lambda: utc_now().isoformat())


__all__ = [
    "KnowledgeDocument",
    "PENDING",
    "PARSING",
    "EMBEDDING",
    "COMPLETED",
    "FAILED",
]
