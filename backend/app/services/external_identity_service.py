"""External identity service — sub → platform_user_id mapping operations."""
from __future__ import annotations

from loguru import logger

from app.db.mongodb import get_database
from app.models.base import generate_id, utc_now


class ExternalIdentityService:
    """Service layer for external identity mapping operations."""

    COLLECTION = "external_identities"

    @staticmethod
    def _collection():
        return get_database()[ExternalIdentityService.COLLECTION]

    @staticmethod
    async def find_by_sub(sub: str) -> dict | None:
        """Find a platform_user_id by sub (runtime reverse lookup).

        Returns:
            The identity document, or None if not found.
        """
        if not sub:
            return None
        return await ExternalIdentityService._collection().find_one({"sub": sub})

    @staticmethod
    async def upsert(sub: str, platform_user_id: str) -> dict:
        """Create the sub → platform_user_id mapping if it doesn't exist.

        If the sub already exists (even for a different platform_user_id),
        the existing record is kept unchanged — a sub maps to exactly one
        platform user.

        Returns:
            The identity document (existing or newly created).
        """
        col = ExternalIdentityService._collection()
        existing = await col.find_one({"sub": sub})
        if existing is not None:
            if existing["platform_user_id"] != platform_user_id:
                # 该外部身份已被其他平台用户绑定——不允许抢注。
                # 否则会出现"绑定时静默失败"：凭证存了但身份映射指向别人，
                # 运行时查到的是别人的身份（session/凭证张冠李戴）。
                from app.core.errors import ConflictError

                raise ConflictError(
                    code="EXTERNAL_IDENTITY_ALREADY_BOUND",
                    message=(
                        f"该外部身份({sub})已绑定到其他平台账号，"
                        "不能重复绑定。如需转移请先由对方解绑。"
                    ),
                )
            return existing

        now_iso = utc_now().isoformat()
        doc = {
            "_id": generate_id("extid"),
            "sub": sub,
            "platform_user_id": platform_user_id,
            "created_at": now_iso,
            "updated_at": now_iso,
        }
        await col.insert_one(doc)
        logger.info(
            "external_identity_created",
            sub=sub,
            platform_user_id=platform_user_id,
        )
        return doc

    @staticmethod
    async def delete_by_sub_and_user(sub: str, platform_user_id: str) -> bool:
        """Delete a mapping by (sub, platform_user_id).

        Used when a user unbinds a group — removes the identity mapping
        that was created during binding. Only deletes if both sub AND
        platform_user_id match (safety: don't delete other users' mappings).

        Returns:
            True if deleted, False if not found.
        """
        result = await ExternalIdentityService._collection().delete_one(
            {"sub": sub, "platform_user_id": platform_user_id}
        )
        if result.deleted_count > 0:
            logger.info(
                "external_identity_deleted",
                sub=sub,
                platform_user_id=platform_user_id,
            )
            return True
        return False

    @staticmethod
    async def list_by_platform_user(platform_user_id: str) -> list[dict]:
        """List all identity mappings for a platform user."""
        cursor = ExternalIdentityService._collection().find(
            {"platform_user_id": platform_user_id}
        )
        return await cursor.to_list(length=None)
