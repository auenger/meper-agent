"""Voice realtime WebSocket endpoint.

Auth supports two modes, chosen explicitly by which query param is present:

- ``?token=<JWT access>`` — internal users (notification-WS style, unchanged)
- ``?ticket=<one-time>``  — embed clients; the ticket was minted via
  ``POST /api/v1/ext/voice/ticket`` (standard header auth) so long-lived
  API Key / user-token material never appears in the URL.

No silent fallback: if ``token`` is present only JWT is tried (fail → 4401);
a ticket is only consulted when ``token`` is absent. One ``VoiceSession``
per connection; the receive loop dispatches binary audio frames and JSON
control messages. Like ``app/api/v1/ws.py``: reject with 4401 on failure,
30s heartbeat ping.
"""

from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect
from loguru import logger

from app.api.v1.ext import resolve_user_id
from app.api.v1.ws import verify_ws_token
from app.core.security import get_current_user
from app.services.voice_config_service import VoiceConfigService
from app.services.voice_ticket_service import consume_ticket
from app.voice.config import get_runtime_config
from app.voice.providers.factory import create_asr_client, create_tts_client
from app.voice.session import VoiceSession

router = APIRouter(tags=["voice"])

HEARTBEAT_INTERVAL = 30  # seconds


@router.get("/voice/status", dependencies=[Depends(get_current_user)])
async def voice_status() -> dict[str, bool]:
    """Expose voice availability without revealing any credential material."""
    return {"configured": await VoiceConfigService.is_configured()}


@router.websocket("/voice/realtime")
async def voice_realtime(websocket: WebSocket, token: str = "", ticket: str = ""):
    """Realtime voice channel: binary PCM up/down + JSON control."""
    principal = None
    if token:
        # JWT mode only — no fallback to ticket on failure.
        user_id = verify_ws_token(token)
    else:
        principal = await consume_ticket(ticket)
        user_id = resolve_user_id(principal) if principal else None
    if user_id is None:
        await websocket.accept()
        await websocket.close(code=4401, reason="Authentication failed")
        return

    await websocket.accept()
    try:
        cfg = await get_runtime_config()
    except Exception as e:
        await websocket.send_text(
            json.dumps({"type": "error", "content": str(e)}, ensure_ascii=False)
        )
        await websocket.close()
        return
    session = VoiceSession(
        websocket,
        user_id,
        cfg=cfg,
        asr_factory=lambda: create_asr_client(cfg),
        tts_factory=lambda: create_tts_client(cfg),
        principal=principal,
    )
    logger.info(
        "voice_client_connected",
        user_id=user_id,
        auth="api_key" if principal else "jwt",
    )

    heartbeat = asyncio.create_task(_heartbeat(websocket))
    try:
        while True:
            msg = await websocket.receive()
            if msg.get("bytes") is not None:
                await session.on_audio(msg["bytes"])
            elif msg.get("text") is not None:
                await session.on_control(msg["text"])
    except WebSocketDisconnect:
        logger.info("voice_client_disconnected", user_id=user_id)
    except Exception as e:
        logger.warning("voice_error", user_id=user_id, error=str(e))
    finally:
        heartbeat.cancel()
        await session.close()


async def _heartbeat(ws: WebSocket) -> None:
    """Keep the WS alive through proxies that idle-timeout idle connections."""
    while True:
        await asyncio.sleep(HEARTBEAT_INTERVAL)
        try:
            await ws.send_text('{"type": "ping"}')
        except Exception:
            break
