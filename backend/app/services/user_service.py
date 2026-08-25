"""User business logic — creation, lookup, and admin initialization."""
import re

from loguru import logger

from app.core.errors import ForbiddenError, NotFoundError, ValidationError
from app.core.security import (
    create_access_token,
    create_refresh_token,
    hash_password,
    validate_password_strength,
)
from app.db.mongodb import get_database
from app.models.user import User, UserRole, UserStatus
from app.schemas.auth import AdminCreateResult, TokenResponse

# System role names for quick validation
_SYSTEM_ROLES = {r.value for r in UserRole}


class UserService:
    """Service layer for user operations."""

    # MongoDB collection name
    COLLECTION = "users"

    @staticmethod
    def _collection():
        return get_database()[UserService.COLLECTION]

    @staticmethod
    async def ensure_indexes() -> None:
        """Create unique indexes on username and email."""
        col = UserService._collection()
        await col.create_index("username", unique=True, name="idx_users_username")
        await col.create_index("email", unique=True, name="idx_users_email")
        logger.info("User indexes ensured: idx_users_username, idx_users_email")

    @staticmethod
    async def get_user_by_username(username: str) -> dict | None:
        """Find a user by username. Returns raw MongoDB document or None."""
        doc: dict | None = await UserService._collection().find_one(
            {"username": username}
        )
        return doc

    @staticmethod
    async def get_user_by_email(email: str) -> dict | None:
        """Find a user by email. Returns raw MongoDB document or None."""
        doc: dict | None = await UserService._collection().find_one(
            {"email": email}
        )
        return doc

    @staticmethod
    async def get_user_by_id(user_id: str) -> dict | None:
        """Find a user by id. Returns raw MongoDB document or None."""
        doc: dict | None = await UserService._collection().find_one(
            {"_id": user_id}
        )
        return doc

    @staticmethod
    async def update_last_login(user_id: str) -> None:
        """Set last_login_at to current UTC time."""
        from app.models.base import utc_now

        now_iso = utc_now().isoformat()
        result = await UserService._collection().update_one(
            {"_id": user_id},
            {"$set": {"last_login_at": now_iso, "updated_at": now_iso}},
        )
        if result.modified_count == 0:
            logger.debug("update_last_login: no document matched", user_id=user_id)

    @staticmethod
    async def _admin_exists() -> bool:
        """Check if any admin-role user already exists."""
        return (
            await UserService._collection().find_one({"role": UserRole.ADMIN.value})
            is not None
        )

    @staticmethod
    async def _validate_role_exists(role: str) -> None:
        """Validate that a role name exists (system or custom).

        Raises:
            NotFoundError: If the role does not exist.
        """
        if role in _SYSTEM_ROLES:
            return  # System role always exists

        from app.services.role_service import RoleService
        role_doc = await RoleService.get_role_by_name(role)
        if role_doc is None:
            raise NotFoundError(
                code="ROLE_NOT_FOUND",
                message=f"角色 '{role}' 不存在",
            )

    @staticmethod
    async def create_admin_user(
        username: str, password: str, email: str
    ) -> AdminCreateResult:
        """Create the first admin user via CLI.

        Args:
            username: Unique username.
            password: Plaintext password (validated for strength here).
            email: Unique email address.

        Returns:
            AdminCreateResult with user info and JWT tokens.

        Raises:
            ForbiddenError: If an admin user already exists.
            ValidationError: If username or email is already taken, or password is weak.
        """
        # AC3: Password strength validation (enforced at service layer)
        validate_password_strength(password)

        # AC2: Prevent duplicate admin creation
        if await UserService._admin_exists():
            raise ForbiddenError(
                code="ADMIN_ALREADY_EXISTS",
                message="管理员账户已存在，请使用用户管理界面",
            )

        # AC5: Username and email uniqueness
        if await UserService.get_user_by_username(username) is not None:
            raise ValidationError(
                code="USER_REGISTER_CONFLICT",
                message="用户名已被占用",
                details={"field": "username"},
            )
        if await UserService.get_user_by_email(email) is not None:
            raise ValidationError(
                code="USER_REGISTER_CONFLICT",
                message="邮箱已被注册",
                details={"field": "email"},
            )

        # Create user document
        user = User(
            username=username,
            email=email,
            password_hash=hash_password(password),
            role=UserRole.ADMIN.value,
            status=UserStatus.ACTIVE,
        )

        # Build MongoDB document — use _id to match User.alias and index queries
        doc = {
            "_id": user.id,
            "username": user.username,
            "email": user.email,
            "password_hash": user.password_hash,
            "role": user.role,
            "status": user.status.value,
            "created_at": user.created_at,
            "updated_at": user.updated_at,
            "last_login_at": user.last_login_at,
        }
        try:
            await UserService._collection().insert_one(doc)
        except Exception as exc:
            from pymongo.errors import DuplicateKeyError

            if isinstance(exc, DuplicateKeyError):
                raise ValidationError(
                    code="USER_REGISTER_CONFLICT",
                    message="用户名或邮箱已被占用",
                ) from exc
            raise ValidationError(
                code="USER_CREATE_FAILED",
                message="用户创建失败，请稍后重试",
            ) from exc

        # Log creation (no password info — NFR-S2)
        logger.info(
            "admin_user_created",
            user_id=user.id,
            username=user.username,
        )

        # AC4: Auto-issue JWT tokens
        from app.core.config import settings

        tokens = TokenResponse(
            access_token=create_access_token(
                subject=user.id,
                claims={"role": user.role},
            ),
            refresh_token=create_refresh_token(subject=user.id),
            expires_in=settings.JWT_ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        )

        return AdminCreateResult(
            message=f"管理员账户已创建：username={username}",
            user_id=user.id,
            username=username,
            tokens=tokens,
        )

    # ------------------------------------------------------------------
    # Admin user management (Story 1.4)
    # ------------------------------------------------------------------

    @staticmethod
    async def list_users(
        page: int = 1,
        page_size: int = 20,
        username: str | None = None,
        role: str | None = None,
        status: str | None = None,
        search: str | None = None,
    ) -> tuple[list[dict], int]:
        """List users with pagination and optional filtering. (AC1)

        Args:
            page: Page number (1-based).
            page_size: Items per page (max 100).
            username: Optional username substring filter (case-insensitive).
            role: Optional role filter (string, supports custom roles).
            status: Optional status filter.
            search: Optional substring filter applied to username OR email
                (用户管理页搜索框，匹配任一字段).

        Returns:
            Tuple of (user_docs, total_count). Password hashes are included
            in docs — the API layer must strip them before returning.
        """
        col = UserService._collection()
        filter_query: dict = {}
        if username:
            filter_query["username"] = {"$regex": re.escape(username), "$options": "i"}
        if role:
            filter_query["role"] = role
        if status:
            filter_query["status"] = status
        if search:
            rx = {"$regex": re.escape(search), "$options": "i"}
            filter_query["$or"] = [{"username": rx}, {"email": rx}]

        total = await col.count_documents(filter_query)
        cursor = (
            col.find(filter_query)
            .sort("created_at", -1)
            .skip((page - 1) * page_size)
            .limit(page_size)
        )
        items = await cursor.to_list(length=page_size)
        return items, total

    @staticmethod
    async def create_user_by_admin(
        username: str,
        email: str,
        password: str,
        role: str = "viewer",
    ) -> dict:
        """Create a new user with specified role. (AC2)

        Args:
            username: Unique username.
            email: Unique email address.
            password: Plaintext password (strength-validated here).
            role: Role name to assign (system or custom, default: viewer).

        Returns:
            Created user MongoDB document.

        Raises:
            ValidationError: If username/email exists or password is weak.
            NotFoundError: If the specified role does not exist.
        """
        validate_password_strength(password)

        # Validate role exists
        await UserService._validate_role_exists(role)

        # Check uniqueness
        if await UserService.get_user_by_username(username) is not None:
            raise ValidationError(
                code="USERNAME_CONFLICT",
                message="用户名已被占用",
                details={"field": "username"},
            )
        if await UserService.get_user_by_email(email) is not None:
            raise ValidationError(
                code="EMAIL_CONFLICT",
                message="邮箱已被注册",
                details={"field": "email"},
            )

        user = User(
            username=username,
            email=email,
            password_hash=hash_password(password),
            role=role,
            status=UserStatus.ACTIVE,
        )

        doc = {
            "_id": user.id,
            "username": user.username,
            "email": user.email,
            "password_hash": user.password_hash,
            "role": user.role,
            "status": user.status.value,
            "created_at": user.created_at,
            "updated_at": user.updated_at,
            "last_login_at": user.last_login_at,
        }

        try:
            await UserService._collection().insert_one(doc)
        except Exception as exc:
            from pymongo.errors import DuplicateKeyError

            if isinstance(exc, DuplicateKeyError):
                raise ValidationError(
                    code="USER_CREATE_CONFLICT",
                    message="用户名或邮箱已被占用",
                ) from exc
            raise ValidationError(
                code="USER_CREATE_FAILED",
                message="用户创建失败，请稍后重试",
            ) from exc

        logger.info(
            "admin_user_created",
            target_user=username,
            target_role=role,
        )
        return doc

    @staticmethod
    async def update_user(
        user_id: str,
        updates: dict,
        current_user_id: str,
    ) -> dict | None:
        """Partially update a user's role and/or status. (AC3)

        Args:
            user_id: Target user's ID.
            updates: Dict with optional keys "role" and/or "status".
            current_user_id: The admin performing the update.

        Returns:
            Updated user document, or None if not found.

        Raises:
            ValidationError: If business rules are violated.
        """
        col = UserService._collection()

        # Fetch target user
        target_doc = await UserService.get_user_by_id(user_id)
        if target_doc is None:
            return None

        # Business rule: permission suicide protection
        if user_id == current_user_id and "role" in updates:
            new_role = updates["role"]
            if new_role != UserRole.ADMIN.value:
                raise ValidationError(
                    code="SELF_DEMOTE_FORBIDDEN",
                    message="不能将自己的角色从管理员降级",
                )

        # Business rule: last admin protection
        if "role" in updates or "status" in updates:
            new_role = updates.get("role", target_doc.get("role"))
            new_status = updates.get("status", target_doc.get("status"))

            is_target_admin = target_doc.get("role") == UserRole.ADMIN.value
            is_demoting = new_role != UserRole.ADMIN.value
            is_disabling = new_status == UserStatus.DISABLED.value

            if is_target_admin and (is_demoting or is_disabling):
                admin_count = await col.count_documents(
                    {"role": UserRole.ADMIN.value}
                )
                if admin_count <= 1:
                    raise ValidationError(
                        code="LAST_ADMIN_PROTECTED",
                        message="不能降级或禁用最后一位管理员",
                    )

        # Validate new role exists if being changed
        if "role" in updates:
            await UserService._validate_role_exists(updates["role"])

        from app.models.base import utc_now

        now_iso = utc_now().isoformat()
        set_fields: dict = {"updated_at": now_iso}
        if "role" in updates:
            set_fields["role"] = updates["role"]
        if "status" in updates:
            status_val = updates["status"]
            set_fields["status"] = status_val.value if isinstance(status_val, UserStatus) else status_val

        await col.update_one({"_id": user_id}, {"$set": set_fields})

        logger.info(
            "admin_user_updated",
            target_user_id=user_id,
            changes={k: set_fields.get(k) for k in ("role", "status") if k in set_fields},
        )

        # Return updated document
        updated = await UserService.get_user_by_id(user_id)
        return updated

    @staticmethod
    async def delete_user(user_id: str, current_user_id: str) -> bool:
        """Delete a user. (AC4)

        Args:
            user_id: Target user's ID.
            current_user_id: The admin performing the delete.

        Returns:
            True if deleted, False if not found.

        Raises:
            ValidationError: If trying to delete self or last admin.
        """
        # Cannot delete self
        if user_id == current_user_id:
            raise ValidationError(
                code="SELF_DELETE_FORBIDDEN",
                message="不能删除自己的账户",
            )

        col = UserService._collection()

        # Check user exists and last admin protection
        target_doc = await UserService.get_user_by_id(user_id)
        if target_doc is None:
            return False

        if target_doc.get("role") == UserRole.ADMIN.value:
            admin_count = await col.count_documents(
                {"role": UserRole.ADMIN.value}
            )
            if admin_count <= 1:
                raise ValidationError(
                    code="LAST_ADMIN_PROTECTED",
                    message="不能删除最后一位管理员",
                )

        result = await col.delete_one({"_id": user_id})
        if result.deleted_count > 0:
            db = get_database()

            # 1. Sessions + Messages（复用 SessionService 逐个删除，含 workspace 清理）
            try:
                from app.services.session_service import SessionService

                async for sess in db["sessions"].find({"user_id": user_id}, {"_id": 1}):
                    await SessionService.delete_session(sess["_id"])
            except Exception as exc:
                logger.warning("user_sessions_cleanup_partial", user_id=user_id, error=str(exc))

            # 2. Tasks
            try:
                await db["tasks"].delete_many({"created_by": user_id})
            except Exception as exc:
                logger.warning("user_tasks_cleanup_partial", user_id=user_id, error=str(exc))

            # 3. Workflows + workflow_registry
            try:
                wf_ids = [
                    wf["_id"]
                    async for wf in db["workflows"].find({"created_by": user_id}, {"_id": 1})
                ]
                if wf_ids:
                    await db["workflows"].delete_many({"_id": {"$in": wf_ids}})
                    await db["workflow_registry"].delete_many(
                        {"workflow_id": {"$in": wf_ids}}
                    )
            except Exception as exc:
                logger.warning("user_workflows_cleanup_partial", user_id=user_id, error=str(exc))

            # 4. Triggers
            try:
                await db["triggers"].delete_many({"user_id": user_id})
            except Exception as exc:
                logger.warning("user_triggers_cleanup_partial", user_id=user_id, error=str(exc))

            # 5. Files（DB 记录 + 物理文件）
            try:
                from app.services.file_storage import LocalFileStorage

                storage = LocalFileStorage()
                async for f in db["file_refs"].find(
                    {"owner_user_id": user_id}, {"storage_key": 1}
                ):
                    await storage.delete(f.get("storage_key", ""))
                await db["file_refs"].delete_many({"owner_user_id": user_id})
                await db["file_usages"].delete_many({"owner_user_id": user_id})
            except Exception as exc:
                logger.warning("user_files_cleanup_partial", user_id=user_id, error=str(exc))

            # 6. Webhooks + delivery logs（webhook 通过 api_key_id 关联用户）
            try:
                api_key_ids = [
                    ak["_id"]
                    async for ak in db["api_keys"].find(
                        {"owner_user_id": user_id}, {"_id": 1}
                    )
                ]
                if api_key_ids:
                    webhook_ids = [
                        wh["_id"]
                        async for wh in db["webhooks"].find(
                            {"api_key_id": {"$in": api_key_ids}}, {"_id": 1}
                        )
                    ]
                    if webhook_ids:
                        await db["webhook_delivery_logs"].delete_many(
                            {"webhook_id": {"$in": webhook_ids}}
                        )
                        await db["webhooks"].delete_many({"_id": {"$in": webhook_ids}})
            except Exception as exc:
                logger.warning("user_webhooks_cleanup_partial", user_id=user_id, error=str(exc))

            # 7. API Keys
            try:
                await db["api_keys"].delete_many({"owner_user_id": user_id})
            except Exception as exc:
                logger.warning("user_api_keys_cleanup_partial", user_id=user_id, error=str(exc))

            # 8. Credentials
            try:
                await db["credentials"].delete_many({"user_id": user_id})
            except Exception as exc:
                logger.warning("user_credentials_cleanup_partial", user_id=user_id, error=str(exc))

            # 9. Workspace 物理目录（最后删，确保前面的文件操作已完成）
            try:
                from app.engine.tool.workspace import WorkspaceManager

                WorkspaceManager.delete_user_workspace(user_id)
            except Exception as exc:
                logger.warning("user_workspace_cleanup_partial", user_id=user_id, error=str(exc))

            logger.info(
                "admin_user_deleted",
                target_user_id=user_id,
            )
            return True
        return False

    @staticmethod
    async def reset_password(
        user_id: str,
        new_password: str,
    ) -> bool:
        """Reset a user's password. (AC5)

        Args:
            user_id: Target user's ID.
            new_password: New plaintext password (strength-validated here).

        Returns:
            True if password was reset, False if user not found.

        Raises:
            ValidationError: If password is weak.
        """
        validate_password_strength(new_password)

        target_doc = await UserService.get_user_by_id(user_id)
        if target_doc is None:
            return False

        from app.models.base import utc_now

        hashed = hash_password(new_password)
        now_iso = utc_now().isoformat()
        await UserService._collection().update_one(
            {"_id": user_id},
            {"$set": {"password_hash": hashed, "updated_at": now_iso}},
        )

        logger.info(
            "admin_password_reset",
            target_user_id=user_id,
        )
        return True
