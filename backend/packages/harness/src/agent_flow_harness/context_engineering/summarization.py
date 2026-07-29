"""SummarizationStrategy — 用 LLM 把早期 messages 智能总结为一条 SystemMessage。

比 v0.1 的机械拼接摘要（_build_summary）保真度更高：LLM 理解语义，
保留关键决策/用户意图/错误信息。
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from agent_flow_harness.context_engineering.base import ContextStrategy
from agent_flow_harness.context_engineering.pairing import ensure_tool_pairing
from agent_flow_harness.context_engineering.token_estimator import count_tokens

if TYPE_CHECKING:
    from langchain_core.language_models.chat_models import BaseChatModel
    from langchain_core.messages import BaseMessage

_SUMMARY_PROMPT = """请将以下对话历史压缩为简洁摘要，保留：
1. 用户的核心意图和需求
2. 关键决策和结论
3. 重要的错误信息或失败尝试
丢弃闲聊和冗余细节。用中文，不超过 300 字。

对话历史：
{history}"""


class SummarizationStrategy(ContextStrategy):
    """LLM 智能摘要策略：把早期 messages 总结为一条 SystemMessage。"""

    def __init__(self, llm: "BaseChatModel", summary_max_tokens: int = 500) -> None:
        self._llm = llm
        self._summary_max = summary_max_tokens

    @property
    def name(self) -> str:
        return "summarization"

    async def select(
        self,
        messages: "list[BaseMessage]",
        *,
        max_tokens: int,
        keep_recent: int = 10,
    ) -> "list[BaseMessage]":
        from langchain_core.messages import HumanMessage, SystemMessage

        from agent_flow_harness.context_engineering.split import split_system_history

        # System prompt is the agent's fixed contract — never summarise it.
        # Split it out so only the compressible history is eligible, and so
        # the result keeps all SystemMessages contiguous at the front (else
        # langchain-anthropic rejects the list with
        # "Received multiple non-consecutive system messages.").
        system_msgs, history = split_system_history(messages)

        # 不超限或不值得总结时直接返回（只做配对清理）
        if count_tokens(messages) <= max_tokens or len(history) <= keep_recent:
            return ensure_tool_pairing(list(messages))

        # 分割：前面要总结的 + 最近保留的（均不含 system）
        to_summarize = history[:-keep_recent] if keep_recent > 0 else history
        recent = history[-keep_recent:] if keep_recent > 0 else []

        # 构建摘要 prompt（固定 prompt，防注入）
        history_text = "\n".join(
            f"[{self._role_label(m)}] {str(m.content)[:200]}" for m in to_summarize
        )
        prompt = HumanMessage(content=_SUMMARY_PROMPT.format(history=history_text))
        summary_resp = await self._llm.ainvoke([prompt])
        summary_text = str(summary_resp.content)[: self._summary_max * 4]

        # 固定 id 让 reducer 幂等更新（多轮压缩替换而非重复 append）。
        summary_msg = SystemMessage(content=f"[对话历史摘要]\n{summary_text}", id="summary")
        # 布局：原始 system 在最前，摘要 system 紧随（连续），再接 recent。
        return ensure_tool_pairing([*system_msgs, summary_msg, *recent])

    @staticmethod
    def _role_label(m: "BaseMessage") -> str:
        from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

        if isinstance(m, HumanMessage):
            return "用户"
        if isinstance(m, AIMessage):
            return "助手"
        if isinstance(m, ToolMessage):
            return "工具"
        if isinstance(m, SystemMessage):
            return "系统"
        return "未知"


__all__ = ["SummarizationStrategy"]
