"""Bridge Doubao Realtime events to DeskBot's audio and robot-control ports."""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import math
import time
import uuid
from array import array
from dataclasses import dataclass, replace
from collections.abc import Callable
from typing import Any

from deskbot_server.infrastructure.llm.runtime import chat_acompletion, resolve_llm_config
from deskbot_server.infrastructure.realtime import DoubaoDuplexClient
from deskbot_server.pb.shapes import PB_ACTION_APPEND, PB_ACTION_REPLACE
from deskbot_server.pb.wire import build_pb_wire_pairs
from deskbot_server.service.application.llm_tool_runner import execute_llm_tools
from deskbot_server.service.pipeline.opus_downlink import OpusStreamBatchEncoder
from deskbot_server.service.pipeline.realtime_echo import (
    PCM16_16K_20MS_BYTES,
    RealtimeEchoCanceller,
)

logger = logging.getLogger("deskbot-server")


@dataclass(slots=True)
class _UplinkItem:
    kind: str
    pcm: bytes = b""
    result: asyncio.Future[bool] | None = None
    auto_commit_seq: int = 0
    duration_ms: int = 0


def build_robot_tools() -> list[dict[str, Any]]:
    """Function schemas supported by the realtime model."""
    return [
        {
            "type": "function",
            "name": "move_head",
            "description": "转动包逗的头。方向指令不要改变另一根轴。",
            "parameters": {
                "type": "object",
                "properties": {
                    "direction": {
                        "type": "string",
                        "enum": ["left", "right", "up", "down", "center"],
                    },
                    "x": {"type": "integer", "minimum": -90, "maximum": 150},
                    "y": {"type": "integer", "minimum": -10, "maximum": 80},
                    "relative": {"type": "boolean"},
                    "ms": {"type": "integer", "minimum": 80, "maximum": 3000},
                },
            },
        },
        {
            "type": "function",
            "name": "set_expression",
            "description": "设置包逗的屏幕表情。",
            "parameters": {
                "type": "object",
                "properties": {
                    "expression": {
                        "type": "string",
                        "enum": ["idle", "happy", "shy", "angry", "surprised", "sad", "sleep", "thinking", "listening"],
                    }
                },
                "required": ["expression"],
            },
        },
        {
            "type": "function",
            "name": "set_volume",
            "description": "设置或相对调整包逗的扬声器音量。",
            "parameters": {
                "type": "object",
                "properties": {
                    "volume": {"type": "integer", "minimum": 0, "maximum": 100},
                    "delta": {"type": "integer", "minimum": -100, "maximum": 100},
                },
            },
        },
        {
            "type": "function",
            "name": "set_listening_profile",
            "description": "调整麦克风收音灵敏度。",
            "parameters": {
                "type": "object",
                "properties": {
                    "profile": {"type": "string", "enum": ["quiet", "normal", "far"]}
                },
                "required": ["profile"],
            },
        },
        {
            "type": "function",
            "name": "set_camera_follow",
            "description": "打开或关闭摄像头人脸跟随。",
            "parameters": {
                "type": "object",
                "properties": {
                    "mode": {"type": "string", "enum": ["off", "follow", "follow_frontal", "gaze"]}
                },
                "required": ["mode"],
            },
        },
        {
            "type": "function",
            "name": "inspect_camera",
            "description": "调用 Seed VLM 拍摄并分析包逗当前看到的画面。凡是用户询问眼前、镜头前、手里或展示的物体是什么，必须调用本工具，禁止凭对话猜测。",
            "parameters": {
                "type": "object",
                "properties": {"question": {"type": "string"}},
                "required": ["question"],
            },
        },
    ]


