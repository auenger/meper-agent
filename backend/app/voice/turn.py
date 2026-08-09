"""TurnContext — mutable per-turn state.

Carries the finalized transcript, the brain's timeline (AppEvent dicts, for
persistence), token usage, a cancel flag (barge-in), the TTS sentence buffer,
and the per-turn TTS queue/task. Kept as a plain holder — VoiceSession drives
its lifecycle.
"""
from __future__ import annotations

import asyncio
from typing import Any


class TurnContext:
    def __init__(self, transcript: str = "") -> None:
        self.transcript: str = transcript
        self.timeline: list[dict[str, Any]] = []
        self.usage: dict[str, Any] = {}
        self.tts_buffer: str = ""
        self._cancelled: bool = False
        self.tts_queue: asyncio.Queue[str | None] | None = None
        self.tts_task: asyncio.Task | None = None
