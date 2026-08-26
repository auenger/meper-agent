"""Auth API endpoints — login, refresh, password change, logout."""
from fastapi import APIRouter, Depends

from app.core.security import get_current_user
from app.schemas.auth import (
    ChangePasswordRequest,
    LoginRequest,
    LogoutRequest,
    MessageResponse,
    RefreshRequest,
    TokenResponse,
)
from app.schemas.user import UserResponse
from app.services.auth_service import AuthService

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post(
    "/login",
    response_model=TokenResponse,
    summary="User login",
    responses={
        401: {"description": "Invalid credentials or account locked"},
    },
)
async def login(body: LoginRequest) -> TokenResponse:
    """Authenticate with username + password, return JWT token pair."""
    return await AuthService.login(body.username, body.password)


@router.post(
    "/refresh",
    response_model=TokenResponse,
    summary="Refresh access token",
    responses={
        401: {"description": "Invalid or expired refresh token"},
    },
)
async def refresh(body: RefreshRequest) -> TokenResponse:
    """Exchange a valid refresh_token for a new access_token."""
    return await AuthService.refresh_token(body.refresh_token)


@router.get(
    "/me",
    response_model=UserResponse,
    summary="Get current user profile with live permissions",
    responses={
        401: {"description": "Invalid or expired token"},
    },
)
async def me(
    current_user: UserResponse = Depends(get_current_user),
) -> UserResponse:
    """Return the authenticated user with freshly resolved permissions.

    前端用于近实时感知角色/权限变更（窗口聚焦/轮询拉取）：每次调用都会
    从 DB 重读角色并重新解析权限集，管理员调整权限后已登录用户无需
    重新登录即可在下次拉取时拿到新权限列表。
    """
    return current_user


@router.post(
    "/change-password",
    response_model=MessageResponse,
    summary="Change password",
    responses={
        401: {"description": "Current password mismatch or unauthorized"},
        422: {"description": "New password validation failure"},
    },
)
async def change_password(
    body: ChangePasswordRequest,
    current_user: UserResponse = Depends(get_current_user),
) -> MessageResponse:
    """Change the authenticated user's password.

    Requires a valid JWT access token. The current password is verified
    before updating. All existing refresh tokens are invalidated after a
    successful password change.
    """
    await AuthService.change_password(
        user_id=current_user.id,
        current_password=body.current_password,
        new_password=body.new_password,
    )
    return MessageResponse(message="密码已修改，请重新登录")


@router.post(
    "/logout",
    response_model=MessageResponse,
    summary="Logout (revoke refresh token)",
    responses={
        200: {"description": "Logout successful (idempotent)"},
    },
)
async def logout(body: LogoutRequest) -> MessageResponse:
    """Logout by revoking the provided refresh_token.

    Idempotent — submitting the same or an invalid token multiple times
    still returns 200. After this call, the refresh_token can no longer
    be used to obtain new access tokens.
    """
    await AuthService.logout(body.refresh_token)
    return MessageResponse(message="已注销")
