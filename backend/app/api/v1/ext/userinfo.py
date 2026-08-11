"""External API — user info（终端用户 token 验证 + 返回用户名）。

供 chat-widget.js 在宿主页面验证用户输入的通用 token 并获取用户名。
鉴权：API Key（接入方凭证）+ X-User-Token（通用 token，本地校验）。
"""
from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.api.v1.ext import auth_and_rate_limit
from app.core.auth_apikey import ApiKeyPrincipal
from app.services.mcp_token_credential_service import McpTokenCredentialService

router = APIRouter(tags=["external-userinfo"])


class ExtUserInfoResponse(BaseModel):
    """终端用户信息（给 widget 显示用户名用）。"""
    name: str
    binding_count: int = 0


@router.get(
    "/userinfo",
    response_model=ExtUserInfoResponse,
    summary="Verify user token & return name",
)
async def get_user_info(
    principal: ApiKeyPrincipal = Depends(auth_and_rate_limit),
) -> ExtUserInfoResponse:
    """验证通用 token，返回用户名和绑定数。

    chat-widget.js 在宿主页面调用此接口验证用户输入的 token。
    principal.user_id = mcp_token_credentials._id（由 get_api_key_principal 本地校验设置）。
    """
    record = await McpTokenCredentialService.get_record(principal.user_id)
    if record is None:
        return ExtUserInfoResponse(name="用户", binding_count=0)
    return ExtUserInfoResponse(
        name=record.get("name", "用户"),
        binding_count=len(record.get("mcp_bindings") or {}),
    )
