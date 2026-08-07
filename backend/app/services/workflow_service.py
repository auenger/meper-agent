"""WorkflowService — CRUD for workflow templates (the DAG definitions).

Manages the ``workflows`` MongoDB collection.  Each document is a
workflow template with nodes, edges, status, and version info.
"""
from __future__ import annotations

import re
from typing import Any

from loguru import logger
from pymongo import ReturnDocument

from app.core.errors import ConflictError, NotFoundError, ValidationError
from app.db.mongodb import get_database
from app.models.base import utc_now
from app.models.workflow import Workflow, WorkflowStatus


class WorkflowService:
    """Service layer for Workflow template operations."""

    COLLECTION = "workflows"

    @staticmethod
    def _collection():
        return get_database()[WorkflowService.COLLECTION]

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    @staticmethod
    async def create(
        name: str,
        description: str = "",
        tags: list[str] | None = None,
        created_by: str = "",
    ) -> dict:
        """Create a new Workflow template in draft status."""
        wf = Workflow(
            name=name,
            description=description,
            tags=tags or [],
            created_by=created_by,
        )
        doc = wf.model_dump(by_alias=True)
        result = await WorkflowService._collection().insert_one(doc)

        logger.info("workflow_created", workflow_id=wf.id, name=name)
        return await WorkflowService._collection().find_one({"_id": result.inserted_id})

    @staticmethod
    async def get(workflow_id: str) -> dict | None:
        """Get a Workflow template by ID."""
        return await WorkflowService._collection().find_one({"_id": workflow_id})

    @staticmethod
    async def get_or_404(workflow_id: str) -> dict:
        """Get a Workflow template by ID or raise NotFoundError."""
        doc = await WorkflowService.get(workflow_id)
        if doc is None:
            raise NotFoundError(
                code="WORKFLOW_NOT_FOUND",
                message=f"工作流模板 {workflow_id} 不存在",
                details={"workflow_id": workflow_id},
            )
        return doc

    @staticmethod
    async def list(
        page: int = 1,
        page_size: int = 20,
        status: WorkflowStatus | None = None,
        name: str | None = None,
    ) -> tuple[list[dict], int]:
        """List Workflow templates with optional filters."""
        query: dict[str, Any] = {}
        if status is not None:
            query["status"] = status.value
        if name:
            query["name"] = {"$regex": re.escape(name), "$options": "i"}

        cursor = (
            WorkflowService._collection()
            .find(query)
            .sort("updated_at", -1)
            .skip((page - 1) * page_size)
            .limit(page_size)
        )
        items = await cursor.to_list(length=page_size)
        total = await WorkflowService._collection().count_documents(query)
        return items, total

    @staticmethod
    async def update(
        workflow_id: str,
        updates: dict[str, Any],
    ) -> dict:
        """Update a Workflow template.

        Args:
            workflow_id: Workflow ID.
            updates: Fields to update (name, description, nodes, edges, tags).

        Returns:
            Updated document.

        Raises:
            NotFoundError: If workflow not found.
        """
        # Prevent changing the ID
        updates.pop("_id", None)
        updates.pop("id", None)

        updates["updated_at"] = utc_now()

        updated = await WorkflowService._collection().find_one_and_update(
            {"_id": workflow_id},
            {"$set": updates},
            return_document=ReturnDocument.AFTER,
        )

        if updated is None:
            raise NotFoundError(
                code="WORKFLOW_NOT_FOUND",
                message=f"工作流模板 {workflow_id} 不存在",
                details={"workflow_id": workflow_id},
            )

        # Sync registry if workflow is published
        if updated.get("status") == WorkflowStatus.PUBLISHED.value:
            await WorkflowService._sync_registry(workflow_id, updated)

        logger.info("workflow_updated", workflow_id=workflow_id)
        return updated

    @staticmethod
    async def delete(workflow_id: str) -> bool:
        """Delete a Workflow template.

        Also removes the corresponding ``workflow_registry`` entry so the
        published-workflow list does not keep an orphan index that 404s on open.

        Returns:
            True if deleted, False if not found.
        """
        result = await WorkflowService._collection().delete_one({"_id": workflow_id})
        if result.deleted_count:
            logger.info("workflow_deleted", workflow_id=workflow_id)
            # 同步清理 registry 索引，避免留下指向已删除模板的孤儿条目
            from app.services.workflow_registry_service import WorkflowRegistryService
            await WorkflowRegistryService.delete_by_workflow_id(workflow_id)
            # 清理 agents.workflow_ids 中的残留引用
            try:
                await get_database()["agents"].update_many(
                    {"workflow_ids": workflow_id},
                    {"$pull": {"workflow_ids": workflow_id}},
                )
            except Exception as exc:
                logger.warning(
                    "workflow_agent_refs_cleanup_partial",
                    workflow_id=workflow_id,
                    error=str(exc),
                )
            return True
        return False

    @staticmethod
    async def publish(workflow_id: str) -> dict:
        """Publish a Workflow template (draft → published) and register it.

        Args:
            workflow_id: Workflow ID.

        Returns:
            Updated document.

        Raises:
            ValidationError: If workflow fails validation checks.
            ConflictError: If already published.
        """
        doc = await WorkflowService.get_or_404(workflow_id)

        # Validate workflow structure before publishing
        WorkflowService._validate_for_publish(doc)

        current_status = WorkflowStatus(doc.get("status", "draft"))

        if current_status == WorkflowStatus.PUBLISHED:
            raise ConflictError(
                code="WORKFLOW_ALREADY_PUBLISHED",
                message=f"工作流模板 {workflow_id} 已发布",
                details={"workflow_id": workflow_id},
            )

        new_version = doc.get("version", 1) + 1
        now = utc_now()

        updated = await WorkflowService._collection().find_one_and_update(
            {"_id": workflow_id},
            {
                "$set": {
                    "status": WorkflowStatus.PUBLISHED.value,
                    "version": new_version,
                    "updated_at": now,
                }
            },
            return_document=ReturnDocument.AFTER,
        )

        # Also register in workflow registry
        try:
            from app.services.workflow_registry_service import WorkflowRegistryService

            await WorkflowRegistryService.register(
                name=doc.get("name", ""),
                description=doc.get("description", ""),
                input_schema=_extract_input_schema(doc.get("nodes", [])),
                workflow_id=workflow_id,
                has_human_node=_has_human_node(doc.get("nodes", [])),
                version=str(new_version),
                tags=doc.get("tags", []),
            )
        except ConflictError:
            # Already registered — update instead
            registry_entry = await WorkflowRegistryService.get_by_workflow_id(workflow_id)
            if registry_entry:
                await WorkflowRegistryService.update(
                    entry_id=registry_entry["_id"],
                    updates={
                        "name": doc.get("name", ""),
                        "version": str(new_version),
                        "description": doc.get("description", ""),
                        "tags": doc.get("tags", []),
                        "has_human_node": _has_human_node(doc.get("nodes", [])),
                    },
                )

        logger.info("workflow_published", workflow_id=workflow_id, version=new_version)
        return updated

    @staticmethod
    async def archive(workflow_id: str) -> dict:
        """Archive a Workflow template (published → archived)."""
        doc = await WorkflowService.get_or_404(workflow_id)
        current_status = WorkflowStatus(doc.get("status", "draft"))

        if current_status == WorkflowStatus.ARCHIVED:
            raise ConflictError(
                code="WORKFLOW_ALREADY_ARCHIVED",
                message=f"工作流模板 {workflow_id} 已归档",
                details={"workflow_id": workflow_id},
            )

        updated = await WorkflowService._collection().find_one_and_update(
            {"_id": workflow_id},
            {"$set": {"status": WorkflowStatus.ARCHIVED.value, "updated_at": utc_now()}},
            return_document=ReturnDocument.AFTER,
        )

        logger.info("workflow_archived", workflow_id=workflow_id)
        return updated

    # ------------------------------------------------------------------
    # Registry sync
    # ------------------------------------------------------------------

    @staticmethod
    async def _sync_registry(workflow_id: str, doc: dict) -> None:
        """Sync name/description/tags to workflow_registry for published workflows."""
        from app.services.workflow_registry_service import WorkflowRegistryService

        registry_entry = await WorkflowRegistryService.get_by_workflow_id(workflow_id)
        if registry_entry:
            await WorkflowRegistryService.update(
                entry_id=registry_entry["_id"],
                updates={
                    "name": doc.get("name", ""),
                    "description": doc.get("description", ""),
                    "tags": doc.get("tags", []),
                },
            )
            logger.debug(
                "registry_synced_on_update",
                workflow_id=workflow_id,
                name=doc.get("name", ""),
            )

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    @staticmethod
    def _validate_for_publish(doc: dict) -> None:
        """Validate workflow structure before publishing.

        Args:
            doc: Workflow MongoDB document.

        Raises:
            ValidationError: If validation fails with code "WORKFLOW_VALIDATION_ERROR".
        """
        nodes: list[dict] = doc.get("nodes", [])
        edges: list[dict] = doc.get("edges", [])
        errors: list[dict] = []

        # Build node ID set for quick lookup
        node_ids = {n.get("node_id") for n in nodes if isinstance(n, dict) and n.get("node_id")}

        # ── Basic structure validation ──

        # 1. At least 1 node (start node is required, end node is optional)
        if len(nodes) < 1:
            errors.append({
                "code": "NO_START_OR_END",
                "message": "工作流至少需要包含 1 个节点",
            })
            # Short-circuit: no point checking further
            raise ValidationError(
                code="WORKFLOW_VALIDATION_ERROR",
                message="工作流验证失败，发布前请修复以下问题",
                details={"errors": errors},
            )

        # 2. Start node is required; end node is recommended but not mandatory
        has_start = any(n.get("type") == "start" for n in nodes if isinstance(n, dict))
        if not has_start:
            errors.append({
                "code": "NO_START_NODE",
                "message": "工作流必须包含开始(start)节点",
            })

        # 3. At least some connectivity (edges or next_nodes)
        has_edges = len(edges) > 0
        has_next_nodes = any(
            isinstance(n, dict) and n.get("config", {}).get("next_nodes")
            for n in nodes
        )
        if not has_edges and not has_next_nodes and len(nodes) >= 2:
            errors.append({
                "code": "NO_EDGES",
                "message": "工作流至少需要 1 条连接来串联节点",
            })

        # 4. All edge source/target must reference existing nodes
        for edge in edges:
            if not isinstance(edge, dict):
                continue
            source = edge.get("source")
            target = edge.get("target")
            edge_label = edge.get("label") or edge.get("edge_id", "")

            if source not in node_ids:
                errors.append({
                    "code": "MISSING_NODE_IN_EDGE",
                    "message": f"边 '{edge_label}' 引用了不存在的源节点 '{source}'",
                })
            if target not in node_ids:
                errors.append({
                    "code": "MISSING_NODE_IN_EDGE",
                    "message": f"边 '{edge_label}' 引用了不存在的目标节点 '{target}'",
                })

        # 5. All next_nodes references must target existing nodes
        for node in nodes:
            if not isinstance(node, dict):
                continue
            next_nodes = node.get("config", {}).get("next_nodes", [])
            if not isinstance(next_nodes, list):
                continue
            for nxt in next_nodes:
                if not isinstance(nxt, dict):
                    continue
                target = nxt.get("target", "")
                if target and target not in node_ids:
                    node_label = node.get("label", node.get("node_id", ""))
                    errors.append({
                        "code": "MISSING_NODE_IN_NEXT_NODES",
                        "message": f"节点 '{node_label}' 的 next_nodes 引用了不存在的目标节点 '{target}'",
                    })

        # ── Node config validation ──

        for node in nodes:
            if not isinstance(node, dict):
                continue

            node_type = node.get("type", "")
            config = node.get("config", {})
            node_id = node.get("node_id", "")
            label = node.get("label", node_type)

            if node_type == "agent":
                if not config.get("agent_id"):
                    errors.append({
                        "code": "AGENT_MISSING_ID",
                        "message": f"Agent 节点 '{label}' 必须选择一个 Agent",
                        "details": {"node_id": node_id},
                    })
                input_query = config.get("input_query", "")
                if not input_query or (isinstance(input_query, str) and not input_query.strip()):
                    errors.append({
                        "code": "AGENT_MISSING_QUERY",
                        "message": f"Agent 节点 '{label}' 必须填写查询内容",
                        "details": {"node_id": node_id},
                    })
            elif node_type == "tool":
                if not config.get("tool_id"):
                    errors.append({
                        "code": "TOOL_MISSING_ID",
                        "message": f"工具节点 '{label}' 必须选择一个工具",
                        "details": {"node_id": node_id},
                    })
            elif node_type == "gateway":
                conditions = config.get("conditions", [])
                if not isinstance(conditions, list) or len(conditions) == 0:
                    errors.append({
                        "code": "GATEWAY_NO_CONDITIONS",
                        "message": f"网关节点 '{label}' 至少需要 1 个条件分支",
                        "details": {"node_id": node_id},
                    })
            elif node_type == "human":
                if not config.get("title"):
                    errors.append({
                        "code": "HUMAN_MISSING_TITLE",
                        "message": f"人工审批节点 '{label}' 必须设置审批标题",
                        "details": {"node_id": node_id},
                    })

        # Raise if any errors
        if errors:
            raise ValidationError(
                code="WORKFLOW_VALIDATION_ERROR",
                message="工作流验证失败，发布前请修复以下问题",
                details={"errors": errors},
            )


