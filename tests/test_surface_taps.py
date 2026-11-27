"""The picture surface's single-click / double-click contract.

The player must not rely on ``Gtk.GestureClick``'s ``n_press`` for touch:
GTK groups multi-press sequences with the server's double-click time and the
gesture's own sequence bookkeeping.  ``TapTracker`` owns the grouping so the
board touch panel behaves exactly like a mouse on the desktop (300 ms / 48 px),
and the window layer only schedules the debounced single tap.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from video_player.ui.taps import (  # noqa: E402
    DOUBLE,
    DOUBLE_TAP_MS,
    DOUBLE_TAP_SLOP_PX,
    SINGLE,
    TapTracker,
)


def test_constants_stay_touch_friendly() -> None:
    # 300 ms is the debounce latency a viewer feels on a pause tap; keep it
    # short, and the slop at tap-target size so a shaky finger is still a tap.
    assert 200 <= DOUBLE_TAP_MS <= 400
    assert 32.0 <= DOUBLE_TAP_SLOP_PX <= 64.0


def test_first_press_is_single_and_stays_armed() -> None:
    tracker = TapTracker()
    assert tracker.classify(10.0, 100.0, 200.0) == SINGLE
    assert tracker.last_at == 10.0
    assert tracker.last_xy == (100.0, 200.0)


def test_second_press_inside_window_and_slop_is_double() -> None:
    tracker = TapTracker()
    assert tracker.classify(10.0, 100.0, 200.0) == SINGLE
    assert tracker.classify(10.2, 120.0, 220.0) == DOUBLE


def test_double_tap_resets_so_the_third_press_is_single() -> None:
    tracker = TapTracker()
    tracker.classify(10.0, 100.0, 200.0)
    assert tracker.classify(10.1, 100.0, 200.0) == DOUBLE
    assert tracker.last_at is None
    assert tracker.classify(10.2, 100.0, 200.0) == SINGLE


def test_slow_second_press_is_a_new_single() -> None:
    tracker = TapTracker()
    tracker.classify(10.0, 100.0, 200.0)
    assert tracker.classify(10.0 + DOUBLE_TAP_MS / 1000.0 + 0.05, 100.0, 200.0) == SINGLE
    assert tracker.last_at == 10.0 + DOUBLE_TAP_MS / 1000.0 + 0.05


def test_far_second_press_is_a_new_single() -> None:
    tracker = TapTracker()
    tracker.classify(10.0, 100.0, 200.0)
    far = DOUBLE_TAP_SLOP_PX + 10.0
    assert tracker.classify(10.1, 100.0 + far, 200.0) == SINGLE


def test_boundary_values_count_as_double() -> None:
    tracker = TapTracker()
    tracker.classify(10.0, 100.0, 200.0)
    assert tracker.classify(10.0 + DOUBLE_TAP_MS / 1000.0, 100.0, 200.0) == DOUBLE
    tracker.classify(20.0, 100.0, 200.0)
    assert tracker.classify(20.1, 100.0 + DOUBLE_TAP_SLOP_PX, 200.0) == DOUBLE


def test_reset_forgets_a_pending_first_press() -> None:
    tracker = TapTracker()
    tracker.classify(10.0, 100.0, 200.0)
    tracker.reset()
    assert tracker.last_at is None
    assert tracker.classify(10.1, 100.0, 200.0) == SINGLE


def test_clock_going_backwards_does_not_create_a_double() -> None:
    tracker = TapTracker()
    tracker.classify(10.0, 100.0, 200.0)
    assert tracker.classify(9.0, 100.0, 200.0) == SINGLE
