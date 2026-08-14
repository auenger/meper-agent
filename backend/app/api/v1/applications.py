"""Application API endpoints — CRUD."""
from __future__ import annotations

from fastapi import APIRouter, Depends

from app.core.security import get_current_user, require_permission
from app.schemas.application import (
    ApplicationCreate,
    ApplicationListResponse,
    ApplicationResponse,
    ApplicationUpdate,
)
from app.schemas.user import UserResponse
from app.services.application_service import ApplicationService

router = APIRouter(
    prefix="/applications",
    tags=["applications"],
    dependencies=[Depends(get_current_user)],
)


def _doc_to_response(doc: dict) -> ApplicationResponse:
    """Convert a raw MongoDB document to ApplicationResponse."""
    return ApplicationResponse(
        id=doc["_id"],
        name=doc["name"],
        description=doc.get("description", ""),
        mcp_connection_ids=doc.get("mcp_connection_ids", []),
        login_config=doc.get("login_config", {}),
        created_at=doc.get("created_at", ""),
        updated_at=doc.get("updated_at", ""),
    )


@router.post(
    "",
    response_model=ApplicationResponse,
    status_code=201,
    summary="Create application",
    responses={403: {"description": "Forbidden — developer+ role required"}},
)
async def create_application(
    body: ApplicationCreate,
    _: UserResponse = Depends(require_permission("application:write")),
) -> ApplicationResponse:
    """Create a new application (authorization boundary for an external system)."""
    doc = await ApplicationService.create_application(body.model_dump())
    return _doc_to_response(doc)


@router.get(
    "",
    response_model=ApplicationListResponse,
    summary="List applications",
    responses={403: {"description": "Forbidden — viewer+ role required"}},
)
async def list_applications(
    _: UserResponse = Depends(require_permission("application:read")),
) -> ApplicationListResponse:
    """List all applications."""
    items = await ApplicationService.list_applications()
    return ApplicationListResponse(
        items=[_doc_to_response(d) for d in items],
        total=len(items),
    )


@router.get(
    "/{application_id}",
    response_model=ApplicationResponse,
    summary="Get application",
    responses={
        403: {"description": "Forbidden — viewer+ role required"},
        404: {"description": "Application not found"},
    },
)
async def get_application(
    application_id: str,
    _: UserResponse = Depends(require_permission("application:read")),
) -> ApplicationResponse:
    """Get an application by ID."""
    from app.core.errors import NotFoundError

    doc = await ApplicationService.get_application(application_id)
    if doc is None:
        raise NotFoundError(
            code="APPLICATION_NOT_FOUND",
            message=f"应用 {application_id} 不存在",
        )
    return _doc_to_response(doc)


@router.put(
    "/{application_id}",
    response_model=ApplicationResponse,
    summary="Update application",
    responses={
        403: {"description": "Forbidden — developer+ role required"},
        404: {"description": "Application not found"},
    },
)
async def update_application(
    application_id: str,
    body: ApplicationUpdate,
    _: UserResponse = Depends(require_permission("application:write")),
) -> ApplicationResponse:
    """Update an application (full PUT)."""
    doc = await ApplicationService.update_application(application_id, body.model_dump())
    return _doc_to_response(doc)  # type: ignore[arg-type]


@router.delete(
    "/{application_id}",
    status_code=204,
    summary="Delete application",
    responses={
        403: {"description": "Forbidden — developer+ role required"},
        404: {"description": "Application not found"},
        409: {"description": "Application still binds MCP connections"},
    },
)
async def delete_application(
    application_id: str,
    _: UserResponse = Depends(require_permission("application:write")),
) -> None:
    """Delete an application. Refuses if it still binds MCP connections."""
    from app.core.errors import NotFoundError

    deleted = await ApplicationService.delete_application(application_id)
    if not deleted:
        raise NotFoundError(
            code="APPLICATION_NOT_FOUND",
            message=f"应用 {application_id} 不存在",
        )
