"""Agent prompt assembly + tool resolution + preview.

This module was originally the old-engine graph builder. After harness
became the sole execution engine (USE_HARNESS_ENGINE removed), the graph
construction code was deleted. What remains are the functions still used
by the API layer:

* ``build_system_prompt`` — render the 5-slot system prompt + tool declarations
* ``build_tool_declaration`` — generate the 5-section tool declaration text
* ``preview_agent`` — dry-run assembly (no LLM call) for debugging

The harness integration layer (``harness_integration.resolve_harness_context``)
uses ``get_llm_client`` and ``get_context_window_async`` directly, not via
this module.
"""
from __future__ import annotations

import json

from loguru import logger

from app.models.compat import (
    resolve_default_model,
    resolve_skill_ids,  # noqa: F401 (used by build_tool_declaration)
)

# ---------------------------------------------------------------------------
# System prompt + tool declaration (used by stream / invoke / preview)
# ---------------------------------------------------------------------------


async def build_skill_declaration(tool_ids: list[str], exclude_names: set[str] | None = None) -> str:
    """Build the Skill declaration text for the system prompt.

    Queries MongoDB for each tool ID and formats a markdown list
    of available Skills (name + description).

    exclude_names：与用户个人技能同名的官方技能被遮蔽（§7.4 个人优先解析），
    从官方声明中剔除，避免 prompt 里同名技能出现两份互相矛盾的描述。
    """
    if not tool_ids:
        return ""

    from app.services.tool_service import ToolService

    skill_docs = await ToolService.get_tools_by_ids(tool_ids)
    if not skill_docs:
        return ""

    lines = [
        "",
        "## Available Skills",
        "",
        "You have access to the following skills. When you need to use one, call the `load_skill` tool with the skill name.",
        "",
        "After loading a skill, you can access auxiliary files (scripts, templates, etc.) via the skill base path shown in the instructions, using your `read` or `bash` tools.",
        "",
    ]
    for doc in skill_docs:
        name = doc.get("name", "unknown")
        if exclude_names and name in exclude_names:
            continue
        desc = doc.get("description", "")
        lines.append(f"- **{name}**: {desc}")

    return "\n".join(lines)


async def build_tool_declaration(
    agent: dict,
    exclude_names: set[str] | None = None,
    *,
    execution_context: str = "chat",
    response_schema: dict | None = None,
    output_schema: list[dict] | None = None,
) -> str:
    """Build the complete tool declaration text for the system prompt.

    Generates declaration sections for all tool categories:
    - Skills (on-demand via load_skill)
    - Knowledge Bases (kb_search / kb_glob / kb_grep / kb_read)
    - MCP tools (directly callable)
    - Workflow list (listed for reference, triggered via propose/dispatch)
    - Built-in tools (directly callable)
    - Task tools (always available)

    execution_context="workflow"（工作流 agent 节点，无人值守）时：
    - Workflow 列表与 Task Management 段不生成（这些工具运行时已剥离，
      防 dispatch 循环派发/干预父任务，声明与工具集保持一致）
    - Built-in 段追加 Autonomous Execution 规则（禁反问 + abort_workflow）
    - 配置了 response_schema 时追加 Output Contract 段（response 的结构
      契约——API 返回体心智：固定字段引擎填，response 结构用户声明，
      最终回复必须是符合契约的 JSON）
    """
    sections: list[str] = []

    # 废弃参数兼容：旧调用方传 output_schema(list) → 转译 response_schema。
    # 主要防热重载进程中新旧模块混载时旧调用方直接炸 unexpected keyword。
    if response_schema is None and output_schema:
        logger.warning(
            "build_tool_declaration: output_schema 参数已废弃，请改用 response_schema",
        )
        response_schema = {"type": "object", "fields": output_schema}

    skill_ids = resolve_skill_ids(agent)
    if skill_ids:
        skill_decl = await build_skill_declaration(skill_ids, exclude_names=exclude_names)
        if skill_decl:
            sections.append(skill_decl)

    kb_ids = agent.get("knowledge_base_ids") or []
    if kb_ids:
        kb_decl = await _build_kb_declaration(kb_ids)
        if kb_decl:
            sections.append(kb_decl)

    mcp_connection_ids = agent.get("mcp_connection_ids") or []
    if mcp_connection_ids:
        mcp_decl = await _build_mcp_tool_declaration(mcp_connection_ids)
        if mcp_decl:
            sections.append(mcp_decl)

    if execution_context != "workflow":
        # Workflow 列表是给 chat 语义的 dispatch_workflow 用的；
        # 工作流上下文没有该工具，声明一并省略。
        workflow_ids = agent.get("workflow_ids") or []
        if workflow_ids:
            workflow_decl = await _build_workflow_tool_declaration(workflow_ids)
            if workflow_decl:
                sections.append(workflow_decl)

    builtin_config = agent.get("builtin_config") or []
    if builtin_config:
        builtin_decl = _build_builtin_tool_declaration(
            builtin_config, execution_context=execution_context,
        )
        if builtin_decl:
            sections.append(builtin_decl)

    if execution_context == "workflow":
        # 自主执行规则是行为契约（禁反问 + 诚实终止），不依赖 builtin_config，
        # 无论 Agent 配了哪些内建工具都必须注入。
        sections.append("\n".join(_build_autonomous_execution_section()))
        # response 结构契约（opt-in）：agent 输出是类 API 返回体——固定字段
        # （status/files/usage/...）由引擎填充，response 的结构由用户声明；
        # 最终回复 = response 的值，必须是符合契约的 JSON。
        if response_schema and response_schema.get("type") in ("object", "array"):
            sections.append("\n".join(_build_output_contract_section(response_schema)))

    if execution_context != "workflow":
        # task/workflow 编排工具仅聊天上下文注入，工作流上下文已剥离。
        task_decl = _build_task_tool_declaration()
        sections.append(task_decl)

    sections.append(_build_chart_tool_declaration())

    return "\n".join(sections) if sections else ""


