"""Content-aware TTS: long / data-dense replies switch to LLM summary playback."""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from typing import Any

import app.engine.llm_factory as llm_factory_module
import pytest
from app.voice import protocol as P  # noqa: N812
from app.voice.config import ASRRuntime, TTSRuntime, VoiceRuntimeConfig
from app.voice.session import (
    SPOKEN_BUDGET_CHARS,
    SUMMARY_CUE,
    SUMMARY_FALLBACK,
    VoiceSession,
)
from app.voice.turn import TurnContext


class FakeWebSocket:
    def __init__(self) -> None:
        self.messages: list[dict[str, Any]] = []

    async def send_text(self, raw: str) -> None:
        self.messages.append(json.loads(raw))


class FakeASR:
    def bind(self, **callbacks: Any) -> None:
        pass

    async def open(self) -> None:
        pass

    async def feed(self, pcm16_frame: bytes) -> None:
        pass

    async def close(self) -> None:
        pass


class FakeTTS:
    async def stop(self) -> None:
        pass

    async def close(self) -> None:
        pass


class FakeSummaryLLM:
    def __init__(self, content: str | None = None, error: Exception | None = None):
        self.content = content
        self.error = error
        self.messages: list[list[Any]] = []

    async def ainvoke(self, messages: list[Any]) -> Any:
        self.messages.append(messages)
        if self.error:
            raise self.error
        return SimpleNamespace(content=self.content)


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


def make_session() -> tuple[FakeWebSocket, VoiceSession]:
    ws = FakeWebSocket()
    session = VoiceSession(
        ws,  # type: ignore[arg-type]
        "user-1",
        cfg=runtime_config(),
        asr_factory=FakeASR,  # type: ignore[arg-type]
        tts_factory=FakeTTS,  # type: ignore[arg-type]
    )
    return ws, session


def make_turn() -> TurnContext:
    turn = TurnContext(transcript="问题")
    turn.tts_queue = asyncio.Queue()
    return turn


async def drain(queue: asyncio.Queue[str | None]) -> list[str]:
    items = []
    while not queue.empty():
        item = queue.get_nowait()
        if item is not None:
            items.append(item)
    return items


def patch_llm(monkeypatch: pytest.MonkeyPatch, llm: FakeSummaryLLM) -> None:
    async def fake_get_llm_client(agent_doc: dict | None = None, **_: Any) -> Any:
        return llm

    monkeypatch.setattr(llm_factory_module, "get_llm_client", fake_get_llm_client)


@pytest.mark.asyncio
async def test_over_budget_switches_to_summary_mode_and_drops_later_deltas() -> None:
    _, session = make_session()
    turn = make_turn()
    chunk = "一二三四五。"  # 6 chars incl. punctuation
    feeds = SPOKEN_BUDGET_CHARS // len(chunk) + 5
    for _ in range(feeds):
        await session._feed_tts(chunk, turn)

    assert turn.summary_mode is True
    spoken = await drain(turn.tts_queue)
    assert SUMMARY_CUE in spoken
    # Budget roughly held: at most one sentence chunk past the limit was spoken.
    assert turn.spoken_chars <= SPOKEN_BUDGET_CHARS + len(chunk)

    queue_len = turn.tts_queue.qsize()
    await session._feed_tts("后续内容不再朗读。", turn)
    assert turn.tts_queue.qsize() == queue_len


@pytest.mark.asyncio
async def test_code_fence_triggers_summary_mode() -> None:
    _, session = make_session()
    turn = make_turn()
    await session._feed_tts("示例代码如下：", turn)
    await session._feed_tts("```python\nprint('hi')\n```", turn)

    assert turn.summary_mode is True
    assert turn.tts_buffer == ""
    assert SUMMARY_CUE in await drain(turn.tts_queue)


@pytest.mark.asyncio
async def test_table_separator_triggers_summary_mode() -> None:
    _, session = make_session()
    turn = make_turn()
    await session._feed_tts("先看表格：", turn)
    await session._feed_tts("| 名称 | 数量 |\n| --- | --- |\n", turn)

    assert turn.summary_mode is True
    assert SUMMARY_CUE in await drain(turn.tts_queue)


@pytest.mark.asyncio
async def test_speak_summary_uses_agent_llm_and_enqueues_sentences(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    llm = FakeSummaryLLM(content="**要点**：已完成部署。共两步。")
    patch_llm(monkeypatch, llm)
    _, session = make_session()
    turn = make_turn()
    turn.agent_doc = {"default_model": "model_x"}
    turn.reply_text = "很长很长的原始回复，包含代码块和表格。"

    await session._speak_summary(turn)

    # Markdown is stripped before the sentences hit the queue.
    assert await drain(turn.tts_queue) == ["要点：已完成部署。", "共两步。"]
    assert len(llm.messages) == 1
    assert llm.messages[0][1].content == turn.reply_text


@pytest.mark.asyncio
async def test_speak_summary_falls_back_when_llm_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    patch_llm(monkeypatch, FakeSummaryLLM(error=RuntimeError("boom")))
    _, session = make_session()
    turn = make_turn()
    turn.reply_text = "长回复"

    await session._speak_summary(turn)

    assert SUMMARY_FALLBACK in await drain(turn.tts_queue)


@pytest.mark.asyncio
async def test_short_reply_streams_normally() -> None:
    ws, session = make_session()
    turn = make_turn()
    for delta in ("今天天气不错。", "适合出去走走。"):
        await session._on_brain_event({"type": "text_delta", "content": delta}, turn)

    assert turn.summary_mode is False
    assert turn.reply_text == "今天天气不错。适合出去走走。"
    spoken = await drain(turn.tts_queue)
    assert spoken == ["今天天气不错。", "适合出去走走。"]
    assert SUMMARY_CUE not in spoken
    # UI protocol untouched: the client still gets every text_delta in full.
    deltas = [
        m["content"] for m in ws.messages if m["type"] == P.SERVER_AGENT_TEXT_DELTA
    ]
    assert deltas == ["今天天气不错。", "适合出去走走。"]
