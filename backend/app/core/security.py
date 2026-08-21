"""Security utilities — password hashing, JWT creation/validation, password strength.

Implements Decision 2.1 (JWT access + refresh) and Decision 2.4 (bcrypt).
"""
from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

import bcrypt
import jwt
from fastapi import Depends, Header

from app.core.config import settings
from app.core.errors import ForbiddenError, UnauthorizedError, ValidationError

if TYPE_CHECKING:
    from app.schemas.user import UserResponse

# ---------------------------------------------------------------------------
# Password hashing (Decision 2.4: bcrypt via the bcrypt library directly)
# ---------------------------------------------------------------------------


def hash_password(plain: str) -> str:
    """Hash a plaintext password using bcrypt.

    Raises:
        ValidationError: If password exceeds 72 bytes (bcrypt truncation limit).
    """
    if len(plain.encode("utf-8")) > 72:
        raise ValidationError(
            code="PASSWORD_TOO_LONG",
            message="密码长度超过 72 字节，请使用更短的密码",
        )
    return bcrypt.hashpw(plain.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    """Verify a plaintext password against a bcrypt hash."""
    return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))


# ---------------------------------------------------------------------------
# Password strength validation
# ---------------------------------------------------------------------------
_PASSWORD_MIN_LENGTH = 8
_PASSWORD_PATTERN = re.compile(r"^(?=.*[A-Za-z])(?=.*\d).+$")


def validate_password_strength(password: str) -> None:
    """Raise ValidationError if password does not meet strength requirements.

    Requirements: >= 8 chars, contains at least one letter AND one digit.
    """
    if len(password) < _PASSWORD_MIN_LENGTH:
        raise ValidationError(
            code="PASSWORD_TOO_SHORT",
            message=f"密码至少 {_PASSWORD_MIN_LENGTH} 字符且必须包含字母和数字",
        )
    if not _PASSWORD_PATTERN.match(password):
        raise ValidationError(
            code="PASSWORD_MISSING_COMPLEXITY",
            message=f"密码至少 {_PASSWORD_MIN_LENGTH} 字符且必须包含字母和数字",
        )


# ---------------------------------------------------------------------------
# JWT helpers (Decision 2.1: access + refresh)
# ---------------------------------------------------------------------------


def create_access_token(
    subject: str,
    claims: dict[str, Any] | None = None,
) -> str:
    """Create a short-lived JWT access token.

    Args:
        subject: The user_id (e.g. "user_01HXYZ...").
        claims: Optional extra claims (e.g. {"role": "admin"}).

    Returns:
        Encoded JWT string.
    """
    now = datetime.now(UTC)
    payload: dict[str, Any] = {
        "sub": subject,
        "type": "access",
        "iat": now,
        "exp": now + timedelta(minutes=settings.JWT_ACCESS_TOKEN_EXPIRE_MINUTES),
    }
    if claims:
        # Filter out reserved claims to prevent override
        for k, v in claims.items():
            if k not in ("sub", "type", "iat", "exp"):
                payload[k] = v
    return jwt.encode(payload, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)


def create_refresh_token(subject: str) -> str:
    """Create a long-lived JWT refresh token.

    Args:
        subject: The user_id.

    Returns:
        Encoded JWT string.
    """
    now = datetime.now(UTC)
    payload: dict[str, Any] = {
        "sub": subject,
        "type": "refresh",
        "iat": now,
        "exp": now + timedelta(days=settings.JWT_REFRESH_TOKEN_EXPIRE_DAYS),
    }
    return jwt.encode(payload, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)


def decode_token(token: str) -> dict[str, Any]:
    """Decode and validate a JWT.

    Raises:
        AppError(401): If the token is expired, invalid, or malformed.
    """

    try:
        return jwt.decode(
            token,
            settings.JWT_SECRET_KEY,
            algorithms=[settings.JWT_ALGORITHM],
        )
    except jwt.ExpiredSignatureError as exc:
        raise UnauthorizedError(
            code="TOKEN_EXPIRED",
            message="Token has expired",
        ) from exc
    except jwt.InvalidTokenError as exc:
        raise UnauthorizedError(
            code="TOKEN_INVALID",
            message="Invalid token",
        ) from exc


def decode_access_token(token: str) -> dict[str, Any] | None:
    """Decode and validate a JWT access token. Returns payload dict or None.

    Unlike decode_token(), this does NOT raise — it returns None on any failure.
    Designed for WebSocket auth where exceptions are awkward.
    """
    try:
        payload = jwt.decode(
            token,
            settings.JWT_SECRET_KEY,
            algorithms=[settings.JWT_ALGORITHM],
        )
        if payload.get("type") != "access":
            return None
        return payload
    except Exception:
        return None


# ---------------------------------------------------------------------------
# FastAPI Depends — JWT authentication (Decision 2.1 + 2.3)
# ---------------------------------------------------------------------------


