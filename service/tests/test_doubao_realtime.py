from __future__ import annotations

import base64
import asyncio
import json
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import patch

from deskbot_server.core.settings import RealtimeSettings
from deskbot_server.infrastructure.llm.runtime import ResolvedLlmConfig
from deskbot_server.infrastructure.realtime.doubao_duplex import DoubaoDuplexClient, build_auth_headers
from deskbot_server.service.application.doubao_realtime_bridge import DoubaoRealtimeBridge, build_robot_tools


def test_legacy_auth_uses_app_id_and_access_token_only():
    cfg = RealtimeSettings(app_id="app", access_token="token")
    headers = build_auth_headers(cfg, request_id="request")
    assert headers == {
        "X-Api-App-Id": "app",
        "X-Api-Access-Key": "token",
        "X-Api-Resource-Id": "volc.speech.dialog",
        "X-Api-App-Key": "PlgvMymc7f3tQnJ6",
        "X-Api-Request-Id": "request",
    }
    assert all("secret" not in key.lower() for key in headers)


def test_new_console_api_key_takes_precedence():
    cfg = RealtimeSettings(api_key="seed-key", app_id="app", access_token="token")
    assert build_auth_headers(cfg) == {"X-Api-Key": "seed-key"}


def test_session_create_matches_current_json_protocol():
    cfg = RealtimeSettings(instructions="你是包逗")
    client = DoubaoDuplexClient(cfg, tools=build_robot_tools())
    event = client.build_session_create_event()
    assert event["type"] == "session.create"
    assert event["session"]["type"] == "realtime"
    assert event["session"]["model"] == "1.2.6.1"
    assert event["session"]["audio"]["input"]["format"] == {"type": "pcm", "rate": 16000}
    assert event["session"]["audio"]["output"]["format"] == {"type": "pcm_s16le", "rate": 24000}
    assert event["session"]["audio"]["output"]["speed"] == 0
    assert event["session"]["audio"]["output"]["loudness"] == 40
    assert event["extension"]["asr"]["extra"]["end_smooth_window_ms"] == 800
    assert event["extension"]["dialog"]["extra"]["enable_loudness_norm"] is True
    assert event["extension"]["dialog"]["extra"]["input_mod"] == "keep_alive"
    assert {tool["name"] for tool in event["session"]["tools"]} >= {
        "move_head",
        "set_expression",
        "set_volume",
        "set_listening_profile",
        "set_camera_follow",
        "inspect_camera",
    }


def test_audio_append_is_base64_json_text_frame():
    class FakeWs:
        def __init__(self):
            self.frames: list[str] = []

        async def send(self, frame: str) -> None:
            self.frames.append(frame)

    async def run():
        client = DoubaoDuplexClient(RealtimeSettings())
        fake = FakeWs()
        client.ws = fake
        await client.append_audio(b"\x01\x02\x03\x04")
        return fake

    fake = asyncio.run(run())
    event = json.loads(fake.frames[0])
    assert event["type"] == "input_audio_buffer.append"
    assert event["event_id"].startswith("event_")
    assert base64.b64decode(event["audio"]) == b"\x01\x02\x03\x04"


def test_realtime_greeting_uses_documented_speech_text_commit():
    class FakeWs:
        def __init__(self):
            self.frames: list[str] = []

        async def send(self, frame: str) -> None:
            self.frames.append(frame)

    async def run():
        client = DoubaoDuplexClient(RealtimeSettings())
        fake = FakeWs()
        client.ws = fake
        await client.commit_greeting("我在")
        return json.loads(fake.frames[0])

    event = asyncio.run(run())
    assert event["type"] == "speech_text_buffer.commit"
    assert event["text"] == "我在"
    assert event["event_id"].startswith("event_")


def test_tool_output_uses_documented_function_call_output_item():
    class FakeWs:
        def __init__(self):
            self.frames: list[str] = []

        async def send(self, frame: str) -> None:
            self.frames.append(frame)

    async def run():
        client = DoubaoDuplexClient(RealtimeSettings())
        fake = FakeWs()
        client.ws = fake
        await client.send_tool_output("call-camera-1", '{"ok":true}')
        return json.loads(fake.frames[0])

    event = asyncio.run(run())
    assert event["type"] == "conversation.item.create"
    assert "items" not in event
    assert event["item"] == {
        "type": "function_call_output",
        "call_id": "call-camera-1",
        "output": '{"ok":true}',
    }


