from __future__ import annotations

from pathlib import Path

from deskbot_server.infrastructure.kws.sherpa import create_acoustic_wake_stream


def test_acoustic_kws_model_initializes_and_silence_does_not_trigger() -> None:
    model = Path(__file__).parents[1] / "models" / "sherpa-onnx-kws-zipformer-wenetspeech-3.3M-2024-01-01"
    if not model.is_dir():
        return
    stream = create_acoustic_wake_stream(
        {
            "acoustic_enabled": True,
            "acoustic_model_dir": str(model),
            "acoustic_keywords_file": str(model / "deskbot_keywords.txt"),
            "acoustic_num_threads": 1,
        }
    )
    assert stream is not None
    assert stream.feed_pcm(b"\x00\x00" * 16000, 16000) is None
