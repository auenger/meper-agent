"""资源导出器 — 依赖闭包收集（引用图 BFS）+ 各资源 JSON/文件序列化。

依赖收集依据 docs/resource-transfer-plan.md §2.2 引用图：

- Agent → skill_ids / legacy tool_ids(markdown 工具) / custom_tools[].tool_id
  (自定义工具) / mcp_connection_ids / knowledge_base_ids(仅 tree) /
  workflow_ids / default_model(``model_`` 前缀时)
- Workflow 节点 → agent 节点 agent_id / tool 节点 tool_id（普通工具进包，
  MCP 工具不进包、改为携带其 connection 并记录 _mcp_tool_refs）/
  subflow workflow_id / kb_search kb_ids(仅 tree)
- MCP 连接 → category_id

Agent↔Workflow 双向引用靠 visited 集合防环。敏感字段一律剔除并在资源
JSON 内留 ``_redacted`` 标记（导入端据此生成 warning）。
"""
from __future__ import annotations

from loguru import logger

from app.core.errors import NotFoundError, ValidationError
from app.db.mongodb import get_database
from app.engine.kb.tree import fs as kb_fs
from app.engine.tool import skill_fs
from app.schemas.transfer import ResourceRef
from app.services.transfer import package as pkg

# user_args / default_params 中按 key 名匹配的敏感词（与 mcp_connection_service
# 的 _SENSITIVE_AUTH_KEYS 同族，取并集做子串匹配）。
_SENSITIVE_SUBSTRINGS = ("token", "key", "secret", "password", "bearer")

# kind → Mongo 集合名。skill / tool 都在 tools 集合，按 source 字段分派。
_KIND_TO_COLLECTION: dict[str, str] = {
    "agent": "agents",
    "workflow": "workflows",
    "mcp_connection": "mcp_connections",
    "mcp_category": "mcp_categories",
    "model": "models",
    "knowledge_base": "knowledge_bases",
}

_ALL_KINDS = set(_KIND_TO_COLLECTION) | {"skill", "tool"}


def _safe_arcname(name: str) -> str:
    """校验名字可安全用作 zip 内路径段（skill 目录名）。"""
    if not name or "/" in name or "\\" in name or ".." in name or "\x00" in name:
        raise ValidationError(
            code="TRANSFER_UNSAFE_NAME",
            message=f"资源名称不能用作包内路径：{name!r}",
        )
    return name


def _sanitize_dict(d: dict) -> tuple[dict, list[str]]:
    """剔除 dict 中 key 命中敏感词的值。返回 (清理后 dict, 被剔除的 key 列表)。"""
    removed: list[str] = []
    out: dict = {}
    for k, v in d.items():
        if any(s in k.lower() for s in _SENSITIVE_SUBSTRINGS):
            removed.append(k)
        else:
            out[k] = v
    return out, removed


def _strip_encrypted_user_args(custom_tools: list[dict]) -> tuple[list[dict], list[dict]]:
    """剔除 custom_tools[].user_args 中 ``enc:`` 前缀的加密值（明文值保留）。

    Returns:
        (清理后的绑定列表, _redacted 标记列表)
    """
    cleaned: list[dict] = []
    redacted: list[dict] = []
    for b in custom_tools or []:
        tool_id = b.get("tool_id", "")
        user_args = b.get("user_args") or {}
        clean_args: dict = {}
        for k, v in user_args.items():
            if isinstance(v, str) and v.startswith("enc:"):
                redacted.append(
                    {"field": f"custom_tools[{tool_id}].user_args.{k}", "reason": "加密参数不随包迁移，请导入后重新填写"}
                )
            else:
                clean_args[k] = v
        cleaned.append({"tool_id": tool_id, "user_args": clean_args})
    return cleaned, redacted


async def _fetch(collection: str, ids: list[str]) -> list[dict]:
    """批量按 _id 取文档。"""
    if not ids:
        return []
    cursor = get_database()[collection].find({"_id": {"$in": ids}})
    return await cursor.to_list(length=len(ids))


