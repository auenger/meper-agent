"""Agent execution service — business orchestration for invoke/stream/resume.

Encapsulates the session management, message persistence, prompt assembly,
and harness execution that was previously inlined in the API endpoints.
The API layer (agents.py) delegates here for all execution-related flows.
"""
from __future__ import annotations

import asyncio
import contextlib
import time
import uuid
from typing import Any

from langchain_core.messages import SystemMessage
from loguru import logger

from app.core.config import settings
from app.core.errors import NotFoundError, ValidationError
from app.engine.agent.builder import build_system_prompt
from app.schemas.execution import ExecutionRequest, ExecutionResponse, ResumeRequest
from app.services.agent_service import AgentService
from app.services.file_rendering import (
    render_attachments_block,
    render_files_by_ids,
    render_files_by_paths,
)
from app.services.message_converters import (
    extract_final_answer,
    messages_to_timeline_entries,
    safe_json,
)
from app.services.run_registry import (
    ActiveRun,
    cancel_runs_by_session,
    make_cancel_checker,
    register_run,
    unregister_run,
)
from app.services.session_service import MessageService, SessionService


def _now_ms() -> int:
    """Current epoch time in milliseconds (for execution-latency timing)."""
    return int(time.time() * 1000)


class AgentExecutionService:
    """Orchestrates agent execution: session, persistence, prompt, harness."""

    # ------------------------------------------------------------------
    # Invoke (synchronous)
    # ------------------------------------------------------------------

    @staticmethod
    async def invoke(
        agent_id: str,
        body: ExecutionRequest,
        user_id: str,
        *,
        external_call_chain: list[str] | None = None,
        user_token: str | None = None,
    ) -> ExecutionResponse:
        """Execute an agent synchronously and persist the result.

        Returns an ExecutionResponse with the agent's output text.
        """
        from app.engine.harness_integration import invoke as harness_invoke

        start_time_ms = _now_ms()  # latency 基准 = 方法入口（含 get_agent/prompt 构建，与 phase 恒等式对齐）
        exec_doc = await AgentService.get_agent(agent_id)
        if exec_doc is None:
            raise NotFoundError(code="AGENT_NOT_FOUND", message=f"Agent {agent_id} 不存在")

        session_id = await _resolve_session(agent_id, body, user_id)
        request_id = str(uuid.uuid4())
        call_chain = [*(external_call_chain or []), agent_id]

        # Build messages (system prompt + user input with file attachments)
        system_text = await _build_system_prompt_checked(exec_doc, user_id)
        user_content = await _build_user_content(body, user_id, session_id)
        initial_messages = _assemble_messages(system_text, user_content)

        # Legacy migration records
        legacy_records = await _load_legacy_records(session_id, body.input)

        # Carry over the session's cumulative token spend so TokenBudgetGuard
        # enforces the per-session budget across requests, not per-request.
        session_doc = await SessionService.get_session(session_id)
        session_total_tokens = int((session_doc or {}).get("total_tokens", 0) or 0)

        initial_state = _build_initial_state(
            agent_id, session_id, user_id, request_id, call_chain,
            external_call_chain, initial_messages,
            total_tokens=session_total_tokens,
        )

        prep_done_ms = _now_ms() - start_time_ms
        run_error: BaseException | None = None
        try:
            result = await harness_invoke(
                exec_doc, initial_state,
                enable_thinking=body.enable_thinking,
                legacy_records=legacy_records,
                user_token=user_token,
            )
        except Exception as exc:
            run_error = exc
            result = {}
            raise
        finally:
            # Unified execution log (all channels).
            # （invoke 的消息持久化在 execution log 之后，phase_timing 不含 persist）
            await _record_execution_log(
                user_id=user_id, agent_id=agent_id, session_id=session_id,
                request_id=request_id, start_time_ms=start_time_ms,
                token_usage=result.get("usage"), error=run_error,
                phase_timing=_compose_phase_timing(
                    result.get("timing"), result.get("usage"), prep_ms=prep_done_ms,
                ),
            )

        # Extract output + persist agent message
        output_text = extract_final_answer(result.get("messages", []))
        timeline = messages_to_timeline_entries(
            result.get("messages", []), enable_thinking=body.enable_thinking,
        )
        await MessageService.add_message(
            session_id=session_id, role="agent", timeline_entries=timeline,
            request_id=request_id,
        )

        return ExecutionResponse(
            output=output_text,
            execution_path=result.get("execution_path", "unknown"),
            request_id=request_id,
            agent_id=agent_id,
            session_id=session_id,
            step_count=result.get("step_count", 0),
        )

    # ------------------------------------------------------------------
    # Stream (SSE)
    # ------------------------------------------------------------------

    @staticmethod
    async def stream(
        agent_id: str,
        body: ExecutionRequest,
        user_id: str,
        *,
        external_call_chain: list[str] | None = None,
        user_token: str | None = None,
    ) -> tuple[asyncio.Queue, str, str]:
        """Start a streaming agent execution in the background.

        Returns (event_queue, request_id, session_id). The caller wraps
        the queue into a StreamingResponse. The background task pushes
        SSE-formatted events and persists the agent message on completion.
        """
        from app.engine.harness_integration import stream as harness_stream

        # 计时容器：start_ms = latency 基准 = 方法入口（覆盖 get_agent/prompt
        # 构建，与 phase 恒等式对齐）；ttft_ms 由首个 token 事件记入；
        # prep_ms/persist_ms 在 _run 内打点。
        timing: dict[str, int] = {"start_ms": _now_ms()}
        exec_doc = await AgentService.get_agent(agent_id)
        if exec_doc is None:
            raise NotFoundError(code="AGENT_NOT_FOUND", message=f"Agent {agent_id} 不存在")

        session_id = await _resolve_session(agent_id, body, user_id)
        request_id = str(uuid.uuid4())
        call_chain = [*(external_call_chain or []), agent_id]

        system_text = await _build_system_prompt_checked(exec_doc, user_id)

        event_queue: asyncio.Queue[str | None] = asyncio.Queue()
        collected_timeline: list[dict] = []

        async def _on_event(event: dict) -> None:
            # TTFT：首个内容 token 到达时打点（只记第一次）。
            if "ttft_ms" not in timing and event.get("type") in _TTFT_EVENT_TYPES:
                timing["ttft_ms"] = max(0, _now_ms() - timing.get("start_ms", _now_ms()))
            collected_timeline.append(event)
            await event_queue.put(f"data: {safe_json(event)}\n\n")

        async def _run():
            start_time_ms = timing["start_ms"]
            user_content = await _build_user_content(body, user_id, session_id)
            initial_messages = _assemble_messages(system_text, user_content)
            legacy_records = await _load_legacy_records(session_id, body.input)

            # Carry over the session's cumulative token spend so the budget
            # guard enforces the per-session ceiling across requests.
            session_doc = await SessionService.get_session(session_id)
            session_total_tokens = int((session_doc or {}).get("total_tokens", 0) or 0)

            initial_state = _build_initial_state(
                agent_id, session_id, user_id, request_id, call_chain,
                external_call_chain, initial_messages, execution_path="react",
                total_tokens=session_total_tokens,
            )
            timing["prep_ms"] = _now_ms() - timing.get("start_ms", _now_ms())
            run_error: BaseException | None = None
            cancelled = False
            try:
                result = await harness_stream(
                    exec_doc, initial_state,
                    on_event=_on_event,
                    enable_thinking=body.enable_thinking,
                    legacy_records=legacy_records,
                    cancel_checker=make_cancel_checker(request_id),
                    user_token=user_token,
                )
                logger.info(
                    "agent_stream_completed",
                    agent_id=agent_id, request_id=request_id,
                    step_count=result.get("step_count", 0),
                )
            except asyncio.CancelledError:
                # 用户 stop（mid-stream abort）。CancelledError 打断当前
                # await 点（LLM token 流 / 工具执行），半截回复不持久化：
                # LangGraph checkpoint 停在上一个完成的 superstep，被取消
                # 的轮次对后续对话不可见，新消息带完整干净历史重新开始。
                cancelled = True
                result = {}
                logger.info(
                    "agent_stream_cancelled",
                    agent_id=agent_id, request_id=request_id, session_id=session_id,
                )
            except Exception as exc:
                run_error = exc
                await _emit_stream_error(
                    exc, event_queue, collected_timeline,
                    agent_id=agent_id, request_id=request_id, log_tag="agent_stream_error",
                )
                result = {}
            finally:
                unregister_run(request_id)
                if not cancelled:
                    persist_t0 = _now_ms()
                    with contextlib.suppress(Exception):
                        await _persist_agent_message(
                            session_id, collected_timeline,
                            token_usage=result.get("usage"),
                            request_id=request_id,
                        )
                    timing["persist_ms"] = _now_ms() - persist_t0
                    # Unified execution log (all channels).
                    with contextlib.suppress(Exception):
                        await _record_execution_log(
                            user_id=user_id, agent_id=agent_id, session_id=session_id,
                            request_id=request_id, start_time_ms=start_time_ms,
                            token_usage=result.get("usage"), error=run_error,
                            ttft_ms=timing.get("ttft_ms", 0),
                            phase_timing=_compose_phase_timing(
                                result.get("timing"), result.get("usage"),
                                prep_ms=timing.get("prep_ms"),
                                persist_ms=timing.get("persist_ms"),
                            ),
                        )
                else:
                    # 取消轮也持久化（展示层）：半截回复 + 已停止标记，刷新后可见。
                    # 只影响 messages 展示集合，不回流模型上下文（checkpointer
                    # 停在上一个完成步，半截回复从未进入状态）。
                    finalized = _finalize_cancelled_timeline(collected_timeline)
                    if finalized:
                        with contextlib.suppress(asyncio.CancelledError, Exception):
                            await asyncio.shield(_persist_agent_message(
                                session_id, finalized,
                                request_id=request_id,
                            ))
                    # 取消态：写 execution_log（status=cancelled）；用
                    # shield 保证在取消态下仍能写完（防御二次 cancel）。
                    with contextlib.suppress(asyncio.CancelledError, Exception):
                        await asyncio.shield(_record_execution_log(
                            user_id=user_id, agent_id=agent_id, session_id=session_id,
                            request_id=request_id, start_time_ms=start_time_ms,
                            token_usage=None, error=None, status_override="cancelled",
                        ))
                await _emit_stream_done(
                    event_queue, request_id=request_id, session_id=session_id,
                    usage=result.get("usage"), cancelled=cancelled,
                    start_time_ms=start_time_ms, ttft_ms=timing.get("ttft_ms", 0),
                )

        # 同 session 的 checkpointer thread 只允许一个写入者：SSE 断连后
        # 后台仍在跑的旧 run 与新 run 并发写同一 thread 会互相覆盖
        # checkpoint / 消息乱序。新 run 启动前取消该 session 残留的活跃
        # run——用户主动发新消息即取代旧生成。
        await cancel_runs_by_session(session_id)

        task = asyncio.create_task(_run())
        # 注册运行句柄：stop 端点据此 task.cancel()（mid-stream abort）。
        register_run(ActiveRun(
            task=task, request_id=request_id, agent_id=agent_id,
            user_id=user_id, session_id=session_id,
        ))
        return event_queue, request_id, session_id

    # ------------------------------------------------------------------
    # Resume (SSE, after interrupt)
    # ------------------------------------------------------------------

    @staticmethod
    async def resume(
        agent_id: str,
        body: ResumeRequest,
        user_id: str,
        *,
        user_token: str | None = None,
    ) -> tuple[asyncio.Queue, str, str]:
        """Resume an interrupted agent and stream the continued execution."""
        from app.engine.harness_integration import resume as harness_resume

        # 计时容器：与 stream() 相同（start_ms = latency 基准 = 方法入口）。
        timing: dict[str, int] = {"start_ms": _now_ms()}
        exec_doc = await AgentService.get_agent(agent_id)
        if exec_doc is None:
            raise NotFoundError(code="AGENT_NOT_FOUND", message=f"Agent {agent_id} 不存在")

        request_id = str(uuid.uuid4())
        session_id = body.session_id

        # Note: user's answer is NOT stored as a separate user message.
        # It flows through interrupt() → ToolMessage (tool_result for
        # ask_clarification) in the agent's timeline, rendered as user input
        # by the frontend. This keeps the conversation history accurate —
        # the answer belongs to the tool call, not a standalone message.

        event_queue: asyncio.Queue[str | None] = asyncio.Queue()
        collected_timeline: list[dict] = []

        async def _on_event(event: dict) -> None:
            if "ttft_ms" not in timing and event.get("type") in _TTFT_EVENT_TYPES:
                timing["ttft_ms"] = max(0, _now_ms() - timing.get("start_ms", _now_ms()))
            collected_timeline.append(event)
            await event_queue.put(f"data: {safe_json(event)}\n\n")

        async def _run():
            start_time_ms = timing["start_ms"]
            # Carry over the session's cumulative token spend so the budget
            # guard enforces the per-session ceiling, consistent with invoke/stream.
            session_doc = await SessionService.get_session(session_id)
            session_total_tokens = int((session_doc or {}).get("total_tokens", 0) or 0)
            state = {
                "messages": [], "agent_id": agent_id,
                "session_id": session_id, "user_id": user_id,
                "total_tokens": session_total_tokens,
            }
            timing["prep_ms"] = _now_ms() - timing.get("start_ms", _now_ms())
            run_error: BaseException | None = None
            cancelled = False
            try:
                result = await harness_resume(
                    exec_doc, state, _on_event, body.answer,
                    enable_thinking=body.enable_thinking,
                    cancel_checker=make_cancel_checker(request_id),
                    user_token=user_token,
                )
            except asyncio.CancelledError:
                # 与 stream() 相同的 mid-stream abort 语义（见 stream 内注释）。
                cancelled = True
                result = {}
                logger.info(
                    "agent_resume_cancelled",
                    agent_id=agent_id, request_id=request_id, session_id=session_id,
                )
            except Exception as exc:
                run_error = exc
                await _emit_stream_error(
                    exc, event_queue, collected_timeline,
                    agent_id=agent_id, request_id=request_id, log_tag="agent_resume_error",
                )
                result = {}
            finally:
                unregister_run(request_id)
                if not cancelled:
                    persist_t0 = _now_ms()
                    with contextlib.suppress(Exception):
                        await _persist_agent_message(
                            session_id, collected_timeline,
                            extra_filter_types=("interrupt",),
                            token_usage=result.get("usage"),
                            append_to_last_agent=True,
                            request_id=request_id,
                        )
                    timing["persist_ms"] = _now_ms() - persist_t0
                    # Unified execution log (all channels).
                    # Failure is non-fatal — must not block the terminal done event.
                    try:
                        await _record_execution_log(
                            user_id=user_id, agent_id=agent_id, session_id=session_id,
                            request_id=request_id, start_time_ms=start_time_ms,
                            token_usage=result.get("usage"), error=run_error,
                            ttft_ms=timing.get("ttft_ms", 0),
                            phase_timing=_compose_phase_timing(
                                result.get("timing"), result.get("usage"),
                                prep_ms=timing.get("prep_ms"),
                                persist_ms=timing.get("persist_ms"),
                            ),
                        )
                    except Exception:
                        logger.exception("agent_resume_log_error", agent_id=agent_id)
                else:
                    # 取消轮也持久化（展示层）：半截回复 + 已停止标记，刷新后可见。
                    # 只影响 messages 展示集合，不回流模型上下文（checkpointer
                    # 停在上一个完成步，半截回复从未进入状态）。
                    finalized = _finalize_cancelled_timeline(collected_timeline)
                    if finalized:
                        with contextlib.suppress(asyncio.CancelledError, Exception):
                            await asyncio.shield(_persist_agent_message(
                                session_id, finalized,
                                extra_filter_types=("interrupt",),
                                append_to_last_agent=True,
                                request_id=request_id,
                            ))
                    with contextlib.suppress(asyncio.CancelledError, Exception):
                        await asyncio.shield(_record_execution_log(
                            user_id=user_id, agent_id=agent_id, session_id=session_id,
                            request_id=request_id, start_time_ms=start_time_ms,
                            token_usage=None, error=None, status_override="cancelled",
                        ))
                await _emit_stream_done(
                    event_queue, request_id=request_id, session_id=session_id,
                    usage=result.get("usage"), cancelled=cancelled,
                    start_time_ms=start_time_ms, ttft_ms=timing.get("ttft_ms", 0),
                )

        # 与 stream 相同的并发防护：resume 也写同一 checkpointer thread。
        await cancel_runs_by_session(session_id)

        task = asyncio.create_task(_run())
        # 注册运行句柄：resume 的流同样可被 stop 端点取消。
        register_run(ActiveRun(
            task=task, request_id=request_id, agent_id=agent_id,
            user_id=user_id, session_id=session_id,
        ))
        return event_queue, request_id, session_id


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

