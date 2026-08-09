"""Volcano Engine (豆包 2.0 / 火山方舟) v3 streaming ASR + TTS adapters.

Configured per-session via runtime config objects (ASRRuntime / TTSRuntime),
loaded from the voice_config DB doc — no settings/env reads here.

v3 protocol = event-based binary frames:
  frame = header(4B) + payload_size(4B big-endian) + payload(JSON event or raw audio)
  byte0: (protocol_version<<4)|header_size   = 0x11
  byte1: (message_type<<4)|msg_type_specific
  byte2: (serialization<<4)|compression
  byte3: reserved = 0x00

TTS (BidirectionalTTS namespace):
  StartConnection (auth) → ServerACK → TaskRequest (voice_type/text) →
  Finished → audio frames → finish event

⚠️ The exact byte layout + event JSON field names are inferred from public
docs/examples; the canonical封装 is火山's ``protocols.py``
(https://www.volcengine.com/docs/82379/2516286) which could not be fetched
here. If a real credential fails, calibrate ``_frame`` / ``_parse_response``
and the ``_build_*`` event dicts in THIS file only — VoiceSession / config /
frontend are unaffected.
"""
from __future__ import annotations

import asyncio
import json
import struct
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable

from loguru import logger

from app.voice.config import ASRRuntime, TTSRuntime

# message_type (high nibble of byte1)
_MT_FULL_CLIENT_REQUEST = 0x1   # JSON event (config / task / control)
_MT_AUDIO_ONLY_REQUEST = 0x2    # raw PCM audio frame
_MT_SERVER_RESPONSE = 0x9       # server → client (ASR result / TTS audio)
_MT_SERVER_ERROR = 0xF
# serialization (high nibble of byte2)
_SER_JSON = 0x1
_SER_RAW = 0x0
# v3 event namespaces
_NS_TTS = "BidirectionalTTS"


def _frame(message_type: int, payload: bytes = b"", serialization: int = _SER_JSON) -> bytes:
    header = bytes([0x11, (message_type << 4) | 0x0, (serialization << 4) | 0x0, 0x00])
    if not payload:
        return header
    return header + struct.pack(">I", len(payload)) + payload


def _parse_response(data: bytes) -> tuple[int, bytes | None, bytes | None]:
    """Parse a server frame → (message_type, json_payload, audio_bytes)."""
    if len(data) < 4:
        return (-1, None, None)
    message_type = (data[1] >> 4) & 0xF
    serialization = (data[2] >> 4) & 0xF
    pos = 4
    json_payload: bytes | None = None
    audio: bytes | None = None
    while pos + 4 <= len(data):
        (size,) = struct.unpack(">I", data[pos:pos + 4])
        pos += 4
        if size == 0 or pos + size > len(data):
            break
        chunk = data[pos:pos + size]
        pos += size
        if json_payload is None and serialization == _SER_JSON:
            json_payload = chunk
        else:
            audio = chunk if audio is None else audio + chunk
    return message_type, json_payload, audio


def _event(event: str, namespace: str, **fields) -> bytes:
    """Serialize a v3 event dict to a full-client-request frame payload."""
    payload = json.dumps(
        {"event": event, "namespace": namespace, **fields}, ensure_ascii=False
    ).encode("utf-8")
    return _frame(_MT_FULL_CLIENT_REQUEST, payload)


