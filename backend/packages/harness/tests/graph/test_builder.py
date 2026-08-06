"""AC7 cover: build_agent_graph builds a node-based graph (compress/llm/tools)."""

from __future__ import annotations

import pytest
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph.state import CompiledStateGraph

from agent_flow_harness.graph import build_agent_graph


def test_build_agent_graph_returns_compiled_graph(agent_doc: dict) -> None:
    graph = build_agent_graph(agent_doc, tools=[], middleware=[])
    assert isinstance(graph, CompiledStateGraph)


def test_build_agent_graph_has_node_topology(agent_doc: dict) -> None:
    """v0.3 topology: compress + llm + tools (node-based, no 'react')."""
    graph = build_agent_graph(agent_doc, tools=[], middleware=[])
    user_nodes = set(graph.nodes) - {"__start__", "__end__"}
    assert user_nodes == {"compress", "llm", "tools"}


def test_build_agent_graph_accepts_checkpointer(
    agent_doc: dict, in_memory_checkpointer: MemorySaver
) -> None:
    graph = build_agent_graph(
        agent_doc, checkpointer=in_memory_checkpointer, tools=[], middleware=[],
    )
    assert isinstance(graph, CompiledStateGraph)


def test_build_agent_graph_accepts_guards_and_middleware_stubs(agent_doc: dict) -> None:
    """Signature is stable: guards/middleware kwargs accepted."""
    graph = build_agent_graph(agent_doc, guards=None, middleware=[], tools=[])
    assert isinstance(graph, CompiledStateGraph)


@pytest.mark.asyncio
async def test_build_agent_graph_runs_llm_node(
    agent_doc: dict, base_state, fake_llm_factory, make_run_config
) -> None:
    """Invoking the graph runs the llm node with the injected config."""
    from langchain_core.messages import AIMessage

    llm = fake_llm_factory([AIMessage(content="graph ok")])
    graph = build_agent_graph(agent_doc, tools=[], middleware=[])
    result = await graph.ainvoke(base_state, config=make_run_config(llm))
    assert result["step_count"] == 1
    assert result["messages"][-1].content == "graph ok"


@pytest.mark.asyncio
async def test_cancel_checker_triggers_interrupt(
    agent_doc: dict, base_state, fake_llm_factory, in_memory_checkpointer
) -> None:
    """When cancel_checker returns True, compress_node calls interrupt() and
    the graph suspends (result contains __interrupt__).
    """
    from langchain_core.messages import AIMessage

    from agent_flow_harness.graph import build_config

    llm = fake_llm_factory([AIMessage(content="should not reach")])

    cancelled = True

    async def _cancel_checker() -> bool:
        return cancelled

    graph = build_agent_graph(
        agent_doc, checkpointer=in_memory_checkpointer, tools=[], middleware=[],
    )
    config = build_config(
        agent_doc, llm, tools=[], middlewares=[],
        thread_id="cancel-test",
        cancel_checker=_cancel_checker,
    )
    result = await graph.ainvoke(base_state, config=config)
    # Graph should be interrupted, not completed
    assert "__interrupt__" in result, f"Expected __interrupt__, got: {result.keys()}"


@pytest.mark.asyncio
async def test_cancel_checker_false_runs_normally(
    agent_doc: dict, base_state, fake_llm_factory, in_memory_checkpointer
) -> None:
    """When cancel_checker returns False, the graph runs normally."""
    from langchain_core.messages import AIMessage

    from agent_flow_harness.graph import build_config

    llm = fake_llm_factory([AIMessage(content="done")])

    async def _cancel_checker() -> bool:
        return False

    graph = build_agent_graph(
        agent_doc, checkpointer=in_memory_checkpointer, tools=[], middleware=[],
    )
    config = build_config(
        agent_doc, llm, tools=[], middlewares=[],
        thread_id="no-cancel-test",
        cancel_checker=_cancel_checker,
    )
    result = await graph.ainvoke(base_state, config=config)
    assert "__interrupt__" not in result
    assert result["messages"][-1].content == "done"


