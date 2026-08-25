"""afpkg 导入器 — 拓扑创建、ID 重映射、冲突/复用判定、落库落盘。

处理顺序（依赖拓扑，docs/resource-transfer-plan.md §5.1）::

    1. mcp_categories, models      （无依赖）
    2. skills, tools, mcp, kbs     （依赖 1）
    3. workflows                   （依赖 2；subflow 互引按包内 DAG 排序）
    4. agents                      （依赖全部）
    5. MCP discover + 工具引用重绑

dry_run 与真导入同构：判定（复用/改名/跳过）照常执行（只读查询），
只是跳过所有写操作；``id_map`` 在 dry_run 下以旧 ID 自映射占位，保证
后续引用重写不产生伪 warning。
"""
from __future__ import annotations

import tempfile
from dataclasses import dataclass, field
from pathlib import Path

import yaml
from loguru import logger

from app.core.errors import ValidationError
from app.db.mongodb import get_database
from app.engine.tool.skill_parser import (
    FRONTMATTER_PATTERN,
    ParsedSkillDirectory,
    SkillFileEntry,
    SkillParseError,
    parse_skill_markdown,
)
from app.schemas.transfer import ImportReport
from app.services.transfer import package as pkg
from app.services.transfer import rewiring
from app.services.transfer.report import ReportCollector


@dataclass
class _Ctx:
    """一次导入的共享状态。"""

    report: ReportCollector
    id_map: dict[str, str] = field(default_factory=dict)
    dry_run: bool = False
    reuse_existing: bool = True
    conflict: str = "rename"  # rename | skip
    imported_by: str = ""
    # 新建的 MCP 连接 [{original_id, new_id, name}]，discover 阶段用
    created_mcp: list[dict] = field(default_factory=list)
    # 待重绑 MCP 工具引用的 workflow [{workflow_id, workflow_name, refs}]
    workflow_plans: list[dict] = field(default_factory=list)
    # 待补写 agent 节点引用的 workflow [{workflow_id, workflow_name, refs, dry_run}]
    # （agents 阶段晚于 workflows，包内 agent 引用先挂起、最后统一补写）
    wf_agent_ref_plans: list[dict] = field(default_factory=list)


async def import_package(
    data: bytes,
    *,
    dry_run: bool = False,
    reuse_existing: bool = True,
    conflict: str = "rename",
    auto_discover: bool = True,
    imported_by: str = "",
) -> ImportReport:
    """导入一个 afpkg 包，返回导入报告。

    Raises:
        ValidationError: 包损坏/超限/格式不 supported（来自 package 层）。
    """
    report = ReportCollector(dry_run=dry_run)
    ctx = _Ctx(
        report=report,
        dry_run=dry_run,
        reuse_existing=reuse_existing,
        conflict=conflict,
        imported_by=imported_by,
    )
    with tempfile.TemporaryDirectory(prefix="afpkg-") as tmp:
        extract_dir = Path(tmp)
        pkg.extract_zip(data, extract_dir)
        manifest = pkg.read_manifest(extract_dir)
        await _run(manifest, extract_dir, ctx, auto_discover=auto_discover)
    logger.info(
        "transfer_package_imported",
        dry_run=dry_run,
        created=len(report.created),
        reused=len(report.reused),
        skipped=len(report.skipped),
        warnings=len(report.warnings),
        errors=len(report.errors),
    )
    return report.to_report()


# ── 公共辅助 ────────────────────────────────────────────────────────


def _payload(extract_dir: Path, res: dict) -> dict:
    return pkg.read_json_file(extract_dir / res["path"])


def _announce_redacted(payload: dict, kind: str, ctx: _Ctx) -> None:
    """把导出端留下的 _redacted 标记转成导入 warning。"""
    for r in payload.get("_redacted") or []:
        ctx.report.warning(
            kind=kind,
            name=payload.get("name", ""),
            field=r.get("field", ""),
            message=r.get("reason", "敏感字段不随包迁移"),
        )


