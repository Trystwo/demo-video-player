"""Fullscreen dock bookkeeping, testable without GTK or a display.

The P4 player page keeps the telemetry/rail/transport block in one place: the
same dock is shown in the window and faded in fullscreen.  These tests lift the
immersive methods out of ``window.py`` and drive them against stand-ins.  What
matters is the behaviour the v4 mockup promised and P4 keeps: entering
fullscreen hides the top chrome, the dock fades after idle while the corner
telemetry and bottom hairline stay, and leaving restores the windowed chrome.
"""

from __future__ import annotations

import ast
import types
from pathlib import Path
from typing import Any

import pytest

WINDOW_PY = (
    Path(__file__).resolve().parents[1] / "src" / "video_player" / "ui" / "window.py"
)

METHODS = (
    "_enter_immersive",
    "_leave_immersive",
    "_show_dock",
    "_maybe_hide_dock",
    "_sync_subtitle_margin",
    "_sync_veils",
)

CONSTANTS = (
    "DOCK_IDLE_SECONDS",
    "DOCK_SUBTITLE_CLEARANCE",
    "TELEMETRY_IDLE_OPACITY",
)


def _read_source() -> tuple[str, ast.Module]:
    text = WINDOW_PY.read_text(encoding="utf-8")
    return text, ast.parse(text)


def _load_methods() -> dict[str, str]:
    text, tree = _read_source()
    window = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "PlayerWindow"
    )
    found = {
        node.name: ast.get_source_segment(text, node)
        for node in window.body
        if isinstance(node, ast.FunctionDef) and node.name in METHODS
    }
    assert set(found) == set(METHODS), sorted(found)
    return found


def _load_constants() -> dict[str, Any]:
    _, tree = _read_source()
    found: dict[str, Any] = {}
    for node in tree.body:
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        target = node.targets[0]
        if isinstance(target, ast.Name) and target.id in CONSTANTS:
            found[target.id] = ast.literal_eval(node.value)
    assert set(found) == set(CONSTANTS), sorted(found)
    return found


CONSTANTS_VALUES = _load_constants()
CLEARANCE = CONSTANTS_VALUES["DOCK_SUBTITLE_CLEARANCE"]
IDLE_OPACITY = CONSTANTS_VALUES["TELEMETRY_IDLE_OPACITY"]


class FakeWidget:
    def __init__(self, name: str) -> None:
        self.name = name
        self.classes: set[str] = set()
        self.visible = True
        self.revealed = True
        self.opacity = 1.0
        self.margins: dict[str, int] = {}

    def add_css_class(self, name: str) -> None:
        self.classes.add(name)

    def remove_css_class(self, name: str) -> None:
        self.classes.discard(name)

    def set_visible(self, visible: bool) -> None:
        self.visible = bool(visible)

    def get_visible(self) -> bool:
        return self.visible

    def set_reveal_child(self, revealed: bool) -> None:
        self.revealed = bool(revealed)

    def get_reveal_child(self) -> bool:
        return self.revealed

    def set_opacity(self, opacity: float) -> None:
        self.opacity = float(opacity)

    def get_opacity(self) -> float:
        return self.opacity

    def set_margin_bottom(self, value: int) -> None:
        self.margins["bottom"] = value

    def get_margin_bottom(self) -> int:
        return self.margins.get("bottom", 0)


class FakeBackend:
    def __init__(self) -> None:
        self.has_media = True
        self.is_playing = True


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def monotonic(self) -> float:
        return self.now


def _namespace(clock: FakeClock) -> dict[str, Any]:
    namespace: dict[str, Any] = {
        "time": clock,
        "log_action": lambda *args, **kwargs: None,
        **CONSTANTS_VALUES,
    }
    for source in _load_methods().values():
        exec(compile(source, "<window.py>", "exec"), namespace)  # noqa: S102
    return namespace


class Harness:
    """Stand-in window holding just the widgets the HUD methods touch."""

    def __init__(self, immersive: bool = False) -> None:
        self.clock = FakeClock()
        namespace = _namespace(self.clock)
        for name in METHODS:
            setattr(self, name, types.MethodType(namespace[name], self))

        self.backend = FakeBackend()
        self._page = "player"
        self._immersive = immersive
        self._drawer_open = False
        self._dock_hover = False
        self._dock_deadline = 0.0
        self._top_bar = FakeWidget("top-bar")
        self._headerbar = FakeWidget("header-bar")
        self._record_path = None
        self._episodes_open = True
        self._sync_episodes_layout = lambda: None
        self._immersive_tools = FakeWidget("immersive-tools")
        self._player_overlay = FakeWidget("video")
        self._dock_revealer = FakeWidget("dock-revealer")
        self._telemetry_box = FakeWidget("telemetry")
        self._fullscreen_progress = FakeWidget("progress")
        self._subtitle_label = FakeWidget("subtitles")
        self._top_veil = FakeWidget("top-veil")
        self._bottom_veil = FakeWidget("bottom-veil")
        self._top_bar.visible = True
        self._immersive_tools.visible = False
        self._telemetry_box.visible = False
        self._fullscreen_progress.visible = False
        self._subtitle_label.set_margin_bottom(24)
        self._sync_compare_overlay = lambda: None
        self._sync_chrome_visibility = lambda: None

    def get_height(self) -> int:
        return 768


