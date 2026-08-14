"""MCP category data model for MongoDB.

A category groups related MCP connections together so that, when binding
tools to an agent, users can select an entire group or pick individual
connections within it.
"""
from pydantic import BaseModel, Field
from pydantic.config import ConfigDict

from app.models.base import generate_id, utc_now


class McpCategory(BaseModel):
    """MongoDB mcp_categories document model.

    MCP connections reference a category via ``category_id``. Connections
    with an empty ``category_id`` are treated as "ungrouped".
    """

    model_config = ConfigDict(populate_by_name=True)

    id: str = Field(default_factory=lambda: generate_id("mcpc"), alias="_id")
    name: str = Field(..., min_length=1, max_length=50)
    description: str = Field(default="", max_length=200)
    sort: int = Field(default=0, description="排序权重，升序")
    created_at: str = Field(default_factory=lambda: utc_now().isoformat())
    updated_at: str = Field(default_factory=lambda: utc_now().isoformat())