def _build_chart_tool_declaration() -> str:
    """Build the data visualization (render_chart) declaration section."""
    lines = [
        "",
        "## Data Visualization Tools",
        "",
        "You have access to the following chart tool. When data would be clearer",
        "as a picture than as text or a table — comparisons, trends, proportions,",
        "or scatter correlations — call it instead of printing raw numbers.",
        "",
        "- **render_chart(type, data, title?, chart_config?)**: Generate an echarts chart",
        "  (bar/line/area/pie/scatter) that renders inline in the chat. For pie pass",
        "  `{names: [...], values: [...]}`; for other types pass",
        "  `{categories: [...], series: [{name, data: [...]}]}`. The chart renders",
        "  directly from this tool's result — do NOT write the option JSON to a file",
        "  or call write_to_output afterwards.",
        "",
    ]
    return "\n".join(lines)


async def _build_kb_declaration(kb_ids: list[str]) -> str:
    """Build knowledge base declaration section for the system prompt.

    Lists the bound KBs (name + type + description) and explains the
    available KB tools so the LLM knows what it can search and how.
    """
    from app.db.mongodb import get_database

    kb_docs = await get_database()["knowledge_bases"].find(
        {"_id": {"$in": kb_ids}}
    ).to_list(len(kb_ids))
    if not kb_docs:
        return ""

    has_tree = any(d.get("type", "tree") == "tree" for d in kb_docs)
    has_vector = any(d.get("type") == "vector" for d in kb_docs)

    lines = [
        "",
        "## Knowledge Bases",
        "",
        "You have access to the following knowledge bases. Use the KB tools to search them.",
        "",
    ]

    for doc in kb_docs:
        name = doc.get("name", "unknown")
        desc = doc.get("description", "")
        kb_type = doc.get("type", "tree")
        kb_id = doc.get("_id", "")
        type_label = "向量" if kb_type == "vector" else "树形"
        lines.append(f"- **{name}** ({type_label}, id: `{kb_id}`): {desc}")
    lines.append("")

    if has_vector:
        lines.extend([
            "### 向量知识库 (kb_search)",
            "",
            "Use `kb_search(query, kb_ids?, top_k?)` for **semantic search** across vector KBs.",
            "Pass `kb_ids` to search specific KBs, or omit to search all bound vector KBs.",
            "Best for: large documents, FAQs, product manuals, semantic Q&A.",
            "",
        ])

    if has_tree:
        lines.extend([
            "### 树形知识库 (kb_glob / kb_grep / kb_read)",
            "",
            "- `kb_glob(pattern, kb_ids?)` — list .md files matching a glob pattern.",
            "- `kb_grep(pattern, kb_ids?)` — search file contents by regex.",
            "- `kb_read(path, kb_id?)` — read a single .md file's content.",
            "",
        ])

    return "\n".join(lines)