def test_entering_fullscreen_hides_top_chrome_and_shows_the_dock() -> None:
    h = Harness()
    h._enter_immersive()

    assert not h._top_bar.get_visible()
    assert h._immersive_tools.get_visible()
    assert "immersive" in h._player_overlay.classes
    assert h._dock_revealer.get_visible()
    assert h._dock_revealer.get_reveal_child()
    assert not h._telemetry_box.get_visible(), "the corner line waits for the fade"
    assert h._fullscreen_progress.get_visible()
    assert not h._top_veil.get_visible(), "no top bar means no top scrim"
    assert h._bottom_veil.get_visible(), "the dock keeps its bottom scrim"
    assert h._subtitle_label.get_margin_bottom() == CLEARANCE


def test_leaving_fullscreen_restores_windowed_chrome() -> None:
    h = Harness()
    h._enter_immersive()
    h._leave_immersive()

    assert h._top_bar.get_visible()
    assert not h._immersive_tools.get_visible()
    assert "immersive" not in h._player_overlay.classes
    assert h._dock_revealer.get_reveal_child(), "windowed mode always shows the dock"
    assert not h._telemetry_box.get_visible()
    assert not h._fullscreen_progress.get_visible()
    assert h._top_veil.get_visible() and h._bottom_veil.get_visible()
    assert h._subtitle_label.get_margin_bottom() == CLEARANCE


@pytest.mark.parametrize("rounds", [1, 3])
def test_round_trip_is_repeatable(rounds: int) -> None:
    h = Harness()
    for _ in range(rounds):
        h._enter_immersive()
        assert not h._top_bar.get_visible()
        assert h._dock_revealer.get_reveal_child()
        h._leave_immersive()
        assert h._top_bar.get_visible()
        assert h._dock_revealer.get_reveal_child()


def test_dock_hides_after_idle_but_telemetry_and_progress_stay() -> None:
    h = Harness()
    h._enter_immersive()

    h.clock.now = 1001.0
    h._maybe_hide_dock()
    assert h._dock_revealer.get_reveal_child(), "hid before the idle window elapsed"

    h.clock.now = 1006.0
    h._maybe_hide_dock()
    assert not h._dock_revealer.get_reveal_child()
    assert h._telemetry_box.get_visible(), "the corner telemetry must survive the fade"
    assert h._telemetry_box.get_opacity() == pytest.approx(IDLE_OPACITY)
    assert h._fullscreen_progress.get_visible()
    assert not h._top_veil.get_visible() and not h._bottom_veil.get_visible()
    assert h._subtitle_label.get_margin_bottom() == 24


def test_hovering_the_dock_postpones_hiding() -> None:
    h = Harness()
    h._enter_immersive()

    h.clock.now = 1003.0
    h._dock_hover = True
    h._maybe_hide_dock()
    h._dock_hover = False

    h.clock.now = 1004.0
    h._maybe_hide_dock()
    assert h._dock_revealer.get_reveal_child(), "hovering did not extend the deadline"

    h.clock.now = 1006.0
    h._maybe_hide_dock()
    assert not h._dock_revealer.get_reveal_child()


def test_dock_stays_while_paused() -> None:
    h = Harness()
    h._enter_immersive()
    h.backend.is_playing = False

    h.clock.now = 9000.0
    h._maybe_hide_dock()
    assert h._dock_revealer.get_reveal_child()


def test_dock_stays_while_the_beauty_drawer_is_open() -> None:
    h = Harness()
    h._enter_immersive()
    h._drawer_open = True

    h.clock.now = 9000.0
    h._maybe_hide_dock()
    assert h._dock_revealer.get_reveal_child()


def test_show_dock_restores_transport_and_hides_corner_telemetry() -> None:
    h = Harness()
    h._enter_immersive()
    h.clock.now = 9000.0
    h._maybe_hide_dock()
    assert h._telemetry_box.get_opacity() == pytest.approx(IDLE_OPACITY)

    h._show_dock()
    assert h._dock_revealer.get_reveal_child()
    assert not h._telemetry_box.get_visible()
    assert h._bottom_veil.get_visible()
    assert h._subtitle_label.get_margin_bottom() == CLEARANCE


def test_subtitles_clear_the_dock_while_it_is_up() -> None:
    h = Harness(immersive=True)
    h._sync_subtitle_margin()
    assert h._subtitle_label.get_margin_bottom() == CLEARANCE

    h._maybe_hide_dock()  # deadline is 0: hides immediately
    assert h._subtitle_label.get_margin_bottom() == 768 - 768 + 24

    h._show_dock()
    assert h._subtitle_label.get_margin_bottom() == CLEARANCE

    h._leave_immersive()
    assert h._subtitle_label.get_margin_bottom() == CLEARANCE
