"""Voice config API — get / save / connectivity-test (admin only).

Mirrors the Model CRUD pattern: admin-gated, tokens masked in responses,
a test endpoint that probes upstream connectivity.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends

from app.core.security import require_any_role
from app.models.voice_config import VoiceConfig
from app.schemas.voice_config import VoiceConfigResponse, VoiceConfigUpdate
from app.services.voice_config_service import VoiceConfigService

router = APIRouter(
    prefix="/voice/config",
    tags=["voice-config"],
    dependencies=[Depends(require_any_role("admin"))],
)


@router.get("", response_model=VoiceConfigResponse, summary="Get voice config")
async def get_voice_config() -> VoiceConfigResponse:
    cfg = await VoiceConfigService.get_config()
    if cfg is None:
        cfg = VoiceConfig()  # defaults (empty tokens)
    return VoiceConfigResponse(**VoiceConfigService.to_masked(cfg))


@router.put("", response_model=VoiceConfigResponse, summary="Save voice config")
async def save_voice_config(body: VoiceConfigUpdate) -> VoiceConfigResponse:
    cfg = await VoiceConfigService.save_config(body)
    return VoiceConfigResponse(**VoiceConfigService.to_masked(cfg))


@router.post("/test", summary="Test ASR/TTS connectivity")
async def test_voice_config() -> dict:
    """Probe upstream connectivity with the saved config.

    Configured-volcano probe runs once the v3 client is wired (volcano.py).
    Until then this validates that tokens are decryptable + present.
    """
    cfg = await VoiceConfigService.get_config()
    if cfg is None:
        return {"success": False, "message": "尚未配置语音凭证"}

    messages: list[str] = []
    # Decrypt-check + presence-check (no network yet — full probe comes with v3).
    for name, enc in (("ASR", cfg.asr.access_token_enc), ("TTS", cfg.tts.access_token_enc)):
        if not enc:
            messages.append(f"{name} 未配置 token")
            continue
        try:
            decrypt_secret(enc)
            messages.append(f"{name} token 可解密")
        except Exception as e:
            messages.append(f"{name} token 解密失败：{e}")
    if not cfg.asr.appid:
        messages.append("ASR 未配置 appid")
    if not cfg.tts.appid:
        messages.append("TTS 未配置 appid")

    success = all("失败" not in m and "未配置" not in m for m in messages)
    await VoiceConfigService.update_test_result(success)
    return {"success": success, "message": "; ".join(messages)}
