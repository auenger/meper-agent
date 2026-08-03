"""Context Engineering 模块 — 可插拔上下文压缩策略 (v0.2-5)。

把 v0.1 硬编码的 compress_messages 重构为可插拔 ContextStrategy 协议：
- SlidingWindowStrategy: 滑动窗口
- SummarizationStrategy: LLM 智能摘要
- HybridStrategy: 默认，token 超阈值时总结+滑动

react_node 在 LLM 调用前调 strategy.select()（config 注入，可选，向后兼容）。
"""
from agent_flow_harness.context_engineering.base import ContextStrategy
from agent_flow_harness.context_engineering.hybrid import HybridStrategy
from agent_flow_harness.context_engineering.sliding_window import SlidingWindowStrategy
from agent_flow_harness.context_engineering.split import split_system_history
from agent_flow_harness.context_engineering.summarization import SummarizationStrategy
from agent_flow_harness.context_engineering.token_estimator import count_tokens
from agent_flow_harness.context_engineering.tool_output import compress_tool_outputs
from agent_flow_harness.context_engineering.turns import (
    DEFAULT_PROTECTED_TURNS,
    split_by_turns,
)

__all__ = [
    "ContextStrategy",
    "DEFAULT_PROTECTED_TURNS",
    "HybridStrategy",
    "SlidingWindowStrategy",
    "SummarizationStrategy",
    "compress_tool_outputs",
    "count_tokens",
    "split_by_turns",
    "split_system_history",
]
