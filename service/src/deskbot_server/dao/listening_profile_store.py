"""Per-device microphone listening profile persistence."""
from __future__ import annotations
import json
import os
from typing import Any, Optional
from deskbot_server.constants import LISTENING_PROFILE_FILE
from deskbot_server.utils.device_data import resolve_json_path

PROFILES: dict[str, dict[str, Any]] = {
    "quiet": {"mic_gain": 4, "label": "安静环境"},
    "normal": {"mic_gain": 5, "label": "正常"},
    "sensitive": {"mic_gain": 7, "label": "灵敏"},
    "noisy": {"mic_gain": 3, "label": "嘈杂环境"},
}
_ALIASES = {"default":"normal","默认":"normal","正常":"normal","标准":"normal","灵敏":"sensitive","敏感":"sensitive","远场":"sensitive","安静":"quiet","近场":"quiet","嘈杂":"noisy","降噪":"noisy","人多":"noisy"}

def normalize_listening_profile(value: object) -> str:
    key = _ALIASES.get(str(value or "normal").strip().lower(), str(value or "normal").strip().lower())
    if key not in PROFILES:
        raise ValueError(f"unknown listening profile: {value!r}")
    return key

def get_listening_profile(*, device_id: Optional[str] = None) -> str:
    try:
        with open(resolve_json_path(LISTENING_PROFILE_FILE, device_id), encoding="utf-8") as f:
            raw = json.load(f)
        return normalize_listening_profile(raw.get("profile") if isinstance(raw, dict) else raw)
    except (OSError, ValueError, json.JSONDecodeError):
        return "normal"

def persist_listening_profile(profile: object, *, device_id: Optional[str] = None) -> str:
    norm = normalize_listening_profile(profile)
    path = resolve_json_path(LISTENING_PROFILE_FILE, device_id)
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"profile": norm, **PROFILES[norm]}, f, ensure_ascii=False, indent=2)
        f.write("\n")
    return norm

def listening_profile_config(profile: object) -> dict[str, Any]:
    norm = normalize_listening_profile(profile)
    return {"profile": norm, **PROFILES[norm]}