class _FakeRealtimeClient:
    connected = True

    def __init__(self) -> None:
        self.audio: list[bytes] = []
        self.commits = 0
        self.cancels = 0
        self.greetings: list[str] = []
        self.tool_outputs: list[tuple[str, str]] = []

    async def append_audio(self, pcm: bytes) -> None:
        self.audio.append(pcm)

    async def commit_audio(self) -> None:
        self.commits += 1

    async def cancel_response(self) -> None:
        self.cancels += 1

    async def send_tool_output(self, call_id: str, output: str) -> None:
        self.tool_outputs.append((call_id, output))

    async def commit_greeting(self, text: str) -> None:
        self.greetings.append(text)

    @staticmethod
    def _error_text(event: dict) -> str:
        return str(event.get("error") or "test error")


def _bridge(*, suppress: bool = True) -> DoubaoRealtimeBridge:
    settings = RealtimeSettings(
        enabled=True,
        api_key="test-key",
        suppress_uplink_during_playback=suppress,
        playback_tail_ms=450,
    )
    pipeline = SimpleNamespace(settings=SimpleNamespace(realtime=settings))
    bridge = DoubaoRealtimeBridge(pipeline=pipeline, device_id="robot", asr_chat_hub=SimpleNamespace())
    bridge.client = _FakeRealtimeClient()
    return bridge


async def _start_uplink_clock(bridge: DoubaoRealtimeBridge) -> asyncio.Task:
    task = asyncio.create_task(bridge._uplink_loop())
    bridge._uplink_task = task
    return task


async def _stop_uplink_clock(task: asyncio.Task) -> None:
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)


def test_bridge_only_commits_audio_that_was_actually_forwarded():
    async def run() -> None:
        bridge = _bridge()
        client = bridge.client
        task = await _start_uplink_clock(bridge)
        try:
            assert await bridge.commit() is False
            assert await bridge.send_audio(b"\x00\x01" * 320) is True
            assert await bridge.commit() is True
            assert client.commits == 1
        finally:
            await _stop_uplink_clock(task)

    asyncio.run(run())


def test_realtime_function_call_joins_created_item_with_arguments_done():
    async def run() -> None:
        bridge = _bridge()
        client = bridge.client
        calls: list[tuple[str, dict]] = []

        async def fake_run_tool(name: str, arguments: dict) -> str:
            calls.append((name, arguments))
            return '{"ok":true,"answer":"这是一本书"}'

        bridge._run_tool = fake_run_tool
        bridge._remember_function_call(
            {
                "type": "conversation.item.created",
                "item": {
                    "type": "function_call",
                    "call_id": "call-vlm-1",
                    "name": "inspect_camera",
                },
            }
        )
        event = {
            "type": "response.function_call_arguments.done",
            "call_id": "call-vlm-1",
            "arguments": '{"question":"这是什么"}',
        }
        await bridge._handle_function_calls(event)
        await bridge._handle_function_calls(event)  # duplicate provider event is idempotent

        assert calls == [("inspect_camera", {"question": "这是什么"})]
        assert client.tool_outputs == [
            ("call-vlm-1", '{"ok":true,"answer":"这是一本书"}')
        ]
        assert client.greetings == ["这是一本书"]
        assert bridge._tool_response_watchdog_task is not None
        bridge._tool_response_watchdog_task.cancel()
        await asyncio.gather(bridge._tool_response_watchdog_task, return_exceptions=True)

    asyncio.run(run())


def test_inspect_camera_delegates_current_frame_to_configured_seed_vlm():
    async def run() -> None:
        bridge = _bridge()
        bridge.settings = replace(
            bridge.settings,
            vision_model="doubao-seed-2-1-turbo-260628",
        )
        observed: dict = {}

        async def fake_capture(*_args, **_kwargs):
            return [{"ok": True, "jpeg_base64": "AQID"}]

        async def fake_completion(messages, **kwargs):
            observed["messages"] = messages
            observed["config"] = kwargs["config"]
            observed["json_mode"] = kwargs["json_mode"]
            return "这是一本蓝色封面的书。", {"model": kwargs["config"].model}

        base_cfg = ResolvedLlmConfig(
            model="ordinary-chat-model",
            api_key="test-key",
            api_base="https://ark.cn-beijing.volces.com/api/v3",
            protocol="ark_responses",
            source="test",
            display_name="test",
        )
        with (
            patch(
                "deskbot_server.service.application.doubao_realtime_bridge.execute_llm_tools",
                fake_capture,
            ),
            patch(
                "deskbot_server.service.application.doubao_realtime_bridge.resolve_llm_config",
                return_value=base_cfg,
            ),
            patch(
                "deskbot_server.service.application.doubao_realtime_bridge.chat_acompletion",
                fake_completion,
            ),
        ):
            result = json.loads(await bridge._inspect_camera("我拿的是什么"))

        assert result == {"ok": True, "answer": "这是一本蓝色封面的书。"}
        assert observed["config"].model == "doubao-seed-2-1-turbo-260628"
        assert observed["json_mode"] is False
        content = observed["messages"][0]["content"]
        assert content[1] == {"type": "input_image", "image_url": "data:image/jpeg;base64,AQID"}

    asyncio.run(run())


