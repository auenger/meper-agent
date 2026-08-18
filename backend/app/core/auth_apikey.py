"""API Key authentication dependency for external API routes.

Provides ``get_api_key_principal`` — a FastAPI Depends that validates
the Bearer token as an API Key (not JWT) and returns an
``ApiKeyPrincipal`` object with scopes and bindings.

终端用户身份由 MEPER 统一托管：X-User-Token 必须是 MEPER 签发的通用 token
（``meper_`` 前缀），本地校验后解出 token 记录 id。详见
``docs/planning-artifacts/mcp-credential-broker-design.md``。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from fastapi import Header, Request

from app.core.errors import ForbiddenError, UnauthorizedError


@dataclass
class ApiKeyPrincipal:
    """Authenticated API Key identity.

    Carries the Key's scopes, resource bindings, and owner_user_id
    so that downstream route handlers can enforce authorization.

    终端用户身份（通用 token 模式）:
    - ``user_id``: 解析出的稳定用户 ID = mcp_token_credentials._id（记录 id）。
    - ``token_record_id``: MCP 兑换器查绑定的 key（同 user_id）。
    - ``user_token``: 原始 X-User-Token（通用 token 原文），标识用途。
    """

    key_id: str
    owner_user_id: str
    scopes: list[str] = field(default_factory=list)
    bindings: dict = field(default_factory=dict)
    rate_limit: int = 60
    user_id: str | None = None
    # MCP 兑换器查绑定的 key = mcp_token_credentials._id
    token_record_id: str | None = None
    # 原始 X-User-Token（通用 token 原文）
    user_token: str | None = None
    # 应用上下文（ticket 序列化 / 语音通道每 turn 凭证复查用）
    app_id: str = ""
    introspect_url: str = ""

    def has_scope(self, scope: str) -> bool:
        return scope in self.scopes

    def require_scope(self, scope: str) -> None:
        if not self.has_scope(scope):
            raise ForbiddenError(
                code="APIKEY_SCOPE_DENIED",
                message=f"API Key 权限不足，需要 {scope} 权限",
            )

    def can_access_agent(self, agent_id: str) -> bool:
        """Check if this key can access the given Agent. Empty bindings = all."""
        allowed = self.bindings.get("agents", [])
        if not allowed:
            return True
        return agent_id in allowed

    def can_access_workflow(self, workflow_id: str) -> bool:
        """Check if this key can access the given Workflow. Empty bindings = all."""
        allowed = self.bindings.get("workflows", [])
        if not allowed:
            return True
        return workflow_id in allowed

    def require_agent_access(self, agent_id: str) -> None:
        if not self.can_access_agent(agent_id):
            raise ForbiddenError(
                code="APIKEY_AGENT_DENIED",
                message="API Key 无权访问该 Agent",
            )

    def require_workflow_access(self, workflow_id: str) -> None:
        if not self.can_access_workflow(workflow_id):
            raise ForbiddenError(
                code="APIKEY_WORKFLOW_DENIED",
                message="API Key 无权访问该 Workflow",
            )

    def owns_resource(self, created_by: str | None) -> bool:
        """Check if a resource (e.g. a Task) belongs to this Key's owner.

        Resources created via the Workflow invoke path carry the bare
        ``owner_user_id``. Resources created by an Agent on the user's
        behalf carry the resolved user_id (``mcp_token_credentials._id``).
        Both shapes belong to the same owner, so we accept an exact match
        or an ``owner:`` prefix match.
        """
        if not created_by:
            return False
        if created_by == self.owner_user_id:
            return True
        return created_by.startswith(f"{self.owner_user_id}:")


def _extract_bearer_token(header_value: str | None) -> str | None:
    """Extract a Bearer token from a header value, accepting both
    ``Bearer xxx`` and bare-token forms. Returns None on missing/empty.
    """
    if not header_value:
        return None
    value = header_value.strip()
    if value.lower().startswith("bearer "):
        value = value[7:].strip()
    return value or None


async def authenticate_api_key(full_key: str, user_token: str) -> ApiKeyPrincipal:
    """Core API Key authentication, independent of FastAPI Request/headers.

    Shared by the HTTP dependency and non-HTTP surfaces (e.g. the voice
    realtime WebSocket, which cannot carry custom headers).

    终端用户身份校验（v4）：X-User-Token 通过接入方 introspection 端点验证，
    ApiKey 绑定的 app_id 提供身份命名空间，sub = {app_id}:{username} 查
    external_identities 反查 platform_user_id。该 platform_user_id 作为
    user_id = token_record_id，供 MCP 凭证兑换器查 app_bindings。

    Raises:
        UnauthorizedError: Missing/invalid API Key, ApiKey not bound to
            an application, unknown application, missing/invalid
            X-User-Token, introspection failure, or user has not
            authorized the application.
    """
    from app.services.api_key_service import ApiKeyService

    if not full_key.startswith("af_live_"):
        raise UnauthorizedError(
            code="APIKEY_INVALID",
            message="Invalid or expired API Key",
        )

    doc = await ApiKeyService.verify_key(full_key)
    if doc is None:
        raise UnauthorizedError(
            code="APIKEY_INVALID",
            message="Invalid or expired API Key",
        )

    principal = ApiKeyPrincipal(
        key_id=doc["_id"],
        owner_user_id=doc["owner_user_id"],
        scopes=doc.get("scopes", []),
        bindings=doc.get("bindings", {}),
        rate_limit=doc.get("rate_limit", 60),
    )

    # 终端用户身份校验（introspection + 应用命名空间 + external_identities）。
    if not user_token:
        raise UnauthorizedError(
            code="EXT_USER_TOKEN_MISSING",
            message="X-User-Token header is required.",
        )

    # ② 应用上下文（绑定在 ApiKey 上——接入方系统与应用一一对应）
    app_id = doc.get("app_id") or ""
    if not app_id:
        raise UnauthorizedError(
            code="APP_ID_MISSING",
            message="API Key 未绑定应用",
        )

    from app.services.application_service import ApplicationService

    application = await ApplicationService.get_application(app_id)
    if application is None:
        raise UnauthorizedError(
            code="APP_NOT_FOUND",
            message=f"API Key 绑定的应用 {app_id} 不存在",
        )

    # ③ introspection（替代 v1 的本地 verify_token）
    from app.services.user_auth_service import UserAuthService

    introspect_url = doc.get("introspect_url")
    if not introspect_url:
        raise UnauthorizedError(
            code="INTROSPECT_URL_NOT_CONFIGURED",
            message="API Key 未配置 introspection 端点",
        )

    result = await UserAuthService.introspect(introspect_url, user_token)
    if not result.active:
        raise UnauthorizedError(
            code="EXT_USER_TOKEN_INVALID",
            message="User token is invalid, expired, or revoked.",
        )
    if not result.username:
        raise UnauthorizedError(
            code="EXT_USER_TOKEN_INVALID",
            message="Introspection result has no username.",
        )

    # ④ 组合 sub（{app_id}:{username}）+ ⑤ 查 external_identities
    from app.models.external_identity import compose_sub
    from app.services.external_identity_service import ExternalIdentityService

    sub = compose_sub(app_id, result.username)
    identity = await ExternalIdentityService.find_by_sub(sub)
    if identity is None:
        raise UnauthorizedError(
            code="EXT_USER_NOT_BOUND",
            message="未授权该应用，请先在外部授权页完成授权",
        )

    # ⑥ 设身份（platform_user_id 替代 mcptok_ id）
    principal.user_id = identity["platform_user_id"]
    principal.token_record_id = identity["platform_user_id"]
    principal.user_token = user_token
    principal.app_id = app_id
    principal.introspect_url = introspect_url

    return principal


async def get_api_key_principal(
    request: Request,
    authorization: str = Header(None, description="Bearer af_live_xxx"),
) -> ApiKeyPrincipal:
    """FastAPI dependency: authenticate via API Key.

    Header extraction only; the full chain lives in ``authenticate_api_key``
    so non-HTTP surfaces (WebSocket ticket validation) can reuse it.
    """
    if not authorization or not authorization.startswith("Bearer "):
        raise UnauthorizedError(
            code="APIKEY_MISSING",
            message="Missing or malformed Authorization header",
        )

    full_key = authorization.removeprefix("Bearer ").strip()
    user_token = _extract_bearer_token(request.headers.get("X-User-Token")) or ""

    return await authenticate_api_key(full_key, user_token)

