"""SlidingWindowStrategy — 滑动窗口：保留 system + 最近 window_size 条。

中间消息丢弃，经 ensure_tool_pairing 保证不切碎 tool_call/result 配对。
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from agent_flow_harness.context_engineering.base import ContextStrategy
from agent_flow_harness.context_engineering.pairing import ensure_tool_pairing
from agent_flow_harness.context_engineering.split import split_system_history
from agent_flow_harness.context_engineering.token_estimator import count_tokens

if TYPE_CHECKING:
    from langchain_core.messages import BaseMessage


class SlidingWindowStrategy(ContextStrategy):
    """滑动窗口策略：保留 system messages + 最近 window_size 条。"""

    def __init__(self, window_size: int = 20) -> None:
        self._window = window_size

    @property
    def name(self) -> str:
        return "sliding_window"

    async def select(
        self, messages: "list[BaseMessage]", *, max_tokens: int
    ) -> "list[BaseMessage]":
        system_msgs, other = split_system_history(messages)

        # 不超限且条数不多时直接返回（只做配对清理）
        if count_tokens(system_msgs + other) <= max_tokens and len(other) <= self._window:
            return ensure_tool_pairing(messages)

        # 保留最近 window_size 条
        recent = other[-self._window:] if len(other) > self._window else other
        result = system_msgs + recent
        return ensure_tool_pairing(result)


__all__ = ["SlidingWindowStrategy"]