def _extract_input_schema(nodes: list[dict]) -> dict:
    """Extract input schema from the start node's output_variables.

    The start node config stores variable definitions in ``output_variables``
    (each item: ``{name, type, constraints: {required, default_value}}``),
    which is what ``StartNodeExecutor`` reads at runtime. We build a JSON-schema-
    like dict from these so the workflow registry knows what inputs the workflow
    expects. Falls back to legacy ``config.input_schema`` if present.
    """
    for node in nodes:
        if not (isinstance(node, dict) and node.get("type") == "start"):
            continue
        config = node.get("config", {})
        output_vars = config.get("output_variables", [])
        if output_vars:
            properties: dict[str, dict] = {}
            required: list[str] = []
            for var in output_vars:
                if not isinstance(var, dict):
                    continue
                name = var.get("name")
                if not name:
                    continue
                properties[name] = {"type": var.get("type", "text")}
                if var.get("constraints", {}).get("required"):
                    required.append(name)
            return {"type": "object", "properties": properties, "required": required}
        # Legacy fallback: older configs may store a raw input_schema dict.
        return config.get("input_schema", {})
    return {}


def _has_human_node(nodes: list[dict]) -> bool:
    """Check if the workflow has any human approval nodes."""
    return any(isinstance(node, dict) and node.get("type") == "human" for node in nodes)
