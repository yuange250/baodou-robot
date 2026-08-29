"""Validated runtime controls shared by LLM tools and fast voice intents."""
from __future__ import annotations
import uuid
from typing import Any
from deskbot_server.dao.device_volume_store import get_device_volume, persist_device_volume
from deskbot_server.dao.face_design_store import _load_face_design_cached, resolve_face_expression
from deskbot_server.dao.face_expr_scenes_store import design_frames_to_pb_chain
from deskbot_server.dao.listening_profile_store import listening_profile_config, persist_listening_profile
from deskbot_server.dao.servo_config_store import servo_limits

_EXPR = {"开心":"happy","高兴":"happy","happy":"happy","害羞":"shy","shy":"shy","生气":"angry","angry":"angry","惊讶":"surprised","surprised":"surprised","伤心":"sad","难过":"sad","sad":"sad","睡觉":"sleep","困":"sleep","sleep":"sleep","思考":"thinking","thinking":"thinking","倾听":"listening","听着":"listening","listening":"listening","默认":"idle","空闲":"idle","正常":"idle","idle":"idle"}

def _control_frame(**extra: Any) -> dict[str, Any]:
    return {"type":"pb_single","req":uuid.uuid4().hex[:16],"idx":0,"chunk_ms":80,"pb_ver":2,"action":"replace","level":3,"servo":[{"xm":2,"ym":2,"x":0,"y":0,"ms":80}],**extra}

async def set_volume(raw: dict[str, Any], *, device_id: str, hub: Any) -> dict[str, Any]:
    current = get_device_volume(device_id)
    value = current + int(raw["delta"]) if raw.get("delta") is not None else int(raw.get("volume", raw.get("value", current)))
    value = max(0, min(100, value))
    if raw.get("persist", True): persist_device_volume(value, device_id=device_id)
    delivered = await hub.send(device_id, _control_frame(volume=value))
    return {"ok":delivered > 0,"volume":value,"delivered":delivered}

async def set_listening(raw: dict[str, Any], *, device_id: str, hub: Any) -> dict[str, Any]:
    cfg = listening_profile_config(raw.get("profile") or raw.get("value") or "normal")
    if raw.get("persist", True): persist_listening_profile(cfg["profile"], device_id=device_id)
    delivered = await hub.send(device_id, _control_frame(mic_gain=cfg["mic_gain"]))
    return {"ok":delivered > 0,**cfg,"delivered":delivered}

async def move_head(raw: dict[str, Any], *, device_id: str, hub: Any) -> dict[str, Any]:
    lim = servo_limits(device_id=device_id)
    direction = str(raw.get("direction") or raw.get("preset") or "").strip().lower()
    presets = {"left":(0,2,lim["xMin"],0),"左":(0,2,lim["xMin"],0),"right":(0,2,lim["xMax"],0),"右":(0,2,lim["xMax"],0),"up":(2,0,0,lim["yMin"]),"上":(2,0,0,lim["yMin"]),"down":(2,0,0,lim["yMax"]),"下":(2,0,0,lim["yMax"]),"center":(0,0,40,50),"回正":(0,0,40,50)}
    relative = bool(raw.get("relative", False))
    if direction in presets:
        xm,ym,x,y = presets[direction]
    else:
        xm = 1 if relative and raw.get("x") is not None else (0 if raw.get("x") is not None else 2)
        ym = 1 if relative and raw.get("y") is not None else (0 if raw.get("y") is not None else 2)
        x,y = int(raw.get("x") or 0),int(raw.get("y") or 0)
        if xm == 0: x = max(lim["xMin"], min(lim["xMax"], x))
        if ym == 0: y = max(lim["yMin"], min(lim["yMax"], y))
        if xm == 1: x = max(-90, min(90, x))
        if ym == 1: y = max(-90, min(90, y))
    ms = max(80, min(3000, int(raw.get("ms") or 450)))
    frame = _control_frame(); frame["chunk_ms"] = ms; frame["servo"] = [{"xm":xm,"ym":ym,"x":x,"y":y,"ms":ms}]
    delivered = await hub.send(device_id, frame)
    return {"ok":delivered > 0,"servo":frame["servo"],"delivered":delivered}

async def set_expression(raw: dict[str, Any], *, device_id: str, hub: Any) -> dict[str, Any]:
    requested = str(raw.get("expression") or raw.get("name") or raw.get("value") or "idle").strip().lower()
    name = _EXPR.get(requested, requested)
    doc = _load_face_design_cached(device_id=device_id)
    ent = resolve_face_expression(doc, kind="emotion", name=name) if isinstance(doc, dict) else None
    if ent is None: raise ValueError(f"unknown expression: {requested!r}")
    pairs = design_frames_to_pb_chain(ent.get("frames") or [], runtime_req=uuid.uuid4().hex[:16])
    if not pairs: raise ValueError(f"empty expression: {name}")
    delivered = await hub.send_pb_chain_ordered(device_id,[p[0] for p in pairs],binaries_per_frame=[list(p[1]) for p in pairs])
    return {"ok":delivered > 0,"expression":name,"delivered":delivered}
