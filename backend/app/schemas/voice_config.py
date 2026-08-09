"""Voice config request/response schemas.

Request: ``access_token`` may be null/empty → "don't change" (keep existing
encrypted value). Response: tokens are masked, never plaintext.
"""
from __future__ import annotations

from pydantic import BaseModel, Field

_TTS_URL = "wss://openspeech.bytedance.com/api/v3/plan/tts/bidirection"
_ASR_URL = "wss://openspeech.bytedance.com/api/v3/sauc/bigmodel"


class ASRConfigUpdate(BaseModel):
    appid: str = ""
    access_token: str | None = Field(default=None, description="留空=不修改")
    resource_id: str = "volc.seedasr.sauc.duration"
    url: str = _ASR_URL


class TTSConfigUpdate(BaseModel):
    appid: str = ""
    access_token: str | None = Field(default=None, description="留空=不修改")
    resource_id: str = "seed-tts-2.0"
    url: str = _TTS_URL
    voice_type: str = "zh_female_wanwanxiaohe_moon_bigtts"


class AudioConfigUpdate(BaseModel):
    input_rate: int = 16000
    output_rate: int = 24000


class VADConfigUpdate(BaseModel):
    mode: str = "energy"
    threshold: float = 0.12
    silence_ms: int = 600


class VoiceConfigUpdate(BaseModel):
    asr: ASRConfigUpdate = Field(default_factory=ASRConfigUpdate)
    tts: TTSConfigUpdate = Field(default_factory=TTSConfigUpdate)
    audio: AudioConfigUpdate = Field(default_factory=AudioConfigUpdate)
    vad: VADConfigUpdate = Field(default_factory=VADConfigUpdate)


# ── Response (masked tokens) ──────────────────────────────────────────
class ASRConfigResponse(BaseModel):
    appid: str
    access_token_masked: str
    resource_id: str
    url: str


class TTSConfigResponse(BaseModel):
    appid: str
    access_token_masked: str
    resource_id: str
    url: str
    voice_type: str


class AudioConfigResponse(BaseModel):
    input_rate: int
    output_rate: int


class VADConfigResponse(BaseModel):
    mode: str
    threshold: float
    silence_ms: int


class VoiceConfigResponse(BaseModel):
    asr: ASRConfigResponse
    tts: TTSConfigResponse
    audio: AudioConfigResponse
    vad: VADConfigResponse
    last_test_success: bool | None
    last_test_at: str