async def build_system_prompt(agent_doc: dict, exclude_skill_names: set[str] | None = None) -> str:
    """Build the fully assembled system prompt for an Agent.

    Delegates to the slot renderer which handles PromptTemplate-based
    prompt composition. exclude_skill_names 透传给技能声明——
    被用户个人技能遮蔽的同名官方技能不进官方列表（§7.4）。
    """
    from app.engine.agent.slot_renderer import render_system_prompt_full

    return await render_system_prompt_full(agent_doc, exclude_skill_names=exclude_skill_names)


async def _build_mcp_tool_declaration(mcp_connection_ids: list[str]) -> str:
    """Build MCP tool declaration section for the system prompt."""
    from app.db.mongodb import get_database
    from app.services.mcp_connection_service import McpConnectionService

    lines = [
        "",
        "## MCP Tools",
        "",
        "You have access to the following MCP tools. Call them directly by name with the required arguments.",
        "",
    ]

    total_tools = 0
    for conn_id in mcp_connection_ids:
        conn_doc = await McpConnectionService.get_connection(conn_id)
        if conn_doc is None:
            continue

        conn_name = conn_doc.get("name", "Unknown")
        col = get_database()["tools"]
        cursor = col.find({
            "mcp_connection_id": conn_id,
            "source": "mcp",
        })
        tool_docs = await cursor.to_list(length=100)

        if not tool_docs:
            continue

        lines.append(f"### {conn_name}")
        lines.append("")
        for doc in tool_docs:
            name = doc.get("name", "unknown")
            desc = doc.get("description", "")
            lines.append(f"- **{name}**: {desc}")
            total_tools += 1
        lines.append("")

    return "\n".join(lines) if total_tools > 0 else ""


async def _build_workflow_tool_declaration(workflow_ids: list[str]) -> str:
    """Build workflow declaration section for the system prompt.

    Reads the actual Workflow definition to extract the Start node's
    ``output_variables`` and show the exact parameter names the LLM
    should pass to ``dispatch_workflow``.
    """
    from app.services.workflow_registry_service import WorkflowRegistryService
    from app.services.workflow_service import WorkflowService

    lines = [
        "",
        "## Available Workflows",
        "",
        "When a workflow matches the user's request:",
        "",
        "1. Call ``confirm_workflow(workflow_name, description, params)`` to",
        "   show the user a confirmation card. Execution pauses until the user",
        "   confirms or rejects. The tool returns the user's decision — do NOT",
        "   add any follow-up text in the same turn, the card speaks for itself.",
        "",
        "2. When the user confirms (the tool returns a confirmation), call",
        "   ``dispatch_workflow(workflow_name, params)`` to create the Task.",
        "",
    ]

    for wf_id in workflow_ids:
        entry = await WorkflowRegistryService.get_by_workflow_id(wf_id)
        if entry is None:
            entry = await WorkflowRegistryService.get_by_id(wf_id)
        if entry is None:
            continue

        wf_name = entry.get("name", "unknown")
        wf_desc = entry.get("description", "") or wf_name
        has_human = entry.get("has_human_node", False)
        wf_ref = entry.get("workflow_id", "")

        lines.append(f"- **{wf_name}**: {wf_desc}")
        if has_human:
            lines.append("  - ⚠️ Contains human approval nodes")

        param_vars: list[dict] = []
        if wf_ref:
            wf_doc = await WorkflowService.get(wf_ref)
            if wf_doc:
                nodes = wf_doc.get("nodes", [])
                start_node = next((n for n in nodes if n.get("type") == "start"), None)
                if start_node:
                    output_vars = start_node.get("config", {}).get("output_variables", [])
                    if isinstance(output_vars, list):
                        param_vars = []
                        for v in output_vars:
                            if not isinstance(v, dict) or not v.get("name"):
                                continue
                            constraints = v.get("constraints") if isinstance(v.get("constraints"), dict) else {}
                            required = constraints.get("required", v.get("required"))
                            required = bool(required) if required is not None else False
                            default_val = constraints.get("default_value", v.get("default"))
                            param_vars.append({
                                "name": v.get("name", ""),
                                "type": v.get("type", "string"),
                                "label": v.get("label", ""),
                                "description": v.get("description", ""),
                                "required": required,
                                "default": default_val,
                            })

        if param_vars:
            parts_list = []
            for p in param_vars:
                attr_parts = [p["type"]]
                if p["required"]:
                    attr_parts.append("required")
                else:
                    attr_parts.append("optional")
                    default_val = p.get("default")
                    if default_val not in (None, ""):
                        attr_parts.append(f"default={default_val!r}")
                attr_str = ", ".join(attr_parts)
                desc = p.get("description") or p.get("label") or ""
                if p.get("type") == "file":
                    desc = (desc + " — pass FileRef ID string (or list of IDs for multiple files)").strip(" —")
                if desc:
                    parts_list.append(f"{p['name']} ({attr_str}): {desc}")
                else:
                    parts_list.append(f"{p['name']} ({attr_str})")
            param_desc = ", ".join(parts_list)
            lines.append(f"  - Input params: {param_desc}")
            lines.append(
                "  - Map the user's request to the exact param name above. "
                "For example, if the param is ``request``, call "
                "``dispatch_workflow(workflow_name, {'request': '<user request>'})``. "
                "Use the EXACT param name from the list — do NOT invent new keys."
            )
        else:
            lines.append(
                "  - Input params: ``input`` (string). "
                "Pass the user's request as ``{'input': '<user request>'}``."
            )

        lines.append("")

    return "\n".join(lines)


