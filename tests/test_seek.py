"""Tests for the seek call convention (PyGObject argument order).

``Gst.Element.seek`` is exposed by PyGObject as
``seek(rate, format, flags, start_type, start, stop_type, stop)`` -- the
format argument comes *before* the seek flags, unlike the C signature.  The
fake pipeline below mirrors that bound signature (it raises ``TypeError`` for
the C order), so these tests fail if the backend regresses.
"""

from __future__ import annotations

import gi

gi.require_version("Gst", "1.0")
from gi.repository import Gst  # noqa: E402

from video_player.backend import SEEK_FLAGS, PlaybackBackend  # noqa: E402
from video_player.config import Config  # noqa: E402


class FakePipeline:
    """Records seek calls and validates the PyGObject argument order."""

    def __init__(self, position: int = 0) -> None:
        self.calls: list[tuple[float, Gst.Format, Gst.SeekFlags, int]] = []
        self._position = position
        self._duration = 0

    def seek(
        self,
        rate: float,
        fmt: Gst.Format,
        flags: Gst.SeekFlags,
        start_type: Gst.SeekType,
        start: int,
        stop_type: Gst.SeekType,
        stop: int,
    ) -> bool:
        if not isinstance(fmt, Gst.Format):
            raise TypeError(f"format argument received {type(fmt).__name__}")
        if not isinstance(flags, Gst.SeekFlags):
            raise TypeError(f"flags argument received {type(flags).__name__}")
        assert start_type is Gst.SeekType.SET
        assert stop_type is Gst.SeekType.NONE
        assert stop == -1
        self.calls.append((rate, fmt, flags, start))
        self._position = start
        return True

    def query_position(self, fmt: Gst.Format):
        assert isinstance(fmt, Gst.Format)
        return True, self._position

    def query_duration(self, fmt: Gst.Format):
        assert isinstance(fmt, Gst.Format)
        return True, self._duration


def _backend_with(pipeline: FakePipeline) -> PlaybackBackend:
    backend = PlaybackBackend(Config())
    backend._pipeline = pipeline
    return backend


def test_seek_ns_uses_the_binding_argument_order() -> None:
    pipeline = FakePipeline()
    _backend_with(pipeline).seek_ns(12_500_000_000)

    assert len(pipeline.calls) == 1
    rate, fmt, flags, position = pipeline.calls[0]
    assert rate == 1.0
    assert fmt == Gst.Format.TIME
    assert flags == SEEK_FLAGS
    assert position == 12_500_000_000


def test_seek_ns_clamps_negative_positions() -> None:
    pipeline = FakePipeline()
    _backend_with(pipeline).seek_ns(-42)

    assert pipeline.calls[0][3] == 0


def test_seek_fraction_maps_to_duration() -> None:
    pipeline = FakePipeline()
    pipeline._duration = 10_000_000_000
    backend = _backend_with(pipeline)

    backend.seek_fraction(0.25)

    assert pipeline.calls[0][3] == 2_500_000_000


def test_set_rate_seeks_with_the_new_rate() -> None:
    pipeline = FakePipeline(position=3_000_000_000)
    backend = _backend_with(pipeline)

    backend.set_rate(2.0)

    assert len(pipeline.calls) == 1
    rate, _fmt, _flags, position = pipeline.calls[0]
    assert rate == 2.0
    assert position == 3_000_000_000
