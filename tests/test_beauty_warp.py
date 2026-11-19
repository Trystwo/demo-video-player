"""Geometry of the M5 experimental warp (face slim + eye enlarge).

The warp is a backwards UV warp in the single beauty shader pass: for every
output pixel the shader samples somewhere else.  These tests pin the Python
half of the contract -- the display->texture mapping and the uniform geometry --
and validate a NumPy reference implementation of the fragment shader's
``warp_uv`` against the invariants that matter visually: identity when off,
the eye centre never moves, nothing outside the radii moves, and the result is
always a valid UV (the shader can only sample inside the texture).

The GLSL itself is exercised on the board (``beauty_ms``, fps, no texture
bounce); PyGObject cannot compile it on the desktop CI box.
"""

from __future__ import annotations

import math
import re

import pytest

from video_player.beauty import (
    BEAUTY_FRAGMENT_SHADER,
    WARP_UNIFORM_KEYS,
    BeautyParams,
    face_rect_to_texture,
    point_to_texture,
    warp_uniforms,
)
from video_player.config import BeautyConfig
from video_player.face import Face

FRAME = (1000, 1000)
#: YuNet order: right eye, left eye, nose, right mouth corner, left mouth corner.
LANDMARKS = (
    (280.0, 300.0),  # right eye (image left)
    (420.0, 300.0),  # left eye (image right)
    (350.0, 390.0),  # nose
    (300.0, 460.0),  # right mouth corner
    (400.0, 460.0),  # left mouth corner
)
FACE_BOX = (200.0, 200.0, 300.0, 300.0)


# -- point_to_texture -------------------------------------------------------


@pytest.mark.parametrize(
    ("rotation", "expected"),
    [
        (0, (0.1, 0.4)),
        (90, (0.4, 0.9)),
        (180, (0.9, 0.6)),
        (270, (0.6, 0.1)),
    ],
)
def test_point_to_texture_maps_the_four_rotations(
    rotation: int, expected: tuple[float, float]
) -> None:
    assert point_to_texture(100.0, 400.0, 1000.0, 1000.0, rotation) == pytest.approx(
        expected
    )


def test_point_to_texture_agrees_with_face_rect_to_texture() -> None:
    """The two mapping helpers must describe the same rotation."""
    rect = (0.1, 0.2, 0.3, 0.4)
    for rotation in (0, 90, 180, 270):
        x, y, width, height = rect
        left, top = point_to_texture(
            x * FRAME[0], y * FRAME[1], FRAME[0], FRAME[1], rotation
        )
        right, bottom = point_to_texture(
            (x + width) * FRAME[0],
            (y + height) * FRAME[1],
            FRAME[0],
            FRAME[1],
            rotation,
        )
        expected = face_rect_to_texture(rect, rotation)
        assert min(left, right) == pytest.approx(expected[0])
        assert min(top, bottom) == pytest.approx(expected[1])
        assert abs(right - left) == pytest.approx(expected[2])
        assert abs(bottom - top) == pytest.approx(expected[3])


def test_point_to_texture_rejects_an_empty_frame() -> None:
    assert point_to_texture(10.0, 20.0, 0, 0) == (0.0, 0.0)


# -- warp_uniforms ----------------------------------------------------------


def test_warp_uniforms_without_five_landmarks_is_disabled() -> None:
    for landmarks in (None, (), LANDMARKS[:4], LANDMARKS + ((1.0, 2.0),)):
        values = warp_uniforms(landmarks, FACE_BOX, FRAME)
        assert set(values) == set(WARP_UNIFORM_KEYS)
        assert values["u_warp_ready"] == 0.0
        assert all(
            value == 0.0 for key, value in values.items() if key != "u_warp_ready"
        )


def test_warp_uniforms_disables_malformed_geometry() -> None:
    assert warp_uniforms(LANDMARKS, (10.0, 20.0, 30.0), FRAME)["u_warp_ready"] == 0.0
    assert warp_uniforms(LANDMARKS, None, FRAME)["u_warp_ready"] == 0.0
    assert warp_uniforms(LANDMARKS, FACE_BOX, (0, 0))["u_warp_ready"] == 0.0
    assert (
        warp_uniforms([("x", "y")] * 5, FACE_BOX, FRAME)["u_warp_ready"] == 0.0
    )


