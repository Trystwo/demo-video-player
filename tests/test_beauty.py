"""Tests for beauty parameters, shaders and face-mask rasterisation."""

from __future__ import annotations

import pytest

from video_player.beauty import (
    BEAUTY_FRAGMENT_SHADER,
    BEAUTY_VERTEX_SHADER,
    BeautyParams,
    build_face_mask,
)
from video_player.config import BeautyConfig
from video_player.face import Face


def test_params_normalise_from_percent_config() -> None:
    params = BeautyParams.from_config(BeautyConfig(smoothing=60, whitening=40))
    assert params.smoothing == pytest.approx(0.6)
    assert params.whitening == pytest.approx(0.4)
    assert params.uniforms() == {"u_smoothing": 0.6, "u_whitening": 0.4}


def test_params_clamp_out_of_range_values() -> None:
    params = BeautyParams.from_config(BeautyConfig(smoothing=500, whitening=-10))
    assert params.smoothing == 1.0
    assert params.whitening == 0.0


def test_identity_detection() -> None:
    assert BeautyParams(smoothing=0.0, whitening=0.0).is_identity()
    assert not BeautyParams(smoothing=0.0, whitening=0.1).is_identity()


def test_shaders_declare_expected_uniforms_and_attributes() -> None:
    assert "a_position" in BEAUTY_VERTEX_SHADER
    assert "a_texcoord" in BEAUTY_VERTEX_SHADER
    for uniform in ("u_tex", "u_mask", "u_texel_size", "u_smoothing", "u_whitening"):
        assert uniform in BEAUTY_FRAGMENT_SHADER
    assert "gl_FragColor" in BEAUTY_FRAGMENT_SHADER


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
    # The mask is monotonic towards the centre along a row.
    row = mask[16, 8:24]
    assert np.all(np.diff(row) >= -1e-6)


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
