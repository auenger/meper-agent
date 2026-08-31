"""OpenAI-compatible provider construction (incl. Anthropic, custom endpoints).

Migrated verbatim from the backend ``llm_factory`` provider/auth helpers,
with ``loguru`` swapped for ``structlog``. These functions are pure — given a
model document or model name, they build a LangChain chat model. The harness
never resolves model documents from a database; callers hand in the decrypted
config.

Public surface:

* :func:`build_client_from_doc` — build from a model-table document shape.
* :func:`build_client_from_env` — legacy env-var fallback for plain model names.
* :func:`build_auth_kwargs` / :func:`parse_custom_auth_headers` — auth helpers.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any, cast

import structlog
from langchain_anthropic import ChatAnthropic
from langchain_core.messages import AIMessageChunk
from langchain_core.outputs import ChatGenerationChunk
from langchain_openai import ChatOpenAI

from agent_flow_harness.llm.thinking import (
    build_thinking_kwargs,
    detect_provider,
)

if TYPE_CHECKING:
    pass

logger = structlog.get_logger(__name__)


class ReasoningChatOpenAI(ChatOpenAI):
    """ChatOpenAI + 第三方兼容端点的思考增量透传。

    标准 ``ChatOpenAI`` 只认官方 OpenAI 规范——``_convert_delta_to_message_chunk``
    只提取 ``content`` / ``tool_calls`` / ``function_call``,delta 里的非标准
    思考字段(``reasoning_content``,Qwen/DeepSeek/vLLM 等端点使用)被**直接
    丢弃**,永远到不了 ``additional_kwargs``。指向这类 base_url 时表现为
    "思考开关已传但全程无思考增量"。本子类把丢失的字段补回
    ``additional_kwargs``;流式 merge 对字符串字段自动拼接,故每个 chunk
    只放本片增量即可,on_chat_model_end 的最终消息自然是完整思考。
    """

    def _convert_chunk_to_generation_chunk(
        self,
        chunk: dict[str, Any],
        default_chunk_class: type,
        base_generation_info: dict[str, Any] | None,
    ) -> ChatGenerationChunk | None:
        generation = super()._convert_chunk_to_generation_chunk(
            chunk, default_chunk_class, base_generation_info
        )
        if generation is None:
            return None
        choices = (
            chunk.get("choices", [])
            or chunk.get("chunk", {}).get("choices", [])
        )
        if not choices or choices[0].get("delta") is None:
            return generation
        delta = choices[0]["delta"] or {}
        reasoning = delta.get("reasoning_content") or delta.get("reasoning")
        if isinstance(reasoning, str) and reasoning:
            msg = generation.message
            if isinstance(msg, AIMessageChunk):
                msg.additional_kwargs["reasoning_content"] = reasoning
        return generation


def build_client_from_doc(
    doc: dict[str, Any],
    agent_config: dict[str, Any] | None = None,
    enable_thinking: bool = False,
) -> ChatOpenAI | ChatAnthropic:
    """Build a chat model from a model-table document shape.

    Args:
        doc: Model document with decrypted ``api_key``, ``base_url``,
            ``model_id``, ``compatibility_type``, ``auth_type``,
            ``auth_header_format`` and ``default_params``.
        agent_config: Optional overrides (e.g. ``temperature``).
        enable_thinking: Enable native reasoning if supported.

    Returns:
        A configured :class:`ChatOpenAI` / :class:`ChatAnthropic`.
    """
    compatibility = doc.get("compatibility_type", "openai")
    model_id = doc["model_id"]
    base_url = doc["base_url"]
    api_key = doc["api_key"]
    auth_type = doc.get("auth_type", "bearer")
    auth_header_format = doc.get("auth_header_format", "Bearer {key}")

    model_defaults = doc.get("default_params", {})
    overrides = agent_config or {}
    temperature = overrides.get("temperature", model_defaults.get("temperature", 0.7))
    max_tokens = model_defaults.get("max_tokens")

    common_kwargs: dict[str, Any] = {
        "model": model_id,
        "temperature": float(temperature),
    }
    if max_tokens is not None:
        common_kwargs["max_tokens"] = int(max_tokens)

    auth_kwargs = build_auth_kwargs(auth_type, api_key, auth_header_format)
    thinking_kwargs = build_thinking_kwargs(
        model_id, compatibility, enable_thinking, max_tokens
    )

    if compatibility == "openai":
        # ReasoningChatOpenAI:透传第三方端点(Qwen/DeepSeek/vLLM)delta 里的
        # reasoning_content——标准 ChatOpenAI 会丢弃该字段。
        return ReasoningChatOpenAI(
            **common_kwargs, base_url=base_url,
            stream_usage=True,
            **auth_kwargs, **thinking_kwargs,
        )
    if compatibility == "anthropic":
        return ChatAnthropic(**common_kwargs, base_url=base_url, **auth_kwargs, **thinking_kwargs)
    msg = f"Unsupported compatibility type: {compatibility}"
    raise ValueError(msg)


def build_client_from_env(
    model_name: str,
    agent_config: dict[str, Any] | None = None,
    enable_thinking: bool = False,
) -> ChatOpenAI | ChatAnthropic:
    """Legacy env-var fallback: build a chat model from a plain model name.

    The caller is responsible for ensuring the matching API-key environment
    variable is set; this function only constructs the client.
    """
    overrides = agent_config or {}
    temperature = float(overrides.get("temperature", 0.7))
    provider = detect_provider(model_name)

    # openai 路径统一用 ReasoningChatOpenAI(思考增量透传,见类 docstring)。
    cls: type = ReasoningChatOpenAI if provider == "openai" else ChatAnthropic

    thinking_kwargs = build_thinking_kwargs(
        model_name, provider, enable_thinking, max_tokens=None
    )

    extra_kwargs: dict[str, Any] = {}
    if cls is ReasoningChatOpenAI:
        extra_kwargs["stream_usage"] = True

    return cast(
        "ChatOpenAI | ChatAnthropic",
        cls(
            model=model_name,
            temperature=temperature,
            **extra_kwargs,
            **thinking_kwargs,
        ),
    )


def build_auth_kwargs(
    auth_type: str,
    api_key: str,
    auth_header_format: str,
) -> dict[str, Any]:
    """Construct the correct kwargs for the LangChain chat model.

    Args:
        auth_type: One of ``bearer`` / ``x_api_key`` / ``api_key_header`` / ``custom``.
        api_key: Decrypted plaintext API key.
        auth_header_format: Template string (used when ``auth_type == "custom"``).

    Returns:
        Kwargs to spread into the chat-model constructor.
    """
    if auth_type == "bearer":
        return {"api_key": api_key}
    if auth_type == "x_api_key":
        return {"api_key": api_key, "default_headers": {"x-api-key": api_key}}
    if auth_type == "api_key_header":
        return {"api_key": api_key, "default_headers": {"api-key": api_key}}
    if auth_type == "custom":
        headers = parse_custom_auth_headers(auth_header_format, api_key)
        return {"api_key": api_key, "default_headers": headers}

    logger.warning("llm_auth_unknown_type", auth_type=auth_type)
    return {"api_key": api_key}


def parse_custom_auth_headers(template: str, api_key: str) -> dict[str, str]:
    """Parse a custom auth-header template into a header dict.

    Supported formats:
    - ``"Bearer {key}"`` → ``{"Authorization": "Bearer sk-xxx"}``
    - ``'{"X-Api-Key": "{key}"}'`` → ``{"X-Api-Key": "sk-xxx"}``
    """
    substituted = template.replace("{key}", api_key)
    try:
        parsed = json.loads(substituted)
        if isinstance(parsed, dict):
            return {str(k): str(v) for k, v in parsed.items()}
    except (json.JSONDecodeError, ValueError):
        pass
    return {"Authorization": substituted}
