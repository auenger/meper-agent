"""AC4/AC8 cover: thinking-mode adaptation."""

from __future__ import annotations

from types import SimpleNamespace

from agent_flow_harness.llm.thinking import (
    apply_thinking_mode,
    build_thinking_kwargs,
    detect_provider,
    supports_thinking,
)


# ---------------------------------------------------------------------------
# build_thinking_kwargs
# ---------------------------------------------------------------------------


def test_thinking_disabled_openai_explicitly_disables() -> None:
    """Disabled on openai returns an explicit disable extra_body."""
    out = build_thinking_kwargs("gpt-4o", "openai", enable_thinking=False)
    assert out == {"extra_body": {"thinking": {"type": "disabled"}}}


def test_thinking_disabled_anthropic_explicitly_disables() -> None:
    out = build_thinking_kwargs("claude-sonnet-4", "anthropic", enable_thinking=False)
    assert out == {"thinking": {"type": "disabled"}}


def test_thinking_anthropic_enables_with_budget() -> None:
    out = build_thinking_kwargs("claude-sonnet-4", "anthropic", enable_thinking=True)
    assert out["thinking"] == {"type": "enabled", "budget_tokens": 5000}
    # max_tokens auto-set when absent (Anthropic requirement).
    assert "max_tokens" in out


def test_thinking_anthropic_max_tokens_too_small_disables() -> None:
    """max_tokens below the floor (no room for budget + answer) must
    explicitly disable thinking — otherwise gateways that default thinking
    on emit reasoning twice (thinking block + "思考过程" in text)."""
    out = build_thinking_kwargs(
        "claude-sonnet-4", "anthropic", enable_thinking=True, max_tokens=100
    )
    assert out == {"thinking": {"type": "disabled"}}


def test_thinking_anthropic_budget_adapts_to_max_tokens() -> None:
    """max_tokens above the floor but below the default budget: the budget
    adapts to half the window instead of dropping thinking entirely."""
    out = build_thinking_kwargs(
        "claude-sonnet-4", "anthropic", enable_thinking=True, max_tokens=4096
    )
    assert out["thinking"] == {"type": "enabled", "budget_tokens": 2048}


def test_thinking_openai_oseries_enables_reasoning_effort() -> None:
    for model in ("o1-mini", "o3-mini", "o4-something"):
        out = build_thinking_kwargs(model, "openai", enable_thinking=True)
        assert out == {"reasoning_effort": "high"}, model


def test_thinking_openai_non_oseries_silently_degrades() -> None:
    out = build_thinking_kwargs("gpt-4o", "openai", enable_thinking=True)
    assert out == {}


def test_thinking_qwen_enables_via_enable_thinking() -> None:
    """Qwen(DashScope/vLLM 兼容端点)用 enable_thinking 布尔开关——
    此前 openai 路径只认 o 系前缀,Qwen 思考开关根本没传给模型。"""
    out = build_thinking_kwargs("qwen3.8-vl-plus", "openai", enable_thinking=True)
    assert out == {"extra_body": {"enable_thinking": True}}
    # 关闭:开源 Qwen3 默认开思考,必须显式传 False
    out_off = build_thinking_kwargs("qwen3.8-vl-plus", "openai", enable_thinking=False)
    assert out_off == {"extra_body": {"enable_thinking": False}}


def test_thinking_glm_enables_via_thinking_object() -> None:
    """GLM 开启:与禁用对称的 thinking 对象(此前开启分支返回空)。"""
    out = build_thinking_kwargs("glm-4.5", "openai", enable_thinking=True)
    assert out == {"extra_body": {"thinking": {"type": "enabled"}}}
    out_off = build_thinking_kwargs("glm-4.5", "openai", enable_thinking=False)
    assert out_off == {"extra_body": {"thinking": {"type": "disabled"}}}


def test_thinking_unknown_provider_silently_degrades() -> None:
    out = build_thinking_kwargs("some-model", "weird", enable_thinking=True)
    assert out == {}


# ---------------------------------------------------------------------------
# supports_thinking / detect_provider
# ---------------------------------------------------------------------------


def test_supports_thinking_matrix() -> None:
    assert supports_thinking("claude-x", "anthropic") is True
    assert supports_thinking("o3-mini", "openai") is True
    assert supports_thinking("gpt-4o", "openai") is False
    assert supports_thinking("qwen3.8-vl-plus", "openai") is True
    assert supports_thinking("glm-4.5", "openai") is True
    assert supports_thinking("x", "unknown") is False


def test_detect_provider() -> None:
    assert detect_provider("claude-sonnet-4") == "anthropic"
    assert detect_provider("gpt-4o") == "openai"


# ---------------------------------------------------------------------------
# apply_thinking_mode (runtime mutation)
# ---------------------------------------------------------------------------


def test_apply_thinking_mode_anthropic_sets_attribute() -> None:
    # The client must already expose the attribute (guard: hasattr).
    llm = SimpleNamespace(thinking=None)
    apply_thinking_mode(llm, enable_thinking=True, model_name="claude-sonnet-4")
    assert llm.thinking == {"type": "enabled", "budget_tokens": 5000}


def test_apply_thinking_mode_openai_oseries_sets_attribute() -> None:
    llm = SimpleNamespace(reasoning_effort=None)
    apply_thinking_mode(llm, enable_thinking=True, model_name="o3-mini")
    assert llm.reasoning_effort == "high"


def test_apply_thinking_mode_unsupported_model_noop() -> None:
    """Non-reasoning models are left untouched (no attribute added)."""
    llm = SimpleNamespace()
    out = apply_thinking_mode(llm, enable_thinking=True, model_name="gpt-4o")
    assert out is llm
    assert not hasattr(llm, "reasoning_effort")
    assert not hasattr(llm, "thinking")


def test_apply_thinking_mode_disabled_returns_unchanged() -> None:
    llm = SimpleNamespace()
    out = apply_thinking_mode(llm, enable_thinking=False, model_name="claude-x")
    assert out is llm
