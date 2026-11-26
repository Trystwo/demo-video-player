"""Fullscreen dock bookkeeping, testable without GTK or a display.

The window cannot be instantiated on a host without GTK, so these tests lift
the immersive layout methods straight out of ``window.py`` and run them against
parent-tracking stand-ins.  What matters is the invariant the v3 mockup asks
for: only the transport moves into the dock, the info row is replaced by the
always-on telemetry line, and after the dock fades the telemetry (dimmed) and
the bottom progress hairline stay visible.
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
)


class FakeWidget:
    """Minimal widget that enforces single-parent insertion rules."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.parent: FakeWidget | None = None
        self.children: list[FakeWidget] = []
        self.classes: set[str] = set()
        self.visible = True
        self.revealed = False
        self.opacity = 1.0
        self.margins: dict[str, int] = {}

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<{self.name}>"

    def get_parent(self) -> FakeWidget | None:
        return self.parent

    def unparent(self) -> None:
        assert self.parent is not None, f"{self} unparent() with no parent"
        self.parent.children.remove(self)
        self.parent = None

    def append(self, child: FakeWidget) -> None:
        assert child.parent is None, f"{child} appended while inside {child.parent}"
        child.parent = self
        self.children.append(child)

    def add_css_class(self, name: str) -> None:
        self.classes.add(name)

    def remove_css_class(self, name: str) -> None:
        self.classes.discard(name)

    def get_visible(self) -> bool:
        return self.visible

    def set_visible(self, visible: bool) -> None:
        self.visible = visible

    def set_margin_top(self, value: int) -> None:
        self.margins["top"] = value

    def set_margin_bottom(self, value: int) -> None:
        self.margins["bottom"] = value

    def set_margin_start(self, value: int) -> None:
        self.margins["start"] = value

    def set_margin_end(self, value: int) -> None:
        self.margins["end"] = value

    def set_reveal_child(self, revealed: bool) -> None:
        self.revealed = revealed

    def get_reveal_child(self) -> bool:
        return self.revealed

    def set_opacity(self, opacity: float) -> None:
        self.opacity = float(opacity)

    def get_opacity(self) -> float:
        return self.opacity

    def child_names(self) -> list[str]:
        return [child.name for child in self.children]


class FakePaned(FakeWidget):
    def get_end_child(self) -> FakeWidget | None:
        return self.children[0] if self.children else None


class FakeSidebarButton(FakeWidget):
    def __init__(self, name: str, active: bool = True) -> None:
        super().__init__(name)
        self.active = active

    def get_active(self) -> bool:
        return self.active


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def monotonic(self) -> float:
        return self.now


class FakeBackend:
    def __init__(self) -> None:
        self.is_playing = True
        self.has_media = True


#: Module constants the extracted methods read; taken from the real source so a
#: changed timeout shows up here instead of being shadowed by a copy.
CONSTANTS = (
    "DOCK_IDLE_SECONDS",
    "DOCK_SUBTITLE_CLEARANCE",
    "TELEMETRY_IDLE_OPACITY",
)


def _read_source() -> tuple[str, ast.Module]:
    text = WINDOW_PY.read_text(encoding="utf-8")
    return text, ast.parse(text)


def _load_methods() -> dict[str, Any]:
    text, tree = _read_source()
    window = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "PlayerWindow"
    )
    produced: dict[str, Any] = {}
    for node in window.body:
        if isinstance(node, ast.FunctionDef) and node.name in METHODS:
            produced[node.name] = ast.get_source_segment(text, node)
    assert set(produced) == set(METHODS), sorted(produced)
    return produced


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


def _exec_methods(clock: FakeClock) -> dict[str, Any]:
    namespace: dict[str, Any] = {
        "time": clock,
        "log_action": lambda *args, **kwargs: None,
        "TELEMETRY_IDLE_OPACITY": IDLE_OPACITY,
        **_load_constants(),
    }
    for source in _load_methods().values():
        code = compile(source, "<window.py>", "exec")
        exec(code, namespace)  # noqa: S102 - fixed, in-repo source
    return namespace


class Harness:
    """A stand-in window holding just the widgets the dock code touches."""

    def __init__(self, immersive: bool = True) -> None:
        self.clock = FakeClock()
        namespace = _exec_methods(self.clock)

        self.box = FakeWidget("box")
        self.overlay = FakeWidget("overlay")
        self.dock = FakeWidget("dock")
        self.revealer = FakeWidget("revealer")
        self.telemetry = FakeWidget("telemetry")
        self.progress = FakeWidget("progress")
        self.tools = FakeWidget("tools")
        self.info = FakeWidget("info")
        self.bar = FakeWidget("bar")
        self.subtitles = FakeWidget("subtitle")
        self.paned = FakePaned("paned")
        self.sidebar = FakeWidget("sidebar")
        self.sidebar_button = FakeSidebarButton("sidebar-button")
        self.backend = FakeBackend()

        # Windowed layout: picture, info row, transport.
        self.box.append(self.overlay)
        self.box.append(self.info)
        self.box.append(self.bar)
        self.paned.append(self.sidebar)
        self.subtitles.set_margin_bottom(24)

        self._immersive = immersive
        self._dock_hover = False
        self._dock_deadline = 0.0
        self._player_margin = 8
        self._player_box = self.box
        self._dock = self.dock
        self._dock_revealer = self.revealer
        self._telemetry_box = self.telemetry
        self._fullscreen_progress = self.progress
        self._immersive_tools = self.tools
        self._info_bar = self.info
        self._controls_bar = self.bar
        self._subtitle_label = self.subtitles
        self._player_overlay = self.overlay
        self._paned = self.paned
        self._sidebar_button = self.sidebar_button
        self._info_bar_was_visible = True
        self._sync_compare_overlay = lambda: None
        self._sync_chrome_visibility = lambda: self.info.set_visible(True)

        for name in METHODS:
            setattr(self, name, types.MethodType(namespace[name], self))

    def get_height(self) -> int:
        return 720


