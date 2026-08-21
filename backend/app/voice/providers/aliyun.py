"""Alibaba Cloud Bailian batch ASR and HTTP TTS adapters."""

from __future__ import annotations

import asyncio
import base64
import io
import json
import urllib.error
import urllib.request
import wave
from collections.abc import Awaitable, Callable
from typing import Any

from app.voice.config import ASRRuntime, TTSRuntime


class AliyunASRClient:
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
            raise RuntimeError("阿里百炼 ASR 未配置 API Key")

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
        audio_data = "data:audio/wav;base64," + base64.b64encode(wav).decode("ascii")
        body = json.dumps(
            {
                "model": self._asr.resource_id,
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "input_audio",
                                "input_audio": {"data": audio_data, "format": "wav"},
                            }
                        ],
                    }
                ],
                "stream": False,
            },
            ensure_ascii=False,
        ).encode("utf-8")
        request = urllib.request.Request(
            self._asr.url,
            data=body,
            headers={
                "Authorization": f"Bearer {self._asr.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        data = json.loads(_read_response(request, timeout=90).decode("utf-8"))
        _raise_api_error(data)
        return _extract_asr_text(data)


class AliyunTTSClient:
    """Synthesize PCM16 at 24 kHz through the Bailian HTTP API."""

    def __init__(self, tts: TTSRuntime) -> None:
        self._tts = tts
        self._stopped = False

    async def probe(self) -> None:
        audio_bytes = 0
        async for chunk in self.synth_stream("语音连接测试"):
            audio_bytes += len(chunk)
        if audio_bytes == 0:
            raise RuntimeError("阿里百炼 TTS 连接成功，但没有返回音频数据")

    async def synth_stream(self, text: str):
        if not self._tts.api_key:
            raise RuntimeError("阿里百炼 TTS 未配置 API Key")
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
                "input": {
                    "text": text,
                    "voice": self._tts.voice_type,
                    "language_type": self._tts.language_type or "Auto",
                },
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
        data = json.loads(_read_response(request, timeout=90).decode("utf-8"))
        _raise_api_error(data)
        audio = (data.get("output") or {}).get("audio") or {}
        if audio.get("data"):
            audio_bytes = base64.b64decode(audio["data"])
        elif audio.get("url"):
            audio_bytes = _download(audio["url"])
        else:
            raise RuntimeError("阿里百炼 TTS 未返回 audio.url 或 audio.data")
        return _audio_to_pcm16(audio_bytes)


def _read_response(request: urllib.request.Request, *, timeout: int) -> bytes:
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.read()
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")
        raise RuntimeError(detail or str(exc)) from exc


def _download(url: str) -> bytes:
    request = urllib.request.Request(url, method="GET")
    return _read_response(request, timeout=90)


def _raise_api_error(data: dict[str, Any]) -> None:
    if "error" in data:
        error = data["error"]
        raise RuntimeError(
            error.get("message") or json.dumps(error, ensure_ascii=False)
        )
    if data.get("code"):
        raise RuntimeError(str(data.get("message") or data["code"]))


def _pcm16_to_wav(pcm: bytes, *, sample_rate: int) -> bytes:
    output = io.BytesIO()
    with wave.open(output, "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(sample_rate)
        wav_file.writeframes(pcm)
    return output.getvalue()


def _audio_to_pcm16(audio: bytes) -> bytes:
    if audio[:4] != b"RIFF":
        return audio
    with wave.open(io.BytesIO(audio), "rb") as wav_file:
        channels = wav_file.getnchannels()
        sample_width = wav_file.getsampwidth()
        frames = wav_file.readframes(wav_file.getnframes())
    if channels == 1 and sample_width == 2:
        return frames
    if sample_width != 2:
        raise RuntimeError("阿里百炼 TTS 返回了非 PCM16 WAV，暂不支持播放")
    mono = bytearray()
    frame_size = channels * 2
    for offset in range(0, len(frames), frame_size):
        samples = [
            int.from_bytes(
                frames[offset + channel * 2 : offset + channel * 2 + 2],
                "little",
                signed=True,
            )
            for channel in range(channels)
        ]
        average = int(sum(samples) / channels)
        mono.extend(average.to_bytes(2, "little", signed=True))
    return bytes(mono)


def _extract_asr_text(data: dict[str, Any]) -> str:
    choices = data.get("choices") or []
    if choices:
        message = choices[0].get("message") or {}
        content = message.get("content")
        if isinstance(content, str):
            return content.strip()
        if isinstance(content, list):
            parts: list[str] = []
            for item in content:
                if isinstance(item, dict):
                    value = item.get("text") or item.get("content")
                    if value:
                        parts.append(str(value))
                elif item:
                    parts.append(str(item))
            return "".join(parts).strip()
    output = data.get("output") or {}
    text = output.get("text") or output.get("transcription")
    return str(text).strip() if text else ""
