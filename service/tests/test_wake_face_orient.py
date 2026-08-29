from __future__ import annotations

import asyncio


def _face(*, x: int, y: int = 120) -> dict:
    return {
        "landmarks": [{"name": "nose", "x": x, "y": y}],
        "image_w": 320,
        "image_h": 240,
    }


def test_only_new_wake_reasons_trigger_orientation():
    from deskbot_server.service.application.wake_face_orient import should_orient_for_wake_reason

    assert should_orient_for_wake_reason("wake_only") is True
    assert should_orient_for_wake_reason("wake_and_command") is True
    assert should_orient_for_wake_reason("acoustic_wake_and_command") is True
    assert should_orient_for_wake_reason("follow_up") is False
    assert should_orient_for_wake_reason("") is False


def test_wake_face_step_is_relative_and_holds_centered_axis():
    from deskbot_server.service.application.wake_face_orient import build_wake_face_servo_step

    step = build_wake_face_servo_step(_face(x=240, y=120), device_id="__wake_face_test__")

    assert step is not None
    assert step["xm"] == 1
    # 画面右侧的人脸应产生正向 X 修正；硬件层已完成镜像校准。
    assert step["x"] > 0
    assert step["ym"] == 2
    assert step["y"] == 0


def test_wake_face_step_uses_dead_zone_and_axis_caps():
    from deskbot_server.service.application import wake_face_orient as orient

    assert orient.build_wake_face_servo_step(_face(x=160, y=120), device_id="__wake_face_test__") is None

    step = orient.build_wake_face_servo_step(_face(x=320, y=240), device_id="__wake_face_test__")
    assert step is not None
    assert abs(step["x"]) <= orient._MAX_REL_X_DEG
    assert abs(step["y"]) <= orient._MAX_REL_Y_DEG


def test_wake_face_uses_body_target_and_current_head_position():
    from deskbot_server.service.application import wake_face_orient as orient

    # 相机固定在身体上：人脸约在身体右侧 41°。头若正停在 -15°，不能只
    # 相对加 41°（只会到 26°），而应朝绝对目标 40+41≈81° 修正。
    step = orient.build_wake_face_servo_step(
        _face(x=240, y=120),
        device_id="__wake_face_test__",
        current_servo={"x": -15},
    )

    assert step is not None
    assert step["xm"] == 1
    assert step["x"] > orient._MAX_REL_X_DEG


def test_wake_orientation_sends_once_for_recent_face(monkeypatch):
    from deskbot_server.service.application import wake_face_orient as orient
    from deskbot_server.service.application.face_snapshot_cache import update_device_faces

    class Hub:
        pipeline_broker = None

        def __init__(self) -> None:
            self.payloads: list[dict] = []

        async def send(self, _device_id: str, payload: dict) -> int:
            self.payloads.append(payload)
            return 1

    async def _run() -> None:
        dev = "dev-wake-face"
        orient._last_orient_mono.clear()
        face = _face(x=240, y=150)
        face["face_id"] = 1
        update_device_faces(dev, [face])
        # 下一帧短暂漏检时仍应使用最近一次正检，避免 1 → 0 → 1 抖动漏转。
        update_device_faces(dev, [])
        monkeypatch.setattr(orient, "publish_auto_dispatch_event", None, raising=False)
        monkeypatch.setattr(orient, "_START_SETTLE_SEC", 0.0)
        hub = Hub()

        delivered = await orient.orient_to_recent_face_on_wake(
            hub,
            dev,
            wake_reason="wake_only",
            asr_request_id="asr1",
        )

        assert delivered == 1
        assert len(hub.payloads) == 1
        payload = hub.payloads[0]
        assert payload["action"] == "replace"
        assert payload["level"] == 3
        assert payload["servo"][0]["xm"] == 1
        assert payload["servo"][0]["ym"] == 1

        # 连续对话 follow-up 不得再次抢舵机。
        again = await orient.orient_to_recent_face_on_wake(hub, dev, wake_reason="follow_up")
        assert again == 0
        assert len(hub.payloads) == 1

    asyncio.run(_run())


def test_wake_orientation_skips_without_recent_face():
    from deskbot_server.service.application import wake_face_orient as orient

    class Hub:
        pipeline_broker = None

        async def send(self, _device_id: str, _payload: dict) -> int:
            raise AssertionError("no face must not send servo")

    async def _run() -> None:
        dev = "dev-no-face"
        orient._last_orient_mono.clear()
        delivered = await orient.orient_to_recent_face_on_wake(Hub(), dev, wake_reason="wake_only")
        assert delivered == 0

    asyncio.run(_run())


def test_recent_positive_face_expires(monkeypatch):
    from deskbot_server.service.application import face_snapshot_cache as cache

    now = [100.0]
    monkeypatch.setattr(cache.time, "monotonic", lambda: now[0])
    face = _face(x=200)
    face["face_id"] = 7
    cache.update_device_faces("dev-stale-face", [face])
    cache.update_device_faces("dev-stale-face", [])

    assert cache.list_recent_positive_faces("dev-stale-face", max_age_sec=1.4)
    now[0] = 101.5
    assert cache.list_recent_positive_faces("dev-stale-face", max_age_sec=1.4) == {}


def test_servo_position_survives_mic_only_ack():
    from deskbot_server.ws.registry import DeviceRegistry

    async def _run() -> None:
        registry = DeviceRegistry()
        registry._devices["dev-servo-cache"] = {}  # type: ignore[attr-defined]
        await registry.record_pb_ack(
            "dev-servo-cache",
            {"type": "pb_ack", "req": "move", "servo": {"x": -15, "y": 30}},
        )
        await registry.record_pb_ack("dev-servo-cache", {"type": "pb_ack", "req": "mic-open"})

        assert await registry.latest_servo_position("dev-servo-cache") == {"x": -15, "y": 30}

    asyncio.run(_run())
