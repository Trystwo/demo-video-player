"""P0-a: reject implausible landmark geometry instead of drawing a thin oval.

The M5.2 review (``.cache/qc/m52_bounds/report/REPORT.md``) found that a
90-degree profile collapses YuNet's five points to a few pixels while the
detector box keeps the head width.  ``fit_face_shape`` then built a 12 px wide
oval inside a 144 px box: a thin beautified strip across the cheek.

These tests pin the two new sanity checks against real probe rows:

* ``d / box_w`` -- the oval (``2a = 1.8 d``) must be at least half the box wide;
* ``b / d`` -- the landmark-derived face height must be consistent with the
  interocular distance (0.99..1.38 on every valid row of the review clips).

They also pin the consequence in :meth:`BeautyParams.uniforms`: a primary face
with finite but implausible points contributes no mask at all (``u_face0``
zeroed, warp off), while missing or non-finite landmarks keep the M5 box
fallback so a lost detection degrades instead of flickering off.
"""

from __future__ import annotations

import math

import pytest

from video_player.beauty import (
    SHAPE_MAX_B_TO_D,
    SHAPE_MIN_B_TO_D,
    SHAPE_MIN_D_TO_BOX,
    BeautyParams,
    fit_face_shape,
    shape_uniforms,
)

FRAME = (1920, 1080)
PROFILE_FRAME = (770, 1080)

# -- probe fixtures ---------------------------------------------------------
# ``box`` is the detector box in display pixels, ``landmarks`` are the five
# YuNet points (right eye, left eye, nose, right mouth, left mouth).

#: .cache/qc/m52_bounds/probe/demo37.faces.json, t=2.999 (frontal selfie).
FRONT = {
    "box": (616.5, 248.3, 513.9, 643.4),
    "landmarks": (
        (753.8, 482.7),
        (1012.2, 487.5),
        (881.8, 647.3),
        (767.6, 744.1),
        (986.2, 749.7),
    ),
}

#: .cache/qc/m52_bounds/probe/probe_turn.jsonl, frame 1920x1080, 30 fps.
TURN = {
    "0.33s": {
        "box": (748.8, 346.5, 448.7, 456.6),
        "landmarks": (
            (855.0, 500.7),
            (1017.0, 505.1),
            (883.8, 591.8),
            (879.3, 676.3),
            (1000.3, 679.2),
        ),
    },
    "2.73s": {
        "box": (784.3, 344.4, 519.6, 618.0),
        "landmarks": (
            (975.2, 562.2),
            (1212.1, 568.1),
            (1141.8, 685.9),
            (988.3, 792.6),
            (1161.0, 797.3),
        ),
    },
    "4.27s": {
        "box": (566.2, 200.1, 532.8, 574.9),
        "landmarks": (
            (751.5, 444.7),
            (1016.8, 448.4),
            (873.1, 561.2),
            (780.3, 661.7),
            (980.0, 663.6),
        ),
    },
}

#: .cache/qc/m52_bounds/probe/06_three_faces.faces.json (side faces) and
#: all_multi.json (center primary; the app's box is 529 px, not the 315 px
#: detector overlay box quoted in the report).
MULTI = {
    "left": {
        "box": (167.4, 36.9, 315.3, 428.2),
        "landmarks": (
            (288.5, 204.9),
            (428.8, 207.4),
            (378.6, 289.3),
            (291.2, 350.3),
            (413.3, 354.4),
        ),
    },
    "center": {
        "box": (764.0, 200.8, 529.2, 431.5),
        "landmarks": (
            (962.0, 375.5),
            (1121.2, 403.8),
            (1011.5, 463.7),
            (931.5, 521.6),
            (1038.3, 544.4),
        ),
    },
    "right": {
        "box": (1438.8, 333.3, 312.2, 422.3),
        "landmarks": (
            (1490.9, 500.9),
            (1627.4, 499.9),
            (1533.4, 586.4),
            (1502.2, 651.8),
            (1616.1, 651.1),
        ),
    },
}

