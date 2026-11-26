"""Landmark face shape, Lab gate and contour-moving lower-face warp.

M5 shipped a rounded-rectangle mask around the detector box and a slim warp
gated by the same box.  Review (M5.1/M5.2) showed both fail on real frames:

* the box contains hair, glasses frames, ears and background, and a 10 % floor
  beatified all of it ("beauty marks are obvious", "hair goes white");
* the box-edge displacement dragged nearby background, while the first M5.2
  oval gate pinned the silhouette and left only the face interior compressed.

M5.2 fits an oval to the five landmarks (top edge on the hairline, bottom on
the chin) and multiplies it with a CIELAB skin probability.  The slim warp now
moves the jaw boundary inward and fades through a finite exterior band.  These
tests pin the Python half of that contract plus a scalar reference of the
fragment shader's geometry, so the invariants are checked without a GPU:

* the oval is 0 at the centre of the face and 1 on the outline;
* the slim field is zero on the centre line, non-zero at the lower-face outline,
  and smoothly returns to 0 outside a finite band;
* the inverse source map remains monotonic (no fold) and the peak is bounded;
* a frame without the five landmarks keeps the M5 box behaviour.
"""

from __future__ import annotations

import math
import re

import pytest

from video_player.beauty import (
    BEAUTY_FRAGMENT_SHADER,
    SHAPE_UNIFORM_KEYS,
    SLIM_PEAK_MAX_PX,
    SLIM_PEAK_MIN_PX,
    SLIM_PEAK_RATIO,
    BeautyParams,
    FaceShape,
    fit_face_shape,
    shape_uniforms,
)

#: The five points of the board frame the M5.2 review was rendered on
#: (.cache/qc/frames/full_02_beauty_4s.png), in display pixels.
LANDMARKS = (
    (775.6, 469.9),  # right eye (image left)
    (1036.2, 470.6),  # left eye (image right)
    (920.4, 630.6),  # nose
    (798.0, 736.6),  # right mouth corner
    (1016.6, 737.3),  # left mouth corner
)
FRAME = (1920, 1080)


def _sstep(edge0: float, edge1: float, x: float) -> float:
    span = edge1 - edge0
    if abs(span) < 1e-9:
        return 1.0 if x >= edge1 else 0.0
    t = min(1.0, max(0.0, (x - edge0) / span))
    return t * t * (3.0 - 2.0 * t)


def _oval_radius(shape: FaceShape, x: float, y: float) -> float:
    return math.hypot((x - shape.cx) / shape.a, (y - shape.cy) / shape.b)


def _shape_mask(shape: FaceShape, x: float, y: float, feather: float) -> float:
    """Scalar reference of the shader's ``face_shape_mask``."""
    edge = (_oval_radius(shape, x, y) - 1.0) * shape.a
    oval = 1.0 - _sstep(0.0, max(feather, 1.0), edge)
    # Neck extension: rounded box hanging below the chin (same as the shader).
    chin_y = shape.cy + shape.b
    centre = (shape.cx, chin_y + 0.13 * shape.b)
    half_w = max(0.45 * shape.a, 1.0)
    half_h = max(0.29 * shape.b, 1.0)
    radius = min(half_w, half_h) * 0.45
    qx = abs(x - centre[0]) - max(half_w - radius, 0.0)
    qy = abs(y - centre[1]) - max(half_h - radius, 0.0)
    dist = math.hypot(max(qx, 0.0), max(qy, 0.0)) + min(max(qx, qy), 0.0) - radius
    neck = 1.0 - _sstep(0.0, max(feather, 1.0), dist)
    return max(oval, neck)


def _slim_support_band(peak: float, slim: float) -> float:
    return max(3.0 * peak * slim, 24.0)


def _slim_gate(
    shape: FaceShape, x: float, y: float, *, peak: float, slim: float
) -> float:
    """Scalar reference of the shader's ``slim_support_gate``."""
    outer = 1.0 + _slim_support_band(peak, slim) / max(shape.a, 1.0)
    return 1.0 - _sstep(1.0, outer, _oval_radius(shape, x, y))