def _make_on_miss(ctx: _Ctx, kind: str, name: str):
    def on_miss(field: str, old: str, where: str) -> None:
        ctx.report.warning(
            kind=kind,
            name=name or where,
            field=field,
            message=f"依赖的资源 {old}（{where} 的 {field}）不在包内且本实例未找到，引用已按规则降级处理",
        )

    return on_miss


async def _find_by_name(collection: str, name: str, extra: dict | None = None) -> dict | None:
    query: dict = {"name": name}
    if extra:
        query.update(extra)
    return await get_database()[collection].find_one(query)


async def _unique_name(collection: str, base: str) -> tuple[str, str]:
    """撞名时生成 ``{base}-imported``、``{base}-imported-2``…（查库确认）。

    Returns:
        (最终名, renamed_from) — 未改名时 renamed_from 为空串。
    """
    col = get_database()[collection]
    name = base
    renamed = ""
    for i in range(1, 51):
        if await col.find_one({"name": name}) is None:
            return name, renamed
        renamed = base
        name = f"{base}-imported" if i == 1 else f"{base}-imported-{i}"
    raise ValidationError(
        code="TRANSFER_NAME_EXHAUSTED",
        message=f"无法为 '{base}' 生成唯一名称（已尝试 50 个后缀）",
    )


def _map_created(ctx: _Ctx, *, kind: str, payload: dict, new_id: str, renamed_from: str) -> None:
    """登记一个已创建资源（dry_run 时旧 ID 自映射占位）。"""
    old = payload.get("original_id", "")
    ctx.id_map[old] = old if ctx.dry_run else new_id
    ctx.report.add_created(
        kind=kind,
        name=payload.get("name", ""),
        original_id=old,
        new_id="" if ctx.dry_run else new_id,
        renamed_from=renamed_from,
    )


# ── 各阶段 ──────────────────────────────────────────────────────────


async def _run(manifest: dict, extract_dir: Path, ctx: _Ctx, *, auto_discover: bool) -> None:
    by_kind: dict[str, list[dict]] = {}
    for res in manifest["resources"]:
        by_kind.setdefault(res["kind"], []).append(res)

    await _phase_categories(by_kind.get("mcp_category", []), extract_dir, ctx)
    await _phase_models(by_kind.get("model", []), extract_dir, ctx)
    await _phase_skills(by_kind.get("skill", []), extract_dir, ctx)
    await _phase_tools(by_kind.get("tool", []), extract_dir, ctx)
    await _phase_mcp(by_kind.get("mcp_connection", []), extract_dir, ctx)
    await _phase_kbs(by_kind.get("knowledge_base", []), extract_dir, ctx)
    await _phase_workflows(by_kind.get("workflow", []), extract_dir, ctx)
    await _phase_agents(by_kind.get("agent", []), extract_dir, ctx)
    await _phase_rewrite_wf_agent_refs(ctx)
    await _phase_discover_rebind(ctx, auto_discover=auto_discover)


async def _phase_categories(entries: list[dict], extract_dir: Path, ctx: _Ctx) -> None:
    from app.services.mcp_category_service import McpCategoryService

    for res in entries:
        try:
            payload = _payload(extract_dir, res)
            old = payload.get("original_id", "")
            existing = await _find_by_name("mcp_categories", payload["name"])
            if existing and ctx.reuse_existing:
                ctx.id_map[old] = existing["_id"]
                ctx.report.add_reused(
                    kind="mcp_category", name=payload["name"], original_id=old,
                    existing_id=existing["_id"],
                )
                continue
            renamed_from = ""
            if existing:  # 撞名且不复用
                if ctx.conflict == "skip":
                    ctx.report.add_skipped(kind="mcp_category", name=payload["name"], original_id=old)
                    continue
                payload["name"], renamed_from = await _unique_name("mcp_categories", payload["name"])
            if ctx.dry_run:
                _map_created(ctx, kind="mcp_category", payload=payload, new_id="", renamed_from=renamed_from)
                continue
            doc = await McpCategoryService.create_category(
                {
                    "name": payload["name"],
                    "description": payload.get("description", ""),
                    "sort": payload.get("sort", 0),
                }
            )
            _map_created(ctx, kind="mcp_category", payload=payload, new_id=doc["_id"], renamed_from=renamed_from)
        except Exception as exc:
            ctx.report.error(kind="mcp_category", name=res.get("name", ""), message=str(exc))


