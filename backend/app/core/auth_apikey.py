"""API Key authentication dependency for external API routes.

Provides ``get_api_key_principal`` — a FastAPI Depends that validates
the Bearer token as an API Key (not JWT) and returns an
``ApiKeyPrincipal`` object with scopes and bindings.

终端用户身份由 MEPER 统一托管：X-User-Token 必须是 MEPER 签发的通用 token
（``meper_`` 前缀），本地校验后解出 token 记录 id。详见
``docs/planning-artifacts/mcp-credential-broker-design.md``。

旧的外部 introspection 回调（RFC 7662）已废弃，``user_info_url`` 字段保留但
不再使用（向后兼容现有 ApiKey 文档）。
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
    # 保留字段（向后兼容），新逻辑不再使用
    user_info_url: str = ""
    user_id: str | None = None
    # MCP 兑换器查绑定的 key = mcp_token_credentials._id
    token_record_id: str | None = None
    # 原始 X-User-Token（通用 token 原文）
    user_token: str | None = None

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
        behalf carry the resolved user_id — ``f"{owner}:{sub}"`` (callback
        mode) or ``f"{owner}:{visitor_id}"`` (legacy mode with visitor_id)
        — because NotificationService reads ``task.created_by`` as the
        end-user id. Both shapes belong to the same owner, so we accept an
        exact match or an ``owner:`` prefix match.
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


async def get_api_key_principal(
    request: Request,
    authorization: str = Header(None, description="Bearer af_live_xxx"),
) -> ApiKeyPrincipal:
    """FastAPI dependency: authenticate via API Key.

    终端用户身份校验：X-User-Token 必填，必须是 MEPER 签发的通用 token
    （``meper_`` 前缀），本地校验后解出 token 记录 id（= user_id =
    token_record_id，供 MCP 兑换器查绑定）。

    Raises:
        UnauthorizedError: Missing/invalid API Key, missing/invalid X-User-Token.
    """
    from app.services.api_key_service import ApiKeyService
    from app.services.mcp_token_credential_service import (
        McpTokenCredentialService,
    )

    if not authorization or not authorization.startswith("Bearer "):
        raise UnauthorizedError(
            code="APIKEY_MISSING",
            message="Missing or malformed Authorization header",
        )

    full_key = authorization.removeprefix("Bearer ").strip()

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
        user_info_url=doc.get("user_info_url", "") or "",  # 保留，不再使用
    )

    # 终端用户身份校验（通用 token，本地校验）。
    user_token = _extract_bearer_token(request.headers.get("X-User-Token"))
    if not user_token:
        raise UnauthorizedError(
            code="EXT_USER_TOKEN_MISSING",
            message="X-User-Token header is required.",
        )
    record = await McpTokenCredentialService.verify_token(user_token)
    if record is None:
        raise UnauthorizedError(
            code="EXT_USER_TOKEN_INVALID",
            message="User token is invalid, expired, or revoked.",
        )
    # user_id = token_record_id = mcp_token_credentials._id，兑换器用它查绑定。
    principal.user_id = record["_id"]
    principal.token_record_id = record["_id"]
    principal.user_token = user_token

    return principal