def test_raw_realtime_uplink_does_not_keep_idle_session_alive():
    async def run() -> None:
        bridge = _bridge()
        bridge._last_activity = 123.0
        task = await _start_uplink_clock(bridge)
        try:
            assert await bridge.send_audio(b"\x00\x01" * 320) is True
            assert await bridge.commit() is True
            assert bridge._last_activity == 123.0
        finally:
            await _stop_uplink_clock(task)

    asyncio.run(run())


def test_aec_never_hard_mutes_near_speech_when_reference_queue_runs_dry():
    async def run() -> None:
        bridge = _bridge()
        client = bridge.client
        bridge._setup_echo_canceller()
        task = await _start_uplink_clock(bridge)
        await bridge._start_response("response-1")
        assert bridge.input_suppressed is False
        bridge._extend_playback_guard(1000)
        assert bridge.input_suppressed is True
        try:
            near_speech = (b"\x10\x27" + b"\xf0\xd8") * 160
            assert await bridge.send_audio(near_speech) is True
            await asyncio.sleep(0.05)
            assert client.audio
            assert all(len(frame) == 640 for frame in client.audio)
            # The reverse-reference queue can end before buffered device audio
            # is actually audible.  AEC with a zero reverse frame must retain
            # near speech instead of applying the legacy playback-tail mute.
            assert any(bridge._pcm_levels(frame) > (2, 2) for frame in client.audio)
            assert await bridge.commit() is True
            assert client.commits == 1
        finally:
            await _stop_uplink_clock(task)

    asyncio.run(run())


def test_local_endpoint_vad_commits_when_provider_vad_does_not():
    class OneShotVad:
        def __init__(self) -> None:
            self.fired = False

        def feed_pcm(self, pcm: bytes):
            if self.fired or not pcm.strip(b"\x00"):
                return None
            self.fired = True
            return pcm

    async def run() -> None:
        bridge = _bridge(suppress=False)
        bridge._endpoint_vad_factory = OneShotVad
        bridge._reset_endpoint_vad()
        bridge._endpoint_vad_enabled = True
        client = bridge.client
        task = await _start_uplink_clock(bridge)
        endpoint_task = asyncio.create_task(bridge._endpoint_vad_loop())
        bridge._endpoint_vad_task = endpoint_task
        try:
            assert await bridge.send_audio((b"\x10\x27" + b"\xf0\xd8") * 160)
            await asyncio.sleep(0.08)
            assert client.commits == 1
            assert bridge._input_pcm_bytes == 0
        finally:
            endpoint_task.cancel()
            await asyncio.gather(endpoint_task, return_exceptions=True)
            await _stop_uplink_clock(task)

    asyncio.run(run())


def test_pb_ack_extends_echo_guard_past_output_done():
    async def run() -> None:
        bridge = _bridge()
        await bridge._start_response("response-1")
        bridge.record_pb_ack({"req": bridge._response_pb_req, "audio_buf_ms": 900})
        bridge._output_streaming = False
        bridge._audio_buffer.clear()
        bridge._drain_play_queue()
        assert bridge.input_suppressed is True
        assert await bridge.send_audio(b"\x00\x01" * 320) is True

    asyncio.run(run())


def test_missing_output_done_does_not_permanently_mute_uplink():
    async def run() -> None:
        bridge = _bridge()
        await bridge._start_response("response-without-done")
        bridge._audio_buffer.clear()
        bridge._drain_play_queue()
        bridge._suppress_input_until = 0.0

        assert bridge._output_streaming is True
        assert bridge.input_suppressed is False
        assert await bridge.send_audio(b"\x00\x01" * 320) is True

        bridge._cancel_output_watchdog()

    asyncio.run(run())


