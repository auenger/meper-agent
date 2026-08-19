"""External API — user info（终端用户 token 验证 + 返回用户名）。

供 chat-widget.js 在宿主页面验证用户输入的通用 token 并获取用户名。
鉴权：API Key（接入方凭证）+ X-User-Token（introspection 回调校验）。
"""
from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.api.v1.ext import auth_and_rate_limit
from app.core.auth_apikey import ApiKeyPrincipal
from app.services.external_identity_service import ExternalIdentityService
from app.services.user_mcp_credential_service import UserMcpCredentialService

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
    """验证终端用户 token，返回用户名和应用绑定数。

    chat-widget.js 在宿主页面调用此接口验证用户输入的 token。
    principal.user_id = platform_user_id（v4：external_identities 反查所得）。
    用户名取该平台用户任一外部身份 sub 的 username 部分；
    绑定数取 user_mcp_credentials.app_bindings 的数量。
    """
    identities = await ExternalIdentityService.list_by_platform_user(principal.user_id or "")
    name = "用户"
    for identity in identities:
        # sub = {app_id}:{username}
        username = (identity.get("sub") or "").split(":", 1)[-1]
        if username:
            name = username
            break

    binding_count = 0
    bindings = await UserMcpCredentialService.list_bindings(principal.user_id or "")
    if bindings:
        binding_count = len(bindings.get("app_bindings") or {})

    return ExtUserInfoResponse(name=name, binding_count=binding_count)