async def _phase_models(entries: list[dict], extract_dir: Path, ctx: _Ctx) -> None:
    from app.services.model_service import ModelService

    for res in entries:
        try:
            payload = _payload(extract_dir, res)
            _announce_redacted(payload, "model", ctx)
            old = payload.get("original_id", "")
            model_id = payload.get("model_id", "")
            existing = await get_database()["models"].find_one({"model_id": model_id}) if model_id else None
            if existing and ctx.reuse_existing:
                ctx.id_map[old] = existing["_id"]
                ctx.report.add_reused(
                    kind="model", name=payload.get("name", ""), original_id=old,
                    existing_id=existing["_id"],
                )
                continue
            if existing:
                # model_id 是上游技术标识，不可乱改名 —— 直接跳过
                ctx.report.add_skipped(kind="model", name=payload.get("name", ""), original_id=old)
                ctx.report.warning(
                    kind="model", name=payload.get("name", ""),
                    field="model_id",
                    message=f"模型标识 '{model_id}' 已存在；技术标识不支持改名，已跳过（可开启「复用已有资源」）",
                )
                continue
            if ctx.dry_run:
                _map_created(ctx, kind="model", payload=payload, new_id="", renamed_from="")
                continue
            doc = await ModelService.create_model(
                model_id=model_id,
                name=payload.get("name", ""),
                base_url=payload.get("base_url", ""),
                api_key="",
                compatibility_type=payload.get("compatibility_type", "openai"),
                auth_type=payload.get("auth_type", "bearer"),
                auth_header_format=payload.get("auth_header_format", "Bearer {key}"),
                default_params=payload.get("default_params") or None,
                provider_tag=payload.get("provider_tag", ""),
                task_type=payload.get("task_type", "chat"),
            )
            _map_created(ctx, kind="model", payload=payload, new_id=doc["_id"], renamed_from="")
        except Exception as exc:
            ctx.report.error(kind="model", name=res.get("name", ""), message=str(exc))


def _rename_skill_frontmatter(content: str, new_name: str) -> str:
    """改写 SKILL.md frontmatter 的 name 字段（语义不变，仅重排 YAML 格式）。"""
    match = FRONTMATTER_PATTERN.match(content)
    if not match:
        raise ValidationError(
            code="TRANSFER_SKILL_INVALID",
            message="SKILL.md 缺少 YAML frontmatter",
        )
    yaml_block, body = match.groups()
    meta = yaml.safe_load(yaml_block) or {}
    meta["name"] = new_name
    new_yaml = yaml.safe_dump(meta, allow_unicode=True, sort_keys=False)
    return f"---\n{new_yaml}---\n{body}"


