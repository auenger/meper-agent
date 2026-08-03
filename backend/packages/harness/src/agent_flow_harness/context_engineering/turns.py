"""按对话轮次分区 — 把历史切分为"最近 N 轮(保护)"和"更早(可压缩)"。

核心规则(与 compress_node 配合):

    一个 HumanMessage 开启一轮(它是 turn separator,见
    messages_to_app_events 的既有语义)。倒数第 N 个 HumanMessage(含)
    之后的所有消息属于"最近 N 轮"(保护窗口);之前的属于"5轮之外"
    (可无条件压缩)。

5轮之外的工具结果一律压缩(有 recall 兜底,需要时可取回原文)。
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from langchain_core.messages import BaseMessage

DEFAULT_PROTECTED_TURNS = 5
"""最近多少轮(以 HumanMessage 计)受保护:5轮内的内容不被无条件压缩。"""


def split_by_turns(
    history: "list[BaseMessage]",
    n_turns: int = DEFAULT_PROTECTED_TURNS,
) -> "tuple[list[BaseMessage], list[BaseMessage]]":
    """把 history(不含 system)按轮次切分为 ``(outer, recent)``。

    * ``recent``:倒数第 ``n_turns`` 个 HumanMessage(含)及之后的所有消息
      —— 最近 N 轮,受保护窗口。
    * ``outer``:上述位置之前的所有消息 —— 5轮之外,可无条件压缩。

    边界:
    * HumanMessage 不足 N 个 → 全部归入 recent(没有 5轮之外的内容)。
    * 开头非 HumanMessage(如直接 AIMessage 打头)→ 归入 outer。
    """
    from langchain_core.messages import HumanMessage

    human_idx = [i for i, m in enumerate(history) if isinstance(m, HumanMessage)]
    if len(human_idx) < n_turns:
        # 不足 N 轮,全部保护,没有可压缩的 outer。
        return [], list(history)

    cut = human_idx[-n_turns]  # 倒数第 N 个 HumanMessage 的位置(含)
    outer = history[:cut]
    recent = history[cut:]
    return outer, recent


def count_turns(history: "list[BaseMessage]") -> int:
    """数 history 里有几轮(HumanMessage 个数)。"""
    from langchain_core.messages import HumanMessage

    return sum(1 for m in history if isinstance(m, HumanMessage))


__all__ = ["DEFAULT_PROTECTED_TURNS", "count_turns", "split_by_turns"]
