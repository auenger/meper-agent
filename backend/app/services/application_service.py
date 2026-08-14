"""Application service — CRUD for authorization-boundary applications."""
from __future__ import annotations

from loguru import logger

from app.core.errors import ConflictError, NotFoundError, ValidationError
from app.db.mongodb import get_database
from app.models.base import generate_id, utc_now


async def _validate_mcp_connection_ids(mcp_connection_ids: list[str]) -> None:
    """校验绑定的 MCP 连接 ID 全部真实存在。

    Raises:
        ValidationError: 如果有 ID 在 mcp_connections 集合中不存在。
    """
    if not mcp_connection_ids:
        return
    db = get_database()
    found = await db["mcp_connections"].count_documents(
        {"_id": {"$in": mcp_connection_ids}}
    )
    if found != len(set(mcp_connection_ids)):
        existing = await db["mcp_connections"].find(
            {"_id": {"$in": mcp_connection_ids}}, {"_id": 1}
        ).to_list(length=None)
        existing_ids = {d["_id"] for d in existing}
        missing = [i for i in dict.fromkeys(mcp_connection_ids) if i not in existing_ids]
        raise ValidationError(
            code="APPLICATION_MCP_NOT_FOUND",
            message=f"MCP 连接不存在: {', '.join(missing)}",
            details={"missing": missing},
        )


class ApplicationService:
    """Service layer for application operations."""

    COLLECTION = "applications"

    @staticmethod
    def _collection():
        return get_database()[ApplicationService.COLLECTION]

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    @staticmethod
    async def create_application(data: dict) -> dict:
        """Create a new application.

        Raises:
            ConflictError: If name is not unique.
        """
        col = ApplicationService._collection()

        name = data["name"]
        existing = await col.find_one({"name": name})
        if existing is not None:
            raise ConflictError(
                code="APPLICATION_NAME_CONFLICT",
                message=f"应用名称 '{name}' 已被占用",
                details={"field": "name"},
            )

        # 校验绑定的 MCP 连接全部存在
        await _validate_mcp_connection_ids(data.get("mcp_connection_ids", []))

        now_iso = utc_now().isoformat()
        doc = {
            "_id": generate_id("app"),
            "name": name,
            "description": data.get("description", ""),
            "mcp_connection_ids": data.get("mcp_connection_ids", []),
            "login_config": data.get("login_config", {}),
            "created_at": now_iso,
            "updated_at": now_iso,
        }
        await col.insert_one(doc)
        logger.info(
            "application_created",
            application_id=doc["_id"],
            name=doc["name"],
            mcp_count=len(doc["mcp_connection_ids"]),
        )
        return doc

    @staticmethod
    async def list_applications() -> list[dict]:
        """List all applications ordered by created_at asc."""
        col = ApplicationService._collection()
        cursor = col.find({}).sort("created_at", 1)
        return await cursor.to_list(length=None)

    @staticmethod
    async def get_application(application_id: str) -> dict | None:
        """Get an application by ID."""
        return await ApplicationService._collection().find_one({"_id": application_id})

    @staticmethod
    async def find_by_mcp_connection(mcp_connection_id: str) -> dict | None:
        """Find the application that binds a given MCP connection.

        Used at credential-resolve time to map MCP → application.
        Returns the first match (an MCP normally belongs to one application).
        """
        return await ApplicationService._collection().find_one(
            {"mcp_connection_ids": mcp_connection_id}
        )

    @staticmethod
    async def update_application(application_id: str, data: dict) -> dict | None:
        """Update an application (full PUT).

        Raises:
            NotFoundError: If not found.
            ConflictError: If new name conflicts with another application.
        """
        col = ApplicationService._collection()

        existing = await col.find_one({"_id": application_id})
        if existing is None:
            raise NotFoundError(
                code="APPLICATION_NOT_FOUND",
                message=f"应用 {application_id} 不存在",
            )

        new_name = data.get("name")
        if new_name and new_name != existing["name"]:
            dup = await col.find_one({"name": new_name, "_id": {"$ne": application_id}})
            if dup:
                raise ConflictError(
                    code="APPLICATION_NAME_CONFLICT",
                    message=f"应用名称 '{new_name}' 已被占用",
                    details={"field": "name"},
                )

        # 校验绑定的 MCP 连接全部存在
        await _validate_mcp_connection_ids(data.get("mcp_connection_ids", []))

        now_iso = utc_now().isoformat()
        set_fields = {
            "name": data.get("name", existing["name"]),
            "description": data.get("description", existing.get("description", "")),
            "mcp_connection_ids": data.get(
                "mcp_connection_ids", existing.get("mcp_connection_ids", [])
            ),
            "login_config": data.get("login_config", existing.get("login_config", {})),
            "updated_at": now_iso,
        }
        await col.update_one({"_id": application_id}, {"$set": set_fields})
        logger.info("application_updated", application_id=application_id)
        return await ApplicationService.get_application(application_id)

    @staticmethod
    async def delete_application(application_id: str) -> bool:
        """Delete an application. Refuses if it still binds MCP connections.

        Returns:
            True if deleted, False if not found.
        """
        col = ApplicationService._collection()

        existing = await col.find_one({"_id": application_id})
        if existing is None:
            return False

        # Guard: refuse to delete an application that still binds MCPs.
        if existing.get("mcp_connection_ids"):
            raise ConflictError(
                code="APPLICATION_NOT_EMPTY",
                message=(
                    f"应用「{existing['name']}」仍绑定了 "
                    f"{len(existing['mcp_connection_ids'])} 个 MCP，请先解绑"
                ),
            )

        await col.delete_one({"_id": application_id})
        logger.info("application_deleted", application_id=application_id)
        return True
