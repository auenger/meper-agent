"""harness context assembly — resolve & release harness injection objects.

Transforms backend application objects (agent doc, LLM config, tools,
sandbox settings, workspace) into the injection dict that the harness
graph expects. This is the "装配" half of the adapter layer.
"""
from __future__ import annotations

from typing import Any

from loguru import logger

from app.core.perf import phases_if_debug, timed_phase


def get_checkpointer() -> Any:
    """返回 harness 的 checkpointer 单例。

    harness 默认用 MemorySaver,应用层在 lifespan 启动时通过
    ``configure_checkpointer`` 覆盖为 MongoDBSaver。
    """
    from agent_flow_harness import get_checkpointer as _harness_get_checkpointer

    return _harness_get_checkpointer()


# 运行时实际注入的 harness 内建工具(单一事实源)。
# 端点 `/api/v1/tools/builtin` 与 `resolve_harness_context` 共同引用此名单,
# 保证「端点展示的 = 运行时注入的」。
_INJECTED_BUILTIN_TOOL_NAMES: tuple[str, ...] = (
    "bash", "read", "write", "edit", "glob", "grep", "ask_clarification",
    "run_code", "parse_file", "view_image",
)

# 可配子集 —— 用户可在 Agent 配置页勾选的内建工具(其余始终开启、不可关闭)。
# ask_clarification 在聊天上下文始终开启(能力型工具,关闭会导致 Agent 无法澄清);
# 工作流上下文(execution_context="workflow")例外——见 _resolve_builtin_tools。
# run_code(代码即工具编排)默认启用:新建 Agent 的 DEFAULT_BUILTIN_CONFIG
# 自动包含;存量 Agent 在配置页手动勾选后生效。
_CONFIGURABLE_BUILTIN_TOOL_NAMES: frozenset[str] = frozenset(
    {"bash", "read", "write", "edit", "glob", "grep", "run_code", "parse_file"}
)

# run_code 代码内可桥接调用的工具排除名单(不进 tools_map)。
# - run_code 自身:防递归编排;
# - ask_clarification / confirm_workflow:HITL interrupt 在工作线程桥接下
#   无法挂起 graph(会退化为异常),失去人机协同语义;
# - delegate_to_subagent / load_skill:子代理/技能加载改变执行上下文,
#   不适合在代码内嵌套;
# - bash/read/write/edit/glob/grep:文件 shell 敏感面,代码内用不到。
_RUN_CODE_EXCLUDED_TOOLS: frozenset[str] = frozenset({
    "run_code", "ask_clarification", "confirm_workflow",
    "delegate_to_subagent", "load_skill",
    "bash", "read", "write", "edit", "glob", "grep",
})

# 新建 Agent 时默认启用的内建工具(白名单语义:列表中的工具才会注入)。
# 保持与 _CONFIGURABLE_BUILTIN_TOOL_NAMES 一致(按 _INJECTED_BUILTIN_TOOL_NAMES
# 的顺序),创建端点与前端默认值共同引用,作为单一事实源避免名单漂移。
DEFAULT_BUILTIN_CONFIG: tuple[str, ...] = tuple(
    n for n in _INJECTED_BUILTIN_TOOL_NAMES if n in _CONFIGURABLE_BUILTIN_TOOL_NAMES
)


def _decrypt_user_args(tool_doc: dict, user_args: dict) -> dict:
    """解密 user_args 里标记为 sensitive 的字段。

    Agent 绑定时 sensitive 字段加密存储（前缀 enc:），运行时解密。
    """
    if not user_args:
        return {}
    from app.core.crypto import CryptoError, decrypt_secret

    user_schema = tool_doc.get("user_args_schema", {})
    props = user_schema.get("properties", {})
    result = {}
    for key, value in user_args.items():
        prop = props.get(key, {})
        if prop.get("sensitive") and isinstance(value, str) and value.startswith("enc:"):
            try:
                result[key] = decrypt_secret(value[4:])
            except (CryptoError, Exception):
                result[key] = value  # 解密失败用原值
        else:
            result[key] = value
    return result