async def _load_top_resource(ref: ResourceRef) -> tuple[str, dict]:
    """加载一个顶层资源，归一化 kind（tools 集合按 source 分派 skill/tool）。"""
    if ref.kind in ("skill", "tool"):
        docs = await _fetch("tools", [ref.id])
        if not docs:
            raise NotFoundError(
                code="TRANSFER_RESOURCE_NOT_FOUND",
                message=f"资源不存在：{ref.kind} {ref.id}",
            )
        source = docs[0].get("source", "")
        if source == "markdown":
            return "skill", docs[0]
        if source in ("openapi", "code", "prebuilt"):
            return "tool", docs[0]
        raise ValidationError(
            code="TRANSFER_KIND_UNSUPPORTED",
            message=f"ID {ref.id} 是 {source} 工具（MCP 工具由连接 discover 生成，不单独导出）",
        )

    collection = _KIND_TO_COLLECTION.get(ref.kind)
    if collection is None:
        raise ValidationError(
            code="TRANSFER_KIND_UNSUPPORTED",
            message=f"不支持的资源类型：{ref.kind!r}",
        )
    docs = await _fetch(collection, [ref.id])
    if not docs:
        raise NotFoundError(
            code="TRANSFER_RESOURCE_NOT_FOUND",
            message=f"资源不存在：{ref.kind} {ref.id}",
        )
    doc = docs[0]
    if ref.kind == "knowledge_base" and doc.get("type", "tree") != "tree":
        raise ValidationError(
            code="TRANSFER_KB_TYPE_UNSUPPORTED",
            message=f"知识库 '{doc.get('name')}' 是 vector 型，不支持导出（见设计文档非目标）",
        )
    return ref.kind, doc


async def _resolve_tools(
    ids: set[str],
    closure: dict[str, dict[str, dict]],
    mcp_tools: dict[str, dict],
    _enqueue,
) -> None:
    """批量解析 tools 集合引用，按 source 分派：markdown→skill，
    mcp→记入 mcp_tools 并携带其 connection，其余→tool。"""
    for td in await _fetch("tools", sorted(ids)):
        source = td.get("source", "")
        if source == "mcp":
            mcp_tools[td["_id"]] = td
            conn_id = td.get("mcp_connection_id", "")
            if conn_id:
                conns = await _fetch("mcp_connections", [conn_id])
                for c in conns:
                    _enqueue("mcp_connection", c)
        elif source == "markdown":
            _enqueue("skill", td)
        else:
            _enqueue("tool", td)


async def collect_closure(
    top: list[tuple[str, dict]],
    *,
    include_dependencies: bool = True,
) -> tuple[dict[str, dict[str, dict]], dict[str, dict]]:
    """BFS 收集依赖闭包。

    Args:
        top: 顶层资源列表 [(kind, doc)]。
        include_dependencies: False 时只收集顶层资源本身，但 workflow 顶层
            的 tool 节点引用仍会解析（生成 _mcp_tool_refs 必需）。

    Returns:
        (closure, mcp_tools) —
        closure: ``{kind: {id: doc}}``（含顶层资源自身）；
        mcp_tools: MCP 工具文档 ``{tool_id: doc}``（本身不进包）。
    """
    closure: dict[str, dict[str, dict]] = {k: {} for k in _ALL_KINDS}
    mcp_tools: dict[str, dict] = {}

    queue: list[tuple[str, str]] = []
    visited: set[tuple[str, str]] = set()

    def _enqueue(kind: str, doc: dict) -> None:
        rid = doc["_id"]
        if (kind, rid) in visited:
            return
        visited.add((kind, rid))
        closure[kind][rid] = doc
        queue.append((kind, rid))

    for kind, doc in top:
        _enqueue(kind, doc)

    while queue:
        # 当前层所有节点的依赖合并成批量查询
        pending: dict[str, set[str]] = {}
        for _ in range(len(queue)):
            kind, rid = queue.pop(0)
            doc = closure[kind][rid]
            for dep_kind, dep_id in _direct_deps(doc, kind, expand_all=include_dependencies):
                if (dep_kind, dep_id) not in visited and dep_id:
                    pending.setdefault(dep_kind, set()).add(dep_id)

        for dep_kind, ids in pending.items():
            if dep_kind in ("skill", "tool", "tool_ref"):
                # 都指向 tools 集合，按 source 分派（markdown→skill / mcp→记
                # mcp_tools+携带 connection / 其余→tool）
                await _resolve_tools(ids, closure, mcp_tools, _enqueue)
            else:
                docs = await _fetch(_KIND_TO_COLLECTION[dep_kind], sorted(ids))
                for d in docs:
                    if dep_kind == "knowledge_base" and d.get("type", "tree") != "tree":
                        # vector KB 不进包（设计非目标）
                        logger.info(
                            "transfer_export_skip_vector_kb", kb_id=d["_id"], name=d.get("name")
                        )
                        continue
                    _enqueue(dep_kind, d)

    return closure, mcp_tools


