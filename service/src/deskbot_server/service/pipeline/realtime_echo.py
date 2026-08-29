"""Realtime acoustic echo cancellation for the robot speaker/microphone path."""

from __future__ import annotations

from array import array
from collections import deque


PCM16_16K_10MS_BYTES = 160 * 2
PCM16_16K_20MS_BYTES = PCM16_16K_10MS_BYTES * 2


class RealtimeEchoCanceller:
    """WebRTC AEC with a paced 24 kHz speaker-reference queue.

    The realtime model returns 24 kHz PCM while the microphone is 16 kHz.  The
    reverse stream is resampled once, split into 20 ms frames, then consumed by
    the same media clock as the microphone so WebRTC can align both streams.
    """

    def __init__(self, *, delay_ms: int = 120, max_reference_ms: int = 10_000) -> None:
        from aec_audio_processing import AudioProcessor

        self._processor = AudioProcessor(
            enable_aec=True,
            enable_ns=False,
            enable_agc=False,
            enable_vad=False,
        )
        self._processor.set_stream_format(16000, 1)
        self._processor.set_reverse_stream_format(16000, 1)
        self._processor.set_stream_delay(max(0, int(delay_ms)))
        self._reference_frames: deque[bytes] = deque(
            maxlen=max(1, int(max_reference_ms) // 20)
        )
        self._reference_partial = bytearray()
        self._resample_remainder = array("h")

    @property
    def delay_ms(self) -> int:
        return int(self._processor.get_stream_delay())

    @property
    def reference_frames(self) -> int:
        return len(self._reference_frames)

    def clear_reference(self) -> None:
        self._reference_frames.clear()
        self._reference_partial.clear()
        self._resample_remainder = array("h")

    def queue_speaker_pcm24k(self, pcm_s16le: bytes) -> int:
        usable = len(pcm_s16le) & ~1
        if usable <= 0:
            return 0
        samples = array("h", self._resample_remainder)
        incoming = array("h")
        incoming.frombytes(pcm_s16le[:usable])
        samples.extend(incoming)
        output = array("h")
        complete = len(samples) - (len(samples) % 3)
        # 24 kHz -> 16 kHz: output samples at source positions 0 and
        # 1.5 inside each three-sample group.  Linear interpolation is enough
        # for an AEC reverse reference and keeps exact rational clocking.
        for offset in range(0, complete, 3):
            output.append(samples[offset])
            output.append((int(samples[offset + 1]) + int(samples[offset + 2])) // 2)
        self._resample_remainder = array("h", samples[complete:])
        self._reference_partial.extend(output.tobytes())
        queued = 0
        while len(self._reference_partial) >= PCM16_16K_20MS_BYTES:
            frame = bytes(self._reference_partial[:PCM16_16K_20MS_BYTES])
            del self._reference_partial[:PCM16_16K_20MS_BYTES]
            self._reference_frames.append(frame)
            queued += 1
        return queued

    def process_near_frame(self, pcm_s16le: bytes) -> tuple[bytes, bool]:
        if len(pcm_s16le) != PCM16_16K_20MS_BYTES:
            raise ValueError(
                f"AEC requires exactly 20 ms/640 bytes, got {len(pcm_s16le)}"
            )
        has_reference = bool(self._reference_frames)
        far = (
            self._reference_frames.popleft()
            if has_reference
            else bytes(PCM16_16K_20MS_BYTES)
        )
        output = bytearray()
        for offset in (0, PCM16_16K_10MS_BYTES):
            end = offset + PCM16_16K_10MS_BYTES
            self._processor.process_reverse_stream(far[offset:end])
            output.extend(self._processor.process_stream(pcm_s16le[offset:end]))
        return bytes(output), has_reference
