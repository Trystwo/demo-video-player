"""Tests for the detector contract and the every-N-frames scheduler."""

from __future__ import annotations

from typing import Sequence

import pytest

from video_player.face import (
    DetectionResult,
    DetectionScheduler,
    Face,
    RknnFaceDetector,
)


class FakeDetector:
    """Deterministic stand-in for the NPU detector."""

    def __init__(self, faces: Sequence[Face]) -> None:
        self.faces = tuple(faces)
        self.calls = 0
        self.last_frame: object = None

    def detect(self, frame: object) -> Sequence[Face]:
        self.calls += 1
        self.last_frame = frame
        return self.faces


def test_scheduler_runs_every_interval_and_reuses_results() -> None:
    detector = FakeDetector([Face(1, 2, 3, 4)])
    scheduler = DetectionScheduler(detector, interval_frames=3)

    results = [scheduler.process(frame) for frame in range(9)]

    assert detector.calls == 3
    assert [r.reused for r in results] == [
        False,
        True,
        True,
        False,
        True,
        True,
        False,
        True,
        True,
    ]
    assert [r.frame_index for r in results] == list(range(9))
    # Reused frames keep the last detection and its timing metadata.
    assert results[1].faces == (Face(1, 2, 3, 4),)
    assert results[1].faces == results[0].faces
    assert results[3].inference_ms >= 0.0


def test_scheduler_reset_re_detects_immediately() -> None:
    detector = FakeDetector([])
    scheduler = DetectionScheduler(detector, interval_frames=5)
    scheduler.process(None)
    scheduler.process(None)
    scheduler.reset()
    result = scheduler.process(None)
    assert detector.calls == 2
    assert result.reused is False
    assert result.frame_index == 0


def test_scheduler_rejects_invalid_interval() -> None:
    with pytest.raises(ValueError, match="interval_frames"):
        DetectionScheduler(FakeDetector([]), interval_frames=0)


def test_face_rectangle_is_clamped_to_frame() -> None:
    face = Face(x=-10, y=-5, width=40, height=30).clamped(100, 80)
    assert face.as_rect() == (0.0, 0.0, 30.0, 25.0)
    assert face.clamped(10, 10) == Face(0.0, 0.0, 10.0, 10.0)


def test_rknn_detector_is_a_placeholder_for_m2() -> None:
    detector = RknnFaceDetector("models/retinaface.rknn")
    with pytest.raises(NotImplementedError, match="M2"):
        detector.load()
    with pytest.raises(NotImplementedError, match="M2"):
        detector.detect(None)


def test_detection_result_defaults() -> None:
    result = DetectionResult()
    assert result.faces == ()
    assert result.reused is False
    assert result.frame_index == -1
