"""NV12 conversion tests for the sampled face detector (M2)."""

from __future__ import annotations

import pytest

np = pytest.importorskip("numpy")

from video_player.detection import nv12_to_bgr  # noqa: E402


def _pad_rows(plane: "np.ndarray", stride: int) -> bytes:
    height, width = plane.shape[:2]
    padded = np.zeros((height, stride), dtype=np.uint8)
    padded[:, :width] = plane
    return padded.tobytes()


def _nv12_padded(width: int, height: int, stride: int, padded_height: int) -> bytes:
    cv2 = pytest.importorskip("cv2")
    rng = np.random.default_rng(7)
    bgr = rng.integers(0, 256, (height, width, 3), dtype=np.uint8)
    planar = cv2.cvtColor(bgr, cv2.COLOR_BGR2YUV_I420)  # (h*1.5, w)
    y = planar[:height]
    u = planar[height : height + height // 4].reshape(height // 2, width // 2)
    v = planar[height + height // 4 :].reshape(height // 2, width // 2)
    uv = np.empty((height // 2, width), dtype=np.uint8)
    uv[:, 0::2] = u
    uv[:, 1::2] = v
    y_plane = np.zeros((padded_height, stride), dtype=np.uint8)
    y_plane[:height, :width] = y
    uv_plane = np.zeros((padded_height // 2, stride), dtype=np.uint8)
    uv_plane[: height // 2, :width] = uv
    return y_plane.tobytes() + uv_plane.tobytes()


def test_strided_buffer_without_height_padding() -> None:
    width, height, stride = 64, 48, 128
    data = _nv12_padded(width, height, stride, height)
    bgr = nv12_to_bgr(data, width, height, len(data))
    assert bgr.shape == (height, width, 3)


def test_strided_buffer_with_height_padding() -> None:
    width, height, stride, padded = 64, 48, 128, 56
    data = _nv12_padded(width, height, stride, padded)
    bgr = nv12_to_bgr(data, width, height, len(data))
    assert bgr.shape == (height, width, 3)


def test_strided_conversion_matches_direct_two_plane_call() -> None:
    cv2 = pytest.importorskip("cv2")
    width, height, stride, padded = 64, 48, 128, 56
    rng = np.random.default_rng(11)
    bgr = rng.integers(0, 256, (height, width, 3), dtype=np.uint8)
    planar = cv2.cvtColor(bgr, cv2.COLOR_BGR2YUV_I420)
    y = planar[:height]
    u = planar[height : height + height // 4].reshape(height // 2, width // 2)
    v = planar[height + height // 4 :].reshape(height // 2, width // 2)
    uv = np.empty((height // 2, width), dtype=np.uint8)
    uv[:, 0::2] = u
    uv[:, 1::2] = v
    y_plane = np.zeros((padded, stride), dtype=np.uint8)
    y_plane[:height, :width] = y
    uv_plane = np.zeros((padded // 2, stride), dtype=np.uint8)
    uv_plane[: height // 2, :width] = uv
    data = y_plane.tobytes() + uv_plane.tobytes()
    out = nv12_to_bgr(data, width, height, len(data))
    expected = cv2.cvtColorTwoPlane(
        np.ascontiguousarray(y_plane[:height, :width]),
        uv.reshape(height // 2, width // 2, 2),
        cv2.COLOR_YUV2BGR_NV12,
    )
    assert np.array_equal(out, expected)


def test_rejects_impossible_layout() -> None:
    with pytest.raises(ValueError):
        nv12_to_bgr(b"\x00" * 100, 64, 48, 100)