def test_warp_uniforms_places_the_geometry() -> None:
    values = warp_uniforms(LANDMARKS, FACE_BOX, FRAME)

    assert values["u_warp_ready"] == 1.0
    assert values["u_warp_cx"] == pytest.approx(350.0 / 1000.0)
    assert values["u_warp_cy"] == pytest.approx(350.0 / 1000.0)
    assert values["u_warp_lx0"] == pytest.approx(0.280)
    assert values["u_warp_ly0"] == pytest.approx(0.300)
    assert values["u_warp_lx1"] == pytest.approx(0.420)
    assert values["u_warp_ly1"] == pytest.approx(0.300)
    # Jaw control points: eye<->mouth-corner midpoint, pushed 0.18 box widths
    # outwards (away from the face centre) and 0.30 box heights down.
    assert values["u_warp_jx0"] == pytest.approx((290.0 - 54.0) / 1000.0)
    assert values["u_warp_jy0"] == pytest.approx((380.0 + 90.0) / 1000.0)
    assert values["u_warp_jx1"] == pytest.approx((410.0 + 54.0) / 1000.0)
    assert values["u_warp_jy1"] == pytest.approx((380.0 + 90.0) / 1000.0)
    # Radii are relative to the face width in texture UV units.
    assert values["u_warp_face_w"] == pytest.approx(0.300)
    assert values["u_warp_eye_r"] == pytest.approx(0.16 * 0.300)
    assert values["u_warp_slim_r"] == pytest.approx(0.26 * 0.300)


def test_warp_uniforms_maps_the_geometry_back_for_rotation() -> None:
    values = warp_uniforms(LANDMARKS, FACE_BOX, FRAME, rotation=180)

    assert values["u_warp_cx"] == pytest.approx(1.0 - 0.350)
    assert values["u_warp_cy"] == pytest.approx(1.0 - 0.350)
    assert values["u_warp_lx0"] == pytest.approx(1.0 - 0.280)
    assert values["u_warp_ly0"] == pytest.approx(1.0 - 0.300)


def test_warp_uniforms_clamps_everything_to_the_unit_square() -> None:
    values = warp_uniforms(
        (
            (-500.0, -400.0),
            (2500.0, -400.0),
            (1200.0, 6000.0),
            (-800.0, 2200.0),
            (3000.0, 2200.0),
        ),
        (-200.0, -300.0, 2000.0, 1400.0),
        FRAME,
    )
    for key in WARP_UNIFORM_KEYS:
        assert 0.0 <= values[key] <= 1.0, key


# -- uniforms() integration -------------------------------------------------


def test_uniforms_carry_warp_strengths_and_geometry() -> None:
    face = Face(x=200.0, y=200.0, width=300.0, height=300.0, landmarks=LANDMARKS)
    params = BeautyParams(face_slim=0.4, eye_enlarge=0.3)
    values = params.uniforms(frame_size=FRAME, faces=[face])

    assert values["u_slim"] == pytest.approx(0.4)
    assert values["u_eyes"] == pytest.approx(0.3)
    assert values["u_warp_ready"] == 1.0
    for key in WARP_UNIFORM_KEYS:
        assert key in values


def test_warp_strengths_take_part_in_identity_detection() -> None:
    assert BeautyParams(0.0, 0.0, 0.0, 0.0).is_identity()
    assert not BeautyParams(0.0, 0.0, 0.0, 0.1).is_identity()
    assert not BeautyParams(0.0, 0.0, 0.1, 0.0).is_identity()

    params = BeautyParams.from_config(BeautyConfig(eye_enlarge=25))
    assert params.eye_enlarge == pytest.approx(0.25)
    assert params.uniforms()["u_active"] == 1.0


def test_warp_uniform_keys_match_the_shader_declaration() -> None:
    declared = set(re.findall(r"uniform float (\w+);", BEAUTY_FRAGMENT_SHADER))
    assert set(WARP_UNIFORM_KEYS) <= declared
    assert {"u_slim", "u_eyes"} <= declared
    # One declaration per key -- glshader rejects duplicates at link time.
    for key in WARP_UNIFORM_KEYS + ("u_slim", "u_eyes"):
        assert len(re.findall(rf"uniform float {key};", BEAUTY_FRAGMENT_SHADER)) == 1


