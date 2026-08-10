"""Runtime voice config — decrypted, in-memory shape for a voice session.

Loaded from VoiceConfigService (DB singleton) at WS handshake. The API key is
decrypted once here; the VoiceSession holds this object for its lifetime.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ASRRuntime:
    api_key: str
    resource_id: str
    url: str


@dataclass
class TTSRuntime:
    api_key: str
    resource_id: str
    url: str
    voice_type: str


@dataclass
class VoiceRuntimeConfig:
    asr: ASRRuntime
    tts: TTSRuntime
    input_rate: int
    output_rate: int
    vad_mode: str
    vad_threshold: float
    vad_silence_ms: int


async def get_runtime_config() -> VoiceRuntimeConfig:
    """Load + decrypt the voice config from DB. Raises if not configured."""
    from app.core.crypto import decrypt_secret
    from app.services.voice_config_service import VoiceConfigService

    cfg = await VoiceConfigService.get_config()
    if cfg is None:
        raise RuntimeError("语音未配置：请在「语音设置」页填写火山引擎凭证")

    def dec(enc: str) -> str:
        return decrypt_secret(enc) if enc else ""

    return VoiceRuntimeConfig(
        asr=ASRRuntime(
            api_key=dec(cfg.api_key_enc),
            resource_id=cfg.asr.resource_id,
            url=cfg.asr.url,
        ),
        tts=TTSRuntime(
            api_key=dec(cfg.api_key_enc),
            resource_id=cfg.tts.resource_id,
            url=cfg.tts.url,
            voice_type=cfg.tts.voice_type,
        ),
        input_rate=cfg.audio.input_rate,
        output_rate=cfg.audio.output_rate,
        vad_mode=cfg.vad.mode,
        vad_threshold=cfg.vad.threshold,
        vad_silence_ms=cfg.vad.silence_ms,
    )
