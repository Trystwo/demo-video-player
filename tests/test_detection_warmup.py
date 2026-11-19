"""Tests for the NPU warm-up that runs before the first video frame (M3).

The RKNN runtime pays ~1.7 s for its first inference on a large frame, but only
~35 ms for every later one.  Paying that while playback is already running made
the opening second of video drop about half its frames on the board, so the tap
warm-up has to (a) use a frame of the real size and (b) run before frames flow.
These tests pin both properties without touching the NPU.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from video_player.config import Config
from video_player.detection import FaceDetectionTap, warmup_frame_sizes


class _FakeDetector:
    instances: list["_FakeDetector"] = []

    def __init__(self, model_path: str, **kwargs: object) -> None:
        self.model_path = model_path
        self.kwargs = kwargs
        self.detected: list[tuple[int, ...]] = []
        self.loaded = False
        self.closed = False
        _FakeDetector.instances.append(self)

    def load(self) -> None:
        self.loaded = True

    def detect(self, frame: object) -> list[object]:
        self.detected.append(tuple(frame.shape))  # type: ignore[attr-defined]
        return []

    def close(self) -> None:
        self.closed = True


class _FakePad:
    def __init__(self) -> None:
        self.probes: list[object] = []
        self.removed: list[int] = []

    def add_probe(self, probe_type: object, callback: object) -> int:
        self.probes.append(callback)
        return 7

    def remove_probe(self, probe_id: int) -> None:
        self.removed.append(probe_id)


@pytest.fixture()
def fake_detector(monkeypatch: pytest.MonkeyPatch) -> type[_FakeDetector]:
    _FakeDetector.instances = []
    monkeypatch.setattr(
        "video_player.detection.RknnFaceDetector", _FakeDetector
    )
    monkeypatch.setattr(
        "video_player.detection.resolve_model_path",
        lambda path: Path("/nonexistent") / str(path),
    )
    return _FakeDetector


def test_warmup_frame_sizes_uses_the_source_size() -> None:
    assert warmup_frame_sizes(1920, 1080) == [(1920, 1080)]
    assert warmup_frame_sizes(1280, 720) == [(1280, 720)]


def test_warmup_frame_sizes_falls_back_when_the_size_is_unknown() -> None:
    assert warmup_frame_sizes(0, 0) == [(1920, 1080)]
    assert warmup_frame_sizes(-1, 1080) == [(1920, 1080)]


@pytest.mark.parametrize("rotation", [90, 270])
def test_warmup_frame_sizes_adds_the_rotated_size(rotation: int) -> None:
    # Detection runs after the display rotation, so 90/270 degree streams feed
    # the detector a frame with swapped width and height.
    assert warmup_frame_sizes(1920, 1080, rotation) == [
        (1920, 1080),
        (1080, 1920),
    ]


@pytest.mark.parametrize("rotation", [0, 180])
def test_warmup_frame_sizes_skips_the_swap_for_upright_streams(
    rotation: int,
) -> None:
    assert warmup_frame_sizes(1920, 1080, rotation) == [(1920, 1080)]


def test_prepare_loads_and_warms_up_before_start(
    fake_detector: type[_FakeDetector],
) -> None:
    np = pytest.importorskip("numpy")
    tap = FaceDetectionTap(Config(), lambda result: None)
    tap.prepare()
    detector = fake_detector.instances[-1]
    assert detector.loaded is True
    assert detector.detected == [(1080, 1920, 3)]
    tap._detector = None  # detach so the fake is not closed behind our back
    assert np is not None


def test_prepare_warms_up_with_a_real_sized_frame(
    fake_detector: type[_FakeDetector],
) -> None:
    pytest.importorskip("numpy")
    tap = FaceDetectionTap(Config(), lambda result: None, rotation=lambda: 180)
    tap.set_source_size(1920, 1080)
    pad = _FakePad()
    tap.prepare()
    detector = fake_detector.instances[-1]
    assert detector.detected == [(1080, 1920, 3)]
    tap.start(pad)  # type: ignore[arg-type]
    try:
        # start() must not repeat the inference on the streaming thread.
        assert detector.detected == [(1080, 1920, 3)]
    finally:
        tap.stop()
    assert detector.closed is True
    assert pad.removed == [7]


def test_prepare_warms_up_the_rotated_size_for_quarter_turns(
    fake_detector: type[_FakeDetector],
) -> None:
    pytest.importorskip("numpy")
    tap = FaceDetectionTap(Config(), lambda result: None, rotation=lambda: 90)
    tap.set_source_size(1920, 1080)
    tap.prepare()
    try:
        assert fake_detector.instances[-1].detected == [
            (1080, 1920, 3),
            (1920, 1080, 3),
        ]
    finally:
        tap.stop()


def test_warm_up_runs_only_once(
    fake_detector: type[_FakeDetector],
) -> None:
    pytest.importorskip("numpy")
    tap = FaceDetectionTap(Config(), lambda result: None)
    tap.prepare()
    tap.prepare()
    tap.start(_FakePad())  # type: ignore[arg-type]
    try:
        assert fake_detector.instances[-1].detected == [(1080, 1920, 3)]
    finally:
        tap.stop()


def test_warm_up_failure_is_swallowed(
    fake_detector: type[_FakeDetector], monkeypatch: pytest.MonkeyPatch
) -> None:
    pytest.importorskip("numpy")
    tap = FaceDetectionTap(Config(), lambda result: None)
    tap.set_source_size(1920, 1080)

    def explode(_frame: object) -> list[object]:
        raise RuntimeError("npu gone")

    detector = _FakeDetector("model")
    detector.detect = explode  # type: ignore[method-assign]
    detector.load()
    tap._detector = detector
    tap._warm_up()  # must not raise: a failed warm-up cannot break playback
    assert tap._detector is detector
