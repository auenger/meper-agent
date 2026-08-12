"""Voice Activity Detection — fast barge-in trigger.

The ASR provider already reports utterance endpoints (turn boundaries); VAD's
only job here is **fast barge-in**: interrupt the agent the moment the user
starts speaking again, without waiting for ASR's ~600ms endpoint latency.

Three modes (``VOICE_VAD_MODE``):
  silero: ONNX silero-vad, accurate (needs ``silero-vad`` + torch). Falls back
          to energy VAD if the library is not installed.
  energy: pure-Python short-time RMS energy, no dependencies — the default-safe
          option that always works.
  off:    no local VAD; rely on ASR endpoint only (no fast barge-in).
"""
from __future__ import annotations

import contextlib
import enum
import struct

from loguru import logger


class Speech(enum.Enum):
    START = 1
    END = 2


class VADDetector:
    """Abstract: feed a PCM16@16k frame, get a Speech event or None."""

    def feed(self, pcm16: bytes) -> Speech | None:
        raise NotImplementedError

    def close(self) -> None:
        pass


class EnergyVAD(VADDetector):
    """Short-time RMS energy VAD (no external deps).

    Fires START on the rising edge (energy crosses threshold), END after
    ``silence_ms`` of consecutive quiet frames. Threshold is normalized RMS
    (0..1 of full-scale PCM16); ~0.02 works well with browser AGC microphones.
    """

    def __init__(self, threshold: float = 0.02, silence_ms: int = 600, frame_ms: int = 20) -> None:
        self.threshold = threshold
        self._silence_frames = max(1, silence_ms // frame_ms)
        self._is_speech = False
        self._silence_count = 0

    def feed(self, pcm16: bytes) -> Speech | None:
        n = len(pcm16) // 2
        if n == 0:
            return None
        samples = struct.unpack("<" + "h" * n, pcm16)
        sumsq = sum(s * s for s in samples)
        rms = (sumsq / n) ** 0.5 / 32768.0
        if rms >= self.threshold:
            self._silence_count = 0
            if not self._is_speech:
                self._is_speech = True
                return Speech.START
            return None
        if self._is_speech:
            self._silence_count += 1
            if self._silence_count >= self._silence_frames:
                self._is_speech = False
                return Speech.END
        return None


class SileroVAD(VADDetector):
    """Accurate VAD via the silero-vad library (needs torch)."""

    def __init__(self, threshold: float = 0.5, silence_ms: int = 600) -> None:
        import torch  # noqa: F401  (silero-vad requires torch)
        from silero_vad import (  # type: ignore[import-untyped]
            VADIterator,
            load_silero_vad,
        )

        self.threshold = threshold
        self._iter = VADIterator(
            load_silero_vad(),
            threshold=threshold,
            sampling_rate=16000,
            min_silence_duration_ms=silence_ms,
            speech_pad_ms=80,
        )

    def feed(self, pcm16: bytes) -> Speech | None:
        import torch

        samples = struct.unpack("<" + "h" * (len(pcm16) // 2), pcm16)
        tensor = torch.tensor(samples, dtype=torch.float32) / 32768.0
        out = self._iter(tensor, return_seconds=True)
        if not out:
            return None
        if "start" in out:
            return Speech.START
        if "end" in out:
            return Speech.END
        return None

    def close(self) -> None:
        with contextlib.suppress(Exception):
            self._iter.reset_states()


def create_vad(mode: str, threshold: float, silence_ms: int) -> VADDetector | None:
    """Build a VAD from runtime config; SileroVAD falls back to EnergyVAD if unavailable."""
    if mode == "off":
        return None
    if mode == "silero":
        try:
            return SileroVAD(threshold=threshold, silence_ms=silence_ms)
        except Exception as e:
            logger.warning("silero_vad_unavailable_fallback_energy", error=str(e))
            return EnergyVAD(threshold=0.02, silence_ms=silence_ms)
    return EnergyVAD(threshold=0.02, silence_ms=silence_ms)
