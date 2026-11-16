"""Unit tests for the YuNet post-processing (NumPy only)."""

from __future__ import annotations

import pytest

np = pytest.importorskip("numpy")

from video_player.face_yunet import (  # noqa: E402
    INPUT_SIZE,
    STRIDES,
    decode_yunet_outputs,
    map_faces_to_frame,
)


def _empty_outputs() -> dict[str, np.ndarray]:
    outputs: dict[str, np.ndarray] = {}
    for stride in STRIDES:
        grid = INPUT_SIZE // stride
        cells = grid * grid
        outputs[f"cls_{stride}"] = np.zeros((1, cells, 1), dtype=np.float32)
        outputs[f"obj_{stride}"] = np.zeros((1, cells, 1), dtype=np.float32)
        outputs[f"bbox_{stride}"] = np.zeros((1, cells, 4), dtype=np.float32)
        outputs[f"kps_{stride}"] = np.zeros((1, cells, 10), dtype=np.float32)
    return outputs


def test_decode_returns_empty_when_all_scores_are_low() -> None:
    faces = decode_yunet_outputs(_empty_outputs(), score_threshold=0.5)

    assert faces == []


def test_decode_one_cell_uses_stride_and_exp_width() -> None:
    outputs = _empty_outputs()
    stride = 8
    grid = INPUT_SIZE // stride
    row, col = 3, 5
    index = row * grid + col
    # score = sqrt(1.0 * 1.0) = 1.0, bbox offsets are zero so the centre is
    # exactly (col * stride, row * stride) and w = h = exp(0) * stride.
    outputs["cls_8"][0, index, 0] = 1.0
    outputs["obj_8"][0, index, 0] = 1.0

    faces = decode_yunet_outputs(outputs, score_threshold=0.5, nms_threshold=0.3)

    assert len(faces) == 1
    face = faces[0]
    assert face.score == pytest.approx(1.0)
    assert face.width == pytest.approx(float(stride))
    assert face.height == pytest.approx(float(stride))
    assert face.x == pytest.approx(col * stride - stride / 2)
    assert face.y == pytest.approx(row * stride - stride / 2)
    assert len(face.landmarks) == 5


def test_decode_applies_nms_and_max_faces() -> None:
    outputs = _empty_outputs()
    for col in range(3):
        outputs["cls_8"][0, col, 0] = 0.9
        outputs["obj_8"][0, col, 0] = 0.9

    faces = decode_yunet_outputs(outputs, score_threshold=0.5, max_faces=2)

    # Three overlapping boxes in the same row collapse to one, so max_faces is
    # not even reached; the assertion documents the NMS behaviour.
    assert len(faces) == 1


def test_map_faces_to_frame_scales_both_axes() -> None:
    from video_player.face import Face

    mapped = map_faces_to_frame(
        [Face(x=0, y=0, width=INPUT_SIZE, height=INPUT_SIZE, landmarks=((0, 0),))],
        frame_width=1280,
        frame_height=720,
    )

    assert mapped[0].width == 1280
    assert mapped[0].height == 720
    assert mapped[0].landmarks == ((0.0, 0.0),)


def test_decode_rejects_mismatched_tensor_sizes() -> None:
    outputs = _empty_outputs()
    outputs["cls_8"] = np.zeros((1, 5, 1), dtype=np.float32)

    with pytest.raises(ValueError, match="unexpected tensor sizes"):
        decode_yunet_outputs(outputs)