_TRANSIENT_EVENT_TYPES = ("text_delta", "thinking_delta", "tool_call_start", "interrupt")

# TTFT 打点事件类型：首个内容 token（含 thinking）到达即计首 token 延迟。
_TTFT_EVENT_TYPES = ("text_delta", "thinking_delta", "text", "thinking")

#: 慢请求阈值（毫秒）——超过时 agent_call_timing 日志升级为 warning，便于筛选。
_SLOW_CALL_MS = 10_000


def _compose_phase_timing(
    result_timing: dict | None,
    usage: dict | None,
    *,
    prep_ms: int | None = None,
    persist_ms: int | None = None,
) -> dict | None:
    """合成 DEBUG 阶段耗时日志字段（settings.DEBUG=false → None，不输出）。

    result_timing 来自 harness execution 返回的 result["timing"]（build/exec
    + build 子阶段）；graph_overhead = exec − llm − tool（图调度/checkpoint/
    compress 等非 LLM 非工具开销，clamp ≥ 0）。恒等式（latency 基准与方法
    入口对齐后严格成立）：
    prep + build + graph_overhead + llm + tool + persist ≈ latency。
    """
    if result_timing is None:
        return None
    usage = usage or {}
    llm_ms = int(round(float(usage.get("llm_duration") or 0) * 1000))
    tool_ms = int(round(float(usage.get("tool_duration") or 0) * 1000))
    exec_ms = int(result_timing.get("exec_ms") or 0)
    fields: dict[str, int] = {
        "prep_ms": prep_ms or 0,
        "build_ms": int(result_timing.get("build_ms") or 0),
        "exec_ms": exec_ms,
        "llm_ms": llm_ms,
        "tool_ms": tool_ms,
        "graph_overhead_ms": max(0, exec_ms - llm_ms - tool_ms),
        "persist_ms": persist_ms or 0,
    }
    # build 子阶段（build_llm_client_ms / build_tools_mcp_ms / ...）平铺并入
    for key, val in (result_timing.get("phases") or {}).items():
        fields[key] = int(val)
    return fields


