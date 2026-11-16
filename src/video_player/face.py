"""Face-detection contract, RKNN detector placeholder and frame scheduler.

M1 does not need face detection; M2 plugs a RetinaFace/SCRFD RKNN model into
:class:`RknnFaceDetector`.  Everything else in the package talks to the
:class:`FaceDetector` protocol, so the detector can be swapped for a fake in
tests and for a tracking implementation later without touching the pipeline.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
import time
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


@runtime_checkable
class FaceDetector(Protocol):
    """Interface implemented by the NPU detector (and by test doubles)."""

    def detect(self, frame: "np.ndarray") -> Sequence[Face]:
        ...


class RknnFaceDetector:
    """RetinaFace/SCRFD detector running through ``librknnrt`` (M2 placeholder).

    The conversion + post-processing steps are deliberately not implemented in
    the scaffold; see ``docs/BOARD.md`` for the model pipeline that M2 must
    verify (letterbox to 640x640, NPU inference, anchor decode, NMS).
    """

    def __init__(
        self,
        model_path: str,
        input_size: tuple[int, int] = (640, 640),
        score_threshold: float = 0.5,
        max_faces: int = 3,
    ) -> None:
        self.model_path = model_path
        self.input_size = input_size
        self.score_threshold = score_threshold
        self.max_faces = max_faces

    def load(self) -> None:
        raise NotImplementedError(
            "M2: load and convert RetinaFace/SCRFD to RKNN; see docs/BOARD.md"
        )

    def detect(self, frame: "np.ndarray") -> Sequence[Face]:
        raise NotImplementedError(
            "M2: run librknnrt inference, decode anchors and apply NMS"
        )


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