# ---------------------------------------------------------------------------
# 统一工具解析层 —— 运行时 (resolve_harness_context) 与 preview (builder.py)
# 共用这组函数,消除双路径不一致。
#
# 每个函数返回 (tools, errors),tools 是 BaseTool 列表,errors 是加载失败
# 项 [{"tool_name": str, "error": str}]。
# ---------------------------------------------------------------------------


def _resolve_builtin_tools(agent: dict, execution_context: str = "chat") -> list:
    """解析内建工具(task/workflow 工具 + harness 内建工具 + parse_file)。

    execution_context:
        "chat" — 聊天/预览语义(默认):注入 task/workflow 工具 + ask_clarification,
            Agent 可反问用户、可派发工作流。
        "workflow" — 工作流 agent 节点语义(无人值守):剥离全部交互式与任务编排
            工具 —— ask_clarification 的 interrupt 会被工作流误判为取消导致停摆;
            _TASK_TOOLS 整组(dispatch/intervene/cancel 等)会 造成循环派发或
            干预父任务 —— 改注入 abort_workflow 诚实终止通道。
            工作流嵌套的正确方式是 subflow 节点,不是 agent 内 dispatch。

    chat 语义下 task/workflow 工具始终注入;harness 内建工具与 app 层 parse_file
    按 builtin_config 白名单过滤。bash 选中时连带 read/write/edit。
    run_code 受全局开关 RUN_CODE_ENABLED 控制,关闭时视为未配置。
    """
    from agent_flow_harness import BUILTIN_TOOLS

    from app.core.config import settings
    from app.engine.agent.chart_tool import _CHART_TOOLS
    from app.engine.agent.image_tool import IMAGE_TOOL_BY_NAME
    from app.engine.agent.parse_tool import PARSE_TOOL_BY_NAME
    from app.engine.agent.workflow_executor import _TASK_TOOLS, _WORKFLOW_CONTEXT_TOOLS

    tools: list = []
    if execution_context == "workflow":
        tools += list(_WORKFLOW_CONTEXT_TOOLS)  # abort_workflow 诚实终止
    else:
        tools += list(_TASK_TOOLS)  # app-level task/workflow 工具始终注入
    tools += list(_CHART_TOOLS)  # render_chart 图表工具始终注入

    builtin_config = set(agent.get("builtin_config") or [])
    if not settings.RUN_CODE_ENABLED:
        builtin_config.discard("run_code")
    if "bash" in builtin_config:
        builtin_config |= {"read", "write", "edit"}

    for name in _INJECTED_BUILTIN_TOOL_NAMES:
        # parse_file / view_image 是 app 层工具,harness 注册表取不到,补查找表。
        tool = (
            BUILTIN_TOOLS.get(name)
            or PARSE_TOOL_BY_NAME.get(name)
            or IMAGE_TOOL_BY_NAME.get(name)
        )
        if tool is None:
            continue
        if name == "ask_clarification" and execution_context == "workflow":
            continue  # 工作流无人值守:反问会 interrupt 挂起导致工作流停摆
        if name not in _CONFIGURABLE_BUILTIN_TOOL_NAMES:
            tools.append(tool)  # 始终开启的能力型工具
        elif name in builtin_config:
            tools.append(tool)
    return tools


