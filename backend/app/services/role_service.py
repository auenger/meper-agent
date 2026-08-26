"""Role business logic — CRUD, system role initialization, permission caching."""
import json

from loguru import logger

from app.core.errors import ConflictError, NotFoundError, ValidationError
from app.db.mongodb import get_database
from app.db.redis import get_redis_client
from app.models.base import utc_now
from app.models.role import RoleType
from app.schemas.role import RoleCreate, RoleUpdate

# Redis cache TTL: 1 hour
_CACHE_TTL = 3600
_CACHE_PREFIX = "role:perms:"


# Default permissions for the 4 system roles (source of truth for init)
DEFAULT_SYSTEM_ROLE_PERMISSIONS: dict[str, list[str]] = {
    "admin": [
        "user:read", "user:write",
        "agent:read", "agent:write", "agent:invoke",
        "workflow:read", "workflow:write",
        "tool:read", "tool:write",
        "application:read", "application:write",
        "mcp:read", "mcp:write",
        "skill:read", "skill:write",
        "task:read", "task:write", "task:invoke",
        "knowledge:read", "knowledge:write",
        "execution:read:all", "execution:read:own",
        "apikey:manage", "settings:manage",
        "model:read", "model:write",
    ],
    "developer": [
        "agent:read", "agent:write", "agent:invoke",
        "workflow:read", "workflow:write",
        "tool:read", "tool:write",
        "application:read", "application:write",
        "mcp:read", "mcp:write",
        "skill:read", "skill:write",
        "task:read", "task:write",
        "knowledge:read", "knowledge:write",
        "execution:read:own",
        "model:read",
    ],
    "operator": [
        "agent:read", "agent:invoke",
        "task:read",
        "execution:read:own",
        "knowledge:read",  # 权限原则：平台资源只读浏览对全员开放（管理仍限 admin/developer）
        "tool:read", "mcp:read",  # 权限原则：平台资源只读浏览对全员开放
        "model:read",
    ],
    "viewer": [
        "agent:read",
        # 会话/对话是登录用户基础能力（studio 会话首页对全员开放），
        # invoke 权限随之全员授予。
        "agent:invoke",
        "task:read",
        "execution:read:own",  # 个人数据（自己的执行日志）：登录即可读
        "knowledge:read",  # 权限原则：平台资源只读浏览对全员开放
        "tool:read", "mcp:read",  # 权限原则：平台资源只读浏览对全员开放
        "model:read",
    ],
}

# Display metadata for system roles
SYSTEM_ROLE_META: dict[str, dict[str, str]] = {
    "admin": {"display_name": "管理员", "description": "拥有全部权限的系统管理员"},
    "developer": {"display_name": "开发者", "description": "可以创建和管理 Agent、工作流、工具"},
    "operator": {"display_name": "运营者", "description": "可以调用 Agent 并查看自己的执行日志"},
    "viewer": {"display_name": "查看者", "description": "只读权限，仅可查看 Agent 和模型"},
}

# All available permission keys (for the /roles/permissions endpoint)
ALL_PERMISSION_KEYS: list[str] = [
    "user:read", "user:write",
    "agent:read", "agent:write", "agent:invoke",
    "workflow:read", "workflow:write",
    "tool:read", "tool:write",
    "application:read", "application:write",
    "mcp:read", "mcp:write",
    "skill:read", "skill:write",
    "task:read", "task:write", "task:invoke",
    "knowledge:read", "knowledge:write",
    "execution:read:all", "execution:read:own",
    "apikey:manage", "settings:manage",
    "model:read", "model:write",
]

# ---------------------------------------------------------------------------
# One-time data migrations (marker-guarded)
#
# init_system_roles() never touches existing role documents, so roles
# materialized by older code keep stale permission lists forever. Each
# backfill below patches a known gap exactly once per database — the marker
# prevents re-adding permissions an admin may have deliberately removed
# afterwards via the role editor.
# ---------------------------------------------------------------------------

_MIGRATION_COLLECTION = "schema_migrations"

# v1: application:read/write were absent from DEFAULT_SYSTEM_ROLE_PERMISSIONS
# when admin/developer were first written to MongoDB, leaving even admins
# without the "create application" button.
_BACKFILL_V1_MARKER = "backfill_application_perms_v1"
_BACKFILL_V1_TARGETS: dict[str, list[str]] = {
    "admin": ["application:read", "application:write"],
    "developer": ["application:read", "application:write"],
}