def _duration_metrics(
    usage: dict | None,
    *,
    start_time_ms: int | None,
    ttft_ms: int = 0,
) -> dict[str, int]:
    """把 UsageMiddleware 的 usage（duration 为浮点秒）换算成毫秒级耗时拆分。

    other = 总耗时 - LLM - 工具（即代码/框架延迟），clamp ≥ 0（monotonic
    时钟与墙钟做差可能轻微为负）。start_time_ms 为 None 时 latency/other 记 0。
    """
    usage = usage or {}
    llm_ms = int(round(float(usage.get("llm_duration") or 0) * 1000))
    tool_ms = int(round(float(usage.get("tool_duration") or 0) * 1000))
    latency_ms = max(0, _now_ms() - start_time_ms) if start_time_ms else 0
    other_ms = max(0, latency_ms - llm_ms - tool_ms) if latency_ms else 0
    return {
        "total_latency_ms": latency_ms,
        "llm_duration_ms": llm_ms,
        "tool_duration_ms": tool_ms,
        "other_duration_ms": other_ms,
        "ttft_ms": ttft_ms,
    }

#: 取消标记条目——三个前端都以纯 text 渲染，语义中性（非 error）。
_CANCELLED_MARKER = "⏹ 已停止生成"


def _finalize_cancelled_timeline(timeline: list[dict]) -> list[dict]:
    """取消轮的展示层收尾：拼出半截文本 + 追加停止标记。

    text_delta 是瞬态事件（正常完成轮会被 _persist_agent_message 过滤），
    取消时把最后一段未定稿的 delta 合成为 text 条目，用户刷新后仍能
    看到半截回复和已执行的工具事件；末尾加 ⏹ 标记表明轮次被主动停止。

    无任何可见内容（秒级取消、连工具都没跑）时返回空列表 → 不落库。
    """
    last_text_idx = -1
    for i, e in enumerate(timeline):
        if e.get("type") == "text":
            last_text_idx = i
    partial = "".join(
        e.get("content", "") for e in timeline[last_text_idx + 1:]
        if e.get("type") == "text_delta"
    )
    meaningful = bool(partial) or any(
        e.get("type") not in _TRANSIENT_EVENT_TYPES for e in timeline
    )
    if not meaningful:
        return []
    entries = list(timeline)
    if partial:
        entries.append({"type": "text", "content": partial})
    entries.append({"type": "text", "content": _CANCELLED_MARKER})
    return entries


