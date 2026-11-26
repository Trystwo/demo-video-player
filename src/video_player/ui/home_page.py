"""ui-v5 home page: the hero plate plus "continue watching" / "recently added".

Every value on this page comes from the scanned library and the play history;
there is no recommendation data on a local player, so the page keeps the two
rows the mockup asks for and binds them to real files:

* **hero** -- the most recently watched clip (with its resume position), or the
  newest file when the history is empty; "details" opens the detail page,
* **continue watching** -- the history, newest first, limited to the four cards
  the 1140 px grid holds,
* **recently added** -- the newest files by mtime.

The page is data-driven: :meth:`HomePage.refresh` re-derives the model from the
library page and the recents store, and the browse shell forwards probe and
poster arrivals so the hero and the posters fill in without another scan.
Posters are Cairo-drawn (:mod:`video_player.ui.paint`); the big hero frame is
requested at backdrop resolution and swapped in when ready.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Sequence

import gi

gi.require_version("GdkPixbuf", "2.0")
gi.require_version("Gtk", "4.0")
gi.require_version("Pango", "1.0")
from gi.repository import GdkPixbuf, Gtk, Pango  # noqa: E402

from ..browse import HOME_CONTINUE_LIMIT, HomeCard, HomeView, home_view, remaining_text
from ..i18n import Translator  # noqa: E402
from ..library import LibraryItem  # noqa: E402
from .chrome import format_duration_ns, normalise_codec, video_meta_text  # noqa: E402
from .library_page import CARD_WIDTH, LibraryCard, LibraryPage  # noqa: E402
from .paint import BackdropArt, GhostFrame  # noqa: E402
from .style import rgba  # noqa: E402

__all__ = ["HomePage"]

#: Hero plate: the mockup's 272 px is the *design* height, but the board window
#: is 710 px of content and must hold two 4-card rows as well.  The plate is
#: therefore the flexible band: it requests HERO_MIN_HEIGHT and expands to take
#: whatever the rows leave (measured: ~225 px on a 1280x768 window), so a
#: slightly taller font on the board shrinks the hero instead of pushing the
#: bottom row off screen.
HERO_MIN_HEIGHT = 170

#: Gap between the hero, the rows and the page edges.
HERO_GAP = 10
PAGE_PAD_TOP = 6
PAGE_PAD_X = 26
PAGE_PAD_BOTTOM = 6


class _Rule(Gtk.DrawingArea):
    """The 18x1 amber rule in front of the hero kicker."""

    def __init__(self) -> None:
        super().__init__()
        self.set_size_request(18, 1)
        self.set_valign(Gtk.Align.CENTER)
        self.set_draw_func(self._draw)

    def _draw(self, _area: Gtk.DrawingArea, cr: object, width: int, height: int) -> None:
        cr.set_line_width(1.0)  # type: ignore[attr-defined]
        cr.set_source_rgba(*rgba("accent", 0.55))  # type: ignore[attr-defined]
        cr.move_to(0.0, 0.5)  # type: ignore[attr-defined]
        cr.line_to(float(max(1, width)), 0.5)  # type: ignore[attr-defined]
        cr.stroke()  # type: ignore[attr-defined]


@dataclass
class _Row:
    """One "section head + four cards" row of the home page."""

    box: Gtk.Box
    title: Gtk.Label
    count: Gtk.Label
    more: Gtk.Button
    cards_box: Gtk.Box
    paths: tuple[Path, ...] = ()
    card_widgets: dict[Path, Gtk.Widget] = field(default_factory=dict)


class HomePage(Gtk.Box):
    """The browse shell's landing page (hero + two rows, no scrolling)."""

    def __init__(
        self,
        *,
        translator: Translator,
        library: LibraryPage,
        recents: object | None,
        on_play: Callable[[Sequence[Path], int], None],
        on_detail: Callable[[LibraryItem], None],
        on_open_library: Callable[[str], None],
        on_choose_directory: Callable[[], None],
        on_open_file: Callable[[], None],
        on_rescan: Callable[[], None],
        request_backdrop: Callable[[LibraryItem, Callable[[Path, GdkPixbuf | None], None]], GdkPixbuf | None],
    ) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.add_css_class("home-page")
        self._t = translator
        self._library = library
        self._recents = recents
        self._on_play = on_play
        self._on_detail = on_detail
        self._on_open_library = on_open_library
        self._on_choose_directory = on_choose_directory
        self._on_open_file = on_open_file
        self._on_rescan = on_rescan
        self._request_backdrop = request_backdrop
        self._view = HomeView()
        self._hero_card: HomeCard | None = None
        #: 1140 px backdrop for the current hero; the small card poster must
        #: never overwrite it when the thumbnail worker finishes later.
        self._hero_big: GdkPixbuf.Pixbuf | None = None
        self._hero_big_path: Path | None = None
        self._row_widgets: list[_Row] = []
        self._card_index: dict[Path, Gtk.Widget] = {}
        self._build()
        self.apply_language(self._t)

    # -- construction ------------------------------------------------------

    def _build(self) -> None:
        self.set_margin_top(PAGE_PAD_TOP)
        self.set_margin_bottom(PAGE_PAD_BOTTOM)
        self.set_margin_start(PAGE_PAD_X)
        self.set_margin_end(PAGE_PAD_X)

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        content.add_css_class("home-content")
        content.set_vexpand(True)
        content.append(self._build_hero())
        for filter_key in ("recent", "all"):
            row = self._build_row(filter_key)
            self._row_widgets.append(row)
            spacer = Gtk.Box()
            spacer.set_size_request(-1, HERO_GAP)
            content.append(spacer)
            content.append(row.box)

        self._stack = Gtk.Stack()
        self._stack.set_transition_type(Gtk.StackTransitionType.NONE)
        self._stack.add_named(content, "content")
        self._stack.add_named(self._build_empty_page(), "empty")
        self._stack.set_visible_child_name("content")
        self.append(self._stack)

    def _build_hero(self) -> Gtk.Widget:
        overlay = Gtk.Overlay()
        overlay.add_css_class("hero")
        overlay.set_size_request(-1, HERO_MIN_HEIGHT)
        overlay.set_vexpand(True)
        self._hero_art = BackdropArt("hero", HERO_MIN_HEIGHT)
        self._hero_art.set_vexpand(True)
        overlay.set_child(self._hero_art)

        self._hero_device_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=7)
        self._hero_device_box.add_css_class("hero-device-pill")
        self._hero_device_box.set_halign(Gtk.Align.END)
        self._hero_device_box.set_valign(Gtk.Align.START)
        self._hero_device_box.set_margin_top(15)
        self._hero_device_box.set_margin_end(20)
        self._hero_device_dot = Gtk.Label(label="●")
        self._hero_device_dot.add_css_class("hero-device-dot")
        self._hero_device = Gtk.Label()
        self._hero_device.add_css_class("hero-device")
        self._hero_device_box.append(self._hero_device_dot)
        self._hero_device_box.append(self._hero_device)
        overlay.add_overlay(self._hero_device_box)

        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        body.set_halign(Gtk.Align.START)
        body.set_valign(Gtk.Align.END)
        body.set_margin_start(26)
        body.set_margin_end(26)
        body.set_margin_bottom(20)
        body.set_size_request(560, -1)
        self._hero_body = body

        kicker = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=9)
        kicker.append(_Rule())
        self._hero_kicker = Gtk.Label()
        self._hero_kicker.add_css_class("hero-kicker")
        kicker.append(self._hero_kicker)
        body.append(kicker)

        self._hero_title = Gtk.Label()
        self._hero_title.add_css_class("hero-title")
        self._hero_title.set_xalign(0.0)
        self._hero_title.set_ellipsize(Pango.EllipsizeMode.END)
        self._hero_title.set_max_width_chars(30)
        self._hero_title.set_margin_top(9)
        body.append(self._hero_title)

        self._hero_meta = Gtk.Label()
        self._hero_meta.add_css_class("hero-meta")
        self._hero_meta.set_xalign(0.0)
        self._hero_meta.set_ellipsize(Pango.EllipsizeMode.MIDDLE)
        self._hero_meta.set_max_width_chars(64)
        self._hero_meta.set_margin_top(7)
        body.append(self._hero_meta)

        self._hero_path = Gtk.Label()
        self._hero_path.add_css_class("hero-path")
        self._hero_path.set_xalign(0.0)
        self._hero_path.set_ellipsize(Pango.EllipsizeMode.MIDDLE)
        self._hero_path.set_max_width_chars(60)
        self._hero_path.set_margin_top(8)
        body.append(self._hero_path)

        self._hero_cta = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        self._hero_cta.set_margin_top(15)
        self._hero_play = Gtk.Button()
        self._hero_play.add_css_class("btn-primary")
        play_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        play_icon = Gtk.Image.new_from_icon_name("media-playback-start-symbolic")
        play_icon.set_pixel_size(16)
        self._hero_play_label = Gtk.Label()
        play_box.append(play_icon)
        play_box.append(self._hero_play_label)
        self._hero_play.set_child(play_box)
        self._hero_play.connect("clicked", self._on_hero_play)
        self._hero_detail = Gtk.Button()
        self._hero_detail.add_css_class("btn-ghost")
        self._hero_detail.connect("clicked", self._on_hero_detail)
        self._hero_cta.append(self._hero_play)
        self._hero_cta.append(self._hero_detail)
        body.append(self._hero_cta)

        overlay.add_overlay(body)
        return overlay

    def _build_empty_page(self) -> Gtk.Widget:
        """First-run state: the page is empty, so explain why and what to do.

        This is not a hero with missing data: the mockup draws a whole empty
        page, and a fresh demo board will land here before anything is
        scanned, so it gets the same viewfinder language as the library's
        empty board (minus the scan-scope bars, which belong to the library).
        """
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        page.add_css_class("home-empty")
        page.set_vexpand(True)
        page.set_valign(Gtk.Align.FILL)
        main = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        main.set_vexpand(True)
        main.set_halign(Gtk.Align.CENTER)
        main.set_valign(Gtk.Align.CENTER)
        main.append(GhostFrame())
        self._empty_title = Gtk.Label()
        self._empty_title.add_css_class("hero-empty-title")
        self._empty_title.set_margin_top(22)
        main.append(self._empty_title)
        self._empty_body = Gtk.Label()
        self._empty_body.add_css_class("hero-empty-body")
        self._empty_body.set_justify(Gtk.Justification.CENTER)
        self._empty_body.set_wrap(True)
        self._empty_body.set_max_width_chars(52)
        self._empty_body.set_margin_top(10)
        main.append(self._empty_body)
        empty_cta = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        empty_cta.set_halign(Gtk.Align.CENTER)
        empty_cta.set_margin_top(22)
        self._empty_add_dir = Gtk.Button()
        self._empty_add_dir.add_css_class("btn-primary")
        self._empty_add_dir.connect("clicked", lambda _b: self._on_choose_directory())
        self._empty_open_file = Gtk.Button()
        self._empty_open_file.add_css_class("btn-ghost")
        self._empty_open_file.connect("clicked", lambda _b: self._on_open_file())
        self._empty_library = Gtk.Button()
        self._empty_library.add_css_class("link-button")
        self._empty_library.connect("clicked", lambda _b: self._on_open_library("all"))
        empty_cta.append(self._empty_add_dir)
        empty_cta.append(self._empty_open_file)
        empty_cta.append(self._empty_library)
        main.append(empty_cta)
        page.append(main)
        return page

    def _build_row(self, filter_key: str) -> _Row:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        box.add_css_class("home-row")
        head = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        head.add_css_class("row-head")
        title = Gtk.Label()
        title.add_css_class("row-title")
        count = Gtk.Label()
        count.add_css_class("row-count")
        count.set_valign(Gtk.Align.END)
        more = Gtk.Button()
        more.add_css_class("row-more")
        more.set_halign(Gtk.Align.END)
        more_label = Gtk.Label()
        more.set_child(more_label)
        more.connect("clicked", lambda _b: self._on_open_library(filter_key))
        spacer = Gtk.Box()
        spacer.set_hexpand(True)
        head.append(title)
        head.append(count)
        head.append(spacer)
        head.append(more)
        cards_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=16)
        cards_box.add_css_class("home-cards")
        box.append(head)
        box.append(cards_box)
        return _Row(box=box, title=title, count=count, more=more, cards_box=cards_box)

    # -- data --------------------------------------------------------------

    def refresh(self) -> None:
        """Re-derive the model from the library page and the history."""
        recents = ()
        if self._recents is not None:
            try:
                recents = self._recents.items(only_existing=True)
            except Exception:  # noqa: BLE001 - history must never break the page
                recents = ()
        self._view = home_view(self._library.items, recents, limit=HOME_CONTINUE_LIMIT)
        self._apply_view()

    def _apply_view(self) -> None:
        view = self._view
        self._hero_card = view.hero
        self._sync_hero()
        self._sync_row(
            self._row_widgets[0],
            continue_cards=view.continue_cards,
        )
        self._sync_row(
            self._row_widgets[1],
            added_items=view.added_items,
        )

    def _sync_hero(self) -> None:
        hero = self._hero_card
        empty = hero is None
        self._stack.set_visible_child_name("empty" if empty else "content")
        if empty:
            self._hero_art.set_pixbuf(None)
            self._apply_empty_language()
            return
        item = hero.item
        self._hero_title.set_text(item.title)
        self._hero_meta.set_text(self._hero_meta_text(hero))
        self._hero_kicker.set_text(self._kicker_text(item))
        self._hero_path.set_text(str(item.path))
        self._hero_device.set_text(self._device_text(item))
        if hero.resumable:
            self._hero_play_label.set_text(
                self._t("home_resume", time=format_duration_ns(hero.position_ns))
            )
        else:
            self._hero_play_label.set_text(self._t("home_play"))
        # Small poster first (the library already decoded it), then the
        # backdrop-resolution frame from the shared backdrop cache.
        if self._hero_big_path != item.path:
            self._hero_big_path = item.path
            self._hero_big = None
        self._hero_art.set_pixbuf(
            self._hero_big or self._library.poster_for(item.path)
        )
        big = self._request_backdrop(item, self._on_hero_backdrop)
        if big is not None:
            self._hero_big = big
            self._hero_art.set_pixbuf(big)

    def _on_hero_backdrop(self, path: Path, pixbuf: GdkPixbuf.Pixbuf | None) -> None:
        hero = self._hero_card
        if pixbuf is None or hero is None or hero.item.path != path:
            return
        self._hero_big = pixbuf
        self._hero_big_path = path
        self._hero_art.set_pixbuf(pixbuf)

    def _sync_row(
        self,
        row: _Row,
        *,
        continue_cards: Sequence[HomeCard] = (),
        added_items: Sequence[LibraryItem] = (),
    ) -> None:
        entries: list[tuple[LibraryItem, HomeCard | None]] = (
            [(card.item, card) for card in continue_cards]
            if continue_cards
            else [(item, None) for item in added_items]
        )
        paths = tuple(item.path for item, _ in entries)
        section_visible = bool(entries)
        row.box.set_visible(section_visible)
        if not section_visible:
            return
        is_continue = bool(continue_cards)
        row.more.set_visible(True)
        count_key = "home_row_count"
        row.count.set_text(self._t(count_key, count=len(entries)))
        if is_continue:
            row.title.set_text(self._t("home_continue"))
        else:
            row.title.set_text(self._t("home_added"))
        if row.paths != paths:
            self._rebuild_row_cards(row, entries, is_continue=is_continue)
            row.paths = paths
            return
        for item, card in entries:
            widget = row.card_widgets.get(item.path)
            if isinstance(widget, Gtk.Button):
                self._update_card(widget, item, card, is_continue=is_continue)

    def _rebuild_row_cards(
        self,
        row: _Row,
        entries: Sequence[tuple[LibraryItem, HomeCard | None]],
        *,
        is_continue: bool,
    ) -> None:
        while (child := row.cards_box.get_first_child()) is not None:
            row.cards_box.remove(child)
        row.card_widgets.clear()
        for item, card in entries:
            widget = self._make_card(item, card, is_continue=is_continue)
            row.cards_box.append(widget)
            row.card_widgets[item.path] = widget

    def _make_card(
        self, item: LibraryItem, card: HomeCard | None, *, is_continue: bool
    ) -> Gtk.Widget:
        widget = LibraryCard(
            item,
            on_activate=(
                self._activate_continue if is_continue else self._activate_added
            ),
        )
        widget.set_size_request(CARD_WIDTH, -1)
        self._update_card(widget, item, card, is_continue=is_continue)
        return widget

    # -- section callbacks -------------------------------------------------

    def _activate_continue(self, item: LibraryItem) -> None:
        paths = [card.item.path for card in self._view.continue_cards]
        if item.path not in paths:
            paths = [item.path]
        self._on_play(paths, paths.index(item.path))

    def _activate_added(self, item: LibraryItem) -> None:
        self._on_detail(item)

    def _on_hero_play(self, _button: Gtk.Button) -> None:
        hero = self._hero_card
        if hero is None:
            return
        paths = [card.item.path for card in self._view.continue_cards] or [hero.item.path]
        if hero.item.path not in paths:
            paths = [hero.item.path]
        self._on_play(paths, paths.index(hero.item.path))

    def _on_hero_detail(self, _button: Gtk.Button) -> None:
        if self._hero_card is not None:
            self._on_detail(self._hero_card.item)

    # -- rendering helpers -------------------------------------------------

    def _update_card(
        self,
        card: Gtk.Button,
        item: LibraryItem,
        entry: HomeCard | None,
        *,
        is_continue: bool,
    ) -> None:
        progress = entry.progress if entry is not None else 0.0
        finished = bool(entry is not None and entry.finished)
        badge = ""
        badge_accent = False
        if finished:
            badge = self._t("home_finished")
        elif is_continue and progress > 0.0:
            badge = ""
        if is_continue:
            meta = self._continue_meta(entry, item) if entry is not None else ""
        else:
            meta = self._item_meta(item)
        duration_text = (
            format_duration_ns(item.duration_ns) if item.duration_ns > 0 else ""
        )
        card.update(
            item,
            pixbuf=self._library.poster_for(item.path),
            duration_text=duration_text,
            badge=badge,
            badge_accent=badge_accent,
            progress=progress,
            meta_text=meta,
            missing=not item.path.is_file(),
            missing_text=self._t("library_missing_file"),
        )

    def _continue_meta(self, entry: HomeCard, item: LibraryItem) -> str:
        codec = ""
        if item.metadata is not None and item.metadata.codec:
            codec = normalise_codec(item.metadata.codec)
        if entry.finished:
            # The card already carries the "finished" badge; repeating it here
            # would print the same word twice.
            return codec or format_duration_ns(entry.duration_ns)
        remaining = remaining_text(entry.position_ns, entry.duration_ns)
        parts: list[str] = []
        if item.metadata is not None and item.metadata.codec:
            parts.append(normalise_codec(item.metadata.codec))
        if remaining:
            parts.append(self._t("home_remaining", time=remaining))
        return " · ".join(parts) if parts else self._item_meta(item)

    def _item_meta(self, item: LibraryItem) -> str:
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

    def _device_text(self, item: LibraryItem) -> str:
        meta = item.metadata
        if meta is None:
            return self._t("rail_ready") if self._library.hardware_ready else ""
        hw = self._library.hardware_ready and meta.codec.lower().replace("x-", "") in {
            "h264",
            "avc",
            "h265",
            "hevc",
        }
        return self._t(
            "home_device",
            picture=f"{meta.width}×{meta.height}",
            codec=normalise_codec(meta.codec) if meta.codec else "—",
            decode=self._t("decode_hw" if hw else "decode_sw"),
            fps=f"{meta.fps:.0f}" if meta.fps > 0 else "—",
        )

    def _folder_siblings(self, item: LibraryItem) -> list[LibraryItem]:
        return [
            candidate
            for candidate in self._library.items
            if candidate.path.parent == item.path.parent
        ]

    def _hero_meta_text(self, hero: HomeCard) -> str:
        """``00:06 · 1080p30 · H.265 · 63.7 MB`` -- probe facts, no folder repeat."""
        item = hero.item
        duration = (
            format_duration_ns(hero.duration_ns)
            if hero.duration_ns > 0
            else format_duration_ns(item.duration_ns)
        )
        return " · ".join(part for part in (duration, self._item_meta(item)) if part)

    def _kicker_text(self, item: LibraryItem) -> str:
        folder = item.path.parent.name or str(item.path.parent)
        return self._t(
            "home_kicker", folder=folder, count=len(self._folder_siblings(item)) or 1
        )

    # -- public hooks ------------------------------------------------------

    @property
    def hero_item(self) -> LibraryItem | None:
        return self._hero_card.item if self._hero_card is not None else None

    def on_poster(self, path: Path, pixbuf: GdkPixbuf.Pixbuf | None) -> None:
        """A library thumbnail arrived: refresh the matching card and the hero."""
        if pixbuf is None:
            return
        for row in self._row_widgets:
            widget = row.card_widgets.get(path)
            if isinstance(widget, Gtk.Button):
                widget.thumb.set_poster(pixbuf)
        hero = self._hero_card
        if (
            hero is not None
            and hero.item.path == path
            and self._hero_big is None
        ):
            self._hero_art.set_pixbuf(pixbuf)

    def on_metadata(self, path: Path, metadata: object) -> None:
        """A probe finished: the meta line and the device line may have changed."""
        self.refresh()

    def apply_language(self, translator: Translator) -> None:
        self._t = translator
        t = translator
        rows = self._row_widgets
        self._hero_detail.set_label(t("home_details"))
        self._apply_empty_language()
        if len(rows) >= 2:
            rows[0].more.get_child().set_text(t("home_all"))
            rows[1].more.get_child().set_text(t("home_all"))
        self._apply_empty_language()
        self._apply_view()

    def _apply_empty_language(self) -> None:
        if not self._view.empty:
            return
        t = self._t
        self._empty_title.set_text(t("home_empty_title"))
        self._empty_body.set_text(t("home_empty_body"))
        self._empty_add_dir.set_label(t("library_add_dir"))
        self._empty_open_file.set_label(t("library_open_file"))
        self._empty_library.set_label(t("home_open_library"))
