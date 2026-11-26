"""Sidebar page tabs (playlist / beauty), testable without GTK.

The v4 sidebar puts two pages behind a GtkToggleButton radio group.  GTK
deactivates the previous member first (emitting its ``toggled``) and only then
activates the clicked one, so the handler must ignore deactivations and the
programmatic ``set_active`` calls in ``_set_sidebar_tab`` must not re-enter it.
These tests lift the two methods out of ``window.py`` and drive them through a
stand-in that reproduces that signal order.
"""

from __future__ import annotations

import ast
import types
from pathlib import Path
from typing import Any

WINDOW_PY = (
    Path(__file__).resolve().parents[1] / "src" / "video_player" / "ui" / "window.py"
)

METHODS = ("_set_sidebar_tab", "_on_sidebar_tab_toggled")


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


def _load_sidebar_pages() -> tuple[str, ...]:
    _, tree = _read_source()
    for node in tree.body:
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        target = node.targets[0]
        if isinstance(target, ast.Name) and target.id == "SIDEBAR_PAGES":
            return tuple(ast.literal_eval(node.value))
    raise AssertionError("SIDEBAR_PAGES not found in window.py")


class FakeToggleButton:
    """Radio-group member with GTK's deactivate-then-activate order."""

    def __init__(self, name: str) -> None:
        self.name = name
        self._active = False
        self.group: list[FakeToggleButton] = []
        self.on_toggled: Any = None
        self.set_active_calls: list[bool] = []

    def get_active(self) -> bool:
        return self._active

    def set_active(self, active: bool) -> None:
        self.set_active_calls.append(active)
        if self._active == active:
            return
        if active:
            for other in self.group:
                if other is not self and other._active:
                    other.set_active(False)
        self._active = active
        if self.on_toggled is not None:
            self.on_toggled(self, self.name)

    def click(self) -> None:
        if self._active:
            return
        self.set_active(True)


class FakePage:
    def __init__(self) -> None:
        self.visible = True

    def set_visible(self, visible: bool) -> None:
        self.visible = bool(visible)

    def get_visible(self) -> bool:
        return self.visible


class Harness:
    def __init__(self) -> None:
        self.logs: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []
        namespace: dict[str, Any] = {
            "SIDEBAR_PAGES": _load_sidebar_pages(),
            "log_action": self._log_action,
        }
        for source in _load_methods().values():
            exec(compile(source, "<window.py>", "exec"), namespace)  # noqa: S102
        for name in METHODS:
            setattr(self, name, types.MethodType(namespace[name], self))

        self._sidebar_tab = "playlist"
        self._updating_tabs = False
        self._tab_playlist = FakeToggleButton("playlist")
        self._tab_beauty = FakeToggleButton("beauty")
        self._tab_beauty.group = [self._tab_playlist]
        self._playlist_page = FakePage()
        self._beauty_page = FakePage()

        # _build_sidebar_tabs sets the initial state before connecting.
        self._tab_playlist.set_active(True)
        self._tab_beauty.set_active(False)
        for button in (self._tab_playlist, self._tab_beauty):
            button.on_toggled = self._on_sidebar_tab_toggled

    def _log_action(self, action: str, *args: Any, **values: Any) -> None:
        self.logs.append((action, args, values))

    def click(self, name: str) -> None:
        getattr(self, f"_tab_{name}").click()

    def active_tabs(self) -> list[str]:
        return [
            name
            for name in ("playlist", "beauty")
            if getattr(self, f"_tab_{name}").get_active()
        ]


def test_sidebar_pages_constant_lists_both_pages() -> None:
    assert _load_sidebar_pages() == ("playlist", "beauty")


def test_set_tab_shows_one_page_and_syncs_both_buttons() -> None:
    h = Harness()

    h._set_sidebar_tab("beauty")
    assert h._sidebar_tab == "beauty"
    assert h.active_tabs() == ["beauty"]
    assert h._beauty_page.get_visible() is True
    assert h._playlist_page.get_visible() is False

    h._set_sidebar_tab("playlist")
    assert h._sidebar_tab == "playlist"
    assert h.active_tabs() == ["playlist"]
    assert h._playlist_page.get_visible() is True
    assert h._beauty_page.get_visible() is False


def test_unknown_page_is_rejected() -> None:
    h = Harness()
    try:
        h._set_sidebar_tab("settings")
    except ValueError as exc:
        assert "settings" in str(exc)
    else:  # pragma: no cover - the assertion below is the real check
        raise AssertionError("unknown page name was accepted")
    assert h._sidebar_tab == "playlist"


def test_clicking_the_other_tab_switches_once() -> None:
    h = Harness()

    h.click("beauty")

    assert h._sidebar_tab == "beauty"
    assert h.active_tabs() == ["beauty"]
    assert h._beauty_page.get_visible() is True
    assert h._playlist_page.get_visible() is False
    assert [values for action, _, values in h.logs if action == "sidebar_tab"] == [
        {"tab": "beauty"}
    ]
    assert h._updating_tabs is False


def test_clicking_the_active_tab_is_a_noop() -> None:
    h = Harness()
    h.click("playlist")
    assert h.logs == []
    assert h.active_tabs() == ["playlist"]


def test_guard_stops_programmatic_set_active_from_reentering() -> None:
    h = Harness()
    h._set_sidebar_tab("beauty")
    # _set_sidebar_tab itself calls set_active on both members; the guard keeps
    # those emissions from recursively calling _set_sidebar_tab again.
    assert len([a for a, _, _ in h.logs if a == "sidebar_tab"]) == 1
    assert h._tab_playlist.set_active_calls == [True, False]
    assert h._tab_beauty.set_active_calls == [False, True]