async def _resolve_session(agent_id: str, body: ExecutionRequest, user_id: str) -> str:
    """Resolve or create a session, then persist the user message."""
    session_id = body.session_id or ""
    # 展示文案（快捷指令 label）优先用于会话标题，避免后台指令泄漏到侧边栏
    title_source = body.display_text or body.input
    if not session_id:
        session_doc = await SessionService.create_session(
            user_id=user_id, agent_id=agent_id, title=title_source[:200],
        )
        session_id = session_doc["_id"]
    await MessageService.add_message(
        session_id=session_id, role="user",
        content=body.input, file_ids=body.file_ids or None,
        display_text=body.display_text or "",
    )
    return session_id


async def _build_system_prompt_checked(exec_doc: dict, user_id: str = "") -> str:
    """Build system prompt, raising ValidationError on slot issues.

    v6 用户技能与记忆注入（§5.3）：全部住主 system prompt 末尾
    （用户技能名列表 + 构建规范段 + <user_preferences> 记忆块）——
    位于 llm_summary 压缩块之前，压缩免疫；每请求重建、下一轮生效。
    IM 渠道（channel: 前缀）由 build_user_sections 内部门控跳过。

    官方/个人重名（§7.4）：个人技能遮蔽同名官方技能——解析层
    （SkillManager 双根个人优先）与注入层（官方声明剔除被遮蔽名）
    双重保证，prompt 中同名技能只出现一份（个人版）。
    """
    personal_skills: list[dict] = []
    if user_id and exec_doc.get("user_skills_enabled", True):
        try:
            from app.engine.user_skills.tools import is_channel_user

            if not is_channel_user(user_id):
                from app.services.user_skill_service import UserSkillService

                personal_skills = await UserSkillService.enabled_skills(user_id)
        except Exception:  # noqa: BLE001 — 查询失败按无个人技能处理
            personal_skills = []

    shadow_names = {s["effective_name"] for s in personal_skills} or None

    try:
        system_text = await build_system_prompt(exec_doc, exclude_skill_names=shadow_names)
    except ValueError as exc:
        raise ValidationError(code="AGENT_PROMPT_SLOT_MISSING", message=str(exc)) from exc

    if user_id:
        try:
            from app.engine.user_skills.injection import build_user_sections

            user_sections = await build_user_sections(user_id, exec_doc, skills=personal_skills or None)
            if user_sections:
                system_text = f"{system_text}\n{user_sections}"
        except Exception:  # noqa: BLE001 — 注入失败不阻断主流程
            import structlog

            structlog.get_logger(__name__).exception("user_sections_inject_failed",
                                                     user_id=user_id)
    return system_text