def _slim_offset(
    shape: FaceShape, x: float, y: float, *, peak: float, slim: float
) -> tuple[float, float]:
    """Vector reference of the shader's ``face_slim_offset`` (display px).

    The offset is what the shader adds to the output position before sampling,
    so a positive ``dx`` on the right cheek samples further right (the visible
    jaw moves inward) and a positive ``dy`` below the chin samples lower (the
    visible chin lifts).  The exterior support gate is included.
    """
    a = max(shape.a, 1.0)
    b = max(shape.b, 1.0)
    rel_x = (x - shape.cx) / a
    rel_y = (y - shape.cy) / b
    below_eyes = _sstep(0.05, 0.55, (y - shape.eye_y) / max(shape.d, 1.0))
    side = _sstep(0.15, 0.95, abs(rel_x)) * below_eyes
    lift = (
        _sstep(0.55, 0.98, rel_y)
        * (1.0 - _sstep(0.55, 1.00, abs(rel_x)))
        * below_eyes
    )
    amount = peak * slim
    gate = _slim_gate(shape, x, y, peak=peak, slim=slim)
    dx = (-amount * side if rel_x < 0.0 else amount * side) * gate
    return dx, amount * 0.85 * lift * gate


def _slim_mag(shape: FaceShape, x: float, y: float, *, peak: float, slim: float) -> float:
    dx, dy = _slim_offset(shape, x, y, peak=peak, slim=slim)
    return math.hypot(dx, dy)


# -- fit_face_shape ---------------------------------------------------------


def test_fit_face_shape_reproduces_the_reviewed_geometry() -> None:
    shape = fit_face_shape(LANDMARKS)
    assert shape is not None
    # Interocular distance is the scale everything else derives from.
    assert shape.d == pytest.approx(260.6, abs=0.1)
    assert shape.a == pytest.approx(0.90 * shape.d)
    assert shape.b == pytest.approx(0.5 * ((736.95 + 0.42 * shape.d) - (470.25 - 0.80 * shape.d)))
    assert shape.eye_y == pytest.approx(470.25, abs=0.01)
    # Centre sits between the eyes and the mouth, so a yaw does not shift it.
    assert shape.cx == pytest.approx(906.65, abs=0.1)
    assert shape.cy == pytest.approx(0.5 * ((470.25 - 0.8 * shape.d) + (736.95 + 0.42 * shape.d)))


def test_fit_face_shape_radius_is_one_on_the_outline() -> None:
    shape = fit_face_shape(LANDMARKS)
    assert shape is not None
    assert shape.radius(shape.cx, shape.cy) == pytest.approx(0.0, abs=1e-9)
    assert shape.radius(shape.cx + shape.a, shape.cy) == pytest.approx(1.0)
    assert shape.radius(shape.cx, shape.cy - shape.b) == pytest.approx(1.0)
    assert shape.radius(shape.cx, shape.cy + shape.b) == pytest.approx(1.0)


@pytest.mark.parametrize("landmarks", [None, (), LANDMARKS[:4], LANDMARKS + ((1.0, 2.0),)])
def test_fit_face_shape_needs_exactly_five_points(landmarks) -> None:
    assert fit_face_shape(landmarks) is None


def test_fit_face_shape_rejects_degenerate_and_non_finite_points() -> None:
    flat = tuple((100.0, 100.0) for _ in range(5))
    assert fit_face_shape(flat) is None
    broken = list(LANDMARKS)
    broken[2] = (float("nan"), 630.6)
    assert fit_face_shape(tuple(broken)) is None


