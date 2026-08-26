"""Active streaming-run registry — powers mid-stream cancellation (stop).

SSE 流式对话的生产者是 ``AgentExecutionService.stream/resume`` 内部
fire-and-forget 的后台任务。stop 端点需要一个句柄才能取消它——本模块
维护进程内注册表：``request_id → ActiveRun``。

取消信号有两级（belt-and-suspenders）：

1. ``ActiveRun.task.cancel()`` —— 立即生效：CancelledError 打进当前
   await 点（token 流 / 工具执行），httpx 连接关闭、供应商停止生成。
2. Redis 标志 ``agent_run:cancel:{request_id}`` —— 配合 harness 的
   ``cancel_checker``（每轮 REACT 迭代边界检查）：若取消信号恰巧落在
   两个 await 之间（task.cancel 的竞态窗口），下一轮迭代边界仍有闸门。

当前单进程部署（单 uvicorn worker）下注册表即可命中；将来多副本时，
stop 请求把 Redis 标志换成 pub/sub 广播、各进程查本地注册表，本模块
接口不变。
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any

from loguru import logger

from app.db.redis import get_redis_client

_CANCEL_KEY_PREFIX = "agent_run:cancel"
_CANCEL_FLAG_TTL = 3600  # 1h — stale flags self-expire


@dataclass
class ActiveRun:
    """One in-flight streaming agent execution (stream or resume)."""

    task: asyncio.Task
    request_id: str
    agent_id: str
    user_id: str
    session_id: str
    started_at: float = field(default_factory=time.time)


_ACTIVE_RUNS: dict[str, ActiveRun] = {}


def register_run(run: ActiveRun) -> None:
    """Register an active run. Re-registration of the same id is a no-op."""
    _ACTIVE_RUNS[run.request_id] = run


def unregister_run(request_id: str) -> None:
    """Remove a finished run from the registry."""
    _ACTIVE_RUNS.pop(request_id, None)


def find_run(request_id: str) -> ActiveRun | None:
    """Look up an active run by request_id."""
    run = _ACTIVE_RUNS.get(request_id)
    if run is not None and run.task.done():
        # Defensive: the task finished but its finally hasn't unregistered yet.
        _ACTIVE_RUNS.pop(request_id, None)
        return None
    return run


def find_latest_run(agent_id: str, user_id: str) -> ActiveRun | None:
    """Latest active run for (agent, user) — stop endpoint's default target."""
    candidates = [
        r for r in _ACTIVE_RUNS.values()
        if r.agent_id == agent_id and r.user_id == user_id and not r.task.done()
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda r: r.started_at)


def find_runs_by_session(session_id: str) -> list[ActiveRun]:
    """All still-active runs on a session.

    SSE stream/resume 共享同一 checkpointer thread，同 session 同时只能有
    一个写入者——新 run 启动前调用方用此取消残留的活跃 run，避免并发写
    同一 thread 导致 checkpoint 互相覆盖 / 消息乱序。
    """
    return [
        r for r in _ACTIVE_RUNS.values()
        if r.session_id == session_id and not r.task.done()
    ]


async def cancel_runs_by_session(session_id: str) -> None:
    """Cancel all still-active runs on a session (one writer per thread).

    stream/resume 启动新 run 前的并发防护：task.cancel() 立即打断当前
    await；Redis 取消标志兜底迭代边界竞态，多个标志用 gather 并发打，
    避免独立 Redis 往返串行阻塞新流启动。
    """
    stale_runs = find_runs_by_session(session_id)
    if not stale_runs:
        return
    for stale in stale_runs:
        stale.task.cancel()
    await asyncio.gather(*(set_cancel_flag(r.request_id) for r in stale_runs))


async def set_cancel_flag(request_id: str) -> None:
    """Set the Redis cancel flag (iteration-boundary gate, best-effort)."""
    try:
        redis = await get_redis_client()
        await redis.set(f"{_CANCEL_KEY_PREFIX}:{request_id}", "1", ex=_CANCEL_FLAG_TTL)
    except Exception:
        # task.cancel() is the primary mechanism; flag failure is non-fatal.
        logger.debug("cancel_flag_set_failed", request_id=request_id)


def make_cancel_checker(request_id: str) -> Any:
    """Build the harness ``cancel_checker`` for this run (queries Redis)."""

    async def _checker() -> bool:
        try:
            redis = await get_redis_client()
            return bool(await redis.exists(f"{_CANCEL_KEY_PREFIX}:{request_id}"))
        except Exception:
            return False

    return _checker
