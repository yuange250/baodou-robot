"""Conservative low-latency parser for explicit robot control utterances."""
from __future__ import annotations
import re
from typing import Any, Optional
from deskbot_server.service.application.robot_control import move_head, set_listening, set_volume

_NUM = re.compile(r"(?:音量|声音)(?:调到|设为|到)?\s*(\d{1,3})")

async def try_fast_voice_control(text: str, *, device_id: str, hub: Any) -> Optional[dict[str, Any]]:
    s = re.sub(r"[\s，。！？,.!?]", "", str(text or "").lower())
    if not s:
        return None
    m = _NUM.search(s)
    if m:
        out = await set_volume({"volume": int(m.group(1))}, device_id=device_id, hub=hub)
        return {"ack": f"音量已调到{out['volume']}", "result": out}
    if any(k in s for k in ("音量大一点","声音大一点","大声一点")):
        out = await set_volume({"delta": 10}, device_id=device_id, hub=hub)
        return {"ack": f"音量{out['volume']}", "result": out}
    if any(k in s for k in ("音量小一点","声音小一点","小声一点")):
        out = await set_volume({"delta": -10}, device_id=device_id, hub=hub)
        return {"ack": f"音量{out['volume']}", "result": out}
    if "静音" in s:
        out = await set_volume({"volume": 0}, device_id=device_id, hub=hub)
        return {"ack": "已静音", "result": out}
    if any(k in s for k in ("收音","麦克风","听力","灵敏度")):
        profile = None
        for words, value in ((('灵敏','敏感','远一点'),'sensitive'),(('嘈杂','降噪','人多'),'noisy'),(('安静','近一点'),'quiet'),(('正常','默认','标准'),'normal')):
            if any(w in s for w in words): profile = value; break
        if profile:
            out = await set_listening({"profile": profile}, device_id=device_id, hub=hub)
            return {"ack": f"收音已设为{out['label']}", "result": out}
    directions = ((('向左看','左转头','看左边'),'left'),(('向右看','右转头','看右边'),'right'),(('抬头','向上看'),'up'),(('低头','向下看'),'down'),(('回正','头回中','看前面'),'center'))
    for words, direction in directions:
        if any(w in s for w in words):
            out = await move_head({"direction": direction}, device_id=device_id, hub=hub)
            return {"ack": "好", "result": out}
    return None
