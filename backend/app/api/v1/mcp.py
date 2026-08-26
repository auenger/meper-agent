"""MCP connection API endpoints — CRUD, test, discover."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from app.core.security import get_current_user, require_permission
from app.models.mcp_connection import ConnectionStatus
from app.schemas.mcp_connection import (
    McpConnectionCreate,
    McpConnectionListResponse,
    McpConnectionResponse,
    McpConnectionUpdate,
    McpDiscoverResult,
    McpTestResult,
)
from app.schemas.user import UserResponse
from app.services.mcp_connection_service import McpConnectionService

router = APIRouter(
    prefix="/mcp/connections",
    tags=["mcp"],
    dependencies=[Depends(get_current_user)],
)

# Sensitive fields to mask in API responses
_SENSITIVE_KEYS = {"api_key", "token", "password"}


def _mask_auth_config(auth_config: dict) -> dict:
    """Mask sensitive values in auth_config for API responses."""
    if not auth_config:
        return auth_config
    return {
        k: ("***" if k in _SENSITIVE_KEYS and v else v)
        for k, v in auth_config.items()
    }


def _mask_default_params(default_params: dict) -> dict:
    """Mask sensitive values in default_params for API responses."""
    if not default_params:
        return default_params
    return {
        k: ("***" if k in _SENSITIVE_KEYS and v else v)
        for k, v in default_params.items()
    }


def _doc_to_response(doc: dict) -> McpConnectionResponse:
    """Convert a raw MongoDB document to McpConnectionResponse."""
    return McpConnectionResponse(
        id=doc["_id"],
        name=doc["name"],
        description=doc.get("description", ""),
        category_id=doc.get("category_id", ""),
        url=doc["url"],
        protocol=doc.get("protocol", "streamable-http"),
        auth_type=doc.get("auth_type", "none"),
        auth_config=_mask_auth_config(doc.get("auth_config", {})),
        timeout=doc.get("timeout", 30),
        default_params=_mask_default_params(doc.get("default_params", {})),
        status=ConnectionStatus(doc.get("status", ConnectionStatus.DISCONNECTED.value)),
        status_message=doc.get("status_message", ""),
        last_connected_at=doc.get("last_connected_at", ""),
        tool_count=doc.get("tool_count", 0),
        created_at=doc.get("created_at", ""),
        updated_at=doc.get("updated_at", ""),
    )


@router.post(
    "",
    response_model=McpConnectionResponse,
    status_code=201,
    summary="Create MCP connection",
    responses={403: {"description": "Forbidden — mcp:write permission required"}},
)
async def create_connection(
    body: McpConnectionCreate,
    _: UserResponse = Depends(require_permission("mcp:write")),
) -> McpConnectionResponse:
    """Create a new MCP server connection configuration."""
    doc = await McpConnectionService.create_connection(body.model_dump())
    return _doc_to_response(doc)


@router.get(
    "",
    response_model=McpConnectionListResponse,
    summary="List MCP connections",
    responses={403: {"description": "Forbidden — mcp:read permission required"}},
)
async def list_connections(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    name: str | None = Query(None, description="Filter by name (substring)"),
    status: ConnectionStatus | None = Query(None, description="Filter by status"),
    category_id: str | None = Query(None, description="Filter by category id"),
    _: UserResponse = Depends(require_permission("mcp:read")),
) -> McpConnectionListResponse:
    """List MCP connections with pagination and optional filtering."""
    items, total = await McpConnectionService.list_connections(
        page=page,
        page_size=page_size,
        name=name,
        status=status.value if status else None,
        category_id=category_id,
    )
    return McpConnectionListResponse(
        items=[_doc_to_response(d) for d in items],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get(
    "/{connection_id}",
    response_model=McpConnectionResponse,
    summary="Get MCP connection",
    responses={
        403: {"description": "Forbidden — mcp:read permission required"},
        404: {"description": "Connection not found"},
    },
)
async def get_connection(
    connection_id: str,
    _: UserResponse = Depends(require_permission("mcp:read")),
) -> McpConnectionResponse:
    """Get an MCP connection by ID."""
    from app.core.errors import NotFoundError

    doc = await McpConnectionService.get_connection(connection_id)
    if doc is None:
        raise NotFoundError(
            code="MCP_CONN_NOT_FOUND",
            message=f"MCP 连接 {connection_id} 不存在",
        )
    return _doc_to_response(doc)


@router.put(
    "/{connection_id}",
    response_model=McpConnectionResponse,
    summary="Update MCP connection",
    responses={
        403: {"description": "Forbidden — mcp:write permission required"},
        404: {"description": "Connection not found"},
    },
)
async def update_connection(
    connection_id: str,
    body: McpConnectionUpdate,
    _: UserResponse = Depends(require_permission("mcp:write")),
) -> McpConnectionResponse:
    """Update an MCP connection configuration."""
    from app.core.errors import NotFoundError

    doc = await McpConnectionService.update_connection(
        connection_id, body.model_dump()
    )
    if doc is None:
        raise NotFoundError(
            code="MCP_CONN_NOT_FOUND",
            message=f"MCP 连接 {connection_id} 不存在",
        )
    return _doc_to_response(doc)


@router.delete(
    "/{connection_id}",
    status_code=204,
    summary="Delete MCP connection",
    responses={
        403: {"description": "Forbidden — mcp:write permission required"},
        404: {"description": "Connection not found"},
    },
)
async def delete_connection(
    connection_id: str,
    _: UserResponse = Depends(require_permission("mcp:write")),
) -> None:
    """Delete an MCP connection and cascade-remove its MCP tools."""
    from app.core.errors import NotFoundError

    deleted = await McpConnectionService.delete_connection(connection_id)
    if not deleted:
        raise NotFoundError(
            code="MCP_CONN_NOT_FOUND",
            message=f"MCP 连接 {connection_id} 不存在",
        )


@router.post(
    "/{connection_id}/test",
    response_model=McpTestResult,
    summary="Test MCP connection",
    responses={
        403: {"description": "Forbidden — mcp:write permission required"},
        404: {"description": "Connection not found"},
    },
)
async def test_connection(
    connection_id: str,
    _: UserResponse = Depends(require_permission("mcp:write")),
) -> McpTestResult:
    """Test an MCP server connection and update its status."""
    result = await McpConnectionService.test_connection(connection_id)
    return McpTestResult(**result)


@router.post(
    "/{connection_id}/discover",
    response_model=McpDiscoverResult,
    summary="Discover MCP tools",
    responses={
        403: {"description": "Forbidden — mcp:write permission required"},
        404: {"description": "Connection not found"},
    },
)
async def discover_tools(
    connection_id: str,
    _: UserResponse = Depends(require_permission("mcp:write")),
) -> McpDiscoverResult:
    """Discover tools from an MCP server and register to tool pool."""
    result = await McpConnectionService.discover_tools(connection_id)
    return McpDiscoverResult(**result)