async def _build_user_content(body: ExecutionRequest, user_id: str, session_id: str) -> str:
    """Embed uploaded file contents into the user message text."""
    user_content = body.input
    try:
        if body.file_ids:
            blocks = await render_files_by_ids(body.file_ids)
            if blocks:
                user_content += render_attachments_block(blocks)
        elif body.file_paths:
            from app.engine.tool.workspace import WorkspaceManager
            ws = WorkspaceManager.get_workspace(user_id, session_id)
            blocks = await render_files_by_paths(body.file_paths, ws.root)
            if blocks:
                user_content += render_attachments_block(blocks)
    except Exception:
        pass
    return user_content


def _assemble_messages(system_text: str, user_content: str) -> list:
    """Build the initial messages list (System + User)."""
    messages: list = []
    if system_text:
        messages.append(SystemMessage(content=system_text, id="sys"))
    messages.append({"role": "user", "content": user_content})
    return messages


async def _load_legacy_records(session_id: str, current_input: str) -> list[dict]:
    """Load legacy history records for thread migration (if enabled)."""
    if not session_id or not settings.MIGRATE_LEGACY_SESSIONS:
        return []
    try:
        records = await MessageService.list_messages(session_id)
        return [
            r for r in records
            if r.get("content") != current_input or r.get("role") != "user"
        ]
    except Exception:
        return []