def test_partial_audio_buffer_does_not_permanently_mute_uplink():
    async def run() -> None:
        bridge = _bridge()
        await bridge._start_response("response-with-partial-audio")
        bridge._audio_buffer.extend(b"\x00\x01" * 160)
        bridge._drain_play_queue()
        bridge._suppress_input_until = 0.0

        assert bridge._output_streaming is True
        assert bridge.input_suppressed is False
        assert await bridge.send_audio(b"\x00\x01" * 320) is True

        bridge._cancel_output_watchdog()

    asyncio.run(run())


def test_provider_gap_does_not_close_stream_before_output_done():
    async def run() -> None:
        bridge = _bridge()
        await bridge._start_response("response-with-long-provider-gap")
        pcm = b"\x00\x01" * 160

        await bridge._queue_audio_delta({"delta": base64.b64encode(pcm).decode("ascii")})
        assert bridge._play_queue.empty()

        await asyncio.sleep(0.25)
        assert bridge._play_queue.empty()
        assert bytes(bridge._audio_buffer) == pcm

        await bridge._finish_output("done")
        assert bridge._play_queue.get_nowait() == (bridge._response_generation, pcm, True)
        bridge._play_queue.task_done()

        bridge._cancel_output_watchdog()

    asyncio.run(run())


def test_realtime_error_releases_playback_guard_and_schedules_recovery():
    async def run() -> None:
        bridge = _bridge()
        await bridge._start_response("response-1")
        bridge._audio_buffer.extend(b"\x00\x01" * 160)
        bridge._device_audio_buf_ms = 960

        await bridge._fail_and_recover("AudioTTSIdleTimeoutError")

        assert bridge._output_streaming is False
        assert bridge._audio_buffer == b""
        assert bridge._device_audio_buf_ms == 0
        assert bridge._response_pb_req == ""
        assert bridge._failed is True
        assert bridge._recovery_task is not None
        bridge._recovery_task.cancel()
        await asyncio.gather(bridge._recovery_task, return_exceptions=True)

    asyncio.run(run())


def test_output_watchdog_recovers_when_done_event_never_arrives():
    async def run() -> None:
        bridge = _bridge()
        bridge.settings = RealtimeSettings(
            enabled=True,
            api_key="test-key",
            playback_tail_ms=0,
            output_stall_timeout_sec=1.0,
            reconnect_delay_sec=5.0,
        )
        client = bridge.client
        await bridge._start_response("response-stalled")
        bridge._last_output_audio_at -= 2.0

        await asyncio.wait_for(bridge._output_watchdog_task, timeout=0.5)

        assert client.cancels == 1
        assert bridge._output_streaming is False
        assert bridge._failed is True
        assert bridge._recovery_task is not None
        bridge._recovery_task.cancel()
        await asyncio.gather(bridge._recovery_task, return_exceptions=True)

    asyncio.run(run())


def test_realtime_output_watchdog_recovers_from_stalled_tts_promptly():
    assert RealtimeSettings().output_stall_timeout_sec == 8.0


def test_realtime_downlink_defaults_are_streaming_sized():
    settings = RealtimeSettings()
    assert settings.downlink_chunk_ms == 120
    assert settings.downlink_pacing_ms == 105
    assert settings.downlink_start_buffer_ms == 600


def test_realtime_pcm_meter_reports_rms_and_peak():
    pcm = b"\x10\x27" * 240 + b"\xf0\xd8" * 240  # alternating +10000/-10000
    assert DoubaoRealtimeBridge._pcm_levels(pcm) == (10000, 10000)


def test_realtime_audio_uses_one_continuous_pb_stream():
    class FakeHub:
        def __init__(self) -> None:
            self.frames: list[dict] = []
            self.gaps: list[float] = []

        async def send_pb_chain_ordered(self, _device_id, frames, **kwargs) -> int:
            self.frames.extend(frames)
            self.gaps.append(kwargs["chunk_gap_sec"])
            return len(frames)

    async def run() -> None:
        bridge = _bridge()
        hub = FakeHub()
        bridge.hub = hub
        bridge.pipeline.tts_cfg = {"output_codec": "s16le"}
        await bridge._start_response("response-stream")
        pcm = b"\x01\x00" * 2880

        await bridge._send_pcm_to_device(pcm)
        await bridge._send_pcm_to_device(pcm)
        await bridge._send_pcm_to_device(b"", end_burst=True)

        assert [frame["type"] for frame in hub.frames] == ["pb_start", "pb_chunk", "pb_end"]
        assert [frame["idx"] for frame in hub.frames] == [0, 1, 2]
        assert {frame["fmt"] for frame in hub.frames} == {"opus"}
        assert all(frame["audio"]["frames"] > 0 for frame in hub.frames)
        assert hub.gaps == [0.0, 0.08, 0.08]
        bridge._cancel_output_watchdog()

    asyncio.run(run())


