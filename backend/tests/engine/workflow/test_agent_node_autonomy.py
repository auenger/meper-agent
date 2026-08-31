"""工作流 agent 节点无人值守语义测试。

三层防护的单元测试：
1. 工具层（context._resolve_builtin_tools）——workflow 上下文剥离
   ask_clarification / _TASK_TOOLS，注入 abort_workflow；chat 上下文不变。
2. Prompt 层（builder.build_tool_declaration）——workflow 声明无
   Clarification / Task Management 段，有 Autonomous Execution 段；
   output_schema（opt-in）追加 Output Contract 段。
3. 执行层（AgentNodeExecutor）——interrupt payload 区分（cancelled vs
   其他 HITL）+ abort_workflow 调用扫描 → AGENT_INPUT_INSUFFICIENT
   （未配置 insufficient_branch 时）/ 信号化分支（配置时）+ 结构化输出
   契约解析（确定性校验 + 反馈重试一轮）。
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

        # workflow_ids 置空：workflow 列表声明会查 Mongo，与本用例无关
        # （CI 无 MongoDB，裸查会 ServerSelectionTimeout）
        decl = await build_tool_declaration(
            _fake_agent(workflow_ids=[]), execution_context="workflow",
        )

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
            _fake_agent(builtin_config=[], workflow_ids=[]), execution_context="workflow",
        )
        assert "Autonomous Execution" in decl

    @pytest.mark.asyncio
    async def test_chat_context_declaration_unchanged(self):
        from app.engine.agent.builder import build_tool_declaration

        # workflow_ids 置空：同上，避免查 Mongo
        decl = await build_tool_declaration(
            _fake_agent(workflow_ids=[]), execution_context="chat",
        )

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

    @pytest.mark.asyncio
    async def test_workflow_context_with_response_schema_appends_contract(self):
        """response_schema（opt-in）→ workflow 声明追加 Output Contract 段。"""
        from app.engine.agent.builder import build_tool_declaration

        schema = {
            "type": "object",
            "fields": [
                {"name": "status", "type": "enum", "required": True,
                 "enum_values": ["completed", "insufficient_info"]},
                {"name": "author", "type": "object", "fields": [
                    {"name": "name", "type": "string", "required": True},
                ]},
            ],
        }
        decl = await build_tool_declaration(
            _fake_agent(workflow_ids=[]),
            execution_context="workflow",
            response_schema=schema,
        )
        assert "## Output Contract" in decl
        assert "`status`" in decl
        assert '"completed"' in decl
        # 嵌套两层渲染：第一层 author + 子字段 name
        assert "`author`" in decl
        assert "nested object fields" in decl
        assert "`name`" in decl
        # 契约段在自主执行规则之后（行为契约 → 输出契约）
        assert decl.index("Autonomous Execution") < decl.index("Output Contract")

    @pytest.mark.asyncio
    async def test_array_response_schema_contract(self):
        from app.engine.agent.builder import build_tool_declaration

        schema = {"type": "array", "fields": [{"name": "title", "type": "string"}]}
        decl = await build_tool_declaration(
            _fake_agent(workflow_ids=[]),
            execution_context="workflow",
            response_schema=schema,
        )
        assert "Output Contract" in decl
        assert "JSON array" in decl

    @pytest.mark.asyncio
    async def test_list_fields_rendered(self):
        """is_list 字段渲染：string list / object list（each element）。"""
        from app.engine.agent.builder import build_tool_declaration

        schema = {"type": "object", "fields": [
            {"name": "tags", "type": "string", "is_list": True},
            {"name": "authors", "type": "object", "is_list": True, "fields": [
                {"name": "name", "type": "string"},
            ]},
        ]}
        decl = await build_tool_declaration(
            _fake_agent(workflow_ids=[]),
            execution_context="workflow",
            response_schema=schema,
        )
        assert "(string list," in decl
        assert "(object list," in decl
        assert "each list element is an object with fields:" in decl

    @pytest.mark.asyncio
    async def test_chat_context_never_gets_output_contract(self):
        """chat 上下文不注入 Output Contract（自由文本对话语义）。"""
        from app.engine.agent.builder import build_tool_declaration

        decl = await build_tool_declaration(
            _fake_agent(workflow_ids=[]),
            execution_context="chat",
            response_schema={"type": "object",
                             "fields": [{"name": "status", "type": "string"}]},
        )
        assert "Output Contract" not in decl

    @pytest.mark.asyncio
    async def test_workflow_context_without_schema_no_contract_section(self):
        from app.engine.agent.builder import build_tool_declaration

        decl = await build_tool_declaration(
            _fake_agent(workflow_ids=[]), execution_context="workflow",
        )
        assert "Output Contract" not in decl


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


def _run_execute_rich(
    result_from_invoke: dict | None = None,
    *,
    node_config: dict | None = None,
    invoke_side_effect: list | None = None,
):
    """跑 AgentNodeExecutor.execute，mock DB/Workspace/invoke。

    Args:
        result_from_invoke: invoke 的固定返回值（与 invoke_side_effect 二选一）。
        node_config: 节点配置覆写（默认 agent_id + input_query）。
        invoke_side_effect: invoke 的逐次返回值列表（反馈重试等多轮场景）。

    Returns:
        (NodeResult, mock_invoke) —— execute 的直接返回值与 invoke mock
        （供断言调用次数 / 逐次 messages）。
    """
    import asyncio

    from app.engine.workflow.node_executor import AgentNodeExecutor

    executor = AgentNodeExecutor(
        node_id="node_agent_1",
        node_config=node_config or {"agent_id": "agent_x", "input_query": "做个分析"},
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

    if invoke_side_effect is not None:
        mock_invoke = AsyncMock(side_effect=list(invoke_side_effect))
    else:
        mock_invoke = AsyncMock(return_value=result_from_invoke)

    with patch("app.db.mongodb.get_database", return_value=mock_db), \
         patch(
             "app.engine.tool.workspace.WorkspaceManager.create_task_workspace",
             return_value=mock_ws,
         ), \
         patch("app.engine.harness_integration.invoke", new=mock_invoke), \
         patch(
             "app.engine.workflow.node_executor.AgentNodeExecutor"
             "._register_task_output_files",
             new=AsyncMock(return_value=[]),
         ):
        result = asyncio.run(executor.execute(variables))
        # 工作流上下文必须显式传给 invoke
        assert mock_invoke.call_args.kwargs.get("execution_context") == "workflow"
    return result, mock_invoke


def _run_execute(result_from_invoke: dict):
    """跑 AgentNodeExecutor.execute，mock DB/Workspace/invoke。

    Returns:
        NodeResult —— execute 的直接返回值。
    """
    result, _ = _run_execute_rich(result_from_invoke)
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
# 3c. 执行层 —— Agent 节点输出提取（response 恒为纯文本正文）
# ---------------------------------------------------------------------------

# GLM anthropic-compat quirk：无视 thinking disabled，把最终回答塞进
# thinking 块的额外 text 字段（anthropic SDK extra="allow" 放行）。
_GLM_QUIRK_BLOCK = {
    "signature": "b6ff287f29a4407e9b68ebee",
    "thinking": "Vague but can proceed.",
    "type": "thinking",
    "text": "好的！这是最终回答。",
}


class TestAgentNodeOutputExtraction:
    """regression: response 不再泄漏 thinking 块原始字段。

    此前 node_executor 直接存 last_msg.content，GLM quirk 块被前端
    KeyValueGrid 逐行渲染成 signature/thinking/type/text 裸字段。
    """

    def test_glm_quirk_block_yields_plain_answer_and_keeps_thinking(self):
        result = _run_execute({
            "messages": [SimpleNamespace(content=[dict(_GLM_QUIRK_BLOCK)])],
        })
        assert result.success is True
        assert result.output["response"] == "好的！这是最终回答。"
        # 网关违规返回的思考过程保留到节点输出
        assert result.output["thinking"] == "Vague but can proceed."

    def test_standard_anthropic_blocks(self):
        result = _run_execute({
            "messages": [SimpleNamespace(content=[
                {"type": "thinking", "thinking": "先推理", "signature": "sig"},
                {"type": "text", "text": "正文回答"},
            ])],
        })
        assert result.success is True
        assert result.output["response"] == "正文回答"
        assert result.output["thinking"] == "先推理"

    def test_plain_string_content_no_thinking_key(self):
        result = _run_execute({
            "messages": [{"role": "assistant", "content": "纯文本"}],
        })
        assert result.success is True
        assert result.output["response"] == "纯文本"
        assert "thinking" not in result.output

    def test_thinking_only_content_empty_response(self):
        result = _run_execute({
            "messages": [SimpleNamespace(content=[
                {"type": "thinking", "thinking": "只有思考", "signature": "s"},
            ])],
        })
        assert result.success is True
        assert result.output["response"] == ""
        assert result.output["thinking"] == "只有思考"

    def test_signature_never_reaches_output(self):
        result = _run_execute({
            "messages": [SimpleNamespace(content=[dict(_GLM_QUIRK_BLOCK)])],
        })
        dumped = str(result.output)
        assert "signature" not in dumped
        assert "b6ff287f" not in dumped


# ---------------------------------------------------------------------------
# 3d. 执行层 —— abort_workflow 信号化（insufficient_branch，opt-in）
# ---------------------------------------------------------------------------

_ABORT_MESSAGES = [
    SimpleNamespace(content="无法继续"),
    SimpleNamespace(tool_calls=[
        {"name": "abort_workflow", "id": "call_abort_sig", "args": {
            "reason": "输入过于泛化，无法确定分析对象",
            "needed_info": "请指明目标产品与时间范围",
        }},
    ]),
    {"role": "tool", "content": "终止请求已登记", "tool_call_id": "call_abort_sig"},
]


class TestAbortSignalBranch:
    """配置 insufficient_branch 时 abort 转为可路由信号而非硬失败。"""

    def test_abort_routes_to_insufficient_branch(self):
        result, _ = _run_execute_rich(
            {"messages": _ABORT_MESSAGES},
            node_config={"agent_id": "agent_x", "input_query": "x",
                         "insufficient_branch": "node_human_clarify"},
        )
        assert result.success is True
        assert result.selected_branch == "node_human_clarify"
        assert result.output["status"] == "insufficient"
        # 恒定结构：字段集与正常分支一致（API 返回体承诺，下游永不踩空）
        assert set(result.output.keys()) == {
            "status", "response", "agent_id", "files", "usage", "needed_info",
        }
        assert result.output["files"] == []
        assert result.output["usage"] == {}
        # reason/needed_info 进 response，澄清分支可引用展示
        assert "输入过于泛化" in result.output["response"]
        assert result.output["needed_info"] == "请指明目标产品与时间范围"

    def test_abort_without_branch_still_fails_hard(self):
        """未配置 insufficient_branch → 维持诚实硬失败（现状回归）。"""
        result, _ = _run_execute_rich(
            {"messages": _ABORT_MESSAGES},
            node_config={"agent_id": "agent_x", "input_query": "x"},
        )
        assert result.success is False
        assert result.error_code == "AGENT_INPUT_INSUFFICIENT"
        assert result.selected_branch is None
        assert result.output == {}


# ---------------------------------------------------------------------------
# 3e. 执行层 —— response 结构契约（response_schema，opt-in）
# ---------------------------------------------------------------------------

_OBJECT_SCHEMA = {
    "type": "object",
    "fields": [
        {"name": "status", "type": "enum", "required": True,
         "enum_values": ["completed", "insufficient_info"]},
        {"name": "summary", "type": "string", "required": True},
    ],
}


class TestParseStructuredOutput:
    """_parse_structured_output：确定性 JSON 校验（无启发式）。"""

    def _parse(self, response: str, schema: dict | None = None):
        from app.engine.workflow.node_executor import AgentNodeExecutor

        return AgentNodeExecutor._parse_structured_output(
            response, schema or _OBJECT_SCHEMA,
        )

    def test_bare_json_object(self):
        parsed, err = self._parse('{"status": "completed", "summary": "完成"}')
        assert parsed == {"status": "completed", "summary": "完成"}
        assert err == ""

    def test_fenced_json(self):
        parsed, err = self._parse(
            '```json\n{"status": "completed", "summary": "完成"}\n```',
        )
        assert parsed is not None and parsed["status"] == "completed"

    def test_json_with_surrounding_prose(self):
        parsed, err = self._parse(
            '好的，结果如下：{"status": "completed", "summary": "完成"} 以上。',
        )
        assert parsed is not None and parsed["summary"] == "完成"

    def test_missing_required_field(self):
        parsed, err = self._parse('{"status": "completed"}')
        assert parsed is None
        assert "summary" in err

    def test_enum_violation(self):
        parsed, err = self._parse('{"status": "done", "summary": "x"}')
        assert parsed is None
        assert "枚举" in err

    def test_type_violation(self):
        parsed, err = self._parse('{"status": "completed", "summary": 123}')
        assert parsed is None
        assert "string" in err

    def test_unknown_fields_tolerated(self):
        parsed, err = self._parse(
            '{"status": "completed", "summary": "x", "extra": "无害"}',
        )
        assert parsed is not None and parsed["extra"] == "无害"

    def test_non_json_output(self):
        parsed, err = self._parse("分析完成，报告如上。")
        assert parsed is None
        assert "无法从输出中解析出 JSON" in err

    def test_empty_output(self):
        parsed, err = self._parse("")
        assert parsed is None
        assert "为空" in err

    def test_array_top_level(self):
        """array 契约：顶层 JSON 数组，逐元素校验。"""
        schema = {"type": "array", "fields": [
            {"name": "title", "type": "string", "required": True},
        ]}
        parsed, err = self._parse('[{"title": "a"}, {"title": "b"}]', schema)
        assert parsed == [{"title": "a"}, {"title": "b"}]
        assert err == ""

    def test_array_element_violation(self):
        schema = {"type": "array", "fields": [
            {"name": "title", "type": "string", "required": True},
        ]}
        parsed, err = self._parse('[{"title": "a"}, {}]', schema)
        assert parsed is None
        assert "[1]" in err and "title" in err

    def test_array_contract_rejects_object(self):
        schema = {"type": "array", "fields": [{"name": "title", "type": "string"}]}
        parsed, err = self._parse('{"title": "a"}', schema)
        assert parsed is None
        assert "数组" in err

    def test_nested_object_two_levels(self):
        """嵌套两层：第一层 object 字段的子字段校验。"""
        schema = {"type": "object", "fields": [
            {"name": "author", "type": "object", "required": True, "fields": [
                {"name": "name", "type": "string", "required": True},
                {"name": "email", "type": "string"},
            ]},
        ]}
        parsed, err = self._parse(
            '{"author": {"name": "张三", "email": "z@x.com"}}', schema,
        )
        assert parsed is not None and parsed["author"]["name"] == "张三"

        parsed, err = self._parse('{"author": {"email": "z@x.com"}}', schema)
        assert parsed is None
        assert "author.name" in err  # 错误信息带嵌套路径

    def test_list_of_string(self):
        """列表字段：string 列表拆包逐元素校验。"""
        schema = {"type": "object", "fields": [
            {"name": "tags", "type": "string", "is_list": True, "required": True},
        ]}
        parsed, err = self._parse('{"tags": ["a", "b"]}', schema)
        assert parsed is not None and parsed["tags"] == ["a", "b"]

        parsed, err = self._parse('{"tags": "a"}', schema)
        assert parsed is None
        assert "列表" in err

    def test_list_of_object(self):
        """object 列表：逐元素按 fields（第二层）校验。"""
        schema = {"type": "object", "fields": [
            {"name": "authors", "type": "object", "is_list": True, "fields": [
                {"name": "name", "type": "string", "required": True},
            ]},
        ]}
        parsed, err = self._parse('{"authors": [{"name": "a"}, {"name": "b"}]}', schema)
        assert parsed is not None

        parsed, err = self._parse('{"authors": [{"name": "a"}, {}]}', schema)
        assert parsed is None
        assert "authors[1]" in err

    def test_second_level_scalar_list(self):
        """第二层标量列表允许；任意层的 object 列表均支持。"""
        schema = {"type": "object", "fields": [
            {"name": "doc", "type": "object", "fields": [
                {"name": "keywords", "type": "string", "is_list": True},
            ]},
        ]}
        parsed, err = self._parse('{"doc": {"keywords": ["k1", "k2"]}}', schema)
        assert parsed is not None and parsed["doc"]["keywords"] == ["k1", "k2"]

    def test_deep_nested_object_list(self):
        """多层嵌套 + 任意层对象列表：{"report": {"sections": [{"title": ...}]}}。"""
        schema = {"type": "object", "fields": [
            {"name": "report", "type": "object", "fields": [
                {"name": "sections", "type": "object", "is_list": True, "fields": [
                    {"name": "title", "type": "string", "required": True},
                ]},
            ]},
        ]}
        parsed, err = self._parse(
            '{"report": {"sections": [{"title": "a"}, {"title": "b"}]}}', schema,
        )
        assert parsed is not None
        assert parsed["report"]["sections"][1]["title"] == "b"

        parsed, err = self._parse(
            '{"report": {"sections": [{"title": "a"}, {}]}}', schema,
        )
        assert parsed is None
        assert "sections[1]" in err and "title" in err


class TestNormalizeResponseSchema:
    """response_schema 归一化（含旧格式转译）。"""

    def _normalize(self, node_config: dict):
        from app.engine.workflow.node_executor import _normalize_response_schema

        return _normalize_response_schema(node_config)

    def test_none_when_unset(self):
        assert self._normalize({}) is None
        # text 等价未声明（零配置，行为不变）
        assert self._normalize({"response_schema": {"type": "text"}}) is None
        assert self._normalize({"response_schema": None}) is None

    def test_valid_object_schema(self):
        schema = self._normalize({"response_schema": _OBJECT_SCHEMA})
        assert schema is not None
        assert schema["type"] == "object"
        assert [f["name"] for f in schema["fields"]] == ["status", "summary"]
        assert schema["fields"][0]["is_list"] is False  # 归一化补默认值

    def test_legacy_output_schema_translated(self):
        """旧 output_schema（未发布格式）转译为 {type: object, fields}。"""
        legacy = [
            {"name": "status", "type": "enum", "enum_values": ["a", "b"]},
        ]
        schema = self._normalize({"output_schema": legacy})
        assert schema is not None
        assert schema["type"] == "object"
        assert [f["name"] for f in schema["fields"]] == ["status"]
        assert schema["fields"][0]["enum_values"] == ["a", "b"]

    def test_legacy_default_output_variables_ignored(self):
        """旧 output_variables（前端自动初始化的 [response]）视同未声明。"""
        schema = self._normalize({
            "output_variables": [{"name": "response", "type": "text"}],
        })
        assert schema is None

    def test_invalid_fields_dropped(self):
        """非法字段（名/类型不合法）运行时宽容跳过。"""
        schema = self._normalize({"response_schema": {
            "type": "object",
            "fields": [
                {"name": "ok", "type": "string"},
                {"name": "bad-name", "type": "string"},   # 非法标识符
                {"name": "bad", "type": "object-typo"},   # 非法类型
            ],
        }})
        assert schema is not None
        assert [f["name"] for f in schema["fields"]] == ["ok"]

    def test_deep_nesting_beyond_limit_stripped(self):
        """嵌套可继续深入；超过防御上限（5 层）在归一化时剥离。"""
        # 三层：保留
        schema = self._normalize({"response_schema": {
            "type": "object",
            "fields": [
                {"name": "a", "type": "object", "fields": [
                    {"name": "b", "type": "object", "fields": [
                        {"name": "c", "type": "string"},
                    ]},
                ]},
            ],
        }})
        third = schema["fields"][0]["fields"][0]["fields"][0]
        assert third["name"] == "c"

        # 六层：第六层被剥离（_MAX_SCHEMA_DEPTH = 5）
        deep = {"name": "x6", "type": "string"}
        node = {"name": "x5", "type": "object", "fields": [deep]}
        for _ in range(4):
            node = {"name": "up", "type": "object", "fields": [node]}
        schema = self._normalize({"response_schema": {
            "type": "object", "fields": [node],
        }})
        walk = schema["fields"][0]
        for _ in range(4):
            walk = walk["fields"][0]
        assert walk["type"] == "object"
        assert walk.get("fields") == []  # 第六层被剥离

    def test_is_list_preserved(self):
        """is_list 标志归一化透传（bool 化）。"""
        schema = self._normalize({"response_schema": {
            "type": "object",
            "fields": [
                {"name": "tags", "type": "string", "is_list": True},
                {"name": "title", "type": "string", "is_list": "yes"},  # 非 bool 宽容
            ],
        }})
        assert schema["fields"][0]["is_list"] is True
        assert schema["fields"][1]["is_list"] is True


class TestStructuredOutputEndToEnd:
    """配置 response_schema 的 execute 全路径：合规/反馈重试/二次违规。"""

    def _config(self) -> dict:
        return {
            "agent_id": "agent_x", "input_query": "x",
            "response_schema": _OBJECT_SCHEMA,
        }

    def test_compliant_json_response_is_native_dict(self):
        result, mock_invoke = _run_execute_rich(
            {"messages": [{"role": "assistant",
                           "content": '{"status": "completed", "summary": "报告完成"}'}]},
            node_config=self._config(),
        )
        assert result.success is True
        # response 是解析后的原生 dict（无 parsed 字段，无深解析依赖）
        assert result.output["response"] == {
            "status": "completed", "summary": "报告完成",
        }
        assert "parsed" not in result.output
        # 固定字段恒定
        assert result.output["status"] == "ok"
        assert result.output["needed_info"] == ""
        assert mock_invoke.call_count == 1

    def test_system_prompt_contains_output_contract(self):
        _, mock_invoke = _run_execute_rich(
            {"messages": [{"role": "assistant",
                           "content": '{"status": "completed", "summary": "ok"}'}]},
            node_config=self._config(),
        )
        first_state = mock_invoke.call_args_list[0].args[1]
        system_msg = first_state["messages"][0]
        assert "Output Contract" in system_msg.content
        assert "`status`" in system_msg.content
        assert "insufficient_info" in system_msg.content

    def test_violation_triggers_feedback_retry_then_success(self):
        """第一轮非 JSON → 同 thread 反馈重试 → 第二轮合规。"""
        result, mock_invoke = _run_execute_rich(
            invoke_side_effect=[
                {"messages": [{"role": "assistant", "content": "分析完成，报告如上。"}]},
                {"messages": [{"role": "assistant",
                               "content": '{"status": "completed", "summary": "报告完成"}'}]},
            ],
            node_config=self._config(),
        )
        assert result.success is True
        assert result.output["response"]["status"] == "completed"
        assert mock_invoke.call_count == 2
        # 反馈轮：同 thread（session_id 不变）+ 只带一条反馈 HumanMessage
        # （历史由 checkpointer 追加，不重复 SystemMessage）
        first_state = mock_invoke.call_args_list[0].args[1]
        second_state = mock_invoke.call_args_list[1].args[1]
        assert second_state["session_id"] == first_state["session_id"]
        retry_messages = second_state["messages"]
        assert len(retry_messages) == 1
        assert "response JSON 契约" in retry_messages[0].content

    def test_second_violation_fails_with_schema_violation_code(self):
        result, mock_invoke = _run_execute_rich(
            invoke_side_effect=[
                {"messages": [{"role": "assistant", "content": "不是 JSON"}]},
                {"messages": [{"role": "assistant", "content": "还是不是 JSON"}]},
            ],
            node_config=self._config(),
        )
        assert result.success is False
        assert result.error_code == "AGENT_OUTPUT_SCHEMA_VIOLATION"
        assert "response_schema" in result.error_message
        assert mock_invoke.call_count == 2  # 反馈重试固定 1 次，不死循环

    def test_no_schema_behaviour_unchanged(self):
        """未配置 response_schema：纯文本输出照常成功、response 为原文。"""
        result, mock_invoke = _run_execute_rich(
            {"messages": [{"role": "assistant", "content": "分析完成，报告如上。"}]},
            node_config={"agent_id": "agent_x", "input_query": "x"},
        )
        assert result.success is True
        assert result.output["response"] == "分析完成，报告如上。"
        assert result.output["status"] == "ok"
        assert mock_invoke.call_count == 1

    def test_normal_output_constant_field_set(self):
        """正常分支固定字段集 = API 返回体承诺（thinking 非空才写）。"""
        result, _ = _run_execute_rich(
            {"messages": [{"role": "assistant", "content": "done"}],
             "usage": {"total_tokens": 5}},
            node_config={"agent_id": "agent_x", "input_query": "x"},
        )
        assert set(result.output.keys()) == {
            "status", "response", "agent_id", "files", "usage", "needed_info",
        }


# ---------------------------------------------------------------------------
# 3f. engine 层 —— selected_branch 互斥分支（从 gateway-only 通用化）
# ---------------------------------------------------------------------------


class TestSelectedBranchRouting:
    """任意执行器返回 selected_branch 时，engine 只执行该分支。"""

    def test_agent_abort_signal_routes_to_branch_only(self):
        """agent abort 信号 → 只走 insufficient_branch，next_nodes 的
        常规下游不执行（互斥，防"未真正执行的结果"流入常规链路）。"""
        import asyncio

        from app.engine.workflow.engine import WorkflowEngine
        from app.engine.workflow.node_executor import NodeResult

        engine = WorkflowEngine()
        engine._task_id = "task_t"
        engine._completed_nodes = set()
        engine._pool = MagicMock()

        engine._node_map = {
            "agent1": {
                "node_id": "agent1", "type": "agent",
                "config": {"agent_id": "a1", "insufficient_branch": "human1",
                           "next_nodes": [{"target": "end1"}]},
            },
            "human1": {"node_id": "human1", "type": "human", "config": {}},
            "end1": {"node_id": "end1", "type": "end", "config": {}},
        }

        agent_exec = MagicMock()
        agent_exec.execute = AsyncMock(return_value=NodeResult(
            success=True,
            output={"response": "输入不足", "sufficiency": "insufficient"},
            selected_branch="human1",
        ))
        human_exec = MagicMock()
        human_exec.execute = AsyncMock(return_value=NodeResult(
            success=True, output={"status": "done"},
        ))
        end_exec = MagicMock()
        end_exec.execute = AsyncMock(return_value=NodeResult(
            success=True, output={},
        ))

        def fake_get_executor(node_type, node_id, config):
            return {"agent1": agent_exec, "human1": human_exec, "end1": end_exec}[node_id]

        with patch.object(engine, "_check_cancelled", new_callable=AsyncMock), \
             patch(
                 "app.engine.workflow.engine.TaskService.append_timeline_event",
                 new_callable=AsyncMock,
             ), \
             patch(
                 "app.engine.workflow.engine.get_node_executor",
                 side_effect=fake_get_executor,
             ):
            result = asyncio.run(engine._execute_node("agent1"))

        assert result is not None and result.selected_branch == "human1"
        agent_exec.execute.assert_awaited_once()
        human_exec.execute.assert_awaited_once()
        # 互斥：next_nodes 里的常规下游不执行
        end_exec.execute.assert_not_awaited()


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