def _direct_deps(
    doc: dict, kind: str, *, expand_all: bool = True
) -> list[tuple[str, str]]:
    """列出一个资源的直接依赖 [(dep_kind, dep_id)]。

    dep_kind 取值：agent / workflow / knowledge_base / mcp_connection /
    mcp_category / model / skill / tool / tool_ref（workflow tool 节点专用，
    由 _resolve_tools 按 source 分派）。
    """
    deps: list[tuple[str, str]] = []
    if kind == "agent" and expand_all:
        skill_ids = list(doc.get("skill_ids") or [])
        # legacy tool_ids 兼容（老 Agent 的绑定同为 markdown 工具 ID）
        skill_ids += [t for t in doc.get("tool_ids") or [] if t not in skill_ids]
        deps += [("skill", sid) for sid in skill_ids]
        deps += [("tool", b.get("tool_id")) for b in doc.get("custom_tools") or [] if b.get("tool_id")]
        deps += [("mcp_connection", cid) for cid in doc.get("mcp_connection_ids") or []]
        deps += [("knowledge_base", kid) for kid in doc.get("knowledge_base_ids") or []]
        deps += [("workflow", wid) for wid in doc.get("workflow_ids") or []]
        dm = doc.get("default_model") or ""
        if dm.startswith("model_"):
            deps.append(("model", dm))
    elif kind == "workflow":
        for node in doc.get("nodes") or []:
            ntype = node.get("type", "")
            config = node.get("config") or {}
            if not expand_all:
                # 不带依赖导出时仅解析 tool 节点（_mcp_tool_refs 需要）
                if ntype == "tool" and config.get("tool_id"):
                    deps.append(("tool_ref", config["tool_id"]))
                continue
            if ntype == "agent" and config.get("agent_id"):
                deps.append(("agent", config["agent_id"]))
            elif ntype == "tool" and config.get("tool_id"):
                deps.append(("tool_ref", config["tool_id"]))
            elif ntype == "subflow" and config.get("workflow_id"):
                deps.append(("workflow", config["workflow_id"]))
            elif ntype == "kb_search":
                deps += [("knowledge_base", kid) for kid in config.get("kb_ids") or []]
    elif kind == "mcp_connection" and expand_all:
        cid = doc.get("category_id") or ""
        if cid:
            deps.append(("mcp_category", cid))
    return deps


# ── 资源 payload 序列化 ──────────────────────────────────────────────


def _agent_payload(doc: dict) -> dict:
    from app.models.compat import resolve_default_model, resolve_max_retry

    custom_tools, redacted = _strip_encrypted_user_args(doc.get("custom_tools") or [])
    payload = {
        "kind": "agent",
        "original_id": doc["_id"],
        "name": doc["name"],
        "description": doc.get("description", ""),
        "welcome_message": doc.get("welcome_message", ""),
        "recommended_items": doc.get("recommended_items") or [],
        "prompt_slots": doc.get("prompt_slots") or {},
        "skill_ids": doc.get("skill_ids") or [],
        "mcp_connection_ids": doc.get("mcp_connection_ids") or [],
        "custom_tools": custom_tools,
        "builtin_config": doc.get("builtin_config") or [],
        "workflow_ids": doc.get("workflow_ids") or [],
        "knowledge_base_ids": doc.get("knowledge_base_ids") or [],
        "default_model": resolve_default_model(doc),
        "voice_enabled": bool(doc.get("voice_enabled", False)),
        "user_skills_enabled": bool(doc.get("user_skills_enabled", True)),
        "max_retry": resolve_max_retry(doc),
        "max_tokens": doc.get("max_tokens", 0),
    }
    if redacted:
        payload["_redacted"] = redacted
    return payload


