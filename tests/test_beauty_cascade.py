"""Beauty master-switch cascade, testable without GTK or a display.

The v4 page owns the whole beauty feature: turning the master switch off must
also clear the A|B split and the face boxes, grey the page, and leave the
fullscreen compare pill visible but disabled.  The window cannot be
instantiated on a host without a display, so these tests lift the cascade
methods out of ``window.py`` and drive them against stand-ins that reproduce
GTK's synchronous ``notify::active`` emission.  What matters here is the
invariant the mockup promises: no dependent control stays lit while the effect
is off, switching beauty back on does not restore them, and the guard keeps
the programmatic ``set_active`` calls from re-entering the cascade.
"""

from __future__ import annotations

import ast
import types
from pathlib import Path
from typing import Any, Callable

from video_player.ui.chrome import (
    BEAUTY_PAGE_OFF_OPACITY,
    BEAUTY_SPLIT_OFF_OPACITY,
    beauty_page_state,
)

WINDOW_PY = (
    Path(__file__).resolve().parents[1] / "src" / "video_player" / "ui" / "window.py"
)

METHODS = (
    "_set_beauty_enabled",
    "_set_compare_enabled",
    "_sync_beauty_toggles",
    "_sync_compare_toggles",
    "_sync_beauty_page_state",
    "_refresh_beauty_status",
    "_on_show_faces_toggled",
    "_on_beauty_toggled",
    "_on_compare_toggled",
    "_on_dock_compare_toggled",
    "_toggle_compare",
)


# -- fake widgets -----------------------------------------------------------


class FakeSwitch:
    """Gtk.Switch stand-in that emits its handler on every state change."""

    def __init__(self, active: bool = False) -> None:
        self._active = active
        self.sensitive = True
        self.tooltip = ""
        self.on_changed: Callable[[FakeSwitch], None] | None = None
        self.set_calls: list[bool] = []
        self.sensitivity_calls: list[bool] = []

    def get_active(self) -> bool:
        return self._active

    def set_active(self, active: bool) -> None:
        self.set_calls.append(active)
        if self._active == active:
            return
        self._active = active
        if self.on_changed is not None:
            self.on_changed(self)

    def set_sensitive(self, sensitive: bool) -> None:
        self.sensitive = bool(sensitive)
        self.sensitivity_calls.append(bool(sensitive))

    def get_sensitive(self) -> bool:
        return self.sensitive

    def set_tooltip_text(self, text: str) -> None:
        self.tooltip = text


class FakeToggle(FakeSwitch):
    def __init__(self, active: bool = False) -> None:
        super().__init__(active)
        self.label = ""

    def set_label(self, label: str) -> None:
        self.label = label


class FakeLabel:
    def __init__(self, text: str = "") -> None:
        self.text = text
        self.visible = True

    def set_text(self, text: str) -> None:
        self.text = text

    def set_visible(self, visible: bool) -> None:
        self.visible = bool(visible)

    def get_visible(self) -> bool:
        return self.visible


class FakeContainer:
    def __init__(self) -> None:
        self.classes: set[str] = set()
        self.sensitive = True
        self.opacity = 1.0

    def add_css_class(self, name: str) -> None:
        self.classes.add(name)

    def remove_css_class(self, name: str) -> None:
        self.classes.discard(name)

    def set_sensitive(self, sensitive: bool) -> None:
        self.sensitive = bool(sensitive)

    def set_opacity(self, opacity: float) -> None:
        self.opacity = float(opacity)


class FakeFaceOverlay:
    def __init__(self) -> None:
        self.faces: tuple[Any, ...] | None = None
        self.calls: list[tuple[tuple[Any, ...], float, float]] = []

    def set_faces(self, faces, width, height) -> None:
        self.faces = tuple(faces)
        self.calls.append((tuple(faces), width, height))


# -- source loading ---------------------------------------------------------


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


# -- harness ----------------------------------------------------------------