async def _resolve_skill_tools(
    agent: dict,
    user_id: str = "",
    session_id: str = "",
    loaded_tracker: set | None = None,
    request_id: str = "",
) -> list:
    """解析 Skill 工具(load_skill),用 harness SkillManager。

    平台用户（非渠道）且 agent 开启 user_skills_enabled 时扩展为双根：
    个人目录（users/{uid}/{name}）+ 全局池；重名解析个人优先（§5.2）。
    on_skill_loaded 回调做 load_count 埋点 + 先读后写 tracker；
    request_id 随 load 日志落库——消息级反馈（§8.2）的轮次键。
    """
    from pathlib import Path

    from agent_flow_harness import SkillManager

    from app.core.config import settings
    from app.models.compat import resolve_skill_ids

    skill_ids = resolve_skill_ids(agent)

    from app.services.tool_service import ToolService

    skills_dir = Path(settings.SKILLS_CONTAINER_DIR).expanduser()

    allowed_names: set[str] = set()
    official_load_ids: dict[str, str] = {}  # name → tool_id（官方埋点，§7.6 积分统一）
    if skill_ids:
        skill_docs = await ToolService.get_tools_by_ids(skill_ids)
        for d in skill_docs:
            if d.get("name"):
                allowed_names.add(d["name"])
                official_load_ids[d["name"]] = d["_id"]

    # 双根扩展：用户个人技能（平台用户 + agent 开关）
    extra_skill_files: dict[str, Path] = {}
    extra_skill_ids: dict[str, str] = {}
    user_enabled = (
        user_id and not user_id.startswith("channel:")
        and agent.get("user_skills_enabled", True)
    )
    if user_enabled:
        from app.services.user_skill_service import UserSkillService

        for s in await UserSkillService.enabled_skills(user_id):
            # load_skill 键 = effective_name（安装别名或原名，§7.4）
            key = s.get("effective_name") or s.get("name", "")
            content_path = s.get("content_path")  # 按 owner/全局池解析（§7.6 修复路径 bug）
            if key and content_path and key not in extra_skill_files:
                extra_skill_files[key] = Path(content_path)
                extra_skill_ids[key] = s.get("_id", "")
                allowed_names.add(key)

    if not allowed_names:
        return []

    def _on_skill_loaded(name: str) -> None:
        """load 回调：tracker（先读后写）+ load_count 埋点（用户/官方统一，§7.6）。

        埋点 fire-and-forget，但必须有异常观察（L-2）——Mongo 抖动时 load
        日志丢失直接影响该轮投票的技能派发，静默丢不可接受。
        """
        if loaded_tracker is not None:
            loaded_tracker.add(name)
        import asyncio

        from app.services.user_skill_service import UserSkillService

        try:
            loop = asyncio.get_running_loop()
            usk_id = extra_skill_ids.get(name)
            tool_id = official_load_ids.get(name)
            coro = (
                UserSkillService.record_load(
                    skill_id=usk_id, name=name, user_id=user_id, session_id=session_id,
                    request_id=request_id,
                )
                if usk_id
                else UserSkillService.record_official_load(
                    tool_id=tool_id, name=name, user_id=user_id, session_id=session_id,
                    request_id=request_id,
                )
                if tool_id
                else None
            )
            if coro is None:
                return

            def _on_done(task: asyncio.Task) -> None:
                if not task.cancelled() and task.exception() is not None:
                    logger.error(
                        "skill_load_tracking_failed",
                        skill=name, user_id=user_id, session_id=session_id,
                        error=repr(task.exception()),
                    )

            loop.create_task(coro).add_done_callback(_on_done)
        except RuntimeError:
            pass  # 无运行循环（如 preview）——跳过埋点

    skill_mgr = SkillManager(
        skills_dir=skills_dir,
        base_path_prefix=settings.SANDBOX_CONTAINER_SKILLS_DIR if settings.SANDBOX_ENABLED else None,
        extra_skill_files=extra_skill_files or None,
        on_skill_loaded=_on_skill_loaded,
    )
    skill_mgr.set_allowed(allowed_names)
    return [skill_mgr.make_load_tool()]