def test_slim_peak_scales_with_the_face_and_is_clamped() -> None:
    # A close-up face saturates the pixel cap; a distant face retains a minimum.
    wide = FaceShape(cx=0.0, cy=0.0, a=500.0, b=600.0, eye_y=-100.0, d=444.0)
    assert wide.slim_peak_px == pytest.approx(SLIM_PEAK_MAX_PX)
    tiny = FaceShape(cx=0.0, cy=0.0, a=20.0, b=25.0, eye_y=-5.0, d=22.0)
    assert tiny.slim_peak_px == pytest.approx(SLIM_PEAK_MIN_PX)
    mid = FaceShape(cx=0.0, cy=0.0, a=234.5, b=292.3, eye_y=-84.0, d=260.6)
    assert mid.slim_peak_px == pytest.approx(SLIM_PEAK_RATIO * 234.5, abs=0.01)


# -- shape_uniforms ---------------------------------------------------------


def test_shape_uniforms_are_off_without_landmarks_but_keep_the_frame_size() -> None:
    values = shape_uniforms(None, FRAME)
    assert set(values) == set(SHAPE_UNIFORM_KEYS)
    assert values["u_shape_ready"] == 0.0
    assert values["u_disp_w"] == 1920.0
    assert values["u_disp_h"] == 1080.0
    assert all(v == 0.0 for k, v in values.items() if k not in {"u_disp_w", "u_disp_h"})


def test_shape_uniforms_carry_the_fitted_oval() -> None:
    values = shape_uniforms(LANDMARKS, FRAME)
    assert values["u_shape_ready"] == 1.0
    shape = fit_face_shape(LANDMARKS)
    assert shape is not None
    assert values["u_shape_cx"] == pytest.approx(shape.cx)
    assert values["u_shape_cy"] == pytest.approx(shape.cy)
    assert values["u_shape_a"] == pytest.approx(shape.a)
    assert values["u_shape_b"] == pytest.approx(shape.b)
    assert values["u_shape_eye_y"] == pytest.approx(shape.eye_y)
    assert values["u_shape_d"] == pytest.approx(shape.d)
    assert values["u_slim_peak"] == pytest.approx(shape.slim_peak_px)


def test_uniforms_include_the_shape_block_for_a_landmarked_frame() -> None:
    values = BeautyParams(smoothing=0.5, whitening=0.3).uniforms(
        frame_size=FRAME, faces=[(600, 250, 526, 599)], landmarks=LANDMARKS
    )
    assert set(SHAPE_UNIFORM_KEYS) <= set(values)
    assert values["u_shape_ready"] == 1.0
    assert values["u_warp_ready"] == 1.0
    # Every uniform the shader reads must be a float (glshader drops gdouble).
    assert all(isinstance(v, float) for v in values.values())


def test_shape_uniforms_default_to_off_for_a_bare_frame() -> None:
    values = BeautyParams().uniforms(frame_size=FRAME)
    assert values["u_shape_ready"] == 0.0
    assert values["u_disp_w"] == 1920.0


# -- shader contract --------------------------------------------------------


def test_shader_declares_every_shape_uniform() -> None:
    for key in SHAPE_UNIFORM_KEYS:
        assert f"uniform float {key};" in BEAUTY_FRAGMENT_SHADER
    # P0-b: every face with landmarks gets its own oval; the old "secondary
    # faces keep a box mask lifted by a 10 % floor" path must be gone from the
    # landmark branch (it lives on only in the no-primary-landmarks fallback).
    # Start at the fragment mask declaration: ``u_shape_ready`` also appears
    # earlier in ``face_mask`` (the warp gate), so splitting on the first
    # occurrence would only capture a helper.
    shape_branch = BEAUTY_FRAGMENT_SHADER.split("float mask;", 1)[1]
    shape_branch = shape_branch.split(
        "    } else {\n        mask = face_mask(px, vec4(u_face0_x", 1
    )[0]
    assert "u_shape1_ready" in shape_branch and "u_shape2_ready" in shape_branch
    assert shape_branch.count("face_shape_mask_at(") >= 2
    assert "others" not in shape_branch
    assert "0.10 + 0.90" not in shape_branch
    # Same per-pixel Lab gate for every face; no un-gated mask survives.
    assert "shape0 * gates.x" in shape_branch
    assert "shape1 * gates.x" in shape_branch
    assert "shape2 * gates.x" in shape_branch
    assert "white = max(shape0 * gates.y" in shape_branch


