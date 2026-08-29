from deskbot_server.dao.servo_config_store import (
    DEFAULT_SERVO_LIMITS,
    clamp_servo_step,
    logical_step_to_protocol,
    normalize_perspective,
    normalize_servo_document,
    resolve_move_for_perspective,
    servo_limits,
)


def test_default_servo_limits_x_compensated_gear_range():
    lim = servo_limits(device_id="__no_such_device__")
    assert lim["xMin"] == DEFAULT_SERVO_LIMITS["xMin"] == -20
    assert lim["xMax"] == DEFAULT_SERVO_LIMITS["xMax"] == 100


def test_normalize_servo_document_allows_negative_x_only():
    doc = normalize_servo_document(
        {"xMin": -30, "xMax": 90, "yMin": -20, "yMax": 70, "xReverse": 0, "yReverse": 1}
    )
    assert doc["xMin"] == -30
    assert doc["xMax"] == 90
    assert doc["yMin"] == -20
    assert doc["yMax"] == 70


def test_clamp_servo_step_absolute_x_no_reverse():
    lim = {"xMin": 0, "xMax": 180, "yMin": 70, "yMax": 110, "xReverse": 0, "yReverse": 0}
    step = clamp_servo_step({"xm": 0, "ym": 0, "x": 200, "y": 90, "ms": 500}, limits=lim)
    assert step["x"] == 180
    step2 = clamp_servo_step({"xm": 0, "ym": 0, "x": -5, "y": 90, "ms": 500}, limits=lim)
    assert step2["x"] == 0


def test_clamp_servo_step_absolute_x_with_reverse():
    lim = {"xMin": 0, "xMax": 180, "yMin": 70, "yMax": 110, "xReverse": 1, "yReverse": 0}
    step = clamp_servo_step({"xm": 0, "ym": 0, "x": 30, "y": 90, "ms": 500}, limits=lim)
    assert step["x"] == 150


def test_clamp_servo_step_relative_x_reverse():
    lim = {"xMin": 0, "xMax": 180, "yMin": 70, "yMax": 110, "xReverse": 1, "yReverse": 0}
    step = clamp_servo_step({"xm": 1, "ym": 1, "x": 45, "y": -20, "ms": 500}, limits=lim)
    assert step["x"] == -45
    assert step["y"] == -20


def test_clamp_servo_step_y_reverse_absolute_and_relative():
    lim = {"xMin": 0, "xMax": 180, "yMin": 70, "yMax": 110, "xReverse": 0, "yReverse": 1}

    absolute = clamp_servo_step({"xm": 0, "ym": 0, "x": 90, "y": 80, "ms": 500}, limits=lim)
    relative = clamp_servo_step({"xm": 1, "ym": 1, "x": 0, "y": 10, "ms": 300}, limits=lim)

    assert absolute["y"] == 100
    assert relative["y"] == -10


def test_logical_step_to_protocol_matches_frontend():
    lim = {"xMin": 0, "xMax": 180, "yMin": 70, "yMax": 110, "xReverse": 1, "yReverse": 0}
    out = logical_step_to_protocol({"xm": 0, "ym": 0, "x": 150, "y": 90, "ms": 500}, lim)
    assert out == {"xm": 0, "ym": 0, "x": 30, "y": 90, "ms": 500}


def test_hold_mode_preserves_axis_without_applying_reverse():
    lim = {"xMin": 0, "xMax": 180, "yMin": 70, "yMax": 110, "xReverse": 0, "yReverse": 1}

    out = logical_step_to_protocol({"xm": 1, "ym": 2, "x": 4, "y": 99, "ms": 400}, lim)

    assert out == {"xm": 1, "ym": 2, "x": 4, "y": 0, "ms": 400}


def test_resolve_move_for_perspective_viewer_swap():
    assert resolve_move_for_perspective("look_left", perspective="viewer") == "look_right"
    assert resolve_move_for_perspective("look_right", perspective="viewer") == "look_left"
    assert resolve_move_for_perspective("center", perspective="viewer") == "center"


def test_resolve_move_for_perspective_robot_no_swap():
    assert resolve_move_for_perspective("look_left", perspective="robot") == "look_left"


def test_normalize_perspective_default():
    assert normalize_perspective(None) == "viewer"
    assert normalize_perspective("robot") == "robot"
    assert normalize_perspective("invalid") == "viewer"
