"""Internal Webhook management endpoints — JWT auth, admin only."""
from fastapi import APIRouter, Depends

from app.core.security import get_current_user, require_role
from app.models.user import UserRole
from app.schemas.webhook import (
    WebhookCreate,
    WebhookDeliveryLogResponse,
    WebhookListResponse,
    WebhookResponse,
    WebhookTestResult,
    WebhookUpdate,
)
from app.services.webhook_service import WebhookService

router = APIRouter(
    prefix="/webhooks",
    tags=["webhooks"],
    dependencies=[Depends(get_current_user), Depends(require_role(UserRole.ADMIN))],
)


def _doc_to_response(doc: dict) -> WebhookResponse:
    return WebhookResponse(
        id=doc["_id"],
        name=doc["name"],
        url=doc["url"],
        events=doc["events"],
        api_key_id=doc.get("api_key_id"),
        status=doc["status"],
        created_at=doc["created_at"],
        updated_at=doc["updated_at"],
    )


@router.post(
    "",
    response_model=WebhookResponse,
    status_code=201,
    summary="Create Webhook",
)
async def create_webhook(body: WebhookCreate) -> WebhookResponse:
    """Create a new Webhook configuration."""
    doc = await WebhookService.create_webhook(
        name=body.name,
        url=body.url,
        events=body.events,
        api_key_id=body.api_key_id,
    )
    return _doc_to_response(doc)


@router.get(
    "",
    response_model=WebhookListResponse,
    summary="List Webhooks",
)
async def list_webhooks(
    page: int = 1,
    page_size: int = 20,
) -> WebhookListResponse:
    """List all Webhook configurations."""
    items, total = await WebhookService.list_webhooks(page=page, page_size=page_size)
    return WebhookListResponse(
        items=[_doc_to_response(d) for d in items],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get(
    "/{webhook_id}",
    response_model=WebhookResponse,
    summary="Get Webhook details",
)
async def get_webhook(webhook_id: str) -> WebhookResponse:
    """Get Webhook configuration details."""
    from app.core.errors import NotFoundError

    doc = await WebhookService.get_webhook(webhook_id)
    if doc is None:
        raise NotFoundError(code="WEBHOOK_NOT_FOUND", message="Webhook not found")
    return _doc_to_response(doc)


@router.put(
    "/{webhook_id}",
    response_model=WebhookResponse,
    summary="Update Webhook",
)
async def update_webhook(
    webhook_id: str,
    body: WebhookUpdate,
) -> WebhookResponse:
    """Update a Webhook configuration."""
    from app.core.errors import NotFoundError

    doc = await WebhookService.update_webhook(
        webhook_id=webhook_id,
        name=body.name,
        url=body.url,
        events=body.events,
        api_key_id=body.api_key_id,
        status=body.status,
    )
    if doc is None:
        raise NotFoundError(code="WEBHOOK_NOT_FOUND", message="Webhook not found")
    return _doc_to_response(doc)


@router.delete(
    "/{webhook_id}",
    status_code=204,
    summary="Delete Webhook",
)
async def delete_webhook(webhook_id: str) -> None:
    """Delete a Webhook configuration."""
    from app.core.errors import NotFoundError

    deleted = await WebhookService.delete_webhook(webhook_id)
    if not deleted:
        raise NotFoundError(code="WEBHOOK_NOT_FOUND", message="Webhook not found")


@router.post(
    "/{webhook_id}/test",
    response_model=WebhookTestResult,
    summary="Test Webhook delivery",
)
async def test_webhook(webhook_id: str) -> WebhookTestResult:
    """Send a test event to the Webhook URL and return the result."""
    from app.core.errors import NotFoundError

    wh = await WebhookService.get_webhook_with_secret(webhook_id)
    if wh is None:
        raise NotFoundError(code="WEBHOOK_NOT_FOUND", message="Webhook not found")

    from app.services.webhook_delivery import deliver_with_retry

    test_payload = {
        "event": "test",
        "message": "This is a test webhook event from MEPER Agent",
        "timestamp": __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat(),
    }

    success = await deliver_with_retry(
        webhook_id=wh["_id"],
        url=wh["url"],
        secret=wh["secret"],
        event="test",
        payload=test_payload,
    )

    return WebhookTestResult(
        success=success,
        status_code=200 if success else None,
        error=None if success else "Delivery failed",
        attempts=1,
    )


@router.get(
    "/{webhook_id}/logs",
    response_model=list[WebhookDeliveryLogResponse],
    summary="List delivery logs",
)
async def list_delivery_logs(
    webhook_id: str,
    limit: int = 50,
) -> list[WebhookDeliveryLogResponse]:
    """List recent delivery logs for a Webhook."""
    logs = await WebhookService.list_delivery_logs(webhook_id, limit=limit)
    return [
        WebhookDeliveryLogResponse(
            id=log["_id"],
            webhook_id=log["webhook_id"],
            event=log["event"],
            url=log["url"],
            status_code=log.get("status_code"),
            success=log["success"],
            attempts=log["attempts"],
            error=log.get("error"),
            timestamp=log["timestamp"],
        )
        for log in logs
    ]
