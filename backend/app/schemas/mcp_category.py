"""MCP category Pydantic schemas for API request/response."""
from __future__ import annotations

from pydantic import BaseModel, Field


class McpCategoryBase(BaseModel):
    """Base fields for MCP category create/update."""

    name: str = Field(..., min_length=1, max_length=50, description="分组名称")
    description: str = Field(default="", max_length=200, description="分组描述")
    sort: int = Field(default=0, description="排序权重，升序")


class McpCategoryCreate(McpCategoryBase):
    """Schema for creating a new MCP category."""


class McpCategoryUpdate(McpCategoryBase):
    """Schema for updating an existing MCP category (full PUT)."""


class McpCategoryResponse(BaseModel):
    """MCP category data returned in API responses."""

    id: str
    name: str
    description: str
    sort: int
    created_at: str
    updated_at: str


class McpCategoryListResponse(BaseModel):
    """MCP category list response (unpaginated — categories are few)."""

    items: list[McpCategoryResponse]
    total: int