async def _phase_skills(entries: list[dict], extract_dir: Path, ctx: _Ctx) -> None:
    from app.services.tool_service import ToolService

    for res in entries:
        name = res.get("name", "")
        old = res.get("original_id", "")
        try:
            skill_dir = extract_dir / "skills" / name
            skill_md = skill_dir / "SKILL.md"
            if not skill_md.is_file():
                ctx.report.error(kind="skill", name=name, message="包内缺少 SKILL.md，无法导入")
                continue
            skill_md_content = skill_md.read_text(encoding="utf-8")

            # 收集目录内全部文件（跳过非 UTF-8 文件，仅记 warning）
            file_entries: list[tuple[str, str]] = [("SKILL.md", skill_md_content)]
            for p in sorted(skill_dir.rglob("*")):
                if not p.is_file() or p == skill_md:
                    continue
                rel = str(p.relative_to(skill_dir))
                try:
                    file_entries.append((rel, p.read_text(encoding="utf-8")))
                except UnicodeDecodeError:
                    ctx.report.warning(
                        kind="skill", name=name, field=rel,
                        message="非 UTF-8 文本文件，已跳过",
                    )

            existing = await _find_by_name("tools", name)
            if existing and ctx.reuse_existing:
                ctx.id_map[old] = existing["_id"]
                ctx.report.add_reused(
                    kind="skill", name=name, original_id=old, existing_id=existing["_id"]
                )
                continue
            renamed_from = ""
            if existing:
                if ctx.conflict == "skip":
                    ctx.report.add_skipped(kind="skill", name=name, original_id=old)
                    continue
                new_name, renamed_from = await _unique_name("tools", name)
                skill_md_content = _rename_skill_frontmatter(skill_md_content, new_name)
                file_entries = [("SKILL.md", skill_md_content)] + [
                    (p, c) for p, c in file_entries if p != "SKILL.md"
                ]

            parsed = parse_skill_markdown(skill_md_content, "SKILL.md")
            parsed_dir = ParsedSkillDirectory(
                parsed=parsed,
                files=[
                    SkillFileEntry(path=p, content=c, size=len(c.encode("utf-8")))
                    for p, c in file_entries
                ],
            )
            if ctx.dry_run:
                payload = {"name": parsed.name, "original_id": old}
                _map_created(ctx, kind="skill", payload=payload, new_id="", renamed_from=renamed_from)
                continue
            doc = await ToolService.create_tool_from_directory(
                parsed_dir, directory_name=name, created_by=ctx.imported_by
            )
            _map_created(
                ctx, kind="skill",
                payload={"name": doc["name"], "original_id": old},
                new_id=doc["_id"], renamed_from=renamed_from,
            )
        except SkillParseError as exc:
            ctx.report.error(kind="skill", name=name, message=f"SKILL.md 解析失败: {exc}")
        except Exception as exc:
            ctx.report.error(kind="skill", name=name, message=str(exc))


async def _phase_tools(entries: list[dict], extract_dir: Path, ctx: _Ctx) -> None:
    from app.services.tool_service import ToolService

    for res in entries:
        try:
            payload = _payload(extract_dir, res)
            old = payload.get("original_id", "")
            existing = await _find_by_name("tools", payload["name"])
            if existing and ctx.reuse_existing:
                ctx.id_map[old] = existing["_id"]
                ctx.report.add_reused(
                    kind="tool", name=payload["name"], original_id=old, existing_id=existing["_id"]
                )
                continue
            renamed_from = ""
            if existing:
                if ctx.conflict == "skip":
                    ctx.report.add_skipped(kind="tool", name=payload["name"], original_id=old)
                    continue
                payload["name"], renamed_from = await _unique_name("tools", payload["name"])
            if ctx.dry_run:
                _map_created(ctx, kind="tool", payload=payload, new_id="", renamed_from=renamed_from)
                continue
            doc = await ToolService.create_custom_tool(
                name=payload["name"],
                description=payload.get("description", ""),
                source=payload.get("source", ""),
                user_args_schema=payload.get("user_args_schema") or None,
                llm_args_schema=payload.get("llm_args_schema") or None,
                endpoint=payload.get("endpoint") or None,
                code=payload.get("code", ""),
                prebuilt_name=payload.get("prebuilt_name", ""),
            )
            _map_created(ctx, kind="tool", payload=payload, new_id=doc["_id"], renamed_from=renamed_from)
        except Exception as exc:
            ctx.report.error(kind="tool", name=res.get("name", ""), message=str(exc))


