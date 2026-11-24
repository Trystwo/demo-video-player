"""Tests for beauty parameters, shaders and face-mask rasterisation."""

from __future__ import annotations

import pytest

from video_player.beauty import (
    BEAUTY_FRAGMENT_SHADER,
    BEAUTY_MAX_FACES,
    BeautyParams,
    build_face_mask,
    face_rect_to_texture,
    normalise_face_boxes,
)
from video_player.config import BeautyConfig
from video_player.face import Face


def test_params_normalise_from_percent_config() -> None:
    params = BeautyParams.from_config(BeautyConfig(smoothing=60, whitening=40))
    assert params.smoothing == pytest.approx(0.6)
    assert params.whitening == pytest.approx(0.4)
    uniforms = params.uniforms()
    assert uniforms["u_smoothing"] == pytest.approx(0.6)
    assert uniforms["u_whitening"] == pytest.approx(0.4)
    assert uniforms["u_active"] == 1.0


def test_uniform_values_are_all_floats_with_scalar_face_boxes() -> None:
    """glshader only binds scalar floats: no vectors, arrays or ints allowed."""
    uniforms = BeautyParams().uniforms(
        frame_size=(1280, 720), faces=[Face(x=100, y=80, width=200, height=240)]
    )
    assert all(isinstance(value, float) for value in uniforms.values())
    assert uniforms["u_face0_x"] == pytest.approx(100 / 1280)
    assert uniforms["u_face0_y"] == pytest.approx(80 / 720)
    assert uniforms["u_face0_w"] == pytest.approx(200 / 1280)
    assert uniforms["u_face0_h"] == pytest.approx(240 / 720)
    # Unused slots must be zeroed so the shader ignores them.
    for index in range(1, BEAUTY_MAX_FACES):
        assert uniforms[f"u_face{index}_w"] == 0.0


def test_uniforms_carry_compare_split_and_texel_size() -> None:
    uniforms = BeautyParams().uniforms(
        frame_size=(1280, 720),
        texture_size=(720, 1280),
        compare_mode=True,
        split=0.3,
        feather_px=20,
        active=False,
        debug=True,
        skin_guard=False,
    )
    assert uniforms["u_full_w"] == 720.0
    assert uniforms["u_full_h"] == 1280.0
    assert uniforms["u_texel_x"] == pytest.approx(1 / 720)
    assert uniforms["u_texel_y"] == pytest.approx(1 / 1280)
    assert uniforms["u_compare"] == 1.0
    assert uniforms["u_split"] == pytest.approx(0.3)
    assert uniforms["u_feather"] == pytest.approx(20.0)
    assert uniforms["u_active"] == 0.0
    assert uniforms["u_skin"] == 0.0
    assert uniforms["u_debug"] == 1.0


def test_no_faces_turn_every_face_box_off() -> None:
    uniforms = BeautyParams().uniforms(frame_size=(640, 360), faces=[])
    for index in range(BEAUTY_MAX_FACES):
        for component in ("x", "y", "w", "h"):
            assert uniforms[f"u_face{index}_{component}"] == 0.0


def test_face_boxes_are_clamped_to_the_shader_capacity() -> None:
    faces = [Face(x=10 * i, y=0, width=40, height=40) for i in range(5)]
    boxes = normalise_face_boxes(faces, 400, 400)
    assert len(boxes) == BEAUTY_MAX_FACES


def test_params_clamp_out_of_range_values() -> None:
    params = BeautyParams.from_config(BeautyConfig(smoothing=500, whitening=-10))
    assert params.smoothing == 1.0
    assert params.whitening == 0.0


def test_identity_detection() -> None:
    assert BeautyParams(smoothing=0.0, whitening=0.0).is_identity()
    assert not BeautyParams(smoothing=0.0, whitening=0.1).is_identity()
    # An identity shader is forced to pass through even when enabled.
    assert BeautyParams(0.0, 0.0).uniforms()["u_active"] == 0.0


def test_face_rect_to_texture_undoes_the_display_rotation() -> None:
    rect = (0.1, 0.2, 0.3, 0.4)
    assert face_rect_to_texture(rect, 0) == rect
    assert face_rect_to_texture(rect, 90) == pytest.approx((0.2, 0.6, 0.4, 0.3))
    assert face_rect_to_texture(rect, 180) == pytest.approx((0.6, 0.4, 0.3, 0.4))
    assert face_rect_to_texture(rect, 270) == pytest.approx((0.4, 0.1, 0.4, 0.3))