def _build_builtin_tool_declaration(
    builtin_config: list[str],
    *,
    execution_context: str = "chat",
) -> str:
    """Build built-in tool declaration section for the system prompt.

    Dynamically reads tool name + description from harness BUILTIN_TOOLS
    instances (plus app-level PARSE_TOOL_BY_NAME), so glob/grep (and any
    future configurable tools) are automatically included without hardcoding.

    execution_context="workflow" 时省略 Clarification 段（ask_clarification
    运行时已剥离）；自主执行规则由 build_tool_declaration 作为独立段注入。
    """
    from agent_flow_harness import BUILTIN_TOOLS

    from app.core.config import settings
    from app.engine.agent.parse_tool import PARSE_TOOL_BY_NAME
    from app.engine.harness_integration.context import (
        _CONFIGURABLE_BUILTIN_TOOL_NAMES,
        _INJECTED_BUILTIN_TOOL_NAMES,
    )

    lines = [
        "",
        "## Built-in Tools",
        "",
        "You have access to the following built-in tools. Call them directly by name.",
        "",
    ]

    enabled = set(builtin_config)
    if "bash" in enabled:
        enabled |= {"read", "write"}
    # 与运行时注入保持一致:全局开关关闭时 run_code 不进声明。
    if not settings.RUN_CODE_ENABLED:
        enabled.discard("run_code")

    for name in _INJECTED_BUILTIN_TOOL_NAMES:
        # 只声明可配工具(ask_clarification 单独在下方声明)
        if name not in _CONFIGURABLE_BUILTIN_TOOL_NAMES:
            continue
        if name not in enabled:
            continue
        tool = BUILTIN_TOOLS.get(name) or PARSE_TOOL_BY_NAME.get(name)
        desc = (tool.description if tool and tool.description else name)
        # 取描述第一行(有些描述很长,system prompt 里只需摘要)
        desc_first_line = desc.split("\n")[0].strip()
        lines.append(f"- **{name}**: {desc_first_line}")

    # run_code 使用策略 —— 硬性规则,约束 LLM 不要逐条调用工具。
    if "run_code" in enabled:
        lines.extend([
            "",
            "### Batch Tool Execution (run_code)",
            "",
            "When a task requires calling the same tool for many items (every person,",
            "every order, every record), OR chaining multiple tools where later calls",
            "depend on earlier results, you MUST write one **run_code** with a loop /",
            "tools.call_many instead of issuing one tool call per item.",
            "Direct per-item tool calls for batches of 3+ items are an error — they",
            "waste turns and tokens. Inside run_code, call tools via",
            "`tools.call(name, **kwargs)` / `tools.call_many([...])`; see the tool",
            "description for the full API and examples.",
            "",
        ])

    if execution_context == "workflow":
        # 工作流无人值守语义：ask_clarification 已剥离，不生成 Clarification 段
        # （自主执行规则由 build_tool_declaration 作为独立段注入）。
        return "\n".join(lines)

    # ask_clarification is always available (not gated by builtin_config)
    lines.extend([
        "",
        "### Clarification",
        "",
        "When you need more information from the user, you MUST call the **ask_clarification** tool.",
        "Do NOT ask questions in plain text — the tool provides interactive UI (option buttons,",
        "confirmation dialogs, structured forms) that plain text cannot.",
        "",
        "Choose the appropriate `clarification_type`:",
        "- `missing_info`: Missing required details (e.g. file format, target audience).",
        "  Set `options` if there are common choices.",
        "- `ambiguous_requirement`: User's request has multiple interpretations.",
        "  Set `options` to the distinct interpretations.",
        "- `approach_choice`: Multiple valid approaches exist (e.g. React vs Vue).",
        "  Set `options` to the approach names.",
        "- `risk_confirmation`: About to perform a risky/irreversible action.",
        "  Do NOT set `options` — the UI provides confirm/cancel buttons.",
        "- `suggestion`: Recommending a specific approach.",
        "  Set `options` to alternative suggestions if applicable.",
        "",
        "**IMPORTANT**: `options` must be a JSON array of strings, e.g. `[\"React\", \"Vue\", \"Svelte\"]`.",
        "",
        "#### Wizard mode (multiple questions, asked one by one)",
        "",
        "When you need to collect **two or more independent pieces of information**,",
        "use the `fields` parameter. The host asks each question one at a time (the user can",
        "go back to edit earlier answers), so all fields are resolved in a single tool call",
        "instead of repeated back-and-forth rounds.",
        "",
        "Do NOT use `fields` when:",
        "- You only have one question (use the single-question form).",
        "- It is a `risk_confirmation` or a single `approach_choice` (use `options`).",
        "",
        "Each field object has: `name` (key the answer returns under), `label` (question text",
        "shown to the user), `field_type` (`text`/`number`/`boolean`/`select`), `required`,",
        "`options`, `default`, and `description` (help text).",
        "",
        "**Deciding whether to provide `options`:**",
        "- **Provide 3-5 recommended options** whenever reasonable (preferred). This gives the",
        "  user quick choices while still allowing free input at the bottom of each question.",
        "- **Omit `options`** only for fields the user MUST type themselves and cannot be",
        "  anticipated (e.g. passwords, tokens, free-form names). In that case only an input",
        "  box is shown.",
        "Boolean fields never take `options` (they are a yes/no toggle).",
        "",
        "Example — collecting report parameters:",
        "```json",
        "[",
        "  {\"name\": \"audience\", \"label\": \"目标受众是谁？\", \"field_type\": \"select\",",
        "   \"options\": [\"技术人员\", \"管理层\", \"客户\", \"通用读者\"]},",
        "  {\"name\": \"format\", \"label\": \"输出格式？\", \"field_type\": \"select\",",
        "   \"options\": [\"Markdown\", \"PDF\", \"PPT\", \"HTML\"]},",
        "  {\"name\": \"api_key\", \"label\": \"API Key\", \"field_type\": \"text\"},",
        "  {\"name\": \"length\", \"label\": \"篇幅(字)?\", \"field_type\": \"number\", \"default\": 500}",
        "]",
        "```",
        "",
        "The user's answers come back as a JSON string like `{\"audience\":\"管理层\",\"format\":\"PDF\",\"api_key\":\"sk-...\",\"length\":800}`,",
        "which you can parse and use directly.",
    ])

    return "\n".join(lines)


