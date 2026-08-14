"""External identity mapping model for MongoDB.

Maps an external sub (``{app_id}:{username}`` — application-namespaced)
to a platform user. This enables cross-application access: regardless of
which application context the request carries, identities bound by the
same platform user all resolve to the same platform_user_id, whose
``user_mcp_credentials`` holds credential bindings per application.
"""
from pydantic import BaseModel, Field
from pydantic.config import ConfigDict

from app.models.base import generate_id, utc_now


def compose_sub(app_id: str, username: str) -> str:
    """Combine application id and username into a globally unique sub.

    The application id namespaces the identity — same-named accounts in
    different applications (e.g. two systems both having an "admin")
    never collide.

    This rule is shared between:
    - binding time: compose_sub(app_id, 用户输入的 username)
    - runtime: compose_sub(X-App-Id, introspection 返回的 username)
    """
    return f"{app_id}:{username}"


class ExternalIdentity(BaseModel):
    """MongoDB external_identities document model."""

    model_config = ConfigDict(populate_by_name=True)

    id: str = Field(default_factory=lambda: generate_id("extid"), alias="_id")
    sub: str = Field(..., description="username:userId 组合，全局唯一")
    platform_user_id: str = Field(..., description="关联平台 User._id")
    created_at: str = Field(default_factory=lambda: utc_now().isoformat())
    updated_at: str = Field(default_factory=lambda: utc_now().isoformat())