async def _resolve_kb_tools(agent: dict) -> list:
    """解析知识库工具(tree: kb_glob/grep/read, vector: kb_search)。"""
    from pathlib import Path

    from app.engine.kb.tree.fs import get_kb_base_path
    from app.engine.kb.tree.manager import KbManager
    from app.engine.kb.vector.search_manager import KbSearchManager

    kb_ids = agent.get("knowledge_base_ids") or []
    if not kb_ids:
        return []

    from app.db.mongodb import get_database

    kb_docs = await get_database()["knowledge_bases"].find(
        {"_id": {"$in": kb_ids}}
    ).to_list(len(kb_ids))

    tools: list = []

    kb_roots: dict[str, Path] = {
        d["_id"]: get_kb_base_path(d["_id"])
        for d in kb_docs
        if d.get("type", "tree") == "tree"
    }
    if kb_roots:
        tools.extend(KbManager(kb_roots).make_tools())

    vector_infos: dict[str, str] = {
        d["_id"]: f"{d.get('name', '')} — {d.get('description', '')}".strip(" —")
        for d in kb_docs
        if d.get("type") == "vector"
    }
    if vector_infos:
        tools.extend(KbSearchManager(vector_infos).make_tools())

    return tools


async def _resolve_mcp_tools(agent: dict) -> tuple[list, list[dict]]:
    """解析 MCP 工具,逐连接走缓存层加载。

    单个连接失败收集到 errors 不中断其他连接。
    用 get_mcp_tools_cached(5 分钟 TTL),与 preview / workflow node 一致。
    """
    mcp_connection_ids = agent.get("mcp_connection_ids") or []
    if not mcp_connection_ids:
        return [], []

    from app.engine.tool.mcp_tool_cache import get_mcp_tools_cached

    all_tools: list = []
    errors: list[dict] = []
    for conn_id in mcp_connection_ids:
        try:
            tools = await get_mcp_tools_cached([conn_id])
            if not tools:
                # MCP server 没返回工具——静默跳过（可能暂时离线），
                # 不作为错误发给前端。只有真正调用时才报错。
                logger.warning("mcp_no_tools", connection_id=conn_id)
            all_tools.extend(tools)
        except Exception as exc:
            # MCP 连接失败——记日志但不发给前端（避免每次聊天都弹错误）。
            # 用户没用到这个 MCP 时不应看到错误。
            logger.warning("mcp_connection_load_failed", connection_id=conn_id, error=str(exc))
    return all_tools, errors


async def _resolve_custom_tools(agent: dict) -> tuple[list, list[dict]]:
    """解析自定义工具(openapi/code/prebuilt),含 user_args 解密。

    单个工具构建失败收集到 errors 不中断其他工具。
    """
    custom_tools = agent.get("custom_tools") or []
    if not custom_tools:
        return [], []

    from app.engine.tool.tool_builder import build_tool
    from app.services.tool_service import ToolService

    # 批量查 tool docs,避免循环内重复查询
    tool_ids = [b.get("tool_id", "") for b in custom_tools if b.get("tool_id")]
    if not tool_ids:
        return [], []
    docs = await ToolService.get_tools_by_ids(tool_ids)
    docs_by_id = {d.get("_id"): d for d in docs if d.get("_id")}

    all_tools: list = []
    errors: list[dict] = []
    for binding in custom_tools:
        tool_id = binding.get("tool_id", "")
        if not tool_id:
            continue
        doc = docs_by_id.get(tool_id)
        if not doc:
            errors.append({"tool_name": f"custom:{tool_id}", "error": "自定义工具不存在"})
            continue
        name = doc.get("name", tool_id)
        user_args = _decrypt_user_args(doc, binding.get("user_args", {}))
        try:
            tool = await build_tool(doc, user_args=user_args)
            if tool is not None:
                all_tools.append(tool)
            else:
                errors.append({"tool_name": name, "error": "工具构建返回空"})
        except Exception as exc:
            logger.warning("custom_tool_build_failed", tool_id=tool_id, error=str(exc))
            errors.append({"tool_name": name, "error": f"工具构建失败: {exc}"})
    return all_tools, errors


