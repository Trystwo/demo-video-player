"""Face-detection contract, RKNN detector placeholder and frame scheduler.

M1 does not need face detection; M2 plugs a RetinaFace/SCRFD RKNN model into
:class:`RknnFaceDetector`.  Everything else in the package talks to the
:class:`FaceDetector` protocol, so the detector can be swapped for a fake in
tests and for a tracking implementation later without touching the pipeline.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
import time
from pathlib import Path
from typing import TYPE_CHECKING, Protocol, Sequence, runtime_checkable

if TYPE_CHECKING:  # pragma: no cover - typing only
    import numpy as np


@dataclass(frozen=True)
class Face:
    """A detected face in frame pixel coordinates (top-left origin)."""

    x: float
    y: float
    width: float
    height: float
    score: float = 1.0
    #: Optional landmarks, e.g. five points for alignment or a full 106-point mesh.
    landmarks: tuple[tuple[float, float], ...] = ()

    def as_rect(self) -> tuple[float, float, float, float]:
        return (self.x, self.y, self.width, self.height)

    def clamped(self, frame_width: int, frame_height: int) -> "Face":
        """Clip the rectangle to the frame, keeping it non-empty."""
        x0 = max(0.0, min(float(frame_width), self.x))
        y0 = max(0.0, min(float(frame_height), self.y))
        x1 = max(0.0, min(float(frame_width), self.x + self.width))
        y1 = max(0.0, min(float(frame_height), self.y + self.height))
        return replace(self, x=x0, y=y0, width=max(0.0, x1 - x0), height=max(0.0, y1 - y0))


@dataclass(frozen=True)
class DetectionResult:
    """Faces valid for one frame, plus timing metadata for the info panel."""

    faces: tuple[Face, ...] = ()
    frame_index: int = -1
    inference_ms: float = 0.0
    #: True when the scheduler reused the previous detection instead of running
    #: the NPU for this frame.
    reused: bool = False
    #: True when this result was empty but the backend kept the previous faces
    #: (see :attr:`video_player.config.DetectionConfig.hold_ms`).  The UI shows
    #: the held boxes; the shader keeps the held mask.
    held: bool = False


def face_centre(face: "Face") -> tuple[float, float]:
    """Centre of the face box, in frame pixels."""
    return (face.x + face.width / 2.0, face.y + face.height / 2.0)


def primary_face(faces: Sequence[Face]) -> "Face | None":
    """Return the largest face by box area (the one the demo is about) or None."""
    best: Face | None = None
    best_area = -1.0
    for face in faces:
        area = face.width * face.height
        if area > best_area:
            best = face
            best_area = area
    return best


def smooth_landmarks(
    previous: Sequence[tuple[float, float]] | None,
    face: "Face | None",
    *,
    alpha: float = 0.35,
) -> tuple[tuple[float, float], ...]:
    """Exponential-moving-average the five landmarks of the tracked face.

    The NPU runs at ~10 Hz while the display runs at 30 fps, so the raw
    landmark points (used as the eye/warp control points in M5) jitter by a few
    pixels from detection to detection.  ``alpha`` is the weight of the new
    sample: ``smoothed = previous + alpha * (new - previous)``.

    The history is dropped -- the new points are returned as-is -- when the
    point count changes or the face centre jumps by more than 30 % of the face
    width: that is a different face, not motion, and blending across it would
    put the control points halfway between two people.
    """
    if face is None:
        return ()
    if len(face.landmarks) != 5:
        return ()
    current = tuple((float(x), float(y)) for x, y in face.landmarks)
    if previous is None or len(previous) != 5:
        return current
    old = tuple((float(x), float(y)) for x, y in previous)
    threshold = 0.3 * float(face.width)
    old_x = sum(point[0] for point in old) / len(old)
    old_y = sum(point[1] for point in old) / len(old)
    new_x = sum(point[0] for point in current) / len(current)
    new_y = sum(point[1] for point in current) / len(current)
    if (new_x - old_x) ** 2 + (new_y - old_y) ** 2 > threshold**2:
        return current
    weight = max(0.0, min(1.0, float(alpha)))
    return tuple(
        (
            old_point[0] + weight * (new_point[0] - old_point[0]),
            old_point[1] + weight * (new_point[1] - old_point[1]),
        )
        for old_point, new_point in zip(old, current)
    )



@runtime_checkable
class FaceDetector(Protocol):
    """Interface implemented by the NPU detector (and by test doubles)."""

    def detect(self, frame: "np.ndarray") -> Sequence[Face]:
        ...


class RknnFaceDetector:
    """YuNet face detector running on the RK3576 NPU through ``librknnrt``.

    The model is the OpenCV Zoo YuNet detector converted with
    ``scripts/convert_yunet.py`` (see ``docs/BOARD.md``): fp16, 640x640 BGR
    input, 12 raw output tensors decoded by :mod:`video_player.face_yunet`.
    """

    def __init__(
        self,
        model_path: str,
        input_size: tuple[int, int] = (640, 640),
        score_threshold: float = 0.5,
        max_faces: int = 3,
        core_mask: int = 0,
    ) -> None:
        self.model_path = str(model_path)
        self.input_size = input_size
        self.score_threshold = score_threshold
        self.max_faces = max_faces
        self.core_mask = core_mask
        self._runtime = None

    def load(self) -> None:
        """Load the RKNN model and warm the NPU up with one dummy inference."""
        if not __import__("pathlib").Path(self.model_path).is_file():
            raise FileNotFoundError(f"face model not found: {self.model_path}")
        from .rknn_runtime import RknnRuntime

        self._runtime = RknnRuntime(self.model_path, core_mask=self.core_mask)
        import numpy as np

        warmup = np.zeros(
            (self.input_size[1], self.input_size[0], 3), dtype=np.uint8
        )
        self._runtime.infer(warmup)

    @property
    def loaded(self) -> bool:
        return self._runtime is not None

    def detect(self, frame: "np.ndarray") -> Sequence[Face]:
        """Detect faces in a BGR frame; boxes are in frame pixel coordinates."""
        if self._runtime is None:
            raise RuntimeError("RknnFaceDetector.load() must be called first")
        from .face_yunet import decode_yunet_outputs, map_faces_to_frame, preprocess_frame

        model_input = preprocess_frame(frame)
        raw = self._runtime.infer(model_input)
        faces = decode_yunet_outputs(
            raw,
            score_threshold=self.score_threshold,
            max_faces=self.max_faces,
        )
        height, width = frame.shape[:2]
        return map_faces_to_frame(faces, width, height)

    def close(self) -> None:
        if self._runtime is not None:
            self._runtime.close()
            self._runtime = None


def resolve_model_path(path: str) -> Path:
    """Resolve a config model path against the CWD and then the demo root."""

    candidate = Path(path).expanduser()
    if candidate.is_file():
        return candidate
    if not candidate.is_absolute():
        project_root = Path(__file__).resolve().parents[2]
        rooted = project_root / candidate
        if rooted.is_file():
            return rooted
    return candidate


class DetectionScheduler:
    """Run the detector every ``interval_frames`` frames and reuse the result.

    Detection is the most expensive stage, so the pipeline reuses the last
    detection for the frames in between (optionally smoothed by a tracker in
    M2).  ``reused`` on :class:`DetectionResult` tells the UI when the info
    panel value is stale.
    """

    def __init__(self, detector: FaceDetector, interval_frames: int = 3) -> None:
        if interval_frames < 1:
            raise ValueError(f"interval_frames must be >= 1, got {interval_frames}")
        self._detector = detector
        self._interval = interval_frames
        self._frame_index = 0
        self._next_detect = 0
        self._last = DetectionResult()

    @property
    def last(self) -> DetectionResult:
        return self._last

    def reset(self) -> None:
        self._frame_index = 0
        self._next_detect = 0
        self._last = DetectionResult()

    def process(self, frame: "np.ndarray") -> DetectionResult:
        index = self._frame_index
        self._frame_index += 1

        if index < self._next_detect:
            self._last = replace(self._last, frame_index=index, reused=True)
            return self._last

        started = time.perf_counter()
        faces = tuple(self._detector.detect(frame))
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        self._last = DetectionResult(
            faces=faces,
            frame_index=index,
            inference_ms=elapsed_ms,
            reused=False,
        )
        self._next_detect = index + self._interval
        return self._last