def _workflow_payload(doc: dict, mcp_tools: dict[str, dict]) -> dict:
    payload = {
        "kind": "workflow",
        "original_id": doc["_id"],
        "name": doc["name"],
        "description": doc.get("description", ""),
        "tags": doc.get("tags") or [],
        "nodes": doc.get("nodes") or [],
        "edges": doc.get("edges") or [],
    }
    # MCP 工具引用：记录 (node_id, 原工具名, 原 conn_id)，导入端 discover 后重绑
    mcp_refs: list[dict] = []
    for node in doc.get("nodes") or []:
        if node.get("type") != "tool":
            continue
        tid = (node.get("config") or {}).get("tool_id", "")
        td = mcp_tools.get(tid)
        if td:
            mcp_refs.append(
                {
                    "node_id": node.get("node_id", ""),
                    "original_tool_id": tid,
                    "tool_name": td.get("name", ""),
                    "mcp_connection_original_id": td.get("mcp_connection_id", ""),
                }
            )
    if mcp_refs:
        payload["_mcp_tool_refs"] = mcp_refs
    return payload


def _mcp_payload(doc: dict) -> dict:
    default_params, removed = _sanitize_dict(doc.get("default_params") or {})
    redacted = [
        {"field": "auth_config", "reason": "认证凭证不随包迁移，请导入后重新填写"}
    ]
    redacted += [
        {"field": f"default_params.{k}", "reason": "敏感参数不随包迁移"} for k in removed
    ]
    return {
        "kind": "mcp_connection",
        "original_id": doc["_id"],
        "name": doc["name"],
        "description": doc.get("description", ""),
        "category_id": doc.get("category_id", ""),
        "url": doc["url"],
        "protocol": doc.get("protocol", "streamable-http"),
        "auth_type": doc.get("auth_type", "none"),
        "timeout": doc.get("timeout", 30),
        "default_params": default_params,
        "_redacted": redacted,
    }


def _category_payload(doc: dict) -> dict:
    return {
        "kind": "mcp_category",
        "original_id": doc["_id"],
        "name": doc["name"],
        "description": doc.get("description", ""),
        "sort": doc.get("sort", 0),
    }


def _model_payload(doc: dict) -> dict:
    return {
        "kind": "model",
        "original_id": doc["_id"],
        "model_id": doc.get("model_id", ""),
        "name": doc.get("name", ""),
        "base_url": doc.get("base_url", ""),
        "compatibility_type": doc.get("compatibility_type", "openai"),
        "auth_type": doc.get("auth_type", "bearer"),
        "auth_header_format": doc.get("auth_header_format", "Bearer {key}"),
        "default_params": doc.get("default_params") or {},
        "provider_tag": doc.get("provider_tag", ""),
        "task_type": doc.get("task_type", "chat"),
        "_redacted": [
            {"field": "api_key", "reason": "模型密钥不随包迁移，请导入后补填"}
        ],
    }


def _tool_payload(doc: dict) -> dict:
    return {
        "kind": "tool",
        "original_id": doc["_id"],
        "name": doc["name"],
        "description": doc.get("description", ""),
        "source": doc.get("source", ""),
        "user_args_schema": doc.get("user_args_schema") or {},
        "llm_args_schema": doc.get("llm_args_schema") or {},
        "endpoint": doc.get("endpoint") or {},
        "code": doc.get("code", ""),
        "prebuilt_name": doc.get("prebuilt_name", ""),
    }


def _kb_payload(doc: dict) -> dict:
    return {
        "kind": "knowledge_base",
        "original_id": doc["_id"],
        "name": doc["name"],
        "description": doc.get("description", ""),
        "type": "tree",
    }