# v2: 权限原则重构（§7.6）——个人数据放开 + 平台资源只读全员开放：
# - viewer 缺 execution:read:own（自己的执行日志是个人数据，登录即可读）
# - operator/viewer 缺 knowledge:read（知识库只读浏览对全员开放，管理仍限管理角色）
_BACKFILL_V2_MARKER = "backfill_user_scoped_perms_v2"
_BACKFILL_V2_TARGETS: dict[str, list[str]] = {
    "viewer": ["execution:read:own", "knowledge:read"],
    "operator": ["knowledge:read"],
}

# v3: RBAC 迁移（require_any_role → require_permission）引入的回归修复：
# - developer 丢 tool:write/mcp:write（工具/MCP 管理能力回归，角色描述承诺可管理）
# - operator/viewer 丢 tool:read/mcp:read（平台资源只读浏览对全员开放原则）
_BACKFILL_V3_MARKER = "backfill_tool_mcp_perms_v3"
_BACKFILL_V3_TARGETS: dict[str, list[str]] = {
    "developer": ["tool:write", "mcp:write"],
    "operator": ["tool:read", "mcp:read"],
    "viewer": ["tool:read", "mcp:read"],
}


class RoleService:
    """Service layer for role management operations."""

    COLLECTION = "roles"

    @staticmethod
    def _collection():
        return get_database()[RoleService.COLLECTION]

    # ------------------------------------------------------------------
    # Indexes
    # ------------------------------------------------------------------

    @staticmethod
    async def ensure_indexes() -> None:
        """Create unique index on role name."""
        col = RoleService._collection()
        await col.create_index("name", unique=True, name="idx_roles_name")
        logger.info("Role indexes ensured: idx_roles_name")

    # ------------------------------------------------------------------
    # System role initialization (idempotent)
    # ------------------------------------------------------------------

    @staticmethod
    async def init_system_roles() -> None:
        """Initialize the 4 system roles on startup. Idempotent — only inserts
        roles that don't already exist. Does NOT overwrite existing permissions.
        """
        col = RoleService._collection()

        for role_name, perms in DEFAULT_SYSTEM_ROLE_PERMISSIONS.items():
            meta = SYSTEM_ROLE_META[role_name]
            existing = await col.find_one({"name": role_name})

            if existing is None:
                now_iso = utc_now().isoformat()
                doc = {
                    "_id": f"role_{role_name}",
                    "name": role_name,
                    "display_name": meta["display_name"],
                    "description": meta["description"],
                    "role_type": RoleType.SYSTEM.value,
                    "permissions": perms,
                    "created_at": now_iso,
                    "updated_at": now_iso,
                }
                try:
                    await col.insert_one(doc)
                    logger.info("system_role_created", role=role_name)
                except Exception:
                    # Race condition: another worker inserted it first — ignore
                    pass
            else:
                # System role exists — ensure role_type is correct (migration safety)
                if existing.get("role_type") != RoleType.SYSTEM.value:
                    await col.update_one(
                        {"name": role_name},
                        {"$set": {"role_type": RoleType.SYSTEM.value}},
                    )

        logger.info("system_roles_initialized")

    @staticmethod
    async def backfill_system_role_permissions() -> None:
        """One-time migration: patch permission keys missing from system roles
        that were materialized by older code.

        Marker-guarded so it runs at most once per database. The marker is
        only written after a successful patch, so a failed run retries on the
        next startup. Affected roles' Redis caches are invalidated so the fix
        is visible immediately instead of after the 1h TTL.
        """
        markers = get_database()[_MIGRATION_COLLECTION]
        if await markers.find_one({"_id": _BACKFILL_V1_MARKER}) is not None:
            return

        col = RoleService._collection()
        patched: list[str] = []
        for role_name, missing in _BACKFILL_V1_TARGETS.items():
            result = await col.update_one(
                {"name": role_name, "role_type": RoleType.SYSTEM.value},
                {"$addToSet": {"permissions": {"$each": missing}}},
            )
            if result.modified_count > 0:
                patched.append(role_name)

        # Record the marker even when nothing needed patching (fresh installs
        # already get the correct defaults from init_system_roles).
        await markers.update_one(
            {"_id": _BACKFILL_V1_MARKER},
            {
                "$set": {
                    "executed_at": utc_now().isoformat(),
                    "patched_roles": patched,
                }
            },
            upsert=True,
        )

        for role_name in patched:
            await RoleService.invalidate_cache(role_name)

        logger.info(
            "role_permissions_backfilled marker={} patched_roles={}",
            _BACKFILL_V1_MARKER,
            patched,
        )

    @staticmethod
    async def run_all_system_role_backfills() -> None:
        """Run all one-time system-role permission backfills (v1→v3).

        bootstrap 启动时调用。数据（marker + targets）集中在本模块，调用方
        无需用字面量重写一份（避免与常量漂移）。每个迁移 marker-guarded，
        各自最多执行一次。
        """
        await RoleService.backfill_system_role_permissions()  # v1
        await RoleService._backfill_permissions(_BACKFILL_V2_MARKER, _BACKFILL_V2_TARGETS)
        await RoleService._backfill_permissions(_BACKFILL_V3_MARKER, _BACKFILL_V3_TARGETS)

    @staticmethod
    async def _backfill_permissions(
        marker: str, targets: dict[str, list[str]],
    ) -> None:
        """Generic marker-guarded permission backfill (shared by v1/v2)."""
        markers = get_database()[_MIGRATION_COLLECTION]
        if await markers.find_one({"_id": marker}) is not None:
            return

        col = RoleService._collection()
        patched: list[str] = []
        for role_name, missing in targets.items():
            result = await col.update_one(
                {"name": role_name, "role_type": RoleType.SYSTEM.value},
                {"$addToSet": {"permissions": {"$each": missing}}},
            )
            if result.modified_count > 0:
                patched.append(role_name)

        await markers.update_one(
            {"_id": marker},
            {"$set": {"executed_at": utc_now().isoformat(), "patched_roles": patched}},
            upsert=True,
        )

        for role_name in patched:
            await RoleService.invalidate_cache(role_name)

        logger.info("role_permissions_backfilled marker={} patched_roles={}", marker, patched)

    # ------------------------------------------------------------------
    # Read operations
    # ------------------------------------------------------------------

    @staticmethod
    async def list_roles(role_type: RoleType | None = None) -> list[dict]:
        """List all roles, optionally filtered by role_type."""
        col = RoleService._collection()
        filter_query: dict = {}
        if role_type:
            filter_query["role_type"] = role_type.value

        cursor = col.find(filter_query).sort("created_at", 1)
        return await cursor.to_list(length=200)

    @staticmethod
    async def get_role_by_id(role_id: str) -> dict | None:
        """Find a role by ID. Returns raw MongoDB document or None."""
        return await RoleService._collection().find_one({"_id": role_id})

    @staticmethod
    async def get_role_by_name(name: str) -> dict | None:
        """Find a role by name. Returns raw MongoDB document or None."""
        return await RoleService._collection().find_one({"name": name})

    @staticmethod
    async def get_role_permissions(role_name: str) -> set[str]:
        """Get the permission set for a role.

        Lookup order:
        1. Redis cache
        2. MongoDB
        3. Fallback to DEFAULT_SYSTEM_ROLE_PERMISSIONS (hardcoded)
        """
        cache_key = f"{_CACHE_PREFIX}{role_name}"

        # 1. Redis cache
        try:
            redis = await get_redis_client()
            cached = await redis.get(cache_key)
            if cached is not None:
                return set(json.loads(cached))
        except Exception:
            pass  # Redis unavailable — fall through to DB

        # 2. MongoDB
        try:
            doc = await RoleService.get_role_by_name(role_name)
            if doc is not None:
                perms = set(doc.get("permissions", []))
                # Populate cache
                try:
                    redis = await get_redis_client()
                    await redis.set(cache_key, json.dumps(list(perms)), ex=_CACHE_TTL)
                except Exception:
                    pass
                return perms
        except Exception:
            pass  # MongoDB unavailable — fall through to defaults

        # 3. Fallback to hardcoded defaults
        if role_name in DEFAULT_SYSTEM_ROLE_PERMISSIONS:
            return set(DEFAULT_SYSTEM_ROLE_PERMISSIONS[role_name])

        # Unknown role — no permissions
        logger.warning("unknown_role_fallback", role=role_name)
        return set()

    # ------------------------------------------------------------------
    # Create
    # ------------------------------------------------------------------

    @staticmethod
    async def create_role(data: RoleCreate) -> dict:
        """Create a new custom role.

        Raises:
            ConflictError: If role name already exists.
            ValidationError: If any permission key is invalid.
        """
        # Validate permission keys
        invalid_perms = set(data.permissions) - set(ALL_PERMISSION_KEYS)
        if invalid_perms:
            raise ValidationError(
                code="INVALID_PERMISSIONS",
                message=f"无效的权限点: {', '.join(sorted(invalid_perms))}",
                details={"invalid_permissions": sorted(invalid_perms)},
            )

        # Check name uniqueness
        if await RoleService.get_role_by_name(data.name) is not None:
            raise ConflictError(
                code="ROLE_NAME_EXISTS",
                message=f"角色标识 '{data.name}' 已存在",
            )

        now_iso = utc_now().isoformat()
        from app.models.base import generate_id
        doc = {
            "_id": generate_id("role"),
            "name": data.name,
            "display_name": data.display_name,
            "description": data.description,
            "role_type": RoleType.CUSTOM.value,
            "permissions": data.permissions,
            "created_at": now_iso,
            "updated_at": now_iso,
        }

        try:
            await RoleService._collection().insert_one(doc)
        except Exception as exc:
            from pymongo.errors import DuplicateKeyError
            if isinstance(exc, DuplicateKeyError):
                raise ConflictError(
                    code="ROLE_NAME_EXISTS",
                    message=f"角色标识 '{data.name}' 已存在",
                ) from exc
            raise

        logger.info("custom_role_created", role_name=data.name)
        return doc

    # ------------------------------------------------------------------
    # Update
    # ------------------------------------------------------------------

    @staticmethod
    async def update_role(role_id: str, data: RoleUpdate) -> dict | None:
        """Update a role.

        System roles: only permissions can be modified.
        Custom roles: display_name, description, and permissions can be modified.

        Returns:
            Updated document, or None if not found.

        Raises:
            ValidationError: If business rules are violated.
        """
        col = RoleService._collection()
        doc = await RoleService.get_role_by_id(role_id)
        if doc is None:
            return None

        is_system = doc.get("role_type") == RoleType.SYSTEM.value

        # Build update fields
        set_fields: dict = {"updated_at": utc_now().isoformat()}

        if data.display_name is not None:
            if is_system:
                raise ValidationError(
                    code="SYSTEM_ROLE_IMMUTABLE_NAME",
                    message="系统角色的显示名称不可修改",
                )
            set_fields["display_name"] = data.display_name

        if data.description is not None:
            if is_system:
                raise ValidationError(
                    code="SYSTEM_ROLE_IMMUTABLE_DESC",
                    message="系统角色的描述不可修改",
                )
            set_fields["description"] = data.description

        if data.permissions is not None:
            # Validate permission keys
            invalid_perms = set(data.permissions) - set(ALL_PERMISSION_KEYS)
            if invalid_perms:
                raise ValidationError(
                    code="INVALID_PERMISSIONS",
                    message=f"无效的权限点: {', '.join(sorted(invalid_perms))}",
                    details={"invalid_permissions": sorted(invalid_perms)},
                )
            set_fields["permissions"] = data.permissions

        if len(set_fields) <= 1:
            # Only updated_at — nothing to do
            return doc

        await col.update_one({"_id": role_id}, {"$set": set_fields})

        # Invalidate cache
        await RoleService.invalidate_cache(doc["name"])

        logger.info("role_updated", role_id=role_id, fields=list(set_fields.keys()))
        return await RoleService.get_role_by_id(role_id)

    # ------------------------------------------------------------------
    # Delete
    # ------------------------------------------------------------------

    @staticmethod
    async def delete_role(role_id: str) -> bool:
        """Delete a custom role.

        Raises:
            NotFoundError: If role not found.
            ValidationError: If role is a system role or has assigned users.
        """
        col = RoleService._collection()
        doc = await RoleService.get_role_by_id(role_id)
        if doc is None:
            raise NotFoundError(
                code="ROLE_NOT_FOUND",
                message=f"角色 {role_id} 不存在",
            )

        if doc["role_type"] == RoleType.SYSTEM.value:
            raise ValidationError(
                code="SYSTEM_ROLE_PROTECTED",
                message="系统内置角色不可删除",
            )

        # Check if any users are using this role
        from app.services.user_service import UserService
        user_count = await UserService._collection().count_documents(
            {"role": doc["name"]}
        )
        if user_count > 0:
            raise ValidationError(
                code="ROLE_IN_USE",
                message=f"该角色仍有 {user_count} 个用户在使用，请先更改他们的角色",
            )

        result = await col.delete_one({"_id": role_id})
        if result.deleted_count > 0:
            await RoleService.invalidate_cache(doc["name"])
            logger.info("custom_role_deleted", role_name=doc["name"])
            return True
        return False

    # ------------------------------------------------------------------
    # Cache management
    # ------------------------------------------------------------------

    @staticmethod
    async def invalidate_cache(role_name: str) -> None:
        """Remove the cached permissions for a role from Redis."""
        cache_key = f"{_CACHE_PREFIX}{role_name}"
        try:
            redis = await get_redis_client()
            await redis.delete(cache_key)
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def new_id(prefix: str = "role") -> str:
        """Generate a new role ID."""
        from app.models.base import generate_id
        return generate_id(prefix)
