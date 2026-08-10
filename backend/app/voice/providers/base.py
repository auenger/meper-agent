"""Provider abstractions for streaming STT and TTS.

Kept as Protocols so VoiceSession depends on the interface, not the Volcano
implementation — swapping vendors later means adding one adapter file under
``providers/`` and wiring it in ``api.py``.
"""
from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Protocol


class STTProvider(Protocol):
    """Streaming speech-to-text — one live recognition stream per voice session."""

    async def open(self) -> None:
        """Open the upstream WS and send the config frame."""
        ...

    async def feed(self, pcm16_frame: bytes) -> None:
        """Send one PCM16@16k audio chunk upstream."""
        ...

    async def close(self) -> None:
        """Close the upstream WS cleanly."""
        ...

    def bind(
        self,
        *,
        on_partial: Callable[[str], Awaitable[None]],
        on_final: Callable[[str], Awaitable[None]],
        on_utterance_end: Callable[[], Awaitable[None]] | None = None,
        on_error: Callable[[str], Awaitable[None]] | None = None,
    ) -> None:
        """Register callbacks for ASR results, utterance boundaries, and errors."""
        ...


class TTSProvider(Protocol):
    """Streaming text-to-speech — synthesizes one text chunk at a time."""

    def synth_stream(self, text: str) -> AsyncIterator[bytes]:
        """Yield PCM16@24k audio chunks for the given text (async generator)."""
        ...

    async def stop(self) -> None:
        """Interrupt the current synthesis (barge-in)."""
        ...

    async def close(self) -> None:
        """Release the upstream connection (session teardown)."""
        ...
