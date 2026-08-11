"""MCP token management API — admin 管理终端用户的通用 token + MCP 绑定。

平台 admin（JWT 鉴权）为终端用户创建通用 token、绑定各 MCP 凭证、轮换/吊销。
详见 docs/planning-artifacts/mcp-credential-broker-design.md。
"""
from fastapi import APIRouter, Depends, Query

from app.core.errors import ConflictError, NotFoundError, ValidationError
from app.core.security import require_permission
from app.schemas.mcp_token_credential import (
    McpTokenCreate,
    McpTokenCreateResponse,
    McpTokenListResponse,
    McpTokenResponse,
    McpTokenRotateResponse,
    McpTokenUpdate,
)
from app.schemas.user import UserResponse
from app.services.mcp_token_credential_service import McpTokenCredentialService

router = APIRouter(
    prefix="/mcp-tokens",
    tags=["mcp-tokens"],
    dependencies=[Depends(require_permission("apikey:manage"))],
)


def _binding_input_to_dict(b) -> dict:
    """把 McpBindingInput/McpBindingUpdate 转成 dict（只保留非 None 字段）。"""
    d = b.model_dump(exclude_none=True)
    # 校验：token 型必须有 token；账密型必须有 username+password
    ctype = d.get("credential_type")
    if ctype == "token" and not d.get("token"):
        raise ValidationError(
            code="MCP_BINDING_INVALID",
            message="token 型绑定必须填 token",
        )
    if ctype == "password" and not (d.get("username") and d.get("password")):
        raise ValidationError(
            code="MCP_BINDING_INVALID",
            message="账密型绑定必须填 username 和 password",
        )
    return d


@router.post("", response_model=McpTokenCreateResponse, status_code=201, summary="创建通用 token")
async def create_mcp_token(
    body: McpTokenCreate,
    user: UserResponse = Depends(require_permission("apikey:manage")),
) -> McpTokenCreateResponse:
    """为终端用户创建一条通用 token 记录。

    通用 token 明文仅此一次返回（``token_plaintext``），admin 须妥善下发给终端用户。
    """
    # 查重：同名用户不允许
    existing = await McpTokenCredentialService._collection().find_one({"name": body.name})
    if existing is not None:
        raise ConflictError(
            code="MCP_TOKEN_NAME_CONFLICT",
            message=f"用户名「{body.name}」已存在",
        )
    mcp_bindings = {k: _binding_input_to_dict(v) for k, v in body.mcp_bindings.items()}
    doc, token = await McpTokenCredentialService.create_record(
        name=body.name,
        mcp_bindings=mcp_bindings,
        api_key_id=body.api_key_id,
        created_by=user.id,
    )
    masked = McpTokenCredentialService._to_masked(doc)
    return McpTokenCreateResponse(**masked, token_plaintext=token)


@router.get("", response_model=McpTokenListResponse, summary="列表")
async def list_mcp_tokens(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    name: str | None = Query(None),
    status: str | None = Query(None),
    _: UserResponse = Depends(require_permission("apikey:manage")),
) -> McpTokenListResponse:
    """分页列表（凭证脱敏）。"""
    items, total = await McpTokenCredentialService.list_records(
        page=page, page_size=page_size, name=name, status=status
    )
    return McpTokenListResponse(
        items=[McpTokenResponse(**item) for item in items],
        total=total,
    )


@router.get("/{record_id}", response_model=McpTokenResponse, summary="详情")
async def get_mcp_token(
    record_id: str,
    _: UserResponse = Depends(require_permission("apikey:manage")),
) -> McpTokenResponse:
    """单条详情（凭证脱敏）。"""
    doc = await McpTokenCredentialService.get_record(record_id)
    if doc is None:
        raise NotFoundError(
            code="MCP_TOKEN_NOT_FOUND",
            message=f"通用 token 记录 {record_id} 不存在",
        )
    return McpTokenResponse(**McpTokenCredentialService._to_masked(doc))


@router.put("/{record_id}", response_model=McpTokenResponse, summary="更新")
async def update_mcp_token(
    record_id: str,
    body: McpTokenUpdate,
    _: UserResponse = Depends(require_permission("apikey:manage")),
) -> McpTokenResponse:
    """更新名称/状态/绑定（字段级合并，空值=不改）。"""
    mcp_bindings = None
    if body.mcp_bindings is not None:
        # update 不做强制校验（service 层 _merge_binding 会保留空值字段的旧值）
        mcp_bindings = {k: v.model_dump(exclude_none=True) for k, v in body.mcp_bindings.items()}

    doc = await McpTokenCredentialService.update_record(
        record_id,
        name=body.name,
        status=body.status.value if body.status else None,
        mcp_bindings=mcp_bindings,
    )
    if doc is None:
        raise NotFoundError(
            code="MCP_TOKEN_NOT_FOUND",
            message=f"通用 token 记录 {record_id} 不存在",
        )
    return McpTokenResponse(**McpTokenCredentialService._to_masked(doc))


@router.delete("/{record_id}", status_code=204, summary="删除（吊销 token）")
async def delete_mcp_token(
    record_id: str,
    _: UserResponse = Depends(require_permission("apikey:manage")),
) -> None:
    """删除记录，通用 token 立即失效。"""
    deleted = await McpTokenCredentialService.delete_record(record_id)
    if not deleted:
        raise NotFoundError(
            code="MCP_TOKEN_NOT_FOUND",
            message=f"通用 token 记录 {record_id} 不存在",
        )


@router.post(
    "/{record_id}/rotate",
    response_model=McpTokenRotateResponse,
    summary="轮换 token",
)
async def rotate_mcp_token(
    record_id: str,
    _: UserResponse = Depends(require_permission("apikey:manage")),
) -> McpTokenRotateResponse:
    """轮换通用 token，旧 token 立即失效。返回新 token 明文。"""
    doc, new_token = await McpTokenCredentialService.rotate_token(record_id)
    if doc is None:
        raise NotFoundError(
            code="MCP_TOKEN_NOT_FOUND",
            message=f"通用 token 记录 {record_id} 不存在",
        )
    return McpTokenRotateResponse(id=doc["_id"], token_plaintext=new_token)


@router.get(
    "/{record_id}/reveal",
    summary="查看 token 明文",
)
async def reveal_mcp_token(
    record_id: str,
    _: UserResponse = Depends(require_permission("apikey:manage")),
) -> dict:
    """返回该用户的通用 token 明文（admin 需要复制给用户时查看）。"""
    doc = await McpTokenCredentialService.get_record(record_id)
    if doc is None:
        raise NotFoundError(
            code="MCP_TOKEN_NOT_FOUND",
            message=f"通用 token 记录 {record_id} 不存在",
        )
    return {"id": doc["_id"], "token": doc.get("token", "")}
