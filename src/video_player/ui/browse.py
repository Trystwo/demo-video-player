"""The ui-v5 browse shell: left rail + home / library / detail pages.

The window's outer :class:`Gtk.Stack` swaps between this shell and the player.
Inside the shell a second stack swaps three browse pages, all built once:

* **home** -- hero + continue watching + recently added,
* **library** -- the scanned-file grid with filters and sorting,
* **detail** -- backdrop, episodes and clip facts for one file.

The shell owns the navigation state (which page is current, where a detail page
was opened from, where "back" from the player lands), the shared backdrop poster
cache (the 1148 px frames the hero and the detail plate need) and the single
drag-and-drop target: a dropped folder joins the scan, a dropped video plays.
Page content lives in the page modules; this file only routes.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable, Sequence

import gi

gi.require_version("Gdk", "4.0")
gi.require_version("GdkPixbuf", "2.0")
gi.require_version("Gtk", "4.0")
from gi.repository import Gdk, GdkPixbuf, GLib, Gtk  # noqa: E402

from ..config import Config  # noqa: E402
from ..diagnostics import log_action  # noqa: E402
from ..i18n import Translator  # noqa: E402
from ..library import LibraryItem, VideoMetadata  # noqa: E402
from ..probe import (  # noqa: E402
    ThumbnailWorker,
    ThumbRequest,
    hardware_decode_ready,
    thumbnail_cache_path,
)
from .detail_page import DetailPage  # noqa: E402
from .home_page import HomePage  # noqa: E402
from .library_page import LibraryPage  # noqa: E402
from .rail import Rail  # noqa: E402

__all__ = ["BACKDROP_WIDTH", "BROWSE_PAGES", "BackdropCache", "BrowseShell"]

#: Browse pages, in rail order.  ``detail`` has no rail entry of its own: it
#: keeps the rail item of the section it was opened from.
BROWSE_PAGES = ("home", "library", "detail")

#: Hero and detail plates are 1140 px wide inside the 26 px page padding; the
#: poster is generated at that width so the backdrop is not an upscaled card
#: thumbnail.
BACKDROP_WIDTH = 1140

#: How often to retry pumping the backdrop queue while the worker thread is
#: still winding down (ms).
BACKDROP_PUMP_RETRY_MS = 60


class BackdropCache:
    """1140 px poster frames shared by the home hero and the detail plate.

    The card grid already generates 546 px thumbnails; blowing one up to 1140 px
    reads as a blurry hero on the board.  This cache decodes one extra frame per
    file version (cached on disk by the same size+mtime+width key) and runs the
    decode on :class:`video_player.probe.ThumbnailWorker`, never on the GTK
    thread.  Requests are queued one at a time, because the browse pages only
    ever need one backdrop at once and the VPU is happiest with one decode.
    """

    def __init__(self) -> None:
        self._pixbufs: dict[Path, GdkPixbuf.Pixbuf] = {}
        self._failed: set[Path] = set()
        self._callbacks: dict[Path, list[Callable[[Path, GdkPixbuf | None], None]]] = {}
        self._pending: list[tuple[LibraryItem, Path]] = []
        self._queued: set[Path] = set()
        self._worker = ThumbnailWorker(self._on_ready, width=BACKDROP_WIDTH)
        self._pump_id = 0
        self._closed = False

    # -- public API --------------------------------------------------------

    def request(
        self,
        item: LibraryItem,
        callback: Callable[[Path, GdkPixbuf | None], None],
    ) -> GdkPixbuf.Pixbuf | None:
        """Return a cached frame now, or queue *callback* for one decode."""
        if self._closed:
            return None
        cached = self._pixbufs.get(item.path)
        if cached is not None:
            return cached
        if item.path in self._failed or item.path in self._queued:
            self._callbacks.setdefault(item.path, []).append(callback)
            return None
        cache_path = thumbnail_cache_path(
            item.path, item.size, item.mtime, width=BACKDROP_WIDTH
        )
        loaded = self._load(cache_path)
        if loaded is not None:
            self._pixbufs[item.path] = loaded
            return loaded
        self._callbacks.setdefault(item.path, []).append(callback)
        self._pending.append((item, cache_path))
        self._queued.add(item.path)
        self._pump()
        return None

    def cancel(self) -> None:
        """Drop queued work (the player page is about to own the VPU)."""
        self._worker.cancel()
        self._pending.clear()
        self._queued.clear()
        self._callbacks.clear()
        if self._pump_id:
            GLib.source_remove(self._pump_id)
            self._pump_id = 0

    def stop(self) -> None:
        self._closed = True
        self.cancel()
        self._worker.stop()

    # -- worker plumbing ---------------------------------------------------

    def _pump(self) -> None:
        if self._closed or self._pump_id:
            return
        if self._worker.running:
            self._pump_id = GLib.timeout_add(BACKDROP_PUMP_RETRY_MS, self._pump_tick)
            return
        if not self._pending:
            return
        item, cache_path = self._pending.pop(0)
        self._worker.start([ThumbRequest(item.path, cache_path)])

    def _pump_tick(self) -> bool:
        self._pump_id = 0
        self._pump()
        return GLib.SOURCE_REMOVE

    def _on_ready(self, path: Path, payload: Path | None) -> None:
        self._queued.discard(path)
        picture: GdkPixbuf.Pixbuf | None = None
        if payload is not None:
            picture = self._load(payload)
        if picture is not None:
            self._pixbufs[path] = picture
        else:
            self._failed.add(path)
        callbacks = self._callbacks.pop(path, [])
        for callback in callbacks:
            callback(path, picture)
        self._pump()

    @staticmethod
    def _load(path: Path) -> GdkPixbuf.Pixbuf | None:
        try:
            return GdkPixbuf.Pixbuf.new_from_file(str(path))
        except GLib.Error:
            return None


class BrowseShell(Gtk.Box):
    """The pre-player half of the window: rail plus the three browse pages."""

    def __init__(
        self,
        *,
        config: Config,
        translator: Translator,
        recents: object | None,
        on_play: Callable[..., None],
        on_open_file: Callable[[], None],
        on_changed: Callable[[], None] | None = None,
        on_drop_paths: Callable[[Sequence[Path]], None] | None = None,
    ) -> None:
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL)
        self.add_css_class("browse-shell")
        self._t = translator
        self._on_play = on_play
        self._on_changed = on_changed
        self._on_drop_paths = on_drop_paths
        self._page = "home"
        self._return_page = "home"
        self._detail_origin = "home"
        self._hw_ready = hardware_decode_ready()

        self._backdrops = BackdropCache()
        self.rail = Rail(
            on_nav=self._on_nav,
            translator=translator,
            hw_ready=self._hw_ready,
        )
        self.library = LibraryPage(
            config=config,
            translator=translator,
            recents=recents,
            on_open=self.show_detail,
            on_open_file=on_open_file,
            on_changed=self._on_library_changed,
            on_filter_change=self._on_filter_changed,
            on_poster=self._on_poster,
            on_metadata=self._on_metadata,
        )
        self.home = HomePage(
            translator=translator,
            library=self.library,
            recents=recents,
            on_play=on_play,
            on_detail=self.show_detail,
            on_open_library=self._show_library,
            on_choose_directory=self.library.choose_directory,
            on_open_file=on_open_file,
            on_rescan=self.library.rescan,
            request_backdrop=self._backdrops.request,
        )
        self.detail = DetailPage(
            translator=translator,
            library=self.library,
            recents=recents,
            on_play=on_play,
            on_back=self.back_from_detail,
            on_select=self.show_detail,
            request_backdrop=self._backdrops.request,
        )
        self._stack = Gtk.Stack()
        self._stack.set_transition_type(Gtk.StackTransitionType.NONE)
        self._stack.add_named(self.home, "home")
        self._stack.add_named(self.library, "library")
        self._stack.add_named(self.detail, "detail")
        self._stack.set_visible_child_name(self._page)
        self.append(self.rail)
        self.append(self._stack)

        drop = Gtk.DropTarget.new(Gdk.FileList, Gdk.DragAction.COPY)
        drop.connect("drop", self._on_drop)
        drop.connect("enter", self._on_drop_enter)
        drop.connect("leave", self._on_drop_leave)
        self.add_controller(drop)
        self._sync_rail()

    # -- navigation --------------------------------------------------------

    @property
    def page(self) -> str:
        return self._page

    def activate(self) -> None:
        """Show the browse page the player was opened from."""
        if self._return_page == "detail" and self.detail.item is None:
            # Defensive: a detail page with no clip would be a blank screen.
            self._return_page = "home"
        self._show(self._return_page)
        self.rail.set_clock(self.library.scan_clock)
        if self._page == "home":
            self.home.refresh()

    def mark_player_origin(self) -> None:
        """Remember where "back" from the player should land."""
        self._return_page = self._page

    def show(self, page: str) -> None:
        if page not in BROWSE_PAGES:
            raise ValueError(f"unknown browse page: {page!r}")
        self._show(page)

    def _show(self, page: str) -> None:
        changed = page != self._page
        self._page = page
        self._stack.set_visible_child_name(page)
        self._sync_rail()
        if changed:
            log_action("browse_page", page=page)
        # The window header is a function of the current browse page (home and
        # library show the library headline, detail shows the clip title).  It
        # also has to follow a same-page detail switch, so this is not gated on
        # ``changed``.
        if self._on_changed is not None:
            self._on_changed()

    def _show_library(self, filter_key: str = "all") -> None:
        self._show("library")
        self.library.select_filter(filter_key)
        self._sync_rail()

    def show_detail(self, item: LibraryItem) -> None:
        """Open the detail page for *item*, remembering the section it came from.

        Called for a card and again when a same-folder row switches the clip;
        in the second case the origin must stay whatever it already was.
        """
        if self._page in ("home", "library"):
            self._detail_origin = self._page
        self.detail.show(item)
        self._show("detail")
        log_action("detail_open", path=str(item.path), origin=self._detail_origin)

    def back_from_detail(self) -> None:
        target = "library" if self._detail_origin == "library" else "home"
        self._show(target)
        if target == "home":
            self.home.refresh()
        else:
            self.library.select_filter(self.library.filter_key)

    def _on_nav(self, kind: str) -> None:
        if kind == "home":
            self._show("home")
            self.home.refresh()
        elif kind == "library":
            self._show_library("all")
        elif kind == "recent":
            self._show_library("recent")

    def _sync_rail(self) -> None:
        if self._page == "home":
            self.rail.set_active("home")
        elif self._page == "detail":
            self.rail.set_active("home" if self._detail_origin == "home" else "library")
        else:
            self.rail.set_active(
                "recent" if self.library.filter_key == "recent" else "library"
            )

    # -- data --------------------------------------------------------------

    def refresh(self) -> None:
        """Rescan and refresh whatever page is on screen."""
        if self._page == "detail":
            item = self.detail.item
            if item is not None and not item.path.is_file():
                self.back_from_detail()
                return
        self.library.refresh()
        self.rail.set_clock(self.library.scan_clock)
        if self._page == "home":
            self.home.refresh()
        elif self._page == "detail":
            self.detail.refresh()

    def _on_library_changed(self) -> None:
        self.rail.set_clock(self.library.scan_clock)
        self.home.refresh()
        if self._page == "detail":
            self.detail.refresh()
        if self._on_changed is not None:
            self._on_changed()

    def _on_filter_changed(self, filter_key: str) -> None:
        self._sync_rail()

    def _on_poster(self, path: Path, pixbuf: GdkPixbuf.Pixbuf | None) -> None:
        self.home.on_poster(path, pixbuf)
        self.detail.on_poster(path, pixbuf)

    def _on_metadata(self, path: Path, metadata: VideoMetadata | None) -> None:
        self.home.on_metadata(path, metadata)
        self.detail.on_metadata(path, metadata)

    # -- drag and drop -----------------------------------------------------

    def _on_drop(self, _target: Gtk.DropTarget, value: object, _x: float, _y: float) -> bool:
        paths: list[Path] = []
        try:
            files = value.get_files()  # type: ignore[attr-defined]
        except AttributeError:
            return False
        for gfile in files:
            text = gfile.get_path()
            if text:
                paths.append(Path(text))
        playable = self.library.accept_drop(paths)
        if playable and self._on_drop_paths is not None:
            self._on_drop_paths(playable)
        return True

    def _on_drop_enter(self, *_args: object) -> bool:
        self.add_css_class("drag-active")
        return True

    def _on_drop_leave(self, *_args: object) -> None:
        self.remove_css_class("drag-active")

    # -- window hooks ------------------------------------------------------

    def headline(self) -> tuple[str, str]:
        """``(title, subtitle)`` for the window header on the browse page."""
        stat, sub = self.library.headline()
        if self._page == "detail":
            view = self.detail.view
            item = self.detail.item
            if view is not None and item is not None:
                folder = view.folder_name
                return item.title, (
                    f"{folder} · "
                    f"{self._t('detail_file_count', count=len(view.episodes))}"
                )
        return self._t("app_title"), f"{stat} · {sub}"

    def is_detail(self) -> bool:
        return self._page == "detail"

    def suspend(self) -> None:
        """The player page needs the VPU: stop scanning and backdrop decodes."""
        self.library.suspend()
        self._backdrops.cancel()

    def shutdown(self) -> None:
        self.library.shutdown()
        self._backdrops.stop()

    def apply_language(self, translator: Translator) -> None:
        self._t = translator
        self.rail.apply_language(translator)
        self.library.apply_language(translator)
        self.home.apply_language(translator)
        self.detail.apply_language(translator)
