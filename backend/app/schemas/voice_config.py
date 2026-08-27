"""Voice config request/response schemas.

Request: ``api_key`` may be null/empty → "don't change" (keep existing
encrypted value). Response: the key is masked, never plaintext.
"""

from __future__ import annotations

from typing import Literal

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


class ZhipuConfigUpdate(BaseModel):
    api_key: str | None = Field(default=None, description="留空=不修改")
    asr_model: str = Field(default="glm-asr-2512", min_length=1, max_length=200)
    asr_url: str = Field(
        default="https://open.bigmodel.cn/api/paas/v4/audio/transcriptions",
        min_length=1,
        max_length=2000,
    )
    tts_model: str = Field(default="glm-tts", min_length=1, max_length=200)
    tts_url: str = Field(
        default="https://open.bigmodel.cn/api/paas/v4/audio/speech",
        min_length=1,
        max_length=2000,
    )
    voice_type: str = Field(default="tongtong", min_length=1, max_length=200)
    speed: float = Field(default=1.0, ge=0.5, le=2.0)
    volume: float = Field(default=1.0, ge=0.0, le=2.0)


class AliyunConfigUpdate(BaseModel):
    api_key: str | None = Field(default=None, description="留空=不修改")
    asr_model: str = Field(default="qwen3-asr-flash", min_length=1, max_length=200)
    asr_url: str = Field(
        default="https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions",
        min_length=1,
        max_length=2000,
    )
    tts_model: str = Field(default="qwen3-tts-flash", min_length=1, max_length=200)
    tts_url: str = Field(
        default=(
            "https://dashscope.aliyuncs.com/api/v1/services/aigc/"
            "multimodal-generation/generation"
        ),
        min_length=1,
        max_length=2000,
    )
    voice_type: str = Field(default="Cherry", min_length=1, max_length=200)
    language_type: str = Field(default="Chinese", min_length=1, max_length=50)


class VoiceConfigUpdate(BaseModel):
    active_provider: Literal["volcano", "zhipu", "aliyun"] = "volcano"
    tts_enabled: bool | None = None
    api_key: str | None = Field(default=None, description="留空=不修改")
    asr: ASRConfigUpdate = Field(default_factory=ASRConfigUpdate)
    tts: TTSConfigUpdate = Field(default_factory=TTSConfigUpdate)
    zhipu: ZhipuConfigUpdate = Field(default_factory=ZhipuConfigUpdate)
    aliyun: AliyunConfigUpdate = Field(default_factory=AliyunConfigUpdate)
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


class ZhipuConfigResponse(BaseModel):
    api_key_masked: str
    asr_model: str
    asr_url: str
    tts_model: str
    tts_url: str
    voice_type: str
    speed: float
    volume: float


class AliyunConfigResponse(BaseModel):
    api_key_masked: str
    asr_model: str
    asr_url: str
    tts_model: str
    tts_url: str
    voice_type: str
    language_type: str


class VoiceConfigResponse(BaseModel):
    active_provider: Literal["volcano", "zhipu", "aliyun"]
    tts_enabled: bool
    api_key_masked: str
    asr: ASRConfigResponse
    tts: TTSConfigResponse
    zhipu: ZhipuConfigResponse
    aliyun: AliyunConfigResponse
    audio: AudioConfigResponse
    vad: VADConfigResponse
    last_test_success: bool | None
    last_test_at: str
