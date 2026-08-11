"""Voice config API — get / save / connectivity-test (admin only).

Mirrors the Model CRUD pattern: admin-gated, API key masked in responses,
a test endpoint that probes upstream connectivity.
"""

from __future__ import annotations

import io
import wave
from dataclasses import replace

from fastapi import APIRouter, Depends, HTTPException, Response

from app.core.security import require_any_role
from app.models.voice_config import VoiceConfig
from app.schemas.voice_config import (
    VoiceConfigResponse,
    VoiceConfigUpdate,
    VoicePreviewRequest,
)
from app.services.voice_config_service import VoiceConfigService
from app.voice.config import get_runtime_config
from app.voice.providers.volcano import VolcanoASRClient, VolcanoTTSClient

router = APIRouter(
    prefix="/voice/config",
    tags=["voice-config"],
    dependencies=[Depends(require_any_role("admin"))],
)


@router.get("", response_model=VoiceConfigResponse, summary="Get voice config")
async def get_voice_config() -> VoiceConfigResponse:
    cfg = await VoiceConfigService.get_config()
    if cfg is None:
        cfg = VoiceConfig()  # defaults (empty API key)
    return VoiceConfigResponse(**VoiceConfigService.to_masked(cfg))


@router.put("", response_model=VoiceConfigResponse, summary="Save voice config")
async def save_voice_config(body: VoiceConfigUpdate) -> VoiceConfigResponse:
    cfg = await VoiceConfigService.save_config(body)
    return VoiceConfigResponse(**VoiceConfigService.to_masked(cfg))


@router.post("/test", summary="Test ASR/TTS connectivity")
async def test_voice_config() -> dict:
    """Validate the saved key with real Agent Plan ASR/TTS handshakes."""
    cfg = await VoiceConfigService.get_config()
    if cfg is None:
        return {"success": False, "message": "尚未配置语音凭证"}
    if not cfg.api_key_enc:
        return {"success": False, "message": "未配置 Agent Plan 专属 API Key"}

    messages: list[str] = []
    success = True
    asr = None
    tts = None
    try:
        runtime = await get_runtime_config()
        asr = VolcanoASRClient(runtime.asr)
        await asr.open()
        messages.append("ASR 连接成功")
    except Exception as exc:
        success = False
        messages.append(f"ASR 连接失败：{exc}")
    finally:
        if asr is not None:
            await asr.close()
    try:
        runtime = await get_runtime_config()
        tts = VolcanoTTSClient(runtime.tts)
        await tts.probe()
        messages.append("TTS 连接成功")
    except Exception as exc:
        success = False
        messages.append(f"TTS 连接失败：{exc}")
    finally:
        if tts is not None:
            await tts.close()

    await VoiceConfigService.update_test_result(success)
    return {"success": success, "message": "; ".join(messages)}


def pcm16_to_wav(pcm: bytes, *, sample_rate: int = 24000) -> bytes:
    """Wrap mono PCM16 returned by Seed TTS in a browser-playable WAV file."""
    output = io.BytesIO()
    with wave.open(output, "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(sample_rate)
        wav_file.writeframes(pcm)
    return output.getvalue()


@router.post("/preview", summary="Preview a TTS voice")
async def preview_voice(body: VoicePreviewRequest) -> Response:
    """Synthesize a short sample with a temporary voice_type selection."""
    voice_type = body.voice_type.strip()
    text = body.text.strip()
    if not voice_type or not text:
        raise HTTPException(status_code=422, detail="音色和试听文本不能为空")

    tts: VolcanoTTSClient | None = None
    try:
        runtime = await get_runtime_config()
        tts = VolcanoTTSClient(replace(runtime.tts, voice_type=voice_type))
        pcm = bytearray()
        async for chunk in tts.synth_stream(text):
            pcm.extend(chunk)
        if not pcm:
            raise RuntimeError("TTS 未返回试听音频")
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"试听生成失败：{exc}") from exc
    finally:
        if tts is not None:
            await tts.close()

    return Response(
        content=pcm16_to_wav(bytes(pcm)),
        media_type="audio/wav",
        headers={"Cache-Control": "no-store"},
    )
