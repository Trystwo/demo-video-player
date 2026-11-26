"""Beauty / face-box switches must start detection on the live clip (v4 fix).

The player ships with face detection off, so a normal launch has no NPU
warm-up and opens the clip immediately.  The beauty shader is always in the
pipeline, but its face mask comes from the detector: turning beauty on while
detection stayed off lit the switch and changed nothing on screen.  Telling
the user "it starts on the next clip" was accurate but not acceptable on a
demo table.

The window cannot be instantiated on a host without a display, so these tests
lift the handlers out of ``window.py`` and drive them with a fake backend,
GLib idle queue and hint sink.  They pin the contract: with media live and
detection off, turning beauty or the face boxes on captures the current
position and schedules one rebuild at that position; the "enabling" hint is
shown, duplicate toggles replace the queued rebuild, and turning the switches
off again cancels it.
"""

from __future__ import annotations

import ast
import types
from pathlib import Path
from typing import Any, Callable

WINDOW_PY = (
    Path(__file__).resolve().parents[1] / "src" / "video_player" / "ui" / "window.py"
)

METHODS = (
    "_detection_needed",
    "_sync_detection_need",
    "_on_detection_dependency_toggled",
    "_cancel_detection_reopen",
    "_reopen_for_detection",
    "_resume_target_ns",
    "_on_show_faces_toggled",
)


# -- source loading ---------------------------------------------------------


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


# -- stand-ins --------------------------------------------------------------


class FakeGLib:
    """Records idle sources so a test can run or cancel them explicitly."""

    SOURCE_REMOVE = False

    def __init__(self) -> None:
        self.pending: dict[int, tuple[Callable[..., Any], tuple[Any, ...]]] = {}
        self.removed: list[int] = []
        self._next_id = 1

    def idle_add(self, callback: Callable[..., Any], *args: Any) -> int:
        source_id = self._next_id
        self._next_id += 1
        self.pending[source_id] = (callback, args)
        return source_id

    def source_remove(self, source_id: int) -> bool:
        self.removed.append(source_id)
        self.pending.pop(source_id, None)
        return True

    def run(self, source_id: int) -> Any:
        callback, args = self.pending.pop(source_id)
        return callback(*args)


class FakeSwitch:
    def __init__(self, active: bool = False) -> None:
        self._active = bool(active)

    def get_active(self) -> bool:
        return self._active

    def set_active(self, active: bool) -> None:
        self._active = bool(active)


class FakeFaceOverlay:
    def __init__(self) -> None:
        self.calls: list[tuple[tuple[Any, ...], float, float]] = []

    def set_faces(self, faces: Any, width: float, height: float) -> None:
        self.calls.append((tuple(faces), width, height))


class FakeBackend:
    def __init__(
        self,
        *,
        has_media: bool = True,
        position_ns: int = 7_500_000_000,
    ) -> None:
        self.has_media = has_media
        self.position = position_ns
        self.video_info = {"width": 1920, "height": 1080}

    def position_ns(self) -> int:
        return self.position


class FakeResume:
    def __init__(self, stored_ns: int) -> None:
        self.stored_ns = stored_ns

    def load(self, _path: Path) -> int:
        return self.stored_ns


class Harness:
    """A stand-in window holding just what the detection-restart path touches."""

    def __init__(
        self,
        *,
        has_media: bool = True,
        position_ns: int = 7_500_000_000,
    ) -> None:
        self.glib = FakeGLib()
        namespace: dict[str, Any] = {
            "Path": Path,
            "GLib": self.glib,
            "log_action": self._log_action,
        }
        for source in _load_methods().values():
            exec(compile(source, "<window.py>", "exec"), namespace)  # noqa: S102
        for name in METHODS:
            setattr(self, name, types.MethodType(namespace[name], self))

        self.actions: list[tuple[str, dict[str, Any]]] = []
        self.hints: list[tuple[str | None, dict[str, Any]]] = []
        self.hint_text: str | None = None
        self.play_calls: list[tuple[Path, int | None, bool]] = []

        self.path = Path("/media/face-clip.mp4")
        self._playback_path: Path | None = self.path
        self._current_path: Path | None = self.path
        self._detection_opted_in = False
        self.demo = False
        self.config = types.SimpleNamespace(
            beauty=types.SimpleNamespace(enabled=False),
            detection=types.SimpleNamespace(enabled=False),
        )
        self._show_faces = False
        self._updating_toggles = False
        self._faces: tuple[Any, ...] = ()
        self._face_overlay = FakeFaceOverlay()
        self.backend = FakeBackend(
            has_media=has_media, position_ns=position_ns
        )
        self._resume: FakeResume | None = None
        self._closed = False
        self._detection_reopen_id = 0
        self._hint_key: str | None = None

    # Dependencies the extracted methods call.
    def _log_action(self, action: str, **values: Any) -> None:
        self.actions.append((action, values))

    def _set_hint_key(self, key: str | None, **values: Any) -> None:
        self._hint_key = key
        self._hint_text = None
        self.hints.append((key, values))

    def _play_path(
        self,
        path: Path,
        *,
        start_position_ns: int | None = None,
        keep_hint: bool = False,
    ) -> None:
        self.play_calls.append((path, start_position_ns, keep_hint))

    # Helpers.
    def schedule(self) -> int:
        self._on_detection_dependency_toggled()
        assert self._detection_reopen_id in self.glib.pending
        return self._detection_reopen_id


