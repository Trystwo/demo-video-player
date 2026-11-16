"""YuNet (2023mar) post-processing: decode raw heads into face rectangles.

The model (``models/face_detection_yunet_2023mar.onnx``) is the OpenCV Zoo
YuNet detector (MIT, (c) 2020 Shiqi Yu).  It runs on a fixed 640x640 BGR input
and returns 12 raw tensors (three feature strides ``8/16/32``):

====================  ============================  ==========================
name                  shape                         meaning
====================  ============================  ==========================
``cls_{8,16,32}``     ``[1, N, 1]``                 classification score
``obj_{8,16,32}``     ``[1, N, 1]``                 objectness score
``bbox_{8,16,32}``    ``[1, N, 4]``                 ``(dx, dy, log(w), log(h))``
``kps_{8,16,32}``     ``[1, N, 10]``                five landmark ``(x, y)`` pairs
====================  ============================  ==========================

The decoding follows OpenCV's ``modules/objdetect/src/face_detect.cpp``:

* ``score = sqrt(cls * obj)``
* ``cx = (col + dx) * stride``, ``cy = (row + dy) * stride``
* ``w = exp(log_w) * stride``, ``h = exp(log_h) * stride``
* landmarks: ``(col + kx) * stride``, ``(row + ky) * stride``

The module is NumPy-only so it can be unit tested without OpenCV, GStreamer or
the NPU runtime.
"""

from __future__ import annotations

from typing import Mapping, Sequence

import numpy as np

from .face import Face

#: Feature strides used by the three detection heads.
STRIDES: tuple[int, int, int] = (8, 16, 32)

#: The model input is a fixed square; YuNet has no dynamic shape support.
INPUT_SIZE = 640

#: Number of landmark points decoded per face.
NUM_LANDMARKS = 5

#: Raw output tensor names in the order the ONNX graph exposes them.
OUTPUT_NAMES: tuple[str, ...] = tuple(
    f"{head}_{stride}"
    for head in ("cls", "obj", "bbox", "kps")
    for stride in STRIDES
)


def decode_yunet_outputs(
    outputs: Mapping[str, np.ndarray],
    *,
    score_threshold: float = 0.5,
    nms_threshold: float = 0.3,
    max_faces: int = 32,
) -> list[Face]:
    """Decode the 12 raw YuNet tensors into classifier-style ``Face`` objects.

    Boxes are expressed in the 640x640 *model* coordinate system; call
    :func:`map_faces_to_frame` to map them back to the source frame.  ``Face``
    rectangles are ``(x, y, width, height)`` with a top-left origin, matching
    the rest of the video player (GStreamer caps and the GTK overlay).
    """

    boxes: list[np.ndarray] = []  # (N, 4) xywh in model coordinates
    scores: list[np.ndarray] = []  # (N,)
    landmarks: list[np.ndarray] = []  # (N, 5, 2)

    for stride in STRIDES:
        grid = INPUT_SIZE // stride
        cls = _as_1d(outputs[f"cls_{stride}"])
        obj = _as_1d(outputs[f"obj_{stride}"])
        bbox = _as_2d(outputs[f"bbox_{stride}"], 4)
        kps = _as_2d(outputs[f"kps_{stride}"], 10)
        if not (cls.size == obj.size == grid * grid == bbox.shape[0]):
            raise ValueError(
                f"unexpected tensor sizes for stride {stride}: "
                f"cls={cls.size} obj={obj.size} bbox={bbox.shape} (grid={grid})"
            )

        scores_s = np.sqrt(np.clip(cls, 0.0, 1.0) * np.clip(obj, 0.0, 1.0))
        keep = np.flatnonzero(scores_s >= score_threshold)
        if keep.size == 0:
            continue

        rows = keep // grid
        cols = keep % grid
        cx = (cols + bbox[keep, 0]) * stride
        cy = (rows + bbox[keep, 1]) * stride
        width = np.exp(bbox[keep, 2]) * stride
        height = np.exp(bbox[keep, 3]) * stride

        boxes.append(np.stack([cx - width / 2.0, cy - height / 2.0, width, height], axis=1))
        scores.append(scores_s[keep])

        stride_landmarks = np.empty((keep.size, NUM_LANDMARKS, 2), dtype=np.float32)
        for point in range(NUM_LANDMARKS):
            stride_landmarks[:, point, 0] = (cols + kps[keep, point * 2]) * stride
            stride_landmarks[:, point, 1] = (rows + kps[keep, point * 2 + 1]) * stride
        landmarks.append(stride_landmarks)

    if not boxes:
        return []

    all_boxes = np.concatenate(boxes, axis=0)
    all_scores = np.concatenate(scores, axis=0)
    all_landmarks = np.concatenate(landmarks, axis=0)

    kept = _nms(all_boxes, all_scores, nms_threshold)[:max_faces]
    return [
        Face(
            x=float(all_boxes[i, 0]),
            y=float(all_boxes[i, 1]),
            width=float(all_boxes[i, 2]),
            height=float(all_boxes[i, 3]),
            score=float(all_scores[i]),
            landmarks=tuple(
                (float(px), float(py)) for px, py in all_landmarks[i]
            ),
        )
        for i in kept
    ]


