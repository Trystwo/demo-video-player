"""P0-b: every face with landmarks gets its own oval.

The M5.2 review (``.cache/qc/m52_bounds/report/REPORT.md`` §5) found that the
primary face used a landmark oval while secondary faces kept a rectangular box
mask lifted by a 10 % floor.  On the 06 three-face shot the same left face
measured MAE 0.45 as primary and 14.74 as secondary: hair and background inside
the secondary box were smoothed and whitened.

P0-b fits each face from its own five points, giving every face the same oval
and the same per-pixel Lab gate.  A secondary face whose points are finite but
implausible is dropped exactly like the primary; a face with no landmarks keeps
the old box mask.  The warp stays primary-only.

Fixtures are real rows from ``.cache/qc/m52_bounds/probe/`` (06 three faces at
t=3.0 s, centre box from ``all_multi.json``; degenerate profile at 9.00 s).
"""

from __future__ import annotations

import pytest

from video_player.beauty import (
    SECONDARY_SHAPE_UNIFORM_KEYS,
    SHAPE_UNIFORM_KEYS,
    BeautyParams,
    fit_face_shape,
    shape_uniforms,
)
from video_player.face import Face

FRAME = (1920, 1080)

#: 06_three_faces.faces.json (sides) + all_multi.json (centre), t=3.0 s.
LEFT = {
    "box": (167.4, 36.9, 315.3, 428.2),
    "landmarks": (
        (288.5, 204.9),
        (428.8, 207.4),
        (378.6, 289.3),
        (291.2, 350.3),
        (413.3, 354.4),
    ),
}

CENTER = {
    "box": (764.0, 200.8, 529.2, 431.5),
    "landmarks": (
        (962.0, 375.5),
        (1121.2, 403.8),
        (1011.5, 463.7),
        (931.5, 521.6),
        (1038.3, 544.4),
    ),
}

RIGHT = {
    "box": (1438.8, 333.3, 312.2, 422.3),
    "landmarks": (
        (1490.9, 500.9),
        (1627.4, 499.9),
        (1533.4, 586.4),
        (1502.2, 651.8),
        (1616.1, 651.1),
    ),
}

#: probe_profile.jsonl, 9.00 s: collapsed profile (d=13.7, b/d=6.34).
DEGENERATE_PROFILE = {
    "box": (1400.0, 300.0, 117.6, 256.4),
    "landmarks": (
        (1429.5, 330.1),
        (1438.3, 340.1),
        (1399.2, 397.5),
        (1430.4, 477.8),
        (1433.1, 485.3),
    ),
}


def _face(fixture: dict, score: float = 0.9) -> Face:
    box = fixture["box"]
    return Face(
        box[0],
        box[1],
        box[2],
        box[3],
        score=score,
        landmarks=tuple(fixture["landmarks"]),
    )


def _three_faces() -> tuple[Face, Face, Face]:
    return _face(CENTER), _face(LEFT), _face(RIGHT)


# -- per-face uniforms ------------------------------------------------------


def test_three_face_frame_gets_one_oval_per_face() -> None:
    values = BeautyParams().uniforms(frame_size=FRAME, faces=_three_faces())
    assert values["u_shape_ready"] == 1.0
    assert values["u_shape1_ready"] == 1.0
    assert values["u_shape2_ready"] == 1.0

    for index, fixture in enumerate((LEFT, RIGHT), start=1):
        shape = fit_face_shape(fixture["landmarks"], box_width=fixture["box"][2])
        assert shape is not None
        assert values[f"u_shape{index}_cx"] == pytest.approx(shape.cx)
        assert values[f"u_shape{index}_cy"] == pytest.approx(shape.cy)
        assert values[f"u_shape{index}_a"] == pytest.approx(shape.a)
        assert values[f"u_shape{index}_b"] == pytest.approx(shape.b)
        assert values[f"u_face{index}_w"] == pytest.approx(
            fixture["box"][2] / FRAME[0]
        )

    # Three different faces really do get three different ovals: a stale
    # primary shape reused for everyone would collapse these to one value.
    centres = {values["u_shape_cx"], values["u_shape1_cx"], values["u_shape2_cx"]}
    assert len(centres) == 3
    # The secondary uniforms carry no eye/slim values; warp stays primary-only.
    assert not any("shape1_eye" in key or "shape2_eye" in key for key in SHAPE_UNIFORM_KEYS)
    assert not any("warp1" in key or "warp2" in key for key in values)


