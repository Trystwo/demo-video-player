"""ui-v5 recent page: the watch history, split into two groups of poster cards.

The browse shell's "最近" rail entry is a real page now, not a library filter.
It answers "what did I open lately" with the same poster cards as the library
grid, but ordered by watch time and grouped by watch state: "继续观看" holds
clips with a position worth resuming, "已看完" the ones watched past
:data:`video_player.library.FINISHED_THRESHOLD`.

The page owns no workers.  Posters and metadata come from the library page's
caches, which already probed every scanned file -- that is also why only files
the library still scans can appear here (see
:func:`video_player.browse.recent_view`).  Tapping a "继续观看" card resumes it
straight away; a "已看完" card opens the detail page, where "重新播放" lives.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Callable, Sequence

import gi

gi.require_version("GdkPixbuf", "2.0")
gi.require_version("Gtk", "4.0")
from gi.repository import GdkPixbuf, Gio, GLib, Gtk  # noqa: E402

from ..browse import HomeCard, RecentView, recent_view, remaining_text  # noqa: E402
from ..diagnostics import log_action  # noqa: E402
from ..i18n import Translator  # noqa: E402
from ..library import LibraryItem, VideoMetadata  # noqa: E402
from .chrome import format_duration_ns, format_watch_time  # noqa: E402
from .library_page import CARD_WIDTH, GRID_COLUMNS, LibraryCard, LibraryPage  # noqa: E402
from .paint import GhostFrame  # noqa: E402

__all__ = ["RecentPage"]


class RecentPage(Gtk.Box):
    """Watch history: page head, grouped poster grid and empty state.

    Args:
        translator: current language; :meth:`apply_language` re-labels the page.
        library: the library page; supplies the scanned items, the poster cache
            and the metadata worker's results.
        recents: history store; None renders the empty state.
        on_play: called with the "继续观看" path list and the index of the
            tapped card, so the playlist is the row the viewer saw.
        on_detail: called with the item of a "已看完" card.
        on_open_library: the empty state's CTA.
        on_cleared: called after the history was cleared, so the shell can
            re-sync the library page's progress bars.
    """

    def __init__(
        self,
        *,
        translator: Translator,
        library: LibraryPage,
        recents: object | None,
        on_play: Callable[[Sequence[Path], int], None],
        on_detail: Callable[[LibraryItem], None],
        on_open_library: Callable[[], None],
        on_cleared: Callable[[], None] | None = None,
    ) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.add_css_class("recent-page")
        self._t = translator
        self._library = library
        self._recents = recents
        self._on_play = on_play
        self._on_detail = on_detail
        self._on_open_library = on_open_library
        self._on_cleared = on_cleared
        self._view = RecentView()
        self._cards: dict[Path, LibraryCard] = {}
        self._models: dict[Path, HomeCard] = {}
        self._build()
        self.apply_language(translator)

    # -- construction ------------------------------------------------------

    def _build(self) -> None:
        self.append(self._build_head())
        self._stack = Gtk.Stack()
        self._stack.set_transition_type(Gtk.StackTransitionType.NONE)
        self._stack.set_vexpand(True)
        self._stack.add_named(self._build_content(), "content")
        self._stack.add_named(self._build_empty(), "empty")
        self._stack.set_visible_child_name("content")
        self.append(self._stack)

    def _build_head(self) -> Gtk.Widget:
        head = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        head.set_margin_bottom(12)
        self._title = Gtk.Label()
        self._title.add_css_class("page-title")
        self._stat = Gtk.Label()
        self._stat.add_css_class("page-stat")
        self._stat.set_valign(Gtk.Align.END)
        self._stat.set_margin_bottom(2)
        self._sub = Gtk.Label()
        self._sub.add_css_class("page-sub")
        self._sub.set_valign(Gtk.Align.END)
        self._sub.set_margin_bottom(3)
        head.append(self._title)
        head.append(self._stat)
        head.append(self._sub)
        spacer = Gtk.Box()
        spacer.set_hexpand(True)
        head.append(spacer)

        self._clear_button = Gtk.Button()
        self._clear_button.add_css_class("tool")
        self._clear_button.add_css_class("recent-clear")
        self._clear_label = Gtk.Label()
        self._clear_button.set_child(self._clear_label)
        self._clear_button.connect("clicked", self._on_clear_clicked)
        head.append(self._clear_button)
        return head

    def _build_content(self) -> Gtk.Widget:
        scroll = Gtk.ScrolledWindow()
        scroll.add_css_class("recent-scroll")
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_vexpand(True)
        self._groups = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self._groups.add_css_class("recent-groups")
        scroll.set_child(self._groups)
        return scroll

    def _build_empty(self) -> Gtk.Widget:
        board = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        board.add_css_class("recent-empty")
        board.set_vexpand(True)
        board.set_valign(Gtk.Align.FILL)

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
        self._empty_body.set_max_width_chars(44)
        self._empty_body.set_margin_top(10)
        main.append(self._empty_body)

        self._empty_button = Gtk.Button()
        self._empty_button.add_css_class("drop-secondary")
        self._empty_button.set_halign(Gtk.Align.CENTER)
        self._empty_button.set_margin_top(22)
        self._empty_button.connect("clicked", lambda _b: self._on_open_library())
        main.append(self._empty_button)
        board.append(main)
        return board

    # -- public API --------------------------------------------------------

    @property
    def view(self) -> RecentView:
        """The model currently on screen (tests and the QC probes read it)."""
        return self._view

    def summary(self) -> tuple[int, int]:
        """``(continue_count, finished_count)`` for the window headline."""
        return len(self._view.continue_cards), len(self._view.finished_cards)

    def refresh(self) -> None:
        """Re-derive the model from the scanned library and the history."""
        recents: tuple[object, ...] = ()
        if self._recents is not None:
            try:
                recents = self._recents.items(only_existing=True)
            except Exception:  # noqa: BLE001 - history must never break the page
                recents = ()
        self._view = recent_view(self._library.items, recents)
        self._render()

    def apply_language(self, translator: Translator) -> None:
        self._t = translator
        self._title.set_text(translator("recent_title"))
        self._clear_label.set_text(translator("recent_clear"))
        self._clear_button.set_tooltip_text(translator("recent_clear"))
        self._empty_title.set_text(translator("recent_empty_title"))
        self._empty_body.set_text(translator("recent_empty_body"))
        self._empty_button.set_label(translator("recent_open_library"))
        self.refresh()

    def on_poster(self, path: Path, _pixbuf: GdkPixbuf.Pixbuf | None) -> None:
        """A poster finished decoding; swap it into the matching card."""
        self._update_path(path)

    def on_metadata(self, path: Path, _metadata: VideoMetadata | None) -> None:
        """The library probed *path*; refresh the card's duration and meta."""
        self._update_path(path)

    # -- rendering ---------------------------------------------------------

    def _render(self) -> None:
        self._sync_head()
        empty = self._view.empty
        self._stack.set_visible_child_name("empty" if empty else "content")
        self._clear_button.set_sensitive(not empty)
        if empty:
            return
        self._rebuild_groups()

    def _sync_head(self) -> None:
        total = self._view.total
        self._stat.set_text(
            self._t("library_stat_one" if total == 1 else "library_stat", count=total)
        )
        self._sub.set_text(
            self._t(
                "recent_headline",
                continue_count=len(self._view.continue_cards),
                finished_count=len(self._view.finished_cards),
            )
        )

    def _rebuild_groups(self) -> None:
        while (child := self._groups.get_first_child()) is not None:
            self._groups.remove(child)
        self._cards.clear()
        self._models.clear()
        for key, cards in self._view.groups():
            self._groups.append(self._build_group(key, cards))

    def _build_group(self, key: str, cards: Sequence[HomeCard]) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        box.add_css_class("recent-group")

        head = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        head.add_css_class("recent-group-head")
        title = Gtk.Label(label=self._t(f"recent_group_{key}"))
        title.add_css_class("recent-group-title")
        count = Gtk.Label(label=str(len(cards)))
        count.add_css_class("recent-group-count")
        rule = Gtk.Box()
        rule.add_css_class("recent-rule")
        rule.set_hexpand(True)
        rule.set_size_request(-1, 1)
        rule.set_valign(Gtk.Align.CENTER)
        head.append(title)
        head.append(count)
        head.append(rule)
        box.append(head)

        grid = Gtk.Grid()
        grid.add_css_class("recent-cards")
        grid.set_column_spacing(16)
        grid.set_row_spacing(12)
        grid.set_valign(Gtk.Align.START)
        for index, card in enumerate(cards):
            widget = self._make_card(card, key)
            grid.attach(widget, index % GRID_COLUMNS, index // GRID_COLUMNS, 1, 1)
        box.append(grid)
        return box

    def _make_card(self, card: HomeCard, group: str) -> Gtk.Widget:
        widget = LibraryCard(
            card.item,
            on_activate=(
                self._play_card if group == "continue" else self._open_detail
            ),
        )
        widget.set_size_request(CARD_WIDTH, -1)
        self._cards[card.item.path] = widget
        self._models[card.item.path] = card
        self._update_card(widget, card)
        return widget

    def _update_path(self, path: Path) -> None:
        widget = self._cards.get(path)
        model = self._models.get(path)
        if widget is None or model is None:
            return
        item = self._library.item_for(path)
        if item is not None and item is not model.item:
            model = replace(model, item=item)
            self._models[path] = model
        self._update_card(widget, model)

    def _update_card(self, widget: LibraryCard, card: HomeCard) -> None:
        item = card.item
        widget.update(
            item,
            pixbuf=self._library.poster_for(item.path),
            duration_text=format_duration_ns(item.duration_ns) if item.duration_ns else "",
            badge=self._t("library_finished_badge") if card.finished else "",
            progress=card.progress,
            meta_text=self._meta_text(card),
            missing=not item.path.is_file(),
            missing_text=self._t("library_missing_file"),
        )

    def _meta_text(self, card: HomeCard) -> str:
        parts: list[str] = []
        when = format_watch_time(card.updated_at, t=self._t)
        if when:
            parts.append(when)
        if card.resumable:
            remaining = remaining_text(card.position_ns, card.duration_ns)
            if remaining:
                parts.append(self._t("home_remaining", time=remaining))
        return " · ".join(parts) if parts else self._t("library_no_meta")

    # -- events ------------------------------------------------------------

    def _play_card(self, item: LibraryItem) -> None:
        paths = [card.item.path for card in self._view.continue_cards]
        if item.path not in paths:
            paths = [item.path]
        self._on_play(paths, paths.index(item.path))

    def _open_detail(self, item: LibraryItem) -> None:
        self._on_detail(item)

    def _on_clear_clicked(self, _button: Gtk.Button) -> None:
        if self._recents is None:
            return
        dialog = Gtk.AlertDialog()
        dialog.set_modal(True)
        dialog.set_message(self._t("recent_clear_title"))
        dialog.set_detail(self._t("recent_clear_body"))
        dialog.set_buttons(
            [self._t("recent_cancel"), self._t("recent_clear_confirm")]
        )
        dialog.set_cancel_button(0)
        dialog.set_default_button(0)
        root = self.get_root()
        dialog.choose(
            root if isinstance(root, Gtk.Window) else None,
            None,
            self._on_clear_finished,
        )

    def _on_clear_finished(
        self, dialog: Gtk.AlertDialog, result: Gio.AsyncResult
    ) -> None:
        try:
            choice = dialog.choose_finish(result)  # type: ignore[arg-type]
        except GLib.Error:
            # Dismissed (Escape / window closed): nothing was cleared.
            return
        if choice != 1 or self._recents is None:
            return
        cleared = self._view.total
        self._recents.clear()
        log_action("recent_cleared", count=cleared)
        self.refresh()
        if self._on_cleared is not None:
            self._on_cleared()
