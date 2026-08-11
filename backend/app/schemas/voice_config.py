"""Voice config request/response schemas.

Request: ``api_key`` may be null/empty → "don't change" (keep existing
encrypted value). Response: the key is masked, never plaintext.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class ASRConfigUpdate(BaseModel):
    pass


class TTSConfigUpdate(BaseModel):
    voice_type: str = "zh_female_vv_uranus_bigtts"


class AudioConfigUpdate(BaseModel):
    input_rate: int = 16000
    output_rate: int = 24000


class VADConfigUpdate(BaseModel):
    mode: str = "energy"
    threshold: float = 0.12
    silence_ms: int = 600


class VoiceConfigUpdate(BaseModel):
    api_key: str | None = Field(default=None, description="留空=不修改")
    asr: ASRConfigUpdate = Field(default_factory=ASRConfigUpdate)
    tts: TTSConfigUpdate = Field(default_factory=TTSConfigUpdate)
    audio: AudioConfigUpdate = Field(default_factory=AudioConfigUpdate)
    vad: VADConfigUpdate = Field(default_factory=VADConfigUpdate)


class VoicePreviewRequest(BaseModel):
    """One-off TTS preview without changing the saved voice config."""

    voice_type: str = Field(min_length=1, max_length=200)
    text: str = Field(min_length=1, max_length=120)


# ── Response (masked tokens) ──────────────────────────────────────────
class ASRConfigResponse(BaseModel):
    resource_id: str
    url: str


class TTSConfigResponse(BaseModel):
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
    api_key_masked: str
    asr: ASRConfigResponse
    tts: TTSConfigResponse
    audio: AudioConfigResponse
    vad: VADConfigResponse
    last_test_success: bool | None
    last_test_at: str
