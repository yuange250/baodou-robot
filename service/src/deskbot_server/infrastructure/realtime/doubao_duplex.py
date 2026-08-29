"""Volcengine Doubao full-duplex realtime speech client.

This uses the current JSON text-frame protocol.  It deliberately supports both
the new API-key console and the legacy speech-console APP ID/access-token
credentials because existing DeskBot deployments use both kinds of account.
"""

from __future__ import annotations

import asyncio
import base64
import json
import uuid
from collections.abc import AsyncIterator
from typing import Any

import websockets

from deskbot_server.core.settings import RealtimeSettings


SESSION_CREATED = "session.created"
SESSION_CLOSED = "session.closed"
ERROR = "error"


def build_auth_headers(settings: RealtimeSettings, *, request_id: str | None = None) -> dict[str, str]:
    """Build authentication headers without ever using an application Secret Key."""
    if settings.api_key:
        return {"X-Api-Key": settings.api_key}
    if settings.app_id and settings.access_token:
        return {
            "X-Api-App-Id": settings.app_id,
            "X-Api-Access-Key": settings.access_token,
            "X-Api-Resource-Id": settings.resource_id,
            "X-Api-App-Key": settings.legacy_app_key,
            "X-Api-Request-Id": request_id or str(uuid.uuid4()),
        }
    raise ValueError("Doubao Realtime 缺少 API Key 或 APP ID/Access Token")


class DoubaoDuplexClient:
    """Small async client for ``/api/v3/duplex/realtime/dialogue``."""

    def __init__(self, settings: RealtimeSettings, *, tools: list[dict[str, Any]] | None = None) -> None:
        self.settings = settings
        self.tools = list(tools or [])
        self.session_id = str(uuid.uuid4())
        self.dialog_id = ""
        self.log_id = ""
        self._event_seq = 0
        self._write_lock = asyncio.Lock()
        self.ws: Any = None

    @property
    def connected(self) -> bool:
        return self.ws is not None

    def new_event_id(self) -> str:
        self._event_seq += 1
        return f"event_{self._event_seq}"

    def build_session_create_event(self) -> dict[str, Any]:
        session = {
            "type": "realtime",
            "id": self.session_id,
            "model": self.settings.model,
            "instructions": self.settings.instructions,
            "audio": {
                "input": {"format": {"type": self.settings.input_format, "rate": 16000}},
                "output": {
                    "format": {"type": self.settings.output_format, "rate": 24000},
                    "speed": self.settings.output_speed,
                    "loudness": self.settings.output_loudness,
                    "voice": self.settings.voice,
                },
            },
            "tools": self.tools,
        }
        return {
            "type": "session.create",
            "event_id": self.new_event_id(),
            "session": session,
            "extension": {
                "asr": {
                    "extra": {
                        # Provider-side VAD owns turn boundaries in realtime mode.
                        # The device therefore keeps one continuous media clock.
                        "end_smooth_window_ms": self.settings.end_silence_ms,
                    }
                },
                "tts": {"extra": {}},
                "dialog": {
                    "extra": {
                        "audit_response": "抱歉，这个问题我暂时无法回答，我们换个话题吧。",
                        "enable_loudness_norm": True,
                        "enable_music": False,
                        # Full-duplex sessions must stay receptive while the
                        # assistant is speaking.  The official troubleshooting
                        # guidance for DialogAudioIdleTimeoutError explicitly
                        # requires this mode.
                        "input_mod": "keep_alive",
                    }
                },
            },
        }

    async def connect(self, *, timeout: float = 12.0) -> None:
        if self.ws is not None:
            return
        headers = build_auth_headers(self.settings)
        self.ws = await websockets.connect(
            self.settings.endpoint_url,
            additional_headers=headers,
            ping_interval=None,
            open_timeout=timeout,
            close_timeout=2,
            max_size=8 * 1024 * 1024,
        )
        response = getattr(self.ws, "response", None)
        response_headers = getattr(response, "headers", None)
        if response_headers is not None:
            self.log_id = str(response_headers.get("X-Tt-Logid") or "")
        await self.send_event(self.build_session_create_event())
        while True:
            event = await asyncio.wait_for(self.recv_event(), timeout=timeout)
            event_type = str(event.get("type") or "")
            if event_type == SESSION_CREATED:
                self.dialog_id = str((event.get("session") or {}).get("id") or self.session_id)
                return
            if event_type == ERROR:
                raise RuntimeError(self._error_text(event))

    async def send_event(self, event: dict[str, Any]) -> None:
        if self.ws is None:
            raise RuntimeError("Doubao Realtime WebSocket 未连接")
        payload = json.dumps(event, ensure_ascii=False, separators=(",", ":"))
        async with self._write_lock:
            await self.ws.send(payload)

    async def recv_event(self) -> dict[str, Any]:
        if self.ws is None:
            raise RuntimeError("Doubao Realtime WebSocket 未连接")
        frame = await self.ws.recv()
        if isinstance(frame, bytes):
            frame = frame.decode("utf-8")
        event = json.loads(frame)
        if not isinstance(event, dict):
            raise ValueError("Doubao Realtime 返回了非 JSON 对象")
        return event

    async def events(self) -> AsyncIterator[dict[str, Any]]:
        while self.ws is not None:
            yield await self.recv_event()

    async def append_audio(self, pcm_s16le: bytes) -> None:
        if not pcm_s16le:
            return
        await self.send_event(
            {
                "type": "input_audio_buffer.append",
                "event_id": self.new_event_id(),
                "audio": base64.b64encode(pcm_s16le).decode("ascii"),
            }
        )

    async def commit_audio(self) -> None:
        await self.send_event({"type": "input_audio_buffer.commit", "event_id": self.new_event_id()})

    async def commit_greeting(self, text: str) -> None:
        if not str(text or "").strip():
            return
        await self.send_event(
            {
                "type": "speech_text_buffer.commit",
                "event_id": self.new_event_id(),
                "text": str(text).strip(),
            }
        )

    async def cancel_response(self) -> None:
        await self.send_event({"type": "response.cancel", "event_id": self.new_event_id()})

    async def send_tool_output(self, call_id: str, output: str) -> None:
        """Return one Function Calling result using Fire's current protocol."""
        call_id = str(call_id or "").strip()
        if not call_id:
            return
        await self.send_event(
            {
                "type": "conversation.item.create",
                "event_id": self.new_event_id(),
                "item": {
                    "type": "function_call_output",
                    "call_id": call_id,
                    "output": str(output or ""),
                },
            }
        )

    async def send_tool_outputs(self, outputs: list[dict[str, str]]) -> None:
        """Compatibility wrapper; the wire protocol still sends one item per event."""
        for output in outputs:
            await self.send_tool_output(str(output.get("call_id") or ""), str(output.get("text") or ""))

    async def close(self) -> None:
        ws, self.ws = self.ws, None
        if ws is None:
            return
        try:
            payload = json.dumps(
                {"type": "session.close", "event_id": self.new_event_id()},
                ensure_ascii=False,
                separators=(",", ":"),
            )
            async with self._write_lock:
                await ws.send(payload)
        except Exception:
            pass
        try:
            await ws.close()
        except Exception:
            pass

    @staticmethod
    def _error_text(event: dict[str, Any]) -> str:
        error = event.get("error")
        if isinstance(error, dict):
            code = str(error.get("code") or "")
            message = str(error.get("message") or error.get("error") or "")
            return f"Doubao Realtime error code={code or '-'} message={message or '-'}"
        return f"Doubao Realtime error: {str(error or event.get('message') or 'unknown')[:500]}"