class Harness:
    """A stand-in window holding just what the cascade methods touch."""

    def __init__(
        self,
        *,
        enabled: bool = False,
        compare: bool = False,
        faces: bool = False,
        beauty_available: bool = True,
        beauty_ms: float = 1.6,
    ) -> None:
        namespace: dict[str, Any] = {
            "beauty_page_state": beauty_page_state,
            "Gtk": types.SimpleNamespace(Switch=object),
            "log_action": self._log_action,
        }
        for source in _load_methods().values():
            exec(compile(source, "<window.py>", "exec"), namespace)  # noqa: S102
        for name in METHODS:
            setattr(self, name, types.MethodType(namespace[name], self))

        self.actions: list[tuple[str, dict[str, Any]]] = []
        self.hints: list[tuple[str, dict[str, Any]]] = []
        self.refresh_calls = 0
        self.dependency_calls = 0

        self.config = types.SimpleNamespace(
            beauty=types.SimpleNamespace(enabled=enabled, compare_mode=compare)
        )
        self._show_faces = faces
        self._updating_toggles = False
        self._faces: tuple[Any, ...] = (object(),)
        self.backend = types.SimpleNamespace(
            beauty_available=beauty_available,
            beauty_ms=beauty_ms,
            video_info={"width": 1920, "height": 1080},
        )

        self._beauty_switch = FakeSwitch(enabled)
        self._beauty_switch.on_changed = self._beauty_signal
        self._dock_beauty_button = FakeToggle(enabled)
        self._compare_switch = FakeSwitch(compare)
        self._compare_switch.on_changed = self._compare_signal
        self._dock_compare_button = FakeToggle(compare)
        self._dock_compare_button.on_changed = self._dock_compare_signal
        self._faces_switch = FakeSwitch(faces)
        self._faces_switch.on_changed = self._faces_signal
        self._face_overlay = FakeFaceOverlay()

        self._beauty_page = FakeContainer()
        self._beauty_body = FakeContainer()
        self._beauty_cascade = FakeLabel()
        self._note_label = FakeLabel()
        self._split_row = FakeContainer()
        self._beauty_status_label = FakeLabel()

    # Signals: the extracted handlers are bound, so the guard under test is the
    # real one rather than a copy in the harness.
    def _beauty_signal(self, switch: FakeSwitch) -> None:
        self._on_beauty_toggled(switch, None)

    def _compare_signal(self, switch: FakeSwitch) -> None:
        self._on_compare_toggled(switch, None)

    def _dock_compare_signal(self, button: FakeToggle) -> None:
        self._on_dock_compare_toggled(button)

    def _faces_signal(self, switch: FakeSwitch) -> None:
        self._on_show_faces_toggled(switch, None)

    # Dependencies the extracted methods call.
    def _log_action(self, action: str, **values: Any) -> None:
        self.actions.append((action, values))

    def _set_hint_key(self, key: str, **values: Any) -> None:
        self.hints.append((key, values))

    def _refresh_beauty(self) -> None:
        self.refresh_calls += 1

    def _on_detection_dependency_toggled(self) -> None:
        self.dependency_calls += 1

    def _t(self, key: str, **values: Any) -> str:
        if values:
            rendered = ",".join(f"{k}={v}" for k, v in sorted(values.items()))
            return f"{key}({rendered})"
        return key

    # Helpers.
    def activate_beauty(self, enabled: bool) -> None:
        self._set_beauty_enabled(enabled)

    def beauty_actions(self) -> list[dict[str, Any]]:
        return [values for action, values in self.actions if action == "beauty"]

    def compare_actions(self) -> list[dict[str, Any]]:
        return [values for action, values in self.actions if action == "compare"]


def test_disabling_beauty_cascades_split_faces_and_dock_pill() -> None:
    h = Harness(enabled=True, compare=True, faces=True)
    h._face_overlay.set_faces(h._faces, 1920.0, 1080.0)

    h.activate_beauty(False)

    assert h.config.beauty.enabled is False
    assert h.config.beauty.compare_mode is False
    assert h._show_faces is False
    assert h._compare_switch.get_active() is False
    assert h._dock_compare_button.get_active() is False
    assert h._faces_switch.get_active() is False
    assert h._face_overlay.faces == (), "the L brackets must disappear at once"
    assert h._beauty_switch.get_active() is False
    assert h._dock_beauty_button.get_active() is False

    # Page chrome (v4 mockup): greyed body, cascade notice, note hidden.
    assert "is-off" in h._beauty_page.classes
    assert h._beauty_body.sensitive is False
    assert h._beauty_body.opacity == BEAUTY_PAGE_OFF_OPACITY
    assert h._beauty_cascade.get_visible() is True
    assert h._note_label.get_visible() is False
    assert h._split_row.sensitive is False
    assert h._split_row.opacity == BEAUTY_SPLIT_OFF_OPACITY

    # Dock entrances: compare disabled with the "needs beauty" label.
    assert h._dock_compare_button.get_sensitive() is False
    assert h._dock_compare_button.label == "compare_needs_beauty"
    assert h._dock_beauty_button.label == "beauty_pill_off"

    assert h.beauty_actions() == [{"enabled": False, "cascade": True}]
    assert h.refresh_calls == 1, "the cascade must not re-enter itself"
    assert h._updating_toggles is False


