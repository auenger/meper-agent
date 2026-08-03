"""消息拆分工具 — 把 SystemMessage 与历史消息分离。

核心安全约束（对所有上下文压缩策略统一适用）：

    system prompt 是 agent 的固定契约，不参与压缩；压缩只动 history。
    所有 SystemMessage 必须连续保留在消息列表最前，否则会被
    langchain-anthropic 校验拒绝（"Received multiple non-consecutive
    system messages."）。

本模块提供单一事实源 ``split_system_history``，供 compress_messages /
SummarizationStrategy / SlidingWindowStrategy 等所有压缩实现复用，
避免每个实现各自重写一遍 system 抽取逻辑导致行为漂移。
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from langchain_core.messages import BaseMessage


def split_system_history(
    messages: "list[BaseMessage]",
) -> "tuple[list[BaseMessage], list[BaseMessage]]":
    """把消息列表拆成 ``(system_msgs, history)``。

    * ``system_msgs``：原列表中**所有**位置的 SystemMessage，保持原相对顺序。
    * ``history``：其余消息（user / assistant / tool），保持原相对顺序。

    为什么收集「所有」位置而非仅开头连续段：中间件、压缩产物等都可能在
    列表中段插入 SystemMessage。统一抽取后让调用方把它们前置重排，才能
    保证最终发给模型的序列 system 永远连续。语义与
    :class:`SlidingWindowStrategy` 既有实现一致。
    """
    from langchain_core.messages import SystemMessage

    system_msgs: list[BaseMessage] = [m for m in messages if isinstance(m, SystemMessage)]
    history: list[BaseMessage] = [m for m in messages if not isinstance(m, SystemMessage)]
    return system_msgs, history


__all__ = ["split_system_history"]
