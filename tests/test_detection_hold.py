"""Detection hold + landmark smoothing (M5 task 1).

The NPU detector returns an empty face list now and then even for a clearly
visible face (measured on the sales clip: 16 empty results in 79 updates,
longest gap 0.58 s).  Before M5 the backend cleared ``_faces`` on the first
empty result, so the beauty shader fell back to pass-through for that frame --
the 100-600 ms "beauty flickers off" the user reported.  The backend now keeps
the last non-empty detection for ``detection.hold_ms`` and smooths the five
landmarks (used by the M5 warp) with an EMA.

The tests drive ``_on_detection_result`` directly with a fake monotonic clock,
so they are deterministic and do not need a pipeline or the NPU.
"""

from __future__ import annotations

import types

import pytest

from video_player.backend import PlaybackBackend
from video_player.config import Config
from video_player.face import DetectionResult, Face

# YuNet order: right eye, left eye, nose, right mouth corner, left mouth corner.
LANDMARKS = (
    (760.0, 470.0),
    (980.0, 470.0),
    (870.0, 620.0),
    (770.0, 800.0),
    (970.0, 800.0),
)


def _face(
    x: float = 600.0,
    y: float = 250.0,
    width: float = 526.0,
    height: float = 599.0,
    *,
    landmarks: tuple[tuple[float, float], ...] = LANDMARKS,
    score: float = 0.91,
) -> Face:
    return Face(
        x=x, y=y, width=width, height=height, score=score, landmarks=landmarks
    )


