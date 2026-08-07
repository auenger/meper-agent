"""Tool-related Pydantic schemas for API request/response."""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class SkillFileResponse(BaseModel):
    """A single file in a Skill directory package."""

    path: str
    content: str
    size: int = 0


class SkillFileUpdate(BaseModel):
    """Request body for updating a single file's content."""

    content: str = Field(..., min_length=1, description="新的文件内容")


class SkillFileTreeNode(BaseModel):
    """A node in the file tree (file or directory)."""

    key: str = Field(..., description="唯一标识（相对路径）")
    title: str = Field(..., description="显示名称")
    is_leaf: bool = Field(default=True)
    children: list[SkillFileTreeNode] | None = Field(default=None)
    size: int = Field(default=0, description="文件大小（仅文件节点有效）")


class SkillFileTreeResponse(BaseModel):
    """Response for file tree endpoint."""

    tool_id: str
    files: list[SkillFileTreeNode]


class ToolResponse(BaseModel):
    """Tool data returned in API responses."""

    id: str
    name: str
    description: str
    input_schema: dict[str, Any] = Field(default_factory=dict)
    output_schema: dict[str, Any] = Field(default_factory=dict)
    instructions: str = ""
    source: str = "markdown"
    source_file: str = ""
    mcp_connection_id: str = ""
    version: int
    tags: list[str] = Field(default_factory=list)
    avatar: str = ""
    files: list[SkillFileResponse] = Field(default_factory=list)
    created_at: str
    updated_at: str


class ToolListResponse(BaseModel):
    """Paginated tool list response."""

    items: list[ToolResponse]
    total: int
    page: int
    page_size: int


class ToolUpdate(BaseModel):
    """Schema for updating an existing Tool (PUT).

    Only ``tags`` is user-editable — the schemas and instructions come
    from the uploaded Markdown file and can be re-uploaded if needed.
    """

    tags: list[str] | None = Field(
        default=None, description="New tags. None preserves existing."
    )


class BuiltinToolResponse(BaseModel):
    """A single built-in tool's metadata."""

    name: str
    description: str
    parameters: dict[str, Any] = Field(
        default_factory=dict, description="JSON Schema of the tool's parameters"
    )
    configurable: bool = Field(
        default=True,
        description="Whether the user can enable/disable this tool per Agent. "
        "False means always-on (e.g. capability tools like ask_clarification).",
    )


class ToolUploadErrorItem(BaseModel):
    """Single file error in an upload batch."""

    filename: str
    error: str


class ToolUploadResponse(BaseModel):
    """Batch upload response — per-file results."""

    created: list[ToolResponse] = Field(default_factory=list)
    errors: list[ToolUploadErrorItem] = Field(default_factory=list)
