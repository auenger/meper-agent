"""User MCP credential model for MongoDB.

Stores per-application credential bindings for a platform user. One
document per platform user; ``app_bindings`` maps ``application_id``
to encrypted username/password.
"""
from pydantic import BaseModel, Field
from pydantic.config import ConfigDict

from app.models.base import generate_id, utc_now


class UserMcpCredential(BaseModel):
    """MongoDB user_mcp_credentials document model."""

    model_config = ConfigDict(populate_by_name=True)

    id: str = Field(default_factory=lambda: generate_id("usermcp"), alias="_id")
    platform_user_id: str = Field(..., description="平台用户 _id，唯一索引")
    app_bindings: dict[str, dict] = Field(
        default_factory=dict,
        description=(
            "key = application_id，value = 凭证对象（username/password，加密态）。"
        ),
    )
    created_at: str = Field(default_factory=lambda: utc_now().isoformat())
    updated_at: str = Field(default_factory=lambda: utc_now().isoformat())
