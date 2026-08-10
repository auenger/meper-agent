from __future__ import annotations

import struct

from app.voice.vad import EnergyVAD, Speech


def test_energy_vad_detects_browser_level_speech_and_silence() -> None:
    vad = EnergyVAD(threshold=0.02, silence_ms=40, frame_ms=20)
    speech = struct.pack("<320h", *([1000] * 320))
    silence = bytes(640)

    assert vad.feed(speech) is Speech.START
    assert vad.feed(silence) is None
    assert vad.feed(silence) is Speech.END