def test_realtime_playback_waits_for_startup_jitter_buffer():
    async def run() -> None:
        bridge = _bridge()
        bridge.settings = replace(bridge.settings, downlink_start_buffer_ms=240)
        sent: list[bytes] = []

        async def fake_send(pcm: bytes, *, end_burst: bool = False) -> None:
            sent.append(pcm)

        bridge._send_pcm_to_device = fake_send
        task = asyncio.create_task(bridge._play_loop())
        pcm = b"\x01\x00" * 2880  # 120 ms

        await bridge._enqueue_play(pcm, end_burst=False)
        await asyncio.sleep(0.01)
        assert bridge._prebuffering is True
        assert sent == []

        await bridge._enqueue_play(pcm, end_burst=False)
        await asyncio.sleep(0.05)
        assert sent == [pcm, pcm]

        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    asyncio.run(run())


def test_realtime_startup_prebuffer_alone_does_not_mute_uplink():
    async def run() -> None:
        bridge = _bridge()
        bridge._prebuffering = True
        bridge._suppress_input_until = 0.0

        assert bridge.input_suppressed is False
        assert await bridge.send_audio(b"\x00\x01" * 320) is True

    asyncio.run(run())


def test_full_duplex_uplink_paces_20ms_frames_and_orders_commit():
    async def run() -> None:
        bridge = _bridge(suppress=False)
        client = bridge.client
        task = await _start_uplink_clock(bridge)
        try:
            pcm = b"\x01\x00" * 640  # two 20 ms frames
            assert await bridge.send_audio(pcm) is True
            assert await bridge.commit() is True
            assert client.commits == 1
            assert client.audio[:2] == [pcm[:640], pcm[640:]]
            await asyncio.sleep(0.045)
            assert client.audio[-1] == bytes(640)
        finally:
            await _stop_uplink_clock(task)

    asyncio.run(run())


def test_aec_reference_is_consumed_on_silent_media_clock_ticks():
    async def run() -> None:
        bridge = _bridge()
        bridge._setup_echo_canceller()
        assert bridge._echo_canceller is not None
        bridge._echo_canceller.queue_speaker_pcm24k(b"\x01\x00" * 960)
        initial = bridge._echo_canceller.reference_frames
        assert initial > 0
        task = await _start_uplink_clock(bridge)
        try:
            await asyncio.sleep(0.05)
            assert bridge._echo_canceller.reference_frames < initial
        finally:
            await _stop_uplink_clock(task)

    asyncio.run(run())


def test_realtime_partial_startup_buffer_releases_after_provider_gap():
    async def run() -> None:
        bridge = _bridge()
        bridge.settings = replace(
            bridge.settings,
            downlink_start_buffer_ms=5000,
            output_stall_timeout_sec=1.0,
        )
        sent: list[bytes] = []

        async def fake_send(pcm: bytes, *, end_burst: bool = False) -> None:
            sent.append(pcm)

        bridge._send_pcm_to_device = fake_send
        task = asyncio.create_task(bridge._play_loop())
        pcm = b"\x01\x00" * 2880

        await bridge._enqueue_play(pcm, end_burst=False)
        await asyncio.sleep(0.35)
        assert sent == [pcm]
        assert bridge._prebuffering is False

        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    asyncio.run(run())


def test_new_response_cannot_replay_audio_buffered_by_previous_response():
    async def run() -> None:
        bridge = _bridge()
        bridge.settings = replace(bridge.settings, downlink_start_buffer_ms=240)
        sent: list[bytes] = []

        async def fake_send(pcm: bytes, *, end_burst: bool = False) -> None:
            sent.append(pcm)

        bridge._send_pcm_to_device = fake_send
        task = asyncio.create_task(bridge._play_loop())
        stale = b"\x01\x00" * 2880
        fresh = b"\x02\x00" * 2880

        await bridge._start_response("response-1")
        await bridge._enqueue_play(stale, end_burst=False)
        await asyncio.sleep(0.01)
        assert bridge._prebuffering is True

        await bridge._start_response("response-2")
        await bridge._enqueue_play(fresh, end_burst=False)
        await bridge._enqueue_play(fresh, end_burst=False)
        await asyncio.sleep(0.08)

        assert sent == [fresh, fresh]
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        bridge._cancel_output_watchdog()

    asyncio.run(run())
