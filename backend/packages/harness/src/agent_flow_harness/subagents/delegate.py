"""delegate_to_subagent 工具 — 主 Agent 委派子任务的入口。

工具签名只暴露 subagent_name + task 给 LLM；所有运行时依赖从 ContextVar 拿。
内部：get_context → registry.get → resolve_tools(排除 delegate) → resolve_llm
→ build_subagent_state(全新隔离) → build_agent_graph(延迟构建) → ainvoke
→ extract_final_text。异常被 catch 转错误字符串返回（AC10 异常隔离）。

提取逻辑 extract_final_text 参照 deer-flow executor.py:674-698。
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

# 顶层 import 以便测试 monkeypatch（测试 patch agent_flow_harness.subagents.delegate.build_agent_graph）
from agent_flow_harness.graph import build_agent_graph, build_config

if TYPE_CHECKING:
    from langchain_core.messages import BaseMessage

logger = logging.getLogger(__name__)

_PLACEHOLDER = "No response generated."

_DELEGATE_AGENT_DOC = {"_id": "subagent", "name": "subagent"}


def extract_final_text(messages: "list[BaseMessage]") -> str:
    """从 messages 提取最后一条 AIMessage 的文本内容。

    处理两种 content 类型：
    - str → 直接返回
    - list → 拼接 block["text"]（dict）和 str 块
    - 都不是 → str(content)
    无 AIMessage → 返回兜底字符串。
    """
    last_ai: Any | None = None
    for msg in reversed(messages):
        # 用类型名字符串判断，避免循环 import AIMessage。
        if msg.__class__.__name__ == "AIMessage":
            last_ai = msg
            break
    if last_ai is None:
        return _PLACEHOLDER
    return _content_to_text(last_ai.content)


def _content_to_text(content: Any) -> str:
    """把 AIMessage.content（str / list / other）转成纯文本。"""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict):
                text = block.get("text")
                if isinstance(text, str):
                    parts.append(text)
        return "\n".join(parts) if parts else _PLACEHOLDER
    return str(content)


class _DelegateArgs(BaseModel):
    """delegate_to_subagent 的 LLM 可见参数。"""

    subagent_name: str = Field(..., description="要委派的子 Agent 名称")
    task: str = Field(..., description="委派给子 Agent 的任务描述")


async def _delegate_to_subagent(subagent_name: str, task: str) -> str:
    """委派子任务给子 Agent，返回其最终输出文本。

    依赖从 ContextVar 获取。异常被 catch 转错误字符串返回（AC10 异常隔离）。
    """
    from agent_flow_harness.subagents.context import get_subagent_context

    try:
        ctx = get_subagent_context()
        spec = ctx.registry.get(subagent_name)
        tools = ctx.resolve_tools(spec)               # AC7: 已排除 delegate
        llm = ctx.resolve_llm(spec)
        state = ctx.build_subagent_state(spec, task)  # AC8: 全新隔离 state
        # 延迟构建子 agent graph（AC5）。
        graph = build_agent_graph(_DELEGATE_AGENT_DOC)
        config = build_config(
            _DELEGATE_AGENT_DOC, llm, tools=tools, recursion_limit=spec.max_turns,
        )
        result_state = await graph.ainvoke(state, config=config)  # type: ignore[call-overload]
        final_messages = result_state.get("messages", [])
        return extract_final_text(final_messages)
    except Exception as exc:
        logger.warning("delegate_to_subagent failed: %s", exc, exc_info=True)
        return f"Error: {exc}"


delegate_to_subagent = StructuredTool.from_function(
    _delegate_to_subagent,
    name="delegate_to_subagent",
    description=(
        "委派子任务给一个专门的子 Agent 执行，返回子 Agent 的最终输出。"
        "当任务复杂、需要不同工具集或独立上下文时使用。"
    ),
    args_schema=_DelegateArgs,
    coroutine=_delegate_to_subagent,
)


__all__ = ["delegate_to_subagent", "extract_final_text"]
