"""AC1/AC3/AC5 cover: provider builders + auth helpers."""

from __future__ import annotations

import pytest
from langchain_anthropic import ChatAnthropic
from langchain_core.messages import AIMessageChunk
from langchain_openai import ChatOpenAI

from agent_flow_harness.llm import build_client_from_doc, build_client_from_env
from agent_flow_harness.llm.providers.openai_compat import (
    ReasoningChatOpenAI,
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


def test_build_client_from_env_openai_uses_reasoning_subclass(monkeypatch) -> None:
    """openai 路径统一用 ReasoningChatOpenAI(思考增量透传)。"""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    llm = build_client_from_env("qwen3.8-max")
    assert isinstance(llm, ReasoningChatOpenAI)
    assert isinstance(llm, ChatOpenAI)


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


# ---------------------------------------------------------------------------
# ReasoningChatOpenAI — 第三方端点思考增量透传
# ---------------------------------------------------------------------------


def _make_reasoning_llm() -> ReasoningChatOpenAI:
    import os

    return ReasoningChatOpenAI(
        model="qwen3.8-max", api_key=os.environ.get("OPENAI_API_KEY", "sk-test"),
    )


def _stream_chunk(reasoning: str = "", content: str = "") -> dict:
    """模拟 Qwen/DeepSeek 兼容端点的流式 delta chunk。"""
    delta: dict = {}
    if reasoning:
        delta["reasoning_content"] = reasoning
    if content:
        delta["content"] = content
    return {"choices": [{"delta": delta, "index": 0}]}


def test_reasoning_content_passes_through() -> None:
    """标准 ChatOpenAI 丢弃 delta.reasoning_content;子类必须补回
    additional_kwargs(否则思考增量永远到不了 extract_thinking_text)。"""
    llm = _make_reasoning_llm()
    gen = llm._convert_chunk_to_generation_chunk(
        _stream_chunk(reasoning="让我想一想"), AIMessageChunk, None
    )
    assert gen is not None
    assert gen.message.additional_kwargs.get("reasoning_content") == "让我想一想"


def test_reasoning_chunks_merge_to_full_text() -> None:
    """流式多片增量 merge 后是完整思考文本(on_chat_model_end 消费)。"""
    from langchain_core.messages import AIMessageChunk as _Chunk

    llm = _make_reasoning_llm()
    g1 = llm._convert_chunk_to_generation_chunk(
        _stream_chunk(reasoning="第一步"), _Chunk, None
    )
    g2 = llm._convert_chunk_to_generation_chunk(
        _stream_chunk(reasoning="第二步"), _Chunk, None
    )
    assert g1 is not None and g2 is not None
    merged = g1.message + g2.message  # LangChain 流式合并(字符串自动拼接)
    assert merged.additional_kwargs["reasoning_content"] == "第一步第二步"


def test_plain_content_chunk_unaffected() -> None:
    """无思考字段的普通 chunk 行为不变。"""
    from langchain_core.messages import AIMessageChunk as _Chunk

    llm = _make_reasoning_llm()
    gen = llm._convert_chunk_to_generation_chunk(
        _stream_chunk(content="你好"), _Chunk, None
    )
    assert gen is not None
    assert gen.message.content == "你好"
    assert "reasoning_content" not in gen.message.additional_kwargs
