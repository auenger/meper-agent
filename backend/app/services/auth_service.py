"""Authentication service — login, refresh, account lockout, password change, logout.

All DB / Redis operations are async (motor + redis.asyncio).
"""
import hashlib
from datetime import UTC, datetime, timedelta

from loguru import logger

from app.core.errors import UnauthorizedError, ValidationError
from app.core.security import (
    create_access_token,
    create_refresh_token,
    decode_token,
    get_role_permissions,
    hash_password,
    validate_password_strength,
    verify_password,
)
from app.db.redis import get_redis_client
from app.models.user import UserRole, UserStatus
from app.schemas.auth import TokenResponse, UserInfo
from app.services.user_service import UserService

# Account lockout configuration
MAX_FAILED_ATTEMPTS = 5
LOCKOUT_TTL_SECONDS = 900  # 15 minutes


class AuthService:
    """Service layer for authentication operations."""

    # ------------------------------------------------------------------
    # Account lockout helpers (Redis-backed)
    # ------------------------------------------------------------------

    @staticmethod
    def _failed_key(username: str) -> str:
        return f"auth:failed:{username}"

    @staticmethod
    def _locked_key(username: str) -> str:
        return f"auth:locked:{username}"

    @staticmethod
    async def is_account_locked(username: str) -> bool:
        """Check if account is currently locked."""
        redis = await get_redis_client()
        locked = await redis.get(AuthService._locked_key(username))
        return locked is not None

    @staticmethod
    async def get_remaining_lock_time(username: str) -> int:
        """Return remaining lockout seconds (0 if not locked)."""
        redis = await get_redis_client()
        ttl = await redis.ttl(AuthService._locked_key(username))
        return max(0, ttl)

    @staticmethod
    async def record_failed_login(username: str) -> None:
        """Increment failure counter and lock if threshold reached."""
        redis = await get_redis_client()
        count = await redis.incr(AuthService._failed_key(username))
        await redis.expire(AuthService._failed_key(username), LOCKOUT_TTL_SECONDS)

        if count >= MAX_FAILED_ATTEMPTS:
            await redis.set(
                AuthService._locked_key(username),
                "1",
                ex=LOCKOUT_TTL_SECONDS,
            )
            logger.warning(
                "account_locked",
                username=username,
                failed_attempts=count,
                ttl=LOCKOUT_TTL_SECONDS,
            )

    @staticmethod
    async def reset_failed_login(username: str) -> None:
        """Clear failure counter after successful login."""
        redis = await get_redis_client()
        await redis.delete(AuthService._failed_key(username))

    # ------------------------------------------------------------------
    # Login / Refresh
    # ------------------------------------------------------------------

    @staticmethod
    async def login(username: str, password: str) -> TokenResponse:
        """Authenticate user with credentials and return JWT tokens.

        Raises:
            UnauthorizedError: ACCOUNT_LOCKED / INVALID_CREDENTIALS
        """
        # AC2: Check lockout BEFORE any DB lookup
        if await AuthService.is_account_locked(username):
            remaining = await AuthService.get_remaining_lock_time(username)
            logger.info("login_blocked_locked", username=username, remaining=remaining)
            raise UnauthorizedError(
                code="ACCOUNT_LOCKED",
                message="账户已锁定，请 15 分钟后重试",
            )

        user_doc = await UserService.get_user_by_username(username)

        # AC5: Don't reveal whether username exists
        if user_doc is None:
            logger.info("login_failed_unknown_user", username=username)
            raise UnauthorizedError(
                code="INVALID_CREDENTIALS",
                message="用户名或密码错误",
            )

        # Verify password
        if not verify_password(password, user_doc["password_hash"]):
            # Admin 账户豁免失败锁定（防止爆破触发锁定把管理入口完全堵死）。
            # admin 永远不会写入 locked key，查库前的 is_account_locked 检查
            # 对其天然无影响；历史遗留的锁 key 由 TTL 自动过期。
            is_admin = (
                user_doc.get("role") == UserRole.ADMIN.value
                or bool(user_doc.get("is_super_admin"))
            )
            if is_admin:
                logger.info(
                    "login_failed_bad_password_admin_no_lockout",
                    username=username,
                )
            else:
                await AuthService.record_failed_login(username)
                logger.info(
                    "login_failed_bad_password",
                    username=username,
                )
            raise UnauthorizedError(
                code="INVALID_CREDENTIALS",
                message="用户名或密码错误",
            )

        # Reject disabled accounts AFTER password verification: a correct
        # password proves identity, while checking earlier would leak account
        # existence to unauthenticated callers. Without this, a disabled user
        # could "log in" successfully and then hit silent 401s on every API.
        if user_doc.get("status") != UserStatus.ACTIVE.value:
            logger.info(
                "login_blocked_disabled",
                username=username,
            )
            raise UnauthorizedError(
                code="ACCOUNT_DISABLED",
                message="账户已被停用，请联系管理员",
            )

        # Success — reset failure counter
        await AuthService.reset_failed_login(username)

        # AC1: Update last_login_at
        user_id = user_doc["_id"]
        await UserService.update_last_login(user_id)

        # Sign tokens
        role = user_doc.get("role", UserRole.VIEWER.value)
        tokens = await AuthService._issue_tokens(user_id, role)

        # Resolve permissions and include user info
        perms = await get_role_permissions(role)
        tokens.user = UserInfo(
            id=user_id,
            username=user_doc.get("username", ""),
            role=role,
            permissions=sorted(perms),
            is_super_admin=bool(user_doc.get("is_super_admin", False)),
        )

        logger.info("login_success", username=username, user_id=user_id)
        return tokens

    @staticmethod
    async def refresh_token(refresh_token: str) -> TokenResponse:
        """Issue a new access_token from a valid refresh_token.

        AC4: refresh_token preserved unless near expiry (< 1 day).
        """
        payload = decode_token(refresh_token)

        # AC3: Check token blacklist before any further processing
        if await AuthService._is_token_revoked(refresh_token):
            raise UnauthorizedError(
                code="TOKEN_REVOKED",
                message="Token has been revoked",
            )

        if payload.get("type") != "refresh":
            raise UnauthorizedError(
                code="TOKEN_INVALID",
                message="Wrong token type — refresh token required",
            )

        user_id = payload.get("sub", "")
        if not user_id:
            raise UnauthorizedError(
                code="TOKEN_INVALID",
                message="Token missing subject claim",
            )
        user_doc = await UserService.get_user_by_id(user_id)

        if user_doc is None:
            raise UnauthorizedError(
                code="USER_NOT_FOUND",
                message="User not found",
            )

        if user_doc.get("status") != UserStatus.ACTIVE.value:
            raise UnauthorizedError(
                code="ACCOUNT_DISABLED",
                message="Account is disabled",
            )

        role = user_doc.get("role", UserRole.VIEWER.value)

        # AC4: Rolling refresh — if TTL < 1 day, issue new refresh_token
        exp = payload.get("exp", 0)
        now = datetime.now(UTC).timestamp()
        remaining = exp - now

        new_access = create_access_token(subject=user_id, claims={"role": role})

        if remaining < timedelta(days=1).total_seconds():
            new_refresh = create_refresh_token(subject=user_id)
        else:
            new_refresh = refresh_token

        # Include user info with permissions
        perms = await get_role_permissions(role)
        user_info = UserInfo(
            id=user_id,
            username=user_doc.get("username", ""),
            role=role,
            permissions=sorted(perms),
            is_super_admin=bool(user_doc.get("is_super_admin", False)),
        )

        logger.info("token_refreshed", user_id=user_id)
        return TokenResponse(
            access_token=new_access,
            refresh_token=new_refresh,
            user=user_info,
        )

    # ------------------------------------------------------------------
    # Token helper
    # ------------------------------------------------------------------

    @staticmethod
    async def _issue_tokens(user_id: str, role: str) -> TokenResponse:
        """Build a fresh access + refresh token pair."""
        return TokenResponse(
            access_token=create_access_token(
                subject=user_id, claims={"role": role}
            ),
            refresh_token=create_refresh_token(subject=user_id),
        )

    # ------------------------------------------------------------------
    # Token blacklist (Story 1.5)
    # ------------------------------------------------------------------

    @staticmethod
    def _token_hash(token: str) -> str:
        """Return first 16 hex chars of SHA256(token)."""
        return hashlib.sha256(token.encode()).hexdigest()[:16]

    @staticmethod
    def _revoked_key(token_hash: str) -> str:
        return f"auth:revoked:{token_hash}"

    @staticmethod
    def _user_revoked_key(user_id: str) -> str:
        return f"auth:revoked:user:{user_id}"

    @staticmethod
    async def _invalidate_refresh_token(token: str) -> None:
        """Add a single refresh_token to the Redis blacklist.

        TTL = remaining lifetime of the token (seconds). Uses the
        first 16 hex chars of SHA256(token) as the key to avoid
        storing the raw token.
        """
        try:
            payload = decode_token(token)
        except UnauthorizedError:
            # Invalid / expired token — nothing to blacklist
            return

        token_hash = AuthService._token_hash(token)
        user_id = payload.get("sub", "")
        exp = payload.get("exp", 0)
        now = datetime.now(UTC).timestamp()
        remaining = max(0, int(exp - now))

        if remaining <= 0:
            return  # Already expired, no need to blacklist

        redis = await get_redis_client()
        await redis.set(
            AuthService._revoked_key(token_hash),
            user_id,
            ex=remaining,
        )

    @staticmethod
    async def _invalidate_all_refresh_tokens(user_id: str) -> None:
        """Invalidate ALL refresh tokens for a user (e.g. after password change).

        Uses a "global revocation timestamp" approach: set a key with
        the current timestamp. During token validation, if this key's
        timestamp is after the token's iat, the token is considered revoked.
        """
        from app.core.config import settings

        now_ts = str(datetime.now(UTC).timestamp())
        redis = await get_redis_client()
        await redis.set(
            AuthService._user_revoked_key(user_id),
            now_ts,
            ex=settings.JWT_REFRESH_TOKEN_EXPIRE_DAYS * 86400,
        )

    @staticmethod
    async def _is_token_revoked(token: str) -> bool:
        """Check whether a refresh_token has been revoked.

        Two-layer check:
        1. Single-token revocation (logout): ``auth:revoked:{hash}``
        2. User-level revocation (password change): ``auth:revoked:user:{user_id}``
           — if the user-level key's timestamp > token's iat, token is revoked.
        """
        try:
            payload = decode_token(token)
        except UnauthorizedError:
            return True  # If token can't be decoded, treat as revoked

        token_hash = AuthService._token_hash(token)
        user_id = payload.get("sub", "")
        token_iat = payload.get("iat", 0)

        redis = await get_redis_client()

        # Check 1: single-token revocation
        single_revoked = await redis.get(AuthService._revoked_key(token_hash))
        if single_revoked is not None:
            return True

        # Check 2: user-level revocation
        user_revoked = await redis.get(AuthService._user_revoked_key(user_id))
        if user_revoked is not None:
            revoked_at = float(user_revoked)
            if revoked_at > token_iat:
                return True

        return False

    # ------------------------------------------------------------------
    # Password change (Story 1.5 — AC1)
    # ------------------------------------------------------------------

    @staticmethod
    async def change_password(user_id: str, current_password: str, new_password: str) -> None:
        """Change the current user's password. (AC1)

        Validates current password, checks new password strength,
        ensures they are different, then updates in MongoDB and
        invalidates all existing refresh tokens.

        Raises:
            UnauthorizedError: CURRENT_PASSWORD_MISMATCH — current password is wrong.
            ValidationError: PASSWORD_TOO_SHORT / PASSWORD_MISSING_COMPLEXITY /
                PASSWORD_SAME_AS_CURRENT — new password validation failure.
        """
        # Fetch user
        user_doc = await UserService.get_user_by_id(user_id)
        if user_doc is None:
            raise UnauthorizedError(
                code="USER_NOT_FOUND",
                message="用户不存在",
            )

        # Validate current password
        if not verify_password(current_password, user_doc["password_hash"]):
            logger.info("password_change_failed_wrong_current", user_id=user_id)
            raise UnauthorizedError(
                code="CURRENT_PASSWORD_MISMATCH",
                message="当前密码错误",
            )

        # Validate new password strength
        validate_password_strength(new_password)

        # Ensure new password is different from current
        if current_password == new_password:
            raise ValidationError(
                code="PASSWORD_SAME_AS_CURRENT",
                message="新密码不能与当前密码相同",
            )

        # Hash and update
        from app.models.base import utc_now

        hashed = hash_password(new_password)
        now_iso = utc_now().isoformat()
        await UserService._collection().update_one(
            {"_id": user_id},
            {"$set": {"password_hash": hashed, "updated_at": now_iso}},
        )

        # Invalidate all existing refresh tokens
        await AuthService._invalidate_all_refresh_tokens(user_id)

        # Audit log (no password/token content — AC4)
        logger.info("password_changed", user_id=user_id)

    # ------------------------------------------------------------------
    # Logout (Story 1.5 — AC2)
    # ------------------------------------------------------------------

    @staticmethod
    async def logout(refresh_token: str) -> None:
        """Logout by revoking the refresh_token. (AC2)

        Idempotent — invalid/expired tokens are silently accepted.
        """
        token_hash = AuthService._token_hash(refresh_token)

        # Try to decode — if invalid, still return success (idempotent)
        try:
            payload = decode_token(refresh_token)
            user_id = payload.get("sub", "unknown")
        except UnauthorizedError:
            user_id = "unknown"

        await AuthService._invalidate_refresh_token(refresh_token)

        # Audit log — only token_hash prefix (AC4)
        logger.info(
            "user_logout",
            user_id=user_id,
            token_hash_prefix=token_hash[:8],
        )