async def _phase_mcp(entries: list[dict], extract_dir: Path, ctx: _Ctx) -> None:
    from app.services.mcp_connection_service import McpConnectionService

    for res in entries:
        try:
            payload = _payload(extract_dir, res)
            _announce_redacted(payload, "mcp_connection", ctx)
            old = payload.get("original_id", "")
            existing = await _find_by_name("mcp_connections", payload["name"])
            if existing and ctx.reuse_existing:
                ctx.id_map[old] = existing["_id"]
                ctx.report.add_reused(
                    kind="mcp_connection", name=payload["name"], original_id=old,
                    existing_id=existing["_id"],
                )
                continue
            renamed_from = ""
            if existing:
                if ctx.conflict == "skip":
                    ctx.report.add_skipped(kind="mcp_connection", name=payload["name"], original_id=old)
                    continue
                payload["name"], renamed_from = await _unique_name("mcp_connections", payload["name"])
            # 分组引用重写（miss → 未分组）
            rewiring.rewrite_mcp_category_ref(
                payload, ctx.id_map, _make_on_miss(ctx, "mcp_connection", payload["name"])
            )
            if ctx.dry_run:
                _map_created(ctx, kind="mcp_connection", payload=payload, new_id="", renamed_from=renamed_from)
                continue
            doc = await McpConnectionService.create_connection(
                {
                    "name": payload["name"],
                    "description": payload.get("description", ""),
                    "category_id": payload.get("category_id", ""),
                    "url": payload["url"],
                    "protocol": payload.get("protocol", "streamable-http"),
                    "auth_type": payload.get("auth_type", "none"),
                    "auth_config": {},
                    "timeout": payload.get("timeout", 30),
                    "default_params": payload.get("default_params") or {},
                }
            )
            ctx.created_mcp.append({"original_id": old, "new_id": doc["_id"], "name": doc["name"]})
            _map_created(ctx, kind="mcp_connection", payload=payload, new_id=doc["_id"], renamed_from=renamed_from)
        except Exception as exc:
            ctx.report.error(kind="mcp_connection", name=res.get("name", ""), message=str(exc))


async def _phase_kbs(entries: list[dict], extract_dir: Path, ctx: _Ctx) -> None:
    from app.services.kb_service import KnowledgeBaseService

    for res in entries:
        try:
            payload = _payload(extract_dir, res)
            old = payload.get("original_id", "")
            existing = await _find_by_name("knowledge_bases", payload["name"], {"type": "tree"})
            if existing and ctx.reuse_existing:
                ctx.id_map[old] = existing["_id"]
                ctx.report.add_reused(
                    kind="knowledge_base", name=payload["name"], original_id=old,
                    existing_id=existing["_id"],
                )
                continue
            renamed_from = ""
            if existing:
                if ctx.conflict == "skip":
                    ctx.report.add_skipped(kind="knowledge_base", name=payload["name"], original_id=old)
                    continue
                payload["name"], renamed_from = await _unique_name("knowledge_bases", payload["name"])
            if ctx.dry_run:
                _map_created(ctx, kind="knowledge_base", payload=payload, new_id="", renamed_from=renamed_from)
                continue
            kb_doc = await KnowledgeBaseService.create_kb(
                name=payload["name"],
                description=payload.get("description", ""),
                owner_user_id=ctx.imported_by,
                type="tree",
            )
            files_dir = extract_dir / "kbs" / old / "files"
            if files_dir.is_dir():
                files = [
                    (str(p.relative_to(files_dir)), p.read_bytes())
                    for p in sorted(files_dir.rglob("*.md"))
                ]
                result = await KnowledgeBaseService._upload_tree(kb_doc["_id"], files)
                for err in result.get("errors") or []:
                    ctx.report.warning(
                        kind="knowledge_base", name=payload["name"],
                        field=err.get("filename", ""),
                        message=f"文件导入失败：{err.get('error', '')}",
                    )
            _map_created(ctx, kind="knowledge_base", payload=payload, new_id=kb_doc["_id"], renamed_from=renamed_from)
        except Exception as exc:
            ctx.report.error(kind="knowledge_base", name=res.get("name", ""), message=str(exc))


