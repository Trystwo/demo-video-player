"""Page shell (browse shell <-> player) wiring, testable without GTK.

The v5 shell is one outer ``Gtk.Stack`` whose browse child is a second stack
(home / library / detail).  What matters is the window-side contract, not the
widgets: entering the browse shell stops the clip and lets the shell restore
the page the player was opened from, entering the player remembers that page
and suspends the browsing workers, a card's list becomes the playlist, and the
header follows whichever browse page is on screen.  These tests lift the page
methods out of ``window.py`` and drive them through stand-ins, like
``test_sidebar_pages`` does for the sidebar tabs.
"""

from __future__ import annotations

import ast
from pathlib import Path
import types
from typing import Any

import pytest

from video_player.playlist import Playlist

WINDOW_PY = (
    Path(__file__).resolve().parents[1] / "src" / "video_player" / "ui" / "window.py"
)

METHODS = (
    "_show_page",
    "_refresh_browse",
    "_sync_page_chrome",
    "_on_library_changed",
    "_on_library_dropped",
    "_on_back_clicked",
    "_play_paths",
    "_on_key_pressed",
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


def _load_module_constant(name: str) -> Any:
    _, tree = _read_source()
    for node in tree.body:
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        target = node.targets[0]
        if isinstance(target, ast.Name) and target.id == name:
            return ast.literal_eval(node.value)
    raise AssertionError(f"{name} not found in window.py")


class FakeLabel:
    def __init__(self) -> None:
        self.text = ""
        self.visible = True

    def set_text(self, text: str) -> None:
        self.text = text

    def get_text(self) -> str:
        return self.text

    def set_visible(self, visible: bool) -> None:
        self.visible = bool(visible)

    def get_visible(self) -> bool:
        return self.visible


class FakeStack:
    def __init__(self) -> None:
        self.visible_page: str | None = None

    def set_visible_child_name(self, name: str) -> None:
        self.visible_page = name


class FakeBackend:
    def __init__(self, has_media: bool = False) -> None:
        self.has_media = has_media


class FakeBrowse:
    """Stand-in for BrowseShell: records the window-facing calls."""

    def __init__(self) -> None:
        self.page = "home"
        self.activated = 0
        self.marked = 0
        self.suspended = 0
        self.refreshed = 0
        self.shut = 0
        self.back_from_detail_calls = 0
        self.library = types.SimpleNamespace()

    def activate(self) -> None:
        self.activated += 1

    def mark_player_origin(self) -> None:
        self.marked += 1

    def suspend(self) -> None:
        self.suspended += 1

    def refresh(self) -> None:
        self.refreshed += 1

    def shutdown(self) -> None:
        self.shut += 1

    def headline(self) -> tuple[str, str]:
        if self.page == "detail":
            return ("面孔档案 · 第三集.mp4", "片库 · 2 个文件")
        return ("视频播放器", "12 项 · 1 个目录")

    def is_detail(self) -> bool:
        return self.page == "detail"

    def back_from_detail(self) -> None:
        self.back_from_detail_calls += 1
        self.page = "home"


class FakeGLib:
    SOURCE_REMOVE = 0

    def __init__(self) -> None:
        self.idle: list[Any] = []

    def idle_add(self, callback: Any, *args: Any) -> int:
        self.idle.append((callback, args))
        return len(self.idle)


class Harness:
    def __init__(self, *, has_media: bool = False, page: str = "player") -> None:
        self.logs: list[tuple[str, dict[str, Any]]] = []
        glib = FakeGLib()
        namespace: dict[str, Any] = {
            "PAGE_NAMES": _load_module_constant("PAGE_NAMES"),
            "Path": Path,
            "Playlist": Playlist,
            "Gdk": _gdk(),
            "Gtk": types.SimpleNamespace(Button=object),
            "GLib": glib,
            "GST_SECOND": 1_000_000_000,
            "log_action": self._log_action,
        }
        for source in _load_methods().values():
            exec(compile(source, "<window.py>", "exec"), namespace)  # noqa: S102
        for name in METHODS:
            setattr(self, name, types.MethodType(namespace[name], self))

        self.glib = glib
        self._page = page
        self._closed = False
        self.backend = FakeBackend(has_media)
        self._current_path = Path("/lib/a.mp4") if has_media else None
        self._stack = FakeStack()
        self._browse = FakeBrowse()
        self._back_button = FakeLabel()
        self._sidebar_button = FakeLabel()
        self._title_label = FakeLabel()
        self._header_subtitle_label = FakeLabel()
        self._header_subtitle_cache = ""
        self._t = lambda key, **values: {
            "app_title": "视频播放器",
            "library_back": "返回片库",
        }.get(key, key)
        self.playlist = Playlist()
        self.played: list[Path] = []
        self.returned = 0
        self.titles_updated = 0
        self.set_window_title = ""

    # -- stubs -------------------------------------------------------------

    def _log_action(self, action: str, **values: Any) -> None:
        self.logs.append((action, values))

    def _return_to_playlist(self) -> None:
        self.returned += 1
        self.backend.has_media = False
        self._current_path = None

    def _update_title(self) -> None:
        self.titles_updated += 1

    def _play_path(self, path: Path, **_kwargs: Any) -> None:
        self.played.append(path)

    def set_title(self, title: str) -> None:
        self.set_window_title = title

    def _refresh_browse(self) -> bool:
        self._browse.refresh()
        return False

    # -- key-handler stubs -------------------------------------------------

    def _show_dock(self) -> None:
        pass

    def is_fullscreen(self) -> bool:
        return False

    def _on_play_clicked(self, _button: Any) -> None:
        pass

    def _on_fullscreen_clicked(self, _button: Any) -> None:
        pass

    # -- helpers -----------------------------------------------------------

    def page(self) -> str:
        return self._page

    def actions(self) -> list[str]:
        return [action for action, _ in self.logs]


def _gdk() -> Any:
    gi = pytest.importorskip("gi")
    gi.require_version("Gdk", "4.0")
    from gi.repository import Gdk  # noqa: PLC0415

    return Gdk


def test_page_names_are_the_two_shell_halves() -> None:
    assert _load_module_constant("PAGE_NAMES") == ("browse", "player")


def test_window_uses_the_browse_shell_not_a_bare_library_page() -> None:
    text = WINDOW_PY.read_text(encoding="utf-8")
    assert "from .browse import BrowseShell" in text
    assert "from .library_page import LibraryPage" not in text
    assert "self._browse = BrowseShell(" in text
    assert 'self._stack.add_named(self._browse, "browse")' in text
    # The alias the QC probes and page tests use.
    assert "self._library = self._browse.library" in text


def test_unknown_page_is_rejected() -> None:
    h = Harness()
    with pytest.raises(ValueError):
        h._show_page("settings")
    assert h.page() == "player"


def test_entering_browse_stops_playback_and_lets_the_shell_restore_its_page() -> None:
    h = Harness(has_media=True)
    h._show_page("browse")
    assert h.page() == "browse"
    assert h._stack.visible_page == "browse"
    assert h.returned == 1
    assert h._browse.activated == 1
    # The browse shell is the one that owns the probe workers now.
    assert h._browse.suspended == 0
    assert h._back_button.get_visible() is False
    assert h._sidebar_button.get_visible() is False
    assert h._title_label.get_text() == "视频播放器"
    assert h._header_subtitle_label.get_text() == "12 项 · 1 个目录"
    assert h.set_window_title == "视频播放器"
    # The scan is deferred to idle so the page can paint first.
    assert len(h.glib.idle) == 1
    callback, _args = h.glib.idle[0]
    callback()
    assert h._browse.refreshed == 1
    assert h.actions().count("page") == 1


def test_entering_browse_always_clears_the_player_state() -> None:
    # Without media the clearing call is a no-op, but it still runs: the error
    # card and a stale recording overlay must not survive a page switch.
    h = Harness(has_media=False)
    h._show_page("browse")
    assert h.returned == 1
    assert h._browse.activated == 1


def test_entering_player_remembers_the_origin_and_suspends_the_shell() -> None:
    h = Harness(page="browse")
    h._show_page("player")
    assert h.page() == "player"
    assert h._stack.visible_page == "player"
    assert h._browse.marked == 1
    assert h._back_button.get_visible() is True
    assert h._sidebar_button.get_visible() is True
    assert h.titles_updated == 1
    assert h._browse.suspended == 1
    # The player page does not rescan: the shell is not visible.
    assert h.glib.idle == []


def test_same_page_is_not_logged_twice() -> None:
    h = Harness(page="player")
    h._show_page("player")
    assert h.actions() == []
    h._show_page("browse")
    h._show_page("browse")
    assert h.actions().count("page") == 1


def test_playing_a_list_adopts_it_as_the_playlist() -> None:
    h = Harness(page="browse")
    paths = [Path("/lib/c.mp4"), Path("/lib/a.mp4"), Path("/lib/b.mp4")]
    h._play_paths(paths, 1)
    assert list(h.playlist.files) == paths
    assert h.playlist.index == 1
    assert h.page() == "player"
    assert h.played == [paths[1]]
    assert h._browse.marked == 1
    assert "browse_play" in h.actions()


def test_playing_a_row_clamps_an_out_of_range_index() -> None:
    h = Harness(page="home")
    paths = [Path("/lib/a.mp4"), Path("/lib/b.mp4")]
    h._play_paths(paths, 9)
    assert h.playlist.index == 1
    assert h.played == [paths[1]]


def test_playing_an_empty_list_is_a_no_op() -> None:
    h = Harness(page="browse")
    h._play_paths([], 0)
    assert h.page() == "browse"
    assert h.played == []


def test_dropped_video_files_become_the_playlist() -> None:
    h = Harness(page="browse")
    paths = [Path("/drop/one.mp4"), Path("/drop/two.mkv")]
    h._on_library_dropped(paths)
    assert list(h.playlist.files) == paths
    assert h.played == [paths[0]]
    assert "library_drop" in h.actions()


def test_chrome_browse_branch_uses_the_shell_headline() -> None:
    h = Harness(page="browse")
    h._sync_page_chrome()
    assert h.titles_updated == 0
    assert h._title_label.get_text() == "视频播放器"
    assert h._header_subtitle_label.get_text() == "12 项 · 1 个目录"
    assert h._back_button.get_visible() is False


def test_chrome_detail_branch_shows_the_clip_title() -> None:
    h = Harness(page="browse")
    h._browse.page = "detail"
    h._sync_page_chrome()
    assert h._title_label.get_text() == "面孔档案 · 第三集.mp4"
    assert h._header_subtitle_label.get_text() == "片库 · 2 个文件"
    # The window title stays the product name even on a detail page.
    assert h.set_window_title == "视频播放器"


def test_chrome_player_branch_calls_update_title() -> None:
    h = Harness(page="player")
    h._sync_page_chrome()
    assert h.titles_updated == 1
    assert h._back_button.get_visible() is True
    assert h._sidebar_button.get_visible() is True


def test_library_changes_only_refresh_the_header_on_the_browse_page() -> None:
    h = Harness(page="browse")
    h._on_library_changed()
    assert h._header_subtitle_label.get_text() == "12 项 · 1 个目录"
    h._page = "player"
    before = h._header_subtitle_label.get_text()
    h._on_library_changed()
    assert h._header_subtitle_label.get_text() == before


def test_back_button_lands_on_the_browse_shell() -> None:
    h = Harness(has_media=True)
    h._on_back_clicked(None)
    assert h.page() == "browse"
    assert h.returned == 1
    assert h._browse.activated == 1
    assert "back_to_browse" in h.actions()


def test_escape_on_the_detail_page_walks_back_instead_of_leaving_browse() -> None:
    gdk = _gdk()
    h = Harness(page="browse")
    h._browse.page = "detail"
    assert h._on_key_pressed(
        None, gdk.KEY_Escape, 0, gdk.ModifierType(0)
    )
    assert h._browse.back_from_detail_calls == 1
    assert h.page() == "browse"
    # Back on home/library Escape is harmless and not consumed.
    h._browse.page = "home"
    assert not h._on_key_pressed(None, gdk.KEY_Escape, 0, gdk.ModifierType(0))


def test_escape_on_the_player_page_returns_to_browse() -> None:
    gdk = _gdk()
    h = Harness(page="player")
    assert h._on_key_pressed(None, gdk.KEY_Escape, 0, gdk.ModifierType(0))
    assert h.page() == "browse"
    assert h._browse.activated == 1
