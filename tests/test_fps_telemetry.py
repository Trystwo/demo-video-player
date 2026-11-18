"""Tests for the fps value shown in the UI and printed by the perf log (M3).

``fpsdisplaysink`` emits both an instantaneous rate and a *lifetime* average.
The average starts at zero and needs about a minute to climb, so on a perfectly
smooth 30 fps stream it reads ~20 fps for the first seconds; feeding that to the
UI made a healthy playback look like it was dropping frames.  The backend now
publishes the delivered rate over a trailing window instead.
"""

from __future__ import annotations

from collections import deque

from video_player.backend import PlaybackBackend


def _backend(frame_times: list[float]) -> PlaybackBackend:
    """A backend with just enough state to exercise the fps signal handler."""
    backend = PlaybackBackend.__new__(PlaybackBackend)
    backend._frame_times = deque(frame_times)
    backend._last_fps = 0.0
    backend.on_fps = None
    return backend


def _steady_times(count: int = 61, fps: float = 30.0) -> list[float]:
    return [index / fps for index in range(count)]


def test_fps_signal_reports_delivered_rate_not_lifetime_average() -> None:
    backend = _backend(_steady_times())
    # 1.5 is what fpsdisplaysink's lifetime average reads right after start.
    backend._on_fps_measurements(None, 30.0, 0.0, 1.5)  # type: ignore[arg-type]
    assert backend.last_fps == 30.0


def test_fps_signal_falls_back_to_the_average_without_frame_history() -> None:
    backend = _backend([])
    backend._on_fps_measurements(None, 0.0, 0.5, 12.5)  # type: ignore[arg-type]
    assert backend.last_fps == 12.5


def test_fps_signal_ignores_a_single_frame_sample() -> None:
    backend = _backend([1.0])
    backend._on_fps_measurements(None, 0.0, 0.0, 4.0)  # type: ignore[arg-type]
    assert backend.last_fps == 4.0


def test_measured_fps_tracks_a_stall() -> None:
    # 30 frames over 1 s, then a 1.5 s gap: the trailing window only sees the
    # old frames draining out, so the reported rate must not stay at 30.
    times = [index / 30.0 for index in range(31)]
    times.append(times[-1] + 1.5)
    backend = _backend(times)
    rate = backend.measured_fps()
    assert rate < 30.0