def _topo_order_workflows(payloads: dict[str, dict]) -> list[dict]:
    """包内 workflow 按 subflow 依赖拓扑排序（Kahn）；环上剩余节点按清单顺序附加，
    环边引用在重写阶段自然产生 miss warning。"""
    ids = list(payloads)
    deps: dict[str, set[str]] = {oid: set() for oid in ids}
    for oid, p in payloads.items():
        for node in p.get("nodes") or []:
            if node.get("type") == "subflow":
                wid = (node.get("config") or {}).get("workflow_id", "")
                if wid in payloads and wid != oid:
                    deps[oid].add(wid)

    order: list[dict] = []
    done: set[str] = set()
    while len(done) < len(ids):
        progress = False
        for oid in ids:
            if oid not in done and deps[oid] <= done:
                order.append(payloads[oid])
                done.add(oid)
                progress = True
        if not progress:
            order += [payloads[oid] for oid in ids if oid not in done]
            break
    return order


async def _phase_workflows(entries: list[dict], extract_dir: Path, ctx: _Ctx) -> None:
    from app.services.workflow_service import WorkflowService

    payloads: dict[str, dict] = {}
    for res in entries:
        try:
            payload = _payload(extract_dir, res)
            payloads[payload.get("original_id", "")] = payload
        except Exception as exc:
            ctx.report.error(kind="workflow", name=res.get("name", ""), message=str(exc))

    for payload in _topo_order_workflows(payloads):
        old = payload.get("original_id", "")
        try:
            _announce_redacted(payload, "workflow", ctx)
            mcp_refs = payload.pop("_mcp_tool_refs", [])
            pending_agent_refs: list[dict] = []
            rewiring.rewrite_workflow_refs(
                payload,
                ctx.id_map,
                _make_on_miss(ctx, "workflow", payload.get("name", "")),
                pending_agent_refs,
            )
            # workflow 无 name 唯一约束，一律新建（设计 §5.2）
            if ctx.dry_run:
                _map_created(ctx, kind="workflow", payload=payload, new_id="", renamed_from="")
                if pending_agent_refs:
                    ctx.wf_agent_ref_plans.append(
                        {
                            "workflow_id": old,  # dry_run 无新 ID，占位（不落库）
                            "workflow_name": payload["name"],
                            "refs": pending_agent_refs,
                            "dry_run": True,
                        }
                    )
                continue
            doc = await WorkflowService.create(
                name=payload["name"],
                description=payload.get("description", ""),
                tags=payload.get("tags") or [],
                created_by=ctx.imported_by,
                nodes=payload.get("nodes") or [],
                edges=payload.get("edges") or [],
            )
            ctx.id_map[old] = doc["_id"]
            ctx.report.add_created(
                kind="workflow", name=payload["name"], original_id=old, new_id=doc["_id"]
            )
            if mcp_refs:
                ctx.workflow_plans.append(
                    {"workflow_id": doc["_id"], "workflow_name": payload["name"], "refs": mcp_refs}
                )
            if pending_agent_refs:
                ctx.wf_agent_ref_plans.append(
                    {
                        "workflow_id": doc["_id"],
                        "workflow_name": payload["name"],
                        "refs": pending_agent_refs,
                        "dry_run": False,
                    }
                )
        except Exception as exc:
            ctx.report.error(kind="workflow", name=payload.get("name", ""), message=str(exc))


