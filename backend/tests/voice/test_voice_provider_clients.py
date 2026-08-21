from __future__ import annotations

import base64
import io
import json
import wave
from unittest.mock import AsyncMock

import pytest
from app.voice.config import ASRRuntime, TTSRuntime, VoiceRuntimeConfig
from app.voice.providers.aliyun import AliyunASRClient, AliyunTTSClient
from app.voice.providers.factory import create_asr_client, create_tts_client
from app.voice.providers.volcano import VolcanoASRClient, VolcanoTTSClient
from app.voice.providers.zhipu import ZhipuASRClient, ZhipuTTSClient


def runtime(provider: str = "volcano") -> VoiceRuntimeConfig:
    return VoiceRuntimeConfig(
        asr=ASRRuntime(
            api_key="key",
            resource_id="asr-model",
            url="https://example.test/asr",
            provider=provider,  # type: ignore[arg-type]
        ),
        tts=TTSRuntime(
            api_key="key",
            resource_id="tts-model",
            url="https://example.test/tts",
            voice_type="voice",
            provider=provider,  # type: ignore[arg-type]
        ),
        input_rate=16000,
        output_rate=24000,
        vad_mode="energy",
        vad_threshold=0.12,
        vad_silence_ms=600,
    )


@pytest.mark.parametrize(
    ("provider", "asr_type", "tts_type"),
    [
        ("volcano", VolcanoASRClient, VolcanoTTSClient),
        ("zhipu", ZhipuASRClient, ZhipuTTSClient),
        ("aliyun", AliyunASRClient, AliyunTTSClient),
    ],
)
def test_factory_selects_provider(provider, asr_type, tts_type) -> None:
    cfg = runtime(provider)

    assert isinstance(create_asr_client(cfg), asr_type)
    assert isinstance(create_tts_client(cfg), tts_type)


@pytest.mark.asyncio
@pytest.mark.parametrize("client_type", [ZhipuASRClient, AliyunASRClient])
async def test_batch_asr_emits_text_on_close(monkeypatch, client_type) -> None:
    client = client_type(runtime("zhipu").asr)
    partial = AsyncMock()

    async def noop(*args) -> None:
        return None

    client.bind(on_partial=partial, on_final=noop, on_error=noop)
    monkeypatch.setattr(client, "_transcribe", lambda pcm: "你好")

    await client.open()
    await client.feed(b"\x00\x00\x01\x00")
    await client.close()

    partial.assert_awaited_once_with("你好")


@pytest.mark.asyncio
async def test_zhipu_tts_payload(monkeypatch) -> None:
    captured: dict = {}

    def fake_read_response(request, *, timeout) -> bytes:
        captured["body"] = json.loads(request.data.decode("utf-8"))
        return b"\x00\x00"

    monkeypatch.setattr("app.voice.providers.zhipu._read_response", fake_read_response)
    cfg = runtime("zhipu")
    cfg.tts.speed = 1.2
    cfg.tts.volume = 0.8

    chunks = [chunk async for chunk in ZhipuTTSClient(cfg.tts).synth_stream("你好")]

    assert chunks == [b"\x00\x00"]
    assert captured["body"] == {
        "model": "tts-model",
        "input": "你好",
        "voice": "voice",
        "response_format": "pcm",
        "speed": 1.2,
        "volume": 0.8,
    }


@pytest.mark.asyncio
async def test_aliyun_tts_decodes_wav(monkeypatch) -> None:
    output = io.BytesIO()
    with wave.open(output, "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(24000)
        wav_file.writeframes(b"\x01\x00\x02\x00")
    response = {
        "output": {"audio": {"data": base64.b64encode(output.getvalue()).decode()}}
    }
    captured: dict = {}

    def fake_read_response(request, *, timeout) -> bytes:
        captured["body"] = json.loads(request.data.decode("utf-8"))
        return json.dumps(response).encode()

    monkeypatch.setattr(
        "app.voice.providers.aliyun._read_response",
        fake_read_response,
    )

    chunks = [
        chunk async for chunk in AliyunTTSClient(runtime("aliyun").tts).synth_stream("你好")
    ]

    assert chunks == [b"\x01\x00\x02\x00"]
    assert captured["body"] == {
        "model": "tts-model",
        "input": {
            "text": "你好",
            "voice": "voice",
            "language_type": "Auto",
        },
    }