def _build_initial_state(
    agent_id: str,
    session_id: str,
    user_id: str,
    request_id: str,
    call_chain: list[str],
    external_chain: list[str] | None,
    messages: list,
    execution_path: str = "",
    total_tokens: int = 0,
) -> dict[str, Any]:
    return {
        "messages": messages,
        "agent_id": agent_id,
        "execution_path": execution_path,
        "request_id": request_id,
        "tool_results": {},
        "step_count": 0,
        "error": None,
        "call_chain": call_chain,
        "current_depth": len(external_chain or []),
        "session_id": session_id,
        "user_id": user_id,
        # Seed cumulative tokens from the session so TokenBudgetGuard judges
        # against the *session* budget, not just this single request. Persisted
        # after each request via SessionService.add_tokens.
        "total_tokens": total_tokens,
    }


# LLM 类异常的类型名关键词（主流 LLM 库：openai / anthropic / 通义等）。
# 这些异常在模型欠费、限流、超时、鉴权失败时抛出。
_LLM_ERROR_TYPE_KEYWORDS = (
    "ratelimit", "rate_limit", "apitimeout", "authentication",
    "permissiondenied", "insufficient_quota", "quotaexceeded",
    "connection", "timeout",
)
# 错误消息中的 LLM 类关键词。
_LLM_ERROR_MSG_KEYWORDS = (
    "rate limit", "quota", "insufficient_quota", "余额不足", "欠费",
    "api key", "invalid_api_key", "authentication",
    "model_not_found", "context_length_exceeded",
)


