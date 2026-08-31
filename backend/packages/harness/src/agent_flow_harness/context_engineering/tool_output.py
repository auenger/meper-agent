"""工具输出内容压缩 — 把过大的 ToolMessage.content 缩短,而不删除消息。

核心安全约束(与 ``pairing`` / ``split`` 并列):

    tool_call 与 tool_result 是配对的原子单元。压缩上下文时绝不能切断
    配对,否则模型会收到孤儿 tool_call / 孤儿 tool_result → API 400 报错
    ("tool_use ids were provided that do not have a tool_use block")。

因此本模块做的是 **context editing**(Claude 官方推荐):对仍在保留窗口里
的工具结果,**缩短其 content** 而非删除整条消息 —— 既降低 token 占用,又
保持配对完整、tool_call_id 不变。

另两种压缩手段各管一段,互不重叠:

* ``compress_messages`` 的滑动窗口摘要 → 丢弃整段旧历史(配对完整地丢);
* ``ensure_tool_pairing``         → 裁剪后兜底清理孤儿;
* ``compress_tool_outputs``(本模块)→ 缩短保留段里的大工具输出内容。
"""
from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any, Callable

import structlog

if TYPE_CHECKING:
    from langchain_core.messages import BaseMessage

logger = structlog.get_logger(__name__)

# 引用标记生成器:接收 tool_call_id,返回追加在被截断结果末尾的提示文本。
# 由应用层注入(因为"用什么工具回溯"是应用层决策,harness 不应硬编码工具名)。
# None 表示压缩后不加任何标记(纯截断)。
ReferenceFormatter = Callable[[str], str]

# 单条 ToolMessage.content 超过此字符数才截断成极简提示。
_DEFAULT_MAX_TOOL_OUTPUT = 1500


def summarize_tool_content(content: str, *, max_output: int = _DEFAULT_MAX_TOOL_OUTPUT) -> str:
    """把一条工具结果压缩成极简提示(有回溯机制时无需保留残缺内容)。

    截断后只留一行提示,告知 AI "这里有个工具结果,大概是 N 条/什么类型"。
    具体回溯方式(用什么工具查回原文)由应用层通过 reference_formatter 注入,
    harness 不假设回溯机制。

    不保留残缺内容 —— 1500 字符的半截 JSON / 截断文本语义不完整,反而误导。
    有回溯能力时,原文能取回,所以这里越短越好,极致省 token。

    内容未超限时原样返回(不截断)。
    """
    if len(content) <= max_output:
        return content

    # 尝试从 JSON 提取"返回了几条"的线索,让 AI 知道大概规模。
    hint = "工具结果已压缩"
    try:
        data = json.loads(content)
        if isinstance(data, list):
            hint = f"工具返回 {len(data)} 条结果(已压缩)"
        elif isinstance(data, dict):
            # 常见结构:{data: {rows: [...]}} 或 {rows: [...]}
            rows = data.get("rows") or (data.get("data") or {}).get("rows")
            if isinstance(rows, list):
                hint = f"工具返回 {len(rows)} 条结果(已压缩)"
            else:
                hint = "工具返回结构化数据(已压缩)"
    except (json.JSONDecodeError, ValueError):
        # 非纯 JSON:看行数给个线索
        line_count = content.count("\n") + 1
        if line_count > 1:
            hint = f"工具返回多行输出({line_count} 行,已压缩)"
    return hint


def find_unconsumed_tool_call_ids(messages: "list[BaseMessage]") -> set[str]:
    """返回「尚未被 AI 消费」的 tool_call_id 集合。

    一个 ToolMessage 是否「已被消费」:它的后面(在列表中)是否还存在一个
    AIMessage —— 只要 AI 又发过言,就说明它已经看过该工具结果并据此行动了。
    没有后续 AIMessage 的 ToolMessage,属于「最新回合 / 正在进行中」的工具
    结果,绝对不能压缩(AI 还没基于它生成回复)。
    """
    from langchain_core.messages import AIMessage, ToolMessage

    # 第一遍:记录每个 ToolMessage 的位置 → tool_call_id
    tool_positions: list[tuple[int, str]] = []
    for i, m in enumerate(messages):
        if isinstance(m, ToolMessage):
            tool_positions.append((i, m.tool_call_id or ""))

    if not tool_positions:
        return set()

    # 最后一个 AIMessage 的位置(它的 AI 消费了之前所有的工具结果)
    last_ai = -1
    for i, m in enumerate(messages):
        if isinstance(m, AIMessage):
            last_ai = i

    # 未被消费:位置在最后一个 AIMessage 之后(或根本没有 AIMessage)
    return {tcid for pos, tcid in tool_positions if pos > last_ai}


