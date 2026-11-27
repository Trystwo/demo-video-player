"""The 选集 / 美颜 tabs of the windowed panel, testable without GTK.

The panel is a real GTK stack; what matters is the small state machine around
it: exactly one tab is active, selecting a tab shows its page, and the tab
choice survives a mode where the panel is unavailable (fullscreen/``--record``)
so it can be restored later.  These tests lift the two methods out of
``window.py`` and drive them with stand-ins, like ``test_player_episodes``.
"""

from __future__ import annotations

import ast
import types
from pathlib import Path
from typing import Any

WINDOW_PY = (
    Path(__file__).resolve().parents[1] / "src" / "video_player" / "ui" / "window.py"
)

METHODS = ("_set_panel_tab", "_on_panel_tab_toggled")


def _load_methods() -> dict[str, str]:
    text = WINDOW_PY.read_text(encoding="utf-8")
    tree = ast.parse(text)
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


class FakeToggle:
    """ToggleButton with the radio group semantics the panel relies on."""

    def __init__(self) -> None:
        self.active = False
        self.group: list[FakeToggle] = [self]

    def set_group(self, other: FakeToggle) -> None:
        self.group = [self, other]
        other.group = self.group

    def get_active(self) -> bool:
        return self.active

    def set_active(self, active: bool) -> None:
        active = bool(active)
        if active:
            for peer in self.group:
                if peer is not self:
                    peer.active = False
        self.active = active


class FakeStack:
    def __init__(self, pages: tuple[str, ...]) -> None:
        self.pages = set(pages)
        self.visible = "episodes"

    def get_child_by_name(self, name: str) -> object | None:
        return object() if name in self.pages else None

    def set_visible_child_name(self, name: str) -> None:
        self.visible = name


class Harness:
    def __init__(self, pages: tuple[str, ...] = ("episodes", "beauty")) -> None:
        namespace: dict[str, Any] = {}
        for source in _load_methods().values():
            exec(compile(source, "<window.py>", "exec"), namespace)  # noqa: S102
        for name in METHODS:
            setattr(self, name, types.MethodType(namespace[name], self))

        self._panel_tab = "episodes"
        self._updating_toggles = False
        self._panel_tab_episodes = FakeToggle()
        self._panel_tab_beauty = FakeToggle()
        self._panel_tab_beauty.set_group(self._panel_tab_episodes)
        self._panel_tab_episodes.set_active(True)
        self._panel_stack = FakeStack(pages)


def test_default_is_the_episode_tab() -> None:
    h = Harness()
    assert h._panel_tab == "episodes"
    assert h._panel_tab_episodes.get_active()
    assert not h._panel_tab_beauty.get_active()
    assert h._panel_stack.visible == "episodes"


def test_selecting_beauty_switches_the_page_and_the_radio() -> None:
    h = Harness()
    h._set_panel_tab("beauty")
    assert h._panel_tab == "beauty"
    assert h._panel_tab_beauty.get_active()
    assert not h._panel_tab_episodes.get_active()
    assert h._panel_stack.visible == "beauty"

    h._set_panel_tab("episodes")
    assert h._panel_tab == "episodes"
    assert h._panel_tab_episodes.get_active()
    assert h._panel_stack.visible == "episodes"


def test_a_user_click_on_the_tab_switches_the_page() -> None:
    h = Harness()
    h._panel_tab_beauty.set_active(True)  # GTK flips the state before toggled
    h._on_panel_tab_toggled(h._panel_tab_beauty)
    assert h._panel_tab == "beauty"
    assert h._panel_stack.visible == "beauty"


def test_an_inactive_or_guarded_toggle_is_ignored() -> None:
    h = Harness()
    h._on_panel_tab_toggled(h._panel_tab_beauty)  # not active
    assert h._panel_tab == "episodes"

    h._updating_toggles = True
    h._panel_tab_beauty.set_active(True)
    h._on_panel_tab_toggled(h._panel_tab_beauty)
    assert h._panel_tab == "episodes"


def test_tab_choice_is_kept_when_the_page_is_unavailable() -> None:
    # Fullscreen/--record: the beauty page leaves the stack, so there is
    # nothing to show; the button/label state is still remembered for return.
    h = Harness(pages=("episodes",))
    h._set_panel_tab("beauty")
    assert h._panel_tab == "beauty"
    assert h._panel_tab_beauty.get_active()
    assert h._panel_stack.visible == "episodes"


def test_unknown_tab_name_falls_back_to_episodes() -> None:
    h = Harness()
    h._set_panel_tab("settings")
    assert h._panel_tab == "episodes"
    assert h._panel_stack.visible == "episodes"


def test_clicking_the_only_active_tab_keeps_it_selected() -> None:
    # A grouped ToggleButton can be deactivated programmatically; a click on
    # the active tab must not leave the panel with no selected tab.
    h = Harness()
    h._panel_tab_episodes.set_active(False)
    h._on_panel_tab_toggled(h._panel_tab_episodes)
    assert h._panel_tab_episodes.get_active()
    assert h._panel_tab == "episodes"
    assert h._panel_stack.visible == "episodes"
