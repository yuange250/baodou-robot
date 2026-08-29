import asyncio

from deskbot_server.core.settings import AppSettings
from deskbot_server.service.application.wake_word import WakeWordGate


def test_default_wake_word_is_baodou() -> None:
    settings = AppSettings.from_config({})

    assert settings.wake_word.word == "包逗"


def test_wake_and_command_strips_prefix() -> None:
    gate = WakeWordGate()

    decision = gate.evaluate(
        "robot-1",
        "阿杜，今天天气怎么样",
        word="阿杜",
        aliases=("啊杜",),
        now=100.0,
    )

    assert decision.accepted is True
    assert decision.wake_only is False
    assert decision.command == "今天天气怎么样"
    assert decision.reason == "wake_and_command"


def test_alias_allows_punctuation_between_syllables() -> None:
    gate = WakeWordGate()

    decision = gate.evaluate(
        "robot-1",
        "啊，杜！抬头",
        word="阿杜",
        aliases=("啊杜",),
        now=100.0,
    )

    assert decision.accepted is True
    assert decision.command == "抬头"
    assert decision.matched_alias == "啊杜"


def test_wake_word_can_follow_short_background_prefix() -> None:
    gate = WakeWordGate()

    decision = gate.evaluate(
        "robot-1",
        "电视里还在说新闻，包豆，抬头",
        word="包逗",
        aliases=("包豆",),
        prefix_scan_chars=10,
        now=100.0,
    )

    assert decision.accepted is True
    assert decision.command == "抬头"


def test_wake_word_is_not_matched_deep_in_background_transcript() -> None:
    gate = WakeWordGate()

    decision = gate.evaluate(
        "robot-1",
        "这是一段很长的电视新闻内容随后才提到包豆品牌",
        word="包逗",
        aliases=("包豆",),
        prefix_scan_chars=10,
        now=100.0,
    )

    assert decision.accepted is False


def test_common_homophone_can_wake_only_when_isolated() -> None:
    gate = WakeWordGate()

    decision = gate.evaluate(
        "robot-1",
        "报道。",
        word="包逗",
        isolated_aliases=("报道", "报到"),
        now=100.0,
    )

    assert decision.accepted is True
    assert decision.wake_only is True
    assert decision.matched_alias == "报道"
    assert decision.reason == "wake_only_fuzzy"


def test_common_homophone_inside_news_does_not_wake() -> None:
    gate = WakeWordGate()

    decision = gate.evaluate(
        "robot-1",
        "根据新闻报道苹果正在调整供应链",
        word="包逗",
        isolated_aliases=("报道", "报到"),
        now=100.0,
    )

    assert decision.accepted is False


def test_rare_homophone_can_carry_a_command() -> None:
    gate = WakeWordGate()

    decision = gate.evaluate(
        "robot-1",
        "抱逗，抬头",
        word="包逗",
        aliases=("抱逗",),
        now=100.0,
    )

    assert decision.accepted is True
    assert decision.command == "抬头"


def test_wake_only_opens_sliding_follow_up_window() -> None:
    gate = WakeWordGate()

    wake = gate.evaluate("robot-1", "阿杜", word="阿杜", follow_up_window_sec=8, now=100.0)
    follow_up = gate.evaluate("robot-1", "给我讲个笑话", word="阿杜", follow_up_window_sec=8, now=105.0)
    third = gate.evaluate("robot-1", "再讲一个", word="阿杜", follow_up_window_sec=8, now=106.0)

    assert wake.wake_only is True
    assert follow_up.accepted is True
    assert follow_up.command == "给我讲个笑话"
    assert follow_up.reason == "follow_up"
    assert third.accepted is True
    assert third.command == "再讲一个"
    assert third.reason == "follow_up"


def test_wake_and_command_also_opens_conversation_window() -> None:
    gate = WakeWordGate()

    first = gate.evaluate(
        "robot-1",
        "阿杜，讲个笑话",
        word="阿杜",
        follow_up_window_sec=8,
        now=100.0,
    )
    second = gate.evaluate(
        "robot-1",
        "再讲一个",
        word="阿杜",
        follow_up_window_sec=8,
        now=107.0,
    )

    assert first.command == "讲个笑话"
    assert second.accepted is True
    assert second.reason == "follow_up"


