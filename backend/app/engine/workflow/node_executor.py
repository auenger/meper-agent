"""Node executor base class and implementations (Strategy pattern).

Each node type has a corresponding executor that implements ``execute()``.
The ``WorkflowEngine`` selects the appropriate executor based on node type.
"""
from __future__ import annotations

import asyncio
import json
import operator as _operator
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from loguru import logger


@dataclass
class NodeResult:
    """Result of executing a single workflow node."""

    success: bool
    output: dict[str, Any] = field(default_factory=dict)
    error_message: str = ""
    error_code: str = ""
    # For gateway nodes: the selected branch target node ID
    selected_branch: str | None = None


class BaseNodeExecutor(ABC):
    """Abstract base for all workflow node executors.

    Subclasses must implement ``execute()``.
    """

    def __init__(self, node_id: str, node_config: dict[str, Any]) -> None:
        self.node_id = node_id
        self.node_config = node_config

    @abstractmethod
    async def execute(self, variables: dict[str, Any]) -> NodeResult:
        """Execute the node and return the result.

        Args:
            variables: Current variable pool (read-only snapshot).

        Returns:
            NodeResult with success/failure and output data.
        """
        ...


# ── Start ──


class StartNodeExecutor(BaseNodeExecutor):
    """Initialise variable pool with Task input.

    Config::

        {
            "output_variables": [
                {
                    "name": "query",
                    "type": "text",
                    "constraints": {"required": true, "default_value": ""},
                    ...
                },
                ...
            ],
            "input_mapping": { "var_name": "{{ input.field }}" }  # optional override
        }

    ``required`` / ``default_value`` are read from ``constraints``
    (matching the frontend ``VariableListEditor`` schema).  Top-level
    ``required`` / ``default`` keys are still accepted for backward
    compatibility with legacy data.
    """

    async def execute(self, variables: dict[str, Any]) -> NodeResult:
        logger.debug(
            "node_start",
            node_id=self.node_id,
            input_keys=list(variables.keys()),
            has_input_mapping=bool(self.node_config.get("input_mapping")),
        )

        output: dict[str, Any] = {}

        # 1. If input_mapping is explicitly configured, use it (takes precedence)
        input_mapping = self.node_config.get("input_mapping", {})
        if input_mapping:
            from app.engine.workflow.expression import ExpressionEngine

            engine = ExpressionEngine(variables)
            output = engine.resolve_dict(input_mapping)
            return NodeResult(success=True, output=output)

        # 2. Otherwise, initialize variables from output_variables definition
        output_variables = self.node_config.get("output_variables", [])
        task_input = variables.get("input")
        if not isinstance(task_input, dict):
            task_input = {}

        if isinstance(output_variables, list) and output_variables:
            missing_required: list[str] = []

            for var_def in output_variables:
                if not isinstance(var_def, dict):
                    continue
                name = var_def.get("name", "")
                if not name:
                    continue

                var_type = var_def.get("type", "text")

                # Read required/default from `constraints` (frontend schema)
                # with top-level fallback for legacy data.
                constraints = var_def.get("constraints") if isinstance(var_def.get("constraints"), dict) else {}
                raw_required = constraints.get("required", var_def.get("required"))
                is_required = bool(raw_required) if raw_required is not None else False
                default_val = constraints.get("default_value", var_def.get("default"))

                # Use input value if provided, else fall back to default.
                if name in task_input:
                    value = task_input[name]
                elif default_val not in (None, ""):
                    value = default_val
                else:
                    value = None

                # File type: delegate to file_validator for resolution.
                if var_type == "file":
                    if value in (None, ""):
                        # No file provided — handled by required check below
                        pass
                    else:
                        from app.engine.workflow.file_validator import (
                            validate_file_variable,
                        )

                        resolved, ferror = await validate_file_variable(value, var_def)
                        if ferror:
                            return NodeResult(
                                success=False,
                                output={},
                                error_message=f"Start 节点文件变量 '{name}' 验证失败: {ferror}",
                            )
                        output[name] = resolved
                        continue

                # Required validation: None or empty string counts as missing.
                if is_required and value in (None, ""):
                    missing_required.append(name)
                else:
                    output[name] = value

            if missing_required:
                return NodeResult(
                    success=False,
                    output={},
                    error_message=(
                        f"Start 节点必填变量缺失: {', '.join(missing_required)}。"
                        f"请在 dispatch_workflow 的 params 中补充这些字段。"
                    ),
                )

            return NodeResult(success=True, output=output)

        # 3. Fallback: pass through all task input
        return NodeResult(success=True, output={"input": task_input})


# ── End ──


class EndNodeExecutor(BaseNodeExecutor):
    """Summarise output and signal completion.

    Config: ``{"output_mapping": { "result": "{{ node_id.field }}" }}``
    """

    async def execute(self, variables: dict[str, Any]) -> NodeResult:
        logger.debug("node_end", node_id=self.node_id)
        output_mapping = self.node_config.get("output_mapping", {})

        if output_mapping:
            from app.engine.workflow.expression import ExpressionEngine

            engine = ExpressionEngine(variables)
            resolved = engine.resolve_dict(output_mapping)
            return NodeResult(success=True, output=resolved)

        return NodeResult(success=True, output={"status": "completed"})


# ── Agent ──

_JSON_FENCE_RE = re.compile(r"```(?:json)?\s*([\s\S]*?)\s*```", re.DOTALL)

_VALID_FIELD_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

_VALID_FIELD_TYPES = {"string", "number", "boolean", "enum", "object"}


def _normalize_response_schema(node_config: dict[str, Any]) -> dict[str, Any] | None:
    """归一化 agent 节点的 response 结构声明，未声明/无效返回 None。

    新格式 ``{type: "object"|"array", fields: [...]}``（text 缺省 = 未声明）。
    兼容旧 ``output_schema``（list[字段] → {type: "object", fields: [...]},
    该格式未发布无真实存量）；旧 ``output_variables`` 的默认 [response]
    视同未声明（后端从不消费它）。非法字段在运行时宽容跳过（validator
    已在保存时拦截）。
    """
    raw = node_config.get("response_schema")
    if isinstance(raw, dict) and raw.get("type") in ("object", "array"):
        fields = _clean_schema_fields(raw.get("fields"), depth=0)
        if fields:
            return {"type": raw["type"], "fields": fields}
        return None
    # 兼容旧 output_schema（未发布格式，直接转译）
    legacy = node_config.get("output_schema")
    if isinstance(legacy, list):
        fields = _clean_schema_fields(legacy, depth=0)
        if fields:
            return {"type": "object", "fields": fields}
    return None


def _clean_schema_fields(fields: Any, *, depth: int) -> list[dict[str, Any]]:
    """清洗字段列表：过滤非法项；第一层 object 字段保留一层子 fields。

    每个字段可带 ``is_list``（列表标志，任何基础类型都可为列表——
    string 列表 / enum 列表 / object 列表）；第二层允许标量列表，
    object 列表只能出现在第一层（其 fields 即第二层）。
    """
    if not isinstance(fields, list):
        return []
    cleaned: list[dict[str, Any]] = []
    for f in fields:
        if not isinstance(f, dict):
            continue
        name = str(f.get("name") or "").strip()
        ftype = str(f.get("type") or "string")
        if not name or not _VALID_FIELD_NAME_RE.fullmatch(name):
            continue
        if ftype not in _VALID_FIELD_TYPES:
            continue
        entry = dict(f)
        entry["name"], entry["type"] = name, ftype
        entry["is_list"] = bool(f.get("is_list"))
        if ftype == "object" and depth == 0:
            # 第二层：剥离再嵌套的 fields（不可再嵌）
            sub = _clean_schema_fields(f.get("fields"), depth=1)
            for g in sub:
                g.pop("fields", None)
            entry["fields"] = sub
        else:
            entry.pop("fields", None)
        cleaned.append(entry)
    return cleaned