class DoubaoRealtimeBridge:
    def __init__(
        self,
        *,
        pipeline: Any,
        device_id: str,
        asr_chat_hub: Any,
        endpoint_vad_factory: Callable[[], Any] | None = None,
    ) -> None:
        self.pipeline = pipeline
        self.device_id = str(device_id or "")
        self.hub = asr_chat_hub
        self.settings = pipeline.settings.realtime
        self.client: DoubaoDuplexClient | None = None
        self._connect_lock = asyncio.Lock()
        self._recv_task: asyncio.Task | None = None
        self._uplink_task: asyncio.Task | None = None
        self._endpoint_vad_task: asyncio.Task | None = None
        self._play_task: asyncio.Task | None = None
        self._idle_task: asyncio.Task | None = None
        self._output_watchdog_task: asyncio.Task | None = None
        self._tool_response_watchdog_task: asyncio.Task | None = None
        self._recovery_task: asyncio.Task | None = None
        self._tool_tasks: set[asyncio.Task[Any]] = set()
        self._pending_tool_names: dict[str, str] = {}
        self._tool_calls_inflight: set[str] = set()
        self._completed_tool_calls: set[str] = set()
        # generation, pcm, end_burst.  Tag every queued item with the response
        # that produced it so an async cancel/new response can never replay a
        # stale phoneme in the next PB/Opus stream.
        self._play_queue: asyncio.Queue[tuple[int, bytes, bool] | None] = asyncio.Queue(maxsize=96)
        # Fire's full-duplex endpoint consumes a continuous input media clock.
        # Audio and commit barriers share one queue so a commit can never pass
        # microphone frames that were received before it.
        self._uplink_queue: asyncio.Queue[_UplinkItem] = asyncio.Queue(maxsize=512)
        self._uplink_enqueue_lock = asyncio.Lock()
        self._uplink_partial = bytearray()
        self._uplink_frames_sent = 0
        self._uplink_clock_frames = 0
        self._uplink_echo_fallback_frames = 0
        self._last_uplink_at = 0.0
        self._uplink_max_gap_ms = 0
        self._echo_canceller: RealtimeEchoCanceller | None = None
        self._endpoint_vad_factory = endpoint_vad_factory
        self._endpoint_vad: Any | None = None
        self._endpoint_vad_enabled = False
        self._endpoint_vad_generation = 0
        self._endpoint_vad_queue: asyncio.Queue[tuple[int, bytes]] = asyncio.Queue(maxsize=256)
        self._endpoint_vad_dropped_frames = 0
        self._manual_commits_pending = 0
        self._provider_auto_commits = 0
        self._local_endpoint_commits = 0
        self._response_generation = 0
        self._last_activity = 0.0
        self._audio_buffer = bytearray()
        self._response_id = ""
        self._response_pb_req = ""
        self._response_chunk_idx = 0
        self._response_audio_received = False
        self._burst_end_queued = False
        self._stream_open = False
        self._play_started = False
        self._prebuffering = False
        self._first_audio_chunk = True
        self._closing = False
        self._failed = False
        self._output_streaming = False
        self._device_audio_buf_ms = 0
        self._suppress_input_until = 0.0
        self._input_pcm_bytes = 0
        self._suppressed_input_bytes = 0
        self._last_suppressed_log = 0.0
        self._last_output_audio_at = 0.0
        self._last_audio_delta_at = 0.0
        self._raw_pcm_meter_buffer = bytearray()
        self._raw_pcm_total_bytes = 0
        self._raw_pcm_bucket_idx = 0
        self._raw_pcm_max_gap_ms = 0
        self._output_opus_encoder: OpusStreamBatchEncoder | None = None

    @property
    def enabled(self) -> bool:
        return bool(
            self.settings.enabled
            and self.device_id
            and (self.settings.api_key or (self.settings.app_id and self.settings.access_token))
        )

    @property
    def active(self) -> bool:
        return self.client is not None and self.client.connected and not self._failed

    @property
    def wants_audio(self) -> bool:
        return self.enabled and self.active

    @property
    def input_suppressed(self) -> bool:
        if not self.settings.suppress_uplink_during_playback:
            return False
        # Server-side buffers are not audible and must never mute the
        # microphone.  The guard is extended only after audio was delivered to
        # the device (and refined by pb_ack).
        return time.monotonic() < self._suppress_input_until

    async def start(self) -> bool:
        if not self.enabled:
            return False
        if self.active:
            self._touch()
            return True
        async with self._connect_lock:
            if self.active:
                self._touch()
                return True
            self._failed = False
            self._closing = False
            client = DoubaoDuplexClient(self.settings, tools=build_robot_tools())
            try:
                await client.connect()
            except Exception as exc:
                await client.close()
                self._failed = True
                logger.warning("[Realtime] 连接失败，回退传统对话 device_id=%s error=%s", self.device_id, exc)
                return False
            self.client = client
            self._setup_echo_canceller()
            self._reset_endpoint_vad()
            self._touch()
            self._uplink_task = asyncio.create_task(
                self._uplink_loop(), name=f"realtime-uplink:{self.device_id}"
            )
            self._endpoint_vad_task = asyncio.create_task(
                self._endpoint_vad_loop(), name=f"realtime-endpoint-vad:{self.device_id}"
            )
            self._recv_task = asyncio.create_task(self._receive_loop(), name=f"realtime-recv:{self.device_id}")
            self._play_task = asyncio.create_task(self._play_loop(), name=f"realtime-play:{self.device_id}")
            self._idle_task = asyncio.create_task(self._idle_watch(), name=f"realtime-idle:{self.device_id}")
            logger.info(
                "[Realtime] 会话已建立 device_id=%s dialog_id=%s logid=%s",
                self.device_id,
                client.dialog_id,
                client.log_id or "-",
            )
            return True

    async def activate_with_utterance(self, pcm: bytes) -> bool:
        if not await self.start():
            return False
        self._endpoint_vad_enabled = False
        try:
            if not await self.send_audio(pcm):
                return False
            return await self.commit()
        finally:
            self._reset_endpoint_vad()
            self._endpoint_vad_enabled = True

    async def start_with_greeting(self, text: str) -> bool:
        if not str(text or "").strip() or not await self.start():
            return False
        client = self.client
        if client is None or not client.connected:
            return False
        await client.commit_greeting(text)
        self._reset_endpoint_vad()
        self._endpoint_vad_enabled = True
        self._touch()
        return True

    async def send_audio(self, pcm: bytes) -> bool:
        client = self.client
        if client is None or not client.connected or not pcm:
            return False
        usable = len(pcm) & ~1
        if usable <= 0:
            return False
        async with self._uplink_enqueue_lock:
            self._uplink_partial.extend(pcm[:usable])
            while len(self._uplink_partial) >= PCM16_16K_20MS_BYTES:
                frame = bytes(self._uplink_partial[:PCM16_16K_20MS_BYTES])
                del self._uplink_partial[:PCM16_16K_20MS_BYTES]
                await self._uplink_queue.put(_UplinkItem("audio", pcm=frame))
        return True

    async def commit(self) -> bool:
        client = self.client
        if client is None or not client.connected:
            return False
        loop = asyncio.get_running_loop()
        result: asyncio.Future[bool] = loop.create_future()
        async with self._uplink_enqueue_lock:
            if self._uplink_partial:
                padded = bytes(self._uplink_partial).ljust(PCM16_16K_20MS_BYTES, b"\x00")
                self._uplink_partial.clear()
                await self._uplink_queue.put(_UplinkItem("audio", pcm=padded))
            await self._uplink_queue.put(_UplinkItem("commit", result=result))
        try:
            timeout = max(15.0, self._uplink_queue.qsize() * 0.020 + 5.0)
            return await asyncio.wait_for(asyncio.shield(result), timeout=timeout)
        except asyncio.TimeoutError:
            logger.warning("[Realtime] uplink commit barrier timeout device_id=%s", self.device_id)
            return False

    async def _mark_input_boundary(self) -> None:
        """Forget audio already endpointed by the provider, in media order."""
        async with self._uplink_enqueue_lock:
            await self._uplink_queue.put(_UplinkItem("boundary"))

    def _setup_echo_canceller(self) -> None:
        self._echo_canceller = None
        if not self.settings.echo_cancellation_enabled:
            logger.warning(
                "[Realtime] AEC disabled; playback uses continuous-silence fallback device_id=%s",
                self.device_id,
            )
            return
        try:
            self._echo_canceller = RealtimeEchoCanceller(delay_ms=self.settings.echo_delay_ms)
        except Exception as exc:
            logger.warning(
                "[Realtime] AEC unavailable; playback uses continuous-silence fallback "
                "device_id=%s error=%s",
                self.device_id,
                exc,
            )
            return
        logger.info(
            "[Realtime] full-duplex uplink ready device_id=%s frame_ms=20 aec=webrtc delay_ms=%d",
            self.device_id,
            self._echo_canceller.delay_ms,
        )

    async def _uplink_loop(self) -> None:
        """Send exactly one 20 ms input frame per media-clock tick."""
        silence = bytes(PCM16_16K_20MS_BYTES)
        loop = asyncio.get_running_loop()
        deadline = loop.time()
        turn_real_bytes = 0
        try:
            while self.active:
                item: _UplinkItem | None = None
                # Commands are processed before the next clock frame.  At most
                # one real audio frame is consumed per tick; an empty tick is
                # filled with PCM silence rather than stopping the stream.
                while True:
                    try:
                        candidate = self._uplink_queue.get_nowait()
                    except asyncio.QueueEmpty:
                        break
                    if candidate.kind == "audio":
                        item = candidate
                        break
                    try:
                        if candidate.kind == "boundary":
                            turn_real_bytes = 0
                            self._input_pcm_bytes = 0
                        elif candidate.kind == "commit":
                            committed = turn_real_bytes > 0
                            if committed:
                                assert self.client is not None
                                self._manual_commits_pending += 1
                                try:
                                    await self.client.commit_audio()
                                except Exception:
                                    self._manual_commits_pending = max(
                                        0, self._manual_commits_pending - 1
                                    )
                                    raise
                                turn_real_bytes = 0
                                self._input_pcm_bytes = 0
                            if candidate.result is not None and not candidate.result.done():
                                candidate.result.set_result(committed)
                        elif candidate.kind == "endpoint":
                            if candidate.auto_commit_seq != self._provider_auto_commits:
                                logger.info(
                                    "[Realtime VAD] provider already committed device_id=%s duration_ms=%d",
                                    self.device_id,
                                    candidate.duration_ms,
                                )
                            elif turn_real_bytes > 0:
                                assert self.client is not None
                                self._manual_commits_pending += 1
                                try:
                                    await self.client.commit_audio()
                                except Exception:
                                    self._manual_commits_pending = max(
                                        0, self._manual_commits_pending - 1
                                    )
                                    raise
                                self._local_endpoint_commits += 1
                                logger.info(
                                    "[Realtime VAD] local endpoint commit device_id=%s duration_ms=%d pcm_bytes=%d",
                                    self.device_id,
                                    candidate.duration_ms,
                                    turn_real_bytes,
                                )
                                turn_real_bytes = 0
                                self._input_pcm_bytes = 0
                    finally:
                        self._uplink_queue.task_done()

                outbound = silence
                is_real = False
                if item is not None:
                    outbound = item.pcm
                    is_real = True
                    if self._echo_canceller is not None:
                        try:
                            outbound, _has_reference = self._echo_canceller.process_near_frame(outbound)
                        except Exception as exc:
                            logger.warning(
                                "[Realtime] AEC processing failed; disabling device_id=%s error=%s",
                                self.device_id,
                                exc,
                            )
                            self._echo_canceller = None
                        # A working AEC must never hard-mute the near-end user.
                        # The speaker-reference queue naturally runs dry before
                        # the device playback/tail guard because downlink audio
                        # is buffered and paced.  Muting merely because the
                        # current 20 ms tick has no reverse frame clips short
                        # follow-up questions immediately after "我在".  With a
                        # zero reverse frame WebRTC still preserves near speech;
                        # hard silence remains only the no-AEC safety fallback.
                    elif self.input_suppressed and self.settings.suppress_uplink_during_playback:
                        # The fallback preserves the provider media clock.  It
                        # never drops or pauses append events.
                        outbound = silence
                        is_real = False
                        self._uplink_echo_fallback_frames += 1
                else:
                    self._uplink_clock_frames += 1
                    # Advance the far-end reference even when the currently
                    # deployed firmware is not yet sending microphone frames
                    # during playback.  Otherwise old speaker audio would be
                    # applied to the next user's utterance.
                    if self._echo_canceller is not None:
                        try:
                            outbound, _ = self._echo_canceller.process_near_frame(silence)
                        except Exception as exc:
                            logger.warning(
                                "[Realtime] AEC clock failed; disabling device_id=%s error=%s",
                                self.device_id,
                                exc,
                            )
                            self._echo_canceller = None
                            outbound = silence

                client = self.client
                if client is None or not client.connected:
                    return
                await client.append_audio(outbound)
                if is_real:
                    turn_real_bytes += len(item.pcm) if item is not None else 0
                    self._input_pcm_bytes = turn_real_bytes
                if self._endpoint_vad_enabled and self._endpoint_vad is not None:
                    try:
                        self._endpoint_vad_queue.put_nowait(
                            (self._endpoint_vad_generation, outbound)
                        )
                    except asyncio.QueueFull:
                        self._endpoint_vad_dropped_frames += 1
                        if self._endpoint_vad_dropped_frames == 1:
                            logger.warning(
                                "[Realtime VAD] local endpoint queue full device_id=%s",
                                self.device_id,
                            )
                now = time.monotonic()
                if self._last_uplink_at > 0:
                    self._uplink_max_gap_ms = max(
                        self._uplink_max_gap_ms,
                        int(round((now - self._last_uplink_at) * 1000)),
                    )
                self._last_uplink_at = now
                self._uplink_frames_sent += 1
                if item is not None:
                    self._uplink_queue.task_done()

                deadline += 0.020
                delay = deadline - loop.time()
                if delay > 0:
                    await asyncio.sleep(delay)
                elif delay < -0.100:
                    # Never burst old silence after a scheduler/network stall.
                    deadline = loop.time()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            if not self._closing:
                self._failed = True
                logger.warning(
                    "[Realtime] uplink media clock stopped device_id=%s error=%s",
                    self.device_id,
                    exc,
                )
                self._schedule_recovery(f"uplink media clock: {exc}")

    async def _endpoint_vad_loop(self) -> None:
        """Run local endpointing off the strict 20 ms realtime media clock."""
        loop = asyncio.get_running_loop()
        try:
            while self.active:
                generation, pcm = await self._endpoint_vad_queue.get()
                try:
                    vad = self._endpoint_vad
                    if (
                        not self._endpoint_vad_enabled
                        or vad is None
                        or generation != self._endpoint_vad_generation
                    ):
                        continue
                    utterance = await loop.run_in_executor(None, vad.feed_pcm, pcm)
                    if (
                        not utterance
                        or generation != self._endpoint_vad_generation
                        or not self._endpoint_vad_enabled
                    ):
                        continue
                    await self._uplink_queue.put(
                        _UplinkItem(
                            "endpoint",
                            auto_commit_seq=self._provider_auto_commits,
                            duration_ms=int(len(utterance) / 32),
                        )
                    )
                finally:
                    self._endpoint_vad_queue.task_done()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            if not self._closing:
                logger.warning(
                    "[Realtime VAD] local endpoint stopped device_id=%s error=%s",
                    self.device_id,
                    exc,
                )

    def record_pb_ack(self, ack: dict[str, Any]) -> None:
        """Track how long realtime audio will remain audible on the device."""
        if str(ack.get("req") or "") != self._response_pb_req:
            return
        try:
            buffered_ms = max(0, int(ack.get("audio_buf_ms") or 0))
        except (TypeError, ValueError):
            buffered_ms = 0
        self._device_audio_buf_ms = buffered_ms
        self._extend_playback_guard(buffered_ms)

    async def cancel_response(self) -> None:
        client = self.client
        if client is not None and client.connected:
            try:
                await client.cancel_response()
            except Exception:
                logger.debug("[Realtime] response.cancel 失败", exc_info=True)
        await self._cancel_device_audio()

    async def close(self) -> None:
        if self._closing:
            return
        self._closing = True
        current = asyncio.current_task()
        tasks = [
            self._recv_task,
            self._uplink_task,
            self._endpoint_vad_task,
            self._play_task,
            self._idle_task,
            self._output_watchdog_task,
            self._tool_response_watchdog_task,
            self._recovery_task,
            *self._tool_tasks,
        ]
        self._recv_task = self._uplink_task = self._endpoint_vad_task = None
        self._play_task = self._idle_task = None
        self._output_watchdog_task = self._tool_response_watchdog_task = self._recovery_task = None
        self._tool_tasks.clear()
        for task in tasks:
            if task is not None and task is not current:
                task.cancel()
        client, self.client = self.client, None
        if client is not None:
            await client.close()
        for task in tasks:
            if task is not None and task is not current:
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass
        self._drain_play_queue()
        self._drain_uplink_queue()
        self._uplink_partial.clear()
        self._audio_buffer.clear()
        self._output_streaming = False
        self._device_audio_buf_ms = 0
        self._input_pcm_bytes = 0
        self._response_id = ""
        self._response_pb_req = ""
        self._response_chunk_idx = 0
        self._response_audio_received = False
        self._burst_end_queued = False
        self._stream_open = False
        self._play_started = False
        self._prebuffering = False
        self._last_output_audio_at = 0.0
        self._last_audio_delta_at = 0.0
        self._raw_pcm_meter_buffer.clear()
        self._raw_pcm_total_bytes = 0
        self._raw_pcm_bucket_idx = 0
        self._raw_pcm_max_gap_ms = 0
        self._output_opus_encoder = None
        self._suppress_input_until = 0.0
        self._echo_canceller = None
        self._endpoint_vad = None
        self._endpoint_vad_enabled = False
        self._endpoint_vad_generation += 1
        self._drain_endpoint_vad_queue()
        self._manual_commits_pending = 0
        self._provider_auto_commits = 0
        logger.info(
            "[Realtime] uplink clock closed device_id=%s frames=%d generated_silence=%d "
            "echo_fallback=%d local_endpoint_commits=%d vad_dropped=%d max_gap_ms=%d",
            self.device_id,
            self._uplink_frames_sent,
            self._uplink_clock_frames,
            self._uplink_echo_fallback_frames,
            self._local_endpoint_commits,
            self._endpoint_vad_dropped_frames,
            self._uplink_max_gap_ms,
        )
        self._uplink_frames_sent = 0
        self._uplink_clock_frames = 0
        self._uplink_echo_fallback_frames = 0
        self._local_endpoint_commits = 0
        self._endpoint_vad_dropped_frames = 0
        self._last_uplink_at = 0.0
        self._uplink_max_gap_ms = 0
        self._closing = False

    def _touch(self) -> None:
        self._last_activity = time.monotonic()

    async def _idle_watch(self) -> None:
        try:
            while self.active:
                await asyncio.sleep(2.0)
                if time.monotonic() - self._last_activity >= self.settings.conversation_idle_sec:
                    logger.info("[Realtime] 空闲会话关闭 device_id=%s", self.device_id)
                    await self.close()
                    return
        except asyncio.CancelledError:
            raise

    async def _receive_loop(self) -> None:
        assert self.client is not None
        try:
            async for event in self.client.events():
                event_type = str(event.get("type") or "")
                if event_type == "input_audio_buffer.committed":
                    if self._manual_commits_pending > 0:
                        self._manual_commits_pending -= 1
                        source = "local"
                    else:
                        self._provider_auto_commits += 1
                        source = "provider"
                        # Invalidate the local recognizer's copy of the same
                        # utterance before its slower ONNX result can enqueue a
                        # duplicate manual commit.
                        self._reset_endpoint_vad()
                    await self._mark_input_boundary()
                    logger.info(
                        "[Realtime VAD] input committed device_id=%s source=%s auto_seq=%d",
                        self.device_id,
                        source,
                        self._provider_auto_commits,
                    )
                elif event_type == "conversation.item.input_audio_transcription.started":
                    if self._output_streaming or self._stream_open or self.input_suppressed:
                        try:
                            if self._output_streaming:
                                await self.client.cancel_response()
                        except Exception:
                            logger.debug("[Realtime] barge-in response.cancel failed", exc_info=True)
                        await self._cancel_device_audio()
                elif event_type == "conversation.item.input_audio_transcription.completed":
                    transcript = str(event.get("transcript") or event.get("text") or "")
                    if transcript.strip():
                        self._touch()
                    logger.info("[Realtime ASR] device_id=%s text=%r", self.device_id, transcript)
                elif event_type == "response.output_audio.started":
                    self._touch()
                    self._cancel_tool_response_watchdog()
                    await self._start_response(str(event.get("response_id") or uuid.uuid4().hex[:12]))
                elif event_type == "response.output_audio.delta":
                    self._touch()
                    await self._queue_audio_delta(event)
                elif event_type == "response.output_audio.done":
                    self._touch()
                    await self._finish_output("done")
                elif event_type == "response.output_text.done":
                    text = str(event.get("text") or "")
                    if text.strip():
                        self._touch()
                    logger.info("[Realtime LLM] device_id=%s text=%r", self.device_id, text)
                elif event_type == "conversation.item.created":
                    self._remember_function_call(event)
                elif event_type == "response.function_call_arguments.done":
                    self._touch()
                    self._schedule_function_calls(event)
                elif event_type == "error":
                    error_text = self.client._error_text(event)
                    logger.warning("[Realtime] 服务端错误 device_id=%s error=%s", self.device_id, error_text)
                    await self._fail_and_recover(error_text)
                    return
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            if not self._closing:
                self._failed = True
                logger.warning("[Realtime] 接收循环结束 device_id=%s error=%s", self.device_id, exc)

    async def _start_response(self, response_id: str) -> None:
        self._response_generation += 1
        self._drain_play_queue()
        self._response_id = response_id
        self._response_pb_req = f"rt_{uuid.uuid4().hex[:12]}"
        self._response_chunk_idx = 0
        self._response_audio_received = False
        self._burst_end_queued = False
        self._stream_open = False
        self._play_started = False
        self._prebuffering = False
        self._first_audio_chunk = True
        self._audio_buffer.clear()
        self._output_streaming = True
        await self._mark_input_boundary()
        self._last_output_audio_at = time.monotonic()
        self._last_audio_delta_at = 0.0
        self._raw_pcm_meter_buffer.clear()
        self._raw_pcm_total_bytes = 0
        self._raw_pcm_bucket_idx = 0
        self._raw_pcm_max_gap_ms = 0
        self._output_opus_encoder = None
        self._suppress_input_until = 0.0
        self._reset_endpoint_vad()
        self._arm_output_watchdog(response_id)

    async def _queue_audio_delta(self, event: dict[str, Any]) -> None:
        raw = str(event.get("delta") or "")
        if not raw:
            return
        try:
            pcm = base64.b64decode(raw, validate=True)
        except Exception:
            logger.warning("[Realtime] 下行音频 base64 解码失败 device_id=%s", self.device_id)
            return
        now = time.monotonic()
        if self._last_audio_delta_at > 0:
            self._raw_pcm_max_gap_ms = max(
                self._raw_pcm_max_gap_ms,
                int(round((now - self._last_audio_delta_at) * 1000)),
            )
        self._last_audio_delta_at = now
        self._last_output_audio_at = now
        self._response_audio_received = True
        self._burst_end_queued = False
        self._meter_raw_pcm(pcm)
        self._audio_buffer.extend(pcm)
        target = max(3840, int(24000 * 2 * self.settings.downlink_chunk_ms / 1000))
        target -= target % 2
        while len(self._audio_buffer) >= target:
            chunk = bytes(self._audio_buffer[:target])
            del self._audio_buffer[:target]
            await self._enqueue_play(chunk, end_burst=False)

    async def _flush_audio_buffer(self) -> None:
        if self._burst_end_queued or not self._response_audio_received:
            return
        if self._audio_buffer:
            chunk = bytes(self._audio_buffer)
            self._audio_buffer.clear()
            if len(chunk) % 2:
                chunk = chunk[:-1]
            await self._enqueue_play(chunk, end_burst=True)
        else:
            # An exact-size final chunk still needs a pb_end.  A tiny silent
            # packet closes the ESP32 stream without adding audible content.
            await self._enqueue_play(b"", end_burst=True)
        self._burst_end_queued = True

    async def _enqueue_play(self, pcm: bytes, *, end_burst: bool) -> None:
        if not pcm and not end_burst:
            return
        try:
            self._play_queue.put_nowait((self._response_generation, pcm, end_burst))
        except asyncio.QueueFull:
            logger.warning("[Realtime] 设备下行队列已满，丢弃音频 device_id=%s", self.device_id)

    @staticmethod
    def _pcm_levels(pcm: bytes) -> tuple[int, int]:
        """Return RMS and absolute peak for little-endian mono PCM16."""
        usable = len(pcm) & ~1
        if usable <= 0:
            return 0, 0
        samples = array("h")
        samples.frombytes(pcm[:usable])
        if not samples:
            return 0, 0
        square_sum = sum(int(value) * int(value) for value in samples)
        rms = int(round(math.sqrt(square_sum / len(samples))))
        peak = max(abs(int(value)) for value in samples)
        return rms, peak

    def _meter_raw_pcm(self, pcm: bytes) -> None:
        """Log provider PCM levels in one-second buckets before any device processing."""
        if not pcm:
            return
        self._raw_pcm_total_bytes += len(pcm)
        self._raw_pcm_meter_buffer.extend(pcm)
        bucket_bytes = 24000 * 2
        while len(self._raw_pcm_meter_buffer) >= bucket_bytes:
            bucket = bytes(self._raw_pcm_meter_buffer[:bucket_bytes])
            del self._raw_pcm_meter_buffer[:bucket_bytes]
            rms, peak = self._pcm_levels(bucket)
            start_ms = self._raw_pcm_bucket_idx * 1000
            self._raw_pcm_bucket_idx += 1
            logger.info(
                "[Realtime PCM] device_id=%s response_id=%s range_ms=%d-%d raw_rms=%d raw_peak=%d max_delta_gap_ms=%d",
                self.device_id,
                self._response_id,
                start_ms,
                start_ms + 1000,
                rms,
                peak,
                self._raw_pcm_max_gap_ms,
            )

    def _log_final_pcm_levels(self, reason: str) -> None:
        if self._raw_pcm_meter_buffer:
            rms, peak = self._pcm_levels(bytes(self._raw_pcm_meter_buffer))
            start_ms = self._raw_pcm_bucket_idx * 1000
            duration_ms = int(round(len(self._raw_pcm_meter_buffer) / 48.0))
            logger.info(
                "[Realtime PCM] device_id=%s response_id=%s range_ms=%d-%d raw_rms=%d raw_peak=%d max_delta_gap_ms=%d",
                self.device_id,
                self._response_id,
                start_ms,
                start_ms + duration_ms,
                rms,
                peak,
                self._raw_pcm_max_gap_ms,
            )
            self._raw_pcm_meter_buffer.clear()
        logger.info(
            "[Realtime PCM] device_id=%s response_id=%s done=%s total_ms=%d max_delta_gap_ms=%d",
            self.device_id,
            self._response_id,
            reason,
            int(round(self._raw_pcm_total_bytes / 48.0)),
            self._raw_pcm_max_gap_ms,
        )

    async def _play_loop(self) -> None:
        try:
            while True:
                item = await self._play_queue.get()
                if item is None:
                    return
                if item[0] != self._response_generation:
                    self._play_queue.task_done()
                    continue
                generation = item[0]
                batch = [item]
                buffered_ms = self._play_item_duration_ms(item)
                saw_stop = False
                processed = 0
                try:
                    if not self._play_started:
                        self._prebuffering = True
                        target_ms = max(0, int(self.settings.downlink_start_buffer_ms))
                        # Do not wait forever for a provider that emits a few
                        # seconds and then pauses.  Release the partial startup
                        # buffer after a short quiet gap so the user hears the
                        # response and the microphone cannot remain guarded by
                        # an inaudible server-side queue.
                        startup_wait_sec = max(
                            0.15,
                            min(1.0, float(self.settings.output_stall_timeout_sec) / 4.0),
                        )
                        while (
                            buffered_ms < target_ms
                            and not batch[-1][2]
                            and generation == self._response_generation
                        ):
                            if not self._play_queue.empty():
                                next_item = self._play_queue.get_nowait()
                            else:
                                try:
                                    next_item = await asyncio.wait_for(
                                        self._play_queue.get(), timeout=startup_wait_sec
                                    )
                                except asyncio.TimeoutError:
                                    logger.warning(
                                        "[Realtime] startup buffer partial release "
                                        "device_id=%s buffered_ms=%d target_ms=%d",
                                        self.device_id,
                                        buffered_ms,
                                        target_ms,
                                    )
                                    break
                            if next_item is None:
                                saw_stop = True
                                break
                            if next_item[0] != generation:
                                # A new response replaced this startup batch.
                                # Put its first item back rather than mixing two
                                # independent Opus streams.
                                self._play_queue.task_done()
                                self._play_queue.put_nowait(next_item)
                                break
                            batch.append(next_item)
                            buffered_ms += self._play_item_duration_ms(next_item)
                        if generation == self._response_generation:
                            self._play_started = True
                        logger.debug(
                            "[Realtime] startup jitter buffer ready device_id=%s buffered_ms=%d chunks=%d",
                            self.device_id,
                            buffered_ms,
                            len(batch),
                        )
                    for generation, pcm, end_burst in batch:
                        try:
                            if generation == self._response_generation:
                                await self._send_pcm_to_device(pcm, end_burst=end_burst)
                        finally:
                            self._play_queue.task_done()
                            processed += 1
                finally:
                    self._prebuffering = False
                    for _ in batch[processed:]:
                        self._play_queue.task_done()
                if saw_stop:
                    return
        except asyncio.CancelledError:
            raise

    @staticmethod
    def _play_item_duration_ms(item: tuple[int, bytes, bool]) -> int:
        _, pcm, end_burst = item
        if pcm:
            return max(1, int(len(pcm) / 2 / 24000 * 1000))
        return 20 if end_burst else 0

    async def _send_pcm_to_device(self, pcm: bytes, *, end_burst: bool = False) -> None:
        if not self._response_pb_req:
            await self._start_response(uuid.uuid4().hex[:12])
        wire_pcm = pcm
        if not wire_pcm and end_burst:
            wire_pcm = b"\x00\x00" * 480  # 20 ms at 24 kHz, inaudible stream terminator.
        samples = memoryview(wire_pcm).cast("h") if len(wire_pcm) >= 2 else ()
        energy = sum(abs(int(v)) for v in samples[:: max(1, len(samples) // 256)]) if samples else 0
        denom = max(1, len(samples[:: max(1, len(samples) // 256)])) if samples else 1
        avg = energy / denom
        phoneme = "_" if avg < 260 else ("a" if avg > 1800 else "o")
        duration_ms = max(1, int(len(wire_pcm) / 2 / 24000 * 1000))
        action = PB_ACTION_REPLACE if self._first_audio_chunk else PB_ACTION_APPEND
        # The deployed board's known-good speaker path is Opus (the wake ACK
        # uses it); raw s16le frames are acknowledged but produce no sound on
        # that firmware.  Keep one encoder for the full response so prediction
        # state remains continuous across PB chunks.
        realtime_tts_cfg = dict(self.pipeline.tts_cfg)
        realtime_tts_cfg["output_codec"] = "s16le"
        pairs, _, _, _ = build_pb_wire_pairs(
            [{"pcm": wire_pcm, "ms": duration_ms, "phoneme": phoneme}],
            realtime_tts_cfg,
            sample_rate=24000,
            request_id=self._response_pb_req,
            device_id=self.device_id,
            action=action,
        )
        if self._output_opus_encoder is None:
            self._output_opus_encoder = OpusStreamBatchEncoder(24000)
        opus_blob, opus_frames = self._output_opus_encoder.encode(wire_pcm, final=end_burst)
        if not opus_blob or opus_frames <= 0:
            logger.warning(
                "[Realtime] Opus 编码无输出 device_id=%s pcm_bytes=%d final=%s",
                self.device_id,
                len(wire_pcm),
                end_burst,
            )
            return
        for pair in pairs:
            pair[0]["fmt"] = "opus"
            pair[0]["audio"] = {"next_bin_len": len(opus_blob), "frames": opus_frames}
            pair[1][:] = [opus_blob]
        stream_was_open = self._stream_open
        if stream_was_open:
            frame_type = "pb_end" if end_burst else "pb_chunk"
        else:
            frame_type = "pb_single" if end_burst else "pb_start"
        for pair in pairs:
            pair[0]["type"] = frame_type
            pair[0]["idx"] = self._response_chunk_idx
        pacing_ms = int(self.settings.downlink_pacing_ms)
        # ``chunk_gap_sec`` is a post-send sleep; network/write time is extra.
        # Keep a modest cushion on the ESP32 without filling its five-job
        # speaker queue and forcing the firmware to discard old audio.
        if self._device_audio_buf_ms < 180:
            pacing_ms = max(60, pacing_ms - 25)
        elif self._device_audio_buf_ms > 420:
            pacing_ms = min(duration_ms, pacing_ms + 15)
        pacing_sec = 0.0 if frame_type == "pb_start" else pacing_ms / 1000.0
        if self._echo_canceller is not None and wire_pcm:
            # Feed the reverse stream before the device starts rendering it;
            # ``echo_delay_ms`` accounts for websocket, board queue and I2S.
            self._echo_canceller.queue_speaker_pcm24k(wire_pcm)
        delivered = await self.hub.send_pb_chain_ordered(
            self.device_id,
            [pair[0] for pair in pairs],
            binaries_per_frame=[list(pair[1]) for pair in pairs],
            chunk_gap_sec=pacing_sec,
        )
        if delivered:
            self._first_audio_chunk = False
            self._response_chunk_idx += 1
            self._stream_open = frame_type in {"pb_start", "pb_chunk"}
            self._extend_playback_guard(duration_ms)
        if end_burst:
            self._output_opus_encoder = None

    async def _cancel_device_audio(self) -> None:
        self._response_generation += 1
        self._cancel_output_watchdog()
        self._audio_buffer.clear()
        self._drain_play_queue()
        self._output_streaming = False
        self._stream_open = False
        self._response_audio_received = False
        self._burst_end_queued = False
        self._play_started = False
        self._prebuffering = False
        self._device_audio_buf_ms = 0
        self._last_output_audio_at = 0.0
        self._last_audio_delta_at = 0.0
        self._raw_pcm_meter_buffer.clear()
        self._raw_pcm_total_bytes = 0
        self._raw_pcm_bucket_idx = 0
        self._raw_pcm_max_gap_ms = 0
        self._output_opus_encoder = None
        if self._echo_canceller is not None:
            self._echo_canceller.clear_reference()
        # A confirmed near-end utterance is a barge-in.  Do not keep the old
        # device buffer guard alive after pb_cancel, or the beginning of the
        # user's next sentence would be replaced by fallback silence.
        self._suppress_input_until = 0.0
        if not self._response_pb_req:
            return
        req = self._response_pb_req
        self._response_pb_req = ""
        try:
            await self.hub.send(self.device_id, {"type": "pb_cancel", "req": req})
        except Exception:
            logger.debug("[Realtime] pb_cancel 失败 device_id=%s", self.device_id, exc_info=True)

    async def _finish_output(self, reason: str) -> None:
        await self._flush_audio_buffer()
        self._log_final_pcm_levels(reason)
        self._output_streaming = False
        self._last_output_audio_at = 0.0
        self._extend_playback_guard(self._device_audio_buf_ms)
        self._cancel_output_watchdog()
        logger.debug("[Realtime] 下行结束 device_id=%s reason=%s", self.device_id, reason)

    async def _fail_and_recover(self, reason: str) -> None:
        """Release local playback state and replace a session that can no longer progress."""
        await self._cancel_device_audio()
        self._failed = True
        self._schedule_recovery(reason)

    def _arm_output_watchdog(self, response_id: str) -> None:
        self._cancel_output_watchdog()
        self._output_watchdog_task = asyncio.create_task(
            self._watch_output(response_id),
            name=f"realtime-output-watch:{self.device_id}",
        )

    def _cancel_output_watchdog(self) -> None:
        task, self._output_watchdog_task = self._output_watchdog_task, None
        if task is not None and task is not asyncio.current_task() and not task.done():
            task.cancel()

    async def _watch_output(self, response_id: str) -> None:
        timeout = max(1.0, float(self.settings.output_stall_timeout_sec))
        try:
            while self._output_streaming and self._response_id == response_id:
                elapsed = time.monotonic() - self._last_output_audio_at
                remaining = timeout - elapsed
                if remaining > 0:
                    await asyncio.sleep(remaining)
                    continue
                logger.warning(
                    "[Realtime] 下行音频超时，重建会话 device_id=%s response_id=%s idle_sec=%.2f",
                    self.device_id,
                    response_id,
                    elapsed,
                )
                client = self.client
                if client is not None and client.connected:
                    try:
                        await client.cancel_response()
                    except Exception:
                        logger.debug("[Realtime] 超时 response.cancel 失败", exc_info=True)
                await self._fail_and_recover(f"output audio stalled for {elapsed:.2f}s")
                return
        except asyncio.CancelledError:
            raise
        finally:
            if self._output_watchdog_task is asyncio.current_task():
                self._output_watchdog_task = None

    def _schedule_recovery(self, reason: str) -> None:
        if self._closing:
            return
        if self._recovery_task is not None and not self._recovery_task.done():
            return
        self._recovery_task = asyncio.create_task(
            self._restart_after_error(reason),
            name=f"realtime-recover:{self.device_id}",
        )

    async def _restart_after_error(self, reason: str) -> None:
        try:
            await asyncio.sleep(max(0.05, float(self.settings.reconnect_delay_sec)))
            if self._closing:
                return
            await self.close()
            if await self.start():
                logger.info("[Realtime] 会话已自动恢复 device_id=%s reason=%s", self.device_id, reason)
            else:
                logger.warning("[Realtime] 会话自动恢复失败 device_id=%s reason=%s", self.device_id, reason)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("[Realtime] 会话自动恢复异常 device_id=%s error=%s", self.device_id, exc)
        finally:
            if self._recovery_task is asyncio.current_task():
                self._recovery_task = None

    def _extend_playback_guard(self, buffered_ms: int) -> None:
        tail_ms = max(0, int(self.settings.playback_tail_ms))
        until = time.monotonic() + (max(0, int(buffered_ms)) + tail_ms) / 1000.0
        if until > self._suppress_input_until:
            self._suppress_input_until = until

    def _reset_endpoint_vad(self) -> None:
        self._endpoint_vad_generation += 1
        self._drain_endpoint_vad_queue()
        if self._endpoint_vad_factory is None:
            self._endpoint_vad = None
            return
        try:
            self._endpoint_vad = self._endpoint_vad_factory()
        except Exception as exc:
            self._endpoint_vad = None
            logger.warning(
                "[Realtime VAD] local endpoint unavailable device_id=%s error=%s",
                self.device_id,
                exc,
            )

    def _drain_endpoint_vad_queue(self) -> None:
        while True:
            try:
                self._endpoint_vad_queue.get_nowait()
            except asyncio.QueueEmpty:
                return
            self._endpoint_vad_queue.task_done()

    def _drain_play_queue(self) -> None:
        while True:
            try:
                item = self._play_queue.get_nowait()
            except asyncio.QueueEmpty:
                return
            if item is not None:
                self._play_queue.task_done()

    def _drain_uplink_queue(self) -> None:
        while True:
            try:
                item = self._uplink_queue.get_nowait()
            except asyncio.QueueEmpty:
                return
            if item.result is not None and not item.result.done():
                item.result.set_result(False)
            self._uplink_queue.task_done()

    def _remember_function_call(self, event: dict[str, Any]) -> None:
        item = event.get("item")
        if not isinstance(item, dict) or str(item.get("type") or "") != "function_call":
            return
        call_id = str(item.get("call_id") or "").strip()
        name = str(item.get("name") or "").strip()
        if call_id and name:
            self._pending_tool_names[call_id] = name
            logger.info(
                "[Realtime tool] requested device_id=%s tool=%s call_id=%s",
                self.device_id,
                name,
                call_id,
            )

    def _schedule_function_calls(self, event: dict[str, Any]) -> None:
        task = asyncio.create_task(
            self._handle_function_calls(event),
            name=f"realtime-tools:{self.device_id}",
        )
        self._tool_tasks.add(task)
        task.add_done_callback(self._tool_tasks.discard)

    async def _handle_function_calls(self, event: dict[str, Any]) -> None:
        # Current Fire events carry scalar call_id/arguments.  Accept the old
        # batched shape too so in-flight sessions can survive a provider rollout.
        raw_items = event.get("items")
        items = raw_items if isinstance(raw_items, list) else [event]
        for item in items:
            if isinstance(item, dict):
                await self._handle_function_call(item)

    async def _handle_function_call(self, item: dict[str, Any]) -> None:
        client = self.client
        if client is None:
            return
        call_id = str(item.get("call_id") or "").strip()
        if not call_id or call_id in self._tool_calls_inflight or call_id in self._completed_tool_calls:
            return
        name = str(item.get("name") or self._pending_tool_names.get(call_id) or "").strip()
        self._tool_calls_inflight.add(call_id)
        started = time.monotonic()
        try:
            if not name:
                raise ValueError("provider did not supply a function name")
            raw_arguments = item.get("arguments")
            arguments = raw_arguments if isinstance(raw_arguments, dict) else json.loads(str(raw_arguments or "{}"))
            if not isinstance(arguments, dict):
                arguments = {}
            text = await self._run_tool(name, arguments)
        except Exception as exc:
            logger.exception("[Realtime tool] 执行失败 device_id=%s tool=%s", self.device_id, name)
            text = json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False)
        try:
            await client.send_tool_output(call_id, text)
            spoken_result = self._tool_spoken_result(name, text)
            if spoken_result:
                # This dialogue endpoint rejects the generic response.create
                # event.  Its supported speech_text_buffer commit provides a
                # deterministic continuation for a completed visual tool and
                # avoids another LLM pass over the already-natural VLM answer.
                await client.commit_greeting(spoken_result)
                self._arm_tool_response_watchdog(call_id)
            logger.info(
                "[Realtime tool] completed device_id=%s tool=%s call_id=%s spoken=%s elapsed_ms=%d",
                self.device_id,
                name,
                call_id,
                bool(spoken_result),
                round((time.monotonic() - started) * 1000),
            )
        finally:
            self._tool_calls_inflight.discard(call_id)
            self._completed_tool_calls.add(call_id)
            self._pending_tool_names.pop(call_id, None)

    @staticmethod
    def _tool_spoken_result(name: str, output: str) -> str:
        if name != "inspect_camera":
            return ""
        try:
            result = json.loads(output)
        except (TypeError, ValueError, json.JSONDecodeError):
            return str(output or "").strip()[:1000]
        if not isinstance(result, dict):
            return ""
        answer = str(result.get("answer") or "").strip()
        if answer:
            return answer[:1000]
        error = str(result.get("error") or "").strip()
        return (error or "我暂时没看清楚。")[:1000]

    def _cancel_tool_response_watchdog(self) -> None:
        task, self._tool_response_watchdog_task = self._tool_response_watchdog_task, None
        if task is not None and task is not asyncio.current_task() and not task.done():
            task.cancel()

    def _arm_tool_response_watchdog(self, call_id: str) -> None:
        self._cancel_tool_response_watchdog()
        generation = self._response_generation

        async def _watch() -> None:
            try:
                await asyncio.sleep(max(1.0, float(self.settings.output_stall_timeout_sec)))
                if not self.active or self._response_generation != generation:
                    return
                logger.warning(
                    "[Realtime tool] continuation response stalled device_id=%s call_id=%s",
                    self.device_id,
                    call_id,
                )
                await self._fail_and_recover(f"tool continuation stalled: {call_id}")
            except asyncio.CancelledError:
                raise
            finally:
                if self._tool_response_watchdog_task is asyncio.current_task():
                    self._tool_response_watchdog_task = None

        self._tool_response_watchdog_task = asyncio.create_task(
            _watch(),
            name=f"realtime-tool-response-watchdog:{self.device_id}",
        )

    async def _run_tool(self, name: str, arguments: dict[str, Any]) -> str:
        if name == "inspect_camera":
            return await self._inspect_camera(str(arguments.get("question") or "摄像头前是什么？"))
        tool = dict(arguments)
        tool["tool"] = name
        results = await execute_llm_tools(
            [tool],
            device_id=self.device_id,
            asr_chat_hub=self.hub,
            cam_fps=5,
        )
        slim: list[dict[str, Any]] = []
        for result in results:
            row = {key: value for key, value in result.items() if key not in {"jpeg_base64", "image_display"}}
            slim.append(row)
        return json.dumps(slim, ensure_ascii=False, default=str)

    async def _inspect_camera(self, question: str) -> str:
        results = await execute_llm_tools(
            [{"tool": "capture_camera"}],
            device_id=self.device_id,
            asr_chat_hub=self.hub,
            cam_fps=5,
        )
        result = results[0] if results else {}
        b64 = str(result.get("jpeg_base64") or "")
        if not result.get("ok") or not b64:
            return json.dumps({"ok": False, "error": result.get("error") or "未获取到摄像头画面"}, ensure_ascii=False)
        image_url = b64 if b64.startswith("data:image/") else f"data:image/jpeg;base64,{b64}"
        content: list[dict[str, str]] = [
            {
                "type": "input_text",
                "text": (
                    f"用户的问题：{question}\n"
                    "只依据这张实时摄像头图片回答。优先识别用户展示的主体；看不清就明确说看不清。"
                    "回答用简短自然中文，不要提及模型、工具或分析过程。"
                ),
            },
            {"type": "input_image", "image_url": image_url},
        ]
        base_cfg = resolve_llm_config(self.device_id)
        vision_model = str(self.settings.vision_model or base_cfg.model).strip()
        vision_cfg = replace(
            base_cfg,
            model=vision_model,
            display_name=f"Seed VLM ({vision_model})",
        )
        started = time.monotonic()
        answer, _meta = await chat_acompletion(
            [{"role": "user", "content": content}],
            device_id=self.device_id,
            config=vision_cfg,
            temperature=0.1,
            json_mode=False,
        )
        visible = str(answer or "").strip()
        logger.info(
            "[Realtime VLM] device_id=%s model=%s elapsed_ms=%d answer=%r",
            self.device_id,
            vision_model,
            round((time.monotonic() - started) * 1000),
            visible[:200],
        )
        if not visible:
            return json.dumps({"ok": False, "error": "视觉模型没有返回结果"}, ensure_ascii=False)
        return json.dumps({"ok": True, "answer": visible[:1000]}, ensure_ascii=False)
