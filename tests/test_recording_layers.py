"""The recorder canvas has to cover the whole window, header bar included.

The canvas is built from a handful of widget snapshots (a full-window snapshot
costs too much), so any chrome missing from :meth:`PlayerWindow._recording_layer_specs`
ends up as a flat band in the recording.  The header bar was missing, which left
an empty ~59 px strip at the top of every file (found on the board 2026-11-27,
STATE §49/§50).  These tests pin the layer list without needing a display.
"""

from __future__ import annotations

import pytest

pytest.importorskip("gi")

from video_player.ui.window import PlayerWindow  # noqa: E402


class _FakeWindow:
    """Bare object carrying the widget attributes the layer spec touches."""


def _fake_window() -> tuple[_FakeWindow, dict[str, object]]:
    widgets = {
        "_headerbar": object(),
        "_sidebar_scroll": object(),
        "_info_bar": object(),
        "_controls_bar": object(),
        "_hint_label": object(),
        "_status_card": object(),
    }
    window = _FakeWindow()
    for name, widget in widgets.items():
        setattr(window, name, widget)
    return window, widgets


def test_recording_layers_cover_header_sidebar_and_transport() -> None:
    window, _widgets = _fake_window()

    names = [name for name, _widget in PlayerWindow._recording_layer_specs(window)]

    assert names == [
        "header",
        "sidebar",
        "info",
        "controls",
        "hint",
        "playback-status",
    ]


def test_recording_layers_start_with_the_real_header_bar() -> None:
    window, widgets = _fake_window()

    specs = PlayerWindow._recording_layer_specs(window)

    assert specs[0][0] == "header"
    assert specs[0][1] is widgets["_headerbar"]
