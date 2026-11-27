"""The P4 beauty drawer entrance, testable without GTK or a display.

The beauty controls moved from the v4 sidebar page into a right-hand overlay
drawer, but the contract stays: the dock pill is the drawer entrance and its
checked state/``美颜 · 开/关`` label still mirror the effect, while the drawer
itself is purely presentation.  These tests lift the two drawer methods out of
``window.py`` and drive them through stand-ins.
"""

from __future__ import annotations

import ast
import types
from pathlib import Path
from typing import Any

WINDOW_PY = (
    Path(__file__).resolve().parents[1] / "src" / "video_player" / "ui" / "window.py"
)

METHODS = (
    "_set_beauty_drawer_open",
    "_toggle_beauty_drawer",
    "_on_beauty_pill_clicked",
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


class FakeRevealer:
    def __init__(self) -> None:
        self.revealed = False

    def set_reveal_child(self, revealed: bool) -> None:
        self.revealed = bool(revealed)

    def get_reveal_child(self) -> bool:
        return self.revealed


class FakeToggle:
    def __init__(self) -> None:
        self.active = False
        self.set_active_calls: list[bool] = []

    def get_active(self) -> bool:
        return self.active

    def set_active(self, active: bool) -> None:
        self.set_active_calls.append(bool(active))
        self.active = bool(active)


class Harness:
    def __init__(self, enabled: bool = False) -> None:
        self.actions: list[tuple[str, dict[str, Any]]] = []
        namespace: dict[str, Any] = {"log_action": self._log_action}
        for source in _load_methods().values():
            exec(compile(source, "<window.py>", "exec"), namespace)  # noqa: S102
        for name in METHODS:
            setattr(self, name, types.MethodType(namespace[name], self))

        self.config = types.SimpleNamespace(
            beauty=types.SimpleNamespace(enabled=enabled)
        )
        self._drawer_open = False
        self._drawer_revealer = FakeRevealer()
        self._updating_toggles = False
        self._episodes_open = False
        self._sync_episodes_layout = lambda: None
        self._dock_beauty_button = FakeToggle()
        self._dock_beauty_button.active = enabled

    def _log_action(self, action: str, **values: Any) -> None:
        self.actions.append((action, values))

    def drawer_actions(self) -> list[dict[str, Any]]:
        return [values for action, values in self.actions if action == "beauty_drawer"]


def test_drawer_starts_closed_and_opens_once() -> None:
    h = Harness()
    assert not h._drawer_open
    assert not h._drawer_revealer.get_reveal_child()

    h._set_beauty_drawer_open(True)
    assert h._drawer_open
    assert h._drawer_revealer.get_reveal_child()
    assert h.drawer_actions() == [{"open": True}]

    # Idempotent: no second log line while the state is unchanged.
    h._set_beauty_drawer_open(True)
    assert h.drawer_actions() == [{"open": True}]


def test_toggle_flips_the_drawer_each_time() -> None:
    h = Harness()
    h._toggle_beauty_drawer()
    assert h._drawer_open
    h._toggle_beauty_drawer()
    assert not h._drawer_open
    assert h.drawer_actions() == [{"open": True}, {"open": False}]


def test_pill_click_restores_effect_state_and_opens_the_drawer() -> None:
    h = Harness(enabled=True)
    # A ToggleButton flips before the clicked handler runs; the pill must reset
    # to the effect state and open the drawer instead of changing beauty.
    h._dock_beauty_button.active = False
    h._on_beauty_pill_clicked(h._dock_beauty_button)

    assert h._dock_beauty_button.get_active() is True
    assert h._dock_beauty_button.set_active_calls == [True]
    assert h._drawer_open
    assert h._updating_toggles is False


def test_pill_click_closes_an_open_drawer_without_touching_beauty() -> None:
    h = Harness(enabled=False)
    h._set_beauty_drawer_open(True)
    h._on_beauty_pill_clicked(h._dock_beauty_button)

    assert h._dock_beauty_button.get_active() is False
    assert not h._drawer_open
    assert h.config.beauty.enabled is False


def test_drawer_does_not_open_while_the_panel_owns_the_page() -> None:
    # Windowed playback hosts the beauty page in the panel tab; the pill is
    # hidden there, so an accidental drawer request must be a no-op.
    h = Harness()
    h._secondary_placement = "panel"
    h._set_beauty_drawer_open(True)
    assert not h._drawer_open
    assert not h._drawer_revealer.get_reveal_child()
    assert h.drawer_actions() == []
