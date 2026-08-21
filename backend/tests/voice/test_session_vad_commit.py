from __future__ import annotations

import json
from typing import Any

import app.voice.session as voice_session_module
import pytest
from app.voice import protocol as P  # noqa: N812
from app.voice.config import ASRRuntime, TTSRuntime, VoiceRuntimeConfig
from app.voice.session import VoiceSession, markdown_to_speech


class FakeWebSocket:
    def __init__(self) -> None:
        self.messages: list[dict[str, Any]] = []

    async def send_text(self, raw: str) -> None:
        self.messages.append(json.loads(raw))


class FakeASR:
    def __init__(self) -> None:
        self.opened = False
        self.closed = False

    def bind(self, **callbacks: Any) -> None:
        self.callbacks = callbacks

    async def open(self) -> None:
        self.opened = True

    async def feed(self, pcm16_frame: bytes) -> None:
        pass

    async def close(self) -> None:
        self.closed = True


class FakeTTS:
    async def stop(self) -> None:
        pass

    async def close(self) -> None:
        pass


class FakeBatchASR(FakeASR):
    async def close(self) -> None:
        self.closed = True
        await self.callbacks["on_partial"]("批量识别结果")


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
async def test_vad_commit_finalizes_partial_and_rotates_asr() -> None:
    ws = FakeWebSocket()
    clients: list[FakeASR] = []

    def make_asr() -> FakeASR:
        client = FakeASR()
        clients.append(client)
        return client

    session = VoiceSession(
        ws,  # type: ignore[arg-type]
        "user-1",
        cfg=runtime_config(),
        asr_factory=make_asr,  # type: ignore[arg-type]
        tts_factory=FakeTTS,  # type: ignore[arg-type]
    )
    await session._start_listening()
    await session._on_asr_partial("你好")

    await session._commit_vad_utterance()

    assert len(clients) == 2
    assert clients[0].closed is True
    assert clients[1].opened is True
    assert {"type": P.SERVER_TRANSCRIPT_FINAL, "content": "你好"} in ws.messages
    await session.close()


@pytest.mark.asyncio
async def test_ptt_release_waits_for_batch_asr_result() -> None:
    ws = FakeWebSocket()
    session = VoiceSession(
        ws,  # type: ignore[arg-type]
        "user-1",
        cfg=runtime_config(),
        asr_factory=FakeBatchASR,  # type: ignore[arg-type]
        tts_factory=FakeTTS,  # type: ignore[arg-type]
    )
    session._ptt = True
    await session._start_listening()

    await session._release()

    assert {
        "type": P.SERVER_TRANSCRIPT_FINAL,
        "content": "批量识别结果",
    } in ws.messages
    await session.close()


@pytest.mark.parametrize(
    ("markdown", "speech"),
    [
        ("**重要提醒**：请保存。", "重要提醒：请保存。"),
        ("### 操作步骤\n- 点击 [保存](https://example.com)。", "操作步骤 点击 保存。"),
        ("使用 `uv sync`，详见 https://example.com/docs", "使用 uv sync，详见"),
        ("~~旧内容~~与 _新内容_", "旧内容与 新内容"),
    ],
)
def test_markdown_to_speech(markdown: str, speech: str) -> None:
    assert markdown_to_speech(markdown) == speech


@pytest.mark.asyncio
async def test_interrupt_clears_playback_after_server_turn_is_done() -> None:
    ws = FakeWebSocket()
    session = VoiceSession(
        ws,  # type: ignore[arg-type]
        "user-1",
        cfg=runtime_config(),
        asr_factory=FakeASR,  # type: ignore[arg-type]
        tts_factory=FakeTTS,  # type: ignore[arg-type]
    )
    session.state = P.STATE_LISTENING

    await session._handle_interrupt()

    assert {"type": P.SERVER_PLAYBACK_CLEAR} in ws.messages
    await session.close()


@pytest.mark.asyncio
async def test_partial_idle_timeout_commits_without_vad_end(monkeypatch) -> None:
    monkeypatch.setattr(voice_session_module, "PARTIAL_IDLE_COMMIT_SECONDS", 0)
    ws = FakeWebSocket()
    clients: list[FakeASR] = []

    def make_asr() -> FakeASR:
        client = FakeASR()
        clients.append(client)
        return client

    session = VoiceSession(
        ws,  # type: ignore[arg-type]
        "user-1",
        cfg=runtime_config(),
        asr_factory=make_asr,  # type: ignore[arg-type]
        tts_factory=FakeTTS,  # type: ignore[arg-type]
    )
    await session._start_listening()
    await session._on_asr_partial("延迟测试")
    commit_task = session._partial_idle_task
    assert commit_task is not None

    await commit_task

    assert len(clients) == 2
    assert {"type": P.SERVER_TRANSCRIPT_FINAL, "content": "延迟测试"} in ws.messages
    await session.close()
