"""PCM16 audio helpers — resampling and frame splitting.

Linear interpolation is enough for voice: it's cheap, dependency-free, and the
quality gap vs sinc resampling is inaudible for 16k→24k speech upsampling.
"""
from __future__ import annotations

import struct


def resample_pcm16(data: bytes, src_rate: int, dst_rate: int) -> bytes:
    """Resample mono PCM16 little-endian audio by linear interpolation."""
    if src_rate == dst_rate or len(data) < 2:
        return data
    n_in = len(data) // 2
    samples = struct.unpack("<" + "h" * n_in, data)
    ratio = dst_rate / src_rate
    n_out = int(n_in * ratio)
    out = bytearray(2 * n_out)
    for i in range(n_out):
        pos = i / ratio
        i0 = int(pos)
        i1 = i0 + 1 if i0 + 1 < n_in else i0
        frac = pos - i0
        value = samples[i0] * (1 - frac) + samples[i1] * frac
        struct.pack_into("<h", out, i * 2, int(value))
    return bytes(out)