#: .cache/qc/m52_bounds/probe/02_two_people_h264.faces.json, t=9.009.
TWO_PEOPLE = {
    "box": (425.0, 36.0, 314.1, 417.7),
    "landmarks": (
        (549.2, 194.4),
        (686.0, 197.8),
        (639.3, 279.4),
        (552.5, 341.5),
        (669.2, 345.6),
    ),
}

#: .cache/qc/m52_bounds/probe/probe_profile.jsonl, frame 770x1080.
PROFILE_VALID = {
    "3.60s": {
        "box": (2.7, 55.2, 130.7, 384.0),
        "landmarks": (
            (14.0, 205.7),
            (109.6, 208.5),
            (64.4, 293.2),
            (14.5, 354.6),
            (98.4, 354.4),
        ),
    },
}

PROFILE_DEGENERATE = {
    # The first frame where b/d fails; d/box_w is still 0.585, so this one is
    # rejected by the height ratio alone.
    "3.80s (b/d only)": {
        "box": (1.6, 43.6, 95.4, 360.1),
        "landmarks": (
            (-6.6, 189.1),
            (48.6, 197.1),
            (19.7, 270.0),
            (-10.0, 322.5),
            (36.0, 325.7),
        ),
    },
    "5.77s": {
        "box": (460.1, 7.9, 127.0, 241.3),
        "landmarks": (
            (488.6, 30.6),
            (497.0, 39.5),
            (457.6, 100.2),
            (490.2, 177.8),
            (493.8, 182.7),
        ),
    },
    "9.00s": {
        "box": (169.6, 4.0, 117.6, 256.4),
        "landmarks": (
            (196.5, 30.1),
            (205.3, 40.1),
            (166.2, 97.5),
            (197.4, 177.8),
            (200.1, 185.3),
        ),
    },
    "9.60s": {
        "box": (213.3, 5.3, 112.8, 191.6),
        "landmarks": (
            (235.0, 16.2),
            (245.4, 21.2),
            (205.2, 63.9),
            (234.1, 129.8),
            (241.8, 134.4),
        ),
    },
}

#: .cache/qc/m52_bounds/probe/selfie92.faces.json: near-profile background
#: faces (yaw -53..-71 degrees) picked as primary because the selfie face was
#: not detected at that moment.
SELFIE92_DEGENERATE = {
    "11.20s yaw -52.9": {
        "box": (139.6, 9.0, 71.1, 92.5),
        "landmarks": (
            (157.2, 42.7),
            (167.7, 43.1),
            (153.9, 60.2),
            (161.0, 72.2),
            (168.6, 77.6),
        ),
    },
    "78.87s yaw -71.1": {
        "box": (339.9, 241.0, 123.6, 157.2),
        "landmarks": (
            (368.9, 298.8),
            (384.9, 308.6),
            (353.8, 335.2),
            (366.8, 357.7),
            (373.3, 364.6),
        ),
    },
}


def _ratios(fixture: dict) -> tuple[float, float]:
    """``(d / box_w, b / d)`` exactly as :func:`fit_face_shape` computes them."""
    pts = fixture["landmarks"]
    (rx, ry), (lx, ly) = pts[0], pts[1]
    mouth_right, mouth_left = pts[3], pts[4]
    d = math.hypot(lx - rx, ly - ry)
    eye_y = 0.5 * (ry + ly)
    mouth_y = 0.5 * (mouth_right[1] + mouth_left[1])
    b = 0.5 * ((mouth_y + 0.42 * d) - (eye_y - 0.80 * d))
    return d / fixture["box"][2], b / d


# -- geometry checks --------------------------------------------------------


