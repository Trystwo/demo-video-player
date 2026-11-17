"""Tests for the GPU pre-scale that keeps GTK's paint scale above 0.5.

Measured on the board (docs/BOARD.md §5.2): a 1920 px texture painted into a
~940 px viewport (scale 0.49) makes GTK fall back to a CPU painting path that
costs ~55 % extra CPU and caps the sink at ~26 fps; pre-scaling the texture on
the GPU keeps the paint scale >= 0.5 and the CPU at the GPU-path floor.
"""

from __future__ import annotations

import pytest

from video_player.backend import scaled_texture_size


@pytest.mark.parametrize(
    ("size", "max_dimension", "expected"),
    [
        ((1920, 1080), 1280, (1280, 720)),
        ((3840, 2160), 1280, (1280, 720)),
        ((1080, 1920), 1280, (720, 1280)),
        ((1280, 720), 1280, (1280, 720)),
        ((720, 576), 1280, (720, 576)),
        ((854, 480), 1280, (854, 480)),
    ],
)
def test_scaled_texture_size_caps_the_long_side(
    size: tuple[int, int], max_dimension: int, expected: tuple[int, int]
) -> None:
    assert scaled_texture_size(*size, max_dimension) == expected


def test_scaled_texture_size_keeps_aspect_ratio_and_even_dimensions() -> None:
    width, height = scaled_texture_size(1918, 800, 1280)
    assert width % 2 == 0 and height % 2 == 0
    assert width == 1280
    assert abs(height / width - 800 / 1918) < 0.01


def test_scaled_texture_size_zero_disables_the_prescale() -> None:
    assert scaled_texture_size(1920, 1080, 0) == (1920, 1080)


def test_scaled_texture_size_ignores_unknown_sizes() -> None:
    assert scaled_texture_size(0, 0, 1280) == (0, 0)
    assert scaled_texture_size(1920, 0, 1280) == (1920, 0)
