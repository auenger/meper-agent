from __future__ import annotations

import io
import wave

import pytest
from app.api.v1 import voice_config
from app.schemas.voice_config import VoicePreviewRequest
from app.voice.config import ASRRuntime, TTSRuntime, VoiceRuntimeConfig

pcm16_to_wav = voice_config.pcm16_to_wav


def test_pcm16_to_wav_wraps_browser_playable_audio() -> None:
    pcm = b"\x00\x00\x01\x00\xff\xff"

    encoded = pcm16_to_wav(pcm)

    with wave.open(io.BytesIO(encoded), "rb") as wav_file:
        assert wav_file.getnchannels() == 1
        assert wav_file.getsampwidth() == 2
        assert wav_file.getframerate() == 24000
        assert wav_file.readframes(wav_file.getnframes()) == pcm


@pytest.mark.asyncio
async def test_preview_uses_temporary_voice_without_saving(monkeypatch) -> None:
    runtime = VoiceRuntimeConfig(
        asr=ASRRuntime(api_key="key", resource_id="asr", url="wss://asr.test"),
        tts=TTSRuntime(
            api_key="key",
            resource_id="seed-tts-2.0",
            url="wss://tts.test",
            voice_type="saved-voice",
        ),
        input_rate=16000,
        output_rate=24000,
        vad_mode="energy",
        vad_threshold=0.12,
        vad_silence_ms=600,
    )
    received_voice_types: list[str] = []

    async def fake_runtime() -> VoiceRuntimeConfig:
        return runtime

    class FakeTTSClient:
        def __init__(self, tts: TTSRuntime) -> None:
            received_voice_types.append(tts.voice_type)

        async def synth_stream(self, text: str):
            assert text == "试听内容"
            yield b"\x00\x00\x01\x00"

        async def close(self) -> None:
            return None

    monkeypatch.setattr(voice_config, "get_runtime_config", fake_runtime)
    monkeypatch.setattr(
        voice_config,
        "create_tts_client",
        lambda cfg: FakeTTSClient(cfg.tts),
    )

    response = await voice_config.preview_voice(
        VoicePreviewRequest(voice_type="preview-voice", text="试听内容")
    )

    assert response.media_type == "audio/wav"
    assert response.headers["cache-control"] == "no-store"
    assert received_voice_types == ["preview-voice"]
    assert runtime.tts.voice_type == "saved-voice"
