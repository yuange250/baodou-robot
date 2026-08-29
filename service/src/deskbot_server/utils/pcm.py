from __future__ import annotations

from array import array
import math
import sys


def apply_pcm16_gain(pcm: bytes, gain: object) -> bytes:
    """Apply bounded gain to little-endian signed 16-bit PCM."""
    try:
        factor = float(gain)
    except (TypeError, ValueError):
        return pcm
    if not math.isfinite(factor) or factor <= 0 or abs(factor - 1.0) < 1e-6 or not pcm:
        return pcm

    factor = min(factor, 4.0)
    sample_bytes = len(pcm) & ~1
    samples = array("h")
    samples.frombytes(pcm[:sample_bytes])
    if sys.byteorder != "little":
        samples.byteswap()
    for idx, sample in enumerate(samples):
        amplified = int(round(sample * factor))
        samples[idx] = max(-32768, min(32767, amplified))
    if sys.byteorder != "little":
        samples.byteswap()
    return samples.tobytes() + pcm[sample_bytes:]
