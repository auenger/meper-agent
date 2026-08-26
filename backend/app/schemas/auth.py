"""Authentication-related Pydantic schemas."""
from pydantic import BaseModel, Field


class LoginRequest(BaseModel):
    """Login request body."""

    username: str = Field(..., min_length=1, max_length=50, description="Username")
    password: str = Field(..., min_length=1, max_length=128, description="Password")


class RefreshRequest(BaseModel):
    """Refresh token request body."""

    refresh_token: str = Field(..., description="JWT refresh token")


class UserInfo(BaseModel):
    """Minimal user info returned alongside tokens."""

    id: str
    username: str
    role: str
    permissions: list[str] = Field(default_factory=list)
    is_super_admin: bool = Field(default=False, description="超级管理员标记（初始管理员）")


class TokenResponse(BaseModel):
    """JWT token pair returned after login or admin creation."""

    access_token: str = Field(..., description="Short-lived JWT access token (15min)")
    refresh_token: str = Field(..., description="Long-lived JWT refresh token (7d)")
    token_type: str = Field(default="bearer")
    expires_in: int = Field(default=900, description="Access token TTL in seconds (override at creation from settings)")
    user: UserInfo | None = Field(default=None, description="Basic user info including permissions")


class AdminCreateResult(BaseModel):
    """Result of the CLI create-admin command."""

    message: str
    user_id: str
    username: str
    tokens: TokenResponse


class ChangePasswordRequest(BaseModel):
    """Password change request body."""

    current_password: str = Field(..., min_length=1, max_length=128, description="Current password")
    new_password: str = Field(..., min_length=1, max_length=128, description="New password")


class LogoutRequest(BaseModel):
    """Logout request body."""

    refresh_token: str = Field(..., description="JWT refresh token to revoke")


class MessageResponse(BaseModel):
    """Generic message response."""

    message: str = Field(..., description="Response message")