def _build_autonomous_execution_section() -> list[str]:
    """工作流 agent 节点的自主执行规则段（无人值守语义，独立成段）。

    与 context.py 的工具剥离配套：ask_clarification / _TASK_TOOLS 均已移除，
    本段告诉 LLM 三条出路 —— 合理假设继续、诚实终止（abort_workflow）、
    绝不反问。作为独立段注入（不依赖 builtin_config），声明与运行时工具集
    保持一致，避免 LLM 幻觉调用不存在的工具。
    """
    return [
        "",
        "## Autonomous Execution (Workflow Context)",
        "",
        "You are running as an unattended node inside an automated workflow.",
        "No human will answer you during execution — the workflow must keep",
        "running without pausing to ask questions.",
        "",
        "- Do NOT ask the user questions, neither via tools nor in plain text.",
        "  A question at the end of your output will not pause anything and",
        "  no one will reply.",
        "- If information is partial but a reasonable assumption is possible,",
        "  proceed autonomously based on the most reasonable interpretation,",
        "  and clearly state the assumptions you made in your final output.",
        "- If the input is so vague or incomplete that continuing would be",
        "  meaningless (any output would be fabricated), call the",
        "  **abort_workflow** tool with an honest `reason` and, when helpful,",
        "  `needed_info` describing what should be provided. The workflow",
        "  will terminate and your reason will be shown to the user as the",
        "  failure explanation.",
        "- Never fabricate results or pretend to have completed work you",
        "  could not actually do.",
        "",
    ]


