"""Geometry of the M5 experimental warp (face slim + eye enlarge).

The warp is a backwards UV warp in the single beauty shader pass: for every
output pixel the shader samples somewhere else.  Since M5 review v2 the warp is
evaluated in *display* space (texture UV -> display UV -> warp -> texture UV),
so the geometry stays upright whatever the stream's rotation metadata says.

These tests pin the Python half of the contract -- the display-space uniform
geometry -- and validate a NumPy-free scalar reference implementation of the
fragment shader's ``warp_uv`` against the invariants that make the effect look
natural:

* identity when off or not ready,
* the eye centre never moves and the eye ellipse has a continuous edge,
* the slim warp is a *global* lower-face compression: everything above the eye
  line and the centre column stay put, the whole outer cheek/jawline moves
  towards the centre line (no single dent around a jaw control point),
* the result is always a valid UV and the warp never folds the texture.

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
#
# ``point_to_texture`` is still part of the public geometry helpers (used by the
# overlay/recording paths), even though the warp itself no longer needs it: the
# shader does the texture<->display mapping on the GPU.


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
    # The box centre and the five landmarks are normalised display UVs; the
    # shader rotates the texture UV into this space itself.
    assert values["u_warp_cx"] == pytest.approx(350.0 / 1000.0)
    assert values["u_warp_cy"] == pytest.approx(350.0 / 1000.0)
    assert values["u_warp_face_w"] == pytest.approx(0.300)
    assert values["u_warp_face_h"] == pytest.approx(0.300)
    # Right/left eye centres (YuNet landmark 0/1).
    assert values["u_warp_lx0"] == pytest.approx(0.280)
    assert values["u_warp_ly0"] == pytest.approx(0.300)
    assert values["u_warp_lx1"] == pytest.approx(0.420)
    assert values["u_warp_ly1"] == pytest.approx(0.300)
    assert values["u_warp_rot"] == 0.0


@pytest.mark.parametrize("rotation", [90, 180, 270])
def test_warp_uniforms_keeps_the_geometry_upright_for_any_rotation(
    rotation: int,
) -> None:
    """Only ``u_warp_rot`` changes: the shader does the rotation, not Python.

    Keeping the detector's upright numbers here is what makes the slim warp
    compress the *visual* jawline even for a video whose stored pixels are
    upside down (the phone selfie clip has display matrix -180).
    """
    baseline = warp_uniforms(LANDMARKS, FACE_BOX, FRAME)
    values = warp_uniforms(LANDMARKS, FACE_BOX, FRAME, rotation=rotation)

    for key in WARP_UNIFORM_KEYS:
        if key == "u_warp_rot":
            assert values[key] == pytest.approx(float(rotation))
        else:
            assert values[key] == pytest.approx(baseline[key]), key


def test_warp_uniforms_clamps_the_geometry_to_the_unit_square() -> None:
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
        if key == "u_warp_rot":
            continue
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


# -- shader contract --------------------------------------------------------


def test_warp_uniform_keys_match_the_shader_declaration() -> None:
    declared = set(re.findall(r"uniform float (\w+);", BEAUTY_FRAGMENT_SHADER))
    assert set(WARP_UNIFORM_KEYS) <= declared
    assert {"u_slim", "u_eyes"} <= declared
    # One declaration per key -- glshader rejects duplicates at link time.
    for key in WARP_UNIFORM_KEYS + ("u_slim", "u_eyes"):
        assert len(re.findall(rf"uniform float {key};", BEAUTY_FRAGMENT_SHADER)) == 1


def test_warp_shader_only_gates_on_the_primary_face() -> None:
    warp_uv = re.search(
        r"vec2 warp_uv\(vec2 texcoord\) \{.*?\n\}", BEAUTY_FRAGMENT_SHADER, re.DOTALL
    )
    assert warp_uv is not None
    block = warp_uv.group(0)
    assert "u_face0_x" in block
    assert "u_face1_x" not in block
    assert "u_face2_x" not in block
    # The per-pixel skin gate made the displacement blotchy (M5 review v2):
    # the warp gate must be the face box only.
    assert "skin_gate" not in block
    warp_gate = re.search(
        r"float warp_gate\(.*?\n\}", BEAUTY_FRAGMENT_SHADER, re.DOTALL
    )
    assert warp_gate is not None
    assert "skin_gate" not in warp_gate.group(0)
    assert "u_expand" not in warp_gate.group(0)


def test_warp_shader_contract() -> None:
    # The mask is measured at the unwarped position and only the colour sample
    # is warped; compare mode samples the untouched texture on the left half.
    assert (
        "vec2 uv = u_active > 0.5 ? warp_uv(v_texcoord) : v_texcoord;"
        in BEAUTY_FRAGMENT_SHADER
    )
    assert "vec2 px = v_texcoord * vec2(u_full_w, u_full_h);" in BEAUTY_FRAGMENT_SHADER
    assert "texture2D(tex, v_texcoord).rgb" in BEAUTY_FRAGMENT_SHADER
    # The warp is evaluated in display space and clamped at both ends.
    assert "vec2 disp = tex_to_display(texcoord);" in BEAUTY_FRAGMENT_SHADER
    assert (
        "vec2 warped = clamp(disp + offset * gate, vec2(0.0), vec2(1.0));"
        in BEAUTY_FRAGMENT_SHADER
    )
    assert (
        "return clamp(display_to_tex(warped), vec2(0.0), vec2(1.0));"
        in BEAUTY_FRAGMENT_SHADER
    )
    # No sampling with an unclamped UV.
    assert "texture2D(tex, uv)" in BEAUTY_FRAGMENT_SHADER


def test_warp_shader_rotation_maps_are_inverses() -> None:
    tex_to_display = re.search(
        r"vec2 tex_to_display\(vec2 uv\) \{.*?\n\}", BEAUTY_FRAGMENT_SHADER, re.DOTALL
    )
    display_to_tex = re.search(
        r"vec2 display_to_tex\(vec2 uv\) \{.*?\n\}", BEAUTY_FRAGMENT_SHADER, re.DOTALL
    )
    assert tex_to_display is not None and display_to_tex is not None
    forward = tex_to_display.group(0)
    backward = display_to_tex.group(0)
    # Every texture->display branch has its inverse in display->texture.
    assert "vec2(1.0 - uv.y, uv.x)" in forward
    assert "vec2(uv.y, 1.0 - uv.x)" in backward
    assert "vec2(1.0 - uv.x, 1.0 - uv.y)" in forward
    assert "vec2(1.0 - uv.x, 1.0 - uv.y)" in backward
    assert "vec2(uv.y, 1.0 - uv.x)" in forward
    assert "vec2(1.0 - uv.y, uv.x)" in backward


# -- display-space reference implementation --------------------------------
#
# A faithful scalar translation of the shader's warp_uv.  It documents the
# intended behaviour and lets the desktop validate the maths without a GPU.
# Keep the constants in sync with BEAUTY_FRAGMENT_SHADER.

SLIM_STRENGTH = 0.16
EYE_MAGNIFY = 0.22
EYE_RADIUS = 0.16  # fraction of the face box width
EYE_ASPECT = 0.72  # vertical / horizontal radius


def _smoothstep(edge0: float, edge1: float, x: float) -> float:
    if edge1 <= edge0:
        return 0.0 if x < edge0 else 1.0
    t = max(0.0, min(1.0, (x - edge0) / (edge1 - edge0)))
    return t * t * (3.0 - 2.0 * t)


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))


def tex_to_display_reference(
    uv: tuple[float, float], rotation: int
) -> tuple[float, float]:
    rotation %= 360
    if rotation == 90:
        return (1.0 - uv[1], uv[0])
    if rotation == 180:
        return (1.0 - uv[0], 1.0 - uv[1])
    if rotation == 270:
        return (uv[1], 1.0 - uv[0])
    return uv


def display_to_tex_reference(
    uv: tuple[float, float], rotation: int
) -> tuple[float, float]:
    rotation %= 360
    if rotation == 90:
        return (uv[1], 1.0 - uv[0])
    if rotation == 180:
        return (1.0 - uv[0], 1.0 - uv[1])
    if rotation == 270:
        return (1.0 - uv[1], uv[0])
    return uv


def warp_uv_reference(
    uv: tuple[float, float],
    *,
    ready: bool,
    centre: tuple[float, float],
    face_size: tuple[float, float],
    eyes: tuple[tuple[float, float], ...],
    rotation: int = 0,
    slim: float = 0.0,
    eye_enlarge: float = 0.0,
    gate: float = 1.0,
) -> tuple[float, float]:
    """Reference ``warp_uv``: returns the UV the shader should sample."""
    if (
        not ready
        or gate <= 0.0
        or (slim <= 0.0 and eye_enlarge <= 0.0)
    ):
        return uv
    disp = tex_to_display_reference(uv, rotation)
    offset_x = 0.0
    offset_y = 0.0
    if slim > 0.0:
        half_w = max(face_size[0] * 0.5, 1e-4)
        half_h = max(face_size[1] * 0.5, 1e-4)
        rel_x = (disp[0] - centre[0]) / half_w
        rel_y = (disp[1] - centre[1]) / half_h
        vertical = _smoothstep(-0.05, 0.30, rel_y)
        horizontal = _smoothstep(0.15, 0.95, abs(rel_x))
        offset_x += (
            (disp[0] - centre[0]) * (slim * SLIM_STRENGTH * vertical * horizontal)
        )
    if eye_enlarge > 0.0:
        radius_x = max(EYE_RADIUS * face_size[0], 1e-4)
        radius_y = max(radius_x * EYE_ASPECT, 1e-4)
        for eye_x, eye_y in eyes:
            delta_x = disp[0] - eye_x
            delta_y = disp[1] - eye_y
            radius = math.hypot(delta_x / radius_x, delta_y / radius_y)
            falloff = 1.0 - _smoothstep(0.20, 1.0, radius)
            offset_x -= delta_x * (EYE_MAGNIFY * eye_enlarge * falloff)
            offset_y -= delta_y * (EYE_MAGNIFY * eye_enlarge * falloff)
    warped = (
        _clamp01(disp[0] + offset_x * gate),
        _clamp01(disp[1] + offset_y * gate),
    )
    result = display_to_tex_reference(warped, rotation)
    return (_clamp01(result[0]), _clamp01(result[1]))


def _reference_geometry() -> dict[str, object]:
    values = warp_uniforms(LANDMARKS, FACE_BOX, FRAME)
    return {
        "ready": values["u_warp_ready"] > 0.5,
        "centre": (values["u_warp_cx"], values["u_warp_cy"]),
        "face_size": (values["u_warp_face_w"], values["u_warp_face_h"]),
        "eyes": (
            (values["u_warp_lx0"], values["u_warp_ly0"]),
            (values["u_warp_lx1"], values["u_warp_ly1"]),
        ),
        "rotation": int(values["u_warp_rot"]),
    }


def test_warp_reference_is_identity_when_disabled() -> None:
    geometry = _reference_geometry()
    for uv in ((0.5, 0.5), (0.3, 0.3), (0.7, 0.6)):
        assert warp_uv_reference(uv, **geometry) == pytest.approx(uv)
        assert warp_uv_reference(
            uv, **{**geometry, "ready": False}, slim=1.0, eye_enlarge=1.0
        ) == pytest.approx(uv)
        assert warp_uv_reference(
            uv, **geometry, slim=1.0, eye_enlarge=1.0, gate=0.0
        ) == pytest.approx(uv)


def test_warp_reference_eye_centre_never_moves() -> None:
    geometry = _reference_geometry()
    eyes = geometry["eyes"]
    assert isinstance(eyes, tuple)
    for eye in eyes:
        assert warp_uv_reference(
            eye, **geometry, eye_enlarge=1.0
        ) == pytest.approx(eye)


def test_warp_reference_leaves_the_eye_ellipse_edge_untouched() -> None:
    geometry = _reference_geometry()
    eyes = geometry["eyes"]
    face_size = geometry["face_size"]
    assert isinstance(eyes, tuple) and isinstance(face_size, tuple)
    radius_x = EYE_RADIUS * face_size[0]
    radius_y = radius_x * EYE_ASPECT
    for eye in eyes:
        for offset in (
            (radius_x * 1.15, 0.0),
            (0.0, radius_y * 1.15),
            (-radius_x * 1.15, 0.0),
        ):
            outside = (eye[0] + offset[0], eye[1] + offset[1])
            assert warp_uv_reference(
                outside, **geometry, eye_enlarge=1.0
            ) == pytest.approx(outside)


def test_warp_reference_magnifies_the_eye() -> None:
    geometry = _reference_geometry()
    eyes = geometry["eyes"]
    face_size = geometry["face_size"]
    assert isinstance(eyes, tuple) and isinstance(face_size, tuple)
    eye = eyes[0]
    radius_x = EYE_RADIUS * face_size[0]
    point = (eye[0] + 0.5 * radius_x, eye[1])

    warped = warp_uv_reference(point, **geometry, eye_enlarge=1.0)

    # The sample moves *towards* the eye centre: that magnifies the eye.
    assert abs(warped[0] - eye[0]) < abs(point[0] - eye[0])
    assert warped[1] == pytest.approx(point[1])


def test_warp_reference_eye_ellipse_is_wider_than_tall() -> None:
    geometry = _reference_geometry()
    eyes = geometry["eyes"]
    face_size = geometry["face_size"]
    assert isinstance(eyes, tuple) and isinstance(face_size, tuple)
    eye = eyes[0]
    radius_x = EYE_RADIUS * face_size[0]
    radius_y = radius_x * EYE_ASPECT

    # Half way to the edge is still inside the ellipse vertically (0.5 < 1),
    # so a vertical point is warped while one beyond the radius is not.
    vertical = (eye[0], eye[1] + 0.5 * radius_y)
    beyond = (eye[0], eye[1] + 1.1 * radius_y)
    assert warp_uv_reference(
        vertical, **geometry, eye_enlarge=1.0
    ) != pytest.approx(vertical)
    assert warp_uv_reference(
        beyond, **geometry, eye_enlarge=1.0
    ) == pytest.approx(beyond)


def test_warp_reference_narrows_the_whole_lower_face() -> None:
    geometry = _reference_geometry()
    centre = geometry["centre"]
    assert isinstance(centre, tuple)
    centre_x, centre_y = centre

    # Two points on the same lower-face row, left and right of the centre line.
    row = centre_y + 0.36
    right_point = (centre_x + 0.24, row)
    left_point = (centre_x - 0.24, row)

    warped_right = warp_uv_reference(right_point, **geometry, slim=1.0)
    warped_left = warp_uv_reference(left_point, **geometry, slim=1.0)

    # Sampling further out on each side pulls the visible face edge inwards.
    assert warped_right[0] > right_point[0]
    assert warped_left[0] < left_point[0]
    assert warped_right[1] == pytest.approx(right_point[1])
    assert warped_left[1] == pytest.approx(left_point[1])


def test_warp_reference_slim_keeps_the_eyes_and_centre_column_still() -> None:
    geometry = _reference_geometry()
    centre = geometry["centre"]
    assert isinstance(centre, tuple)
    centre_x, centre_y = centre
    # above the eye line (same y as the landmarks): vertical weight is 0
    above_eye = (centre_x + 0.22, 0.30)
    centre_column = (centre_x, centre_y + 0.36)
    assert warp_uv_reference(above_eye, **geometry, slim=1.0) == pytest.approx(
        above_eye
    )
    assert warp_uv_reference(centre_column, **geometry, slim=1.0) == pytest.approx(
        centre_column
    )


def test_warp_reference_slim_grows_towards_the_jawline() -> None:
    geometry = _reference_geometry()
    centre = geometry["centre"]
    assert isinstance(centre, tuple)
    centre_x, centre_y = centre
    row = centre_y + 0.36

    inner = (centre_x + 0.10, row)
    outer = (centre_x + 0.22, row)
    inner_shift = warp_uv_reference(inner, **geometry, slim=1.0)
    outer_shift = warp_uv_reference(outer, **geometry, slim=1.0)

    assert (inner_shift[0] - inner[0]) < (outer_shift[0] - outer[0])


def test_warp_reference_gate_scales_the_displacement() -> None:
    geometry = _reference_geometry()
    centre = geometry["centre"]
    assert isinstance(centre, tuple)
    point = (centre[0] + 0.24, centre[1] + 0.36)
    full = warp_uv_reference(point, **geometry, slim=1.0, gate=1.0)
    half = warp_uv_reference(point, **geometry, slim=1.0, gate=0.5)
    expected = (point[0] + (full[0] - point[0]) * 0.5, point[1])
    assert half == pytest.approx(expected)


def test_warp_reference_maps_rotation_back_to_texture_space() -> None:
    """A 180-rotated texture must be warped on the upright jawline."""
    geometry = _reference_geometry()
    centre = geometry["centre"]
    assert isinstance(centre, tuple)
    # Display point on the right cheek; its texture UV is the 180-rotated one.
    display_point = (centre[0] + 0.24, centre[1] + 0.36)
    texture_point = display_to_tex_reference(display_point, 180)

    warped_display = warp_uv_reference(
        display_point, **{**geometry, "rotation": 0}, slim=1.0
    )
    expected_texture = display_to_tex_reference(warped_display, 180)
    warped_texture = warp_uv_reference(
        texture_point, **{**geometry, "rotation": 180}, slim=1.0
    )

    assert warped_texture == pytest.approx(expected_texture)
    assert warped_texture != pytest.approx(texture_point)


@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
def test_warp_reference_rotation_maps_are_inverses(rotation: int) -> None:
    for uv in ((0.25, 0.75), (0.0, 0.0), (1.0, 1.0), (0.4, 0.1)):
        display = tex_to_display_reference(uv, rotation)
        assert display_to_tex_reference(display, rotation) == pytest.approx(uv)


def test_warp_reference_never_leaves_the_texture() -> None:
    geometry = _reference_geometry()
    for rotation in (0, 90, 180, 270):
        for point in ((0.0, 0.0), (1.0, 1.0), (0.0, 0.5), (1.0, 0.5), (0.5, 0.0)):
            warped = warp_uv_reference(
                point,
                **{**geometry, "rotation": rotation},
                slim=1.0,
                eye_enlarge=1.0,
            )
            assert 0.0 <= warped[0] <= 1.0
            assert 0.0 <= warped[1] <= 1.0


def test_warp_reference_does_not_fold_the_lower_face() -> None:
    np = pytest.importorskip("numpy")
    geometry = _reference_geometry()
    centre = geometry["centre"]
    assert isinstance(centre, tuple)
    row = centre[1] + 0.36
    xs = np.linspace(centre[0] - 0.30, centre[0] + 0.30, 121)
    warped_x = np.array(
        [
            warp_uv_reference(
                (float(x), row), **geometry, slim=1.0, eye_enlarge=0.0
            )[0]
            for x in xs
        ]
    )
    # A positive step in the output coordinate must still map to a positive
    # step in the source coordinate; otherwise the texture "tears" (M5 test 4).
    assert np.all(np.diff(warped_x) > 0.0)
    assert np.all(warped_x <= 1.0) and np.all(warped_x >= 0.0)