async def resolve_all_tools(
    agent: dict,
    user_id: str = "",
    session_id: str = "",
    loaded_tracker: set | None = None,
    request_id: str = "",
    execution_context: str = "chat",
    phases: dict[str, int] | None = None,
) -> tuple[list, list[dict]]:
    """统一工具解析入口 —— 运行时和 preview 共用。

    传 user_id（平台用户）时额外装配：load_skill 双根（个人技能）+
    skill_manage / memory 常驻工具（§2）；preview 不传则保持原工具集。

    execution_context 透传给 _resolve_builtin_tools：workflow 上下文剥离
    交互式/任务编排工具并注入 abort_workflow（见其 docstring）。

    phases 非空时逐段计时（DEBUG 诊断专用，见 app/core/perf.py）。

    Returns:
        (all_tools, load_errors)
    """
    with timed_phase(phases, "build_tools_builtin"):
        all_tools = _resolve_builtin_tools(agent, execution_context)
    with timed_phase(phases, "build_tools_skills"):
        all_tools.extend(
            await _resolve_skill_tools(agent, user_id, session_id, loaded_tracker, request_id=request_id)
        )
    with timed_phase(phases, "build_tools_kb"):
        all_tools.extend(await _resolve_kb_tools(agent))
    with timed_phase(phases, "build_tools_mcp"):
        mcp_tools, mcp_errors = await _resolve_mcp_tools(agent)
    all_tools.extend(mcp_tools)
    with timed_phase(phases, "build_tools_custom"):
        custom_tools, custom_errors = await _resolve_custom_tools(agent)
    all_tools.extend(custom_tools)
    if user_id and not user_id.startswith("channel:"):
        from app.db.mongodb import get_database
        from app.engine.user_skills.tools import make_user_skill_tools

        with timed_phase(phases, "build_tools_user"):
            # 管理员无个人技能：会话内 create 直接产出官方（§7.6）——按角色装配
            is_admin = False
            user_doc = await get_database()["users"].find_one(
                {"_id": user_id}, {"role": 1}
            )
            if user_doc and (user_doc.get("role") or "") in ("admin", "developer"):
                is_admin = True
            all_tools.extend(
                make_user_skill_tools(user_id, session_id, loaded_tracker or set(), is_admin=is_admin)
            )
    errors = mcp_errors + custom_errors
    return all_tools, errors


