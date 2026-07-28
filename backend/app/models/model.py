"""LLM Model data model for MongoDB.

Each ``Model`` document describes one LLM endpoint that the platform
can call: model id, base URL, encrypted API key, compatibility type,
and default inference parameters. Agents reference models by ``_id``.
"""
from enum import StrEnum

from pydantic import BaseModel, Field
from pydantic.config import ConfigDict

from app.models.base import generate_id, utc_now


class ModelStatus(StrEnum):
    """Model lifecycle status."""

    ACTIVE = "active"
    INACTIVE = "inactive"


class CompatibilityType(StrEnum):
    """Provider-agnostic compatibility protocol for the model endpoint."""

    OPENAI = "openai"
    ANTHROPIC = "anthropic"


class AuthType(StrEnum):
    """Authentication scheme used by the upstream model endpoint.

    Different providers authenticate API keys via different HTTP headers.
    This enum lets the factory construct the correct auth header per
    provider without hardcoding provider-specific logic.

    - ``bearer``: ``Authorization: Bearer {key}`` (OpenAI / DeepSeek
      / Qwen / most OpenAI-compatible endpoints)
    - ``x_api_key``: ``x-api-key: {key}`` (Anthropic native style)
    - ``api_key_header``: ``api-key: {key}`` (Azure OpenAI style)
    - ``custom``: Use the ``auth_header_format`` template to build
      arbitrary header(s).
    """

    BEARER = "bearer"
    X_API_KEY = "x_api_key"
    API_KEY_HEADER = "api_key_header"
    CUSTOM = "custom"


class ModelTaskType(StrEnum):
    """What a Model is used for.

    - ``chat``: Conversational/completion LLM (the default — backward
      compatible with all pre-existing models).
    - ``embedding``: Text embedding model used by vector knowledge bases.
    - ``rerank``: Cross-encoder reranker used to re-rank retrieved chunks.
    """

    CHAT = "chat"
    EMBEDDING = "embedding"
    RERANK = "rerank"


class Model(BaseModel):
    """MongoDB model document.

    Follows the same pattern as ``Agent`` — raw Pydantic model,
    serialized to dict for MongoDB insertion/update.

    The ``api_key`` field stores the AES-256-GCM ciphertext produced
    by :mod:`app.core.crypto`. Plaintext keys only exist transiently
    in memory during creation / retrieval for LLM calls.
    """

    model_config = ConfigDict(populate_by_name=True)

    id: str = Field(default_factory=lambda: generate_id("model"), alias="_id")
    # The model identifier sent to the upstream provider, e.g.
    # "deepseek-chat", "gpt-4o-mini", "claude-3-5-sonnet".
    model_id: str = Field(..., min_length=1, max_length=200)
    # Display name shown in the UI, e.g. "DeepSeek V3 Chat".
    name: str = Field(..., min_length=1, max_length=100)
    # Upstream base URL, e.g. "https://api.deepseek.com/v1".
    base_url: str = Field(..., min_length=1, max_length=500)
    # AES-256-GCM ciphertext (Base64). See app.core.crypto.
    api_key: str = Field(..., min_length=1, description="Encrypted API key")
    compatibility_type: CompatibilityType = Field(
        default=CompatibilityType.OPENAI,
        description="Compatibility protocol: openai | anthropic",
    )
    auth_type: AuthType = Field(
        default=AuthType.BEARER,
        description="Authentication scheme: bearer | x_api_key | api_key_header | custom",
    )
    # Optional auth header template. Only used when auth_type == "custom".
    # Supports ``{key}`` placeholder, e.g. "Bearer {key}" or
    # JSON ``{"X-My-Key": "{key}"}``.
    auth_header_format: str = Field(default="Bearer {key}", max_length=500)
    # Default inference parameters merged into every LLM call.
    default_params: dict = Field(
        default_factory=lambda: {
            "temperature": 0.7,
            "max_tokens": 4096,
            "context_window": 128000,
        },
        description="Default inference parameters (temperature, max_tokens, context_window, ...)",
    )
    status: ModelStatus = Field(default=ModelStatus.ACTIVE)
    # What this model is used for. chat = conversational LLM (default);
    # embedding / rerank = used by vector knowledge bases.
    task_type: ModelTaskType = Field(
        default=ModelTaskType.CHAT,
        description="Model purpose: chat | embedding | rerank",
    )
    # Result of the most recent connectivity test. None = never tested.
    # Set only by the test endpoint, never by create/update forms.
    last_test_success: bool | None = Field(default=None)
    # ISO-8601 timestamp of the most recent test. "" = never tested.
    last_test_at: str = Field(default="")
    # Optional grouping tag, e.g. "DeepSeek", "OpenAI", "通义千问".
    provider_tag: str = Field(default="", max_length=100)
    version: int = Field(default=1, ge=1)
    created_at: str = Field(default_factory=lambda: utc_now().isoformat())
    updated_at: str = Field(default_factory=lambda: utc_now().isoformat())
