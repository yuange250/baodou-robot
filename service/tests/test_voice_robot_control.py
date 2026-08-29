from __future__ import annotations

import asyncio

from deskbot_server.service.application.robot_control import move_head, set_listening, set_volume
from deskbot_server.service.application.voice_control_intent import try_fast_voice_control


class FakeHub:
    def __init__(self):
        self.sent = []

    async def send(self, device_id, payload):
        self.sent.append((device_id, payload))
        return 1


def test_runtime_controls_emit_safe_pb():
    hub = FakeHub()
    vol = asyncio.run(set_volume({"volume": 120, "persist": False}, device_id="test", hub=hub))
    assert vol["volume"] == 100
    assert hub.sent[-1][1]["volume"] == 100

    listening = asyncio.run(
        set_listening({"profile": "sensitive", "persist": False}, device_id="test", hub=hub)
    )
    assert listening["mic_gain"] == 7
    assert hub.sent[-1][1]["mic_gain"] == 7

    head = asyncio.run(move_head({"x": 999, "y": -999}, device_id="test", hub=hub))
    step = head["servo"][0]
    assert step["x"] == 100
    assert step["y"] == -10


def test_fast_voice_intents_are_conservative():
    hub = FakeHub()
    out = asyncio.run(try_fast_voice_control("音量调到70", device_id="test", hub=hub))
    assert out and out["result"]["volume"] == 70
    out = asyncio.run(try_fast_voice_control("收音调灵敏一点", device_id="test", hub=hub))
    assert out and out["result"]["mic_gain"] == 7
    assert asyncio.run(try_fast_voice_control("今天天气怎么样", device_id="test", hub=hub)) is None