async def resolve_harness_context(
    agent: dict,
    state: dict,
    *,
    enable_thinking: bool = False,
    workspace: Any | None = None,
    user_token: str | None = None,
    execution_context: str = "chat",
) -> dict:
    """装配 harness 执行所需的全部注入物,返回 dict 供 graph + config 使用。

    合并工具策略(统一由 resolve_all_tools 解析):
      ① 内建工具(task/workflow 工具 + bash/read/write/glob/grep + ask_clarification;
         execution_context="workflow" 时剥离交互式/编排工具,注入 abort_workflow)
      ② Skill(load_skill)— harness SkillManager
      ③ 知识库(kb_glob/grep/read + kb_search)
      ④ MCP(逐连接,走 get_mcp_tools_cached 缓存)
      ⑤ 自定义工具(openapi/code/prebuilt,含 user_args 解密)

    Args:
        workspace: 可选,workflow agent 节点传入已创建的 task workspace。
        user_token: 可选,外部终端用户 token(回调验证模式)。设置后 MCP
            工具调用会把该 token 放进 Authorization header 透传给 MCP
            server;为 None 时(兼容模式/平台用户)用 MCP connection 静态凭证。
        execution_context: "chat"(默认,聊天/preview)或 "workflow"(工作流
            agent 节点,无人值守语义,影响工具集与 prompt 声明)。

    Returns:
        dict 含 keys: agent_doc, llm, tools, sb_token, ws_token,
              user_token, middlewares, context_window
              middlewares, context_window
    """
    agent_id = agent.get("_id", "agent")
    session_id = state.get("session_id", "")
    logger.debug(
        "harness_context_resolve_start",
        agent_id=agent_id,
        agent_name=agent.get("name", ""),
        session_id=session_id,
        enable_thinking=enable_thinking,
        has_workspace=workspace is not None,
    )
    load_errors: list[dict] = []
    # DEBUG 诊断：分段计时（关闭时为 None，timed_phase 直通零开销）
    timing_phases = phases_if_debug()

    from pathlib import Path

    from agent_flow_harness import (
        DockerSandbox,
        DockerSandboxConfig,
        SandboxContext,
        UsageMiddleware,
        set_sandbox_context,
    )

    from app.core.config import settings
    from app.engine.agent.builtin_tools import set_workspace_context
    from app.engine.agent.context import get_context_window_async
    from app.engine.llm_factory import get_llm_client
    from app.models.compat import resolve_default_model

    # 1. 解析 LLM + context_window
    with timed_phase(timing_phases, "build_llm_client"):
        llm = await get_llm_client(agent, enable_thinking=enable_thinking)
        model_ref = resolve_default_model(agent)
        context_window = await get_context_window_async(model_ref)

    # 2. 工具解析:统一调用 resolve_all_tools(与 preview 共用,消除双路径不一致)。
    #    平台用户额外装配个人技能双根 + skill_manage/memory 工具(§2)。
    #    工作流上下文(execution_context="workflow")在此剥离交互式/编排工具。
    loaded_tracker: set[str] = set()
    all_tools, load_errors = await resolve_all_tools(
        agent,
        user_id=state.get("user_id", ""),
        session_id=state.get("session_id", ""),
        loaded_tracker=loaded_tracker,
        request_id=state.get("request_id", ""),
        execution_context=execution_context,
        phases=timing_phases,
    )

    # 3. 构造 agent_doc(含 token budget guard 防止会话被滥用)
    agent_max_tokens = int(agent.get("max_tokens") or 0)
    session_token_limit = agent_max_tokens if agent_max_tokens > 0 else settings.DEFAULT_SESSION_MAX_TOKENS
    agent_doc = {
        "_id": agent.get("_id", "agent"),
        "name": agent.get("name", "agent"),
        "guards": [
            {"name": "token_budget", "config": {"max_total_tokens": session_token_limit}},
        ],
    }

    # 6. sandbox:用 backend 配置构造 harness DockerSandbox
    sandbox_config = DockerSandboxConfig(
        image=settings.SANDBOX_IMAGE,
        enabled=settings.SANDBOX_ENABLED,
        allow_local_fallback=settings.SANDBOX_FALLBACK == "local",
        mem_limit=settings.SANDBOX_MEM_LIMIT,
        cpu_quota=settings.SANDBOX_CPU_QUOTA,
        timeout=settings.SANDBOX_TIMEOUT,
        max_output_bytes=settings.SANDBOX_MAX_OUTPUT_BYTES,
        network_mode=settings.SANDBOX_NETWORK_MODE,
        container_workspace_dir=settings.SANDBOX_CONTAINER_WORKSPACE_DIR,
        container_skills_dir=settings.SANDBOX_CONTAINER_SKILLS_DIR,
    )

    # 7. workspace
    with timed_phase(timing_phases, "build_workspace"):
        if workspace is not None:
            ws_token = set_workspace_context(workspace)
            work_dir = workspace.tmp_dir
            work_dir.mkdir(parents=True, exist_ok=True)
            sandbox_mounts = {
                "tmp": workspace.tmp_dir,
                "input": workspace.input_dir,
                "output": workspace.output_dir,
            }
            sandbox_id = f"{state.get('session_id') or workspace.root.name}"
        else:
            session_id = state.get("session_id", "")
            user_id = state.get("user_id", "")
            ws_token = None
            if session_id and user_id:
                try:
                    from app.engine.tool.workspace import WorkspaceManager
                    ws = WorkspaceManager.get_workspace(user_id, session_id)
                    ws.input_dir.mkdir(parents=True, exist_ok=True)
                    ws.output_dir.mkdir(parents=True, exist_ok=True)
                    ws.tmp_dir.mkdir(parents=True, exist_ok=True)
                    ws_token = set_workspace_context(ws)
                except Exception:
                    pass
            work_dir = Path(settings.WORKSPACES_CONTAINER_DIR) / user_id / session_id / "tmp"
            work_dir.mkdir(parents=True, exist_ok=True)
            sandbox_mounts = {
                "tmp": work_dir,
                "input": Path(settings.WORKSPACES_CONTAINER_DIR) / user_id / session_id / "input",
                "output": Path(settings.WORKSPACES_CONTAINER_DIR) / user_id / session_id / "output",
            }
            sandbox_id = f"{session_id}"

    sandbox = DockerSandbox(
        sandbox_id=sandbox_id,
        work_dir=work_dir,
        mounts=sandbox_mounts,
        config=sandbox_config,
        timeout=settings.SANDBOX_TIMEOUT,
    )

    # 8. 注入 sandbox context
    sb_token = set_sandbox_context(SandboxContext(sandbox=sandbox))

    # 8.6 注入 run_code 工具桥接 context(代码内 tools.call 的工具表)。
    # 仅当 run_code 实际注入本次执行时才设置(工具表按 Agent 绑定动态构建,
    # 排除 HITL/子代理/文件 shell 类工具,见 _RUN_CODE_EXCLUDED_TOOLS)。
    tb_token = None
    if settings.RUN_CODE_ENABLED and "run_code" in set(agent.get("builtin_config") or []):
        from agent_flow_harness import ToolBridgeContext, set_tool_bridge_context

        tb_token = set_tool_bridge_context(ToolBridgeContext(
            tools_map={
                t.name: t
                for t in all_tools
                if getattr(t, "name", "") not in _RUN_CODE_EXCLUDED_TOOLS
            },
            restricted=settings.RUN_CODE_RESTRICTED,
            call_timeout=settings.RUN_CODE_CALL_TIMEOUT,
            overall_timeout=settings.RUN_CODE_TIMEOUT,
            max_output_bytes=settings.RUN_CODE_MAX_OUTPUT_BYTES,
        ))

    # 8.5 注入 user_token context(供 MCP 工具透传给 MCP server)。
    # asyncio.create_task 会复制 contextvars,所以即便 stream/resume 的
    # 真正执行在后台任务里,MCP loader 的 interceptor 也能读到。
    from agent_flow_harness import set_user_token_context
    from agent_flow_harness.mcp.user_token_context import set_token_record_id_context

    ut_token = set_user_token_context(user_token)

    # 外部路径（/ext/*，user_token 非空）额外注入 token_record_id，触发 MCP
    # 凭证兑换。内部路径（studio 测试，user_token=None）不注入，interceptor
    # 自然降级用 connection 静态凭证。
    # user_id 在外部路径 = principal.user_id = mcp_token_credentials._id。
    tri_token = set_token_record_id_context(
        state.get("user_id") if user_token else None
    )

    logger.debug(
        "harness_context_resolved",
        agent_id=agent_id,
        model=model_ref,
        context_window=context_window,
        tool_count=len(all_tools),
        tool_names=[getattr(t, "name", str(t)) for t in all_tools],
        sandbox_enabled=settings.SANDBOX_ENABLED,
        sandbox_id=sandbox_id,
        session_token_limit=session_token_limit,
    )

    return {
        "agent_doc": agent_doc,
        "llm": llm,
        "tools": all_tools,
        "load_errors": load_errors,
        "sb_token": sb_token,
        "ws_token": ws_token,
        "ut_token": ut_token,
        "tri_token": tri_token,
        "tb_token": tb_token,
        "middlewares": [UsageMiddleware()],
        "context_window": context_window,
        # DEBUG 分段计时（关闭时为 None；execution.py 汇总进 agent_phase_timing 日志）
        "_timing_phases": timing_phases,
        # 压缩配置(全局可配)。
        "protected_turns": settings.COMPRESSION_PROTECTED_TURNS,
        "compression_threshold": settings.COMPRESSION_THRESHOLD,
        "hard_limit_ratio": settings.COMPRESSION_HARD_LIMIT_RATIO,
        # 工具输出被压缩后,在被截断的结果末尾追加"可回溯"提示。这是应用层
        # 决策——具体用什么工具回溯(recall_tool_result)由 app 层定义,harness
        # 不硬编码工具名,只透传这个 formatter。
        "tool_output_reference_formatter": _make_tool_output_reference_formatter(),
        # 图片降级同理:旧轮 image 块被移出上下文时,用此 formatter 生成
        # 可回取占位(file_id 供 view_image 使用)。harness 只做机械替换。
        "image_reference_formatter": _make_image_reference_formatter(),
        # 降级时保留最近 N 张真图(0 = 全部降级,最省 token)。
        "image_keep_recent": settings.IMAGE_KEEP_RECENT_N,
    }


