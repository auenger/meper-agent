"""LLM client factory facade.

The harness owns the *pure* LLM-construction logic (provider selection,
auth-kwargs, thinking adaptation) in :mod:`agent_flow_harness.llm.providers`
and :mod:`agent_flow_harness.llm.thinking`. It deliberately does **not** own
model-table resolution: looking up a model document, decrypting its API key,
and reading agent ``temperature_override`` is backend infrastructure that
stays in the application layer (harness rule: no DB / crypto coupling here).

This module exposes a thin facade so callers build clients through one
harness entry point:

* :func:`build_client_from_doc` — delegate to the provider builder (the app
  resolves the model document first, then hands it here).
* :func:`build_client_from_env` — legacy plain-model-name fallback.

The application's own ``get_llm_client`` orchestrates the table lookup +
delegates to :func:`build_client_from_doc` / :func:`build_client_from_env`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from agent_flow_harness.llm.providers.openai_compat import (
    build_client_from_doc as _build_client_from_doc,
)
from agent_flow_harness.llm.providers.openai_compat import (
    build_client_from_env as _build_client_from_env,
)
from agent_flow_harness.llm.thinking import (
    apply_thinking_mode,
    build_thinking_kwargs,
    detect_provider,
    supports_thinking,
)

if TYPE_CHECKING:
    from langchain_anthropic import ChatAnthropic
    from langchain_openai import ChatOpenAI


def build_client_from_doc(
    doc: dict[str, Any],
    agent_config: dict[str, Any] | None = None,
    enable_thinking: bool = False,
) -> ChatOpenAI | ChatAnthropic:
    """Build a chat model from a resolved model-table document.

    The caller (application layer) is responsible for resolving ``doc`` from
    the models collection and decrypting ``api_key`` first.
    """
    return _build_client_from_doc(doc, agent_config, enable_thinking=enable_thinking)


def build_client_from_env(
    model_name: str,
    agent_config: dict[str, Any] | None = None,
    enable_thinking: bool = False,
) -> ChatOpenAI | ChatAnthropic:
    """Legacy env-var fallback: build a chat model from a plain model name."""
    return _build_client_from_env(model_name, agent_config, enable_thinking=enable_thinking)


__all__ = [
    "apply_thinking_mode",
    "build_client_from_doc",
    "build_client_from_env",
    "build_thinking_kwargs",
    "detect_provider",
    "supports_thinking",
]
