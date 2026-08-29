"""Streaming Chinese acoustic wake-word detection using sherpa-onnx KWS."""

from __future__ import annotations

import logging
import os
import threading
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Optional

import numpy as np

from deskbot_server.utils.paths import MODELS_DIR, PROJECT_ROOT

logger = logging.getLogger("deskbot-server")

DEFAULT_MODEL_NAME = "sherpa-onnx-kws-zipformer-wenetspeech-3.3M-2024-01-01"


@dataclass(frozen=True)
class AcousticWakeConfig:
    model_dir: str
    keywords_file: str
    num_threads: int = 1
    provider: str = "cpu"


def _resolve_path(raw: object, default: Path) -> str:
    text = str(raw or "").strip()
    path = Path(text) if text else default
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    return os.path.abspath(os.fspath(path))


class _SharedKeywordSpotter:
    def __init__(self, cfg: AcousticWakeConfig) -> None:
        import sherpa_onnx

        root = Path(cfg.model_dir)
        encoder = root / "encoder-epoch-12-avg-2-chunk-16-left-64.int8.onnx"
        decoder = root / "decoder-epoch-12-avg-2-chunk-16-left-64.onnx"
        joiner = root / "joiner-epoch-12-avg-2-chunk-16-left-64.int8.onnx"
        tokens = root / "tokens.txt"
        required = (encoder, decoder, joiner, tokens, Path(cfg.keywords_file))
        missing = [str(p) for p in required if not p.is_file()]
        if missing:
            raise FileNotFoundError("KWS model files missing: " + ", ".join(missing))
        self._spotter = sherpa_onnx.KeywordSpotter(
            tokens=str(tokens),
            encoder=str(encoder),
            decoder=str(decoder),
            joiner=str(joiner),
            num_threads=max(1, int(cfg.num_threads)),
            keywords_file=cfg.keywords_file,
            provider=cfg.provider,
        )
        self._lock = threading.Lock()
        logger.info(
            "[KWS] sherpa-onnx ready model_dir=%s keywords_file=%s threads=%d",
            cfg.model_dir,
            cfg.keywords_file,
            max(1, int(cfg.num_threads)),
        )

    def create_stream(self) -> Any:
        with self._lock:
            return self._spotter.create_stream()

    def feed(self, stream: Any, samples: np.ndarray, sample_rate: int) -> str:
        with self._lock:
            stream.accept_waveform(int(sample_rate), samples)
            detected = ""
            while self._spotter.is_ready(stream):
                self._spotter.decode_stream(stream)
                result = str(self._spotter.get_result(stream) or "").strip()
                if result:
                    detected = result
                    self._spotter.reset_stream(stream)
                    break
            return detected


@lru_cache(maxsize=4)
def _shared_runtime(cfg: AcousticWakeConfig) -> _SharedKeywordSpotter:
    return _SharedKeywordSpotter(cfg)


class AcousticWakeStream:
    """One streaming decoder state; the small ONNX model is shared process-wide."""

    def __init__(self, runtime: _SharedKeywordSpotter) -> None:
        self._runtime = runtime
        self._stream = runtime.create_stream()

    def feed_pcm(self, pcm: bytes, sample_rate: int = 16000) -> Optional[str]:
        if not pcm:
            return None
        samples = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
        result = self._runtime.feed(self._stream, samples, sample_rate)
        return result or None

    def reset(self) -> None:
        self._stream = self._runtime.create_stream()


def create_acoustic_wake_stream(raw_cfg: dict[str, Any]) -> Optional[AcousticWakeStream]:
    if not bool(raw_cfg.get("acoustic_enabled", False)):
        return None
    model_default = MODELS_DIR / DEFAULT_MODEL_NAME
    model_dir = _resolve_path(raw_cfg.get("acoustic_model_dir"), model_default)
    keywords_file = _resolve_path(
        raw_cfg.get("acoustic_keywords_file"), Path(model_dir) / "deskbot_keywords.txt"
    )
    cfg = AcousticWakeConfig(
        model_dir=model_dir,
        keywords_file=keywords_file,
        num_threads=max(1, int(raw_cfg.get("acoustic_num_threads", 1))),
        provider=str(raw_cfg.get("acoustic_provider") or "cpu").strip(),
    )
    try:
        return AcousticWakeStream(_shared_runtime(cfg))
    except Exception:
        logger.exception("[KWS] acoustic wake unavailable; falling back to ASR text gate")
        return None
