"""LLM 语义摘要:后台压缩任务 + 历史渲染 + 按ID回填。

核心原则:
- 非工具消息(用户/AI文本)原文喂给摘要LLM(不截断,保证语义完整)。
- 工具调用渲染成「已调用 X(参数概要)」。
- 工具结果不喂原始内容,只留 recall 引用(大结果不撑爆摘要请求)。
- 绝不生成机械摘要(避免累积退化)。
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any, Callable

import structlog

from agent_flow_harness.context_engineering.summary_cache import (
    SummaryCache,
    SummaryResult,
)

if TYPE_CHECKING:
    from langchain_core.language_models.chat_models import BaseChatModel
    from langchain_core.messages import BaseMessage

logger = structlog.get_logger(__name__)

_SUMMARY_PROMPT = """请将以下对话历史压缩为结构化摘要，用于后续对话的上下文延续。严格按以下六段输出（每段 1-3 句，没有则写"无"）：

1. 用户的核心意图与需求：
2. 关键决策与结论：
3. 重要错误与修复（含失败尝试）：
4. 进行中的任务与未完成项：
5. 当前工作状态与建议的下一步：
6. 关键文件/数据引用（recall 引用标记原样保留，便于重取）：

要求：优先保留任务连续性关键信息；丢弃闲聊与冗余细节；[此前摘要] 是更早历史的浓缩，其中信息须合并进对应段落继续保留（不得丢弃）；用中文。

对话历史：
{history}"""


def _get_msg_id(m: Any) -> str | None:
    """取消息的 id(没有则返回 None,用于区分"无id"和"id为空串")。"""
    mid = getattr(m, "id", None)
    return mid if mid else None


def _truncate_args_preview(args: dict[str, Any], limit: int = 100) -> str:
    """工具参数概要(截断防过长)。"""
    s = str(args)
    return s[:limit] + "..." if len(s) > limit else s


def render_history_for_summary(
    messages: "list[BaseMessage]",
    *,
    reference_formatter: "Callable[[str], str] | None" = None,
) -> str:
    """渲染历史给摘要 LLM:非工具原文,工具结果只留引用。

    - 摘要消息(id="llm_summary"/"summary",任何形态)或 SystemMessage:
      [此前摘要] {原文} —— 修复硬丢失 bug:旧摘要必须参与再摘要,不能静默跳过
    - HumanMessage: [用户] {原文}
    - AIMessage(有tool_calls): [助手] 已调用 {name}({参数概要})
    - AIMessage(有content): [助手] {原文}
    - ToolMessage: [工具结果] 已返回 + reference_formatter(tool_call_id) 的输出

    Args:
        reference_formatter: app 层注入的引用标记生成器(与 compress_tool_outputs
            共用同一个)。None 时只写"[工具结果] 已返回"(harness 不硬编码工具名)。
    """
    from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

    parts: list[str] = []
    for m in messages:
        mid = getattr(m, "id", "") or ""
        content = str(m.content)
        if mid in ("llm_summary", "summary") or isinstance(m, SystemMessage):
            if content.strip():
                parts.append(f"[此前摘要]\n{content}")
        elif isinstance(m, HumanMessage):
            content = str(m.content)
            if content.strip():
                parts.append(f"[用户] {content}")
        elif isinstance(m, AIMessage):
            # 先渲染工具调用
            for tc in getattr(m, "tool_calls", None) or []:
                name = tc.get("name", "") if isinstance(tc, dict) else getattr(tc, "name", "")
                args = tc.get("args", {}) if isinstance(tc, dict) else getattr(tc, "args", {})
                parts.append(f"[助手] 已调用 {name}({_truncate_args_preview(args)})")
            # 再渲染文本内容(原文,不截断)
            content = str(m.content)
            if content.strip():
                parts.append(f"[助手] {content}")
        elif isinstance(m, ToolMessage):
            tcid = m.tool_call_id or ""
            ref = reference_formatter(tcid) if reference_formatter else ""
            parts.append(f"[工具结果] 已返回{ref}")
    return "\n".join(parts)


async def compress_history_with_llm(
    llm: "BaseChatModel",
    outer_messages: "list[BaseMessage]",
    session_id: str,
    cache: SummaryCache,
    *,
    reference_formatter: "Callable[[str], str] | None" = None,
    timeout: float = 120.0,
) -> None:
    """后台任务:对 5轮外历史调 LLM 生成语义摘要,存入缓存。

    完成后下一轮 compress_node 会自动取用(按ID回填)。
    """
    import asyncio

    from langchain_core.messages import HumanMessage

    # covered_ids 只收集有 id 的消息(无 id 的 None 不入集合,避免误匹配)。
    covered_ids = [mid for m in outer_messages if (mid := _get_msg_id(m))]
    history_text = render_history_for_summary(
        outer_messages, reference_formatter=reference_formatter,
    )
    prompt = _SUMMARY_PROMPT.format(history=history_text)

    try:
        resp = await asyncio.wait_for(
            llm.ainvoke([HumanMessage(content=prompt)]), timeout=timeout,
        )
        summary_text = str(resp.content).strip()
        if not summary_text:
            logger.warning("compress_background_empty", session_id=session_id)
            return
        cache.set(session_id, SummaryResult(
            summary_text=summary_text,
            covered_ids=covered_ids,
        ))
        logger.info(
            "compress_background_done",
            session_id=session_id,
            covered_count=len(covered_ids),
        )
    except TimeoutError:
        logger.warning("compress_background_timeout", session_id=session_id, timeout=timeout)
    except Exception:
        logger.exception("compress_background_failed", session_id=session_id)
    finally:
        cache.clear_running(session_id)


def apply_cached_summary(
    messages: "list[BaseMessage]", result: SummaryResult,
) -> "tuple[list[BaseMessage], bool]":
    """按消息 ID 精确回填:用摘要替换它覆盖的消息,其余保留。

    摘要是 **HumanMessage**（Claude Code 同款,B.3-3）:
    - 天然属于历史区,下次压缩自然重吸收,回卷特判可删
    - 不受 _system_messages_first 重排影响
    - 配框架文本防归因混淆（模型不会当成用户发言）
    返回 (回填后的列表, 是否真的插入了摘要)。
    covered_ids 里的 None/空串已被过滤(在 compress_history_with_llm 里),
    所以无 id 的消息不会被误匹配。
    """
    from langchain_core.messages import HumanMessage

    covered = set(result.covered_ids)
    rebuilt: list[BaseMessage] = []
    inserted = False
    for m in messages:
        mid = _get_msg_id(m)
        if mid is not None and mid in covered:
            if not inserted:
                rebuilt.append(HumanMessage(
                    content=f"[系统生成的此前对话摘要，非用户发言]\n{result.summary_text}",
                    id="llm_summary",
                ))
                inserted = True
            # 被覆盖的消息不 append(用摘要替代了)
        else:
            rebuilt.append(m)
    return rebuilt, inserted


__all__ = [
    "apply_cached_summary",
    "compress_history_with_llm",
    "render_history_for_summary",
]