def _validate_schema_fields(
    obj: Any,
    fields: list[dict[str, Any]],
    *,
    path: str = "",
    depth: int = 0,
) -> str:
    """校验 dict 是否符合字段契约（嵌套最多两层），返回错误串或空串。"""
    if not isinstance(obj, dict):
        return f"{path or '值'} 应为对象"
    for f in fields:
        name = str(f.get("name") or "").strip()
        if not name:
            continue
        label = f"{path}.{name}" if path else name
        if f.get("required") and name not in obj:
            return f"缺少必填字段 {label}"
        if name not in obj:
            continue
        err = _validate_field_value(obj[name], f, label, depth)
        if err:
            return err
    return ""


def _validate_field_value(
    value: Any, f: dict[str, Any], label: str, depth: int,
) -> str:
    """校验单个字段值（列表/类型/枚举/嵌套对象），返回错误串或空串。"""
    # 列表字段：拆包后逐元素按基础类型校验（object 列表 = 逐元素按 fields）
    if f.get("is_list"):
        if not isinstance(value, list):
            return f"字段 {label} 应为列表，实际为 {type(value).__name__}"
        base = {k: v for k, v in f.items() if k != "is_list"}
        for idx, item in enumerate(value):
            err = _validate_field_value(item, base, f"{label}[{idx}]", depth)
            if err:
                return err
        return ""

    ftype = str(f.get("type") or "string")
    enum_values = f.get("enum_values") or []
    if enum_values or ftype == "enum":
        if value not in enum_values:
            return f"字段 {label} 的值 {value!r} 不在允许枚举 {enum_values} 内"
        return ""
    if ftype == "object":
        if not isinstance(value, dict):
            return f"字段 {label} 应为 object，实际为 {type(value).__name__}"
        if depth >= 1:
            return ""  # 第二层不允许再嵌（配置侧已拦，运行时防御）
        return _validate_schema_fields(
            value, f.get("fields") or [], path=label, depth=depth + 1,
        )
    if ftype == "number":
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return f"字段 {label} 应为 number，实际为 {type(value).__name__}"
        return ""
    if ftype == "boolean":
        if not isinstance(value, bool):
            return f"字段 {label} 应为 boolean，实际为 {type(value).__name__}"
        return ""
    # string（默认）
    if not isinstance(value, str):
        return f"字段 {label} 应为 string，实际为 {type(value).__name__}"
    return ""


def _extract_json_payload(text: str) -> tuple[dict[str, Any] | list[Any] | None, str]:
    """从模型输出文本中提取 JSON（对象或数组），返回 (value, error)。

    依次尝试：裸 JSON → ```json 围栏块 → 首个 ``{``/``[`` 到最后一个
    ``}``/``]`` 的子串（容忍 JSON 前后的解释性文字）。标量不符合
    Output Contract。
    """
    raw = (text or "").strip()
    if not raw:
        return None, "输出为空，无法解析 JSON"

    candidates: list[str] = [raw]
    fence_match = _JSON_FENCE_RE.search(raw)
    if fence_match:
        candidates.append(fence_match.group(1).strip())
    for open_ch, close_ch in (("{", "}"), ("[", "]")):
        start, end = raw.find(open_ch), raw.rfind(close_ch)
        if 0 <= start < end:
            candidates.append(raw[start : end + 1])

    for candidate in candidates:
        try:
            value = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(value, (dict, list)):
            return value, ""
    return None, "无法从输出中解析出 JSON"