async def get_current_user(
    authorization: str = Header(None, description="Bearer token"),
) -> UserResponse:
    """FastAPI dependency: parse Bearer token and return the authenticated user.

    Raises:
        UnauthorizedError: Missing header, invalid/expired token, wrong type,
            user not found, or account disabled.
    """
    from app.models.user import UserStatus
    from app.schemas.user import UserResponse
    from app.services.user_service import UserService

    if not authorization or not authorization.startswith("Bearer "):
        raise UnauthorizedError(
            code="TOKEN_INVALID",
            message="Missing or malformed Authorization header",
        )

    token = authorization.removeprefix("Bearer ").strip()
    payload = decode_token(token)  # may raise UnauthorizedError

    if payload.get("type") != "access":
        raise UnauthorizedError(
            code="TOKEN_INVALID",
            message="Wrong token type — access token required",
        )

    user_id = payload.get("sub")
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

    # Resolve permissions for this user's role (Redis-cached)
    role_name = user_doc.get("role", "")
    perms = await get_role_permissions(role_name)

    return UserResponse(
        id=user_doc.get("_id", ""),
        username=user_doc.get("username", ""),
        email=user_doc.get("email", ""),
        role=role_name,
        status=user_doc.get("status", ""),
        created_at=user_doc.get("created_at", ""),
        updated_at=user_doc.get("updated_at", ""),
        last_login_at=user_doc.get("last_login_at"),
        permissions=sorted(perms),
    )


async def get_current_user_optional(
    authorization: str | None = None,
) -> UserResponse | None:
    """Optional version of get_current_user — returns None instead of raising."""
    if not authorization:
        return None

    try:
        return await get_current_user(authorization)
    except UnauthorizedError:
        return None


# ---------------------------------------------------------------------------
# RBAC — Role-based access control (Decision 2.3: hand-written Depends)
# ---------------------------------------------------------------------------

# Permission matrix: maps permission keys to allowed roles (fallback defaults)
DEFAULT_ROLE_PERMISSIONS: dict[str, set[str]] = {
    "user:read": {"admin"},
    "user:write": {"admin"},
    "agent:read": {"admin", "developer", "operator", "viewer"},
    "agent:write": {"admin", "developer"},
    "agent:invoke": {"admin", "developer", "operator"},
    "workflow:read": {"admin", "developer"},
    "workflow:write": {"admin", "developer"},
    "tool:read": {"admin", "developer"},
    "tool:write": {"admin", "developer"},
    "application:read": {"admin", "developer"},
    "application:write": {"admin", "developer"},
    "knowledge:read": {"admin", "developer", "operator", "viewer"},
    "knowledge:write": {"admin", "developer"},
    "execution:read:all": {"admin"},
    "execution:read:own": {"admin", "developer", "operator", "viewer"},
    "apikey:manage": {"admin"},
    "settings:manage": {"admin"},
    "model:read": {"admin", "developer", "operator", "viewer"},
    "model:write": {"admin"},
}

# Backward-compatible alias
ROLE_PERMISSIONS = DEFAULT_ROLE_PERMISSIONS


def has_permission(user_role: str, permission: str) -> bool:
    """Check if a role has a specific permission using the static fallback matrix.

    Args:
        user_role: The user's role string (e.g. "admin", "developer").
        permission: The permission key to check (e.g. "user:read").

    Returns:
        True if the role is allowed, False otherwise.
    """
    allowed = DEFAULT_ROLE_PERMISSIONS.get(permission, set())
    return user_role in allowed


async def get_role_permissions(role_name: str) -> set[str]:
    """Dynamically resolve a role's permission set.

    Lookup order: Redis cache → MongoDB → hardcoded defaults.
    """
    from app.services.role_service import RoleService
    return await RoleService.get_role_permissions(role_name)


def require_permission(perm: str):
    """Factory: return a FastAPI Depends that checks for a specific permission.

    Usage:
        @router.get("/agents")
        async def list_agents(
            _: UserResponse = Depends(require_permission("agent:read")),
        ): ...
    """

    async def _check(
        current_user: UserResponse = Depends(get_current_user),
    ) -> UserResponse:
        perms = await get_role_permissions(current_user.role)
        if perm not in perms:
            raise ForbiddenError(
                code="FORBIDDEN",
                message=f"权限不足，需要 {perm} 权限",
            )
        return current_user

    return _check


def require_role(required_role):
    """Factory: return a FastAPI Depends that checks for an exact role match.

    Usage:
        @router.get("/users")
        async def list_users(
            _: UserResponse = Depends(require_role(UserRole.ADMIN)),
        ): ...
    """
    from app.models.user import UserRole

    required = UserRole(required_role) if isinstance(required_role, str) else required_role

    def _verify(current_user: UserResponse) -> UserResponse:
        if current_user.role != required:
            raise ForbiddenError(
                code="FORBIDDEN",
                message=f"权限不足，需要 {required.value} 角色",
            )
        return current_user

    async def _check(
        current_user: UserResponse = Depends(get_current_user),
    ) -> UserResponse:
        return _verify(current_user)

    return _check


def require_any_role(*roles):
    """Factory: return a FastAPI Depends that accepts any of the given roles.

    Usage:
        @router.get("/agents")
        async def list_agents(
            _: UserResponse = Depends(require_any_role("admin", "developer")),
        ): ...
    """
    from app.models.user import UserRole

    allowed = {UserRole(r) if isinstance(r, str) else r for r in roles}

    def _verify(current_user: UserResponse) -> UserResponse:
        if current_user.role not in allowed:
            role_names = ", ".join(r.value for r in allowed)
            raise ForbiddenError(
                code="FORBIDDEN",
                message=f"权限不足，需要以下角色之一：{role_names}",
            )
        return current_user

    async def _check(
        current_user: UserResponse = Depends(get_current_user),
    ) -> UserResponse:
        return _verify(current_user)

    return _check
