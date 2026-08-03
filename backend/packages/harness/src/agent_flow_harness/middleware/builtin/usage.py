"""UsageMiddleware — 统计 token 消耗 + LLM/tool 执行耗时。

从 LLM response.response_metadata 提取 token usage（兼容 OpenAI/Anthropic 格式），
累计到 state["total_tokens"]（供 TokenBudgetGuard 读取）+ 自身 metrics。
同时统计 LLM 调用次数 / 工具调用次数 / 各自耗时。

用法：
    # 声明式（config）
    AgentConfig(middleware=[{"name": "usage"}])

    # 实例直传（需持有 .summary 引用）
    usage = UsageMiddleware()
    create_agent(config, model=llm, middlewares=[usage])
    await agent.run("hi")
    print(usage.summary)
"""
from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from langchain_core.messages import BaseMessage

    from agent_flow_harness.state import AgentState


class UsageMiddleware:
    """统计 token 消耗 + 节点执行时间，累计到 state 和自身 metrics。"""

    name = "usage"
    order = 50  # 早执行，确保其他 middleware 也能读到 total_tokens

    def __init__(self) -> None:
        self.metrics: dict[str, Any] = {
            "total_tokens": 0,
            "input_tokens": 0,
            "output_tokens": 0,
            "llm_calls": 0,
            "tool_calls": 0,
            "llm_duration": 0.0,
            "tool_duration": 0.0,
        }
        self._llm_start: float | None = None
        self._tool_starts: dict[str, float] = {}
        # Whether self.metrics has been seeded from state["total_tokens"] yet.
        # On the first after_llm we adopt any pre-existing cumulative total
        # (e.g. carried over from a prior request in the same session) so the
        # middleware's running totals stay consistent with the guard's view.
        self._seeded = False

    async def before_llm(self, state: "AgentState") -> "AgentState":
        self._llm_start = time.monotonic()
        return state

    async def after_llm(self, state: "AgentState", response: "BaseMessage") -> "AgentState":
        # 提取 token usage,优先用 LangChain 标准的 usage_metadata 属性,
        # 其次从 response_metadata 兼容 OpenAI/Anthropic 格式
        usage = self._extract_usage_metadata(response)
        if usage.get("total", 0) == 0:
            meta = getattr(response, "response_metadata", None) or {}
            usage = self._extract_usage(meta)
        if usage.get("total", 0) == 0 and usage.get("input", 0) == 0:
            import structlog
            structlog.get_logger(__name__).debug(
                "usage_no_token_data",
                response_metadata_keys=list((getattr(response, "response_metadata", None) or {}).keys()),
            )

        # On first invocation, adopt the cumulative total already in state
        # (e.g. carried over from prior requests in the same session). Without
        # this, self.metrics["total_tokens"] restarts at 0 each request and
        # overwrites state["total_tokens"] below, defeating TokenBudgetGuard.
        if not self._seeded:
            carried = int(state.get("total_tokens", 0) or 0)
            if carried:
                self.metrics["total_tokens"] = carried
            self._seeded = True

        self.metrics["input_tokens"] += usage.get("input", 0)
        self.metrics["output_tokens"] += usage.get("output", 0)
        total = usage.get("total") or (usage.get("input", 0) + usage.get("output", 0))
        self.metrics["total_tokens"] += total
        self.metrics["llm_calls"] += 1

        if self._llm_start is not None:
            self.metrics["llm_duration"] += time.monotonic() - self._llm_start
            self._llm_start = None

        # 写入 state（供 TokenBudgetGuard 等读取）
        state["total_tokens"] = self.metrics["total_tokens"]
        state["step_tokens"] = total  # type: ignore[typeddict-unknown-key]
        return state

    async def before_tool(self, state: "AgentState", tool_call: dict[str, Any]) -> dict[str, Any]:
        tid = tool_call.get("id", "")
        self._tool_starts[tid] = time.monotonic()
        return tool_call

    async def after_tool(
        self, state: "AgentState", tool_call: dict[str, Any], result: str
    ) -> "AgentState":
        tid = tool_call.get("id", "")
        start = self._tool_starts.pop(tid, None)
        if start is not None:
            self.metrics["tool_duration"] += time.monotonic() - start
        self.metrics["tool_calls"] += 1
        return state

    @staticmethod
    def _extract_usage_metadata(response: "BaseMessage") -> dict[str, int]:
        """从 LangChain 标准的 usage_metadata 属性提取 token usage。

        ``usage_metadata`` 是 LangChain 统一的 token 用量属性(UsageMetadata TypedDict),
        所有 provider 的 ChatModel 在非流式 ainvoke 时都会填充它:
        ``{"input_tokens": N, "output_tokens": N, "total_tokens": N}``
        """
        um = getattr(response, "usage_metadata", None)
        if not isinstance(um, dict):
            return {"input": 0, "output": 0, "total": 0}
        return {
            "input": int(um.get("input_tokens", 0) or 0),
            "output": int(um.get("output_tokens", 0) or 0),
            "total": int(um.get("total_tokens", 0) or 0),
        }

    @staticmethod
    def _extract_usage(meta: dict[str, Any]) -> dict[str, int]:
        """从 response_metadata 提取 token usage，兼容多 provider 格式。

        - OpenAI:    meta["token_usage"]["prompt_tokens" / "completion_tokens"]
        - Anthropic: meta["usage"]["input_tokens" / "output_tokens"]
        - 其他:      尽力提取
        """
        raw: dict[str, Any] = {}
        # OpenAI 格式
        tu = meta.get("token_usage")
        if isinstance(tu, dict):
            raw = tu
        # Anthropic 格式
        elif isinstance(meta.get("usage"), dict):
            raw = meta["usage"]

        def _get(*keys: str) -> int:
            for k in keys:
                v = raw.get(k)
                if isinstance(v, (int, float)):
                    return int(v)
            return 0

        return {
            "input": _get("prompt_tokens", "input_tokens"),
            "output": _get("completion_tokens", "output_tokens"),
            "total": _get("total_tokens"),
        }

    @property
    def summary(self) -> dict[str, Any]:
        """返回当前累计的统计快照（拷贝，避免外部修改）。"""
        return dict(self.metrics)

    def reset(self) -> None:
        """重置统计（多次 run 复用同一实例时用）。"""
        self.metrics = {
            "total_tokens": 0,
            "input_tokens": 0,
            "output_tokens": 0,
            "llm_calls": 0,
            "tool_calls": 0,
            "llm_duration": 0.0,
            "tool_duration": 0.0,
        }
        self._llm_start = None
        self._tool_starts = {}
        self._seeded = False


__all__ = ["UsageMiddleware"]
