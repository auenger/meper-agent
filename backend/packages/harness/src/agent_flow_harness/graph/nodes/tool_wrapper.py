"""Middleware bridge for the native ``langgraph.prebuilt.ToolNode``.

``ToolNode`` accepts an ``awrap_tool_call`` interceptor that receives every
tool call before execution. This module builds such an interceptor from a
harness :class:`~agent_flow_harness.middleware.chain.MiddlewareChain`, wiring
the ``run_before_tool`` / ``run_after_tool`` hooks without giving up the
native node's error handling, concurrency, and command support.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

import structlog
from langchain_core.messages import ToolMessage
from langchain_core.tools import ToolException
from langgraph.errors import GraphBubbleUp

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from langgraph.prebuilt.tool_node import ToolCallRequest
    from langgraph.types import Command

    from agent_flow_harness.middleware.chain import MiddlewareChain

logger = structlog.get_logger(__name__)


def make_tool_wrapper(
    chain: MiddlewareChain,
) -> Callable[
    [ToolCallRequest, Callable[[ToolCallRequest], Awaitable["ToolMessage | Command[Any]"]]],  # noqa: UP006
    Awaitable["ToolMessage | Command[Any]"],  # noqa: UP006
]:
    """Create an ``awrap_tool_call`` that runs middleware around tool execution.

    Args:
        chain: The middleware chain whose ``run_before_tool`` /
            ``run_after_tool`` hooks should fire on each tool call.

    Returns:
        An async wrapper compatible with ``ToolNode(awrap_tool_call=...)``.
    """

    async def awrap(
        request: ToolCallRequest,
        execute: Callable[[ToolCallRequest], Awaitable["ToolMessage | Command[Any]"]],  # noqa: UP006
    ) -> "ToolMessage | Command[Any]":
        state: Any = request.state
        tc: dict[str, Any] = dict(request.tool_call)

        # before_tool — middleware may observe / modify the call args.
        tc = await chain.run_before_tool(state, tc)

        # Re-inject any middleware modifications into the request.
        modified = request.override(tool_call=tc)  # type: ignore[arg-type]

        # Execute via the native ToolNode (handles errors, concurrency).
        #
        # 把工具执行异常转成 error ToolMessage 返回给 LLM，让模型据此决定下一步
        # （重试 / 换工具 / 转告用户），而不是让异常冒泡终止整个 agent 流。
        # 覆盖两类异常：
        #   - ToolException：工具业务失败。MCP adapter 在 MCP ``isError=true``
        #     时抛的正是它（langchain_mcp_adapters/tools.py），langchain 工具的
        #     业务校验失败也用它。
        #   - 其它普通 Exception：如 openapi 工具的 httpx 连接错误、code 工具的
        #     执行错误等。默认 ToolNode(handle_tool_errors=...) 只消化
        #     ToolInvocationError，其它异常会 re-raise 让图崩溃，导致前端工具卡在
        #     「执行中」、agent 收不到错误无法继续，故在此兜底。
        #
        # 关键：必须先 ``except GraphBubbleUp: raise`` 放行人机协同中断。
        # GraphInterrupt（HITL ask_clarification / confirm_workflow 用的
        # interrupt()）是 GraphBubbleUp 子类，若被 ``except Exception`` 吞成
        # error ToolMessage，会破坏人机协同（interrupt 无法挂起 graph）。
        # LangGraph 在 _execute_tool_async 内部也遵循同样的顺序（先 GraphBubbleUp
        # 后 Exception，tool_node.py:982-984）。
        #
        # 为何在这里处理而非用 ToolNode(handle_tool_errors=...)：langgraph 1.2.4
        # 的 ToolNode 在配了 awrap_tool_call 时，_arun_one 外层 except Exception
        # 会把 GraphInterrupt 也吞掉（_arun_one:1211 不区分 GraphBubbleUp），
        # 所以必须由本 wrapper 精确放行。文案与旧 react 引擎 (engine/react.py:218)
        # 一致。
        try:
            result = await execute(modified)
        except GraphBubbleUp:
            # 人机协同中断（HITL），必须原样冒泡挂起 graph，不可吞
            raise
        except Exception as exc:
            if isinstance(exc, ToolException):
                logger.warning(
                    "tool_execution_failed",
                    tool_name=tc.get("name", ""),
                    error=str(exc),
                )
            else:
                # 非业务异常（连接错误、执行错误等），记录 error 级别便于排查
                logger.error(
                    "tool_execution_error",
                    tool_name=tc.get("name", ""),
                    error=str(exc),
                    exc_info=exc,
                )
            result = ToolMessage(
                content=f"Error executing tool: {exc}",
                name=tc.get("name", ""),
                tool_call_id=tc.get("id", ""),
                status="error",
            )

        # after_tool — middleware observes the result content.
        result_content = result.content if isinstance(result, ToolMessage) else ""
        await chain.run_after_tool(state, tc, str(result_content))

        return result

    return awrap


__all__ = ["make_tool_wrapper"]
