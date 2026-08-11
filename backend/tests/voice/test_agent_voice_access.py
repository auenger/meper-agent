from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from app.voice import protocol as P  # noqa: N812
from app.voice.config import ASRRuntime, TTSRuntime, VoiceRuntimeConfig
from app.voice.session import VoiceSession


class FakeWebSocket:
    def __init__(self) -> None:
        self.messages: list[dict[str, Any]] = []

    async def send_text(self, raw: str) -> None:
        self.messages.append(json.loads(raw))


class FakeASR:
    def __init__(self) -> None:
        self.opened = False

    def bind(self, **callbacks: Any) -> None:
        self.callbacks = callbacks

    async def open(self) -> None:
        self.opened = True

    async def feed(self, pcm16_frame: bytes) -> None:
        return None

    async def close(self) -> None:
        return None


class FakeTTS:
    async def stop(self) -> None:
        return None

    async def close(self) -> None:
        return None


def runtime_config() -> VoiceRuntimeConfig:
    return VoiceRuntimeConfig(
        asr=ASRRuntime(api_key="key", resource_id="asr", url="wss://asr"),
        tts=TTSRuntime(
            api_key="key", resource_id="tts", url="wss://tts", voice_type="voice"
        ),
        input_rate=16000,
        output_rate=24000,
        vad_mode="energy",
        vad_threshold=0.12,
        vad_silence_ms=600,
    )


@pytest.mark.asyncio
async def test_voice_start_rejects_agent_when_capability_is_disabled() -> None:
    ws = FakeWebSocket()
    created_asr: list[FakeASR] = []

    def make_asr() -> FakeASR:
        client = FakeASR()
        created_asr.append(client)
        return client

    session = VoiceSession(
        ws,  # type: ignore[arg-type]
        "user-1",
        cfg=runtime_config(),
        asr_factory=make_asr,  # type: ignore[arg-type]
        tts_factory=FakeTTS,  # type: ignore[arg-type]
    )

    with patch(
        "app.services.agent_service.AgentService.get_agent",
        new=AsyncMock(return_value={"_id": "agent-1", "voice_enabled": False}),
    ):
        await session.on_control(
            json.dumps({"type": P.CLIENT_VOICE_START, "agent_id": "agent-1"})
        )

    assert created_asr == []
    assert {
        "type": P.SERVER_ERROR,
        "content": "当前 Agent 未开启语音对话能力",
    } in ws.messages
    await session.close()


@pytest.mark.asyncio
async def test_voice_start_opens_asr_when_capability_is_enabled() -> None:
    ws = FakeWebSocket()
    created_asr: list[FakeASR] = []

    def make_asr() -> FakeASR:
        client = FakeASR()
        created_asr.append(client)
        return client

    session = VoiceSession(
        ws,  # type: ignore[arg-type]
        "user-1",
        cfg=runtime_config(),
        asr_factory=make_asr,  # type: ignore[arg-type]
        tts_factory=FakeTTS,  # type: ignore[arg-type]
    )

    with patch(
        "app.services.agent_service.AgentService.get_agent",
        new=AsyncMock(return_value={"_id": "agent-1", "voice_enabled": True}),
    ):
        await session.on_control(
            json.dumps({"type": P.CLIENT_VOICE_START, "agent_id": "agent-1"})
        )

    assert len(created_asr) == 1
    assert created_asr[0].opened is True
    await session.close()