def test_shape_uniforms_zeroes_secondary_ovals_by_default() -> None:
    values = shape_uniforms(
        CENTER["landmarks"], FRAME, box_width=CENTER["box"][2]
    )
    assert values["u_shape_ready"] == 1.0
    for key in SECONDARY_SHAPE_UNIFORM_KEYS:
        assert values[key] == 0.0
    assert values["u_disp_w"] == 1920.0


def test_secondary_shape_uses_its_own_box_width() -> None:
    # Same five points, huge box: d/box_w fails for the secondary face only.
    oversized = _face({"box": (1000.0, 200.0, 700.0, 500.0), "landmarks": LEFT["landmarks"]})
    values = BeautyParams().uniforms(
        frame_size=FRAME, faces=(_face(CENTER), oversized)
    )
    assert values["u_shape_ready"] == 1.0
    assert values["u_shape1_ready"] == 0.0
    assert values["u_face1_w"] == 0.0
    assert values["u_face1_x"] == 0.0
    # The primary keeps its box and warp.
    assert values["u_face0_w"] == pytest.approx(CENTER["box"][2] / FRAME[0])
    assert values["u_warp_ready"] == 1.0


def test_degenerate_secondary_paints_nothing_but_leaves_the_others() -> None:
    values = BeautyParams().uniforms(
        frame_size=FRAME,
        faces=(_face(CENTER), _face(LEFT), _face(DEGENERATE_PROFILE)),
    )
    assert values["u_shape2_ready"] == 0.0
    assert values["u_face2_x"] == 0.0
    assert values["u_face2_y"] == 0.0
    assert values["u_face2_w"] == 0.0
    assert values["u_face2_h"] == 0.0
    # The valid primary and the valid left face are untouched.
    assert values["u_shape_ready"] == 1.0
    assert values["u_shape1_ready"] == 1.0
    assert values["u_face1_w"] > 0.0
    assert values["u_warp_ready"] == 1.0


def test_secondary_without_landmarks_keeps_its_box_mask() -> None:
    side = Face(167.4, 36.9, 315.3, 428.2, score=0.8)
    values = BeautyParams().uniforms(
        frame_size=FRAME, faces=(_face(CENTER), side)
    )
    assert values["u_shape1_ready"] == 0.0
    assert values["u_face1_w"] == pytest.approx(315.3 / FRAME[0])
    # A missing third face writes zeros, which the shader treats as no mask.
    assert values["u_shape2_ready"] == 0.0
    assert values["u_face2_w"] == 0.0


def test_secondary_shapes_stay_aligned_when_a_zero_width_face_is_skipped() -> None:
    # normalise_face_boxes drops empty rectangles; the shape array must be
    # filtered the same way or u_shape1 would describe the wrong face.
    empty = Face(0.0, 0.0, 0.0, 0.0, score=0.5, landmarks=LEFT["landmarks"])
    values = BeautyParams().uniforms(
        frame_size=FRAME, faces=(_face(CENTER), empty, _face(LEFT))
    )
    left_shape = fit_face_shape(LEFT["landmarks"], box_width=LEFT["box"][2])
    assert left_shape is not None
    assert values["u_face1_w"] == pytest.approx(LEFT["box"][2] / FRAME[0])
    assert values["u_shape1_cx"] == pytest.approx(left_shape.cx)
    assert values["u_shape2_ready"] == 0.0


def test_secondary_uniforms_are_floats() -> None:
    values = BeautyParams().uniforms(frame_size=FRAME, faces=_three_faces())
    for key in SECONDARY_SHAPE_UNIFORM_KEYS:
        assert isinstance(values[key], float)
