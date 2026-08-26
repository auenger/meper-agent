"""Phase timing — 开发专用分段计时（DEBUG 门控，线上零开销）。

用法：
    phases = phases_if_debug()          # DEBUG=false → None（关闭信号）
    with timed_phase(phases, "mcp"):    # None 时直通，不计时
        await load_mcp_tools()
    # phases: {"mcp": 123}  → 随 agent_phase_timing 日志一次性输出
"""
from __future__ import annotations

import time
from collections.abc import Iterator
from contextlib import contextmanager

from app.core.config import settings


def phases_if_debug() -> dict[str, int] | None:
    """DEBUG 开启时返回空 dict（调用方逐段累加），否则 None 表示不收集。"""
    return {} if settings.DEBUG else None


@contextmanager
def timed_phase(phases: dict[str, int] | None, name: str) -> Iterator[None]:
    """计时一个阶段并累加毫秒到 phases[name]；phases 为 None 时直通零开销。"""
    if phases is None:
        yield
        return
    start = time.perf_counter()
    try:
        yield
    finally:
        phases[name] = phases.get(name, 0) + int((time.perf_counter() - start) * 1000)
