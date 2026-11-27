"""The recorder canvas has to cover the whole P4 player page.

The canvas is built from a handful of widget snapshots (a full-window snapshot
costs too much), so any chrome missing from :meth:`PlayerWindow._recording_layer_specs`
ends up as a flat band in the recording.  The P4 page has no header bar and no
sidebar: its chrome is the picture-top bar, the bottom dock and the beauty
drawer.  These tests pin the layer list without needing a display.
"""

from __future__ import annotations

import pytest

pytest.importorskip("gi")

from video_player.ui.window import PlayerWindow  # noqa: E402


class _FakeWindow:
    """Bare object carrying the widget attributes the layer spec touches."""


def _fake_window() -> tuple[_FakeWindow, dict[str, object]]:
    widgets = {
        "_top_bar": object(),
        "_dock": object(),
        "_beauty_page": object(),
        "_hint_label": object(),
        "_status_card": object(),
    }
    window = _FakeWindow()
    for name, widget in widgets.items():
        setattr(window, name, widget)
    return window, widgets


def test_recording_layers_cover_top_bar_dock_drawer_and_status() -> None:
    window, _widgets = _fake_window()

    names = [name for name, _widget in PlayerWindow._recording_layer_specs(window)]

    assert names == ["top", "dock", "drawer", "hint", "playback-status"]


def test_recording_layers_start_with_the_real_top_bar() -> None:
    window, widgets = _fake_window()

    specs = PlayerWindow._recording_layer_specs(window)

    assert specs[0][0] == "top"
    assert specs[0][1] is widgets["_top_bar"]
    assert specs[1][1] is widgets["_dock"]
