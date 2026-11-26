"""ui-v5 library page: the scanned-file grid, filters and empty state.

The browse shell (:mod:`video_player.ui.browse`) owns the left rail and the
page switch; this module owns the library page itself.  Poster frames are
Cairo-drawn (:class:`video_player.ui.paint.Poster`) so a rounded, cropped
thumbnail with a duration pill and a progress hairline renders the same on the
board's trimmed GTK theme and in a local snapshot.

Scanning and probing are delegated to :mod:`video_player.library` and
:mod:`video_player.probe`; this module never decodes anything on the main
thread.  The page only starts its workers while the browse shell is visible.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Callable, Sequence

import gi

gi.require_version("GdkPixbuf", "2.0")
gi.require_version("Gtk", "4.0")
gi.require_version("Pango", "1.0")
from gi.repository import GdkPixbuf, GLib, Gtk, Pango  # noqa: E402

from ..config import Config  # noqa: E402
from ..diagnostics import log_action  # noqa: E402
from ..i18n import Translator  # noqa: E402
from ..library import (  # noqa: E402
    FILTERS,
    FINISHED_THRESHOLD,
    SORTS,
    LibraryItem,
    LibraryStore,
    VideoMetadata,
    filter_items,
    library_counts,
    scan_library,
    sort_items,
)
from ..pipeline import VIDEO_SUFFIXES  # noqa: E402
from ..probe import (  # noqa: E402
    MetadataWorker,
    ThumbnailWorker,
    ThumbRequest,
    hardware_decode_ready,
    thumbnail_cache_path,
)
from .chrome import (  # noqa: E402
    format_clock,
    format_duration_ns,
    video_meta_text,
)
from .paint import GhostFrame, Poster  # noqa: E402

#: Cards are laid out in a fixed 4-column grid (273 px + 16 px gaps = 1140).
#: The width is pinned so a filtered grid never stretches three cards across
#: the row the way a homogeneous grid would.
GRID_COLUMNS = 4
CARD_WIDTH = 273

#: One poster costs a real video decode, so a first pass only fills the grid
#: that is actually on screen; the rest fill in on the next visit.
THUMBNAIL_BUDGET = 48

#: Store flush delay: metadata arrives one file at a time, each save rewrites
#: the whole JSON file, so coalesce a burst into one write.
STORE_SAVE_DELAY_MS = 1500


class LibraryCard(Gtk.Button):
    """One library tile: poster, title and a meta line."""

    def __init__(
        self, item: LibraryItem, *, on_activate: Callable[[LibraryItem], None]
    ) -> None:
        super().__init__()
        self.add_css_class("lib-card")
        self.set_focus_on_click(False)
        self.set_size_request(CARD_WIDTH, -1)
        self.set_hexpand(False)
        self.set_halign(Gtk.Align.START)
        self.item = item
        self._on_activate = on_activate
        self.set_tooltip_text(str(item.path))
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.thumb = Poster()
        box.append(self.thumb)
        fig = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
        self.title_label = Gtk.Label(label=item.title)
        self.title_label.add_css_class("lib-title")
        self.title_label.set_xalign(0.0)
        self.title_label.set_ellipsize(Pango.EllipsizeMode.END)
        self.title_label.set_max_width_chars(20)
        self.meta_label = Gtk.Label(label="")
        self.meta_label.add_css_class("lib-meta")
        self.meta_label.set_xalign(0.0)
        self.meta_label.set_ellipsize(Pango.EllipsizeMode.END)
        self.meta_label.set_max_width_chars(24)
        fig.append(self.title_label)
        fig.append(self.meta_label)
        box.append(fig)
        self.set_child(box)
        self.connect("clicked", self._activate)

    def _activate(self, _button: Gtk.Button) -> None:
        self._on_activate(self.item)

    def update(
        self,
        item: LibraryItem,
        *,
        pixbuf: GdkPixbuf.Pixbuf | None,
        duration_text: str = "",
        badge: str = "",
        badge_accent: bool = False,
        progress: float = 0.0,
        meta_text: str = "",
        missing: bool = False,
        missing_text: str = "",
        current: bool = False,
    ) -> None:
        self.item = item
        self.set_tooltip_text(f"{item.path}\n{meta_text}")
        if item.title != self.title_label.get_text():
            self.title_label.set_text(item.title)
        self.meta_label.set_text(meta_text)
        self.thumb.set_content(
            pixbuf=pixbuf,
            duration=duration_text,
            badge=badge,
            badge_accent=badge_accent,
            progress=progress,
            missing_text=missing_text,
            missing=missing,
            current=current,
        )


class LibraryPage(Gtk.Box):
    """The library page: page head, filter chips, poster grid and empty state.

    Args:
        config: player configuration (``library.directories`` seeds the scan).
        translator: current language; :meth:`apply_language` re-labels the page.
        recents: history store used for the "recently watched" / "finished"
            filters and the card progress bar; None disables both.
        on_open: called with the item when a card is activated (the browse shell
            opens the detail page for it).
        on_open_file: opens the single-file dialog (shared with the header).
        on_changed: called after a scan or filter change (header + home page).
        on_filter_change: called with the new filter key when a chip changes it.
    """

    def __init__(
        self,
        *,
        config: Config,
        translator: Translator,
        recents: object | None,
        on_open: Callable[[LibraryItem], None],
        on_open_file: Callable[[], None],
        on_changed: Callable[[], None] | None = None,
        on_filter_change: Callable[[str], None] | None = None,
        on_poster: Callable[[Path, GdkPixbuf.Pixbuf | None], None] | None = None,
        on_metadata: Callable[[Path, VideoMetadata | None], None] | None = None,
    ) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.add_css_class("library-page")
        self.config = config
        self._t = translator
        self._recents = recents
        self._on_open = on_open
        self._on_open_file = on_open_file
        self._on_changed = on_changed
        self._on_filter_change = on_filter_change
        # NOTE: ``self._on_metadata`` would shadow the LibraryPage._on_metadata
        # method the workers call, so the hooks use their own names.
        self._poster_hook = on_poster
        self._metadata_hook = on_metadata
        self._store = LibraryStore()
        self._items: list[LibraryItem] = []
        self._visible: list[LibraryItem] = []
        self._cards: dict[Path, LibraryCard] = {}
        self._pixbufs: dict[Path, GdkPixbuf.Pixbuf | None] = {}
        self._probe_failed: set[Path] = set()
        self._thumb_failed: set[Path] = set()
        self._filter = "all"
        self._sort = "added"
        self._scan_seconds = 0.0
        self._scan_clock = ""
        self._updating_nav = False
        self._store_save_id = 0
        self._closed = False
        self._hw_ready = hardware_decode_ready()
        self._metadata_worker = MetadataWorker(self._on_metadata)
        self._thumbnail_worker = ThumbnailWorker(self._on_thumbnail)
        self._build()

    # -- construction ------------------------------------------------------

    def _build(self) -> None:
        self._content = self
        self.append(self._build_page_head())
        self.append(self._build_filters())
        self._grid_area = self._build_grid_area()
        self._empty_area = self._build_empty_area()
        self.append(self._grid_area)
        self.append(self._empty_area)
        self.apply_language(self._t)

    def _build_page_head(self) -> Gtk.Widget:
        head = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        self._head = head
        self._page_title = Gtk.Label()
        self._page_title.add_css_class("page-title")
        self._page_stat = Gtk.Label()
        self._page_stat.add_css_class("page-stat")
        self._page_stat.set_valign(Gtk.Align.END)
        self._page_stat.set_margin_bottom(2)
        self._page_sub = Gtk.Label()
        self._page_sub.add_css_class("page-sub")
        self._page_sub.set_valign(Gtk.Align.END)
        self._page_sub.set_margin_bottom(3)
        head.append(self._page_title)
        head.append(self._page_stat)
        head.append(self._page_sub)
        spacer = Gtk.Box()
        spacer.set_hexpand(True)
        head.append(spacer)

        self._sort_button = Gtk.MenuButton()
        self._sort_button.add_css_class("tool")
        self._sort_button.set_tooltip_text(self._t("library_sort"))
        sort_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self._sort_key_label = Gtk.Label()
        self._sort_key_label.add_css_class("tool-key")
        self._sort_value_label = Gtk.Label()
        self._sort_value_label.add_css_class("tool-value")
        self._sort_chevron = Gtk.Label(label="▾")
        self._sort_chevron.add_css_class("tool-key")
        sort_box.append(self._sort_key_label)
        sort_box.append(self._sort_value_label)
        sort_box.append(self._sort_chevron)
        self._sort_button.set_child(sort_box)

        sort_popover = Gtk.Popover()
        sort_list = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        sort_list.set_margin_top(6)
        sort_list.set_margin_bottom(6)
        sort_list.set_margin_start(6)
        sort_list.set_margin_end(6)
        self._sort_choices: dict[str, Gtk.Button] = {}
        for key in SORTS:
            choice = Gtk.Button()
            choice.add_css_class("tool")
            choice.set_halign(Gtk.Align.FILL)
            label = Gtk.Label()
            label.set_xalign(0.0)
            choice.set_child(label)
            choice.connect("clicked", self._on_sort_clicked, key)
            sort_list.append(choice)
            self._sort_choices[key] = choice
        sort_popover.set_child(sort_list)
        self._sort_button.set_popover(sort_popover)

        self._rescan_button = Gtk.Button()
        self._rescan_button.add_css_class("tool")
        self._rescan_button.set_tooltip_text(self._t("library_rescan"))
        rescan_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self._rescan_icon = Gtk.Label(label="↻")
        self._rescan_label = Gtk.Label()
        rescan_box.append(self._rescan_icon)
        rescan_box.append(self._rescan_label)
        self._rescan_button.set_child(rescan_box)
        self._rescan_button.connect("clicked", self._on_rescan_clicked)

        head.append(self._sort_button)
        head.append(self._rescan_button)
        return head

    def _build_filters(self) -> Gtk.Widget:
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        row.set_margin_top(10)
        row.set_margin_bottom(10)
        self._filter_buttons: dict[str, Gtk.ToggleButton] = {}
        self._filter_counts: dict[str, Gtk.Label] = {}
        self._filter_labels: dict[str, Gtk.Label] = {}
        first: Gtk.ToggleButton | None = None
        for key in FILTERS:
            button = Gtk.ToggleButton()
            button.add_css_class("fchip")
            button.set_focus_on_click(False)
            content = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=5)
            label = Gtk.Label()
            count = Gtk.Label(label="0")
            count.add_css_class("fchip-count")
            content.append(label)
            content.append(count)
            button.set_child(content)
            if first is not None:
                button.set_group(first)
            else:
                first = button
            button.connect("toggled", self._on_filter_toggled, key)
            row.append(button)
            self._filter_buttons[key] = button
            self._filter_counts[key] = count
            self._filter_labels[key] = label
        return row

    def _build_grid_area(self) -> Gtk.Widget:
        area = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        area.set_vexpand(True)
        self._scroll = Gtk.ScrolledWindow()
        self._scroll.add_css_class("library-scroll")
        self._scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self._scroll.set_vexpand(True)
        self._scroll.set_child(self._build_grid())
        area.append(self._scroll)

        foot = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self._foot_label = Gtk.Label()
        self._foot_label.add_css_class("lib-foot")
        self._foot_label.set_xalign(0.0)
        self._foot_path = Gtk.Label()
        self._foot_path.add_css_class("lib-foot-path")
        self._foot_path.set_xalign(1.0)
        self._foot_path.set_hexpand(True)
        self._foot_path.set_ellipsize(Pango.EllipsizeMode.MIDDLE)
        self._foot_path.set_max_width_chars(60)
        foot.append(self._foot_label)
        foot.append(self._foot_path)
        area.append(foot)
        return area

    def _build_grid(self) -> Gtk.Widget:
        grid = Gtk.Grid()
        grid.add_css_class("library-grid")
        grid.set_column_homogeneous(False)
        grid.set_row_homogeneous(False)
        grid.set_column_spacing(16)
        grid.set_row_spacing(12)
        grid.set_valign(Gtk.Align.START)
        self._grid = grid
        return grid

    def _build_empty_area(self) -> Gtk.Widget:
        board = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        board.add_css_class("empty-board")
        board.set_vexpand(True)
        board.set_valign(Gtk.Align.FILL)
        board.set_margin_top(2)

        main = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        main.set_vexpand(True)
        main.set_halign(Gtk.Align.CENTER)
        main.set_valign(Gtk.Align.CENTER)
        main.append(GhostFrame())

        self._empty_title = Gtk.Label()
        self._empty_title.add_css_class("eb-title")
        self._empty_title.set_margin_top(22)
        main.append(self._empty_title)

        self._empty_body = Gtk.Label()
        self._empty_body.add_css_class("eb-body")
        self._empty_body.set_justify(Gtk.Justification.CENTER)
        self._empty_body.set_wrap(True)
        self._empty_body.set_max_width_chars(46)
        self._empty_body.set_margin_top(10)
        main.append(self._empty_body)

        cta = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        cta.set_halign(Gtk.Align.CENTER)
        cta.set_margin_top(22)
        self._empty_add_dir = Gtk.Button()
        self._empty_add_dir.add_css_class("drop-primary")
        self._empty_add_dir.connect("clicked", self._on_choose_directory)
        self._empty_open_file = Gtk.Button()
        self._empty_open_file.add_css_class("drop-secondary")
        self._empty_open_file.connect("clicked", lambda _b: self._on_open_file())
        self._empty_rescan = Gtk.Button()
        self._empty_rescan.add_css_class("link-button")
        self._empty_rescan.connect("clicked", self._on_rescan_clicked)
        cta.append(self._empty_add_dir)
        cta.append(self._empty_open_file)
        cta.append(self._empty_rescan)
        main.append(cta)
        self._empty_hint = Gtk.Label()
        self._empty_hint.add_css_class("lib-empty-note")
        self._empty_hint.set_margin_top(6)
        main.append(self._empty_hint)
        board.append(main)

        scan_bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        scan_bar.add_css_class("eb-bar")
        scan_bar.set_size_request(-1, 52)
        self._scan_scope_label = Gtk.Label()
        self._scan_scope_label.add_css_class("eb-bar-label")
        scan_bar.append(self._scan_scope_label)
        self._dir_chips = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        scan_bar.append(self._dir_chips)
        spacer = Gtk.Box()
        spacer.set_hexpand(True)
        scan_bar.append(spacer)
        self._bar_add_dir = Gtk.Button()
        self._bar_add_dir.add_css_class("tool")
        self._bar_add_dir.connect("clicked", self._on_choose_directory)
        scan_bar.append(self._bar_add_dir)
        board.append(scan_bar)

        fmt_bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        fmt_bar.add_css_class("eb-bar")
        fmt_bar.set_size_request(-1, 50)
        self._fmt_label = Gtk.Label()
        self._fmt_label.add_css_class("eb-bar-label")
        fmt_bar.append(self._fmt_label)
        self._fmt_chips = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        fmt_bar.append(self._fmt_chips)
        spacer2 = Gtk.Box()
        spacer2.set_hexpand(True)
        fmt_bar.append(spacer2)
        self._fmt_hint = Gtk.Label()
        self._fmt_hint.add_css_class("lib-foot")
        fmt_bar.append(self._fmt_hint)
        board.append(fmt_bar)
        return board

    # -- public API --------------------------------------------------------

    @property
    def items(self) -> tuple[LibraryItem, ...]:
        return tuple(self._items)

    @property
    def visible_items(self) -> tuple[LibraryItem, ...]:
        return tuple(self._visible)

    @property
    def filter_key(self) -> str:
        return self._filter

    @property
    def sort_key(self) -> str:
        return self._sort

    def directories(self) -> tuple[Path, ...]:
        """Effective scan roots: config entries first, then user-picked ones."""
        roots: list[Path] = []
        seen: set[str] = set()
        for raw in self.config.library.directories:
            try:
                path = Path(raw).expanduser().resolve()
            except (OSError, RuntimeError):
                continue
            if str(path) in seen:
                continue
            seen.add(str(path))
            roots.append(path)
        for path in self._store.directories:
            if str(path) in seen:
                continue
            seen.add(str(path))
            roots.append(path)
        return tuple(roots)

    def headline(self) -> tuple[str, str]:
        """``(stat, sub)`` for the window header on the browse page."""
        count = len(self._items)
        folders = len(self.directories())
        stat = self._t(
            "library_stat_one" if count == 1 else "library_stat", count=count
        )
        if not folders:
            sub = self._t("library_sub_empty")
        else:
            sub = self._t(
                "library_sub_one" if folders == 1 else "library_sub",
                folders=folders,
            )
        return stat, sub

    @property
    def scan_clock(self) -> str:
        """Clock of the last scan (rail footer), empty before the first one."""
        return self._scan_clock

    @property
    def hardware_ready(self) -> bool:
        """Whether the RK3576 VPU decoder is registered (spec card, rail dot)."""
        return self._hw_ready

    def poster_for(self, path: Path) -> GdkPixbuf.Pixbuf | None:
        """Poster of *path* if the thumbnail worker already rendered it."""
        return self._pixbufs.get(path)

    def choose_directory(self) -> None:
        """Folder picker, shared with the home page's empty state."""
        self._on_choose_directory(None)

    def rescan(self) -> None:
        """Rescan entry, shared with the home page's empty state."""
        self._on_rescan_clicked(None)

    def refresh(self, *, retry_failed: bool = False) -> None:
        """Rescan the folders, rebuild the grid and restart the workers."""
        if self._closed:
            return
        if retry_failed:
            self._probe_failed.clear()
            self._thumb_failed.clear()
        started = time.monotonic()
        roots = self.directories()
        items = scan_library(roots, self._store, limit=self.config.library.max_items)
        self._scan_seconds = time.monotonic() - started
        self._scan_clock = format_clock(time.time())
        self._items = items
        self._apply_view()
        self._start_metadata_probe()
        if not self._metadata_worker.pending:
            self._start_thumbnails()
        log_action(
            "library_scan",
            folders=len(roots),
            items=len(items),
            seconds=round(self._scan_seconds, 3),
        )

    def suspend(self) -> None:
        """Cancel probing (the player page needs the VPU to itself)."""
        self._metadata_worker.cancel()
        self._thumbnail_worker.cancel()
        self._flush_store()

    def shutdown(self) -> None:
        self._closed = True
        self._metadata_worker.stop()
        self._thumbnail_worker.stop()
        self._flush_store()

    def apply_language(self, translator: Translator) -> None:
        """Re-label every long-lived widget (called from the window)."""
        self._t = translator
        t = translator
        self._page_title.set_text(t("library_title"))
        self._sort_key_label.set_text(t("library_sort"))
        for key in SORTS:
            choice = self._sort_choices.get(key)
            if choice is not None and choice.get_child() is not None:
                choice.get_child().set_text(t(f"library_sort_{key}"))
        self._sort_value_label.set_text(t(f"library_sort_{self._sort}"))
        self._rescan_label.set_text(t("library_rescan"))
        self._rescan_button.set_tooltip_text(t("library_rescan"))
        for key in FILTERS:
            label = self._filter_labels.get(key)
            if label is not None:
                label.set_text(t(f"library_filter_{key}"))

        self._empty_title.set_text(t("library_empty_title"))
        self._empty_body.set_text(t("library_empty_body"))
        self._empty_add_dir.set_label(t("library_add_dir"))
        self._empty_open_file.set_label(t("library_open_file"))
        self._empty_rescan.set_label(t("library_rescan"))
        self._empty_hint.set_text(t("empty_drop_hint"))
        self._scan_scope_label.set_text(t("library_scan_scope"))
        self._bar_add_dir.set_label(t("library_add_dir"))
        self._fmt_label.set_text(t("library_supported"))
        self._fmt_hint.set_text(t("library_empty_hint"))
        self._rebuild_dir_chips()
        self._rebuild_fmt_chips()
        self._sync_head()
        self._sync_cards_meta()
        self._sync_foot()
        self._sync_empty()

    # -- view state --------------------------------------------------------

    def _recent_state(self) -> tuple[set[Path], set[Path]]:
        recent: set[Path] = set()
        finished: set[Path] = set()
        if self._recents is None:
            return recent, finished
        try:
            entries = self._recents.items(only_existing=True)
        except Exception:  # noqa: BLE001 - history must never break the grid
            return recent, finished
        for entry in entries:
            recent.add(entry.path)
            if entry.duration_ns > 0 and entry.progress >= FINISHED_THRESHOLD:
                finished.add(entry.path)
        return recent, finished

    def _recent_progress(self, path: Path) -> float:
        if self._recents is None:
            return 0.0
        try:
            entries = self._recents.items()
        except Exception:  # noqa: BLE001
            return 0.0
        for entry in entries:
            if entry.path == path:
                return entry.progress
        return 0.0

    def _apply_view(self) -> None:
        recent, finished = self._recent_state()
        visible = filter_items(
            self._items,
            self._filter,
            recent_paths=recent,
            finished_paths=finished,
        )
        self._visible = sort_items(visible, self._sort)
        counts = library_counts(
            self._items, recent_paths=recent, finished_paths=finished
        )
        for key, count_label in self._filter_counts.items():
            count_label.set_text(str(counts.get(key, 0)))
        self._rebuild_grid()
        self._sync_head()
        self._sync_filters()
        self._sync_foot()
        self._sync_empty()
        if self._on_changed is not None:
            self._on_changed()

    def _sync_head(self) -> None:
        count = len(self._items)
        self._page_stat.set_text(
            self._t("library_stat_one" if count == 1 else "library_stat", count=count)
        )
        folders = len(self.directories())
        if not folders:
            self._page_sub.set_text(self._t("library_sub_empty"))
        else:
            self._page_sub.set_text(
                self._t(
                    "library_sub_one" if folders == 1 else "library_sub",
                    folders=folders,
                )
            )

    def _sync_filters(self) -> None:
        for key, button in self._filter_buttons.items():
            active = key == self._filter
            if button.get_active() != active:
                button.set_active(active)

    def _sync_foot(self) -> None:
        if self._scan_clock:
            self._foot_label.set_text(
                self._t(
                    "library_scan_summary",
                    clock=self._scan_clock,
                    seconds=self._scan_seconds,
                )
            )
        else:
            self._foot_label.set_text(self._t("library_scan_never"))
        roots = self.directories()
        self._foot_path.set_text(str(roots[0]) if roots else "")

    def _sync_empty(self) -> None:
        empty = not self._items
        self._grid_area.set_visible(not empty)
        self._empty_area.set_visible(empty)
        if empty:
            self._rebuild_dir_chips()

    def _rebuild_dir_chips(self) -> None:
        while (child := self._dir_chips.get_first_child()) is not None:
            self._dir_chips.remove(child)
        roots = self.directories()
        if not roots:
            label = Gtk.Label(label=self._t("library_no_dirs"))
            label.add_css_class("dir-chip")
            self._dir_chips.append(label)
            return
        store_roots = {str(path) for path in self._store.directories}
        for root in roots:
            chip = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
            chip.set_valign(Gtk.Align.CENTER)
            label = Gtk.Label(label=str(root))
            label.add_css_class("dir-chip")
            if not root.is_dir():
                label.add_css_class("is-missing")
            chip.append(label)
            if str(root) in store_roots:
                remove = Gtk.Button(label="×")
                remove.add_css_class("dir-remove")
                remove.set_tooltip_text(self._t("library_remove_dir"))
                remove.set_focus_on_click(False)
                remove.connect("clicked", self._on_remove_dir, root)
                chip.append(remove)
            self._dir_chips.append(chip)

    def _rebuild_fmt_chips(self) -> None:
        while (child := self._fmt_chips.get_first_child()) is not None:
            self._fmt_chips.remove(child)
        for text in (self._t("library_formats"), self._t("library_codecs")):
            chip = Gtk.Label(label=text)
            chip.add_css_class("fmt-chip")
            self._fmt_chips.append(chip)

    def _sync_cards_meta(self) -> None:
        for card in self._cards.values():
            self._update_card(card, card.item)

    def _update_card(self, card: LibraryCard, item: LibraryItem) -> None:
        progress = self._recent_progress(item.path)
        card.update(
            item,
            pixbuf=self._pixbufs.get(item.path),
            duration_text=(
                format_duration_ns(item.duration_ns) if item.duration_ns else ""
            ),
            badge=(
                self._t("library_finished_badge")
                if progress >= FINISHED_THRESHOLD
                else ""
            ),
            progress=progress,
            meta_text=self._meta_text(item),
            missing=not item.path.is_file(),
            missing_text=self._t("library_missing_file"),
        )

    def _meta_text(self, item: LibraryItem) -> str:
        if item.metadata is None:
            return self._t("library_no_meta")
        meta = item.metadata
        return video_meta_text(
            width=meta.width,
            height=meta.height,
            fps=meta.fps,
            codec=meta.codec,
            size=item.size,
        )

    def _rebuild_grid(self) -> None:
        while (child := self._grid.get_first_child()) is not None:
            self._grid.remove(child)
        self._cards.clear()
        if not self._visible and self._items:
            # A filter can legitimately match nothing; an empty grid would read
            # as a rendering bug, so say so where the cards would be.  The
            # wrapper expands across the grid, because a spanning label alone
            # only gets the collapsed column widths.
            note_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
            note_box.set_hexpand(True)
            note_box.set_halign(Gtk.Align.FILL)
            note_box.set_margin_top(48)
            note = Gtk.Label(label=self._t("library_no_matches"))
            note.add_css_class("lib-empty-note")
            note.set_hexpand(True)
            note.set_halign(Gtk.Align.CENTER)
            note_box.append(note)
            self._grid.attach(note_box, 0, 0, GRID_COLUMNS, 1)
        for index, item in enumerate(self._visible):
            card = LibraryCard(item, on_activate=self._on_open)
            self._update_card(card, item)
            self._cards[item.path] = card
            self._grid.attach(card, index % GRID_COLUMNS, index // GRID_COLUMNS, 1, 1)

    # -- workers -----------------------------------------------------------

    def _start_metadata_probe(self) -> None:
        queue = [
            item.path
            for item in self._items
            if item.metadata is None and item.path not in self._probe_failed
        ]
        if not queue:
            return
        self._metadata_worker.start(queue)

    def _start_thumbnails(self) -> None:
        requests: list[ThumbRequest] = []
        for item in self._items[:THUMBNAIL_BUDGET]:
            if item.path in self._pixbufs or item.path in self._thumb_failed:
                continue
            if item.path in self._probe_failed:
                # The metadata probe already failed; decoding the same file for
                # a poster would just burn another timeout.
                continue
            requests.append(
                ThumbRequest(
                    item.path,
                    thumbnail_cache_path(item.path, item.size, item.mtime),
                )
            )
        if requests:
            self._thumbnail_worker.start(requests)

    def _on_metadata(self, path: Path, metadata: VideoMetadata | None) -> None:
        if self._closed:
            return
        index = next(
            (i for i, item in enumerate(self._items) if item.path == path), None
        )
        if index is None:
            return
        item = self._items[index]
        if metadata is None:
            self._probe_failed.add(path)
            self._maybe_start_thumbnails()
            if self._metadata_hook is not None:
                self._metadata_hook(path, None)
            return
        if self._store.remember_metadata(path, item.size, item.mtime, metadata):
            self._schedule_store_save()
        updated = item.with_metadata(metadata)
        self._items[index] = updated
        card = self._cards.get(path)
        if card is not None:
            self._update_card(card, updated)
        self._maybe_start_thumbnails()
        if self._metadata_hook is not None:
            self._metadata_hook(path, metadata)

    def _maybe_start_thumbnails(self) -> None:
        if self._metadata_worker.pending:
            return
        if self._thumbnail_worker.running:
            return
        self._start_thumbnails()

    def _on_thumbnail(self, path: Path, payload: Path | None) -> None:
        if self._closed:
            return
        if payload is None:
            self._thumb_failed.add(path)
            if self._poster_hook is not None:
                self._poster_hook(path, None)
            return
        pixbuf = self._load_pixbuf(payload)
        if pixbuf is None:
            return
        self._pixbufs[path] = pixbuf
        card = self._cards.get(path)
        if card is not None:
            self._update_card(card, card.item)
        if self._poster_hook is not None:
            self._poster_hook(path, pixbuf)

    def _load_pixbuf(self, path: Path) -> GdkPixbuf.Pixbuf | None:
        try:
            return GdkPixbuf.Pixbuf.new_from_file(str(path))
        except GLib.Error:
            return None

    def _schedule_store_save(self) -> None:
        if self._store_save_id:
            return
        self._store_save_id = GLib.timeout_add(
            STORE_SAVE_DELAY_MS, self._flush_store
        )

    def _flush_store(self) -> bool:
        self._store_save_id = 0
        self._store.save()
        return GLib.SOURCE_REMOVE

    # -- drag and drop -----------------------------------------------------

    def accept_drop(self, paths: Sequence[Path]) -> list[Path]:
        """Folders become scan roots; returns the video files to play.

        Public so the window (and the QC probes) can exercise the same rule
        without synthesising a GDK drag payload.
        """
        changed = False
        playable: list[Path] = []
        for path in paths:
            try:
                if path.is_dir():
                    if self._store.add_directory(path):
                        changed = True
                    continue
            except OSError:
                continue
            if path.suffix.lower() in VIDEO_SUFFIXES and path.is_file():
                playable.append(path)
        if changed:
            self._store.save()
            log_action("library_drop_folders", folders=len(self._store.directories))
            self.refresh(retry_failed=True)
        return playable

    # -- event handlers ----------------------------------------------------

    def _on_filter_toggled(self, button: Gtk.ToggleButton, key: str) -> None:
        if self._updating_nav or not button.get_active():
            return
        self._set_filter(key)

    def select_filter(self, key: str) -> None:
        """Public entry for the browse shell's rail ("recent" shortcut)."""
        self._set_filter(key)

    def _set_filter(self, key: str) -> None:
        if key not in FILTERS or key == self._filter:
            self._sync_filters()
            return
        self._filter = key
        log_action("library_filter", filter=key)
        self._updating_nav = True
        try:
            self._apply_view()
        finally:
            self._updating_nav = False
        if self._on_filter_change is not None:
            self._on_filter_change(self._filter)

    def _on_sort_clicked(self, _button: Gtk.Button, key: str) -> None:
        if key not in SORTS:
            return
        self._sort = key
        self._sort_value_label.set_text(self._t(f"library_sort_{key}"))
        popover = self._sort_button.get_popover()
        if popover is not None:
            popover.popdown()
        log_action("library_sort", sort=key)
        self._apply_view()

    def _on_rescan_clicked(self, _button: Gtk.Button | None = None) -> None:
        log_action("library_rescan")
        self.refresh(retry_failed=True)

    def _on_choose_directory(self, _button: Gtk.Button | None = None) -> None:
        dialog = Gtk.FileDialog(title=self._t("library_dialog_title"))
        root = self.get_root()
        dialog.select_folder(root if isinstance(root, Gtk.Window) else None, None, self._on_folder_selected)

    def _on_folder_selected(self, dialog: Gtk.FileDialog, result: object) -> None:
        try:
            gfile = dialog.select_folder_finish(result)  # type: ignore[arg-type]
        except GLib.Error as exc:
            log_action("library_dir_cancelled", error=str(exc))
            return
        path = gfile.get_path() if gfile is not None else None
        if not path:
            log_action("library_dir_cancelled")
            return
        if not self._store.add_directory(path):
            log_action("library_dir_rejected", path=path)
            return
        self._store.save()
        log_action("library_dir_added", path=path)
        self.refresh(retry_failed=True)

    def _on_remove_dir(self, _button: Gtk.Button, root: Path) -> None:
        if not self._store.remove_directory(root):
            return
        self._store.save()
        log_action("library_dir_removed", path=str(root))
        self.refresh()
