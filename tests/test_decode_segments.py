"""Decode-mode segments, testable without GTK or a display.

The three segments are a GtkToggleButton radio group: GTK deactivates the
previously active member first, emitting its ``toggled`` signal, and only then
activates the clicked one.  The old handler reacted to the deactivation by
calling ``set_active(True)`` again, which fought the group and left the row
with two active segments -- the first click appeared to work and every later
click was swallowed.  These tests lift the four handlers out of ``window.py``
and drive them through a stand-in that reproduces that signal order.
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
    "_on_decode_toggled",
    "_sync_decode_buttons",
    "_apply_decode_mode",
    "_reopen_for_decode_mode",
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


class FakeGLib:
    """Record idle sources instead of running a real main loop."""

    SOURCE_REMOVE = False

    def __init__(self) -> None:
        self.pending: dict[int, tuple[Any, tuple[Any, ...]]] = {}
        self.next_id = 1
        self.removed: list[int] = []

    def idle_add(self, func: Any, *args: Any) -> int:
        source_id = self.next_id
        self.next_id += 1
        self.pending[source_id] = (func, args)
        return source_id

    def source_remove(self, source_id: int) -> None:
        self.removed.append(source_id)
        self.pending.pop(source_id, None)

    def run_pending(self) -> None:
        for source_id, (func, args) in list(self.pending.items()):
            self.pending.pop(source_id, None)
            func(*args)


class FakeToggleButton:
    """Minimal radio-group member with GTK's toggled emission order."""

    def __init__(self, mode: str) -> None:
        self.mode = mode
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
            # GTK deactivates the other group members first (each emits its
            # own "toggled"), then activates the clicked button.
            for other in self.group:
                if other is not self and other._active:
                    other.set_active(False)
        self._active = active
        if self.on_toggled is not None:
            self.on_toggled(self, self.mode)

    def click(self) -> None:
        # Clicking the active member of a radio group does not deactivate it.
        if self._active:
            return
        self.set_active(True)


class Harness:
    """Stand-in window holding only what the decode handlers touch."""

    def __init__(self, *, has_media: bool = True) -> None:
        self.glib = FakeGLib()
        namespace: dict[str, Any] = {
            "GLib": self.glib,
            "Gtk": types.SimpleNamespace(ToggleButton=object),
            "Path": Path,
            "log_action": lambda *args, **kwargs: None,
        }
        for source in _load_methods().values():
            exec(compile(source, "<window.py>", "exec"), namespace)  # noqa: S102
        for name in METHODS:
            setattr(self, name, types.MethodType(namespace[name], self))

        self.config = types.SimpleNamespace(
            player=types.SimpleNamespace(decode_mode="auto")
        )
        self._updating_decode = False
        self._decode_reopen_id = 0
        self._closed = False
        self.backend = types.SimpleNamespace(has_media=has_media)
        self._playback_path = Path("/tmp/clip.mp4")
        self._current_path = self._playback_path
        self.hints: list[tuple[str, dict[str, Any]]] = []
        self.reopened: list[Path] = []

        self._decode_buttons = {
            mode: FakeToggleButton(mode) for mode in ("auto", "hw", "sw")
        }
        buttons = list(self._decode_buttons.values())
        for button in buttons:
            button.group = buttons
        # _build_decode_segments() sets the initial state before connecting.
        self._decode_buttons["auto"].set_active(True)
        for button in buttons:
            button.on_toggled = self._on_decode_toggled

    def _t(self, key: str) -> str:
        return key

    def _set_hint_key(self, key: str, **kwargs: Any) -> None:
        self.hints.append((key, kwargs))

    def _play_path(self, path: Path) -> None:
        self.reopened.append(path)

    def click(self, mode: str) -> None:
        self._decode_buttons[mode].click()

    def active_modes(self) -> list[str]:
        return [mode for mode, button in self._decode_buttons.items() if button.get_active()]

    def run_idle(self) -> None:
        self.glib.run_pending()


def test_three_consecutive_switches_each_rebuild_once() -> None:
    h = Harness()
    for mode in ("hw", "sw", "auto"):
        before = len(h.reopened)
        h.click(mode)
        assert h.config.player.decode_mode == mode
        assert h.active_modes() == [mode]
        assert len(h.glib.pending) == 1
        assert h._decode_reopen_id != 0
        assert len(h.reopened) == before, "rebuild must wait for the next main-loop turn"
        h.run_idle()
        assert len(h.reopened) == before + 1
        assert h.reopened[-1] == h._playback_path
        assert h._decode_reopen_id == 0
        assert not h.glib.pending
    assert len(h.reopened) == 3


def test_group_handoff_does_not_reactivate_previous_segment() -> None:
    h = Harness()
    auto = h._decode_buttons["auto"]
    h.click("hw")
    # [initial True, GTK's deactivation False]; the old handler appended a
    # third True here, which is what locked the group after one click.
    assert auto.set_active_calls == [True, False]
    assert h.active_modes() == ["hw"]
    assert h.config.player.decode_mode == "hw"


def test_bare_deactivation_notification_is_ignored() -> None:
    h = Harness()
    auto = h._decode_buttons["auto"]
    auto.set_active(False)
    assert h.config.player.decode_mode == "auto"
    assert not h.glib.pending
    assert auto.set_active_calls == [True, False]


def test_clicking_active_segment_does_not_rebuild() -> None:
    h = Harness()
    h.click("auto")
    assert h.config.player.decode_mode == "auto"
    assert not h.glib.pending
    assert not h.reopened


def test_programmatic_sync_is_guarded_against_reentry() -> None:
    h = Harness()
    h.config.player.decode_mode = "sw"
    h._sync_decode_buttons()
    assert h.active_modes() == ["sw"]
    assert h._updating_decode is False
    assert not h.glib.pending
    assert not h.reopened


def test_rapid_clicks_keep_only_the_latest_rebuild() -> None:
    h = Harness()
    h.click("hw")
    first = h._decode_reopen_id
    h.click("sw")
    second = h._decode_reopen_id
    assert first != second
    assert first in h.glib.removed
    assert len(h.glib.pending) == 1
    h.run_idle()
    assert len(h.reopened) == 1
    assert h.config.player.decode_mode == "sw"


def test_stale_idle_is_skipped() -> None:
    h = Harness()
    h.click("hw")
    h.config.player.decode_mode = "sw"
    h.run_idle()
    assert not h.reopened


def test_idle_after_window_close_does_not_rebuild() -> None:
    h = Harness()
    h.click("hw")
    h._closed = True
    h.run_idle()
    assert not h.reopened
    assert h._decode_reopen_id == 0


def test_no_media_stages_the_mode_without_rebuilding() -> None:
    h = Harness(has_media=False)
    h.click("hw")
    assert h.config.player.decode_mode == "hw"
    assert not h.glib.pending
    assert not h.reopened
    assert h.hints == [("decode_staged", {"mode": "decode_hw"})]