async def _phase_agents(entries: list[dict], extract_dir: Path, ctx: _Ctx) -> None:
    from app.services.agent_service import AgentService

    for res in entries:
        try:
            payload = _payload(extract_dir, res)
            _announce_redacted(payload, "agent", ctx)
            old = payload.get("original_id", "")
            existing = await _find_by_name("agents", payload["name"])
            if existing and ctx.reuse_existing:
                ctx.id_map[old] = existing["_id"]
                ctx.report.add_reused(
                    kind="agent", name=payload["name"], original_id=old,
                    existing_id=existing["_id"],
                )
                continue
            renamed_from = ""
            if existing:
                if ctx.conflict == "skip":
                    ctx.report.add_skipped(kind="agent", name=payload["name"], original_id=old)
                    continue
                payload["name"], renamed_from = await _unique_name("agents", payload["name"])
            rewiring.rewrite_agent_refs(
                payload, ctx.id_map, _make_on_miss(ctx, "agent", payload["name"])
            )
            if ctx.dry_run:
                _map_created(ctx, kind="agent", payload=payload, new_id="", renamed_from=renamed_from)
                continue
            doc = await AgentService.create_agent(
                name=payload["name"],
                description=payload.get("description", ""),
                prompt_slots=payload.get("prompt_slots") or {},
                skill_ids=payload.get("skill_ids") or [],
                mcp_connection_ids=payload.get("mcp_connection_ids") or [],
                builtin_config=payload.get("builtin_config") or [],
                workflow_ids=payload.get("workflow_ids") or [],
                custom_tools=payload.get("custom_tools") or [],
                knowledge_base_ids=payload.get("knowledge_base_ids") or [],
                default_model=payload.get("default_model", ""),
                voice_enabled=bool(payload.get("voice_enabled", False)),
                max_retry=payload.get("max_retry", 3),
                max_tokens=payload.get("max_tokens", 0),
                welcome_message=payload.get("welcome_message", ""),
                recommended_items=payload.get("recommended_items") or [],
                avatar="",
                user_skills_enabled=bool(payload.get("user_skills_enabled", True)),
            )
            _map_created(ctx, kind="agent", payload=payload, new_id=doc["_id"], renamed_from=renamed_from)
        except Exception as exc:
            ctx.report.error(kind="agent", name=res.get("name", ""), message=str(exc))


async def _phase_rewrite_wf_agent_refs(ctx: _Ctx) -> None:
    """agents 阶段结束后，补写 workflow agent 节点引用。

    agents 在 workflows 之后导入，创建 workflow 时包内 agent 引用先挂起；
    此处按最终 id_map 补写。dry_run 只判命中与否（决定 warning），不落库。
    """
    if not ctx.wf_agent_ref_plans:
        return
    wf_col = get_database()["workflows"]

    for plan in ctx.wf_agent_ref_plans:
        for ref in plan["refs"]:
            old = ref["old_agent_id"]
            new = ctx.id_map.get(old)
            if not new:
                ctx.report.warning(
                    kind="workflow",
                    name=plan["workflow_name"],
                    field=f"nodes[{ref['node_id']}].agent_id",
                    message=(
                        f"依赖的智能体 {old} 不在包内且本实例未找到，"
                        f"节点引用悬空（发布前校验会拦截，请手动重选节点智能体）"
                    ),
                )
                continue
            if plan.get("dry_run") or ctx.dry_run:
                continue
            wf_doc = await wf_col.find_one({"_id": plan["workflow_id"]})
            if wf_doc is None:
                continue
            updated = False
            for node in wf_doc.get("nodes") or []:
                if node.get("node_id") == ref["node_id"]:
                    node.setdefault("config", {})["agent_id"] = new
                    updated = True
            if updated:
                await wf_col.update_one(
                    {"_id": plan["workflow_id"]}, {"$set": {"nodes": wf_doc["nodes"]}}
                )


async def _phase_discover_rebind(ctx: _Ctx, *, auto_discover: bool) -> None:
    if ctx.dry_run:
        return
    if auto_discover and ctx.created_mcp:
        from app.services.mcp_connection_service import McpConnectionService

        for conn in ctx.created_mcp:
            try:
                result = await McpConnectionService.discover_tools(conn["new_id"])
                if result.get("error"):
                    ctx.report.warning(
                        kind="mcp_connection", name=conn["name"],
                        message=f"导入后自动 discover 失败：{result['error']}（请补填凭证后重试）",
                    )
            except Exception as exc:
                ctx.report.warning(
                    kind="mcp_connection", name=conn["name"],
                    message=f"导入后自动 discover 异常：{exc}",
                )
    if ctx.workflow_plans:
        await rewiring.rebind_mcp_tool_refs(ctx.workflow_plans, ctx.id_map, ctx.report)
