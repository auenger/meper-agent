"""中断标注 — 新一轮开始时检测上一轮是否被中断，注入显式标记。

设计动机：mid-stream 取消/进程崩溃会让 thread 停在三种"未完成形态"之一。
硬丢弃（剥离孤儿）模型一无所知，可能误解上下文；本模块让模型**永远
被告知"这里发生了什么"**，而不是靠猜。文案用中性的"被中断"——用户取消
与进程崩溃在 thread 形状上不可区分，也不必区分。

三种中断形态与标注（仅当尾部是 HumanMessage，即新一轮开始时触发；
轮内迭代（尾部为 AIMessage/ToolMessage）绝不误触发）：

① 工具执行中被中断：尾部 AI(tool_calls) 无对应结果
   → 为每个未应答 tool_call 合成 ToolMessage("[执行被中断…]")
   → 结构变为完整配对，模型知道工具被取消而非"没结果"

② 工具已完成、总结回复被中断：尾部是 ToolMessage
   → 注入 AIMessage("（上一轮执行被中断：工具结果尚未汇报给用户）")
   → 防止模型把工具结果当作"已经汇报过了"

③ 未产生任何输出即被中断：尾部是上一轮的 HumanMessage
   → 注入 AIMessage("（上一轮回复被中断，未产生输出）")
   → 显式告知第一条消息被放弃，而非被静默合并

正常完成的轮次（尾部为带文本的 AIMessage）不受影响。

判据是 thread 状态本身的形状（原子落盘、无竞态）：取消时刻不做任何
判断，新一轮开始时状态已尘埃落定。
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from langchain_core.messages import BaseMessage

_TOOL_INTERRUPTED = "[执行被中断：此工具未完成，没有结果]"
_MARK_TOOLS_UNREPORTED = "（上一轮执行被中断：工具结果尚未汇报给用户）"
_MARK_NO_OUTPUT = "（上一轮回复被中断，未产生输出）"


def annotate_interruptions(messages: "list[BaseMessage]") -> "tuple[list[BaseMessage], dict[str, Any]]":
    """检测并标注上一轮的中断形态。返回 (messages, info)。

    info: {"changed": bool, "case": "tool_cancelled"|"tools_unreported"|"no_output"|""}
    只在新一轮开始（尾消息为 HumanMessage）时动作。
    """
    from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

    if not messages or not isinstance(messages[-1], HumanMessage):
        return messages, {"changed": False, "case": ""}

    prior = messages[:-1]
    if not prior:
        return messages, {"changed": False, "case": ""}

    last = prior[-1]

    # ① 工具执行中被中断：孤儿 tool_call → 合成"被取消"的 ToolMessage
    if isinstance(last, AIMessage):
        tool_calls = [
            tc for tc in (getattr(last, "tool_calls", []) or [])
            if isinstance(tc, dict) and tc.get("id")
        ]
        if tool_calls:
            cancelled = [
                ToolMessage(content=_TOOL_INTERRUPTED, tool_call_id=tc["id"])
                for tc in tool_calls
            ]
            return (
                [*prior, *cancelled, messages[-1]],
                {"changed": True, "case": "tool_cancelled"},
            )
        # 带文本无工具调用的 AIMessage → 上一轮正常完成，无需标注
        return messages, {"changed": False, "case": ""}

    # ② 工具已完成、总结回复被中断
    if isinstance(last, ToolMessage):
        marker = AIMessage(content=_MARK_TOOLS_UNREPORTED)
        return (
            [*prior, marker, messages[-1]],
            {"changed": True, "case": "tools_unreported"},
        )

    # ③ 未产生任何输出即被中断（尾部是上一轮的 HumanMessage）
    if isinstance(last, HumanMessage):
        marker = AIMessage(content=_MARK_NO_OUTPUT)
        return (
            [*prior, marker, messages[-1]],
            {"changed": True, "case": "no_output"},
        )

    # 其他（SystemMessage 等）→ 不动作
    return messages, {"changed": False, "case": ""}


__all__ = ["annotate_interruptions"]