def test_reenabling_beauty_does_not_restore_split_or_boxes() -> None:
    h = Harness(enabled=True, compare=True, faces=True)
    h.activate_beauty(False)
    h.activate_beauty(True)

    assert h.config.beauty.enabled is True
    assert h.config.beauty.compare_mode is False
    assert h._show_faces is False
    assert h._compare_switch.get_active() is False
    assert h._dock_compare_button.get_active() is False
    assert h._faces_switch.get_active() is False

    assert "is-off" not in h._beauty_page.classes
    assert h._beauty_body.sensitive is True
    assert h._beauty_body.opacity == 1.0
    assert h._beauty_cascade.get_visible() is False
    assert h._note_label.get_visible() is True
    assert h._dock_compare_button.get_sensitive() is True
    assert h._dock_compare_button.label == "compare"
    assert h._dock_beauty_button.label == "beauty_pill_on"


def test_compare_is_gated_while_beauty_is_off() -> None:
    h = Harness(enabled=False)

    h._set_compare_enabled(True)

    assert h.config.beauty.compare_mode is False
    assert h._compare_switch.get_active() is False
    assert h._dock_compare_button.get_active() is False
    assert h.hints == [("compare_requires_beauty", {})]
    assert h.compare_actions() == []
    assert h.refresh_calls == 0

    h.activate_beauty(True)
    h._set_compare_enabled(True)
    assert h.config.beauty.compare_mode is True
    assert h._compare_switch.get_active() is True
    assert h._split_row.sensitive is True
    assert h._split_row.opacity == 1.0
    assert h.compare_actions() == [{"enabled": True}]


def test_compare_shortcut_uses_the_same_gate() -> None:
    h = Harness(enabled=False)
    h._toggle_compare()
    assert h._compare_switch.get_active() is False
    assert h.hints == [("compare_requires_beauty", {})]

    h.activate_beauty(True)
    h._toggle_compare()
    assert h._compare_switch.get_active() is True
    assert h.config.beauty.compare_mode is True

    h._toggle_compare()
    assert h._compare_switch.get_active() is False
    assert h.config.beauty.compare_mode is False


def test_disabling_compare_only_dims_the_split_row() -> None:
    h = Harness(enabled=True, compare=True, faces=True)

    h._set_compare_enabled(False)

    assert h.config.beauty.enabled is True
    assert h.config.beauty.compare_mode is False
    assert h._beauty_body.sensitive is True
    assert h._beauty_body.opacity == 1.0
    assert "is-off" not in h._beauty_page.classes
    assert h._split_row.sensitive is False
    assert h._split_row.opacity == BEAUTY_SPLIT_OFF_OPACITY
    assert h._dock_compare_button.get_sensitive() is True
    assert h._dock_compare_button.label == "compare"
    assert h._faces_switch.get_active() is True, "beauty itself is still on"


def test_cascade_marks_what_it_actually_turned_off() -> None:
    h = Harness(enabled=True, compare=False, faces=False)
    h.activate_beauty(False)
    assert h.beauty_actions() == [{"enabled": False, "cascade": False}]

    h = Harness(enabled=True, compare=False, faces=True)
    h.activate_beauty(False)
    assert h.beauty_actions() == [{"enabled": False, "cascade": True}]


def test_faces_switch_ignores_the_programmatic_cascade_echo() -> None:
    h = Harness(enabled=True, faces=True)
    h._updating_toggles = True
    h._faces_switch.set_active(False)
    # The handler is guarded: the cascade owns this change, so _show_faces is
    # not touched and no user action is logged here.
    assert h._show_faces is True
    assert not [a for a, _ in h.actions if a == "show_faces"]

    h._updating_toggles = False
    h._faces_switch.set_active(True)
    assert h._show_faces is True
    assert [values for a, values in h.actions if a == "show_faces"] == [
        {"enabled": True}
    ]


def test_status_line_tracks_gpu_availability() -> None:
    h = Harness(enabled=False)
    h._refresh_beauty_status()
    assert h._beauty_status_label.text == "beauty_status_off"

    h = Harness(enabled=True, beauty_available=True, beauty_ms=1.6)
    h._refresh_beauty_status()
    assert h._beauty_status_label.text == "beauty_status_on(ms=1.6)"

    h = Harness(enabled=True, beauty_available=False)
    h._refresh_beauty_status()
    assert h._beauty_status_label.text == "beauty_status_on_pending"
