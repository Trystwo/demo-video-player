"""Rotation handling for phone videos whose display matrix is not identity."""

import pytest

from video_player.detection import rotate_bgr, rotation_from_tag


def test_rotation_from_tag_maps_known_values():
    assert rotation_from_tag("rotate-90") == 90
    assert rotation_from_tag("rotate-180") == 180
    assert rotation_from_tag("rotate-270") == 270
    assert rotation_from_tag("flip-x") == 0
    assert rotation_from_tag("") == 0


def test_rotate_bgr_swaps_dimensions():
    np = pytest.importorskip("numpy")
    pytest.importorskip("cv2")
    frame = np.zeros((48, 64, 3), dtype=np.uint8)
    assert rotate_bgr(frame, 0).shape == (48, 64, 3)
    assert rotate_bgr(frame, 90).shape == (64, 48, 3)
    assert rotate_bgr(frame, 180).shape == (48, 64, 3)
    assert rotate_bgr(frame, 270).shape == (64, 48, 3)


def test_rotate_180_twice_is_identity():
    np = pytest.importorskip("numpy")
    cv2 = pytest.importorskip("cv2")
    frame = np.arange(48 * 64 * 3, dtype=np.uint8).reshape(48, 64, 3)
    once = rotate_bgr(frame, 180)
    twice = rotate_bgr(once, 180)
    assert np.array_equal(frame, twice)