def test_warp_shader_only_gates_on_the_primary_face() -> None:
    warp_gate = re.search(
        r"float warp_gate\(.*?\n\}", BEAUTY_FRAGMENT_SHADER, re.DOTALL
    )
    assert warp_gate is not None
    block = warp_gate.group(0)
    assert "u_face0_x" in block
    assert "u_face1_x" not in block
    assert "u_face2_x" not in block


def test_warp_shader_contract() -> None:
    # The mask is measured at the unwarped position and only the colour sample
    # is warped; compare mode samples the untouched texture on the left half.
    assert (
        "vec2 uv = u_active > 0.5 ? warp_uv(v_texcoord) : v_texcoord;"
        in BEAUTY_FRAGMENT_SHADER
    )
    assert "vec2 px = v_texcoord * vec2(u_full_w, u_full_h);" in BEAUTY_FRAGMENT_SHADER
    assert "texture2D(tex, v_texcoord).rgb" in BEAUTY_FRAGMENT_SHADER
    assert "clamp(uv + offset * gate, vec2(0.0), vec2(1.0))" in BEAUTY_FRAGMENT_SHADER
    # No sampling with an unclamped UV.
    assert "texture2D(tex, uv)" in BEAUTY_FRAGMENT_SHADER


# -- NumPy reference implementation ----------------------------------------
#
# A faithful scalar/vector translation of the shader's warp_uv.  It documents
# the intended behaviour and lets the desktop (with NumPy) validate the maths
# without a GPU.  Keep the constants in sync with BEAUTY_FRAGMENT_SHADER.

EYE_MAGNIFY = 0.40
SLIM_PUSH = 0.065


def _falloff(delta_x: float, delta_y: float, radius: float) -> float:
    distance = math.hypot(delta_x, delta_y)
    radius = max(radius, 1e-4)
    if distance >= radius:
        return 0.0
    if distance <= 0.0:
        return 1.0
    t = distance / radius
    smoothstep = t * t * (3.0 - 2.0 * t)
    return 1.0 - smoothstep


def warp_uv_reference(
    uv: tuple[float, float],
    *,
    ready: bool,
    centre: tuple[float, float],
    eyes: tuple[tuple[float, float], ...],
    jaws: tuple[tuple[float, float], ...],
    eye_radius: float,
    slim_radius: float,
    face_width: float,
    slim: float,
    eye_enlarge: float,
    gate: float = 1.0,
) -> tuple[float, float]:
    """Reference ``warp_uv``: returns the UV the shader should sample."""
    if not ready or gate <= 0.0:
        return uv
    offset_x = 0.0
    offset_y = 0.0
    if eye_enlarge > 0.0:
        for eye_x, eye_y in eyes:
            delta_x = uv[0] - eye_x
            delta_y = uv[1] - eye_y
            falloff = _falloff(delta_x, delta_y, eye_radius)
            offset_x -= delta_x * (EYE_MAGNIFY * eye_enlarge * falloff)
            offset_y -= delta_y * (EYE_MAGNIFY * eye_enlarge * falloff)
    if slim > 0.0:
        for jaw_x, jaw_y in jaws:
            side = -1.0 if jaw_x < centre[0] else 1.0
            falloff = _falloff(uv[0] - jaw_x, uv[1] - jaw_y, slim_radius)
            offset_x += side * (SLIM_PUSH * face_width * slim * falloff)
    return (
        min(1.0, max(0.0, uv[0] + offset_x * gate)),
        min(1.0, max(0.0, uv[1] + offset_y * gate)),
    )