class _Clock:
    """Fake ``time.monotonic``/``time.perf_counter`` for the backend module."""

    def __init__(self, now: float = 1000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class _Harness:
    def __init__(self, config: Config, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            PlaybackBackend,
            "_dispatch",
            staticmethod(
                lambda callback, *args: (
                    callback(*args) if callback is not None else None
                )
            ),
        )
        self.clock = _Clock()
        monkeypatch.setattr(
            "video_player.backend.time",
            types.SimpleNamespace(
                monotonic=self.clock, perf_counter=self.clock
            ),
        )
        self.backend = PlaybackBackend(config)
        self.backend._coded_size = (1920, 1080)
        self.backend._texture_size = (1920, 1080)
        self.results: list[DetectionResult] = []
        self.backend.on_detection = self.results.append

    def detect(self, *faces: Face) -> DetectionResult:
        result = DetectionResult(faces=tuple(faces), frame_index=len(self.results))
        self.backend._on_detection_result(result)
        return self.results[-1]


@pytest.fixture()
def harness(monkeypatch: pytest.MonkeyPatch) -> _Harness:
    return _Harness(Config.from_dict({"detection": {"hold_ms": 600}}), monkeypatch)


def test_empty_detection_is_held_inside_the_window(harness: _Harness) -> None:
    first = harness.detect(_face())
    assert first.held is False
    assert harness.backend.faces == (_face(),)

    harness.clock.advance(0.3)  # 10 Hz detector: ~every 0.1 s
    held = harness.detect()

    assert held.held is True
    assert held.faces == (_face(),)
    assert harness.backend.faces == (_face(),)
    assert harness.backend.held_detections == 1


def test_empty_detection_clears_after_the_window(harness: _Harness) -> None:
    harness.detect(_face())
    harness.clock.advance(0.7)  # longer than hold_ms=600

    cleared = harness.detect()

    assert cleared.held is False
    assert cleared.faces == ()
    assert harness.backend.faces == ()
    assert harness.backend.held_detections == 0


def test_hold_ms_zero_clears_immediately(monkeypatch: pytest.MonkeyPatch) -> None:
    harness = _Harness(Config.from_dict({"detection": {"hold_ms": 0}}), monkeypatch)
    harness.detect(_face())
    harness.clock.advance(0.01)

    cleared = harness.detect()

    assert cleared.faces == ()
    assert harness.backend.faces == ()


def test_multiple_faces_are_all_held(harness: _Harness) -> None:
    primary = _face()
    second = _face(x=1650.0, y=120.0, width=140.0, height=160.0, landmarks=())
    harness.detect(primary, second)
    harness.clock.advance(0.2)

    held = harness.detect()

    assert held.held is True
    assert held.faces == (primary, second)


def test_held_result_keeps_the_last_landmarks(harness: _Harness) -> None:
    harness.detect(_face())
    assert harness.backend._landmarks == LANDMARKS

    harness.clock.advance(0.3)
    harness.detect()

    # The warp keeps its control points while the detection is held.
    assert harness.backend._landmarks == LANDMARKS


def test_tracking_resumes_after_a_held_gap(harness: _Harness) -> None:
    harness.detect(_face())
    harness.clock.advance(0.1)
    harness.detect()  # held
    harness.clock.advance(0.1)

    fresh = _face(x=604.0, y=252.0)
    result = harness.detect(fresh)

    assert result.held is False
    assert harness.backend.faces == (fresh,)
    assert harness.backend.held_detections == 1


def test_landmarks_are_ema_smoothed(harness: _Harness) -> None:
    harness.detect(_face())
    harness.clock.advance(0.1)

    moved = tuple((x + 10.0, y) for x, y in LANDMARKS)
    harness.detect(_face(landmarks=moved))

    # alpha 0.35: previous + 0.35 * (new - previous)
    expected = [(x + 3.5, y) for x, y in LANDMARKS]
    flat = [value for point in harness.backend._landmarks for value in point]
    assert flat == pytest.approx([value for point in expected for value in point])


def test_face_swap_resets_the_landmarks(harness: _Harness) -> None:
    harness.detect(_face())
    harness.clock.advance(0.1)

    # A different face: centre jumps by far more than 30 % of the face width.
    other_landmarks = tuple((x + 400.0, y + 60.0) for x, y in LANDMARKS)
    other = _face(x=1400.0, y=300.0, landmarks=other_landmarks)
    harness.detect(other)

    assert harness.backend._landmarks == other_landmarks


def test_held_detection_keeps_the_shader_mask(harness: _Harness) -> None:
    harness.detect(_face())
    before = harness.backend._beauty_values()

    harness.clock.advance(0.3)
    harness.detect()
    after = harness.backend._beauty_values()

    assert before["u_active"] == 1.0
    assert after["u_active"] == 1.0
    assert after["u_face0_w"] == pytest.approx(before["u_face0_w"])
    assert after["u_face0_x"] == pytest.approx(before["u_face0_x"])


def test_primary_face_is_first_for_the_shader(harness: _Harness) -> None:
    small = _face(x=1600.0, y=100.0, width=120.0, height=130.0, landmarks=())
    big = _face()
    harness.backend._faces = (small, big)  # detector order is not size order
    harness.backend._landmarks = LANDMARKS
    harness.backend._rotation = 180  # the selfie clip's display matrix

    values = harness.backend._beauty_values()

    # rotate-180 texture mapping of the big box: x' = 1 - (x+w)/1920.
    assert values["u_face0_x"] == pytest.approx(1.0 - (600.0 + 526.0) / 1920.0)
    assert values["u_face0_w"] == pytest.approx(526.0 / 1920.0)
    assert values["u_face1_w"] == pytest.approx(120.0 / 1920.0)
    assert values["u_warp_ready"] == 1.0
    # Warp centre is the big face's centre, not the first detector box, and it
    # is normalised in *display* space (the shader rotates the texture itself).
    assert values["u_warp_cx"] == pytest.approx((600.0 + 526.0 / 2.0) / 1920.0)
    assert values["u_warp_cy"] == pytest.approx((250.0 + 599.0 / 2.0) / 1080.0)
    assert values["u_warp_rot"] == pytest.approx(180.0)


def test_no_landmarks_means_no_warp(harness: _Harness) -> None:
    harness.detect(_face(landmarks=()))

    values = harness.backend._beauty_values()

    assert values["u_warp_ready"] == 0.0
