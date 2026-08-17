"""Thinking-mode (native LLM reasoning) adaptation.

Migrated verbatim from the backend ``llm_factory`` thinking helpers, with
``loguru`` swapped for ``structlog``. Two surfaces:

* :func:`build_thinking_kwargs` — produces the constructor kwargs that enable
  (or explicitly disable) native reasoning for a model + provider pair. Used
  by the provider builder so thinking is applied at construction time.
* :func:`apply_thinking_mode` — mutates an already-built chat model to turn
  reasoning on (best-effort; some LangChain clients expose ``thinking`` /
  ``reasoning_effort`` attributes).
* :func:`supports_thinking` — predicate for UI/API pre-validation.

Provider behaviour:

* **Anthropic** (``claude-*``): ``thinking={"type": "enabled",
  "budget_tokens": ...}``; requires ``max_tokens > budget``.
* **OpenAI o-series** (``o1-*`` / ``o3-*`` / ``o4-*``): ``reasoning_effort="high"``.
* **Others**: silently degrade (no exception).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import structlog

if TYPE_CHECKING:
    from langchain_core.language_models.chat_models import BaseChatModel

logger = structlog.get_logger(__name__)

# Model-name prefixes that drive provider detection (legacy env-var path).
_ANTHROPIC_PREFIXES: tuple[str, ...] = ("claude-",)

# OpenAI reasoning models that support reasoning_effort.
_OPENAI_REASONING_PREFIXES: tuple[str, ...] = ("o1-", "o3-", "o4-")

# Default token budget for Claude extended thinking.
_ANTHROPIC_THINKING_BUDGET = 5000

# Anthropic requires budget_tokens >= 1024; below this max_tokens there is no
# room for a meaningful thinking budget + answer, so thinking is disabled.
_MIN_THINKING_MAX_TOKENS = 2048


def build_thinking_kwargs(
    model_id: str,
    provider_or_compatibility: str,
    enable_thinking: bool,
    max_tokens: int | None = None,
) -> dict[str, Any]:
    """Build constructor kwargs enabling/disabling native LLM reasoning.

    Args:
        model_id: Model identifier (e.g. ``"claude-sonnet-4"``, ``"o3-mini"``).
        provider_or_compatibility: Provider name (``"openai"`` / ``"anthropic"``)
            from detection, or a ``compatibility_type`` from a model document.
        enable_thinking: Whether the caller requested thinking mode.
        max_tokens: Optional ``max_tokens`` (Anthropic needs ``max_tokens >
            budget``).

    Returns:
        Kwargs to spread into the chat-model constructor. Empty when thinking
        is disabled/unsupported.
    """
    if not enable_thinking:
        # Explicitly disable thinking for providers that default to returning
        # reasoning content (e.g. DeepSeek).
        if provider_or_compatibility == "openai":
            return {"extra_body": {"thinking": {"type": "disabled"}}}
        if provider_or_compatibility == "anthropic":
            return {"thinking": {"type": "disabled"}}
        return {}

    # Anthropic path.
    if provider_or_compatibility == "anthropic":
        budget = _ANTHROPIC_THINKING_BUDGET
        if max_tokens is not None:
            if int(max_tokens) < _MIN_THINKING_MAX_TOKENS:
                # Too small to fit a meaningful budget + answer — explicitly
                # disable so the model doesn't emit thinking on its own
                # (some gateways default it on) and duplicate reasoning in
                # the visible text.
                logger.warning(
                    "llm_thinking_max_tokens_too_small",
                    max_tokens=max_tokens,
                    min_required=_MIN_THINKING_MAX_TOKENS,
                )
                return {"thinking": {"type": "disabled"}}
            # Adapt the budget so thinking is actually enabled instead of
            # silently dropped: cap it at half the token window, leaving the
            # rest for the answer. (Previously max_tokens <= budget caused the
            # kwargs to be dropped entirely, leaving the model to its own
            # default — which made GLM emit reasoning both as a thinking
            # block AND as a "思考过程" section in the text.)
            budget = min(budget, int(max_tokens) // 2)
        kwargs: dict[str, Any] = {
            "thinking": {"type": "enabled", "budget_tokens": budget},
        }
        if max_tokens is None:
            # Anthropic requires max_tokens when thinking is enabled.
            kwargs["max_tokens"] = budget * 4
        return kwargs

    # OpenAI o-series path.
    if provider_or_compatibility == "openai":
        if any(model_id.startswith(p) for p in _OPENAI_REASONING_PREFIXES):
            return {"reasoning_effort": "high"}
        logger.info("llm_thinking_not_supported", model_id=model_id)
        return {}

    # Unknown provider — silently degrade.
    logger.info("llm_thinking_unknown_provider", provider=provider_or_compatibility)
    return {}


def apply_thinking_mode(
    llm: BaseChatModel,
    *,
    enable_thinking: bool,
    model_name: str,
) -> BaseChatModel:
    """Best-effort mutate an already-built chat model to enable reasoning.

    Unlike :func:`build_thinking_kwargs` (applied at construction), this toggles
    the runtime attributes some LangChain clients expose (``thinking`` for
    Anthropic, ``reasoning_effort`` for OpenAI o-series). Unsupported models
    are left untouched.

    Args:
        llm: A built chat model.
        enable_thinking: Whether to enable reasoning.
        model_name: Model id, used to pick the provider knob.

    Returns:
        The same ``llm`` instance (mutated in place when supported).
    """
    if not enable_thinking:
        return llm

    lowered = model_name.lower()

    # Claude / Anthropic.
    if lowered.startswith(_ANTHROPIC_PREFIXES) or "anthropic" in type(llm).__name__.lower():
        if hasattr(llm, "thinking"):
            llm.thinking = {
                "type": "enabled",
                "budget_tokens": _ANTHROPIC_THINKING_BUDGET,
            }
        else:
            logger.warning(
                "llm_thinking_attribute_missing",
                model_name=model_name,
                attribute="thinking",
            )
        return llm

    # OpenAI o-series.
    if lowered.startswith(_OPENAI_REASONING_PREFIXES):
        if hasattr(llm, "reasoning_effort"):
            llm.reasoning_effort = "high"
        else:
            logger.warning(
                "llm_thinking_attribute_missing",
                model_name=model_name,
                attribute="reasoning_effort",
            )
        return llm

    logger.info("llm_thinking_ignored", model_name=model_name)
    return llm


def supports_thinking(model_id: str, compatibility: str) -> bool:
    """Return whether a model supports native LLM reasoning.

    Public helper for UI/API pre-validation before showing the thinking toggle.
    """
    if compatibility == "anthropic":
        return True
    if compatibility == "openai":
        return any(model_id.startswith(p) for p in _OPENAI_REASONING_PREFIXES)
    return False


def detect_provider(model: str) -> str:
    """Return ``"anthropic"`` or ``"openai"`` (fallback) for a model name."""
    if any(model.startswith(p) for p in _ANTHROPIC_PREFIXES):
        return "anthropic"
    return "openai"