def _reference_geometry() -> dict[str, object]:
    values = warp_uniforms(LANDMARKS, FACE_BOX, FRAME)
    return {
        "ready": values["u_warp_ready"] > 0.5,
        "centre": (values["u_warp_cx"], values["u_warp_cy"]),
        "eyes": (
            (values["u_warp_lx0"], values["u_warp_ly0"]),
            (values["u_warp_lx1"], values["u_warp_ly1"]),
        ),
        "jaws": (
            (values["u_warp_jx0"], values["u_warp_jy0"]),
            (values["u_warp_jx1"], values["u_warp_jy1"]),
        ),
        "eye_radius": values["u_warp_eye_r"],
        "slim_radius": values["u_warp_slim_r"],
        "face_width": values["u_warp_face_w"],
    }


def test_warp_reference_is_identity_when_disabled() -> None:
    geometry = _reference_geometry()
    for uv in ((0.5, 0.5), (0.3, 0.3), (0.7, 0.6)):
        assert warp_uv_reference(
            uv, **geometry, slim=0.0, eye_enlarge=0.0
        ) == pytest.approx(uv)
        assert warp_uv_reference(
            uv, **{**geometry, "ready": False}, slim=1.0, eye_enlarge=1.0
        ) == pytest.approx(uv)


def test_warp_reference_eye_centre_never_moves() -> None:
    geometry = _reference_geometry()
    eye = geometry["eyes"][0]
    assert warp_uv_reference(
        eye, **geometry, slim=0.0, eye_enlarge=1.0
    ) == pytest.approx(eye)


def test_warp_reference_leaves_the_radius_untouched() -> None:
    geometry = _reference_geometry()
    for eye in geometry["eyes"]:
        outside = (eye[0] + geometry["eye_radius"] * 1.1, eye[1])
        assert warp_uv_reference(
            outside, **geometry, slim=0.0, eye_enlarge=1.0
        ) == pytest.approx(outside)


def test_warp_reference_magnifies_the_eye() -> None:
    geometry = _reference_geometry()
    eye = geometry["eyes"][0]
    radius = geometry["eye_radius"]
    point = (eye[0] + 0.5 * radius, eye[1])

    warped = warp_uv_reference(point, **geometry, slim=0.0, eye_enlarge=1.0)

    # The sample moves *towards* the eye centre: that magnifies the eye.
    assert abs(warped[0] - eye[0]) < abs(point[0] - eye[0])
    assert warped[1] == pytest.approx(point[1])


def test_warp_reference_narrows_the_jawline() -> None:
    geometry = _reference_geometry()
    centre_x = geometry["centre"][0]
    left_jaw = geometry["jaws"][0]  # landmark 0 side (image left)
    right_jaw = geometry["jaws"][1]
    assert left_jaw[0] < centre_x < right_jaw[0]

    right_point = (right_jaw[0] + 0.01, right_jaw[1])
    left_point = (left_jaw[0] - 0.01, left_jaw[1])

    warped_right = warp_uv_reference(
        right_point, **geometry, slim=1.0, eye_enlarge=0.0
    )
    warped_left = warp_uv_reference(
        left_point, **geometry, slim=1.0, eye_enlarge=0.0
    )

    # Sampling further out on each side pulls the face edge inwards.
    assert warped_right[0] > right_point[0]
    assert warped_left[0] < left_point[0]


def test_warp_reference_never_leaves_the_texture() -> None:
    geometry = _reference_geometry()
    for point in ((0.0, 0.0), (1.0, 1.0), (0.0, 0.5), (1.0, 0.5), (0.5, 0.0)):
        warped = warp_uv_reference(
            point, **geometry, slim=1.0, eye_enlarge=1.0
        )
        assert 0.0 <= warped[0] <= 1.0
        assert 0.0 <= warped[1] <= 1.0


def test_warp_reference_does_not_fold_the_jaw_horizontally() -> None:
    np = pytest.importorskip("numpy")
    geometry = _reference_geometry()
    xs = np.linspace(0.2, 0.8, 121)
    y = geometry["jaws"][0][1] + 0.02
    warped_x = np.array(
        [
            warp_uv_reference(
                (float(x), y), **geometry, slim=1.0, eye_enlarge=0.0
            )[0]
            for x in xs
        ]
    )
    # A positive step in the output coordinate must still map to a positive
    # step in the source coordinate; otherwise the texture "tears" (M5 test 4).
    assert np.all(np.diff(warped_x) > 0.0)
    assert np.all(warped_x <= 1.0) and np.all(warped_x >= 0.0)