# -- scheduling -------------------------------------------------------------


def test_beauty_on_live_clip_schedules_one_position_preserving_reopen() -> None:
    h = Harness(position_ns=11_250_000_000)
    h.config.beauty.enabled = True

    source_id = h.schedule()

    assert h.hints == [("detection_enabling", {})]
    callback, args = h.glib.pending[source_id]
    assert args == (h.path, 11_250_000_000), "position is captured before teardown"
    assert h.play_calls == [], "never rebuild inside the switch's own signal"

    assert callback(*args) is False

    assert h.play_calls == [(h.path, 11_250_000_000, True)]
    assert h._detection_reopen_id == 0


def test_face_boxes_on_live_clip_schedules_the_same_reopen() -> None:
    h = Harness(position_ns=2_000_000_000)

    h._on_show_faces_toggled(FakeSwitch(True), None)

    assert h._show_faces is True
    assert h.hints == [("detection_enabling", {})]
    assert len(h.glib.pending) == 1
    source_id = h._detection_reopen_id
    assert h.glib.pending[source_id][1] == (h.path, 2_000_000_000)


def test_detection_already_enabled_does_not_reopen() -> None:
    h = Harness()
    h.config.detection.enabled = True
    h.config.beauty.enabled = True

    h._on_detection_dependency_toggled()

    assert h.glib.pending == {}
    assert h.hints == []
    assert h.play_calls == []


def test_no_media_leaves_detection_to_the_next_open() -> None:
    h = Harness(has_media=False)
    h.config.beauty.enabled = True

    h._on_detection_dependency_toggled()

    assert h.glib.pending == {}
    assert h.hints == [], "the old 'next clip' warning is no longer needed"
    assert h.play_calls == []


def test_second_switch_replaces_the_queued_rebuild() -> None:
    h = Harness(position_ns=1_000_000_000)
    h.config.beauty.enabled = True
    first = h.schedule()

    h.backend.position = 1_500_000_000
    second = h.schedule()

    assert first in h.glib.removed
    assert first not in h.glib.pending
    assert list(h.glib.pending) == [second]
    assert h.glib.pending[second][1] == (h.path, 1_500_000_000)


# -- cancellation / stale callbacks ----------------------------------------


def test_turning_the_last_dependent_switch_off_cancels_and_hides_the_hint() -> None:
    h = Harness()
    h.config.beauty.enabled = True
    source_id = h.schedule()
    assert h._hint_key == "detection_enabling"

    h.config.beauty.enabled = False
    h._on_detection_dependency_toggled()

    assert source_id in h.glib.removed
    assert h._detection_reopen_id == 0
    assert h.hints[-1] == (None, {})
    assert h.play_calls == []


def test_stale_idle_callback_skips_when_the_switch_went_off() -> None:
    h = Harness()
    h.config.beauty.enabled = True
    source_id = h.schedule()
    callback, args = h.glib.pending[source_id]

    h.config.beauty.enabled = False
    assert callback(*args) is False

    assert h.play_calls == []
    assert h._detection_reopen_id == 0


def test_stale_idle_callback_skips_when_another_clip_opened() -> None:
    h = Harness()
    h.config.beauty.enabled = True
    source_id = h.schedule()
    callback, args = h.glib.pending[source_id]

    h._playback_path = Path("/media/another.mp4")

    assert callback(*args) is False
    assert h.play_calls == []


def test_stale_idle_callback_skips_after_the_window_closed() -> None:
    h = Harness()
    h.config.beauty.enabled = True
    source_id = h.schedule()
    callback, args = h.glib.pending[source_id]

    h._closed = True

    assert callback(*args) is False
    assert h.play_calls == []


# -- start position ---------------------------------------------------------


def test_explicit_position_beats_the_resume_store() -> None:
    h = Harness()
    h._resume = FakeResume(4_000_000_000)

    assert h._resume_target_ns(h.path, 11_000_000_000) == 11_000_000_000
    assert h._resume_target_ns(h.path, 0) == 0
    assert h._resume_target_ns(h.path, None) == 4_000_000_000


def test_position_falls_back_to_zero_without_a_resume_store() -> None:
    h = Harness()

    assert h._resume_target_ns(h.path, None) == 0
    assert h._resume_target_ns(h.path, -5) == 0


def test_sync_detection_need_follows_every_dependent_switch() -> None:
    h = Harness()

    h._sync_detection_need()
    assert h.config.detection.enabled is False
    h.config.beauty.enabled = True
    h._sync_detection_need()
    assert h.config.detection.enabled is True

    h.config.beauty.enabled = False
    h._show_faces = True
    h._sync_detection_need()
    assert h.config.detection.enabled is True

    h._show_faces = False
    h._detection_opted_in = True
    h._sync_detection_need()
    assert h.config.detection.enabled is True
