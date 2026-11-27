"""Windowed playback panel: gating, mutual exclusion and the chip mirror.

The panel itself is GTK; what matters is the state machine around it:
windowed playback owns a docked column, fullscreen and ``--record`` collapse it
without losing the user's choice, the beauty drawer and the panel never stack,
the 选集 chip only mirrors the panel, and the secondary controls are re-hosted
when the panel becomes available.  These tests lift the methods out of
``window.py`` and drive them with stand-ins, like ``test_player_drawer``.
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
    "_set_episodes_open",
    "_sync_episodes_layout",
    "_on_episodes_clicked",
)


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


class FakeRevealer:
    def __init__(self) -> None:
        self.reveal = False
        self.visible = True
        self.child_revealed = False
        self.visible_calls: list[bool] = []

    def set_reveal_child(self, revealed: bool) -> None:
        self.reveal = bool(revealed)
        self.child_revealed = bool(revealed)

    def get_reveal_child(self) -> bool:
        return self.reveal

    def get_child_revealed(self) -> bool:
        return self.child_revealed

    def set_visible(self, visible: bool) -> None:
        self.visible = bool(visible)
        self.visible_calls.append(bool(visible))

    def get_visible(self) -> bool:
        return self.visible


class FakeTopBar:
    def __init__(self) -> None:
        self.active = False

    def set_episodes_active(self, active: bool) -> None:
        self.active = bool(active)


class FakeVisible:
    def __init__(self) -> None:
        self.visible = True

    def set_visible(self, visible: bool) -> None:
        self.visible = bool(visible)


class FakeToggle:
    def __init__(self, active: bool = False) -> None:
        self.active = active
        self.set_active_calls: list[bool] = []

    def get_active(self) -> bool:
        return self.active

    def set_active(self, active: bool) -> None:
        self.active = bool(active)
        self.set_active_calls.append(bool(active))


class Harness:
    def __init__(
        self,
        *,
        page: str = "player",
        immersive: bool = False,
        record: Path | None = None,
        open_: bool = True,
    ) -> None:
        self.actions: list[tuple[str, dict[str, Any]]] = []
        namespace: dict[str, Any] = {"log_action": self._log_action}
        for source in _load_methods().values():
            exec(compile(source, "<window.py>", "exec"), namespace)  # noqa: S102
        for name in METHODS:
            setattr(self, name, types.MethodType(namespace[name], self))

        self._page = page
        self._immersive = immersive
        self._record_path = record
        self._episodes_open = open_
        self._drawer_open = False
        self._updating_toggles = False
        self._episodes_revealer = FakeRevealer()
        self._top_bar = FakeTopBar()
        self._episodes_button = FakeVisible()
        self.placement: list[bool] = []
        self._beauty_close_calls = 0

    def _log_action(self, action: str, **values: Any) -> None:
        self.actions.append((action, values))

    def _sync_secondary_placement(self, available: bool) -> None:
        self.placement.append(bool(available))

    def _set_beauty_drawer_open(self, opened: bool) -> None:
        self._beauty_close_calls += int(not opened)
        self._drawer_open = bool(opened)

    def episodes_actions(self) -> list[dict[str, Any]]:
        return [values for action, values in self.actions if action == "episodes_sidebar"]


def test_windowed_player_opens_the_column_and_rehosts_the_controls() -> None:
    h = Harness()
    h._sync_episodes_layout()
    assert h._episodes_revealer.get_visible()
    assert h._episodes_revealer.get_reveal_child()
    assert h._top_bar.active
    assert h.placement == [True]
    assert h._episodes_button.visible


def test_closed_column_collapses_and_keeps_the_controls_in_place() -> None:
    h = Harness(open_=False)
    h._sync_episodes_layout()
    assert h._episodes_revealer.get_visible()
    assert not h._episodes_revealer.get_reveal_child()
    assert not h._top_bar.active
    # The panel is still available, so 字幕/美颜 stay in their windowed hosts.
    assert h.placement == [True]


def test_browse_and_fullscreen_hide_the_column_without_forgetting_the_choice() -> None:
    for kwargs in (
        {"page": "browse"},
        {"immersive": True},
        {"record": Path("/tmp/out.mp4")},
    ):
        h = Harness(**kwargs)
        h._sync_episodes_layout()
        assert not h._episodes_revealer.get_visible(), kwargs
        assert not h._episodes_revealer.get_reveal_child(), kwargs
        # The user's "open" choice survives, so leaving fullscreen restores it.
        assert h._episodes_open, kwargs
        assert h.placement == [False], kwargs
        assert not h._episodes_button.visible, kwargs
        h._page = "player"
        h._immersive = False
        h._record_path = None
        h._sync_episodes_layout()
        assert h._episodes_revealer.get_reveal_child(), kwargs
        assert h.placement[-1] is True, kwargs


def test_opening_the_column_closes_the_beauty_drawer() -> None:
    h = Harness(open_=False)
    h._drawer_open = True
    h._set_episodes_open(True)
    assert not h._drawer_open
    assert h._beauty_close_calls == 1
    assert h._episodes_open
    assert h.episodes_actions() == [{"open": True}]


def test_closing_the_column_does_not_touch_the_drawer() -> None:
    h = Harness(open_=True)
    h._set_episodes_open(False)
    assert not h._episodes_open
    assert h._beauty_close_calls == 0
    assert h.episodes_actions() == [{"open": False}]


def test_same_state_is_idempotent_and_still_resyncs() -> None:
    h = Harness(open_=True)
    h._set_episodes_open(True)
    assert h.episodes_actions() == []
    assert h._episodes_revealer.get_reveal_child()


def test_chip_click_mirrors_then_toggles() -> None:
    h = Harness(open_=True)
    button = FakeToggle(active=False)  # a click already flipped the toggle
    h._on_episodes_clicked(button)
    # It first restored the mirror to the real state, then closed the column.
    assert button.set_active_calls[0] is True
    assert not h._episodes_open

    button = FakeToggle(active=True)
    h._on_episodes_clicked(button)
    assert button.set_active_calls[0] is False
    assert h._episodes_open


def test_closed_column_still_reports_the_mirror_off() -> None:
    h = Harness(open_=False)
    h._sync_episodes_layout()
    assert h._top_bar.active is False
