"""Model API endpoints — CRUD operations for LLM model management."""
from fastapi import APIRouter, Depends, Query

from app.core.security import get_current_user, require_any_role
from app.models.model import AuthType, CompatibilityType, ModelStatus, ModelTaskType
from app.schemas.model import (
    ModelCreate,
    ModelListResponse,
    ModelResponse,
    ModelTestResponse,
    ModelUpdate,
)
from app.schemas.user import UserResponse
from app.services.model_service import ModelService

router = APIRouter(
    prefix="/models",
    tags=["models"],
    dependencies=[Depends(get_current_user)],
)


def _doc_to_response(doc: dict) -> ModelResponse:
    """Convert a raw MongoDB document to ModelResponse."""
    return ModelResponse(
        id=doc["_id"],
        model_id=doc["model_id"],
        name=doc["name"],
        base_url=doc["base_url"],
        api_key=doc.get("api_key", "****"),
        compatibility_type=CompatibilityType(doc["compatibility_type"]),
        auth_type=AuthType(doc.get("auth_type", "bearer")),
        auth_header_format=doc.get("auth_header_format", "Bearer {key}"),
        default_params=doc.get("default_params", {}),
        status=ModelStatus(doc["status"]),
        task_type=ModelTaskType(doc.get("task_type", "chat")),
        last_test_success=doc.get("last_test_success"),  # None for legacy docs
        last_test_at=doc.get("last_test_at", ""),
        provider_tag=doc.get("provider_tag", ""),
        version=doc.get("version", 1),
        created_at=doc["created_at"],
        updated_at=doc["updated_at"],
    )


@router.get(
    "",
    response_model=ModelListResponse,
    summary="List all Models",
    responses={
        403: {"description": "Forbidden — viewer+ role required"},
    },
)
async def list_models(
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(20, ge=1, le=200, description="Items per page"),
    status: ModelStatus | None = Query(None, description="Filter by status"),
    provider_tag: str | None = Query(None, description="Filter by provider tag"),
    task_type: ModelTaskType | None = Query(None, description="Filter by model purpose (chat/embedding/rerank)"),
    _: UserResponse = Depends(require_any_role("admin", "developer", "operator", "viewer")),
) -> ModelListResponse:
    """List all Models with pagination and optional filtering."""
    items, total = await ModelService.list_models(
        page=page,
        page_size=page_size,
        status=status.value if status else None,
        provider_tag=provider_tag,
        task_type=task_type.value if task_type else None,
    )

    models = [_doc_to_response(doc) for doc in items]
    return ModelListResponse(items=models, total=total, page=page, page_size=page_size)


@router.post(
    "",
    response_model=ModelResponse,
    status_code=201,
    summary="Create a new Model",
    responses={
        403: {"description": "Forbidden — admin role required"},
        409: {"description": "Model ID conflict"},
        422: {"description": "Validation error"},
    },
)
async def create_model(
    body: ModelCreate,
    _: UserResponse = Depends(require_any_role("admin")),
) -> ModelResponse:
    """Create a new LLM model configuration."""
    doc = await ModelService.create_model(
        model_id=body.model_id,
        name=body.name,
        base_url=body.base_url,
        api_key=body.api_key,
        compatibility_type=body.compatibility_type.value,
        auth_type=body.auth_type.value,
        auth_header_format=body.auth_header_format,
        default_params=body.default_params,
        provider_tag=body.provider_tag,
        task_type=body.task_type.value,
    )
    return _doc_to_response(doc)


@router.get(
    "/{model_id}",
    response_model=ModelResponse,
    summary="Get Model details",
    responses={
        403: {"description": "Forbidden — viewer+ role required"},
        404: {"description": "Model not found"},
    },
)
async def get_model(
    model_id: str,
    _: UserResponse = Depends(require_any_role("admin", "developer", "operator", "viewer")),
) -> ModelResponse:
    """Get a Model by its ID."""
    from app.core.errors import NotFoundError

    doc = await ModelService.get_model(model_id)
    if doc is None:
        raise NotFoundError(
            code="MODEL_NOT_FOUND",
            message=f"模型 {model_id} 不存在",
        )

    return _doc_to_response(doc)


@router.post(
    "/{model_id}/test",
    response_model=ModelTestResponse,
    summary="Test Model connectivity",
    responses={
        403: {"description": "Forbidden — admin/developer role required"},
        404: {"description": "Model not found or key decryption failed"},
    },
)
async def test_model(
    model_id: str,
    _: UserResponse = Depends(require_any_role("admin", "developer")),
) -> ModelTestResponse:
    """Send a minimal probe request to validate model connectivity.

    Sends a "ping" message via the model's configured endpoint and
    returns the round-trip latency plus the model's reply. Useful for
    verifying API Key validity and base_url reachability.
    """
    result = await ModelService.test_model(model_id)
    return ModelTestResponse(**result)


@router.put(
    "/{model_id}",
    response_model=ModelResponse,
    summary="Update a Model",
    responses={
        403: {"description": "Forbidden — admin role required"},
        404: {"description": "Model not found"},
        409: {"description": "Model ID conflict"},
        422: {"description": "Validation error"},
    },
)
async def update_model(
    model_id: str,
    body: ModelUpdate,
    _: UserResponse = Depends(require_any_role("admin")),
) -> ModelResponse:
    """Update a Model's configuration. Auto-increments version."""
    from app.core.errors import NotFoundError

    doc = await ModelService.update_model(
        model_id=model_id,
        model_id_str=body.model_id,
        name=body.name,
        base_url=body.base_url,
        api_key=body.api_key,
        compatibility_type=body.compatibility_type.value,
        auth_type=body.auth_type.value,
        auth_header_format=body.auth_header_format,
        default_params=body.default_params,
        provider_tag=body.provider_tag,
        task_type=body.task_type.value,
    )
    if doc is None:
        raise NotFoundError(
            code="MODEL_NOT_FOUND",
            message=f"模型 {model_id} 不存在",
        )

    return _doc_to_response(doc)


@router.delete(
    "/{model_id}",
    status_code=204,
    summary="Delete a Model",
    responses={
        403: {"description": "Forbidden — admin role required"},
        404: {"description": "Model not found"},
        409: {"description": "Model is referenced by one or more Agents"},
    },
)
async def delete_model(
    model_id: str,
    _: UserResponse = Depends(require_any_role("admin")),
) -> None:
    """Delete a Model by ID. Checks for Agent references."""
    from app.core.errors import NotFoundError

    deleted = await ModelService.delete_model(model_id)
    if not deleted:
        raise NotFoundError(
            code="MODEL_NOT_FOUND",
            message=f"模型 {model_id} 不存在",
        )
