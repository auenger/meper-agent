"""KnowledgeBase-related Pydantic schemas for API request/response."""
from __future__ import annotations

from pydantic import BaseModel, Field


class KbFileResponse(BaseModel):
    """A single .md file in a KB directory."""

    path: str
    content: str
    size: int = 0


class KbFileUpdate(BaseModel):
    """Request body for updating a single KB file's content."""

    content: str = Field(..., min_length=1, description="新的文件内容")


class KbFileTreeNode(BaseModel):
    """A node in the KB file tree (file or directory)."""

    key: str = Field(..., description="唯一标识（相对路径）")
    title: str = Field(..., description="显示名称")
    is_leaf: bool = Field(default=True)
    children: list[KbFileTreeNode] | None = Field(default=None)
    size: int = Field(default=0, description="文件大小（仅文件节点有效）")


class KbFileTreeResponse(BaseModel):
    """Response for KB file tree endpoint."""

    kb_id: str
    files: list[KbFileTreeNode]


class KnowledgeBaseResponse(BaseModel):
    """KnowledgeBase data returned in API responses."""

    id: str
    name: str
    description: str = ""
    type: str = "tree"
    embedding_model_id: str = ""
    owner_user_id: str = ""
    status: str = "active"
    file_count: int = 0
    total_size: int = 0
    created_at: str
    updated_at: str


class KnowledgeBaseListResponse(BaseModel):
    """Paginated knowledge base list response."""

    items: list[KnowledgeBaseResponse]
    total: int
    page: int
    page_size: int


class KnowledgeBaseCreate(BaseModel):
    """Schema for creating a new KnowledgeBase (POST)."""

    name: str = Field(..., min_length=1, max_length=100)
    description: str = Field(default="", max_length=500)
    type: str = Field(
        default="tree",
        description="tree (Markdown 文件树，agent 探索) / vector (RAG 语义检索)",
    )


class KnowledgeBaseUpdate(BaseModel):
    """Schema for updating an existing KnowledgeBase (PUT)."""

    name: str | None = Field(default=None, min_length=1, max_length=100)
    description: str | None = Field(default=None, max_length=500)


class KbUploadErrorItem(BaseModel):
    """Single file error in an upload batch."""

    filename: str
    error: str


class KbUploadResponse(BaseModel):
    """Batch upload response — per-file results.

    KB files are not separate DB entities (they live on the FS), so
    ``created`` is a list of relative paths written, not full objects.
    For vector KBs, ``document_ids`` lists the created KnowledgeDocument ids.
    """

    created: list[str] = Field(default_factory=list, description="成功写入的相对路径/文件名")
    errors: list[KbUploadErrorItem] = Field(default_factory=list)
    document_ids: list[str] = Field(
        default_factory=list, description="vector KB: 创建的文档 id 列表"
    )


# ── Vector KB: documents + retrieval ────────────────────────────────────


class KbDocumentItem(BaseModel):
    """A document in a vector KB (KnowledgeDocument metadata)."""

    id: str
    name: str
    file_type: str
    file_size: int = 0
    parse_status: str = "pending"
    parse_progress: int = 0
    parse_error: str = ""
    chunk_count: int = 0
    created_at: str
    updated_at: str


class KbDocumentListResponse(BaseModel):
    """Paginated document list for a vector KB."""

    items: list[KbDocumentItem]
    total: int
    page: int
    page_size: int


class KbSearchRequest(BaseModel):
    """Body for the vector KB retrieval-test endpoint."""

    query: str = Field(..., min_length=1, description="检索查询文本")
    top_k: int = Field(default=5, ge=1, le=50, description="返回结果数")


class KbSearchResultItem(BaseModel):
    """One retrieved chunk with citation metadata."""

    text: str
    score: float
    doc_id: str = ""
    source_file: str = ""
    page: int | None = None


class KbSearchResponse(BaseModel):
    """Retrieval-test response."""

    query: str
    results: list[KbSearchResultItem]