def map_faces_to_frame(
    faces: Sequence[Face], frame_width: int, frame_height: int
) -> list[Face]:
    """Map faces from the square model input back to the source frame.

    The video frame is stretched to ``INPUT_SIZE`` x ``INPUT_SIZE`` before
    inference, so both axes scale independently.
    """

    if frame_width <= 0 or frame_height <= 0:
        raise ValueError("frame dimensions must be positive")
    scale_x = frame_width / INPUT_SIZE
    scale_y = frame_height / INPUT_SIZE
    mapped: list[Face] = []
    for face in faces:
        mapped.append(
            Face(
                x=face.x * scale_x,
                y=face.y * scale_y,
                width=face.width * scale_x,
                height=face.height * scale_y,
                score=face.score,
                landmarks=tuple(
                    (px * scale_x, py * scale_y) for px, py in face.landmarks
                ),
            )
        )
    return mapped


def preprocess_frame(frame: np.ndarray, size: int = INPUT_SIZE) -> np.ndarray:
    """Stretch a BGR frame to the model input size (OpenCV layout, no mean).

    Uses OpenCV when available because the board ships ``python3-opencv``;
    falls back to a simple NumPy nearest-neighbour resize for headless tests.
    """

    if frame.ndim != 3 or frame.shape[2] != 3:
        raise ValueError(f"expected HxWx3 BGR frame, got shape {frame.shape}")
    if frame.shape[0] == size and frame.shape[1] == size:
        return np.ascontiguousarray(frame, dtype=np.uint8)
    try:  # pragma: no cover - depends on the board image
        import cv2

        return cv2.resize(frame, (size, size), interpolation=cv2.INTER_LINEAR)
        # fall through only when OpenCV is missing
    except Exception:
        rows = (np.arange(size) * frame.shape[0] // size).clip(0, frame.shape[0] - 1)
        cols = (np.arange(size) * frame.shape[1] // size).clip(0, frame.shape[1] - 1)
        return np.ascontiguousarray(frame[rows][:, cols], dtype=np.uint8)


def _as_1d(array: np.ndarray) -> np.ndarray:
    return np.asarray(array, dtype=np.float32).reshape(-1)


def _as_2d(array: np.ndarray, width: int) -> np.ndarray:
    array = np.asarray(array, dtype=np.float32)
    if array.ndim == 3:  # [1, N, width]
        array = array.reshape(array.shape[0] * array.shape[1] * array.shape[2] // width, width)
    return array.reshape(-1, width)


def _nms(boxes: np.ndarray, scores: np.ndarray, threshold: float) -> list[int]:
    """Greedy non-maximum suppression on ``(x, y, w, h)`` boxes."""

    order = np.argsort(-scores)
    kept: list[int] = []
    while order.size:
        current = int(order[0])
        kept.append(current)
        if order.size == 1:
            break
        rest = order[1:]
        overlaps = _iou(boxes[current], boxes[rest])
        order = rest[overlaps <= threshold]
    return kept


def _iou(box: np.ndarray, others: np.ndarray) -> np.ndarray:
    def corners(b: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
        x1 = b[..., 0]
        y1 = b[..., 1]
        return x1, y1, x1 + b[..., 2], y1 + b[..., 3]

    bx1, by1, bx2, by2 = corners(box)
    ox1, oy1, ox2, oy2 = corners(others)
    inter_w = np.clip(np.minimum(bx2, ox2) - np.maximum(bx1, ox1), 0.0, None)
    inter_h = np.clip(np.minimum(by2, oy2) - np.maximum(by1, oy1), 0.0, None)
    intersection = inter_w * inter_h
    area_box = max(0.0, float(bx2 - bx1)) * max(0.0, float(by2 - by1))
    areas_others = np.clip(ox2 - ox1, 0.0, None) * np.clip(oy2 - oy1, 0.0, None)
    union = area_box + areas_others - intersection
    return np.where(union > 0.0, intersection / np.maximum(union, 1e-9), 0.0)
