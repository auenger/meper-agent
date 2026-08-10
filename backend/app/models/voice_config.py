"""Voice configuration — global singleton document (one per deployment).

Stored as a single doc with ``_id="voice_config"`` in the ``voice_config``
collection. The Agent Plan API key is AES-256-GCM encrypted via :mod:`app.core.crypto`
and masked in API responses (mirrors the Model / credential pattern).
"""

from __future__ import annotations

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


class VoiceConfig(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str = Field(default=CONFIG_DOC_ID, alias="_id")
    api_key_enc: str = ""  # Agent Plan dedicated API Key, AES-256-GCM encrypted
    asr: ASRConfig = Field(default_factory=ASRConfig)
    tts: TTSConfig = Field(default_factory=TTSConfig)
    audio: AudioConfig = Field(default_factory=AudioConfig)
    vad: VADConfig = Field(default_factory=VADConfig)
    last_test_success: bool | None = None
    last_test_at: str = ""
    updated_at: str = Field(default_factory=lambda: utc_now().isoformat())