def test_shader_has_no_stale_shape_helpers() -> None:
    # Guard against a half-applied edit: the oval path must not still gate the
    # warp on the detector box.
    assert re.search(
        r"float warp_gate\(vec2 px, vec2 disp_px, vec4 box, float face_radius\)",
        BEAUTY_FRAGMENT_SHADER,
    )
    assert "u_shape_feather" not in BEAUTY_FRAGMENT_SHADER


def test_shader_keeps_slim_transition_separate_from_eye_gate() -> None:
    match = re.search(
        r"vec2 warp_uv\(vec2 texcoord\) \{(?P<body>.*?)\n\}",
        BEAUTY_FRAGMENT_SHADER,
        re.DOTALL,
    )
    assert match is not None
    body = match.group("body")
    assert "slim_support_gate(face_radius)" in body
    assert "offset += face_slim_offset(disp_px) / disp_size * slim_gate;" in body
    assert "eye_enlarge_offset(disp, vec2(u_warp_lx0, u_warp_ly0)) * eye_gate" in body
    assert "eye_enlarge_offset(disp, vec2(u_warp_lx1, u_warp_ly1)) * eye_gate" in body
    assert "offset * gate" not in body


def test_shader_slims_the_sides_and_lifts_the_chin_without_a_tip_cap() -> None:
    match = re.search(
        r"vec2 face_slim_offset\(vec2 disp_px\) \{(?P<body>.*?)\n\}",
        BEAUTY_FRAGMENT_SHADER,
        re.DOTALL,
    )
    assert match is not None
    body = match.group("body")
    # The horizontal part is normalised by the oval half-width only.  A per-row
    # normalisation (a_y) is what made the displacement collapse row by row near
    # the tip and read as a crease.
    assert "float rel_x = (disp_px.x - u_shape_cx) / a;" in body
    assert "a_y" not in body
    assert "float side = smoothstep(0.15, 0.95, abs(rel_x)) * below_eyes;" in body
    assert "float lift = smoothstep(0.55, 0.98, rel_y)" in body
    assert "return vec2((rel_x < 0.0 ? -1.0 : 1.0) * amount * side, amount * 0.85 * lift);" in body
    # No C0 clamp/taper at the tip any more.
    assert "min(requested_amount" not in body
    assert "jaw_taper" not in body


def test_oval_mask_is_one_inside_and_zero_outside() -> None:
    shape = fit_face_shape(LANDMARKS)
    assert shape is not None
    assert _shape_mask(shape, shape.cx, shape.cy, 24.0) == pytest.approx(1.0)
    assert _shape_mask(shape, shape.cx + 1.4 * shape.a, shape.cy, 24.0) == pytest.approx(0.0)
    assert _shape_mask(shape, shape.cx, shape.cy - 1.3 * shape.b, 24.0) == pytest.approx(0.0)
    # Half way through the feather band the mask is ~0.5.
    edge = shape.cx + shape.a + 12.0
    assert _shape_mask(shape, edge, shape.cy, 24.0) == pytest.approx(0.5, abs=0.06)


def test_oval_mask_covers_the_neck_below_the_chin() -> None:
    shape = fit_face_shape(LANDMARKS)
    assert shape is not None
    # Just under the chin the oval has already ended; the neck patch keeps the
    # jaw/neck boundary from showing an untreated step.
    assert _shape_mask(shape, shape.cx, shape.cy + shape.b - 4.0, 24.0) > 0.5
    assert _shape_mask(shape, shape.cx, shape.cy + shape.b + 0.25 * shape.b, 24.0) > 0.5
    # ...but it does not reach the collar or the background.
    assert _shape_mask(shape, shape.cx + 0.8 * shape.a, shape.cy + shape.b + 0.6 * shape.b, 24.0) == pytest.approx(0.0)


