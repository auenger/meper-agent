"""UsageMiddleware token tracking tests."""
from __future__ import annotations

import pytest
from langchain_core.messages import AIMessage

from agent_flow_harness.middleware import UsageMiddleware, resolve_middleware


# ---------------------------------------------------------------------------
# UsageMiddleware 单元测试
# ---------------------------------------------------------------------------


class _FakeLLM:
    """带 response_metadata(token usage) 的假 LLM。"""

    def __init__(self, response: AIMessage):
        self._response = response

    @property
    def model_name(self):
        return "fake"

    def bind_tools(self, _tools):
        return self

    async def ainvoke(self, messages, _config=None):
        return self._response


def _ai_with_openai_usage(content="ok", prompt=100, completion=20):
    """构造 OpenAI 格式 token usage 的 AIMessage。"""
    return AIMessage(
        content=content,
        response_metadata={
            "model_name": "gpt-4o",
            "token_usage": {
                "prompt_tokens": prompt,
                "completion_tokens": completion,
                "total_tokens": prompt + completion,
            },
        },
    )


def _ai_with_anthropic_usage(content="ok", input_t=200, output_t=30):
    """构造 Anthropic 格式 token usage 的 AIMessage。"""
    return AIMessage(
        content=content,
        response_metadata={
            "usage": {"input_tokens": input_t, "output_tokens": output_t},
        },
    )


@pytest.mark.asyncio
async def test_openai_usage_extracted():
    mw = UsageMiddleware()
    resp = _ai_with_openai_usage(prompt=100, completion=20)
    await mw.before_llm({})
    await mw.after_llm({}, resp)
    s = mw.summary
    assert s["input_tokens"] == 100
    assert s["output_tokens"] == 20
    assert s["total_tokens"] == 120
    assert s["llm_calls"] == 1


@pytest.mark.asyncio
async def test_anthropic_usage_extracted():
    mw = UsageMiddleware()
    resp = _ai_with_anthropic_usage(input_t=200, output_t=30)
    await mw.before_llm({})
    await mw.after_llm({}, resp)
    s = mw.summary
    assert s["input_tokens"] == 200
    assert s["output_tokens"] == 30
    assert s["total_tokens"] == 230


@pytest.mark.asyncio
async def test_accumulates_across_calls():
    mw = UsageMiddleware()
    for _ in range(3):
        await mw.before_llm({})
        await mw.after_llm({}, _ai_with_openai_usage(prompt=50, completion=10))
    s = mw.summary
    assert s["total_tokens"] == 180  # 3 * 60
    assert s["llm_calls"] == 3


@pytest.mark.asyncio
async def test_no_usage_metadata():
    """无 usage metadata 时不崩溃，token 为 0。"""
    mw = UsageMiddleware()
    resp = AIMessage(content="ok")  # 无 response_metadata
    await mw.before_llm({})
    await mw.after_llm({}, resp)
    assert mw.summary["total_tokens"] == 0
    assert mw.summary["llm_calls"] == 1


@pytest.mark.asyncio
async def test_writes_total_tokens_to_state():
    mw = UsageMiddleware()
    state: dict = {}
    await mw.before_llm(state)
    await mw.after_llm(state, _ai_with_openai_usage(prompt=100, completion=20))
    assert state["total_tokens"] == 120
    assert state["step_tokens"] == 120


@pytest.mark.asyncio
async def test_seeds_from_state_total_tokens():
    """A pre-existing state["total_tokens"] (carried over from a prior
    request in the same session) must be adopted as the baseline on the
    first after_llm, so the middleware does not overwrite it with 0.
    """
    mw = UsageMiddleware()
    state: dict = {"total_tokens": 5000}
    await mw.before_llm(state)
    await mw.after_llm(state, _ai_with_openai_usage(prompt=100, completion=20))
    # 5000 baseline + 120 this step
    assert state["total_tokens"] == 5120
    assert mw.summary["total_tokens"] == 5120


@pytest.mark.asyncio
async def test_seeds_only_once_then_accumulates():
    """The baseline is adopted once; subsequent calls keep accumulating on
    top of it rather than re-seeding.
    """
    mw = UsageMiddleware()
    state: dict = {"total_tokens": 5000}
    await mw.before_llm(state)
    await mw.after_llm(state, _ai_with_openai_usage(prompt=100, completion=20))  # +120
    state["total_tokens"] = 5120  # as the runner would merge
    await mw.before_llm(state)
    await mw.after_llm(state, _ai_with_openai_usage(prompt=50, completion=10))  # +60
    assert state["total_tokens"] == 5180  # 5000 + 120 + 60, not re-seeded
    assert mw.summary["total_tokens"] == 5180


@pytest.mark.asyncio
async def test_tool_duration_tracked():
    mw = UsageMiddleware()
    await mw.before_tool({}, {"id": "tc1", "name": "bash"})
    await mw.after_tool({}, {"id": "tc1", "name": "bash"}, "result")
    s = mw.summary
    assert s["tool_calls"] == 1
    assert s["tool_duration"] >= 0.0


@pytest.mark.asyncio
async def test_llm_duration_tracked():
    mw = UsageMiddleware()
    await mw.before_llm({})
    await mw.after_llm({}, _ai_with_openai_usage())
    assert mw.summary["llm_duration"] >= 0.0


def test_reset():
    mw = UsageMiddleware()
    mw.metrics["total_tokens"] = 999
    mw.reset()
    assert mw.summary["total_tokens"] == 0


def test_name_and_order():
    mw = UsageMiddleware()
    assert mw.name == "usage"
    assert mw.order == 50


def test_resolve_by_name():
    """resolve_middleware 能按 'usage' 名实例化。"""
    mws = resolve_middleware([{"name": "usage"}])
    assert len(mws) == 1
    assert isinstance(mws[0], UsageMiddleware)