def compress_tool_outputs(
    messages: "list[BaseMessage]",
    *,
    max_output: int = _DEFAULT_MAX_TOOL_OUTPUT,
    reference_formatter: ReferenceFormatter | None = None,
    unconsumed_ids: set[str] | None = None,
) -> "list[BaseMessage]":
    """对消息列表里过大的 ToolMessage.content 做内容压缩。

    - 不改变消息条数、顺序。
    - 不动 tool_call_id、不动 AIMessage 的 tool_calls(配对另一半)。
    - 只缩短超限的 ToolMessage.content。
    - **永不截断未被 AI 消费的最新工具结果**(它们是 AI 即将基于其生成
      回复的输入,截断会导致 AI 看不到完整工具结果)。

    Args:
        reference_formatter: 可选的引用标记生成器。传入时,被截断的工具结果
            末尾会追加它返回的文本(通常提示如何取回完整原文)。这是应用层
            决策——"用什么工具/机制回溯"由应用层定义,harness 不硬编码工具名。
            ``None`` 时只纯截断,不加任何标记。
        unconsumed_ids: 由调用方在**完整** messages 上算好的未消费 tool_call_id
            集合。切片后调用时必须传入(否则 find_unconsumed_tool_call_ids 丢失
            全局上下文会误判)。None 时本函数内部计算(仅对完整列表正确)。

    未超限的消息保持原对象引用(避免无谓复制)。
    """
    from langchain_core.messages import ToolMessage

    if unconsumed_ids is None:
        unconsumed_ids = find_unconsumed_tool_call_ids(messages)

    changed = False
    result: "list[BaseMessage]" = []
    trimmed_log: list[dict[str, Any]] = []
    skipped_unconsumed = 0
    for m in messages:
        if isinstance(m, ToolMessage):
            tcid = m.tool_call_id or ""
            if tcid in unconsumed_ids:
                # 未被 AI 消费的最新工具结果:绝对保护,不截断。
                skipped_unconsumed += 1
                result.append(m)
                continue
            if isinstance(m.content, list):
                # 多模态 content(view_image 的 [IMAGE 标记]+image 块):不走
                # str() 截断——那会把 base64 全量转字符串并毁掉块结构。
                # 其中 image 块的降级由 stale-image downgrade 流程统一负责
                # (标记块很短,无需文本压缩),这里原样保留。
                result.append(m)
                continue
            original = str(m.content)
            shortened = summarize_tool_content(original, max_output=max_output)
            if shortened != original:
                changed = True
                content = shortened
                if reference_formatter is not None:
                    content += reference_formatter(tcid)
                result.append(
                    ToolMessage(content=content, tool_call_id=tcid)
                )
                trimmed_log.append({
                    "tool_name": getattr(m, "name", "") or "",
                    "tool_call_id": tcid,
                    "chars_before": len(original),
                    "chars_after": len(content),
                })
                continue
        result.append(m)

    if skipped_unconsumed:
        logger.info(
            "tool_outputs_protected",
            count=skipped_unconsumed,
            tool_call_ids=sorted(unconsumed_ids),
        )
    if trimmed_log:
        logger.info(
            "tool_outputs_compressed",
            count=len(trimmed_log),
            items=trimmed_log,
        )

    return result if changed else messages


__all__ = [
    "compress_tool_outputs",
    "find_unconsumed_tool_call_ids",
    "summarize_tool_content",
]
