"""External voice endpoints — WS ticket issuance + availability status.

Both endpoints use the standard header-based API Key auth
(``auth_and_rate_limit``). The ticket exists because browser WebSocket
cannot carry custom headers: the embed client exchanges its long-lived
``af_live_`` key + X-User-Token for a 60s single-use ticket here, then
connects to ``/api/v1/voice/realtime?ticket=...`` — long-lived credentials
never appear in any URL (which production uvicorn/Caddy access logs record).
"""
from __future__ import annotations

from fastapi import APIRouter, Depends

from app.api.v1.ext import auth_and_rate_limit
from app.core.auth_apikey import ApiKeyPrincipal
from app.services.voice_config_service import VoiceConfigService
from app.services.voice_ticket_service import TICKET_TTL, issue_ticket

router = APIRouter(tags=["external-voice"])


@router.post("/voice/ticket")
async def create_voice_ticket(
    principal: ApiKeyPrincipal = Depends(auth_and_rate_limit),
) -> dict:
    """Mint a one-time ticket for the voice realtime WS."""
    ticket = await issue_ticket(principal)
    return {"ticket": ticket, "expires_in": TICKET_TTL}


@router.get("/voice/status")
async def ext_voice_status(
    principal: ApiKeyPrincipal = Depends(auth_and_rate_limit),
) -> dict[str, bool]:
    """Voice availability for embed clients (no credential material)."""
    return {"configured": await VoiceConfigService.is_configured()}
