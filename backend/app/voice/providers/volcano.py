"""Volcengine Agent Plan adapters for Seed ASR 2.0 and Seed TTS 2.0."""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import suppress
from typing import Any

from loguru import logger

from app.voice.config import ASRRuntime, TTSRuntime
from app.voice.providers.volcano_protocol import (
    EventType,
    MsgType,
    TTSMessage,
    build_asr_audio,
    build_asr_full_request,
    parse_asr_response,
    tts_event,
)

ASR_CHUNK_BYTES = 16000 * 2 * 200 // 1000  # PCM16 mono, 200 ms


class VolcanoASRClient:
    """Agent Plan streaming ASR over the v3 sequence + gzip protocol."""

    def __init__(self, asr: ASRRuntime) -> None:
        self._asr = asr
        self._ws: Any = None
        self._on_partial: Callable[[str], Awaitable[None]] | None = None
        self._on_final: Callable[[str], Awaitable[None]] | None = None
        self._on_end: Callable[[], Awaitable[None]] | None = None
        self._on_error: Callable[[str], Awaitable[None]] | None = None
        self._task: asyncio.Task | None = None
        self._closed = False
        self._sequence = 1
        self._last_partial = ""
        self._finalized_utterances: set[tuple[object, object, str]] = set()
        self._audio_buffer = bytearray()

    def bind(
        self, *, on_partial, on_final, on_utterance_end=None, on_error=None
    ) -> None:
        self._on_partial = on_partial
        self._on_final = on_final
        self._on_end = on_utterance_end
        self._on_error = on_error

    async def open(self) -> None:
        import websockets

        if not self._asr.api_key:
            raise RuntimeError("ASR 未配置 Agent Plan 专属 API Key")
        request_id = str(uuid.uuid4())
        headers = {
            "X-Api-Key": self._asr.api_key,
            "X-Api-Resource-Id": self._asr.resource_id,
            "X-Api-Request-Id": request_id,
            "X-Api-Connect-Id": request_id,
            "X-Api-Sequence": "-1",
        }
        self._ws = await websockets.connect(
            self._asr.url,
            additional_headers=headers,
            max_size=10 * 1024 * 1024,
        )
        payload = {
            "user": {"uid": "meper-agent"},
            "audio": {
                "format": "pcm",
                "codec": "raw",
                "rate": 16000,
                "bits": 16,
                "channel": 1,
            },
            "request": {
                "model_name": "bigmodel",
                "enable_itn": True,
                "enable_punc": True,
                "enable_ddc": True,
                "show_utterances": True,
                "enable_nonstream": False,
            },
        }
        await self._ws.send(build_asr_full_request(self._sequence, payload))
        self._sequence += 1
        raw = await asyncio.wait_for(self._ws.recv(), timeout=10)
        if not isinstance(raw, bytes):
            raise RuntimeError("ASR 初始化返回了非二进制响应")
        initial = parse_asr_response(raw)
        if initial.error_code:
            raise RuntimeError(_asr_error(initial.error_code, initial.payload))
        logger.info(
            "volcano_asr_connected",
            logid=self._ws.response.headers.get("x-tt-logid", ""),
        )
        self._task = asyncio.create_task(self._recv_loop())

    async def feed(self, pcm16_frame: bytes) -> None:
        if self._ws is None or self._closed or not pcm16_frame:
            return
        self._audio_buffer.extend(pcm16_frame)
        while len(self._audio_buffer) >= ASR_CHUNK_BYTES:
            chunk = bytes(self._audio_buffer[:ASR_CHUNK_BYTES])
            del self._audio_buffer[:ASR_CHUNK_BYTES]
            await self._send_audio(chunk)

    async def _send_audio(self, pcm16: bytes, *, is_last: bool = False) -> None:
        assert self._ws is not None
        await self._ws.send(build_asr_audio(self._sequence, pcm16, is_last=is_last))
        self._sequence += 1

    async def _recv_loop(self) -> None:
        try:
            async for raw in self._ws:
                if not isinstance(raw, bytes):
                    continue
                msg = parse_asr_response(raw)
                if msg.error_code:
                    raise RuntimeError(_asr_error(msg.error_code, msg.payload))
                if msg.payload:
                    await self._dispatch_result(msg.payload)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            if not self._closed:
                logger.warning("volcano_asr_recv_error", error=str(exc))
                if self._on_error:
                    await self._on_error(str(exc))

    async def _dispatch_result(self, payload: dict) -> None:
        result = payload.get("result")
        if not isinstance(result, dict):
            return
        utterances = result.get("utterances")
        emitted_final = False
        has_definite = False
        if isinstance(utterances, list):
            for utterance in utterances:
                if not isinstance(utterance, dict) or not utterance.get("definite"):
                    continue
                has_definite = True
                text = str(utterance.get("text") or "").strip()
                key = (utterance.get("start_time"), utterance.get("end_time"), text)
                if text and key not in self._finalized_utterances:
                    self._finalized_utterances.add(key)
                    emitted_final = True
                    self._last_partial = ""
                    if self._on_final:
                        await self._on_final(text)
                    if self._on_end:
                        await self._on_end()
        text = str(result.get("text") or "").strip()
        if (
            not emitted_final
            and not has_definite
            and text
            and text != self._last_partial
        ):
            self._last_partial = text
            if self._on_partial:
                await self._on_partial(text)

    async def close(self) -> None:
        if self._ws is not None:
            with suppress(Exception):
                await self._send_audio(bytes(self._audio_buffer), is_last=True)
        self._audio_buffer.clear()
        self._closed = True
        if self._task:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
        if self._ws:
            with suppress(Exception):
                await self._ws.close()
            self._ws = None


