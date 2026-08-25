"""引用重写 — 导入时按 id_map 改写跨资源 ID 引用 + MCP 工具重绑。

重写规则见 docs/resource-transfer-plan.md §5.3。所有函数就地修改
payload，miss 的引用通过 ``on_miss`` 回调上报（importer 转成 warning）。
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from app.db.mongodb import get_database

# on_miss(field, old_id, where) — where 为定位信息（如 "nodes[node_3].agent_id"）
OnMiss = Callable[[str, str, str], None]


def rewrite_agent_refs(payload: dict, id_map: dict[str, str], on_miss: OnMiss) -> None:
    """就地重写 Agent payload 的全部 ID 引用字段。

    - 四个 *_ids 列表：命中替换，miss 剔除
    - custom_tools[].tool_id：miss 时剔除整个绑定
    - default_model：``model_`` 前缀走映射（miss 置空），裸名原样保留
    """
    where = payload.get("name", payload.get("original_id", ""))
    for field in ("skill_ids", "mcp_connection_ids", "workflow_ids", "knowledge_base_ids"):
        ids = payload.get(field) or []
        kept: list[str] = []
        for old in ids:
            if old in id_map:
                kept.append(id_map[old])
            else:
                on_miss(field, old, where)
        payload[field] = kept

    kept_bindings: list[dict] = []
    for b in payload.get("custom_tools") or []:
        old = b.get("tool_id", "")
        if old in id_map:
            kept_bindings.append(
                {"tool_id": id_map[old], "user_args": b.get("user_args") or {}}
            )
        else:
            on_miss("custom_tools[].tool_id", old, where)
    payload["custom_tools"] = kept_bindings

    dm = payload.get("default_model") or ""
    if dm.startswith("model_"):
        if dm in id_map:
            payload["default_model"] = id_map[dm]
        else:
            payload["default_model"] = ""
            on_miss("default_model", dm, where)


def rewrite_workflow_refs(
    payload: dict,
    id_map: dict[str, str],
    on_miss: OnMiss,
    pending_agent_refs: list[dict] | None = None,
) -> None:
    """就地重写 Workflow payload 节点 config 中的跨资源 ID。

    MCP 工具节点（在 ``_mcp_tool_refs`` 清单内）的 ``config.tool_id`` 保留
    旧值，待连接 discover 后由 :func:`rebind_mcp_tool_refs` 回填。

    agent 节点引用若 ``id_map`` 未命中（agents 阶段晚于 workflows，包内
    agent 尚未登记），保留旧值并追加进 ``pending_agent_refs``，由 importer
    在 agents 阶段结束后统一补写（见 importer._phase_rewrite_wf_agent_refs）。
    """
    wf_name = payload.get("name", payload.get("original_id", ""))
    mcp_ref_nodes = {r["node_id"] for r in payload.get("_mcp_tool_refs") or []}

    for node in payload.get("nodes") or []:
        node_id = node.get("node_id", "")
        where = f"{wf_name} nodes[{node_id}]"
        config = node.get("config") or {}
        ntype = node.get("type", "")

        if ntype == "agent" and config.get("agent_id"):
            old = config["agent_id"]
            if old in id_map:
                config["agent_id"] = id_map[old]
            elif pending_agent_refs is not None:
                # agents 阶段在 workflows 之后 — 先挂起，导入 agent 后补写
                pending_agent_refs.append({"node_id": node_id, "old_agent_id": old})
            else:
                on_miss("agent_id", old, where)
        elif ntype == "tool" and node_id in mcp_ref_nodes:
            continue  # MCP 工具：discover 后重绑
        elif ntype == "tool" and config.get("tool_id"):
            old = config["tool_id"]
            if old in id_map:
                config["tool_id"] = id_map[old]
            else:
                on_miss("tool_id", old, where)
        elif ntype == "subflow" and config.get("workflow_id"):
            old = config["workflow_id"]
            if old in id_map:
                config["workflow_id"] = id_map[old]
            else:
                on_miss("workflow_id", old, where)
        elif ntype == "kb_search" and config.get("kb_ids"):
            kept: list[str] = []
            for old in config["kb_ids"]:
                if old in id_map:
                    kept.append(id_map[old])
                else:
                    on_miss("kb_ids", old, where)
            config["kb_ids"] = kept


def rewrite_mcp_category_ref(payload: dict, id_map: dict[str, str], on_miss: OnMiss) -> None:
    """MCP 连接的 category_id：命中替换，miss 置空（归未分组）。"""
    old = payload.get("category_id") or ""
    if not old:
        return
    if old in id_map:
        payload["category_id"] = id_map[old]
    else:
        payload["category_id"] = ""
        on_miss("category_id", old, payload.get("name", ""))


async def rebind_mcp_tool_refs(
    workflow_plans: list[dict[str, Any]],
    id_map: dict[str, str],
    report,
) -> None:
    """discover 后按 ``(新 conn_id, 原工具名)`` 回填 workflow 工具节点的 tool_id。

    Args:
        workflow_plans: ``[{"workflow_id", "workflow_name", "refs": [...]}]``，
            refs 条目含 node_id / tool_name / mcp_connection_original_id。
        report: ReportCollector（warning 上报）。
    """
    tools_col = get_database()["tools"]
    wf_col = get_database()["workflows"]

    for plan in workflow_plans:
        for ref in plan["refs"]:
            new_conn = id_map.get(ref["mcp_connection_original_id"], "")
            if not new_conn:
                report.warning(
                    kind="workflow",
                    name=plan["workflow_name"],
                    field=f"nodes[{ref['node_id']}].tool_id",
                    message=(
                        f"工具节点引用的 MCP 连接未能建立，工具"
                        f"'{ref['tool_name']}' 引用悬空，请检查包完整性"
                    ),
                )
                continue
            tool_doc = await tools_col.find_one(
                {
                    "mcp_connection_id": new_conn,
                    "name": ref["tool_name"],
                    "source": "mcp",
                }
            )
            if tool_doc is None:
                report.warning(
                    kind="workflow",
                    name=plan["workflow_name"],
                    field=f"nodes[{ref['node_id']}].tool_id",
                    message=(
                        f"MCP 工具 '{ref['tool_name']}' 尚未在本实例发现"
                        f"（连接需补填凭证并 discover），节点引用暂时悬空"
                    ),
                )
                continue
            wf_doc = await wf_col.find_one({"_id": plan["workflow_id"]})
            if wf_doc is None:
                continue
            updated = False
            for node in wf_doc.get("nodes") or []:
                if node.get("node_id") == ref["node_id"]:
                    node.setdefault("config", {})["tool_id"] = tool_doc["_id"]
                    updated = True
            if updated:
                await wf_col.update_one(
                    {"_id": plan["workflow_id"]}, {"$set": {"nodes": wf_doc["nodes"]}}
                )