def test_reply_completion_can_renew_conversation_window() -> None:
    gate = WakeWordGate()

    gate.evaluate(
        "robot-1",
        "阿杜，讲个故事",
        word="阿杜",
        follow_up_window_sec=8,
        now=100.0,
    )
    gate.touch("robot-1", 8, now=120.0)
    follow_up = gate.evaluate(
        "robot-1",
        "后来呢",
        word="阿杜",
        follow_up_window_sec=8,
        now=127.0,
    )

    assert follow_up.accepted is True
    assert follow_up.reason == "follow_up"


def test_follow_up_window_expires() -> None:
    gate = WakeWordGate()

    gate.evaluate("robot-1", "阿杜", word="阿杜", follow_up_window_sec=8, now=100.0)
    decision = gate.evaluate("robot-1", "你还在吗", word="阿杜", follow_up_window_sec=8, now=109.0)

    assert decision.accepted is False
    assert decision.reason == "wake_word_missing"


def test_acoustic_wake_recovers_mistranscribed_name() -> None:
    gate = WakeWordGate()

    decision = gate.evaluate(
        "robot-1",
        "讲个笑话",
        word="包逗",
        acoustic_wake="包逗",
        now=100.0,
    )

    assert decision.accepted is True
    assert decision.command == "讲个笑话"
    assert decision.reason == "acoustic_wake_and_command"


def test_acoustic_wake_does_not_forward_long_background_transcript() -> None:
    gate = WakeWordGate()

    decision = gate.evaluate(
        "robot-1",
        "拿破仑在战后的报告中描述到尽管我们在战役之初经历了激烈战斗",
        word="包逗",
        acoustic_wake="包逗",
        now=100.0,
    )

    assert decision.accepted is True
    assert decision.wake_only is True
    assert decision.command == ""
    assert decision.reason == "acoustic_wake_only"


def test_acoustic_wake_only_opens_follow_up() -> None:
    gate = WakeWordGate()

    wake = gate.evaluate(
        "robot-1",
        "报到",
        word="包逗",
        acoustic_wake="包逗",
        follow_up_window_sec=8,
        now=100.0,
    )
    follow_up = gate.evaluate("robot-1", "抬头", word="包逗", now=104.0)

    assert wake.accepted is True
    assert wake.wake_only is True
    assert follow_up.accepted is True
    assert follow_up.command == "抬头"


def test_wake_state_is_scoped_per_device() -> None:
    gate = WakeWordGate()

    gate.evaluate("robot-1", "阿杜", word="阿杜", follow_up_window_sec=8, now=100.0)

    assert gate.evaluate("robot-2", "你好", word="阿杜", now=101.0).accepted is False
    assert gate.evaluate("robot-1", "你好", word="阿杜", now=101.0).accepted is True


def test_settings_load_wake_word_config() -> None:
    settings = AppSettings.from_config(
        {
            "wake_word": {
                "enabled": True,
                "word": "阿杜",
                "aliases": ["啊杜", "阿度"],
                "follow_up_window_sec": 6,
                "ack_text": "在呢",
            }
        }
    )

    assert settings.wake_word.enabled is True
    assert settings.wake_word.word == "阿杜"
    assert settings.wake_word.aliases == ("啊杜", "阿度")
    assert settings.wake_word.follow_up_window_sec == 6
    assert settings.wake_word.ack_text == "在呢"


def test_rom_uplink_keeps_shared_turn_task_holder(monkeypatch) -> None:
    from deskbot_server.ws import asr_chat

    class StubSession:
        rom_sr = 16000
        rom_ch = 1
        rom_codec = "pcm16"

        async def feed_audio(self, *_args, **_kwargs):
            return b"\x00\x00", True, False

    async def run() -> None:
        holder: list = []
        observed: list = []

        async def fake_schedule(*_args, **kwargs):
            observed.append(kwargs["turn_task_holder"])

        monkeypatch.setattr(asr_chat, "_schedule_asr_turn", fake_schedule)
        await asr_chat._feed_rom_uplink(
            b"\x00\x00",
            "pcm16",
            session=StubSession(),
            asr_chat_hub=None,
            device_id="robot-1",
            websocket=object(),
            pipeline=object(),
            audio_cfg=object(),
            dp_broker=None,
            registry=None,
            turn_task_holder=holder,
        )

        assert observed == [holder]
        assert observed[0] is holder

    asyncio.run(run())
