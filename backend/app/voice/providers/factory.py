"""Create voice clients for the active runtime provider."""

from __future__ import annotations

from app.voice.config import VoiceRuntimeConfig
from app.voice.providers.aliyun import AliyunASRClient, AliyunTTSClient
from app.voice.providers.base import STTProvider, TTSProvider
from app.voice.providers.volcano import VolcanoASRClient, VolcanoTTSClient
from app.voice.providers.zhipu import ZhipuASRClient, ZhipuTTSClient


def create_asr_client(cfg: VoiceRuntimeConfig) -> STTProvider:
    if cfg.asr.provider == "aliyun":
        return AliyunASRClient(cfg.asr)
    if cfg.asr.provider == "zhipu":
        return ZhipuASRClient(cfg.asr)
    return VolcanoASRClient(cfg.asr)


def create_tts_client(cfg: VoiceRuntimeConfig) -> TTSProvider:
    if cfg.tts.provider == "aliyun":
        return AliyunTTSClient(cfg.tts)
    if cfg.tts.provider == "zhipu":
        return ZhipuTTSClient(cfg.tts)
    return VolcanoTTSClient(cfg.tts)