class VolcanoTTSClient:
    """Agent Plan bidirectional streaming TTS using one session per sentence."""

    def __init__(self, tts: TTSRuntime) -> None:
        self._tts = tts
        self._ws: Any = None
        self._stopped = False
        self._session_id = ""

    async def _ensure_open(self) -> None:
        if self._ws is not None:
            return
        import websockets

        if not self._tts.api_key:
            raise RuntimeError("TTS 未配置 Agent Plan 专属 API Key")
        headers = {
            "X-Api-Key": self._tts.api_key,
            "X-Api-Resource-Id": self._tts.resource_id,
            "X-Api-Connect-Id": str(uuid.uuid4()),
            "X-Control-Require-Usage-Tokens-Return": "*",
        }
        self._ws = await websockets.connect(
            self._tts.url,
            additional_headers=headers,
            max_size=10 * 1024 * 1024,
        )
        await self._ws.send(tts_event(EventType.START_CONNECTION))
        msg = await self._receive(timeout=10)
        if msg.type == MsgType.ERROR or msg.event != EventType.CONNECTION_STARTED:
            await self.close()
            raise RuntimeError(_tts_error(msg))

    async def probe(self) -> None:
        """Validate credentials, session events, the voice, and audio output."""
        audio_bytes = 0
        async for chunk in self.synth_stream("语音连接测试"):
            audio_bytes += len(chunk)
        if audio_bytes == 0:
            raise RuntimeError("TTS 连接成功，但没有返回音频数据")

    async def synth_stream(self, text: str) -> AsyncIterator[bytes]:
        await self._ensure_open()
        assert self._ws is not None
        self._stopped = False
        session_id = str(uuid.uuid4())
        self._session_id = session_id
        request = {
            "event": int(EventType.START_SESSION),
            "req_params": {
                "text": text,
                "speaker": self._tts.voice_type,
                "audio_params": {
                    "format": "pcm",
                    "sample_rate": 24000,
                },
            },
        }
        try:
            await self._ws.send(
                tts_event(
                    EventType.START_SESSION,
                    session_id=session_id,
                    payload=request,
                )
            )
            started = await self._receive(timeout=10)
            if (
                started.type == MsgType.ERROR
                or started.event != EventType.SESSION_STARTED
            ):
                raise RuntimeError(_tts_error(started))

            request["event"] = int(EventType.TASK_REQUEST)
            await self._ws.send(
                tts_event(
                    EventType.TASK_REQUEST,
                    session_id=session_id,
                    payload=request,
                )
            )
            await self._ws.send(
                tts_event(EventType.FINISH_SESSION, session_id=session_id)
            )
            while not self._stopped:
                msg = await self._receive()
                if msg.type == MsgType.ERROR or msg.event == EventType.SESSION_FAILED:
                    raise RuntimeError(_tts_error(msg))
                if msg.type == MsgType.AUDIO_ONLY_SERVER and msg.payload:
                    yield msg.payload
                if msg.event in {
                    EventType.SESSION_CANCELED,
                    EventType.SESSION_FINISHED,
                }:
                    break
        finally:
            # Do not let a canceled generator clear a newer session identifier.
            if self._session_id == session_id:
                self._session_id = ""

    async def _receive(self, timeout: float | None = None) -> TTSMessage:
        assert self._ws is not None
        raw = (
            await asyncio.wait_for(self._ws.recv(), timeout)
            if timeout
            else await self._ws.recv()
        )
        if not isinstance(raw, bytes):
            raise RuntimeError("TTS 返回了非二进制响应")
        return TTSMessage.from_bytes(raw)

    async def stop(self) -> None:
        self._stopped = True
        ws = self._ws
        session_id = self._session_id
        if ws is not None and session_id:
            with suppress(Exception):
                await ws.send(
                    tts_event(EventType.CANCEL_SESSION, session_id=session_id)
                )
        # A canceled session can leave SessionCanceled/audio frames queued on the
        # bidirectional socket. Reusing it makes the next synthesis consume the
        # old response as its SessionStarted response, so discard the connection.
        if ws is not None:
            with suppress(Exception):
                await ws.close()
        if self._ws is ws:
            self._ws = None
        if self._session_id == session_id:
            self._session_id = ""

    async def close(self) -> None:
        self._stopped = True
        if self._ws:
            with suppress(Exception):
                await self._ws.send(tts_event(EventType.FINISH_CONNECTION))
            with suppress(Exception):
                await self._ws.close()
            self._ws = None
        self._session_id = ""


def _asr_error(code: int, payload: dict | None) -> str:
    detail = json.dumps(payload, ensure_ascii=False) if payload else ""
    return f"火山 ASR 错误 {code}: {detail}".rstrip()


def _tts_error(msg: TTSMessage) -> str:
    detail = msg.payload.decode("utf-8", "replace") if msg.payload else ""
    code = f" {msg.error_code}" if msg.error_code else ""
    return f"火山 TTS 错误{code}: {detail}".rstrip()
