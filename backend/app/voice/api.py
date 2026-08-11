"""Voice realtime WebSocket endpoint.

Auth reuses the notification WS token check (``verify_ws_token``) so no new
auth surface is introduced. One ``VoiceSession`` per connection; the receive
loop dispatches binary audio frames and JSON control messages.

Like ``app/api/v1/ws.py``: token via ``?token=xxx`` query param, reject with
4401 on failure, 30s heartbeat ping.
"""

from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect
from loguru import logger

from app.api.v1.ws import verify_ws_token
from app.core.security import get_current_user
from app.services.voice_config_service import VoiceConfigService
from app.voice.config import get_runtime_config
from app.voice.providers.volcano import VolcanoASRClient, VolcanoTTSClient
from app.voice.session import VoiceSession

router = APIRouter(tags=["voice"])

HEARTBEAT_INTERVAL = 30  # seconds


@router.get("/voice/status", dependencies=[Depends(get_current_user)])
async def voice_status() -> dict[str, bool]:
    """Expose voice availability without revealing any credential material."""
    cfg = await VoiceConfigService.get_config()
    return {"configured": bool(cfg and cfg.api_key_enc)}


@router.websocket("/voice/realtime")
async def voice_realtime(websocket: WebSocket, token: str = ""):
    """Realtime voice channel: binary PCM up/down + JSON control."""
    user_id = verify_ws_token(token)
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
        asr_factory=lambda: VolcanoASRClient(cfg.asr),
        tts_factory=lambda: VolcanoTTSClient(cfg.tts),
    )
    logger.info("voice_client_connected", user_id=user_id)

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
