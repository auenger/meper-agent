"""MCP category API endpoints — CRUD."""
from __future__ import annotations

from fastapi import APIRouter, Depends

from app.core.security import get_current_user, require_any_role
from app.schemas.mcp_category import (
    McpCategoryCreate,
    McpCategoryListResponse,
    McpCategoryResponse,
    McpCategoryUpdate,
)
from app.schemas.user import UserResponse
from app.services.mcp_category_service import McpCategoryService

router = APIRouter(
    prefix="/mcp/categories",
    tags=["mcp"],
    dependencies=[Depends(get_current_user)],
)


def _doc_to_response(doc: dict) -> McpCategoryResponse:
    """Convert a raw MongoDB document to McpCategoryResponse."""
    return McpCategoryResponse(
        id=doc["_id"],
        name=doc["name"],
        description=doc.get("description", ""),
        sort=doc.get("sort", 0),
        created_at=doc.get("created_at", ""),
        updated_at=doc.get("updated_at", ""),
    )


@router.post(
    "",
    response_model=McpCategoryResponse,
    status_code=201,
    summary="Create MCP category",
    responses={403: {"description": "Forbidden — developer+ role required"}},
)
async def create_category(
    body: McpCategoryCreate,
    _: UserResponse = Depends(require_any_role("admin", "developer")),
) -> McpCategoryResponse:
    """Create a new MCP category for grouping connections."""
    doc = await McpCategoryService.create_category(body.model_dump())
    return _doc_to_response(doc)


@router.get(
    "",
    response_model=McpCategoryListResponse,
    summary="List MCP categories",
    responses={403: {"description": "Forbidden — viewer+ role required"}},
)
async def list_categories(
    _: UserResponse = Depends(require_any_role("admin", "developer", "operator", "viewer")),
) -> McpCategoryListResponse:
    """List all MCP categories ordered by sort asc."""
    items = await McpCategoryService.list_categories()
    return McpCategoryListResponse(
        items=[_doc_to_response(d) for d in items],
        total=len(items),
    )


@router.get(
    "/{category_id}",
    response_model=McpCategoryResponse,
    summary="Get MCP category",
    responses={
        403: {"description": "Forbidden — viewer+ role required"},
        404: {"description": "Category not found"},
    },
)
async def get_category(
    category_id: str,
    _: UserResponse = Depends(require_any_role("admin", "developer", "operator", "viewer")),
) -> McpCategoryResponse:
    """Get an MCP category by ID."""
    from app.core.errors import NotFoundError

    doc = await McpCategoryService.get_category(category_id)
    if doc is None:
        raise NotFoundError(
            code="MCP_CATEGORY_NOT_FOUND",
            message=f"MCP 分组 {category_id} 不存在",
        )
    return _doc_to_response(doc)


@router.put(
    "/{category_id}",
    response_model=McpCategoryResponse,
    summary="Update MCP category",
    responses={
        403: {"description": "Forbidden — developer+ role required"},
        404: {"description": "Category not found"},
    },
)
async def update_category(
    category_id: str,
    body: McpCategoryUpdate,
    _: UserResponse = Depends(require_any_role("admin", "developer")),
) -> McpCategoryResponse:
    """Update an MCP category (full PUT)."""
    doc = await McpCategoryService.update_category(category_id, body.model_dump())
    # update_category raises NotFoundError internally, so doc is non-None here.
    return _doc_to_response(doc)  # type: ignore[arg-type]


@router.delete(
    "/{category_id}",
    status_code=204,
    summary="Delete MCP category",
    responses={
        403: {"description": "Forbidden — developer+ role required"},
        404: {"description": "Category not found"},
        409: {"description": "Category not empty"},
    },
)
async def delete_category(
    category_id: str,
    _: UserResponse = Depends(require_any_role("admin", "developer")),
) -> None:
    """Delete an MCP category. Refuses if any connection still references it."""
    from app.core.errors import NotFoundError

    deleted = await McpCategoryService.delete_category(category_id)
    if not deleted:
        raise NotFoundError(
            code="MCP_CATEGORY_NOT_FOUND",
            message=f"MCP 分组 {category_id} 不存在",
        )
