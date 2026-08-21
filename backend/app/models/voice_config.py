"""Voice configuration — global singleton document (one per deployment).

Stored as a single doc with ``_id="voice_config"`` in the ``voice_config``
collection. The Agent Plan API key is AES-256-GCM encrypted via :mod:`app.core.crypto`
and masked in API responses (mirrors the Model / credential pattern).
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.models.base import utc_now

CONFIG_DOC_ID = "voice_config"
COLLECTION = "voice_config"


class ASRConfig(BaseModel):
    resource_id: str = "volc.seedasr.sauc.duration"
    url: str = "wss://openspeech.bytedance.com/api/v3/plan/sauc/bigmodel_async"


class TTSConfig(BaseModel):
    resource_id: str = "seed-tts-2.0"
    url: str = "wss://openspeech.bytedance.com/api/v3/plan/tts/bidirection"
    voice_type: str = "zh_female_vv_uranus_bigtts"


class AudioConfig(BaseModel):
    input_rate: int = 16000
    output_rate: int = 24000


class VADConfig(BaseModel):
    mode: str = "energy"  # energy | silero | off
    threshold: float = 0.12
    silence_ms: int = 600


class ZhipuConfig(BaseModel):
    api_key_enc: str = ""
    asr_model: str = "glm-asr-2512"
    asr_url: str = "https://open.bigmodel.cn/api/paas/v4/audio/transcriptions"
    tts_model: str = "glm-tts"
    tts_url: str = "https://open.bigmodel.cn/api/paas/v4/audio/speech"
    voice_type: str = "tongtong"
    speed: float = 1.0
    volume: float = 1.0


class AliyunConfig(BaseModel):
    api_key_enc: str = ""
    asr_model: str = "qwen3-asr-flash"
    asr_url: str = (
        "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"
    )
    tts_model: str = "qwen3-tts-flash"
    tts_url: str = (
        "https://dashscope.aliyuncs.com/api/v1/services/aigc/"
        "multimodal-generation/generation"
    )
    voice_type: str = "Cherry"
    language_type: str = "Chinese"


class VoiceConfig(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str = Field(default=CONFIG_DOC_ID, alias="_id")
    active_provider: Literal["volcano", "zhipu", "aliyun"] = "volcano"
    api_key_enc: str = ""  # Agent Plan dedicated API Key, AES-256-GCM encrypted
    asr: ASRConfig = Field(default_factory=ASRConfig)
    tts: TTSConfig = Field(default_factory=TTSConfig)
    zhipu: ZhipuConfig = Field(default_factory=ZhipuConfig)
    aliyun: AliyunConfig = Field(default_factory=AliyunConfig)
    audio: AudioConfig = Field(default_factory=AudioConfig)
    vad: VADConfig = Field(default_factory=VADConfig)
    last_test_success: bool | None = None
    last_test_at: str = ""
    updated_at: str = Field(default_factory=lambda: utc_now().isoformat())