@pytest.mark.parametrize(
    "fixture",
    [
        pytest.param(FRONT, id="front-demo37"),
        pytest.param(TURN["0.33s"], id="turn-0.33s"),
        pytest.param(TURN["2.73s"], id="turn-2.73s"),
        pytest.param(TURN["4.27s"], id="turn-4.27s"),
        pytest.param(MULTI["left"], id="multi-left"),
        pytest.param(MULTI["center"], id="multi-center"),
        pytest.param(MULTI["right"], id="multi-right"),
        pytest.param(TWO_PEOPLE, id="two-people"),
        pytest.param(PROFILE_VALID["3.60s"], id="profile-3.60s"),
    ],
)
def test_probe_valid_faces_pass_the_sanity_checks(fixture: dict) -> None:
    shape = fit_face_shape(fixture["landmarks"], box_width=fixture["box"][2])
    assert shape is not None
    dw, bd = _ratios(fixture)
    assert dw >= SHAPE_MIN_D_TO_BOX
    assert SHAPE_MIN_B_TO_D <= bd <= SHAPE_MAX_B_TO_D
    # The P0-a acceptance: a drawn oval is at least half the detector box wide.
    assert 2.0 * shape.a >= 0.5 * fixture["box"][2]


@pytest.mark.parametrize(
    "fixture",
    [
        pytest.param(PROFILE_DEGENERATE["5.77s"], id="profile-5.77s"),
        pytest.param(PROFILE_DEGENERATE["9.00s"], id="profile-9.00s"),
        pytest.param(PROFILE_DEGENERATE["9.60s"], id="profile-9.60s"),
        pytest.param(SELFIE92_DEGENERATE["11.20s yaw -52.9"], id="selfie92-11.20s"),
        pytest.param(SELFIE92_DEGENERATE["78.87s yaw -71.1"], id="selfie92-78.87s"),
    ],
)
def test_probe_degenerate_faces_are_rejected(fixture: dict) -> None:
    dw, bd = _ratios(fixture)
    assert dw < SHAPE_MIN_D_TO_BOX or not (SHAPE_MIN_B_TO_D <= bd <= SHAPE_MAX_B_TO_D)
    assert fit_face_shape(fixture["landmarks"], box_width=fixture["box"][2]) is None


def test_profile_transition_is_rejected_by_height_even_with_plausible_width() -> None:
    fixture = PROFILE_DEGENERATE["3.80s (b/d only)"]
    dw, bd = _ratios(fixture)
    assert dw >= SHAPE_MIN_D_TO_BOX
    assert bd > SHAPE_MAX_B_TO_D
    assert fit_face_shape(fixture["landmarks"], box_width=fixture["box"][2]) is None


def test_collapsed_landmarks_are_rejected_without_a_box_width() -> None:
    # b/d alone already catches the profile collapse; the box check is an
    # additional guard for boxes that hide the collapse (multi-face scenes).
    fixture = PROFILE_DEGENERATE["5.77s"]
    assert fit_face_shape(fixture["landmarks"]) is None


def test_min_d_to_box_keeps_the_oval_at_least_half_the_box_wide() -> None:
    # a = 0.90 d, so the oval width is 1.8 d.  The constant is chosen from the
    # report's acceptance criterion, not from the probe distribution.
    assert 1.8 * SHAPE_MIN_D_TO_BOX >= 0.5
    from video_player.beauty import LANDMARK_LEFT_EYE, LANDMARK_RIGHT_EYE

    d = math.dist(FRONT["landmarks"][LANDMARK_RIGHT_EYE], FRONT["landmarks"][LANDMARK_LEFT_EYE])
    assert fit_face_shape(FRONT["landmarks"], box_width=d / 0.29) is not None
    assert fit_face_shape(FRONT["landmarks"], box_width=d / 0.27) is None


def test_b_to_d_bounds_accept_real_faces_and_reject_collapsed_mouth_points() -> None:
    # d = 100, eyes on y = 0: b/d = (mouth_y + 122) / 200.
    def landmarks(mouth_y: float) -> tuple[tuple[float, float], ...]:
        return ((0.0, 0.0), (100.0, 0.0), (50.0, 50.0), (10.0, mouth_y), (90.0, mouth_y))

    assert fit_face_shape(landmarks(30.0), box_width=300.0) is None  # b/d = 0.76
    assert fit_face_shape(landmarks(100.0), box_width=300.0) is not None  # 1.11
    assert fit_face_shape(landmarks(160.0), box_width=300.0) is not None  # 1.41
    assert fit_face_shape(landmarks(240.0), box_width=300.0) is None  # 1.81


# -- uniforms: degenerate primary paints nothing ----------------------------


