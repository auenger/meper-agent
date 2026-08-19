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

from loguru import logger

from app.models.compat import (
    resolve_skill_ids,  # noqa: F401 (used by build_tool_declaration)
)

# ---------------------------------------------------------------------------
# System prompt + tool declaration (used by stream / invoke / preview)
# ---------------------------------------------------------------------------


async def build_skill_declaration(tool_ids: list[str]) -> str:
    """Build the Skill declaration text for the system prompt.

    Queries MongoDB for each tool ID and formats a markdown list
    of available Skills (name + description).
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
        desc = doc.get("description", "")
        lines.append(f"- **{name}**: {desc}")

    return "\n".join(lines)


async def build_tool_declaration(agent: dict) -> str:
    """Build the complete tool declaration text for the system prompt.

    Generates declaration sections for all tool categories:
    - Skills (on-demand via load_skill)
    - Knowledge Bases (kb_search / kb_glob / kb_grep / kb_read)
    - MCP tools (directly callable)
    - Workflow list (listed for reference, triggered via propose/dispatch)
    - Built-in tools (directly callable)
    - Task tools (always available)
    """
    sections: list[str] = []

    skill_ids = resolve_skill_ids(agent)
    if skill_ids:
        skill_decl = await build_skill_declaration(skill_ids)
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

    workflow_ids = agent.get("workflow_ids") or []
    if workflow_ids:
        workflow_decl = await _build_workflow_tool_declaration(workflow_ids)
        if workflow_decl:
            sections.append(workflow_decl)

    builtin_config = agent.get("builtin_config") or []
    if builtin_config:
        builtin_decl = _build_builtin_tool_declaration(builtin_config)
        if builtin_decl:
            sections.append(builtin_decl)

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


async def build_system_prompt(agent_doc: dict) -> str:
    """Build the fully assembled system prompt for an Agent.

    Delegates to the slot renderer which handles PromptTemplate-based
    prompt composition.
    """
    from app.engine.agent.slot_renderer import render_system_prompt_full

    return await render_system_prompt_full(agent_doc)


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


def _build_builtin_tool_declaration(builtin_config: list[str]) -> str:
    """Build built-in tool declaration section for the system prompt.

    Dynamically reads tool name + description from harness BUILTIN_TOOLS
    instances (plus app-level PARSE_TOOL_BY_NAME), so glob/grep (and any
    future configurable tools) are automatically included without hardcoding.
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

    model_ref = agent.get("default_model") or (agent.get("llm_config") or {}).get("default_model", "")

    return {
        "system_prompt": system_text,
        "messages": messages,
        "tools": tool_previews,
        "tool_summary": summary,
        "model": model_ref,
    }
