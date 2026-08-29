from __future__ import annotations

import re
import time
from dataclasses import dataclass
from typing import Iterable

_SEPARATORS = r"\s，。！？、,.!?：:；;~～—\-"
_COMMAND_CUES = frozenset("讲说看抬低转回调设收播放打开关闭查问是谁怎么为什么几多哪里什么")


@dataclass(frozen=True)
class WakeWordDecision:
    accepted: bool
    command: str = ""
    wake_only: bool = False
    matched_alias: str = ""
    reason: str = ""


class WakeWordGate:
    """Device-scoped wake gate with a sliding conversation window."""

    def __init__(self) -> None:
        self._awake_until: dict[str, float] = {}

    def touch(
        self,
        device_id: str,
        follow_up_window_sec: float,
        *,
        now: float | None = None,
    ) -> None:
        """Open or renew the post-reply conversation window for one device."""
        key = str(device_id or "__default__")
        current = time.monotonic() if now is None else float(now)
        self._awake_until[key] = current + max(1.0, float(follow_up_window_sec))

    @staticmethod
    def _aliases(word: str, aliases: Iterable[str]) -> tuple[str, ...]:
        values: list[str] = []
        for item in (word, *aliases):
            value = str(item or "").strip()
            if value and value not in values:
                values.append(value)
        return tuple(values)

    @staticmethod
    def _alias_pattern(alias: str) -> re.Pattern[str]:
        flexible = f"[{_SEPARATORS}]*"
        spelling = flexible.join(re.escape(char) for char in alias)
        return re.compile(rf"{spelling}[{_SEPARATORS}]*")

    def evaluate(
        self,
        device_id: str,
        text: str,
        *,
        word: str,
        aliases: Iterable[str] = (),
        isolated_aliases: Iterable[str] = (),
        follow_up_window_sec: float = 8.0,
        prefix_scan_chars: int = 10,
        acoustic_wake: str = "",
        now: float | None = None,
    ) -> WakeWordDecision:
        key = str(device_id or "__default__")
        spoken = str(text or "").strip()
        current = time.monotonic() if now is None else float(now)

        awake_until = self._awake_until.get(key, 0.0)
        if awake_until > current:
            self.touch(key, follow_up_window_sec, now=current)
            return WakeWordDecision(
                accepted=True,
                command=spoken,
                reason="follow_up",
            )
        self._awake_until.pop(key, None)

        for alias in self._aliases(word, aliases):
            match = self._alias_pattern(alias).search(spoken)
            if match is None:
                continue
            # 背景声可能先被 ASR 写进同一句，只在句首附近查找，避免正文中
            # 偶然出现机器人名字时误唤醒。标点和空白不占扫描额度。
            leading = re.sub(f"[{_SEPARATORS}]", "", spoken[: match.start()])
            if len(leading) > max(0, int(prefix_scan_chars)):
                continue
            command = spoken[match.end() :].strip()
            if command:
                self.touch(key, follow_up_window_sec, now=current)
                return WakeWordDecision(
                    accepted=True,
                    command=command,
                    matched_alias=alias,
                    reason="wake_and_command",
                )
            self.touch(key, follow_up_window_sec, now=current)
            return WakeWordDecision(
                accepted=True,
                wake_only=True,
                matched_alias=alias,
                reason="wake_only",
            )

        # “报道/报到”等在连续语句中很常见，只允许它们作为独立短句唤醒。
        # 这样能覆盖 SenseVoice 对“包逗”的高频误识别，同时不把“据新闻报道”
        # 或“请先报到”等正文当成机器人指令。
        compact_spoken = re.sub(f"[{_SEPARATORS}]", "", spoken)
        for alias in self._aliases("", isolated_aliases):
            compact_alias = re.sub(f"[{_SEPARATORS}]", "", alias)
            if compact_alias and compact_spoken == compact_alias:
                self.touch(key, follow_up_window_sec, now=current)
                return WakeWordDecision(
                    accepted=True,
                    wake_only=True,
                    matched_alias=alias,
                    reason="wake_only_fuzzy",
                )

        # Dedicated KWS has already matched the audio even when general ASR
        # mistranscribes or drops the uncommon two-syllable robot name.
        if str(acoustic_wake or "").strip():
            compact = re.sub(f"[{_SEPARATORS}]", "", spoken)
            looks_like_short_command = any(char in _COMMAND_CUES for char in compact)
            # KWS and general ASR inspect the same room audio but do not agree
            # on word boundaries.  A long unrelated TV/radio transcript must
            # not be forwarded as the command just because KWS heard the name.
            if len(compact) <= 3 or len(compact) > 20 or (len(compact) <= 5 and not looks_like_short_command):
                self.touch(key, follow_up_window_sec, now=current)
                return WakeWordDecision(
                    accepted=True,
                    wake_only=True,
                    matched_alias=str(acoustic_wake).strip(),
                    reason="acoustic_wake_only",
                )
            self.touch(key, follow_up_window_sec, now=current)
            return WakeWordDecision(
                accepted=True,
                command=spoken,
                matched_alias=str(acoustic_wake).strip(),
                reason="acoustic_wake_and_command",
            )

        return WakeWordDecision(accepted=False, reason="wake_word_missing")

    def clear(self, device_id: str | None = None) -> None:
        if device_id is None:
            self._awake_until.clear()
            return
        self._awake_until.pop(str(device_id or "__default__"), None)
