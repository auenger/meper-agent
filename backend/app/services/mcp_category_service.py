"""MCP category business logic — CRUD with non-empty delete guard."""
from __future__ import annotations

from loguru import logger

from app.core.errors import ConflictError, NotFoundError
from app.db.mongodb import get_database
from app.models.base import generate_id, utc_now


class McpCategoryService:
    """Service layer for MCP category operations."""

    COLLECTION = "mcp_categories"
    MCP_CONNECTIONS_COLLECTION = "mcp_connections"

    @staticmethod
    def _collection():
        return get_database()[McpCategoryService.COLLECTION]

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    @staticmethod
    async def create_category(data: dict) -> dict:
        """Create a new MCP category.

        Raises:
            ConflictError: If name is not unique.
        """
        col = McpCategoryService._collection()

        name = data["name"]
        existing = await col.find_one({"name": name})
        if existing is not None:
            raise ConflictError(
                code="MCP_CATEGORY_NAME_CONFLICT",
                message=f"MCP 分组名称 '{name}' 已被占用",
                details={"field": "name"},
            )

        now_iso = utc_now().isoformat()
        doc = {
            "_id": generate_id("mcpc"),
            "name": name,
            "description": data.get("description", ""),
            "sort": data.get("sort", 0),
            "created_at": now_iso,
            "updated_at": now_iso,
        }
        await col.insert_one(doc)
        logger.info("mcp_category_created", category_id=doc["_id"], name=doc["name"])
        return doc

    @staticmethod
    async def list_categories() -> list[dict]:
        """List all categories ordered by sort asc, then created_at asc."""
        col = McpCategoryService._collection()
        cursor = col.find({}).sort([("sort", 1), ("created_at", 1)])
        return await cursor.to_list(length=None)

    @staticmethod
    async def get_category(category_id: str) -> dict | None:
        """Get a category by ID."""
        return await McpCategoryService._collection().find_one({"_id": category_id})

    @staticmethod
    async def update_category(category_id: str, data: dict) -> dict | None:
        """Update a category (full PUT).

        Raises:
            NotFoundError: If not found.
            ConflictError: If new name conflicts with another category.
        """
        col = McpCategoryService._collection()

        existing = await col.find_one({"_id": category_id})
        if existing is None:
            raise NotFoundError(
                code="MCP_CATEGORY_NOT_FOUND",
                message=f"MCP 分组 {category_id} 不存在",
            )

        new_name = data.get("name")
        if new_name and new_name != existing["name"]:
            dup = await col.find_one({"name": new_name, "_id": {"$ne": category_id}})
            if dup:
                raise ConflictError(
                    code="MCP_CATEGORY_NAME_CONFLICT",
                    message=f"MCP 分组名称 '{new_name}' 已被占用",
                    details={"field": "name"},
                )

        now_iso = utc_now().isoformat()
        set_fields = {
            "name": data.get("name", existing["name"]),
            "description": data.get("description", existing.get("description", "")),
            "sort": data.get("sort", existing.get("sort", 0)),
            "updated_at": now_iso,
        }
        await col.update_one({"_id": category_id}, {"$set": set_fields})
        logger.info("mcp_category_updated", category_id=category_id)
        return await McpCategoryService.get_category(category_id)

    @staticmethod
    async def delete_category(category_id: str) -> bool:
        """Delete a category. Refuses if any MCP connection still references it.

        Raises:
            NotFoundError: If not found.
            ConflictError: If the category is still in use.

        Returns:
            True if deleted, False if not found.
        """
        col = McpCategoryService._collection()

        existing = await col.find_one({"_id": category_id})
        if existing is None:
            return False

        # Guard: refuse to delete a non-empty category.
        conn_col = get_database()[McpCategoryService.MCP_CONNECTIONS_COLLECTION]
        in_use = await conn_col.count_documents({"category_id": category_id})
        if in_use > 0:
            raise ConflictError(
                code="MCP_CATEGORY_NOT_EMPTY",
                message=f"分组「{existing['name']}」下仍有 {in_use} 个 MCP 连接，请先移除或重新归类",
                details={"in_use": in_use},
            )

        await col.delete_one({"_id": category_id})
        logger.info("mcp_category_deleted", category_id=category_id)
        return True
