"""Zhipu GLM batch ASR and HTTP TTS adapters."""

from __future__ import annotations

import asyncio
import io
import json
import urllib.error
import urllib.request
import uuid
import wave
from collections.abc import Awaitable, Callable

from app.voice.config import ASRRuntime, TTSRuntime


class ZhipuASRClient:
    """Buffer one PTT utterance and submit it when the stream closes."""

    def __init__(self, asr: ASRRuntime) -> None:
        self._asr = asr
        self._audio = bytearray()
        self._on_partial: Callable[[str], Awaitable[None]] | None = None
        self._on_error: Callable[[str], Awaitable[None]] | None = None
        self._closed = False

    def bind(
        self,
        *,
        on_partial,
        on_final=None,
        on_utterance_end=None,
        on_error=None,
    ) -> None:
        self._on_partial = on_partial
        self._on_error = on_error

    async def open(self) -> None:
        if not self._asr.api_key:
            raise RuntimeError("智谱 ASR 未配置 API Key")

    async def feed(self, pcm16_frame: bytes) -> None:
        if not self._closed and pcm16_frame:
            self._audio.extend(pcm16_frame)

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if not self._audio:
            return
        try:
            text = await asyncio.to_thread(self._transcribe, bytes(self._audio))
        except Exception as exc:
            if self._on_error:
                await self._on_error(str(exc))
            return
        if text and self._on_partial:
            await self._on_partial(text)

    def _transcribe(self, pcm16: bytes) -> str:
        wav = _pcm16_to_wav(pcm16, sample_rate=16000)
        boundary = f"----meper-zhipu-asr-{uuid.uuid4().hex}"
        body = _multipart_body(
            boundary,
            fields={"model": self._asr.resource_id, "stream": "false"},
            files={"file": ("utterance.wav", "audio/wav", wav)},
        )
        request = urllib.request.Request(
            self._asr.url,
            data=body,
            headers={
                "Authorization": f"Bearer {self._asr.api_key}",
                "Content-Type": f"multipart/form-data; boundary={boundary}",
            },
            method="POST",
        )
        data = json.loads(_read_response(request, timeout=60).decode("utf-8"))
        if "error" in data:
            error = data["error"]
            raise RuntimeError(
                error.get("message") or json.dumps(error, ensure_ascii=False)
            )
        return str(data.get("text") or "").strip()


class ZhipuTTSClient:
    """Synthesize raw PCM16 at 24 kHz through the GLM TTS HTTP API."""

    def __init__(self, tts: TTSRuntime) -> None:
        self._tts = tts
        self._stopped = False

    async def probe(self) -> None:
        audio_bytes = 0
        async for chunk in self.synth_stream("语音连接测试"):
            audio_bytes += len(chunk)
        if audio_bytes == 0:
            raise RuntimeError("智谱 TTS 连接成功，但没有返回音频数据")

    async def synth_stream(self, text: str):
        if not self._tts.api_key:
            raise RuntimeError("智谱 TTS 未配置 API Key")
        self._stopped = False
        pcm = await asyncio.to_thread(self._synthesize, text)
        if not self._stopped and pcm:
            yield pcm

    async def stop(self) -> None:
        self._stopped = True

    async def close(self) -> None:
        self._stopped = True

    def _synthesize(self, text: str) -> bytes:
        body = json.dumps(
            {
                "model": self._tts.resource_id,
                "input": text,
                "voice": self._tts.voice_type,
                "response_format": "pcm",
                "speed": self._tts.speed,
                "volume": self._tts.volume,
            },
            ensure_ascii=False,
        ).encode("utf-8")
        request = urllib.request.Request(
            self._tts.url,
            data=body,
            headers={
                "Authorization": f"Bearer {self._tts.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        payload = _read_response(request, timeout=60)
        if payload[:1] == b"{":
            data = json.loads(payload.decode("utf-8"))
            if "error" in data:
                error = data["error"]
                raise RuntimeError(
                    error.get("message") or json.dumps(error, ensure_ascii=False)
                )
        return payload


def _read_response(request: urllib.request.Request, *, timeout: int) -> bytes:
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.read()
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")
        raise RuntimeError(detail or str(exc)) from exc


def _pcm16_to_wav(pcm: bytes, *, sample_rate: int) -> bytes:
    output = io.BytesIO()
    with wave.open(output, "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(sample_rate)
        wav_file.writeframes(pcm)
    return output.getvalue()


def _multipart_body(
    boundary: str,
    *,
    fields: dict[str, str],
    files: dict[str, tuple[str, str, bytes]],
) -> bytes:
    chunks: list[bytes] = []
    for name, value in fields.items():
        chunks.extend(
            [
                f"--{boundary}\r\n".encode(),
                f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode(),
                value.encode("utf-8"),
                b"\r\n",
            ]
        )
    for name, (filename, content_type, content) in files.items():
        chunks.extend(
            [
                f"--{boundary}\r\n".encode(),
                (
                    f'Content-Disposition: form-data; name="{name}"; '
                    f'filename="{filename}"\r\n'
                ).encode(),
                f"Content-Type: {content_type}\r\n\r\n".encode(),
                content,
                b"\r\n",
            ]
        )
    chunks.append(f"--{boundary}--\r\n".encode())
    return b"".join(chunks)