def test_degenerate_primary_is_dropped_from_the_beauty_pass() -> None:
    fixture = PROFILE_DEGENERATE["9.00s"]
    values = BeautyParams(smoothing=0.6, whitening=0.4).uniforms(
        frame_size=PROFILE_FRAME,
        faces=[fixture["box"]],
        landmarks=fixture["landmarks"],
    )
    assert values["u_active"] == 1.0
    assert values["u_shape_ready"] == 0.0
    assert values["u_warp_ready"] == 0.0
    assert values["u_face0_x"] == 0.0
    assert values["u_face0_y"] == 0.0
    assert values["u_face0_w"] == 0.0
    assert values["u_face0_h"] == 0.0
    assert values["u_disp_w"] == 770.0
    assert values["u_disp_h"] == 1080.0


def test_degenerate_primary_leaves_secondary_faces_untouched() -> None:
    fixture = PROFILE_DEGENERATE["5.77s"]
    left, right = MULTI["left"], MULTI["right"]
    values = BeautyParams(smoothing=0.6).uniforms(
        frame_size=FRAME,
        faces=[fixture["box"], left["box"], right["box"]],
        landmarks=fixture["landmarks"],
    )
    assert values["u_face0_w"] == 0.0
    assert values["u_face1_x"] == pytest.approx(left["box"][0] / FRAME[0])
    assert values["u_face1_w"] == pytest.approx(left["box"][2] / FRAME[0])
    assert values["u_face2_x"] == pytest.approx(right["box"][0] / FRAME[0])
    assert values["u_face2_w"] == pytest.approx(right["box"][2] / FRAME[0])
    assert values["u_shape_ready"] == 0.0
    assert values["u_warp_ready"] == 0.0


def test_valid_primary_keeps_the_box_mask_and_the_warp() -> None:
    values = BeautyParams(smoothing=0.5, face_slim=0.4).uniforms(
        frame_size=FRAME,
        faces=[FRONT["box"]],
        landmarks=FRONT["landmarks"],
    )
    assert values["u_face0_w"] == pytest.approx(FRONT["box"][2] / FRAME[0])
    assert values["u_shape_ready"] == 1.0
    assert values["u_warp_ready"] == 1.0


def test_missing_landmarks_keep_the_m5_box_fallback() -> None:
    values = BeautyParams(smoothing=0.5).uniforms(
        frame_size=FRAME, faces=[FRONT["box"]], landmarks=None
    )
    assert values["u_face0_w"] == pytest.approx(FRONT["box"][2] / FRAME[0])
    assert values["u_shape_ready"] == 0.0
    assert values["u_warp_ready"] == 0.0


def test_non_finite_landmarks_keep_the_m5_box_fallback() -> None:
    broken = list(FRONT["landmarks"])
    broken[2] = (float("nan"), broken[2][1])
    values = BeautyParams(smoothing=0.5).uniforms(
        frame_size=FRAME, faces=[FRONT["box"]], landmarks=broken
    )
    # A lost/garbled detection must not flicker the whole face off.
    assert values["u_face0_w"] == pytest.approx(FRONT["box"][2] / FRAME[0])
    assert values["u_shape_ready"] == 0.0


def test_shape_uniforms_accept_the_box_width() -> None:
    good = PROFILE_VALID["3.60s"]
    bad = PROFILE_DEGENERATE["9.00s"]
    assert shape_uniforms(good["landmarks"], PROFILE_FRAME, box_width=good["box"][2])[
        "u_shape_ready"
    ] == 1.0
    assert shape_uniforms(bad["landmarks"], PROFILE_FRAME, box_width=bad["box"][2])[
        "u_shape_ready"
    ] == 0.0


def test_suppression_survives_a_display_rotation() -> None:
    fixture = PROFILE_DEGENERATE["9.00s"]
    values = BeautyParams(smoothing=0.6).uniforms(
        frame_size=PROFILE_FRAME,
        faces=[fixture["box"]],
        landmarks=fixture["landmarks"],
        rotation=90,
    )
    assert values["u_face0_w"] == 0.0
    assert values["u_shape_ready"] == 0.0
    assert values["u_warp_ready"] == 0.0
