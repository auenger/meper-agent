"""KnowledgeBase data model for MongoDB — metadata for tree & vector KBs.

Two KB types coexist:

- ``tree`` (default): a directory of ``.md`` files on the filesystem
  (managed by ``engine/tool/kb_fs.py``). Agents explore it at runtime via
  ``kb_glob`` / ``kb_grep`` / ``kb_read``. MongoDB stores only metadata.
- ``vector``: documents are parsed → cleaned → chunked → embedded and
  stored as dense+sparse vectors in Qdrant (``kb_chunks`` collection),
  filtered by ``kb_id``. Searched via the ``kb_search`` tool/node. The
  ``embedding_model_id`` records which embedding model was used.

File contents do NOT live here (unlike ``Tool.files``); the source of
truth for tree KBs is the FS, and for vector KBs is Qdrant + FileRef.
Agents bind KBs via ``Agent.knowledge_base_ids``.
"""
from pydantic import BaseModel, Field
from pydantic.config import ConfigDict

from app.models.base import generate_id, utc_now


class KnowledgeBase(BaseModel):
    """MongoDB knowledge_base document model.

    Follows the same pattern as ``Tool`` — raw Pydantic model serialized
    to dict for MongoDB insertion/update.
    """

    model_config = ConfigDict(populate_by_name=True)

    id: str = Field(default_factory=lambda: generate_id("kb"), alias="_id")
    name: str = Field(..., min_length=1, max_length=100)
    description: str = Field(default="", max_length=500)
    # KB type: "tree" (Markdown file tree, agent-explorable) or "vector"
    # (RAG over Qdrant, search via kb_search). Defaults to tree for
    # backward compatibility with pre-existing KBs.
    type: str = Field(default="tree", description="tree / vector")
    # Vector KB only: which embedding model generated the dense vectors.
    # Mirrors settings.KB_EMBEDDING_MODEL_ID at creation time.
    embedding_model_id: str = Field(default="")
    owner_user_id: str = Field(default="")
    status: str = Field(default="active", description="active / archived")
    # Cached stats — refreshed by kb_service.recompute_stats after FS changes
    # (tree KB: .md file count/size; vector KB: derived from KnowledgeDocument).
    file_count: int = Field(default=0)
    total_size: int = Field(default=0)
    created_at: str = Field(default_factory=lambda: utc_now().isoformat())
    updated_at: str = Field(default_factory=lambda: utc_now().isoformat())