def _classify_error_source(exc: BaseException) -> str:
    """根据异常类型/消息判定错误来源，供前端区分展示。

    返回 "llm"（模型欠费/限流/超时/鉴权）、"tool"（工具加载/配置错误）、
    或 "graph"（其余含服务端内部异常）。

    注意：按"所有错误都暴露给前端"的原则，这里只分类来源，不脱敏——
    顶层异常的 str(exc) 会原样发给前端。
    """
    type_name = type(exc).__name__.lower()
    msg = str(exc).lower()

    # LLM 类异常：匹配类型名或消息关键词
    if any(kw in type_name for kw in _LLM_ERROR_TYPE_KEYWORDS):
        return "llm"
    if any(kw in msg for kw in _LLM_ERROR_MSG_KEYWORDS):
        return "llm"

    return "graph"


async def _emit_stream_error(
    exc: BaseException,
    event_queue: asyncio.Queue,
    collected_timeline: list[dict],
    *,
    agent_id: str,
    request_id: str,
    log_tag: str,
) -> None:
    """Push an ErrorEvent to the SSE stream + timeline, then log it.

    Shared by stream/resume so the error-handling block stays in sync.
    Best-effort: shielded put is swallow-and-continue so cancel/errors
    can't block the caller's finally cleanup.
    """
    from app.engine.harness_integration.adapters.app_event import ErrorEvent

    try:
        err_evt = ErrorEvent(
            message=str(exc),
            source=_classify_error_source(exc),
        ).model_dump()
        err_evt["content"] = err_evt.pop("message", "")
        collected_timeline.append(err_evt)
        await asyncio.shield(
            event_queue.put(f"data: {safe_json(err_evt)}\n\n")
        )
    except Exception:
        pass  # shield 也可能被取消;尽力而为
    logger.error(log_tag, agent_id=agent_id, request_id=request_id, error=str(exc))
    logger.exception(f"{log_tag}_traceback")


async def _emit_stream_done(
    event_queue: asyncio.Queue,
    *,
    request_id: str,
    session_id: str,
    usage: dict | None,
    cancelled: bool = False,
    start_time_ms: int | None = None,
    ttft_ms: int = 0,
) -> None:
    """Push the terminal done event + close sentinel. Shared by stream/resume.

    start_time_ms 提供时，把 usage 补齐为毫秒级耗时拆分（total_latency_ms /
    llm_duration_ms / tool_duration_ms / other_duration_ms / ttft_ms），
    前端可直接渲染「慢在哪」而无需换算。
    """
    usage = dict(usage or {})
    if start_time_ms is not None:
        m = _duration_metrics(usage, start_time_ms=start_time_ms, ttft_ms=ttft_ms)
        usage.update(m)
    await event_queue.put(
        f"data: {safe_json({'done': True, 'cancelled': cancelled, 'request_id': request_id, 'session_id': session_id, 'usage': usage})}\n\n"
    )
    await event_queue.put(None)


async def _persist_agent_message(
    session_id: str,
    collected_timeline: list[dict],
    *,
    extra_filter_types: tuple[str, ...] = (),
    token_usage: dict | None = None,
    append_to_last_agent: bool = False,
    request_id: str = "",
) -> None:
    """Filter transient events and persist the agent message.

    Args:
        append_to_last_agent: If True, append to the last agent message instead
            of creating a new one. Used by resume so that tool_call and
            tool_result for ask_clarification end up in the same message.
        request_id: 本轮请求 id——消息级反馈（§8.2）的轮次键。
    """
    filter_types = _TRANSIENT_EVENT_TYPES + extra_filter_types
    persistence_timeline = [
        e for e in collected_timeline
        if e.get("type") not in filter_types
    ]
    if persistence_timeline:
        try:
            if append_to_last_agent:
                await MessageService.append_to_last_agent_message(
                    session_id, persistence_timeline, token_usage=token_usage or {},
                    request_id=request_id,
                )
            else:
                await MessageService.add_message(
                    session_id=session_id, role="agent",
                    timeline_entries=persistence_timeline,
                    token_usage=token_usage or {},
                    request_id=request_id,
                )
            # Accumulate token usage on the session
            if token_usage and token_usage.get("total_tokens"):
                await SessionService.add_tokens(session_id, token_usage["total_tokens"])
        except Exception as exc:
            logger.error("agent_stream_persist_error", error=str(exc))


