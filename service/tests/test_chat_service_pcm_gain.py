from deskbot_server.utils.pcm import apply_pcm16_gain


def test_apply_pcm16_gain_amplifies_and_clips() -> None:
    pcm = (
        int(1000).to_bytes(2, "little", signed=True)
        + int(-1000).to_bytes(2, "little", signed=True)
        + int(20000).to_bytes(2, "little", signed=True)
    )

    boosted = apply_pcm16_gain(pcm, 2.0)
    samples = [int.from_bytes(boosted[i : i + 2], "little", signed=True) for i in range(0, len(boosted), 2)]

    assert samples == [2000, -2000, 32767]


def test_apply_pcm16_gain_ignores_invalid_gain() -> None:
    pcm = b"\xe8\x03"

    assert apply_pcm16_gain(pcm, 1.0) == pcm
    assert apply_pcm16_gain(pcm, "invalid") == pcm