def _build_output_contract_section(response_schema: dict) -> list[str]:
    """工作流 agent 节点的 response 结构契约段（opt-in，node_executor 解析校验）。

    agent 输出是类 API 返回体：固定字段（status/files/usage/...）由引擎
    填充，LLM 的最终回复就是 ``response`` 字段的值。schema 形如
    ``{type: "object"|"array", fields: [{name, type, required,
    enum_values, description, fields(第二层)}]}``（嵌套最多两层）。
    解析后的 response 以原生 dict/list 写入变量池，下游
    ``{{node.response.field.sub}}`` 直接取值。
    """
    schema_type = response_schema.get("type")
    fields = [f for f in (response_schema.get("fields") or []) if isinstance(f, dict)]
    is_array = schema_type == "array"

    lines = [
        "",
        "## Output Contract",
        "",
        "Your final reply is the value of the ``response`` field consumed by",
        "downstream nodes. It MUST be "
        + ("a JSON array of objects, each with fields:" if is_array else "a JSON object with fields:"),
        "no prose, no markdown fences, nothing outside the JSON.",
        "If the input is too vague or incomplete to produce a meaningful",
        "answer, call **abort_workflow** instead of producing JSON.",
        "",
    ]
    lines.extend(_render_schema_fields(fields, indent=0))
    lines.append("")
    return lines


def _render_schema_fields(fields: list[dict], indent: int) -> list[str]:
    """渲染契约字段列表（最多两层：第一层 object 字段可带子 fields）。

    每个字段可带 ``is_list``（string list / enum list / object list）。
    """
    pad = "  " * indent
    lines: list[str] = []
    for f in fields:
        name = str(f.get("name") or "").strip()
        if not name:
            continue
        ftype = str(f.get("type") or "string")
        is_list = bool(f.get("is_list"))
        req = "required" if f.get("required") else "optional"
        enum_values = f.get("enum_values") or []
        enum_note = (
            f", one of {json.dumps([str(v) for v in enum_values], ensure_ascii=False)}"
            if enum_values
            else ""
        )
        desc = str(f.get("description") or "").strip()
        desc_note = f" — {desc}" if desc else ""
        type_note = f"{ftype} list" if is_list else ftype
        lines.append(f"{pad}- `{name}` ({type_note}, {req}{enum_note}){desc_note}")
        if ftype == "object":
            sub_fields = [g for g in (f.get("fields") or []) if isinstance(g, dict)]
            if sub_fields:
                if is_list:
                    lines.append(f"{pad}  each list element is an object with fields:")
                else:
                    lines.append(f"{pad}  nested object fields:")
                lines.extend(_render_schema_fields(sub_fields, indent=indent + 2))
    return lines