# ── 主入口 ──────────────────────────────────────────────────────────


async def export_package(
    resources: list[ResourceRef],
    *,
    include_dependencies: bool = True,
    exported_by: str = "",
) -> bytes:
    """导出资源（含依赖闭包）为 afpkg zip 字节流。

    Raises:
        NotFoundError: 顶层资源不存在。
        ValidationError: kind 不支持 / vector KB / 名字不可用作包路径。
    """
    top: list[tuple[str, dict]] = []
    for ref in resources:
        top.append(await _load_top_resource(ref))

    closure, mcp_tools = await collect_closure(
        top, include_dependencies=include_dependencies
    )

    entries: dict[str, bytes] = {}
    manifest_resources: list[dict] = []

    def _put_json(kind: str, doc: dict, payload: dict, subdir: str) -> str:
        path = f"{subdir}/{doc['_id']}.json"
        pkg.write_json_entry(entries, path, payload)
        manifest_resources.append(
            {"kind": kind, "path": path, "original_id": doc["_id"], "name": doc["name"]}
        )
        return path

    # 1. 无依赖类：分组 / 模型 / 自定义工具
    for _cid, doc in closure["mcp_category"].items():
        _put_json("mcp_category", doc, _category_payload(doc), "mcp_categories")
    for _mid, doc in closure["model"].items():
        _put_json("model", doc, _model_payload(doc), "models")
    for _tid, doc in closure["tool"].items():
        _put_json("tool", doc, _tool_payload(doc), "tools")

    # 2. MCP 连接
    for _cid, doc in closure["mcp_connection"].items():
        _put_json("mcp_connection", doc, _mcp_payload(doc), "mcp")

    # 3. Skill（元数据 + 磁盘整目录）
    for sid, doc in closure["skill"].items():
        name = doc["name"]
        arc_root = _safe_arcname(name)
        files = skill_fs.list_skill_files(name)
        wrote = 0
        for f in files:
            content = skill_fs.read_skill_file(name, f["path"])
            if content is None:
                logger.warning(
                    "transfer_export_skill_file_skipped",
                    skill=name, rel_path=f["path"],
                )
                continue
            entries[f"skills/{arc_root}/{f['path']}"] = content.encode("utf-8")
            wrote += 1
        if wrote == 0:
            raise ValidationError(
                code="TRANSFER_SKILL_FILES_MISSING",
                message=f"Skill '{name}' 的磁盘文件缺失（{skill_fs.get_skill_base_path(name)}），无法导出",
            )
        manifest_resources.append({"kind": "skill", "name": name, "original_id": sid})

    # 4. tree（Wiki）KB（元数据 + .md 文件树）
    for kid, doc in closure["knowledge_base"].items():
        kb_json_path = f"kbs/{kid}/kb.json"
        pkg.write_json_entry(entries, kb_json_path, _kb_payload(doc))
        manifest_resources.append(
            {
                "kind": "knowledge_base",
                "path": kb_json_path,
                "original_id": kid,
                "name": doc["name"],
                "type": "tree",
            }
        )
        for f in kb_fs.list_kb_files(kid):
            content = kb_fs.read_kb_file(kid, f["path"])
            if content is None:
                logger.warning(
                    "transfer_export_kb_file_skipped", kb_id=kid, rel_path=f["path"]
                )
                continue
            entries[f"kbs/{kid}/files/{f['path']}"] = content.encode("utf-8")

    # 5. Workflow
    for _wid, doc in closure["workflow"].items():
        _put_json("workflow", doc, _workflow_payload(doc, mcp_tools), "workflows")

    # 6. Agent
    for _aid, doc in closure["agent"].items():
        _put_json("agent", doc, _agent_payload(doc), "agents")

    manifest = pkg.build_manifest(manifest_resources, exported_by=exported_by)
    pkg.write_json_entry(entries, pkg.MANIFEST_NAME, manifest)

    logger.info(
        "transfer_package_exported",
        resources=len(manifest_resources),
        top=len(top),
        include_dependencies=include_dependencies,
    )
    return pkg.build_zip(entries)
