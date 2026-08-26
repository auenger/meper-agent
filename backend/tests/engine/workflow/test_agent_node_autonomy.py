"""工作流 agent 节点无人值守语义测试。

三层防护的单元测试：
1. 工具层（context._resolve_builtin_tools）——workflow 上下文剥离
   ask_clarification / _TASK_TOOLS，注入 abort_workflow；chat 上下文不变。
2. Prompt 层（builder.build_tool_declaration）——workflow 声明无
   Clarification / Task Management 段，有 Autonomous Execution 段。
3. 执行层（AgentNodeExecutor）——interrupt payload 区分（cancelled vs
   其他 HITL）+ abort_workflow 调用扫描 → AGENT_INPUT_INSUFFICIENT。
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# 1. 工具层
# ---------------------------------------------------------------------------


def _tool_names(tools: list) -> set[str]:
    return {t.name for t in tools}


class TestResolveBuiltinToolsByContext:
    """_resolve_builtin_tools 按执行上下文裁剪工具集。"""

    def test_workflow_context_strips_hitl_and_task_tools(self):
        from app.engine.agent.workflow_executor import _TASK_TOOLS
        from app.engine.harness_integration.context import _resolve_builtin_tools

        agent = {"builtin_config": ["bash"]}
        names = _tool_names(_resolve_builtin_tools(agent, "workflow"))

        # 交互式反问工具被剥离
        assert "ask_clarification" not in names
        # task/workflow 编排工具整组被剥离（防循环派发/干预父任务）
        for t in _TASK_TOOLS:
            assert t.name not in names
        # 诚实终止通道被注入
        assert "abort_workflow" in names
        # Agent 自身配置的内建工具保留
        assert {"bash", "read", "write", "edit"} <= names

    def test_workflow_context_without_builtin_config_still_gets_abort(self):
        from app.engine.harness_integration.context import _resolve_builtin_tools

        names = _tool_names(_resolve_builtin_tools({}, "workflow"))
        assert "abort_workflow" in names
        assert "ask_clarification" not in names

    def test_chat_context_unchanged(self):
        from app.engine.agent.workflow_executor import _TASK_TOOLS
        from app.engine.harness_integration.context import _resolve_builtin_tools

        agent = {"builtin_config": []}
        names = _tool_names(_resolve_builtin_tools(agent, "chat"))

        # 聊天语义：ask_clarification + task 工具照常注入
        assert "ask_clarification" in names
        for t in _TASK_TOOLS:
            assert t.name in names
        assert "abort_workflow" not in names

    def test_default_context_is_chat(self):
        from app.engine.harness_integration.context import _resolve_builtin_tools

        names = _tool_names(_resolve_builtin_tools({}))
        assert "ask_clarification" in names
        assert "abort_workflow" not in names


# ---------------------------------------------------------------------------
# 2. Prompt 层
# ---------------------------------------------------------------------------


def _fake_agent(**extra) -> dict:
    agent = {
        "_id": "agent_01HTEST",
        "name": "Test Agent",
        "skill_ids": [],
        "knowledge_base_ids": [],
        "mcp_connection_ids": [],
        "workflow_ids": ["wf_demo"],
        "builtin_config": ["bash"],
    }
    agent.update(extra)
    return agent


class TestBuildToolDeclarationByContext:
    """build_tool_declaration 的声明与运行时工具集保持一致。"""

    @pytest.mark.asyncio
    async def test_workflow_context_declaration(self):
        from app.engine.agent.builder import build_tool_declaration

        decl = await build_tool_declaration(_fake_agent(), execution_context="workflow")

        # 无 Clarification 段（ask_clarification 已剥离）
        assert "Clarification" not in decl
        assert "ask_clarification" not in decl
        # 无 Task Management 段（_TASK_TOOLS 已剥离）
        assert "Task Management Tools" not in decl
        assert "dispatch_workflow" not in decl
        # Workflow 列表声明是给 dispatch 用的，一并省略
        assert "## Workflows" not in decl
        # 自主执行规则必须存在（禁反问 + abort_workflow）
        assert "Autonomous Execution" in decl
        assert "abort_workflow" in decl

    @pytest.mark.asyncio
    async def test_workflow_context_autonomous_section_not_gated_by_builtin(self):
        """Agent 未配置任何内建工具时，自主执行规则段仍必须注入。"""
        from app.engine.agent.builder import build_tool_declaration

        decl = await build_tool_declaration(
            _fake_agent(builtin_config=[]), execution_context="workflow",
        )
        assert "Autonomous Execution" in decl

    @pytest.mark.asyncio
    async def test_chat_context_declaration_unchanged(self):
        from app.engine.agent.builder import build_tool_declaration

        decl = await build_tool_declaration(_fake_agent(), execution_context="chat")

        assert "Clarification" in decl
        assert "ask_clarification" in decl
        assert "Task Management Tools" in decl
        assert "Autonomous Execution" not in decl

    @pytest.mark.asyncio
    async def test_slot_renderer_passthrough(self):
        """render_system_prompt_full 透传 execution_context。"""
        from app.engine.agent.slot_renderer import render_system_prompt_full

        # workflow_ids 置空：workflow 列表声明会查 Mongo，与本用例无关
        agent = _fake_agent(
            prompt_slots={"role": "分析师", "task": "生成日报"},
            workflow_ids=[],
        )
        text = await render_system_prompt_full(agent, execution_context="workflow")
        assert "Autonomous Execution" in text
        assert "Clarification" not in text

        text_chat = await render_system_prompt_full(agent)
        assert "Clarification" in text_chat


# ---------------------------------------------------------------------------
# 3. 执行层 —— AgentNodeExecutor 判定辅助
# ---------------------------------------------------------------------------


class _FakeInterrupt:
    """模拟 langgraph.types.Interrupt（payload 在 .value）。"""

    def __init__(self, value):
        self.value = value


class TestInterruptClassification:
    """cancelled interrupt vs 其他 HITL interrupt 的分流。"""

    def test_cancel_payload_recognised(self):
        from app.engine.workflow.node_executor import AgentNodeExecutor

        interrupts = [_FakeInterrupt({"reason": "cancelled"})]
        assert AgentNodeExecutor._interrupt_is_cancel(interrupts) is True

    def test_clarification_payload_not_cancel(self):
        from app.engine.workflow.node_executor import AgentNodeExecutor

        interrupts = [
            _FakeInterrupt({
                "question": "需要什么格式？", "type": "missing_info",
                "context": None, "options": None, "fields": None,
            }),
        ]
        assert AgentNodeExecutor._interrupt_is_cancel(interrupts) is False

    def test_bare_dict_payloads_supported(self):
        """防御性兼容裸 dict 形态。"""
        from app.engine.workflow.node_executor import AgentNodeExecutor

        assert AgentNodeExecutor._interrupt_is_cancel([{"reason": "cancelled"}]) is True
        assert AgentNodeExecutor._interrupt_is_cancel([{"type": "workflow_confirmation"}]) is False
        assert AgentNodeExecutor._interrupt_is_cancel(None) is False
        assert AgentNodeExecutor._interrupt_is_cancel([]) is False

    def test_summarise_interrupts(self):
        from app.engine.workflow.node_executor import AgentNodeExecutor

        summary = AgentNodeExecutor._summarise_interrupts(
            [_FakeInterrupt({"question": "目标受众是谁？", "type": "missing_info"})],
        )
        assert "目标受众是谁" in summary


class TestFindAbortRequest:
    """abort_workflow tool_call 扫描（确定性诚实终止信号）。"""

    def test_found_in_aimessage_like(self):
        from app.engine.workflow.node_executor import AgentNodeExecutor

        msg = SimpleNamespace(
            tool_calls=[
                {"name": "abort_workflow", "id": "call_abort_1",
                 "args": {"reason": "输入太泛化", "needed_info": "目标产品"}},
            ],
        )
        tool_result = SimpleNamespace(tool_call_id="call_abort_1")
        args = AgentNodeExecutor._find_abort_request([msg, tool_result])
        assert args == {"reason": "输入太泛化", "needed_info": "目标产品"}

    def test_found_in_dict_message(self):
        from app.engine.workflow.node_executor import AgentNodeExecutor

        msg = {
            "role": "assistant",
            "tool_calls": [{"name": "abort_workflow", "id": "call_abort_2",
                            "args": {"reason": "无法执行"}}],
        }
        tool_result = {"role": "tool", "tool_call_id": "call_abort_2"}
        assert AgentNodeExecutor._find_abort_request([msg, tool_result]) == {"reason": "无法执行"}

    def test_other_tool_calls_ignored(self):
        from app.engine.workflow.node_executor import AgentNodeExecutor

        msg = SimpleNamespace(tool_calls=[{"name": "bash", "args": {"command": "ls"}}])
        assert AgentNodeExecutor._find_abort_request([msg]) is None

    def test_unexecuted_abort_ignored(self):
        """仅声明的 abort（无匹配 ToolMessage，工具未执行）不算——
        恢复线程残留的未决 abort，agent 已重新决策。"""
        from app.engine.workflow.node_executor import AgentNodeExecutor

        msg = SimpleNamespace(
            tool_calls=[
                {"name": "abort_workflow", "id": "call_abort_3",
                 "args": {"reason": "无法执行"}},
            ],
        )
        assert AgentNodeExecutor._find_abort_request([msg]) is None

    def test_empty_messages(self):
        from app.engine.workflow.node_executor import AgentNodeExecutor

        assert AgentNodeExecutor._find_abort_request([]) is None
        assert AgentNodeExecutor._find_abort_request(None) is None


# ---------------------------------------------------------------------------
# 3b. 执行层 —— AgentNodeExecutor.execute 端到端（mock invoke）
# ---------------------------------------------------------------------------


def _run_execute(result_from_invoke: dict):
    """跑 AgentNodeExecutor.execute，mock DB/Workspace/invoke。

    Returns:
        NodeResult —— execute 的直接返回值。
    """
    import asyncio

    from app.engine.workflow.node_executor import AgentNodeExecutor

    executor = AgentNodeExecutor(
        node_id="node_agent_1",
        node_config={"agent_id": "agent_x", "input_query": "做个分析"},
    )
    variables = {"system": {"task_id": "task_1", "user_id": "user_1"}}

    agent_doc = _fake_agent(prompt_slots={"role": "分析师", "task": "生成日报"})

    mock_db = MagicMock()
    mock_db.__getitem__ = MagicMock(return_value=MagicMock(
        find_one=AsyncMock(return_value=agent_doc),
    ))
    mock_ws = SimpleNamespace(
        root="/tmp/ws", tmp_dir="/tmp/ws/tmp",
        input_dir="/tmp/ws/input", output_dir="/tmp/ws/output",
    )

    with patch("app.db.mongodb.get_database", return_value=mock_db), \
         patch(
             "app.engine.tool.workspace.WorkspaceManager.create_task_workspace",
             return_value=mock_ws,
         ), \
         patch(
             "app.engine.harness_integration.invoke",
             new=AsyncMock(return_value=result_from_invoke),
         ) as mock_invoke, \
         patch(
             "app.engine.workflow.node_executor.AgentNodeExecutor"
             "._register_task_output_files",
             new=AsyncMock(return_value=[]),
         ):
        result = asyncio.run(executor.execute(variables))
        # 工作流上下文必须显式传给 invoke
        assert mock_invoke.call_args.kwargs.get("execution_context") == "workflow"
    return result


class TestAgentNodeExecuteWorkflowContext:
    """execute 的三条新分支：取消/意外 HITL/诚实终止。"""

    def test_cancel_interrupt_keeps_agent_interrupted(self):
        result = _run_execute({
            "messages": [],
            "__interrupt__": (_FakeInterrupt({"reason": "cancelled"}),),
        })
        assert result.success is False
        assert result.error_code == "AGENT_INTERRUPTED"

    def test_unexpected_hitl_interrupt_fails_honestly(self):
        result = _run_execute({
            "messages": [],
            "__interrupt__": (
                _FakeInterrupt({"question": "要什么格式？", "type": "missing_info"}),
            ),
        })
        assert result.success is False
        # 不再被误判为可恢复取消
        assert result.error_code != "AGENT_INTERRUPTED"
        assert "无人值守" in result.error_message
        assert "要什么格式" in result.error_message

    def test_abort_workflow_fails_with_input_insufficient(self):
        result = _run_execute({
            "messages": [
                SimpleNamespace(content="无法继续"),
                SimpleNamespace(tool_calls=[
                    {"name": "abort_workflow", "id": "call_abort_e2e", "args": {
                        "reason": "输入过于泛化，无法确定分析对象",
                        "needed_info": "请指明目标产品与时间范围",
                    }},
                ]),
                {"role": "tool", "content": "终止请求已登记", "tool_call_id": "call_abort_e2e"},
            ],
        })
        assert result.success is False
        assert result.error_code == "AGENT_INPUT_INSUFFICIENT"
        assert "输入过于泛化" in result.error_message
        assert "目标产品" in result.error_message

    def test_normal_result_succeeds(self):
        result = _run_execute({
            "messages": [{"role": "assistant", "content": "分析完成"}],
            "usage": {"total_tokens": 10},
        })
        assert result.success is True
        assert result.output["response"] == "分析完成"


# ---------------------------------------------------------------------------
# 4. kb_search 超时
# ---------------------------------------------------------------------------


def _run_kb_search(kb_ids: list[str], retrieve_impl, timeout_ms=None):
    import asyncio

    from app.engine.workflow.nodes.kb_search import KbSearchNodeExecutor

    cfg = {"kb_ids": kb_ids, "query": "质量报告"}
    if timeout_ms is not None:
        cfg["timeout_ms"] = timeout_ms
    executor = KbSearchNodeExecutor(node_id="node_kb_1", node_config=cfg)
    with patch(
        "app.engine.kb.vector.retriever.retrieve", new=retrieve_impl,
    ):
        return asyncio.run(executor.execute({"system": {"task_id": "t"}}))


class TestKbSearchTimeout:
    """检索超时按失败收集，不再无限拖垮工作流。"""

    def test_timeout_surfaces_as_failure(self):
        async def slow_retrieve(kid, query, top_k=5):
            import asyncio
            await asyncio.sleep(10)
            return []

        result = _run_kb_search(["kb_1"], slow_retrieve, timeout_ms=1000)
        assert result.success is False
        assert result.error_code == "KB_SEARCH_FAILED"
        assert "超时" in result.error_message

    def test_partial_timeout_keeps_partial_results(self):
        async def retrieve(kid, query, top_k=5):
            import asyncio
            if kid == "kb_slow":
                await asyncio.sleep(10)
            return [{"text": "chunk", "score": 0.9}]

        # 两个 KB 均分节点超时 → 慢的那个超时，快的正常返回
        result = _run_kb_search(["kb_fast", "kb_slow"], retrieve, timeout_ms=2000)
        assert result.success is True
        assert len(result.output["results"]) == 1
        assert result.output["results"][0]["kb_id"] == "kb_fast"

    def test_normal_retrieve_unaffected(self):
        async def retrieve(kid, query, top_k=5):
            return [{"text": "chunk", "score": 0.9}]

        result = _run_kb_search(["kb_1"], retrieve)
        assert result.success is True
        assert result.output["results"][0]["kb_id"] == "kb_1"