@pytest.mark.asyncio
async def test_tool_exception_becomes_tool_message(
    agent_doc: dict, base_state, fake_llm_factory, make_run_config
) -> None:
    """A tool raising ToolException (e.g. MCP isError=true) must be turned into
    an error ToolMessage returned to the LLM — NOT re-raised to kill the agent
    flow.

    Regression: the default ToolNode handle_tool_errors only catches
    ToolInvocationError, so a ToolException (what langchain-mcp-adapters raises
    on MCP ``isError=true``) used to bubble up and terminate the whole graph.
    """
    from langchain_core.messages import AIMessage, ToolMessage
    from langchain_core.tools import StructuredTool, ToolException

    def _boom(**_kwargs):  # noqa: ANN202
        # 模拟 MCP adapter 对 isError=true 的处理（langchain_mcp_adapters/
        # tools.py:_convert_call_tool_result 抛的就是 ToolException）。
        msg = "User Name or Password is Invalid!"
        raise ToolException(msg)

    boom = StructuredTool.from_function(_boom, name="boom", description="raises")

    # 第一轮：LLM 发起 tool_call；第二轮：LLM 看到错误 ToolMessage 后改用文本回复。
    llm = fake_llm_factory([
        AIMessage(content="", tool_calls=[{"name": "boom", "args": {}, "id": "c1"}]),
        AIMessage(content="登录失败，请检查凭证"),
    ])
    config = make_run_config(llm, tools=[boom])

    graph = build_agent_graph(agent_doc, tools=[boom], middleware=[])
    result = await graph.ainvoke(base_state, config=config)

    # 1. 不抛异常、正常结束（修复前会崩）
    # 2. 有一条 status="error" 的 ToolMessage，内容含错误信息
    tool_msgs = [m for m in result["messages"] if isinstance(m, ToolMessage)]
    assert len(tool_msgs) == 1
    assert tool_msgs[0].status == "error"
    assert "User Name or Password is Invalid!" in tool_msgs[0].content
    # 3. LLM 看到错误后用文本收尾（REACT 循环继续、没有被工具错误打断）
    assert result["messages"][-1].content == "登录失败，请检查凭证"
    assert result["messages"][-1].tool_calls == []


@pytest.mark.asyncio
async def test_tool_generic_exception_becomes_tool_message(
    agent_doc: dict, base_state, fake_llm_factory, make_run_config
) -> None:
    """工具抛出非 ToolException 的普通异常（如 openapi 工具的 httpx 连接错误、
    code 工具执行错误）也必须转成 error ToolMessage 返回给 LLM，不能让图崩溃。

    Regression: tool_wrapper 之前只 catch ToolException，普通异常会逃逸 →
    ToolNode 默认 handle_tool_errors 只消化 ToolInvocationError → re-raise →
    图崩溃 → 前端工具卡在「执行中」、agent 收不到错误无法继续。
    """
    from langchain_core.messages import AIMessage, ToolMessage
    from langchain_core.tools import StructuredTool

    def _boom(**_kwargs):  # noqa: ANN202
        # 模拟 httpx.ConnectError 等系统异常（不是 ToolException）
        raise RuntimeError("connection refused")

    boom = StructuredTool.from_function(_boom, name="boom", description="raises")

    llm = fake_llm_factory([
        AIMessage(content="", tool_calls=[{"name": "boom", "args": {}, "id": "c1"}]),
        AIMessage(content="工具调用失败，已记录"),
    ])
    config = make_run_config(llm, tools=[boom])

    graph = build_agent_graph(agent_doc, tools=[boom], middleware=[])
    result = await graph.ainvoke(base_state, config=config)

    tool_msgs = [m for m in result["messages"] if isinstance(m, ToolMessage)]
    assert len(tool_msgs) == 1
    assert tool_msgs[0].status == "error"
    assert "connection refused" in tool_msgs[0].content
    # LLM 收到错误后继续，没有被异常打断
    assert result["messages"][-1].content == "工具调用失败，已记录"


@pytest.mark.asyncio
async def test_tool_pydantic_validation_error_becomes_tool_message(
    agent_doc: dict, base_state, fake_llm_factory, make_run_config
) -> None:
    """agent 传了不符合 pydantic 模型的参数（类型错误/缺字段），触发 ValidationError，
    必须转成 error ToolMessage 返回给 LLM，让模型据此修正参数重试，而不是卡死。

    这是最常见的「工具一直执行中」现场：LLM 生成的 args 不合法。ToolNode 内部
    会把 ValidationError 转成 ToolInvocationError，本测试确认整条链路能把它消化
    成 error ToolMessage 回到 LLM。
    """
    from langchain_core.messages import AIMessage, ToolMessage
    from langchain_core.tools import StructuredTool

    def _need_int(count: int):  # noqa: ANN202
        return f"got {count}"

    need_int = StructuredTool.from_function(_need_int, name="need_int", description="needs int")

    # agent 故意传了字符串而非 int，触发 ValidationError
    llm = fake_llm_factory([
        AIMessage(
            content="",
            tool_calls=[{"name": "need_int", "args": {"count": "not-an-int"}, "id": "c1"}],
        ),
        # 第二轮修正参数重试
        AIMessage(
            content="",
            tool_calls=[{"name": "need_int", "args": {"count": 5}, "id": "c2"}],
        ),
        AIMessage(content="完成"),
    ])
    config = make_run_config(llm, tools=[need_int])

    graph = build_agent_graph(agent_doc, tools=[need_int], middleware=[])
    result = await graph.ainvoke(base_state, config=config)

    tool_msgs = [m for m in result["messages"] if isinstance(m, ToolMessage)]
    # 第一轮：参数错误 → error ToolMessage；第二轮：参数正确 → 正常 ToolMessage
    assert len(tool_msgs) == 2
    assert tool_msgs[0].status == "error"
    assert tool_msgs[1].status in (None, "success")  # 正常结果默认无 status 或 success
    # LLM 看到参数错误后能修正并完成，没有卡死
    assert result["messages"][-1].content == "完成"