def _build_task_tool_declaration() -> str:
    """Build task management tool declaration section for the system prompt."""
    lines = [
        "",
        "## Task Management Tools",
        "",
        "You have access to the following task management tools. Use them to query or manage workflow Tasks.",
        "",
        "- **confirm_workflow(workflow_name, description, params)**: Ask the user to confirm executing a workflow. Execution pauses (a confirmation card is shown) and resumes when the user confirms or rejects. Pass the workflow's name and description (from the Workflow declaration above) plus the proposed input params.",
        "- **dispatch_workflow(workflow_name, params)**: Create and dispatch a workflow Task. Only call this AFTER the user explicitly confirms. Pass the user's original request as params using the exact variable names from the Workflow declaration above.",
        "- **task_query(task_ids)**: Query status/results of Tasks by their IDs. Returns status + output (completed) or error (failed). Only call this when the user asks about progress — do NOT poll or loop.",
        "- **task_intervene**: Intervene in a Task (approve, reject, cancel, resume, retry)",
        "- **cancel_task**: Shortcut to cancel a Task",
        "- **update_task_variables**: Update the variable pool of a running Task",
        "",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Tool resolution — delegates to the unified resolver in context.py
# (same logic as runtime resolve_harness_context, no dual-path divergence).
# ---------------------------------------------------------------------------


async def _resolve_tools_for_preview(agent: dict) -> list:
    """Resolve ALL tools for preview (mirrors runtime exactly).

    Delegates to ``context.resolve_all_tools`` so preview and runtime
    always show the same tool set. Load errors are logged but not
    surfaced (preview is best-effort; runtime surfaces them to frontend).
    """
    from app.engine.harness_integration.context import resolve_all_tools

    tools, errors = await resolve_all_tools(agent)
    if errors:
        for e in errors:
            logger.warning("preview_tool_load_error", **e)
    return tools


# (removed _make_skill_loader — preview now uses the unified resolver
#  which delegates to harness SkillManager, same as runtime)


# ---------------------------------------------------------------------------
# Preview / Dry-run — inspect assembled prompt & tools without invoking LLM
# ---------------------------------------------------------------------------

_WORKFLOW_TOOL_NAMES = {"confirm_workflow", "dispatch_workflow"}
_TASK_TOOL_NAMES = {
    "task_query", "task_intervene",
    "cancel_task", "update_task_variables",
}


def _classify_tool_type(name: str) -> str:
    """Classify a tool into its origin type for preview/labelling."""
    if name == "load_skill":
        return "skill"
    if name in _WORKFLOW_TOOL_NAMES or name in _TASK_TOOL_NAMES:
        return "workflow"
    if name.startswith("mcp__"):
        return "mcp"
    return "builtin"


async def preview_agent(
    agent: dict,
    user_input: str = "Hello",
    enable_thinking: bool = False,
) -> dict:
    """Assemble the Agent's prompt and tools without invoking the LLM.

    Returns a dict with the fully composed system prompt, messages,
    and a structured tool list suitable for debugging and inspection.
    """
    from langchain_core.tools import StructuredTool

    from app.engine.agent.slot_renderer import render_system_prompt_full

    system_text = await render_system_prompt_full(agent, strict=False)

    messages: list[dict] = []
    if system_text:
        messages.append({"role": "system", "content": system_text})
    messages.append({"role": "user", "content": user_input})

    all_tools = await _resolve_tools_for_preview(agent)

    tool_previews: list[dict] = []
    summary: dict[str, int] = {"total": len(all_tools), "skill": 0, "mcp": 0, "builtin": 0, "workflow": 0}

    for t in all_tools:
        if isinstance(t, StructuredTool):
            t_name = t.name
            t_desc = t.description or ""
            t_schema = {}
            if t.args_schema:
                if isinstance(t.args_schema, dict):
                    t_schema = t.args_schema
                elif hasattr(t.args_schema, "model_json_schema"):
                    t_schema = t.args_schema.model_json_schema()
                else:
                    t_schema = {}

            t_type = _classify_tool_type(t_name)
            t_source = "skill_loader" if t_type == "skill" else t_name

            summary[t_type] = summary.get(t_type, 0) + 1
            tool_previews.append({
                "name": t_name,
                "type": t_type,
                "description": t_desc[:500],
                "source": t_source,
                "input_schema": t_schema,
            })
        else:
            fn_name = getattr(t, "__name__", str(t))
            summary["builtin"] += 1
            tool_previews.append({
                "name": fn_name,
                "type": "builtin",
                "description": getattr(t, "__doc__", "") or "",
                "source": fn_name,
                "input_schema": {},
            })

    model_ref = resolve_default_model(agent)

    return {
        "system_prompt": system_text,
        "messages": messages,
        "tools": tool_previews,
        "tool_summary": summary,
        "model": model_ref,
    }