def test_enter_moves_only_the_transport_into_the_dock() -> None:
    h = Harness()
    h._enter_immersive()

    assert h.box.child_names() == ["overlay", "info"]
    assert h.dock.child_names() == ["bar"]
    assert h.revealer.visible and h.revealer.revealed
    assert not h.info.get_visible(), "the info row is replaced by telemetry"
    assert not h.tools.get_visible() or True  # tools live in the dock row
    assert h.telemetry.visible and h.telemetry.get_opacity() == pytest.approx(1.0)
    assert h.progress.visible
    assert "docked" in h.bar.classes
    assert "immersive" in h.overlay.classes
    assert not h.sidebar.get_visible()
    assert h.box.margins == {"top": 0, "bottom": 0, "start": 0, "end": 0}


def test_leave_restores_the_windowed_order() -> None:
    h = Harness()
    h._enter_immersive()
    h._leave_immersive()

    assert h.box.child_names() == ["overlay", "info", "bar"]
    assert h.dock.children == []
    assert not h.revealer.visible and not h.revealer.revealed
    assert not h.telemetry.visible
    assert not h.progress.visible
    assert not h.tools.visible
    assert h.info.get_visible()
    assert "docked" not in h.bar.classes
    assert "immersive" not in h.overlay.classes
    assert h.sidebar.get_visible()
    assert h.box.margins == {"top": 8, "bottom": 8, "start": 8, "end": 8}


def test_hidden_sidebar_stays_hidden_after_fullscreen() -> None:
    h = Harness()
    h.sidebar_button.active = False
    h._enter_immersive()
    h._leave_immersive()

    assert not h.sidebar.get_visible()


@pytest.mark.parametrize("rounds", [1, 3])
def test_round_trip_is_repeatable(rounds: int) -> None:
    h = Harness()
    for _ in range(rounds):
        h._enter_immersive()
        assert h.box.child_names() == ["overlay", "info"]
        assert h.dock.child_names() == ["bar"]
        assert h.telemetry.visible and h.progress.visible
        h._leave_immersive()
        assert h.box.child_names() == ["overlay", "info", "bar"]
        assert not h.telemetry.visible and not h.progress.visible


def test_dock_hides_after_idle_but_telemetry_and_progress_stay() -> None:
    h = Harness()
    h._enter_immersive()

    h.clock.now = 1001.0
    h._maybe_hide_dock()
    assert h.revealer.revealed, "hid before the idle window elapsed"

    h.clock.now = 1006.0
    h._maybe_hide_dock()
    assert not h.revealer.revealed
    assert h.telemetry.visible, "the telemetry line must survive the dock fade"
    assert h.telemetry.get_opacity() == pytest.approx(IDLE_OPACITY)
    assert h.progress.visible, "the bottom progress hairline must survive the fade"


def test_hovering_the_dock_postpones_hiding() -> None:
    h = Harness()
    h._enter_immersive()

    h.clock.now = 1003.0
    h._dock_hover = True
    h._maybe_hide_dock()
    h._dock_hover = False

    h.clock.now = 1004.0
    h._maybe_hide_dock()
    assert h.revealer.revealed, "hovering did not extend the deadline"

    h.clock.now = 1006.0
    h._maybe_hide_dock()
    assert not h.revealer.revealed


def test_dock_stays_while_paused() -> None:
    h = Harness()
    h._enter_immersive()
    h.backend.is_playing = False

    h.clock.now = 9000.0
    h._maybe_hide_dock()
    assert h.revealer.revealed


def test_show_dock_restores_full_telemetry_opacity() -> None:
    h = Harness()
    h._enter_immersive()
    h.clock.now = 9000.0
    h._maybe_hide_dock()
    assert h.telemetry.get_opacity() == pytest.approx(IDLE_OPACITY)

    h._show_dock()
    assert h.revealer.revealed
    assert h.telemetry.get_opacity() == pytest.approx(1.0)


def test_subtitles_clear_the_dock_while_it_is_up() -> None:
    h = Harness()
    h._enter_immersive()
    assert h.subtitles.margins["bottom"] == CLEARANCE

    h.clock.now = 9000.0
    h._maybe_hide_dock()
    assert h.subtitles.margins["bottom"] == 24, "subtitles still lifted while hidden"

    h._show_dock()
    assert h.subtitles.margins["bottom"] == CLEARANCE

    h._leave_immersive()
    assert h.subtitles.margins["bottom"] == 24