def test_slim_support_gate_is_one_on_the_outline_and_fades_outside() -> None:
    shape = fit_face_shape(LANDMARKS)
    assert shape is not None
    peak = 20.0
    band = _slim_support_band(peak, 1.0)
    outer = 1.0 + band / shape.a
    # Probe the gate radially at the widest row so the oval radius is |rel_x|.
    assert _slim_gate(shape, shape.cx + shape.a, shape.cy, peak=peak, slim=1.0) == pytest.approx(1.0)
    assert _slim_gate(
        shape, shape.cx + shape.a * (1.0 + band / shape.a * 0.5), shape.cy, peak=peak, slim=1.0
    ) == pytest.approx(0.5)
    assert _slim_gate(
        shape, shape.cx + shape.a * outer, shape.cy, peak=peak, slim=1.0
    ) == pytest.approx(0.0)
    assert _slim_gate(
        shape, shape.cx + shape.a * (outer + 0.02), shape.cy, peak=peak, slim=1.0
    ) == pytest.approx(0.0)
    # The band widens with the effect strength.
    weak = _slim_support_band(peak, 0.25)
    assert weak == max(3.0 * peak * 0.25, 24.0)
    assert weak < band


def test_slim_offset_has_no_horizontal_push_on_the_centre_line() -> None:
    shape = fit_face_shape(LANDMARKS)
    assert shape is not None
    peak = 20.0
    y = shape.cy + 0.55 * shape.b  # jaw height, just above the chin lift zone
    a_y = shape.a * math.sqrt(max(1.0 - ((y - shape.cy) / shape.b) ** 2, 1e-6))
    assert _slim_offset(shape, shape.cx, y, peak=peak, slim=1.0) == pytest.approx(
        (0.0, 0.0), abs=1e-6
    )
    right = _slim_offset(shape, shape.cx + a_y, y, peak=peak, slim=1.0)
    left = _slim_offset(shape, shape.cx - a_y, y, peak=peak, slim=1.0)
    assert right[0] > 0.85 * peak and left[0] < -0.85 * peak
    assert right[0] == pytest.approx(-left[0])
    # Outside the outline the pull falls away to exactly zero at the end of the
    # band, so the exterior has no step.
    band = _slim_support_band(peak, 1.0)
    rel_y = (y - shape.cy) / shape.b
    outer = 1.0 + band / shape.a
    x_zero = shape.cx + shape.a * math.sqrt(outer * outer - rel_y * rel_y)
    values = []
    step = band / 16.0
    steps = int((x_zero - (shape.cx + a_y)) // step) + 1
    for i in range(steps):
        x = shape.cx + a_y + i * step
        values.append(abs(_slim_offset(shape, x, y, peak=peak, slim=1.0)[0]))
    assert max(values) <= peak + 1e-6
    assert values[0] > 12.0
    assert _slim_offset(shape, x_zero, y, peak=peak, slim=1.0)[0] == pytest.approx(0.0, abs=1e-6)
    tail = [abs(_slim_offset(shape, x_zero - k * step, y, peak=peak, slim=1.0)[0]) for k in range(4, -1, -1)]
    assert all(b < a for a, b in zip(tail, tail[1:]))


def test_slim_pulls_the_side_outline_inward_by_about_the_peak() -> None:
    shape = fit_face_shape(LANDMARKS)
    assert shape is not None
    peak = shape.slim_peak_px
    y = shape.cy + 0.70 * shape.b
    a_y = shape.a * math.sqrt(max(1.0 - ((y - shape.cy) / shape.b) ** 2, 1e-6))
    old_edge = shape.cx + a_y
    lo, hi = shape.cx + 0.4 * a_y, old_edge
    for _ in range(48):
        middle = 0.5 * (lo + hi)
        dx, _ = _slim_offset(shape, middle, y, peak=peak, slim=1.0)
        if middle + dx < old_edge:
            lo = middle
        else:
            hi = middle
    new_edge = 0.5 * (lo + hi)
    edge_dx, _ = _slim_offset(shape, old_edge, y, peak=peak, slim=1.0)
    inward = old_edge - new_edge
    assert 0.80 * edge_dx <= inward <= edge_dx + 1e-6
    assert 0.55 * peak < inward <= peak + 1e-6


def test_chin_bottom_is_lifted_instead_of_left_pinned() -> None:
    shape = fit_face_shape(LANDMARKS)
    assert shape is not None
    peak = shape.slim_peak_px
    chin = (shape.cx, shape.cy + shape.b)
    dx, dy = _slim_offset(shape, chin[0], chin[1], peak=peak, slim=1.0)
    # The bottom outline is almost horizontal: only a vertical sample can move it.
    assert abs(dx) < 0.02 * peak
    assert dy > 0.6 * peak
    # ...and it keeps lifting a few pixels above the tip, without a step.
    above = _slim_offset(shape, shape.cx, shape.cy + 0.90 * shape.b, peak=peak, slim=1.0)[1]
    assert above > 0.5 * peak
    # Sides do not lift: the vertical part is confined to the chin zone.
    side = _slim_offset(shape, shape.cx + 0.95 * shape.a, shape.cy + 0.55 * shape.b, peak=peak, slim=1.0)
    assert abs(side[1]) < 0.15 * peak


def test_slim_map_is_injective_over_the_lower_face() -> None:
    """No fold: the inverse sample map must have a positive Jacobian."""
    shape = fit_face_shape(LANDMARKS)
    assert shape is not None
    peak = shape.slim_peak_px
    step = 1.0
    e = 0.05
    worst = None
    for i in range(70):
        y = shape.cy + 0.10 * shape.b + 0.95 * shape.b * i / 69.0
        for j in range(70):
            x = shape.cx - 1.25 * shape.a + 2.5 * shape.a * j / 69.0
            d0 = _slim_offset(shape, x, y, peak=peak, slim=1.0)
            dxs = _slim_offset(shape, x + e, y, peak=peak, slim=1.0)
            dys = _slim_offset(shape, x, y + e, peak=peak, slim=1.0)
            jxx = 1.0 + (dxs[0] - d0[0]) / e
            jxy = (dys[0] - d0[0]) / e
            jyx = (dxs[1] - d0[1]) / e
            jyy = 1.0 + (dys[1] - d0[1]) / e
            det = jxx * jyy - jxy * jyx
            if worst is None or det < worst[0]:
                worst = (det, x, y)
    assert worst is not None
    assert worst[0] > 0.2, worst
    _ = step


def test_slim_profile_preserves_midface_and_focuses_on_outer_jaw() -> None:
    shape = fit_face_shape(LANDMARKS)
    assert shape is not None
    peak = shape.slim_peak_px
    # The nose/mouth column sits deep inside the oval and must stay put: the
    # reported "features squeezed inward" came from pushing the midface.
    nose = (shape.cx + 0.06 * shape.a, shape.cy + 0.24 * shape.b)
    mouth = (shape.cx, shape.cy + 0.63 * shape.b)
    assert _slim_mag(shape, nose[0], nose[1], peak=peak, slim=1.0) < 0.05 * peak
    assert _slim_mag(shape, mouth[0], mouth[1], peak=peak, slim=1.0) < 0.30 * peak
    # The visible cheek, at the same height as the mouth, does the work.
    y = shape.cy + 0.55 * shape.b
    a_y = shape.a * math.sqrt(max(1.0 - ((y - shape.cy) / shape.b) ** 2, 1e-6))
    outer_cheek = _slim_mag(shape, shape.cx + 0.75 * a_y, y, peak=peak, slim=1.0)
    assert outer_cheek > 0.50 * peak


def test_small_face_tip_stays_bounded_and_fold_free() -> None:
    small = FaceShape(cx=0.0, cy=0.0, a=20.0, b=25.0, eye_y=-5.0, d=22.0)
    peak = small.slim_peak_px
    e = 0.05
    for i in range(20):
        y = small.cy + 0.5 * small.b + 0.55 * small.b * i / 19.0
        for j in range(20):
            x = small.cx - 1.1 * small.a + 2.2 * small.a * j / 19.0
            assert _slim_mag(small, x, y, peak=peak, slim=1.0) <= peak + 1e-6
            d0 = _slim_offset(small, x, y, peak=peak, slim=1.0)
            dxs = _slim_offset(small, x + e, y, peak=peak, slim=1.0)
            dys = _slim_offset(small, x, y + e, peak=peak, slim=1.0)
            det = (1.0 + (dxs[0] - d0[0]) / e) * (1.0 + (dys[1] - d0[1]) / e)
            det -= (dys[0] - d0[0]) / e * (dxs[1] - d0[1]) / e
            assert det > 0.2


def test_slim_offset_moves_both_cheeks_inwards() -> None:
    shape = fit_face_shape(LANDMARKS)
    assert shape is not None
    y = shape.cy + 0.55 * shape.b
    left = _slim_offset(shape, shape.cx - 0.7 * shape.a, y, peak=20.0, slim=1.0)
    right = _slim_offset(shape, shape.cx + 0.7 * shape.a, y, peak=20.0, slim=1.0)
    # Positive offset = sample further out = the visible content moves inwards.
    assert left[0] < 0.0 and right[0] > 0.0
    assert left[0] == pytest.approx(-right[0])
    assert left[1] == pytest.approx(right[1])


def test_slim_offset_leaves_the_eye_line_alone() -> None:
    shape = fit_face_shape(LANDMARKS)
    assert shape is not None
    for dy in (-0.2, -0.02, 0.0):
        y = shape.eye_y + dy * shape.d
        point = (shape.cx + 0.6 * shape.a, y)
        assert _slim_mag(shape, point[0], point[1], peak=20.0, slim=1.0) < 0.05


def test_slim_offset_never_exceeds_the_peak() -> None:
    shape = fit_face_shape(LANDMARKS)
    assert shape is not None
    peak = shape.slim_peak_px
    worst = 0.0
    for i in range(120):
        y = shape.cy - shape.b + (2.0 * shape.b) * i / 119.0
        for j in range(120):
            x = shape.cx - 1.1 * shape.a + (2.2 * shape.a) * j / 119.0
            worst = max(worst, _slim_mag(shape, x, y, peak=peak, slim=1.0))
    assert worst <= peak + 1e-6
    # ...and the effect is actually there: a few pixels at the cheeks.
    assert worst > 0.5 * peak


def test_slim_offset_has_no_cliff_at_the_face_edge() -> None:
    """A horizontal-only field sheared 5.5 px/px near the chin; keep it smooth."""
    shape = fit_face_shape(LANDMARKS)
    assert shape is not None
    peak = shape.slim_peak_px
    jump = 0.0
    for row in (0.55, 0.75, 0.90, 0.97, 0.995):
        y = shape.cy + row * shape.b
        previous = None
        x = shape.cx - 1.3 * shape.a
        while x <= shape.cx + 1.3 * shape.a:
            value = _slim_mag(shape, x, y, peak=peak, slim=1.0)
            if previous is not None:
                jump = max(jump, abs(value - previous))
            previous = value
            x += 1.0
    # Same probe down the chin centre: the old field changed ~4 px per row there.
    previous = None
    y = shape.cy + 0.60 * shape.b
    while y <= shape.cy + 1.08 * shape.b:
        value = _slim_mag(shape, shape.cx, y, peak=peak, slim=1.0)
        if previous is not None:
            jump = max(jump, abs(value - previous))
        previous = value
        y += 1.0
    assert jump < 1.2, jump