async def _record_execution_log(
    *,
    user_id: str,
    agent_id: str,
    session_id: str,
    request_id: str,
    start_time_ms: int,
    token_usage: dict | None,
    error: BaseException | None = None,
    status_override: str | None = None,
    ttft_ms: int = 0,
    phase_timing: dict | None = None,
) -> None:
    """Write one unified execution_logs record for ANY agent call.

    Writes unconditionally for internal / api_key / im calls alike. Channel
    (source) is derived from ``user_id`` by the service. External-only
    fields (api_key_id / endpoint) are pulled from the stashed
    ExtCallContext when present, and the context is marked consumed so
    the stats middleware fallback skips the duplicate write.

    同时输出一行 ``agent_call_timing`` 诊断日志（毫秒级耗时拆分），用于
    快速判断慢因：llm 高 = 模型服务慢；tool 高 = 工具慢；other 高 =
    代码/框架延迟。超过 _SLOW_CALL_MS 时日志级别升为 warning。
    phase_timing（DEBUG 专用，_compose_phase_timing 合成）非空时额外输出
    ``agent_phase_timing`` 日志，把 other 拆到 prep/build/graph_overhead/persist。

    Failure is logged but never raised — execution logging must not
    break the user-facing request flow.
    """
    import time as _time

    from app.services.execution_log_service import ExecutionLogService
    from app.services.ext_api_call_log_service import get_ext_call_context

    usage = token_usage or {}
    latency_ms = int(_time.time() * 1000) - start_time_ms
    metrics = _duration_metrics(usage, start_time_ms=start_time_ms, ttft_ms=ttft_ms)
    slow = latency_ms > _SLOW_CALL_MS
    log = logger.warning if slow else logger.info
    log(
        "agent_call_timing",
        request_id=request_id, agent_id=agent_id,
        latency_ms=latency_ms,
        ttft_ms=metrics["ttft_ms"],
        llm_duration_ms=metrics["llm_duration_ms"],
        tool_duration_ms=metrics["tool_duration_ms"],
        other_duration_ms=metrics["other_duration_ms"],
        input_tokens=int(usage.get("input_tokens") or 0),
        output_tokens=int(usage.get("output_tokens") or 0),
        llm_calls=int(usage.get("llm_calls") or 0),
        tool_calls=int(usage.get("tool_calls") or 0),
        slow=slow,
    )
    if phase_timing:
        logger.info(
            "agent_phase_timing",
            request_id=request_id, agent_id=agent_id, **phase_timing,
        )
    if status_override is not None:
        # e.g. "cancelled"（用户 stop）——显式状态优先于 error 推断。
        status = status_override
        status_code = 499  # client closed request（nginx 惯例）
    else:
        status = "error" if error is not None else "success"
        status_code = 500 if error is not None else 200

    # External calls carry api_key_id / endpoint in the stashed context.
    api_key_id = ""
    endpoint = ""
    ctx = get_ext_call_context()
    if ctx is not None:
        api_key_id = ctx.api_key_id
        endpoint = ctx.endpoint
        ctx.consumed = True  # suppress middleware fallback duplicate

    await ExecutionLogService.write_log(
        user_id=user_id,
        agent_id=agent_id,
        session_id=session_id,
        request_id=request_id,
        api_key_id=api_key_id,
        endpoint=endpoint,
        status=status,
        status_code=status_code,
        latency_ms=latency_ms,
        llm_duration_ms=metrics["llm_duration_ms"],
        tool_duration_ms=metrics["tool_duration_ms"],
        ttft_ms=metrics["ttft_ms"],
        total_tokens=int(usage.get("total_tokens") or 0),
        input_tokens=int(usage.get("input_tokens") or 0),
        output_tokens=int(usage.get("output_tokens") or 0),
        llm_calls=int(usage.get("llm_calls") or 0),
    )


__all__ = ["AgentExecutionService"]
