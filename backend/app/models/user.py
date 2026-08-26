"""User data model for MongoDB."""
from enum import StrEnum

from pydantic import BaseModel, Field

from app.models.base import generate_id, utc_now


class UserRole(StrEnum):
    """System role enum — the 4 built-in roles.

    The `role` field on User accepts any string to support custom roles,
    but these enum values are reserved for system roles.
    """

    ADMIN = "admin"
    DEVELOPER = "developer"
    OPERATOR = "operator"
    VIEWER = "viewer"


class UserStatus(StrEnum):
    """User account status."""

    ACTIVE = "active"
    DISABLED = "disabled"


class User(BaseModel):
    """MongoDB user document model.

    IMPORTANT: `password_hash` must never be serialized to API responses.
    Use UserResponse for API output and UserInDB for internal handling.

    NOTE: `role` is `str` (not UserRole enum) to support custom roles.
    System roles use the names defined in UserRole.
    """

    id: str = Field(default_factory=lambda: generate_id("user"), alias="_id")
    username: str = Field(..., min_length=1, max_length=50)
    email: str = Field(..., max_length=255, pattern=r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$")
    password_hash: str = Field(..., exclude=True)  # Never serialize
    role: str = Field(default=UserRole.VIEWER.value)
    status: UserStatus = Field(default=UserStatus.ACTIVE)
    # 超级管理员：初始 create-admin 的账户自动获得；普通管理员不可对
    # 其他管理员执行管理写操作（锁定/删除/改角色/重置密码），仅超管可以。
    is_super_admin: bool = Field(default=False)
    created_at: str = Field(default_factory=lambda: utc_now().isoformat())
    updated_at: str = Field(default_factory=lambda: utc_now().isoformat())
    last_login_at: str | None = Field(default=None)

    model_config = {"populate_by_name": True}
