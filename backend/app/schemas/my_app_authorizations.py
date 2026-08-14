"""My app authorizations schemas — user self-service authorization API."""
from __future__ import annotations

from pydantic import BaseModel, Field


class AuthorizeAppRequest(BaseModel):
    """授权应用：绑定账密。"""

    username: str = Field(..., min_length=1, max_length=100, description="用户名")
    password: str = Field(..., min_length=1, max_length=200, description="密码")


class AppBindingResponse(BaseModel):
    """单个应用的授权状态（凭证脱敏）。"""

    app_id: str
    app_name: str = ""
    username: str = ""
    password_masked: str = ""
    bound: bool = False


class MyAuthorizationsResponse(BaseModel):
    """当前用户的全部应用授权。"""

    platform_user_id: str
    bindings: list[AppBindingResponse] = Field(default_factory=list)
    updated_at: str = ""


class AvailableAppResponse(BaseModel):
    """可授权的应用。"""

    id: str
    name: str
    description: str = ""
    mcp_count: int = 0


class AvailableAppsResponse(BaseModel):
    """可授权的应用列表。"""

    items: list[AvailableAppResponse]
