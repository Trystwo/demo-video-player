"""P1: per-face branch decoupling and temporal hold.

Two failure modes are pinned here:

* the shader used to gate the whole landmark branch on the *primary* face's
  ``u_shape_ready``.  If the primary lost its landmarks for one frame, the
  secondary faces dropped back to box masks (and the 10 % floor).  The branch
  now keys on ``any_shape`` so each face keeps its own oval independently.
* ``BeautyParams.uniforms`` is stateless (its config is frozen and rebuilt every
  frame), so a single rejected landmark fit used to blink a face off.  A
  :class:`ShapeHold` owned by the backend bridges a few frames, then lets the
  normal drop/box-fallback path take over.

Fixtures are the same real probe rows used by the P0-a/P0-b tests.
"""

from __future__ import annotations

import pytest

from video_player.beauty import (
    BEAUTY_FRAGMENT_SHADER,
    SHAPE_HOLD_FRAMES,
    BeautyParams,
    ShapeHold,
)
from video_player.face import Face

FRAME = (1920, 1080)
BOX = (600.0, 250.0, 526.0, 599.0)
LANDMARKS = (
    (775.6, 469.9),
    (1036.2, 470.6),
    (920.4, 630.6),
    (798.0, 736.6),
    (1016.6, 737.3),
)
#: profile collapse: finite points, but d/box_w and b/d both fail.
BAD_LANDMARKS = (
    (1429.5, 330.1),
    (1438.3, 340.1),
    (1399.2, 397.5),
    (1430.4, 477.8),
    (1433.1, 485.3),
)


def _face(
    box: tuple[float, float, float, float] = BOX,
    landmarks: tuple[tuple[float, float], ...] = LANDMARKS,
) -> Face:
    return Face(box[0], box[1], box[2], box[3], score=0.9, landmarks=landmarks)


def _uniforms(hold: ShapeHold | None, *faces: Face, landmarks=None) -> dict[str, float]:
    return BeautyParams().uniforms(
        frame_size=FRAME,
        faces=faces,
        landmarks=landmarks if landmarks is not None else faces[0].landmarks,
        face_box=faces[0] if faces else None,
        shape_hold=hold,
    )


# -- shape hold -------------------------------------------------------------


def test_hold_reuses_the_last_oval_for_a_bad_frame() -> None:
    hold = ShapeHold(frames=3)
    good = _uniforms(hold, _face())
    assert good["u_shape_ready"] == 1.0

    held = _uniforms(hold, _face(landmarks=BAD_LANDMARKS))
    assert held["u_shape_ready"] == 1.0
    assert held["u_shape_cx"] == pytest.approx(good["u_shape_cx"])
    assert held["u_shape_cy"] == pytest.approx(good["u_shape_cy"])
    assert held["u_shape_a"] == pytest.approx(good["u_shape_a"])
    assert held["u_shape_b"] == pytest.approx(good["u_shape_b"])
    # The mask stays, but the warp must not follow the bad landmarks.
    assert held["u_warp_ready"] == 0.0
    assert held["u_face0_w"] == pytest.approx(good["u_face0_w"])


def test_hold_expires_after_the_configured_frames() -> None:
    hold = ShapeHold(frames=2)
    _uniforms(hold, _face())
    for _ in range(2):
        assert _uniforms(hold, _face(landmarks=BAD_LANDMARKS))["u_shape_ready"] == 1.0
    expired = _uniforms(hold, _face(landmarks=BAD_LANDMARKS))
    assert expired["u_shape_ready"] == 0.0
    assert expired["u_face0_w"] == 0.0
    assert expired["u_warp_ready"] == 0.0


def test_hold_does_not_follow_a_moved_face() -> None:
    hold = ShapeHold(frames=3)
    _uniforms(hold, _face())
    moved = _face(box=(1500.0, 300.0, 300.0, 340.0), landmarks=BAD_LANDMARKS)
    values = _uniforms(hold, moved)
    assert values["u_shape_ready"] == 0.0
    assert values["u_face0_w"] == 0.0


def test_hold_slot_is_cleared_when_the_face_disappears() -> None:
    hold = ShapeHold(frames=3)
    _uniforms(hold, _face())
    # No face at all: the tracker must forget the old oval, otherwise a new
    # face entering the same slot would inherit it.
    BeautyParams().uniforms(frame_size=FRAME, faces=(), shape_hold=hold)
    returned = _uniforms(hold, _face(landmarks=BAD_LANDMARKS))
    assert returned["u_shape_ready"] == 0.0


def test_hold_clear_forgets_every_slot() -> None:
    hold = ShapeHold(frames=3)
    _uniforms(hold, _face())
    hold.clear()
    assert _uniforms(hold, _face(landmarks=BAD_LANDMARKS))["u_shape_ready"] == 0.0


def test_default_hold_is_three_frames() -> None:
    assert SHAPE_HOLD_FRAMES == 3
    assert ShapeHold().frames == 3


# -- per-face decoupling ----------------------------------------------------


def test_primary_landmark_loss_keeps_secondary_ovals() -> None:
    """No hold: secondaries still get ovals when only the primary has none."""
    left = _face(
        box=(167.4, 36.9, 315.3, 428.2),
        landmarks=(
            (288.5, 204.9),
            (428.8, 207.4),
            (378.6, 289.3),
            (291.2, 350.3),
            (413.3, 354.4),
        ),
    )
    right = _face(
        box=(1438.8, 333.3, 312.2, 422.3),
        landmarks=(
            (1490.9, 500.9),
            (1627.4, 499.9),
            (1533.4, 586.4),
            (1502.2, 651.8),
            (1616.1, 651.1),
        ),
    )
    values = BeautyParams().uniforms(
        frame_size=FRAME,
        faces=(_face(landmarks=()), left, right),
        landmarks=(),
        face_box=_face(landmarks=()),
    )
    assert values["u_shape_ready"] == 0.0
    assert values["u_shape1_ready"] == 1.0
    assert values["u_shape2_ready"] == 1.0
    # The primary keeps its box (missing landmarks = M5 fallback) but does not
    # drag the secondaries back to boxes.
    assert values["u_face0_w"] > 0.0


def test_shader_branch_is_selected_by_any_face() -> None:
    body = BEAUTY_FRAGMENT_SHADER.split("float mask;", 1)[1]
    assert (
        "bool any_shape = u_shape_ready > 0.5 || u_shape1_ready > 0.5 || u_shape2_ready > 0.5;"
        in body
    )
    # Primary without an oval falls back to its own box inside the branch.
    assert "shape0 = face_mask(px, vec4(u_face0_x" in body
    # The old whole-branch gate must not come back.
    assert "float mask;\n    float white;\n    if (u_shape_ready > 0.5) {" not in body


def test_hold_drops_immediately_when_the_box_collapses() -> None:
    """A profile is not a one-frame glitch: the box width collapses with it."""
    hold = ShapeHold(frames=3)
    _uniforms(hold, _face())
    collapsed = _face(box=(690.0, 60.0, 70.0, 360.0), landmarks=BAD_LANDMARKS)
    values = _uniforms(hold, collapsed)
    assert values["u_shape_ready"] == 0.0
    assert values["u_face0_w"] == 0.0
