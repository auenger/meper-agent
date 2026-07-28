"""Model-related Pydantic schemas for API request/response."""
from __future__ import annotations

from urllib.parse import urlparse

from pydantic import BaseModel, Field, field_validator

from app.models.model import AuthType, CompatibilityType, ModelStatus, ModelTaskType


def _validate_base_url(v: str) -> str:
    """Ensure base_url is a well-formed http(s) URL.

    Rejects empty/whitespace strings, garbage, and dangerous schemes
    (``file://``, ``javascript:``, ``gopher://`` …) that could enable
    SSRF or broken upstream calls downstream.
    """
    if not isinstance(v, str) or not v or not v.strip():
        raise ValueError("base_url 不能为空")
    parsed = urlparse(v.strip())
    if parsed.scheme not in ("http", "https"):
        raise ValueError("base_url 必须是合法的 http/https URL")
    if not parsed.netloc:
        raise ValueError("base_url 缺少有效的域名或主机地址")
    return v.strip()


class ModelCreate(BaseModel):
    """Schema for creating a new Model."""

    model_id: str = Field(
        ..., min_length=1, max_length=200,
        description="Model identifier sent to the upstream provider (e.g. 'deepseek-chat')",
    )
    name: str = Field(
        ..., min_length=1, max_length=100,
        description="Display name (e.g. 'DeepSeek V3 Chat')",
    )
    base_url: str = Field(
        ..., min_length=1, max_length=500,
        description="Upstream base URL (e.g. 'https://api.deepseek.com/v1')",
    )
    api_key: str = Field(
        ..., min_length=1,
        description="Plaintext API key (encrypted before storage)",
    )
    compatibility_type: CompatibilityType = Field(
        default=CompatibilityType.OPENAI,
        description="Compatibility protocol: openai | anthropic",
    )
    auth_type: AuthType = Field(
        default=AuthType.BEARER,
        description="Authentication scheme: bearer | x_api_key | api_key_header | custom",
    )
    auth_header_format: str = Field(
        default="Bearer {key}",
        max_length=500,
        description="Auth header template (used only when auth_type=custom). "
        'Supports {key} placeholder, e.g. "Bearer {key}"',
    )
    default_params: dict = Field(
        default_factory=lambda: {
            "temperature": 0.7,
            "max_tokens": 4096,
            "context_window": 128000,
        },
        description="Default inference parameters",
    )
    provider_tag: str = Field(
        default="",
        max_length=100,
        description="Optional grouping tag (e.g. 'DeepSeek', 'OpenAI')",
    )
    task_type: ModelTaskType = Field(
        default=ModelTaskType.CHAT,
        description="Model purpose: chat | embedding | rerank",
    )

    @field_validator("base_url", mode="after")
    @classmethod
    def _check_base_url(cls, v: str) -> str:
        return _validate_base_url(v)


class ModelUpdate(BaseModel):
    """Schema for updating an existing Model (full replacement via PUT)."""

    model_id: str = Field(
        ..., min_length=1, max_length=200,
        description="Model identifier sent to the upstream provider",
    )
    name: str = Field(
        ..., min_length=1, max_length=100,
        description="Display name",
    )
    base_url: str = Field(
        ..., min_length=1, max_length=500,
        description="Upstream base URL",
    )
    api_key: str = Field(
        default="",
        description="Plaintext API key. Leave empty to preserve the existing key on update.",
    )
    compatibility_type: CompatibilityType = Field(
        default=CompatibilityType.OPENAI,
        description="Compatibility protocol: openai | anthropic",
    )
    auth_type: AuthType = Field(
        default=AuthType.BEARER,
        description="Authentication scheme: bearer | x_api_key | api_key_header | custom",
    )
    auth_header_format: str = Field(
        default="Bearer {key}",
        max_length=500,
        description="Auth header template (used only when auth_type=custom)",
    )
    default_params: dict = Field(
        default_factory=lambda: {
            "temperature": 0.7,
            "max_tokens": 4096,
            "context_window": 128000,
        },
        description="Default inference parameters",
    )
    provider_tag: str = Field(
        default="",
        max_length=100,
        description="Optional grouping tag",
    )
    task_type: ModelTaskType = Field(
        default=ModelTaskType.CHAT,
        description="Model purpose: chat | embedding | rerank",
    )

    @field_validator("base_url", mode="after")
    @classmethod
    def _check_base_url(cls, v: str) -> str:
        return _validate_base_url(v)


class ModelResponse(BaseModel):
    """Model data returned in API responses."""

    id: str
    model_id: str
    name: str
    base_url: str
    api_key: str = Field(description="Masked API key (e.g. 'sk-****abcd')")
    compatibility_type: CompatibilityType
    auth_type: AuthType = AuthType.BEARER
    auth_header_format: str = ""
    default_params: dict = Field(default_factory=dict)
    status: ModelStatus
    task_type: ModelTaskType = ModelTaskType.CHAT
    last_test_success: bool | None = None
    last_test_at: str = ""
    provider_tag: str = ""
    version: int = 1
    created_at: str
    updated_at: str


class ModelListResponse(BaseModel):
    """Paginated model list response."""

    items: list[ModelResponse]
    total: int
    page: int
    page_size: int


class ModelTestResponse(BaseModel):
    """Response schema for the model connectivity test endpoint."""

    success: bool = Field(description="Whether the probe request succeeded")
    latency_ms: int = Field(description="Round-trip latency in milliseconds")
    reply: str = Field(default="", description="Model reply text (truncated to 500 chars)")
    error: str = Field(default="", description="Human-readable error message (empty on success)")
    error_code: str = Field(default="", description="Machine-readable error code (empty on success)")
    tested_at: str = Field(description="ISO-8601 timestamp of the test")