class AgentNodeExecutor(BaseNodeExecutor):
    """Invoke an Agent to perform reasoning/actions.

    输出 = 类 API 返回体（固定字段恒定，分支间结构一致）::

        {
            "status": "ok" | "insufficient",   # 固定信号
            "response": str | dict | list,     # 默认文本；契约模式下原生 dict/list
            "agent_id": "...",                 # 固定
            "files": [...],                    # 固定（insufficient 时 []）
            "usage": {...},                    # 固定（insufficient 时 {}）
            "needed_info": "...",              # 固定（insufficient 时有值，否则 ""）
            "thinking": "..."                  # 可选，非空才写
        }

    Config::

        {
            "agent_id": "agent_xxx",
            "system_prompt_override": "...",  # optional
            "input_prompt": "{{ ... }}",       # prompt template
            "temperature": 0.7,                # optional
            "insufficient_branch": "node_y",   # optional — abort_workflow 触发
                                               # 时走该澄清分支而非硬失败
            "response_schema": {               # optional — response 结构契约
                "type": "object",              #   text(默认)|object|array
                "fields": [
                    {"name": "status", "type": "enum", "required": true,
                     "enum_values": ["completed", "insufficient_info"]},
                    {"name": "author", "type": "object",
                     "fields": [{"name": "name", "type": "string"}]}   # 最多两层
                ]
            }
        }
    """

    async def execute(self, variables: dict[str, Any]) -> NodeResult:
        agent_id = self.node_config.get("agent_id", "")

        # ── Story 4-15: Read task identity from system variables ──
        # System variables (task_id, user_id) are bound to the VariablePool
        # at task creation time by WorkflowEngine. They are available to all
        # nodes via variables["system"]["task_id"] etc. This is more robust
        # than constructor injection because it works for both initial
        # execution and checkpoint resume without special-casing.
        sys_vars = variables.get("system", {}) or {}
        task_id = sys_vars.get("task_id", "")

        logger.debug(
            "node_agent_start",
            node_id=self.node_id,
            agent_id=agent_id,
            task_id=task_id,
            has_input_mapping=bool(self.node_config.get("input_mapping")),
        )

        if not agent_id:
            return NodeResult(success=False, output={}, error_message="agent_id 未配置")

        user_id = sys_vars.get("user_id", "")
        # 终端用户通用 token（外部触发 Workflow 时透传，供 MCP 凭证兑换）。
        # 内部触发（studio 测试）为空 → resolve_harness_context 不 set
        # token_record_id → interceptor 降级用静态凭证。
        user_token = sys_vars.get("user_token", "") or None
        if not task_id or not user_id:
            missing = []
            if not task_id:
                missing.append("system.task_id")
            if not user_id:
                missing.append("system.user_id")
            return NodeResult(
                success=False,
                output={},
                error_message=(
                    "AgentNodeExecutor 缺少执行身份: "
                    f"{', '.join(missing)}（无法定位 task workspace）"
                ),
            )

        from app.engine.workflow.expression import ExpressionEngine

        engine = ExpressionEngine(variables)

        # input_query → user message（查询）
        # resolve_str 保证返回 str：input 经 dispatch_workflow 落库时可能是
        # bool/int/dict（LLM 给的 JSON 原生类型），裸 resolve 会返回原类型，
        # 下游 .strip() 会抛 AttributeError。统一用 resolve_str 归一化。
        input_query = self.node_config.get("input_query", "")
        resolved_query = engine.resolve_str(input_query) if input_query else ""

        # input_prompt → context 卡槽覆盖（注入系统提示，不作为 user message）
        # 向后兼容：也读取 slot_values.context
        input_prompt = self.node_config.get("input_prompt", "")
        resolved_context = engine.resolve_str(input_prompt) if input_prompt else ""
        if not resolved_context:
            legacy_slot_ctx = self.node_config.get("slot_values", {}).get("context", "")
            resolved_context = engine.resolve_str(legacy_slot_ctx) if legacy_slot_ctx else ""

        # response 结构契约（opt-in，API 返回体心智模型）：
        # {type: "text"|"object"|"array", fields: [{name, type, required,
        # enum_values, description, fields(第二层)}]}（嵌套最多两层）。
        # object/array 时 system prompt 追加 Output Contract 段——最终回复
        # 即 response 字段的值，必须是符合契约的 JSON；输出后确定性解析为
        # 原生 dict/list 写入变量池（下游 {{node.response.field}} 直接取值，
        # 不依赖 JSON 字符串深解析）。text（默认）零配置、行为不变。
        response_schema = _normalize_response_schema(self.node_config)

        # 信息不足分支（opt-in）：abort_workflow 触发时不再硬失败，而是
        # success + sufficiency="insufficient" + selected_branch 只走该分支
        # （如 human 澄清节点）。未配置时维持诚实硬失败语义（现状）。
        insufficient_branch = str(self.node_config.get("insufficient_branch") or "").strip()

        try:
            from langchain_core.messages import SystemMessage

            from app.db.mongodb import get_database

            db = get_database()
            agent_doc = await db["agents"].find_one({"_id": agent_id})
            if agent_doc is None:
                return NodeResult(
                    success=False,
                    output={},
                    error_message=f"Agent {agent_id} 不存在",
                )

            # 仅允许覆盖 context 卡槽（role/task/constraints/output_format 由 Agent 自身决定）
            context_overrides = {"context": resolved_context} if resolved_context else None

            temperature = self.node_config.get("temperature")
            if temperature is not None:
                agent_doc["temperature_override"] = temperature

            # harness 的 invoke 内部会自己 build graph,这里不需要构造。

            # Build system prompt via slot renderer (context override + variable pool)
            from app.engine.agent.slot_renderer import render_system_prompt_full

            # execution_context="workflow"：无人值守语义——工具声明去掉
            # Clarification/Task 段，追加自主执行规则（禁反问 + abort_workflow）。
            # response_schema（opt-in）：追加 Output Contract 段，最终回复即
            # response 字段的值（见 _parse_structured_output）。
            system_text = await render_system_prompt_full(
                agent_doc,
                node_slot_overrides=context_overrides,
                variable_pool=variables,
                execution_context="workflow",
                response_schema=response_schema,
            )
        except Exception as exc:
            # 展开 ExceptionGroup 以显示真正原因
            detail = str(exc)
            if hasattr(exc, "exceptions"):
                sub_errors = "; ".join(str(e) for e in exc.exceptions)  # type: ignore[attr-defined]
                detail = f"{exc} — 子异常: {sub_errors}"
            logger.error("node_agent_setup_failed", node_id=self.node_id, error=detail)
            return NodeResult(
                success=False,
                output={},
                error_message=f"Agent 初始化失败: {detail}",
            )

        # Execute with timeout protection and retry
        timeout_ms = self.node_config.get("timeout_ms", 300000)
        max_retry = self.node_config.get("max_retry", 0)
        retry_delay_ms = self.node_config.get("retry_delay_ms", 2000)

        # thread_id 固定为 {task_id}_{node_id}，使 checkpoint 可按 task+node 反查，
        # 供前端按需读取 agent 节点的完整执行过程（thinking/tool_call/tool_result）。
        # 重试时由 API 层清除 LangGraph checkpointer 中该 thread 的旧数据，
        # 避免残留消息导致 "multiple non-consecutive system messages"。
        import time as _time

        _thread_id = f"{task_id}_{self.node_id}"

        # ── 恢复路径：若 checkpoint 中有 agent_thread_id，说明此 agent 节点
        # 之前被取消（interrupt 挂起），用 Command(resume) 续接 REACT 循环，
        # 完整上下文（messages/tool结果/step_count）从 MongoDB checkpointer 恢复。
        resume_thread_id = sys_vars.get("resume_agent_thread_id", "")
        # 恢复时文件扫描不用 node_start_ts 过滤——agent 在上次执行（被取消前）
        # 已写入 output/ 的文件 mtime 早于本次恢复时间，会被错误过滤。
        # sha256 去重已保证不会重复注册。
        is_resume = bool(resume_thread_id)

        # 组装初始消息：system prompt（含工具声明 + context 卡槽注入）+ 用户查询
        # 注意：input_prompt 已经通过 context 卡槽注入系统提示，不作为独立 user message
        # 只有 input_query 作为 user message
        # strip() 防止空白字符串逃过 truthiness 检查导致 LLM API 报
        # "messages: at least one message is required"（空 SystemMessage 不算有效消息）
        stripped_system = system_text.strip() if system_text else ""
        stripped_query = resolved_query.strip() if resolved_query else ""

        initial_messages: list = []
        if stripped_system:
            initial_messages.append(SystemMessage(content=stripped_system))

        if stripped_query:
            initial_messages.append({"role": "user", "content": stripped_query})

        # 兜底：没有任何有效消息时 LLM API 会报 "at least one message is required"
        if not initial_messages:
            logger.warning(
                "node_agent_empty_messages",
                node_id=self.node_id,
                agent_id=agent_id,
                input_query=input_query,
                variables=variables,
            )
            initial_messages.append({
                "role": "user",
                "content": f"请根据你的系统提示执行任务（input_query='{input_query}' 解析结果为空）",
            })

        # ── Story 4-15: Set up task workspace context for Agent tools ──
        # Without this, builtin tools like write_to_output / read / write
        # / bash have no workspace to write to and fall back to PROJECT_ROOT.
        from app.engine.tool.workspace import WorkspaceManager

        task_workspace = WorkspaceManager.create_task_workspace(
            user_id, task_id,
        )
        # harness 路径:workspace 注入由 invoke 内部的 resolve_harness_context
        # 接管(传入 task_workspace),无需手动 set_workspace_context。
        node_start_ts = _time.time()
        logger.info(
            "agent_node_workspace_set",
            node_id=self.node_id,
            task_id=task_id,
            user_id=user_id,
            workspace_root=str(task_workspace.root),
        )

        last_error: str | None = None
        # 记录被取消时的 agent thread_id，供 engine 保存到 checkpoint
        self.agent_thread_id: str = ""

        # ── response 结构契约的反馈重试状态 ──
        # 契约校验失败时同 thread 追加一条 HumanMessage 反馈再跑一轮
        # （add_messages 语义：完整历史保留，反馈轮不重复 SystemMessage）。
        # 独立于 max_retry（异常重试是"原样重跑"，反馈重试是"带矫正信号"），
        # 固定 1 次防死循环。
        pending_messages: list | None = None
        schema_retry_used = False
        total_attempts = 1 + max_retry + (1 if response_schema else 0)

        try:
            for attempt in range(total_attempts):
                try:
                    # 取消检查器：每轮 REACT 循环在 compress_node 检查 task 状态，
                    # 若已 CANCELLED 则 interrupt() 优雅挂起，完整上下文存入 checkpointer。
                    async def _cancel_checker() -> bool:
                        from app.db.mongodb import get_database

                        doc = await get_database()["tasks"].find_one(
                            {"_id": task_id}, {"status": 1},
                        )
                        return bool(doc and doc.get("status") == "cancelled")

                    if resume_thread_id:
                        # 恢复被取消的 agent：用 Command(resume) 续接 REACT 循环
                        from app.engine.harness_integration import resume_agent

                        result = await asyncio.wait_for(
                            resume_agent(
                                agent_doc,
                                {
                                    "messages": initial_messages,
                                    "session_id": resume_thread_id,
                                    "user_id": user_id,
                                    "agent_id": agent_id,
                                },
                                thread_id=resume_thread_id,
                                resume_value="continue",
                                workspace=task_workspace,
                                cancel_checker=_cancel_checker,
                                user_token=user_token,
                                execution_context="workflow",
                            ),
                            timeout=timeout_ms / 1000,
                        )
                        # 恢复成功后清除标志，后续重试走正常 invoke
                        resume_thread_id = ""
                    else:
                        from app.engine.harness_integration import invoke

                        # pending_messages 非空 = schema 反馈轮：只带反馈消息，
                        # 历史（system prompt/工具调用/违规输出）由 checkpointer
                        # 按 thread_id 追加恢复，不重复注入 SystemMessage。
                        result = await asyncio.wait_for(
                            invoke(
                                agent_doc,
                                {
                                    "messages": pending_messages
                                    if pending_messages is not None
                                    else initial_messages,
                                    "session_id": _thread_id,
                                    "user_id": user_id,
                                    "agent_id": agent_id,
                                },
                                workspace=task_workspace,
                                cancel_checker=_cancel_checker,
                                user_token=user_token,
                                execution_context="workflow",
                            ),
                            timeout=timeout_ms / 1000,
                        )
                        pending_messages = None  # 反馈轮只生效一次

                    # ── 检测 LangGraph interrupt ──
                    # interrupt() 后 ainvoke 返回的 state 带 __interrupt__ 键。
                    # 区分 payload：cancel_checker 挂起的是 {"reason": "cancelled"}
                    # （可恢复取消，上下文已存 checkpointer，记录 thread_id 供
                    # engine 保存 checkpoint，恢复时用 Command(resume) 续接）；
                    # 其他 HITL interrupt（如未来新增的交互式工具）在工作流
                    # 无人值守语义下不允许——按节点失败诚实报错，不再误判为
                    # 取消导致工作流停摆。
                    if isinstance(result, dict) and result.get("__interrupt__"):
                        if self._interrupt_is_cancel(result.get("__interrupt__")):
                            self.agent_thread_id = _thread_id
                            return NodeResult(
                                success=False,
                                output={},
                                error_message="",
                                error_code="AGENT_INTERRUPTED",
                            )
                        detail = self._summarise_interrupts(result.get("__interrupt__"))
                        logger.error(
                            "node_agent_hitl_interrupt_rejected",
                            node_id=self.node_id,
                            interrupts=detail,
                        )
                        return NodeResult(
                            success=False,
                            output={},
                            error_message=(
                                "Agent 节点触发交互式中断，但工作流为无人值守执行"
                                f"（不允许向用户提问）：{detail}"
                            ),
                        )

                    # ── 检测 abort_workflow 诚实终止 ──
                    # 输入含糊到执行无意义时，Agent 调 abort_workflow(reason,
                    # needed_info)（非 interrupt 工具）。扫描 messages 中的
                    # tool_call 判定（确定性信号，不进重试）：
                    # - 未配置 insufficient_branch（默认）：工作流诚实失败终止，
                    #   reason 展示给用户。
                    # - 配置了 insufficient_branch：转为可路由信号——节点
                    #   success + status="insufficient"，执行流只走该分支
                    #   （如 human 澄清节点），由作者设计澄清后的续接路径。
                    abort_args = self._find_abort_request(
                        result.get("messages") if isinstance(result, dict) else None,
                    )
                    if abort_args is not None:
                        reason = str(abort_args.get("reason") or "").strip()
                        needed = str(abort_args.get("needed_info") or "").strip()
                        parts = [p for p in (reason, f"需要补充: {needed}" if needed else "") if p]
                        abort_summary = "；".join(parts) or "Agent 判定输入信息不足以继续执行"
                        logger.warning(
                            "node_agent_abort_requested",
                            node_id=self.node_id,
                            agent_id=agent_id,
                            reason=reason,
                            needed_info=needed,
                            insufficient_branch=insufficient_branch or None,
                        )
                        if insufficient_branch:
                            # 信号模式：固定字段集与正常分支一致（API 返回体
                            # 恒定结构）。response 为 reason 汇总文本——信息
                            # 不足时无结构可依，不解析 response 契约；下游
                            # 澄清分支可用 {{agent_x.response}} /
                            # {{agent_x.needed_info}} 展示原因并收集补充信息。
                            return NodeResult(
                                success=True,
                                output={
                                    "status": "insufficient",
                                    "response": abort_summary,
                                    "agent_id": agent_id,
                                    "files": [],
                                    "usage": {},
                                    "needed_info": needed,
                                },
                                selected_branch=insufficient_branch,
                            )
                        return NodeResult(
                            success=False,
                            output={},
                            error_message=abort_summary,
                            error_code="AGENT_INPUT_INSUFFICIENT",
                        )
                    # ── 提取最终输出（response 恒为纯文本正文）──
                    # content 可能是 str（多数 provider）、标准块列表
                    # （Anthropic thinking+text 各自成块）或 GLM quirk
                    # （无视 thinking disabled，正文藏在 thinking 块的
                    # 额外 text 字段里）。统一走 extract_answer_text 提取，
                    # signature/thinking 等元数据不进 response。
                    output_content = ""
                    thinking_content = ""
                    if result.get("messages"):
                        last_msg = result["messages"][-1]
                        # 兼容 LangChain AIMessage（.content）和 dict（["content"]）
                        raw_content = (
                            last_msg.get("content") if isinstance(last_msg, dict)
                            else getattr(last_msg, "content", None)
                        )
                        from app.engine.harness_integration.adapters.content import (
                            extract_answer_text,
                            extract_thinking_text,
                        )

                        output_content = extract_answer_text(raw_content)
                        # 网关违规返回的思考过程保留到节点输出（response 只含正文）
                        thinking_content = extract_thinking_text(last_msg)

                    # ── response 结构契约校验（opt-in）──
                    # JSON 解析/枚举校验是确定性信号（区别于启发式文本检测）：
                    # 违规 → 同 thread 追加反馈重跑一轮（至多 1 次）；
                    # 二次仍违规 → 节点诚实失败（不与 max_retry 的原样重跑混语义）。
                    parsed_output: dict[str, Any] | list[Any] | None = None
                    if response_schema:
                        parsed_output, schema_error = self._parse_structured_output(
                            output_content, response_schema,
                        )
                        if schema_error:
                            if schema_retry_used:
                                logger.warning(
                                    "node_agent_schema_violation",
                                    node_id=self.node_id,
                                    agent_id=agent_id,
                                    error=schema_error,
                                )
                                return NodeResult(
                                    success=False,
                                    output={},
                                    error_message=(
                                        "Agent 输出不符合 response_schema 契约: "
                                        f"{schema_error}"
                                    ),
                                    error_code="AGENT_OUTPUT_SCHEMA_VIOLATION",
                                )
                            schema_retry_used = True
                            feedback = (
                                "你上一轮的最终输出不符合约定的 response JSON 契约："
                                f"{schema_error}。请重新给出最终答复：只输出符合契约"
                                "的 JSON（response 的值），JSON 之外不要有任何文字；"
                                "若信息确实不足无法完成，请调用 abort_workflow"
                                "(reason, needed_info) 诚实终止。"
                            )
                            from langchain_core.messages import HumanMessage

                            pending_messages = [HumanMessage(content=feedback)]
                            last_error = (
                                f"Agent 输出不符合 response_schema 契约: {schema_error}"
                            )
                            logger.info(
                                "node_agent_schema_retry",
                                node_id=self.node_id,
                                agent_id=agent_id,
                                error=schema_error,
                            )
                            continue  # 立即带反馈重跑（不 sleep）

                    # ── 提取 Agent 工具生成的文件引用（Story 4-15）──
                    # 1) MCP / artifact-based (legacy path)
                    mcp_files = self._extract_files_from_messages(
                        result.get("messages") or [],
                    )
                    # 2) 扫描 task workspace output/ 中新生成的文件并注册到
                    #    file_library（覆盖内置工具真实行为：write_to_output
                    #    等通过 contextvars 落到本 task 专属 output/）。
                    registered_files = await self._register_task_output_files(
                        task_workspace, node_start_ts,
                        task_id=task_id, user_id=user_id,
                        register_all=is_resume,
                    )

                    # 合并去重（registered 优先，保留最新的 file_id/大小/路径）
                    files_output = self._merge_file_outputs(
                        mcp_files, registered_files,
                    )

                    # ── 组装节点输出（API 返回体恒定结构）──
                    # 固定字段集在正常/insufficient 分支间恒定，下游引用永不
                    # 踩空：status / response / agent_id / files / usage /
                    # needed_info（thinking 非空才写，调试性可选字段）。
                    # response 在契约模式下是解析后的原生 dict/list——下游
                    # {{node.response.field.sub}} 原生取值，无深解析依赖。
                    output: dict[str, Any] = {
                        "status": "ok",
                        "response": parsed_output
                        if parsed_output is not None
                        else output_content,
                        "agent_id": agent_id,
                        "files": files_output,
                        # Token usage from harness (execution.py puts mw.summary
                        # into result["usage"]); surfaced for timeline + task total.
                        "usage": result.get("usage") or {},
                        "needed_info": "",
                    }
                    if thinking_content:
                        # GLM 等网关无视 thinking disabled 时仍返回思考过程；
                        # 保留到节点输出供调试/展示，仅在非空时写入。
                        output["thinking"] = thinking_content

                    return NodeResult(
                        success=True,
                        output=output,
                    )
                except TimeoutError:
                    last_error = f"Agent 执行超时 ({timeout_ms}ms)"
                    logger.warning("node_agent_timeout", node_id=self.node_id, attempt=attempt + 1)
                except Exception as exc:
                    last_error = f"Agent 执行失败: {exc}"
                    logger.error("node_agent_failed", node_id=self.node_id, error=str(exc), attempt=attempt + 1)

                if attempt < total_attempts - 1:
                    logger.info("agent_retry", node_id=self.node_id, attempt=attempt + 1, max_retry=max_retry)
                    await asyncio.sleep(retry_delay_ms / 1000)
        finally:
            # workspace context 注入已由 harness 的 resolve_harness_context 接管，
            # 无需在此 reset（原 reset_workspace_context 调用已移除）。
            pass

        return NodeResult(
            success=False,
            output={},
            error_message=last_error or "Agent 执行失败",
        )

    @staticmethod
    def _iter_interrupt_payloads(interrupts: Any) -> list[dict]:
        """从 __interrupt__ 集合提取 payload dict 列表。

        LangGraph 返回 Interrupt 对象列表（payload 在 .value），防御性兼容
        裸 dict（测试桩/版本差异）。
        """
        payloads: list[dict] = []
        if not interrupts:
            return payloads
        items = interrupts if isinstance(interrupts, (list, tuple)) else [interrupts]
        for item in items:
            payload = item if isinstance(item, dict) else getattr(item, "value", None)
            if isinstance(payload, dict):
                payloads.append(payload)
        return payloads

    @classmethod
    def _interrupt_is_cancel(cls, interrupts: Any) -> bool:
        """cancel_checker 的挂起 payload 为 {"reason": "cancelled"}。"""
        return any(p.get("reason") == "cancelled" for p in cls._iter_interrupt_payloads(interrupts))

    @classmethod
    def _summarise_interrupts(cls, interrupts: Any) -> str:
        """把 interrupt payload 压缩成一句话，供错误信息/日志展示。"""
        payloads = cls._iter_interrupt_payloads(interrupts)
        if not payloads:
            return "未知 interrupt"
        summaries = []
        for p in payloads:
            question = p.get("question") or p.get("workflow_name") or p.get("type") or "interrupt"
            summaries.append(str(question))
        return "; ".join(summaries)[:200]

    @staticmethod
    def _find_abort_request(messages: Any) -> dict | None:
        """扫描 messages 中的 abort_workflow tool_call，返回其 args。

        仅当 abort_workflow **已被执行**（存在 tool_call_id 匹配的
        ToolMessage）才判定有效——恢复（resume）线程里可能残留仅声明、
        尚未执行的 abort tool_call（取消发生在工具执行前），此时 agent
        恢复后已重新决策，历史 abort 不构成当前节点失败。tool_call 是
        机器可读的确定性信号（比文本检测可靠）。兼容 AIMessage
        （.tool_calls）与 dict（["tool_calls"]）两种形态。
        """
        if not messages:
            return None
        from app.engine.agent.workflow_executor import ABORT_TOOL_NAME

        executed_ids: set[str] = set()
        for msg in messages:
            tool_call_id = (
                msg.get("tool_call_id")
                if isinstance(msg, dict)
                else getattr(msg, "tool_call_id", None)
            )
            if tool_call_id:
                executed_ids.add(tool_call_id)

        for msg in messages:
            if isinstance(msg, dict):
                tool_calls = msg.get("tool_calls") or []
            else:
                tool_calls = getattr(msg, "tool_calls", None) or []
            for tc in tool_calls:
                if not isinstance(tc, dict):
                    continue
                if tc.get("name") != ABORT_TOOL_NAME:
                    continue
                if tc.get("id") not in executed_ids:
                    # 仅声明的 tool_call（工具未执行）不算：中断时刻未真正
                    # abort，恢复后 agent 重新决策，不应按历史 abort 判失败。
                    continue
                args = tc.get("args")
                return args if isinstance(args, dict) else {}
        return None

    @staticmethod
    def _parse_structured_output(
        response: str,
        schema: dict[str, Any],
    ) -> tuple[dict[str, Any] | list[Any] | None, str]:
        """按 response_schema 契约校验最终输出，返回 (parsed, error)。

        agent 输出是类 API 返回体：LLM 最终回复即 response 字段的值。
        schema = {type: "object"|"array", fields: [...]}，嵌套最多两层
        （第一层 object 字段可带子 fields，第二层不可再嵌）。契约违背是
        确定性信号（JSON 解析失败/必填缺失/枚举越界/类型不符均为机器
        判定，无启发式误报），调用方据此带反馈重试或诚实失败。未知字段
        宽容放行（LLM 附加字段不破坏下游对已声明字段的消费）。
        """
        value, extract_err = _extract_json_payload(response)
        if value is None:
            return None, extract_err

        if schema.get("type") == "array":
            if not isinstance(value, list):
                return None, "response 契约要求 JSON 数组，实际不是数组"
            fields = schema.get("fields") or []
            for idx, item in enumerate(value):
                item_err = _validate_schema_fields(item, fields, path=f"[{idx}]")
                if item_err:
                    return None, item_err
            return value, ""

        if not isinstance(value, dict):
            return None, "response 契约要求 JSON 对象，实际不是对象"
        err = _validate_schema_fields(value, schema.get("fields") or [])
        if err:
            return None, err
        return value, ""

    @staticmethod
    def _merge_file_outputs(
        mcp_files: list[dict[str, Any]],
        registered_files: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Merge MCP-artifact files and task-workspace-registered files.

        Both sources may report the same file; registered files win because
        they have authoritative ``file_id``/``storage_key`` values from
        file_library. Deduplication is by ``file_id`` when present,
        otherwise by ``name``.
        """
        seen_ids: set[str] = set()
        seen_names: set[str] = set()
        merged: list[dict[str, Any]] = []

        for entry in registered_files:
            fid = entry.get("file_id", "")
            name = entry.get("name", "")
            if fid and fid in seen_ids:
                continue
            if not fid and name in seen_names:
                continue
            merged.append(entry)
            if fid:
                seen_ids.add(fid)
            if name:
                seen_names.add(name)

        for entry in mcp_files:
            fid = entry.get("file_id", "")
            name = entry.get("name", "")
            if fid and fid in seen_ids:
                continue
            if not fid and name in seen_names:
                continue
            merged.append(entry)
            if fid:
                seen_ids.add(fid)
            if name:
                seen_names.add(name)

        return merged

    async def _register_task_output_files(
        self,
        task_workspace: Any,
        node_start_ts: float,
        *,
        task_id: str,
        user_id: str,
        register_all: bool = False,
    ) -> list[dict[str, Any]]:
        """Register files newly created in the task workspace to file_library.

        Story 4-15: After the Agent graph finishes, we walk
        ``task_workspace.output_dir`` and register every file with an
        mtime > ``node_start_ts`` to ``file_library`` as
        ``origin_kind='workflow_run'`` / ``origin_id=task_id``. Files
        already registered with the same ``sha256`` for this task are
        skipped (deduplication).

        Returns:
            A list of dicts with ``file_id``, ``name``, ``size``,
            ``mime_type``, ``storage_key`` — the canonical output shape
            downstream nodes consume via ``{{ agent_node.files[i].file_id }}``.
        """
        if not task_workspace.output_dir.exists():
            return []

        # Local import to avoid top-level cycle (file_service depends on
        # db, which loads settings — safe at runtime, not at import time).
        from app.models.file_library import FileConsumerKind
        from app.services.file_service import FileService
        from app.services.file_storage import LocalFileStorage

        file_service = FileService(LocalFileStorage())

        # Pre-fetch known sha256s for this task to avoid registering the
        # same file twice (e.g. on node retry).
        existing_cursor = file_service._file_refs().find(
            {
                "origin_kind": FileConsumerKind.WORKFLOW_RUN.value,
                "origin_id": task_id,
            },
            {"sha256": 1, "_id": 0},
        )
        existing_docs = await existing_cursor.to_list(length=None)
        seen_sha256: set[str] = {doc["sha256"] for doc in existing_docs if doc.get("sha256")}

        registered: list[dict[str, Any]] = []
        import hashlib
        import mimetypes

        for path in sorted(task_workspace.output_dir.rglob("*")):
            if not path.is_file():
                continue
            try:
                stat = path.stat()
            except FileNotFoundError:
                # File was removed between rglob and stat — skip.
                continue
            if not register_all and stat.st_mtime < node_start_ts:
                # Pre-existing file from an earlier run; do not re-register.
                # 恢复场景(register_all=True)跳过此过滤——agent 在上次执行
                # （被取消前）已写入的文件 mtime 早于本次恢复时间。
                continue
            try:
                data = path.read_bytes()
            except OSError as exc:
                logger.warning(
                    "agent_node_read_output_failed",
                    node_id=self.node_id,
                    path=str(path),
                    error=str(exc),
                )
                continue

            sha256 = hashlib.sha256(data).hexdigest()
            if sha256 in seen_sha256:
                continue

            mime_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
            try:
                fref = await file_service.create(
                    data=data,
                    filename=path.name,
                    mime_type=mime_type,
                    owner_user_id=user_id,
                    origin_kind=FileConsumerKind.WORKFLOW_RUN,
                    origin_id=task_id,
                )
            except Exception as exc:  # noqa: BLE001 — surface but don't crash node
                logger.error(
                    "agent_node_register_file_failed",
                    node_id=self.node_id,
                    path=str(path),
                    error=str(exc),
                )
                continue

            seen_sha256.add(sha256)
            registered.append({
                "file_id": fref.id,
                "name": fref.name,
                "size": fref.size,
                "mime_type": fref.mime_type,
                "storage_key": fref.storage_key,
            })
            logger.info(
                "agent_node_file_registered",
                node_id=self.node_id,
                task_id=task_id,
                file_id=fref.id,
                name=fref.name,
                size=fref.size,
            )

        return registered

    def _extract_files_from_messages(self, messages: list) -> list[dict[str, Any]]:
        """Scan LangGraph messages for tool-emitted file references.

        Tools may attach a ``files`` array to their ToolMessage result. We
        normalise each entry to a stable ``{file_id, name, mime_type, size}``
        shape and de-duplicate by ``file_id`` so downstream consumers see a
        single canonical list regardless of how many tool calls emitted the
        same file.
        """
        collected: list[dict[str, Any]] = []
        seen: set[str] = set()

        for msg in messages:
            payload: Any = None
            if isinstance(msg, dict):
                payload = msg.get("artifact") or msg.get("additional_kwargs")
                if payload is None and isinstance(msg.get("content"), (dict, list)):
                    payload = msg.get("content")
            else:
                if getattr(msg, "artifact", None) is not None:
                    payload = msg.artifact
                elif getattr(msg, "additional_kwargs", None):
                    payload = msg.additional_kwargs

            if payload is None:
                continue

            if isinstance(payload, list):
                file_entries = [p for p in payload if isinstance(p, dict) and p.get("file_id")]
            elif isinstance(payload, dict) and isinstance(payload.get("files"), list):
                file_entries = [p for p in payload["files"] if isinstance(p, dict) and p.get("file_id")]
            else:
                continue

            for entry in file_entries:
                file_id = str(entry.get("file_id", ""))
                if not file_id or file_id in seen:
                    continue
                seen.add(file_id)
                # size may be int or numeric string; coerce defensively
                try:
                    size_val = int(entry.get("size", 0) or 0)
                except (TypeError, ValueError):
                    size_val = 0
                collected.append({
                    "file_id": file_id,
                    "name": str(entry.get("name", "")),
                    "mime_type": str(entry.get("mime_type", "application/octet-stream")),
                    "size": size_val,
                })

        return collected


# ── Tool ──


class ToolNodeExecutor(BaseNodeExecutor):
    """Invoke a registered tool from the tool pool.

    Config::

        {
            "tool_id": "tool_xxx",
            "params": { "key": "{{ node.field }}" },  # resolved at runtime
            "timeout_ms": 30000,
            "retry_policy": { "max_retries": 3, "backoff_ms": 1000 }
        }
    """

    async def execute(self, variables: dict[str, Any]) -> NodeResult:
        tool_id = self.node_config.get("tool_id", "")
        if not tool_id:
            return NodeResult(success=False, output={}, error_message="tool_id 未配置")

        # Resolve params from variables
        from app.engine.workflow.expression import ExpressionEngine

        engine = ExpressionEngine(variables)
        raw_params = self.node_config.get("params", {})
        resolved_params = engine.resolve_dict(raw_params) if isinstance(raw_params, dict) else raw_params

        try:
            # Fetch tool from MongoDB
            from app.db.mongodb import get_database

            db = get_database()
            tool_doc = await db["tools"].find_one({"$or": [{"_id": tool_id}, {"name": tool_id}]})
            if tool_doc is None:
                return NodeResult(
                    success=False,
                    output={},
                    error_message=f"Tool {tool_id} 不存在",
                )

            # Execute based on tool source type
            source = tool_doc.get("source", "markdown")

            if source == "mcp":
                # MCP tool — invoke via MCP client
                return await self._execute_mcp_tool(tool_doc, resolved_params, variables)
            else:
                # Markdown/Skill tool — return instructions as context
                return NodeResult(
                    success=True,
                    output={
                        "tool_name": tool_doc.get("name", ""),
                        "tool_description": tool_doc.get("description", ""),
                        "instructions": tool_doc.get("instructions", ""),
                        "params": resolved_params,
                        "note": "工具的完整执行由 Agent 推理循环处理",
                    },
                )
        except Exception as exc:
            logger.error("node_tool_failed", node_id=self.node_id, error=str(exc))
            return NodeResult(
                success=False,
                output={},
                error_message=f"Tool 调用失败: {exc}",
            )

    async def _execute_mcp_tool(
        self,
        tool_doc: dict[str, Any],
        params: dict[str, Any],
        variables: dict[str, Any] | None = None,
    ) -> NodeResult:
        """Execute an MCP-sourced tool with timeout protection and retry.

        外部触发的 Workflow 里，从 system 变量取 user_token/user_id，set
        ContextVar 让 interceptor 走凭证兑换；内部触发（无 user_token）则
        interceptor 自动降级用静态凭证。
        """
        try:
            # 注入终端用户身份 ContextVar（若外部触发），让 MCP 调用走凭证兑换。
            # 使用 set/reset 配对，确保不污染后续节点。
            from agent_flow_harness.mcp.user_token_context import (
                reset_token_record_id_context,
                set_token_record_id_context,
            )

            from app.engine.tool.mcp_tool_cache import get_mcp_tools_cached

            sys_vars = (variables or {}).get("system", {}) or {}
            ext_user_id = sys_vars.get("user_id", "")
            ext_user_token = sys_vars.get("user_token", "") or None
            tri_token = None
            if ext_user_token and ext_user_id:
                tri_token = set_token_record_id_context(ext_user_id)

            # Get connection ID from tool doc
            conn_id = tool_doc.get("mcp_connection_id", "")
            if not conn_id:
                return NodeResult(
                    success=False,
                    output={},
                    error_message="MCP 工具缺少 connection_id",
                )

            # Get tools for this connection
            tools = await get_mcp_tools_cached([conn_id])

            # Find the matching tool by name
            tool_name = tool_doc.get("name", "")
            matching = [t for t in tools if t.name == tool_name or t.name.endswith(f"__{tool_name}")]
            if not matching:
                return NodeResult(
                    success=False,
                    output={},
                    error_message=f"MCP 工具 {tool_name} 未在连接中找到",
                )

            tool = matching[0]

            # Retry and timeout configuration
            timeout_ms = self.node_config.get("timeout_ms", 30000)
            timeout_s = timeout_ms / 1000
            retry_policy = self.node_config.get("retry_policy", {})
            max_retries = retry_policy.get("max_retries", 0)
            backoff_ms = retry_policy.get("backoff_ms", 1000)

            last_error: str | None = None
            for attempt in range(1 + max_retries):
                try:
                    result = await asyncio.wait_for(tool.ainvoke(params), timeout=timeout_s)
                    return NodeResult(success=True, output={"result": result, "tool_id": tool_doc["_id"]})
                except TimeoutError:
                    last_error = f"MCP 工具执行超时 ({timeout_ms}ms)"
                    logger.warning("mcp_tool_timeout", node_id=self.node_id, attempt=attempt + 1)
                except Exception as exc:
                    last_error = f"MCP 工具执行失败: {exc}"
                    logger.error("mcp_tool_execution_failed", node_id=self.node_id, error=str(exc), attempt=attempt + 1)

                if attempt < max_retries:
                    logger.info("tool_retry", node_id=self.node_id, attempt=attempt + 1, max_retries=max_retries)
                    await asyncio.sleep(backoff_ms / 1000)

            return NodeResult(
                success=False,
                output={},
                error_message=last_error or "MCP 工具执行失败",
            )

        except Exception as exc:
            logger.error("mcp_tool_setup_failed", error=str(exc))
            return NodeResult(
                success=False,
                output={},
                error_message=f"MCP 工具执行失败: {exc}",
            )
        finally:
            # reset ContextVar（仅当之前 set 过）
            if tri_token is not None:
                reset_token_record_id_context(tri_token)


# ── Gateway ──


class GatewayNodeExecutor(BaseNodeExecutor):
    """Evaluate conditions and select a branch.

    Config::

        {
            "conditions": [
                { "expression": "{{ node_1.result.status }}", "operator": "==", "expected": "ok", "target": "node_3" },
                { "expression": "{{ node_1.count }}", "operator": ">", "expected": 10, "target": "node_4" }
            ],
            "default_branch": "node_5",
            "fallback_on_error": "node_5"
        }

    Each condition has an ``operator`` (one of ==, !=, >, <, >=, <=; defaults to
    ``"=="`` for backward compatibility). ``==``/``!=`` perform case-insensitive
    comparison for strings (e.g. ``"APPROVE"`` matches ``"approve"``), and keep
    the bool-coercion behavior for boolean expected values.

    Conditions are evaluated in order; the first match wins.
    """

    async def execute(self, variables: dict[str, Any]) -> NodeResult:
        conditions = self.node_config.get("conditions", [])
        default_branch = self.node_config.get("default_branch", "")
        fallback = self.node_config.get("fallback_on_error", default_branch)

        from app.engine.workflow.expression import ExpressionEngine

        engine = ExpressionEngine(variables)

        for cond in conditions:
            expression = cond.get("expression", "")
            expected = cond.get("expected", True)
            op = cond.get("operator", "==")  # default "==" for backward compat
            target = cond.get("target", "")

            try:
                actual = engine.resolve(expression)
                if _gateway_compare(actual, expected, op):
                    logger.debug(
                        "gateway_match",
                        node_id=self.node_id,
                        target=target,
                    )
                    return NodeResult(
                        success=True,
                        output={"selected_branch": target, "condition": expression},
                        selected_branch=target,
                    )
            except Exception as exc:
                logger.warning("gateway_condition_error", node_id=self.node_id, expression=expression, error=str(exc))
                continue

        # No condition matched — use fallback
        logger.debug("gateway_fallback", node_id=self.node_id, target=fallback)
        return NodeResult(
            success=True,
            output={"selected_branch": fallback, "condition": "default"},
            selected_branch=fallback,
        )


# Operator → implementation for ordering comparisons (>, <, >=, <=).
# == and != are handled specially in ``_gateway_compare`` for case-insensitivity.
_GATEWAY_ORDER_OPS: dict[str, Any] = {
    ">": _operator.gt,
    "<": _operator.lt,
    ">=": _operator.ge,
    "<=": _operator.le,
}


def _gateway_compare(actual: Any, expected: Any, op: str) -> bool:
    """Compare ``actual`` against ``expected`` using operator ``op``.

    - ``==`` / ``!=``: case-insensitive for str vs str (e.g. ``"APPROVE"`` ==
      ``"approve"``); bool expected coerces ``actual`` via ``bool()``; other
      types use native equality. This preserves the pre-operator behavior while
      adding case-insensitivity for the common "match approval decision" case.
    - ``contains`` / ``not_contains``: 子串包含（actual 是字符串、expected 是
      子串）或元素包含（actual 是列表/元组、expected 是元素）。字符串场景下
      同样不区分大小写，与 ``==`` 保持一致。类型不匹配（如 actual 不是
      str/list）视为不匹配。
    - ``>`` / ``<`` / ``>=`` / ``<=``: native ordering comparison; a
      ``TypeError`` (incomparable types) is treated as no-match.
    - unknown operator: no-match (returns ``False``).
    """
    if op in ("==", "!="):
        a: Any
        e: Any
        if isinstance(actual, str) and isinstance(expected, str):
            a, e = actual.lower(), expected.lower()
        elif isinstance(expected, bool):
            a, e = bool(actual), expected
        else:
            a, e = actual, expected
        return a == e if op == "==" else a != e

    if op in ("contains", "not_contains"):
        # 字符串子串包含（大小写不敏感）
        if isinstance(actual, str) and isinstance(expected, str):
            contained = expected.lower() in actual.lower()
        # 列表/元组元素包含（等值比较，不递归）
        elif isinstance(actual, (list, tuple)):
            contained = expected in actual
        else:
            # 类型不匹配（如 actual 为 None/数字/字典）一律视为不包含
            contained = False
        return contained if op == "contains" else not contained

    func = _GATEWAY_ORDER_OPS.get(op)
    if func is None:
        return False
    try:
        return bool(func(actual, expected))
    except TypeError:
        return False


# ── Parallel ──


class ParallelNodeExecutor(BaseNodeExecutor):
    """Execute multiple branches in parallel and merge results.

    Config::

        {
            "branches": [
                { "id": "branch_1", "start_node": "node_a" },
                { "id": "branch_2", "start_node": "node_b" }
            ],
            "join_strategy": "all",      # all | any | n-of-m
            "join_count": null,           # for n-of-m strategy
            "scope": "shared"             # shared | isolated
        }

    The actual branch execution is handled by the WorkflowEngine.
    This executor only manages the parallel fan-out configuration.
    """

    async def execute(self, variables: dict[str, Any]) -> NodeResult:
        branches = self.node_config.get("branches", [])
        scope = self.node_config.get("scope", "shared")

        branch_ids = [b.get("id", f"branch_{i}") for i, b in enumerate(branches)]
        start_nodes = {
            b.get("id", f"branch_{i}"): b.get("start_node", "")
            for i, b in enumerate(branches)
        }

        return NodeResult(
            success=True,
            output={
                "branches": branch_ids,
                "start_nodes": start_nodes,
                "join_strategy": self.node_config.get("join_strategy", "all"),
                "join_count": self.node_config.get("join_count"),
                "scope": scope,
            },
        )


# ── Subflow ──


class SubflowNodeExecutor(BaseNodeExecutor):
    """Create a child Task from another Workflow and wait for results.

    Config::

        {
            "workflow_id": "wf_xxx",
            "input_mapping": { "var_1": "{{ node_1.result }}" },
            "result_mapping": { "output": "{{ result.field }}" },
            "timeout_ms": 600000
        }
    """

    async def execute(self, variables: dict[str, Any]) -> NodeResult:
        workflow_id = self.node_config.get("workflow_id", "")
        if not workflow_id:
            return NodeResult(success=False, output={}, error_message="workflow_id 未配置")

        # Resolve input from variables
        from app.engine.workflow.expression import ExpressionEngine

        engine = ExpressionEngine(variables)
        input_mapping = self.node_config.get("input_mapping", {})
        resolved_input = engine.resolve_dict(input_mapping) if isinstance(input_mapping, dict) else {}

        try:
            from app.db.mongodb import get_database
            from app.engine.workflow.engine import WorkflowEngine
            from app.models.task import Task

            db = get_database()

            # Fetch child workflow definition
            child_wf_doc = await db["workflows"].find_one({"_id": workflow_id})
            if child_wf_doc is None:
                return NodeResult(
                    success=False,
                    output={},
                    error_message=f"Workflow {workflow_id} 不存在",
                )

            # Build child task document.
            # Inherit task_id / user_id / call_chain from the parent's system
            # variables (Story 4-15: system vars live under variables['system']).
            sys_vars = variables.get("system", {}) or {}
            parent_task_id = sys_vars.get("task_id", "")
            parent_call_chain = sys_vars.get("call_chain", []) or []
            child_task = Task(
                workflow_id=workflow_id,
                input=resolved_input,
                created_by=sys_vars.get("user_id", "system"),
                created_by_type="system",
                parent_task_id=parent_task_id,
                call_chain=parent_call_chain + [self.node_id],
            )
            child_doc = child_task.model_dump(by_alias=True)

            # Persist the child task so it is visible in the task list, can be
            # queried/cancelled, and so execute_task's internal transition_task
            # calls (which update DB by task_id) have a document to update.
            await db["tasks"].insert_one(child_doc)

            # Execute child workflow with timeout
            timeout_ms = self.node_config.get("timeout_ms", 600000)
            child_engine = WorkflowEngine()
            try:
                child_output = await asyncio.wait_for(
                    child_engine.execute_task(child_doc, child_wf_doc),
                    timeout=timeout_ms / 1000,
                )
            except TimeoutError:
                # Mark the persisted child task as failed on timeout
                from app.models.task import TaskError, TaskStatus

                await db["tasks"].update_one(
                    {"_id": child_doc["_id"]},
                    {
                        "$set": {
                            "status": TaskStatus.FAILED.value,
                            "error": TaskError(
                                error_message=f"Subflow 执行超时 ({timeout_ms}ms)",
                                error_code="SUBFLOW_TIMEOUT",
                            ).model_dump(),
                        },
                    },
                )
                return NodeResult(
                    success=False,
                    output={},
                    error_message=f"Subflow 执行超时 ({timeout_ms}ms)",
                )

            # Map results using result_mapping against child variable pool
            result_mapping = self.node_config.get("result_mapping", {})
            if result_mapping and child_engine._pool is not None:
                child_vars = child_engine._pool.get_all()
                child_expr_engine = ExpressionEngine(child_vars)
                resolved_result = child_expr_engine.resolve_dict(result_mapping)
            else:
                resolved_result = child_output

            return NodeResult(
                success=True,
                output={
                    "child_task_id": child_doc["_id"],
                    "child_output": resolved_result,
                    "workflow_id": workflow_id,
                },
            )
        except Exception as exc:
            logger.error("node_subflow_failed", node_id=self.node_id, error=str(exc))
            return NodeResult(
                success=False,
                output={},
                error_message=f"Subflow 执行失败: {exc}",
            )


# ── Factory ──

_NODE_EXECUTOR_MAP: dict[str, type[BaseNodeExecutor]] = {
    "start": StartNodeExecutor,
    "end": EndNodeExecutor,
    "agent": AgentNodeExecutor,
    "tool": ToolNodeExecutor,
    "gateway": GatewayNodeExecutor,
    "human": None,  # lazy-loaded in get_node_executor to avoid circular import
    "parallel": ParallelNodeExecutor,
    "subflow": SubflowNodeExecutor,
    "kb_search": None,  # lazy-loaded (lives in nodes/ subdir, imports BaseNodeExecutor)
}

# Lazy import cache
_human_executor_cls: type[BaseNodeExecutor] | None = None
_kb_search_executor_cls: type[BaseNodeExecutor] | None = None


def get_node_executor(node_type: str, node_id: str, node_config: dict[str, Any]) -> BaseNodeExecutor:
    """Factory: return the appropriate executor for *node_type*.

    Raises ``ValueError`` for unknown node types.
    """
    cls = _NODE_EXECUTOR_MAP.get(node_type)
    if cls is None and node_type not in ("human", "kb_search"):
        raise ValueError(f"未知的节点类型: {node_type}")

    if node_type == "human":
        global _human_executor_cls
        if _human_executor_cls is None:
            from app.engine.workflow.nodes.human import HumanNodeExecutor
            _human_executor_cls = HumanNodeExecutor
        return _human_executor_cls(node_id=node_id, node_config=node_config)

    if node_type == "kb_search":
        global _kb_search_executor_cls
        if _kb_search_executor_cls is None:
            from app.engine.workflow.nodes.kb_search import KbSearchNodeExecutor
            _kb_search_executor_cls = KbSearchNodeExecutor
        return _kb_search_executor_cls(node_id=node_id, node_config=node_config)

    return cls(node_id=node_id, node_config=node_config)  # type: ignore[return-value]
