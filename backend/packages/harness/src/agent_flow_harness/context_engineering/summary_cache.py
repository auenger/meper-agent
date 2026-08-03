"""后台 LLM 压缩结果缓存(per session)。

compress_node 每轮检查:有没有上一轮后台压缩好的 LLM 摘要可用。
后台任务完成后写入:session_id → SummaryResult(摘要文本 + 覆盖的消息ID)。

竞态安全:摘要记录它覆盖的消息 ID 列表(covered_ids)。回填时只替换
这些 ID 对应的消息,压缩期间新增的消息保留(下一轮后台压缩重新覆盖)。
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class SummaryResult:
    """一次后台 LLM 压缩的结果。"""

    summary_text: str
    covered_ids: list[str] = field(default_factory=list)


class SummaryCache:
    """Per-session 的后台压缩结果缓存 + 运行状态跟踪。

    单实例即可(模块级),因为按 session_id 隔离。
    """

    def __init__(self) -> None:
        self._store: dict[str, SummaryResult] = {}
        self._running: set[str] = set()

    def get(self, session_id: str) -> SummaryResult | None:
        """读取并取出(读后清,摘要只用一次)。"""
        return self._store.get(session_id)

    def set(self, session_id: str, result: SummaryResult) -> None:
        """后台任务完成后写入结果。"""
        self._store[session_id] = result

    def clear(self, session_id: str) -> None:
        """用过后清除(摘要只用一次)。"""
        self._store.pop(session_id, None)

    def is_running(self, session_id: str) -> bool:
        """该 session 是否有后台压缩任务正在跑(防重复触发)。"""
        return session_id in self._running

    def mark_running(self, session_id: str) -> None:
        """标记开始跑(触发后台任务前调用)。"""
        self._running.add(session_id)

    def clear_running(self, session_id: str) -> None:
        """后台任务完成后清除标记。"""
        self._running.discard(session_id)


# 模块级单例(进程内,重启即丢;持久化由应用层兜底)。
summary_cache = SummaryCache()


__all__ = ["SummaryCache", "SummaryResult", "summary_cache"]
