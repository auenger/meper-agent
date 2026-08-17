"""AC1/AC3/AC5 cover: provider builders + auth helpers."""

from __future__ import annotations

import pytest
from langchain_anthropic import ChatAnthropic
from langchain_openai import ChatOpenAI

from agent_flow_harness.llm import build_client_from_doc, build_client_from_env
from agent_flow_harness.llm.providers.openai_compat import (
    build_auth_kwargs,
    parse_custom_auth_headers,
)


# ---------------------------------------------------------------------------
# build_client_from_env
# ---------------------------------------------------------------------------


def test_build_client_from_env_openai(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    llm = build_client_from_env("gpt-4o-mini", {"temperature": 0.3})
    assert isinstance(llm, ChatOpenAI)
    assert llm.model_name == "gpt-4o-mini"
    assert llm.temperature == 0.3


def test_build_client_from_env_anthropic() -> None:
    llm = build_client_from_env("claude-sonnet-4")
    assert isinstance(llm, ChatAnthropic)


def test_build_client_from_env_unknown_provider_raises() -> None:
    """A model name that maps to no known provider raises ValueError."""
    # detect_provider falls back to openai for anything non-claude, so to
    # exercise the unsupported branch we patch the known-providers map.
    import agent_flow_harness.llm.providers.openai_compat as mod

    original = mod._KNOWN_PROVIDERS.copy()
    try:
        mod._KNOWN_PROVIDERS.clear()
        with pytest.raises(ValueError, match="Unsupported"):
            build_client_from_env("gpt-4o")
    finally:
        mod._KNOWN_PROVIDERS.update(original)


# ---------------------------------------------------------------------------
# build_client_from_doc
# ---------------------------------------------------------------------------


def _doc(**over):
    base = {
        "model_id": "gpt-4o-mini",
        "base_url": None,
        "api_key": "sk-test",
        "compatibility_type": "openai",
        "auth_type": "bearer",
        "default_params": {"temperature": 0.7, "max_tokens": 1024},
    }
    base.update(over)
    return base


def test_build_client_from_doc_openai() -> None:
    llm = build_client_from_doc(_doc(), enable_thinking=False)
    assert isinstance(llm, ChatOpenAI)
    assert llm.model_name == "gpt-4o-mini"
    assert llm.temperature == 0.7


def test_build_client_from_doc_anthropic_with_thinking() -> None:
    llm = build_client_from_doc(
        _doc(model_id="claude-sonnet-4", compatibility_type="anthropic", default_params={"temperature": 0.7, "max_tokens": 8192}),
        enable_thinking=True,
    )
    assert isinstance(llm, ChatAnthropic)
    # thinking kwargs applied at construction — budget adapts to half of
    # max_tokens (8192 // 2 = 4096 < default 5000).
    assert getattr(llm, "thinking", None) == {"type": "enabled", "budget_tokens": 4096}


def test_build_client_from_doc_agent_overrides_temperature() -> None:
    llm = build_client_from_doc(_doc(), agent_config={"temperature": 0.1})
    assert llm.temperature == 0.1


def test_build_client_from_doc_unsupported_compatibility_raises() -> None:
    with pytest.raises(ValueError, match="compatibility type"):
        build_client_from_doc(_doc(compatibility_type="gemini"))


# ---------------------------------------------------------------------------
# auth helpers
# ---------------------------------------------------------------------------


def test_build_auth_kwargs_bearer() -> None:
    assert build_auth_kwargs("bearer", "k", "Bearer {key}") == {"api_key": "k"}


def test_build_auth_kwargs_x_api_key() -> None:
    out = build_auth_kwargs("x_api_key", "k", "")
    assert out["default_headers"] == {"x-api-key": "k"}


def test_build_auth_kwargs_api_key_header() -> None:
    out = build_auth_kwargs("api_key_header", "k", "")
    assert out["default_headers"] == {"api-key": "k"}


def test_build_auth_kwargs_custom_template_plain() -> None:
    out = build_auth_kwargs("custom", "k", "Bearer {key}")
    assert out["default_headers"] == {"Authorization": "Bearer k"}


def test_build_auth_kwargs_custom_template_json() -> None:
    out = build_auth_kwargs("custom", "k", '{"X-My-Key": "{key}"}')
    assert out["default_headers"] == {"X-My-Key": "k"}


def test_build_auth_kwargs_unknown_falls_back() -> None:
    assert build_auth_kwargs("mystery", "k", "") == {"api_key": "k"}


def test_parse_custom_auth_headers_json_and_plain() -> None:
    assert parse_custom_auth_headers("Bearer {key}", "sk") == {"Authorization": "Bearer sk"}
    assert parse_custom_auth_headers('{"X-Key": "{key}"}', "sk") == {"X-Key": "sk"}
