"""User-related Pydantic schemas for API request/response."""
from __future__ import annotations

from pydantic import BaseModel, Field

from app.models.user import UserStatus


class UserCreate(BaseModel):
    """Schema for creating a new user (admin-init or registration)."""

    username: str = Field(..., min_length=1, max_length=50, description="Unique username")
    email: str = Field(..., max_length=255, pattern=r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$", description="Unique email address")
    password: str = Field(..., min_length=8, max_length=128, description="Plaintext password")
    role: str = Field(default="viewer", description="User role (system or custom role name)")


class UserResponse(BaseModel):
    """User data returned in API responses — never includes password_hash."""

    id: str
    username: str
    email: str
    role: str
    status: UserStatus
    created_at: str
    updated_at: str
    last_login_at: str | None = None
    permissions: list[str] = Field(default_factory=list, description="Resolved permission keys")
    is_super_admin: bool = Field(default=False, description="超级管理员标记（初始管理员）")


class UserInDB(BaseModel):
    """Full user document as stored in MongoDB — includes password_hash for internal use."""

    id: str = Field(alias="_id")
    username: str
    email: str
    password_hash: str = Field(exclude=True)  # Never serialize (AC6)
    role: str
    status: UserStatus
    created_at: str
    updated_at: str
    last_login_at: str | None = None

    model_config = {"populate_by_name": True}


class UserUpdate(BaseModel):
    """Schema for updating an existing user (partial update — PATCH)."""

    role: str | None = Field(default=None, description="New role name")
    status: UserStatus | None = Field(default=None, description="New status")


class UserListResponse(BaseModel):
    """Paginated user list response."""

    items: list[UserResponse]
    total: int
    page: int
    page_size: int


class PasswordResetRequest(BaseModel):
    """Admin password reset request."""

    new_password: str = Field(
        ..., min_length=8, max_length=128, description="New plaintext password"
    )


class PasswordResetResponse(BaseModel):
    """Password reset response."""

    message: str