class VolcanoASRClient:
    """Streaming ASR over the v3 bigmodel protocol."""

    def __init__(self, asr: ASRRuntime) -> None:
        self._asr = asr
        self._ws = None
        self._on_partial: Callable[[str], Awaitable[None]] | None = None
        self._on_final: Callable[[str], Awaitable[None]] | None = None
        self._on_end: Callable[[], Awaitable[None]] | None = None
        self._task: asyncio.Task | None = None
        self._closed = False

    def bind(self, *, on_partial, on_final, on_utterance_end=None) -> None:
        self._on_partial = on_partial
        self._on_final = on_final
        self._on_end = on_utterance_end

    async def open(self) -> None:
        import websockets

        if not self._asr.appid or not self._asr.access_token:
            raise RuntimeError("ASR 凭证未配置（appid / access_token）")
        headers = {
            "X-Api-App-Id": self._asr.appid,
            "X-Api-Access-Token": self._asr.access_token,
            "X-Api-Resource-Id": self._asr.resource_id,
            "X-Api-Connect-Id": uuid.uuid4().hex,
        }
        self._ws = await websockets.connect(self._asr.url, additional_headers=headers, max_size=None)
        # v3 ASR config frame (TODO: confirm exact fields vs protocols.py).
        config = _event(
            "StartConnection", "BidirectionalASR",
            user={"uid": "meper-agent"},
            audio={"format": "pcm", "sample_rate": 16000, "channels": 1, "bits": 16},
            request={"model": self._asr.resource_id, "enable_punc": True, "result_type": "full"},
        )
        await self._ws.send(config)
        self._task = asyncio.create_task(self._recv_loop())

    async def feed(self, pcm16_frame: bytes) -> None:
        if self._ws is None or self._closed:
            return
        await self._ws.send(_frame(_MT_AUDIO_ONLY_REQUEST, pcm16_frame, serialization=_SER_RAW))

    async def _recv_loop(self) -> None:
        try:
            async for raw in self._ws:
                _mt, payload, _audio = _parse_response(raw)
                if payload is None:
                    continue
                try:
                    msg = json.loads(payload)
                except json.JSONDecodeError:
                    continue
                result = msg.get("result") if isinstance(msg.get("result"), dict) else msg
                text = (result.get("text") or "").strip() if isinstance(result, dict) else ""
                is_final = bool(
                    (isinstance(result, dict) and (result.get("is_last") or result.get("utterances")))
                    or msg.get("is_last")
                )
                if not text:
                    continue
                if is_final:
                    if self._on_final:
                        await self._on_final(text)
                    if self._on_end:
                        await self._on_end()
                elif self._on_partial:
                    await self._on_partial(text)
        except Exception as e:
            if not self._closed:
                logger.warning("volcano_asr_recv_error", error=str(e))

    async def close(self) -> None:
        self._closed = True
        if self._task:
            self._task.cancel()
        if self._ws:
            try:
                await self._ws.close()
            except Exception:
                pass


class VolcanoTTSClient:
    """Streaming TTS over the v3 BidirectionalTTS protocol."""

    def __init__(self, tts: TTSRuntime) -> None:
        self._tts = tts
        self._ws = None
        self._stopped = False

    async def _ensure_open(self) -> None:
        if self._ws is not None:
            return
        import websockets

        if not self._tts.appid or not self._tts.access_token:
            raise RuntimeError("TTS 凭证未配置（appid / access_token）")
        headers = {
            "X-Api-App-Id": self._tts.appid,
            "X-Api-Access-Token": self._tts.access_token,
            "X-Api-Resource-Id": self._tts.resource_id,
            "X-Api-Connect-Id": uuid.uuid4().hex,
        }
        self._ws = await websockets.connect(self._tts.url, additional_headers=headers, max_size=None)
        # StartConnection (auth handshake). TODO: confirm header/cluster fields.
        await self._ws.send(_event(
            "StartConnection", _NS_TTS,
            user={"uid": "meper-agent"},
            header={"appid": self._tts.appid, "token": self._tts.access_token},
        ))
        # Wait for ServerACK (best-effort: first frame).
        try:
            await asyncio.wait_for(self._ws.recv(), timeout=5)
        except asyncio.TimeoutError:
            logger.warning("volcano_tts_ack_timeout")

    async def synth_stream(self, text: str) -> AsyncIterator[bytes]:
        await self._ensure_open()
        self._stopped = False
        # TaskRequest: voice_type + text + audio format. TODO: confirm req_params.
        await self._ws.send(_event(
            "TaskRequest", _NS_TTS,
            req_params={
                "speaker": {"voice_type": self._tts.voice_type},
                "audio": {"format": "pcm", "sample_rate": 24000},
                "model": self._tts.resource_id,
            },
            payload={"text": text},
        ))
        # Signal input finished so the server flushes remaining audio.
        await self._ws.send(_event("Finished", _NS_TTS))
        async for raw in self._ws:
            if self._stopped:
                break
            mt, jpayload, audio = _parse_response(raw)
            if mt == _MT_SERVER_ERROR or (jpayload and _tts_is_error(jpayload)):
                logger.warning("volcano_tts_error", payload=jpayload)
                break
            if audio:
                yield audio
            if jpayload and _tts_is_done(jpayload):
                break

    async def stop(self) -> None:
        self._stopped = True

    async def close(self) -> None:
        self._stopped = True
        if self._ws:
            try:
                await self._ws.close()
            except Exception:
                pass
            self._ws = None


def _tts_is_done(payload: bytes) -> bool:
    try:
        d = json.loads(payload)
    except json.JSONDecodeError:
        return False
    ev = d.get("event")
    return ev in ("ServerACK", "Finished") or d.get("code") == 3000 or d.get("is_last") is True


def _tts_is_error(payload: bytes) -> bool:
    try:
        d = json.loads(payload)
    except json.JSONDecodeError:
        return False
    code = d.get("code")
    return code not in (None, 0, 3000)
