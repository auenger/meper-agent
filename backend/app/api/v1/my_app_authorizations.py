"""My app authorizations — user self-service authorization API.

用户登录平台后，自行管理对应用的授权。授权时调应用的 login_url
验证账密，并自动建立 external_identities 映射
（``{app_id}:{username}`` → platform_user_id）。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends

from app.core.errors import NotFoundError, ValidationError
from app.core.security import get_current_user
from app.schemas.my_app_authorizations import (
    AppBindingResponse,
    AuthorizeAppRequest,
    AvailableAppResponse,
    AvailableAppsResponse,
    MyAuthorizationsResponse,
)
from app.schemas.user import UserResponse
from app.services.application_service import ApplicationService
from app.services.user_mcp_credential_service import UserMcpCredentialService

router = APIRouter(
    prefix="/my-app-authorizations",
    tags=["applications"],
    dependencies=[Depends(get_current_user)],
)


async def get_my_authorizations(user: UserResponse) -> MyAuthorizationsResponse:
    """查看当前用户的应用授权列表（凭证脱敏）。"""
    platform_user_id = user.id
    result = await UserMcpCredentialService.list_bindings(platform_user_id)

    # 获取所有应用用于名称映射
    applications = await ApplicationService.list_applications()
    app_map = {a["_id"]: a for a in applications}

    bindings: list[AppBindingResponse] = []
    app_bindings = (result or {}).get("app_bindings", {})
    for aid, b in app_bindings.items():
        app = app_map.get(aid, {})
        bindings.append(AppBindingResponse(
            app_id=aid,
            app_name=app.get("name", ""),
            username=b.get("username", ""),
            password_masked=b.get("password", ""),
            bound=True,
        ))

    return MyAuthorizationsResponse(
        platform_user_id=platform_user_id,
        bindings=bindings,
        updated_at=(result or {}).get("updated_at", ""),
    )


@router.get(
    "",
    response_model=MyAuthorizationsResponse,
    summary="查看我的应用授权",
)
async def read_my_authorizations(
    user: UserResponse = Depends(get_current_user),
) -> MyAuthorizationsResponse:
    return await get_my_authorizations(user)


@router.get(
    "/available-apps",
    response_model=AvailableAppsResponse,
    summary="查看可授权的应用",
)
async def list_available_apps(
    user: UserResponse = Depends(get_current_user),
) -> AvailableAppsResponse:
    """列出所有可授权的应用（配置了 login_url 的），供用户选择授权。"""
    applications = await ApplicationService.list_applications()
    items: list[AvailableAppResponse] = []
    for a in applications:
        login_config = a.get("login_config") or {}
        if not login_config.get("login_url"):
            continue  # 没有登录配置的应用不可授权
        items.append(AvailableAppResponse(
            id=a["_id"],
            name=a.get("name", ""),
            description=a.get("description", ""),
            mcp_count=len(a.get("mcp_connection_ids") or []),
        ))
    return AvailableAppsResponse(items=items)


@router.put(
    "/{app_id}",
    response_model=MyAuthorizationsResponse,
    summary="授权应用（绑定账密）",
)
async def authorize_app(
    app_id: str,
    body: AuthorizeAppRequest,
    user: UserResponse = Depends(get_current_user),
) -> MyAuthorizationsResponse:
    """授权某应用：绑定/更新账密。

    内部调应用的 login_url 验证账密，成功后：
    - sub = {app_id}:{username} → 写 external_identities（含抢注保护）
    - 加密账密写入 user_mcp_credentials.app_bindings
    """
    application = await ApplicationService.get_application(app_id)
    if application is None:
        raise NotFoundError(
            code="APPLICATION_NOT_FOUND",
            message=f"应用 {app_id} 不存在",
        )

    login_config = application.get("login_config") or {}
    if not login_config.get("login_url"):
        raise ValidationError(
            code="APPLICATION_NO_LOGIN_CONFIG",
            message=f"应用「{application.get('name', app_id)}」未配置登录端点",
        )

    try:
        await UserMcpCredentialService.bind_credential(
            platform_user_id=user.id,
            app_id=app_id,
            username=body.username,
            password=body.password,
            login_config=login_config,
        )
    except PermissionError as exc:
        raise ValidationError(
            code="MCP_CREDENTIAL_INVALID",
            message=str(exc),
        ) from exc

    return await get_my_authorizations(user)


@router.delete(
    "/{app_id}",
    response_model=MyAuthorizationsResponse,
    summary="取消授权",
)
async def revoke_app(
    app_id: str,
    user: UserResponse = Depends(get_current_user),
) -> MyAuthorizationsResponse:
    """取消某应用的授权（解绑凭证 + 删身份映射）。"""
    await UserMcpCredentialService.unbind_credential(
        platform_user_id=user.id,
        app_id=app_id,
    )
    return await get_my_authorizations(user)