def _make_tool_output_reference_formatter():
    """构造工具输出引用标记生成器(供 harness compress_tool_outputs 使用)。

    被压缩的工具结果末尾会追加提示,告知 LLM 用 recall_tool_result 取回
    完整原文 —— 这样压缩是"可逆"的,信息没真正丢失。
    """

    def _format(tool_call_id: str) -> str:
        return (
            f"\n\n[此结果已被压缩,完整原文已存档,"
            f'可用 recall_tool_result(tool_call_id="{tool_call_id}") 查看]'
        )

    return _format


def _make_image_reference_formatter():
    """构造图片降级占位生成器(供 harness 图片降级使用)。

    与 tool_output formatter 同构:压缩是"可逆"的——图片字节仍在 FileRef
    存储,占位文案携带 file_id,LLM 可用 view_image 随时拉回当轮查看。
    """

    def _format(file_id: str, name: str) -> str:
        return f'[图片 {name}(file_id="{file_id}")已移出上下文,可用 view_image(file_id="{file_id}") 查看]'

    return _format


def release_harness_context(hctx: dict) -> None:
    """释放 resolve_harness_context 持有的 contextvar token(在 finally 调用)。"""
    from agent_flow_harness import (
        reset_sandbox_context,
        reset_tool_bridge_context,
        reset_user_token_context,
    )
    from agent_flow_harness.mcp.user_token_context import reset_token_record_id_context

    from app.engine.agent.builtin_tools import reset_workspace_context

    reset_sandbox_context(hctx["sb_token"])
    if hctx.get("ws_token") is not None:
        reset_workspace_context(hctx["ws_token"])
    reset_user_token_context(hctx["ut_token"])
    if hctx.get("tri_token") is not None:
        reset_token_record_id_context(hctx["tri_token"])
    if hctx.get("tb_token") is not None:
        reset_tool_bridge_context(hctx["tb_token"])


async def _maybe_migrate_legacy(graph, config, legacy_records: list[dict] | None) -> None:
    """灌入老session历史到 thread(仅当 MIGRATE_LEGACY_SESSIONS 且 thread 空)。"""
    if not legacy_records:
        return
    from app.core.config import settings

    if not settings.MIGRATE_LEGACY_SESSIONS:
        return
    state = await graph.aget_state(config)
    if state.values:
        return
    from app.engine.harness_integration.history import rebuild_messages_from_records

    rebuilt = await rebuild_messages_from_records(legacy_records)
    if rebuilt:
        await graph.aupdate_state(config, {"messages": rebuilt})
