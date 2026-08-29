"""Acoustic keyword spotting adapters."""

from deskbot_server.infrastructure.kws.sherpa import AcousticWakeStream, create_acoustic_wake_stream

__all__ = ["AcousticWakeStream", "create_acoustic_wake_stream"]
