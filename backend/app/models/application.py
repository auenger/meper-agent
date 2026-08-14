"""Application data model for MongoDB.

An application is the authorization boundary for external systems —
admin creates one per external system (e.g. "OA", "最成"), binds MCP
connections to it, and users authorize the application with their
credentials for that system.

The application id also namespaces external identities:
``sub = f"{app_id}:{username}"`` — same-named accounts in different
applications never collide.
"""
from pydantic import BaseModel, Field
from pydantic.config import ConfigDict

from app.models.base import generate_id, utc_now


class Application(BaseModel):
    """MongoDB applications document model."""

    model_config = ConfigDict(populate_by_name=True)

    id: str = Field(default_factory=lambda: generate_id("app"), alias="_id")
    name: str = Field(..., min_length=1, max_length=100)
    description: str = Field(default="", max_length=500)
    mcp_connection_ids: list[str] = Field(
        default_factory=list,
        description="绑定的 MCP 连接（正向关联，可按分组批量或单个选择）",
    )
    login_config: dict = Field(
        default_factory=dict,
        description=(
            "账密验证配置。字段：login_url/method/username_field/"
            "password_field/token_jsonpath/session_ttl。"
            "仅用于验证用户账密，不提取身份（sub 由 app_id + 用户名组合）。"
        ),
    )
    created_at: str = Field(default_factory=lambda: utc_now().isoformat())
    updated_at: str = Field(default_factory=lambda: utc_now().isoformat())
