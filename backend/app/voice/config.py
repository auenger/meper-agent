"""Runtime voice config — decrypted, in-memory shape for a voice session.

Loaded from VoiceConfigService (DB singleton) at WS handshake. The API key is
decrypted once here; the VoiceSession holds this object for its lifetime.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


@dataclass
class ASRRuntime:
    api_key: str
    resource_id: str
    url: str
    provider: Literal["volcano", "zhipu", "aliyun"] = "volcano"


@dataclass
class TTSRuntime:
    api_key: str
    resource_id: str
    url: str
    voice_type: str
    provider: Literal["volcano", "zhipu", "aliyun"] = "volcano"
    speed: float = 1.0
    volume: float = 1.0
    language_type: str = ""


@dataclass
class VoiceRuntimeConfig:
    asr: ASRRuntime
    tts: TTSRuntime
    input_rate: int
    output_rate: int
    vad_mode: str
    vad_threshold: float
    vad_silence_ms: int
    tts_enabled: bool = True


async def get_runtime_config() -> VoiceRuntimeConfig:
    """Load + decrypt the voice config from DB. Raises if not configured."""
    from app.core.crypto import decrypt_secret
    from app.services.voice_config_service import VoiceConfigService

    cfg = await VoiceConfigService.get_config()
    if cfg is None:
        raise RuntimeError("语音未配置：请在「语音设置」页填写火山引擎凭证")

    def dec(enc: str) -> str:
        return decrypt_secret(enc) if enc else ""

    if cfg.active_provider == "zhipu":
        api_key = dec(cfg.zhipu.api_key_enc)
        asr = ASRRuntime(
            api_key=api_key,
            resource_id=cfg.zhipu.asr_model,
            url=cfg.zhipu.asr_url,
            provider="zhipu",
        )
        tts = TTSRuntime(
            api_key=api_key,
            resource_id=cfg.zhipu.tts_model,
            url=cfg.zhipu.tts_url,
            voice_type=cfg.zhipu.voice_type,
            provider="zhipu",
            speed=cfg.zhipu.speed,
            volume=cfg.zhipu.volume,
        )
    elif cfg.active_provider == "aliyun":
        api_key = dec(cfg.aliyun.api_key_enc)
        asr = ASRRuntime(
            api_key=api_key,
            resource_id=cfg.aliyun.asr_model,
            url=cfg.aliyun.asr_url,
            provider="aliyun",
        )
        tts = TTSRuntime(
            api_key=api_key,
            resource_id=cfg.aliyun.tts_model,
            url=cfg.aliyun.tts_url,
            voice_type=cfg.aliyun.voice_type,
            provider="aliyun",
            language_type=cfg.aliyun.language_type,
        )
    else:
        api_key = dec(cfg.api_key_enc)
        asr = ASRRuntime(
            api_key=api_key,
            resource_id=cfg.asr.resource_id,
            url=cfg.asr.url,
        )
        tts = TTSRuntime(
            api_key=api_key,
            resource_id=cfg.tts.resource_id,
            url=cfg.tts.url,
            voice_type=cfg.tts.voice_type,
        )

    return VoiceRuntimeConfig(
        asr=asr,
        tts=tts,
        input_rate=cfg.audio.input_rate,
        output_rate=cfg.audio.output_rate,
        vad_mode=cfg.vad.mode,
        vad_threshold=cfg.vad.threshold,
        vad_silence_ms=cfg.vad.silence_ms,
        tts_enabled=cfg.tts_enabled,
    )
