"""Tap grouping for the picture surface (pure Python, no GTK).

The player distinguishes a single tap (play/pause) from a double tap (full
screen).  GTK's ``Gtk.GestureClick`` reports ``n_press`` for pointer input, but
touch sequences are grouped by the gesture implementation and the server's
double-click time; both are outside the product's control.  The board is a
touch panel, so the grouping lives here instead:

* a second press within :data:`DOUBLE_TAP_MS` and :data:`DOUBLE_TAP_SLOP_PX`
  of the first one is a double tap;
* after a double tap the tracker resets, so a third tap starts a new single;
* the caller debounces the single tap for :data:`DOUBLE_TAP_MS`.

Keeping this module free of ``gi`` means the behaviour is unit-tested on the
desktop without a display, exactly like ``chapters.py``.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

__all__ = [
    "DOUBLE_TAP_MS",
    "DOUBLE_TAP_SLOP_PX",
    "SINGLE",
    "DOUBLE",
    "TapTracker",
]

#: How long a single tap waits for a companion press before it fires.
DOUBLE_TAP_MS = 300
#: A second press farther than this (in picture pixels) is a new single tap.
DOUBLE_TAP_SLOP_PX = 48.0

SINGLE = "single"
DOUBLE = "double"


@dataclass
class TapTracker:
    """Classify presses as the first half of a single or the second of a double."""

    interval_s: float = DOUBLE_TAP_MS / 1000.0
    slop_px: float = DOUBLE_TAP_SLOP_PX
    last_at: float | None = None
    last_xy: tuple[float, float] = (0.0, 0.0)

    def reset(self) -> None:
        """Forget the previous press (page switch, clip change, drawer close)."""
        self.last_at = None
        self.last_xy = (0.0, 0.0)

    def classify(self, now: float, x: float, y: float) -> str:
        """Return :data:`SINGLE` or :data:`DOUBLE` for the press at *now*.

        A returned ``SINGLE`` arms the caller's debounce timer; a ``DOUBLE``
        means the timer must be cancelled and the full-screen action run.
        """
        previous = self.last_at
        if previous is not None:
            elapsed = float(now) - previous
            distance = math.hypot(float(x) - self.last_xy[0], float(y) - self.last_xy[1])
            if (
                0.0 <= elapsed <= self.interval_s + 1e-9
                and distance <= self.slop_px + 1e-9
            ):
                self.reset()
                return DOUBLE
        self.last_at = float(now)
        self.last_xy = (float(x), float(y))
        return SINGLE