def test_normalise_face_boxes_rotate_for_the_selfie_clip() -> None:
    """The M2 selfie clip carries a rotate-180 display matrix."""
    boxes = normalise_face_boxes([(100, 100, 200, 200)], 1000, 500, rotation=180)
    assert boxes == [pytest.approx((1 - 0.3, 1 - 0.6, 0.2, 0.4))]


def test_normalise_face_boxes_rejects_degenerate_sizes() -> None:
    assert normalise_face_boxes([(0, 0, 10, 10)], 0, 0) == []
    assert normalise_face_boxes([(0, 0, 0, 10)], 100, 100) == []


def test_fragment_shader_declares_the_uniform_contract() -> None:
    # glshader injects no precision statement: without this line the shader
    # fails to compile on the board ("No precision specified ... for type vec2").
    assert "precision highp float;" in BEAUTY_FRAGMENT_SHADER
    assert "uniform sampler2D tex;" in BEAUTY_FRAGMENT_SHADER
    for uniform in (
        "u_smoothing",
        "u_whitening",
        "u_active",
        "u_compare",
        "u_split",
        "u_feather",
        "u_expand",
        "u_skin",
        "u_debug",
        "u_texel_x",
        "u_texel_y",
        "u_full_w",
        "u_full_h",
    ):
        assert f"uniform float {uniform};" in BEAUTY_FRAGMENT_SHADER
    for index in range(BEAUTY_MAX_FACES):
        for component in ("x", "y", "w", "h"):
            assert f"uniform float u_face{index}_{component};" in BEAUTY_FRAGMENT_SHADER
    assert "varying vec2 v_texcoord;" in BEAUTY_FRAGMENT_SHADER
    assert "gl_FragColor" in BEAUTY_FRAGMENT_SHADER


def test_every_uniform_the_shader_reads_is_provided() -> None:
    import re

    declared = {
        name
        for type_name, name in re.findall(
            r"uniform (\w+) (\w+);", BEAUTY_FRAGMENT_SHADER
        )
        if type_name != "sampler2D"
    }
    provided = set(BeautyParams().uniforms(frame_size=(640, 360)))
    assert declared == provided


def test_empty_mask_is_all_zero() -> None:
    np = pytest.importorskip("numpy")
    mask = build_face_mask([], width=32, height=24, feather_px=4)
    assert mask.shape == (24, 32)
    assert mask.dtype == np.float32
    assert float(mask.max()) == 0.0


def test_face_mask_is_full_inside_and_feathered_at_edges() -> None:
    np = pytest.importorskip("numpy")
    face = Face(x=8, y=8, width=16, height=16)
    mask = build_face_mask([face], width=32, height=32, feather_px=4)

    assert float(mask[16, 16]) == pytest.approx(1.0)
    # Outside the face rectangle stays untouched.
    assert float(mask[0, 0]) == 0.0
    assert float(mask[31, 31]) == 0.0
    # Two pixels inside the border the feather ramp is around 0.5.
    assert float(mask[10, 16]) == pytest.approx(0.5, abs=0.1)
    # The mask is monotonic towards the centre along a row: the feather ramps
    # up on the left border and down on the right one (the mask is symmetric
    # about the face centre, so a single left-to-right sweep is not monotonic).
    assert np.all(np.diff(mask[16, 8:17]) >= -1e-6)
    assert np.all(np.diff(mask[16, 16:24]) <= 1e-6)


def test_face_mask_accepts_plain_tuples_and_clips_to_frame() -> None:
    np = pytest.importorskip("numpy")
    mask = build_face_mask([(-10, -10, 20, 20)], width=16, height=16, feather_px=2)
    assert mask.shape == (16, 16)
    assert 0.0 <= float(mask.mean()) <= 1.0
    assert float(mask[0, 0]) >= 0.0


def test_face_mask_rejects_degenerate_frame_size() -> None:
    pytest.importorskip("numpy")
    with pytest.raises(ValueError, match="frame size"):
        build_face_mask([], width=0, height=10)
