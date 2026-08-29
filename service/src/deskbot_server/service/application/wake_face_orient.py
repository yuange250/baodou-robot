"""真正唤醒后，按最近一帧人脸位置做一次相对舵机修正。"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from typing import Any, Optional

from deskbot_server.dao.servo_config_store import clamp_servo_step, servo_limits
from deskbot_server.pb.servo_pcm import attach_pb_device_hints_from_config
from deskbot_server.pb.shapes import PB_ACTION_REPLACE, PB_LEVEL_DEBUG
from deskbot_server.service.application.camera_servo_follower import (
    _MAP_PITCH_SIGN,
    _SERVO_CENTER_X,
    _SERVO_CENTER_Y,
    _clamp,
    _screen_angles_from_analysis,
)
from deskbot_server.service.application.camera_frame import analyze_face_detections
from deskbot_server.service.application.face_snapshot_cache import list_recent_positive_faces

logger = logging.getLogger("deskbot-server")

# follow_up 是连续对话窗口，不应每句话都重新抢一次舵机控制权。
_NEW_WAKE_REASONS = frozenset(
    {
        "wake_only",
        "wake_only_fuzzy",
        "wake_and_command",
        "acoustic_wake_only",
        "acoustic_wake_and_command",
    }
)

_FACE_MAX_AGE_SEC = 1.4
_WAKE_ORIENT_COOLDOWN_SEC = 1.0
_YAW_DEAD_ZONE_DEG = 3.0
_PITCH_DEAD_ZONE_DEG = 3.0
# 当前硬件 X 轴已经在固件/舵机配置中完成镜像校准；唤醒注视不再沿用
# 旧持续跟脸模块的二次反向，否则画面右侧的人会驱动机器人向左看。
_WAKE_MAP_YAW_SIGN = 1
_MAX_REL_X_DEG = 50
_MAX_REL_Y_DEG = 24
_MIN_SERVO_MS = 220
_MAX_SERVO_MS = 500
_START_SETTLE_SEC = 0.55

_last_orient_mono: dict[str, float] = {}


def should_orient_for_wake_reason(reason: str) -> bool:
    """只有本轮真正命中唤醒词/KWS 时才触发；连续追问不触发。"""
    return str(reason or "").strip() in _NEW_WAKE_REASONS


def _relative_delta(angle: float, *, sign: int, dead_zone: float, max_abs: int) -> int:
    if abs(angle) <= dead_zone:
        return 0
    raw = int(round(float(sign) * angle))
    return max(-max_abs, min(max_abs, raw))


def _protocol_position_to_logical(value: int, *, axis: str, limits: dict[str, int]) -> int:
    reverse = int(limits.get(f"{axis}Reverse", 0)) == 1
    if not reverse:
        return int(value)
    return int(limits[f"{axis}Min"] + limits[f"{axis}Max"] - int(value))


def build_wake_face_servo_step(
    analysis: dict[str, Any],
    *,
    device_id: Optional[str] = None,
    current_servo: Optional[dict[str, int]] = None,
) -> Optional[dict[str, int]]:
    """把人脸在画面中的偏差转换为一次相对舵机动作。

    相对坐标非常重要：唤醒动作只消除当前视线偏差，不会把大模型或
    空闲动作留下的姿态强制拉回某个软件中位。
    """
    screen_yaw, screen_pitch = _screen_angles_from_analysis(analysis)
    if screen_yaw is None or screen_pitch is None:
        return None

    limits = servo_limits(device_id=device_id)
    current_servo = current_servo if isinstance(current_servo, dict) else {}

    if "x" in current_servo:
        current_x = _protocol_position_to_logical(int(current_servo["x"]), axis="x", limits=limits)
        target_x = int(
            round(
                _clamp(
                    _SERVO_CENTER_X + _WAKE_MAP_YAW_SIGN * screen_yaw,
                    limits["xMin"],
                    limits["xMax"],
                )
            )
        )
        raw_dx = target_x - current_x
        dx = 0 if abs(raw_dx) <= _YAW_DEAD_ZONE_DEG else raw_dx
    else:
        # 没有新鲜 pb_ack 时仍可安全做一次有限的相对修正。
        dx = _relative_delta(
            screen_yaw,
            sign=_WAKE_MAP_YAW_SIGN,
            dead_zone=_YAW_DEAD_ZONE_DEG,
            max_abs=_MAX_REL_X_DEG,
        )

    if "y" in current_servo:
        current_y = _protocol_position_to_logical(int(current_servo["y"]), axis="y", limits=limits)
        target_y = int(
            round(
                _clamp(
                    _SERVO_CENTER_Y + _MAP_PITCH_SIGN * screen_pitch,
                    limits["yMin"],
                    limits["yMax"],
                )
            )
        )
        raw_dy = target_y - current_y
        if abs(raw_dy) <= _PITCH_DEAD_ZONE_DEG:
            raw_dy = 0
        dy = max(-_MAX_REL_Y_DEG, min(_MAX_REL_Y_DEG, raw_dy))
    else:
        dy = _relative_delta(
            screen_pitch,
            sign=_MAP_PITCH_SIGN,
            dead_zone=_PITCH_DEAD_ZONE_DEG,
            max_abs=_MAX_REL_Y_DEG,
        )
    if dx == 0 and dy == 0:
        return None

    motion_ratio = min(1.0, max(abs(dx) / _MAX_REL_X_DEG, abs(dy) / _MAX_REL_Y_DEG))
    ms = int(round(_MIN_SERVO_MS + (_MAX_SERVO_MS - _MIN_SERVO_MS) * motion_ratio))
    return clamp_servo_step(
        {
            "xm": 1 if dx else 2,
            "ym": 1 if dy else 2,
            "x": dx,
            "y": dy,
            "ms": ms,
        },
        device_id=device_id,
        limits=limits,
    )


async def orient_to_recent_face_on_wake(
    hub: Any,
    device_id: Optional[str],
    *,
    wake_reason: str,
    asr_request_id: Optional[str] = None,
    current_servo: Optional[dict[str, int]] = None,
) -> int:
    """唤醒后看向最近的人脸一次，返回成功送达的连接数。"""
    dev = str(device_id or "").strip()
    if not dev or hub is None or not should_orient_for_wake_reason(wake_reason):
        return 0

    now = time.monotonic()
    last = _last_orient_mono.get(dev, 0.0)
    if now - last < _WAKE_ORIENT_COOLDOWN_SEC:
        logger.debug(
            "[wake_face_orient] cooldown device_id=%s age_ms=%d",
            dev,
            int((now - last) * 1000),
        )
        return 0

    faces = list_recent_positive_faces(dev, max_age_sec=_FACE_MAX_AGE_SEC)
    analysis = analyze_face_detections(list(faces.values())) if faces else None
    if not analysis or not analysis.get("landmarks"):
        logger.info(
            "[wake_face_orient] skip device_id=%s asr_req=%s reason=no_recent_face max_age_ms=%d",
            dev,
            asr_request_id or "-",
            int(_FACE_MAX_AGE_SEC * 1000),
        )
        return 0

    screen_yaw, screen_pitch = _screen_angles_from_analysis(analysis)
    step = build_wake_face_servo_step(analysis, device_id=dev, current_servo=current_servo)
    if step is None:
        logger.info(
            "[wake_face_orient] skip device_id=%s asr_req=%s reason=face_in_dead_zone yaw=%s pitch=%s",
            dev,
            asr_request_id or "-",
            screen_yaw,
            screen_pitch,
        )
        return 0

    request_id = uuid.uuid4().hex[:16]
    payload: dict[str, Any] = {
        "type": "pb_single",
        "req": request_id,
        "idx": 0,
        "chunk_ms": int(step["ms"]),
        "pb_ver": 2,
        # 实时舵机 replace 会清掉尚未执行的待机动作；本动作结束后不再续发，
        # 随后的口播/LLM 动作可正常接管。
        "action": PB_ACTION_REPLACE,
        "level": PB_LEVEL_DEBUG,
        "servo": [step],
    }
    attach_pb_device_hints_from_config(payload)
    try:
        delivered = int(await hub.send(dev, payload))
    except Exception:
        logger.exception(
            "[wake_face_orient] send failed device_id=%s asr_req=%s req=%s",
            dev,
            asr_request_id or "-",
            request_id,
        )
        return 0
    if delivered <= 0:
        logger.info(
            "[wake_face_orient] not delivered device_id=%s asr_req=%s req=%s",
            dev,
            asr_request_id or "-",
            request_id,
        )
        return 0

    _last_orient_mono[dev] = now
    logger.info(
        "[wake_face_orient] sent device_id=%s asr_req=%s req=%s wake_reason=%s "
        "yaw=%s pitch=%s relative=(%d,%d) modes=(%d,%d) ms=%d delivered=%d",
        dev,
        asr_request_id or "-",
        request_id,
        wake_reason,
        screen_yaw,
        screen_pitch,
        step["x"],
        step["y"],
        step["xm"],
        step["ym"],
        step["ms"],
        delivered,
    )
    if current_servo:
        logger.info("[wake_face_orient] current_servo device_id=%s value=%s", dev, current_servo)

    try:
        from deskbot_server.ws.device_pipeline import publish_auto_dispatch_event

        await publish_auto_dispatch_event(
            hub.pipeline_broker,
            device_id=dev,
            request_id=request_id,
            source="wake_face_orient",
            summary=f"唤醒注视人脸，相对舵机 ({step['x']}, {step['y']})",
            status="ok",
        )
    except Exception:
        logger.debug("[wake_face_orient] publish event failed device_id=%s", dev, exc_info=True)

    # 固件收到后续 replace 口播时会清理尚未完成的电机帧，因此这里等本次
    # 动作完整执行完（最长 500ms），避免大角度注视只走到一半。
    await asyncio.sleep(min(_START_SETTLE_SEC, max(0.0, step["ms"] / 1000.0)))
    return delivered
