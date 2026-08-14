"""API Key data model for MongoDB."""
from enum import StrEnum

from pydantic import BaseModel, Field
from pydantic.config import ConfigDict

from app.models.base import generate_id, utc_now


class ApiKeyStatus(StrEnum):
    """API Key lifecycle status."""

    ACTIVE = "active"
    REVOKED = "revoked"


class ApiKeyScope(StrEnum):
    """API Key permission scopes."""

    AGENTS_READ = "agents:read"
    AGENTS_INVOKE = "agents:invoke"
    WORKFLOWS_READ = "workflows:read"
    WORKFLOWS_INVOKE = "workflows:invoke"
    EXECUTIONS_READ = "executions:read"


ALL_SCOPES = [s.value for s in ApiKeyScope]


class ApiKeyBindings(BaseModel):
    """Resource bindings — empty list means no restriction (access all)."""

    agents: list[str] = Field(default_factory=list)
    workflows: list[str] = Field(default_factory=list)


class ApiKey(BaseModel):
    """MongoDB API Key document model.

    The raw key value is NEVER stored. Only a bcrypt hash and a short
    prefix are persisted. The full key is returned once at creation time.
    """

    model_config = ConfigDict(populate_by_name=True)

    id: str = Field(default_factory=lambda: generate_id("apikey"), alias="_id")
    name: str = Field(..., min_length=1, max_length=100)
    key_hash: str = Field(...)
    key_prefix: str = Field(...)
    owner_user_id: str = Field(...)
    scopes: list[str] = Field(default_factory=list)
    bindings: ApiKeyBindings = Field(default_factory=ApiKeyBindings)
    rate_limit: int = Field(default=60, ge=1, le=10000)
    status: ApiKeyStatus = Field(default=ApiKeyStatus.ACTIVE)
    introspect_url: str | None = Field(default=None, description="接入方 token introspection 端点（RFC 7662）")
    app_id: str = Field(default="", description="绑定的应用 ID（接入方系统 = 应用，身份 sub 的命名空间）")
    expires_at: str | None = Field(default=None)
    last_used_at: str | None = Field(default=None)
    created_at: str = Field(default_factory=lambda: utc_now().isoformat())
    updated_at: str = Field(default_factory=lambda: utc_now().isoformat())
