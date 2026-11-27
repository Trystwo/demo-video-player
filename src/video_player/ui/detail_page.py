"""ui-v5 detail page: backdrop plate, episode grid and the clip-facts card.

The mockup drew a streaming series page; this is a local-file player, so the
page is bound to what the library actually knows:

* the backdrop is the clip's own poster frame, with the title, folder chips and
  a resume/replay CTA over it,
* "episodes" are the files that share the clip's folder (a one-level scan means
  a folder is the only series this player has),
* the right column carries the probe facts (codec / picture / fps / duration /
  size / derived bitrate / decode) and a same-folder list.

No rating, cast or synopsis is invented: the page shows a real file path where
the mockup had a blurb.  The big backdrop poster is requested from the shared
backdrop cache; until it arrives the library's card poster is shown.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable, Sequence

import gi

gi.require_version("GdkPixbuf", "2.0")
gi.require_version("Gtk", "4.0")
gi.require_version("Pango", "1.0")
from gi.repository import GdkPixbuf, GLib, Gtk, Pango  # noqa: E402

from ..browse import DetailEpisode, DetailView, detail_view
from ..i18n import Translator  # noqa: E402
from ..library import LibraryItem  # noqa: E402
from .chrome import (  # noqa: E402
    format_duration_ns,
    format_resolution,
    format_size,
    normalise_codec,
)
from .library_page import LibraryPage  # noqa: E402
from .paint import BackdropArt, Poster  # noqa: E402

__all__ = ["DetailPage"]

#: Backdrop plate height: the mockup's 304 px scaled to the 710 px content area
#: the board actually has, leaving ~414 px for the two-column body.
BACKDROP_HEIGHT = 268
BODY_GAP = 12
PAGE_PAD_TOP = 8
PAGE_PAD_X = 26
PAGE_PAD_BOTTOM = 8

#: Left column: 3 x 237 px episode cards (237*3 + 14*2 = 739 of the 740 column).
EP_COLUMNS = 3
EP_WIDTH = 237
EP_THUMB_HEIGHT = 133
LEFT_WIDTH = 740
RIGHT_WIDTH = 370

#: Same-folder rows on the right (the mockup's "同系列" slot).
SIBLING_ROWS = 3


class _EpisodeCard(Gtk.Button):
    """One episode tile: poster (with progress hairline), title and duration."""

    def __init__(
        self,
        episode: DetailEpisode,
        *,
        on_activate: Callable[[LibraryItem], None],
        translator: Translator,
    ) -> None:
        super().__init__()
        self.add_css_class("ep-card")
        self.set_focus_on_click(False)
        self.set_size_request(EP_WIDTH, -1)
        self.set_halign(Gtk.Align.START)
        self.episode = episode
        self._on_activate = on_activate
        self._t = translator
        self.set_tooltip_text(str(episode.item.path))
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.poster = Poster(height=EP_THUMB_HEIGHT, radius=8.0)
        box.append(self.poster)
        fig = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        fig.set_margin_top(7)
        self.title_label = Gtk.Label(label=episode.item.title)
        self.title_label.add_css_class("ep-title")
        self.title_label.set_xalign(0.0)
        self.title_label.set_hexpand(True)
        self.title_label.set_ellipsize(Pango.EllipsizeMode.END)
        self.title_label.set_max_width_chars(18)
        self.duration_label = Gtk.Label()
        self.duration_label.add_css_class("ep-duration")
        self.duration_label.set_valign(Gtk.Align.END)
        fig.append(self.title_label)
        fig.append(self.duration_label)
        box.append(fig)
        self.set_child(box)
        self.connect("clicked", self._activate)
        self.update(episode)

    def _activate(self, _button: Gtk.Button) -> None:
        self._on_activate(self.episode.item)

    def update(
        self,
        episode: DetailEpisode,
        *,
        pixbuf: GdkPixbuf.Pixbuf | None = None,
    ) -> None:
        self.episode = episode
        self.set_tooltip_text(str(episode.item.path))
        if episode.current:
            self.add_css_class("now")
        else:
            self.remove_css_class("now")
        if episode.item.title != self.title_label.get_text():
            self.title_label.set_text(episode.item.title)
        duration = (
            format_duration_ns(episode.duration_ns)
            if episode.duration_ns > 0
            else format_duration_ns(episode.item.duration_ns)
        )
        self.duration_label.set_text(duration)
        badge = self._t("detail_now_playing") if episode.current else ""
        self.poster.set_content(
            pixbuf=pixbuf,
            duration=duration,
            badge=badge,
            badge_accent=episode.current,
            progress=episode.progress,
            current=episode.current,
            missing=not episode.item.path.is_file(),
            missing_text=self._t("library_missing_file"),
        )


class DetailPage(Gtk.Box):
    """Backdrop + episode grid + clip facts for one library item."""

    def __init__(
        self,
        *,
        translator: Translator,
        library: LibraryPage,
        recents: object | None,
        on_play: Callable[[Sequence[Path], int, int | None], None],
        on_back: Callable[[], None],
        on_select: Callable[[LibraryItem], None],
        request_backdrop: Callable[
            [LibraryItem, Callable[[Path, GdkPixbuf | None], None]],
            GdkPixbuf.Pixbuf | None,
        ],
    ) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.add_css_class("detail-page")
        self._t = translator
        self._library = library
        self._recents = recents
        self._on_play = on_play
        self._on_back = on_back
        self._on_select = on_select
        self._request_backdrop = request_backdrop
        self._item: LibraryItem | None = None
        self._view: DetailView | None = None
        #: 1140 px backdrop for the opened clip; a later small poster arriving
        #: from the library thumbnail worker must not downgrade it.
        self._big_path: Path | None = None
        self._episode_cards: dict[object, _EpisodeCard] = {}
        self._build()
        self.apply_language(self._t)

    # -- construction ------------------------------------------------------

    def _build(self) -> None:
        self.set_margin_top(PAGE_PAD_TOP)
        self.set_margin_bottom(PAGE_PAD_BOTTOM)
        self.set_margin_start(PAGE_PAD_X)
        self.set_margin_end(PAGE_PAD_X)
        self.append(self._build_backdrop())
        gap = Gtk.Box()
        gap.set_size_request(-1, BODY_GAP)
        self.append(gap)
        self.append(self._build_body())

    def _build_backdrop(self) -> Gtk.Widget:
        overlay = Gtk.Overlay()
        overlay.add_css_class("detail-backdrop")
        overlay.set_size_request(-1, BACKDROP_HEIGHT)
        self._art = BackdropArt("detail", BACKDROP_HEIGHT)
        overlay.set_child(self._art)

        self._back_button = Gtk.Button()
        self._back_button.add_css_class("detail-back")
        back_glyph = Gtk.Label(label="\u2039")
        back_glyph.add_css_class("detail-back-glyph")
        self._back_button.set_child(back_glyph)
        self._back_button.set_halign(Gtk.Align.START)
        self._back_button.set_valign(Gtk.Align.START)
        self._back_button.set_margin_top(18)
        self._back_button.set_margin_start(18)
        self._back_button.set_tooltip_text(self._t("detail_back"))
        self._back_button.connect("clicked", lambda _b: self._on_back())
        overlay.add_overlay(self._back_button)

        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        body.set_halign(Gtk.Align.START)
        body.set_valign(Gtk.Align.END)
        body.set_margin_start(26)
        body.set_margin_end(26)
        body.set_margin_bottom(20)
        body.set_size_request(720, -1)

        self._chips = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=7)
        self._chips.set_margin_bottom(11)
        body.append(self._chips)

        title_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=14)
        title_row.set_valign(Gtk.Align.END)
        self._title_label = Gtk.Label()
        self._title_label.add_css_class("bd-title")
        self._title_label.set_xalign(0.0)
        self._title_label.set_ellipsize(Pango.EllipsizeMode.END)
        self._title_label.set_max_width_chars(26)
        self._sub_label = Gtk.Label()
        self._sub_label.add_css_class("bd-sub")
        self._sub_label.set_xalign(0.0)
        self._sub_label.set_valign(Gtk.Align.END)
        self._sub_label.set_margin_bottom(3)
        title_row.append(self._title_label)
        title_row.append(self._sub_label)
        body.append(title_row)

        self._path_label = Gtk.Label()
        self._path_label.add_css_class("bd-path")
        self._path_label.set_xalign(0.0)
        self._path_label.set_ellipsize(Pango.EllipsizeMode.MIDDLE)
        self._path_label.set_max_width_chars(72)
        self._path_label.set_margin_top(9)
        body.append(self._path_label)

        cta = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        cta.set_margin_top(15)
        self._play_button = Gtk.Button()
        self._play_button.add_css_class("btn-primary")
        play_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        play_icon = Gtk.Image.new_from_icon_name("media-playback-start-symbolic")
        play_icon.set_pixel_size(16)
        self._play_label = Gtk.Label()
        play_box.append(play_icon)
        play_box.append(self._play_label)
        self._play_button.set_child(play_box)
        self._play_button.connect("clicked", self._on_play_current)
        self._replay_button = Gtk.Button()
        self._replay_button.add_css_class("btn-ghost")
        self._replay_button.connect("clicked", self._on_replay_current)
        cta.append(self._play_button)
        cta.append(self._replay_button)
        body.append(cta)
        overlay.add_overlay(body)
        return overlay

    def _build_body(self) -> Gtk.Widget:
        grid = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=30)
        grid.set_vexpand(True)
        grid.append(self._build_left())
        grid.append(self._build_right())
        return grid

    def _build_left(self) -> Gtk.Widget:
        left = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        left.set_size_request(LEFT_WIDTH, -1)
        left.set_valign(Gtk.Align.FILL)
        head = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        head.add_css_class("row-head")
        self._episodes_title = Gtk.Label()
        self._episodes_title.add_css_class("row-title")
        self._episodes_count = Gtk.Label()
        self._episodes_count.add_css_class("row-count")
        self._episodes_count.set_valign(Gtk.Align.END)
        self._auto_next = Gtk.Label()
        self._auto_next.add_css_class("row-note")
        self._auto_next.set_halign(Gtk.Align.END)
        spacer = Gtk.Box()
        spacer.set_hexpand(True)
        head.append(self._episodes_title)
        head.append(self._episodes_count)
        head.append(spacer)
        head.append(self._auto_next)
        left.append(head)

        scroll = Gtk.ScrolledWindow()
        scroll.add_css_class("episode-scroll")
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_vexpand(True)
        scroll.set_margin_top(6)
        self._grid = Gtk.Grid()
        self._grid.add_css_class("episode-grid")
        self._grid.set_column_spacing(14)
        self._grid.set_row_spacing(12)
        self._grid.set_valign(Gtk.Align.START)
        scroll.set_child(self._grid)
        self._scroll = scroll
        left.append(scroll)

        self._foot = Gtk.Label()
        self._foot.add_css_class("detail-foot")
        self._foot.set_xalign(0.0)
        self._foot.set_margin_top(8)
        self._foot.set_ellipsize(Pango.EllipsizeMode.END)
        left.append(self._foot)
        return left

    def _build_right(self) -> Gtk.Widget:
        right = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        right.set_size_request(RIGHT_WIDTH, -1)
        right.set_valign(Gtk.Align.START)

        spec_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        spec_box.add_css_class("card-box")
        self._spec_title = Gtk.Label()
        self._spec_title.add_css_class("card-box-title")
        self._spec_title.set_xalign(0.0)
        self._spec_title.set_margin_bottom(8)
        spec_box.append(self._spec_title)
        self._spec_keys: list[Gtk.Label] = []
        self._spec_values: list[Gtk.Label] = []
        for _ in range(7):
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            row.add_css_class("spec-row")
            key = Gtk.Label()
            key.add_css_class("spec-key")
            key.set_xalign(0.0)
            key.set_hexpand(True)
            value = Gtk.Label()
            value.add_css_class("spec-value")
            value.set_xalign(1.0)
            row.append(key)
            row.append(value)
            spec_box.append(row)
            self._spec_keys.append(key)
            self._spec_values.append(value)
        right.append(spec_box)

        rel_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        rel_box.add_css_class("card-box")
        self._rel_title = Gtk.Label()
        self._rel_title.add_css_class("card-box-title")
        self._rel_title.set_xalign(0.0)
        self._rel_title.set_margin_bottom(6)
        rel_box.append(self._rel_title)
        self._rel_rows = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        rel_box.append(self._rel_rows)
        self._rel_empty = Gtk.Label()
        self._rel_empty.add_css_class("lib-empty-note")
        self._rel_empty.set_xalign(0.0)
        self._rel_empty.set_visible(False)
        rel_box.append(self._rel_empty)
        right.append(rel_box)
        return right

    # -- data --------------------------------------------------------------

    def show(self, item: LibraryItem) -> None:
        """Render the page for *item* (the card the user opened)."""
        self._item = item
        self.refresh()
        # Small poster first; the backdrop cache upgrades it when ready.
        self._art.set_pixbuf(self._library.poster_for(item.path))
        big = self._request_backdrop(item, self._on_backdrop)
        if big is not None:
            self._art.set_pixbuf(big)

    def refresh(self) -> None:
        """Re-derive the view after a probe/poster update or language switch."""
        item = self._item
        if item is None:
            return
        # The card handed us a LibraryItem; a late metadata probe replaces that
        # object in the library, so re-resolve by path before building facts.
        for candidate in self._library.items:
            if candidate.path == item.path:
                item = candidate
                self._item = candidate
                break
        recents = ()
        if self._recents is not None:
            try:
                recents = self._recents.items(only_existing=True)
            except Exception:  # noqa: BLE001
                recents = ()
        self._view = detail_view(
            item,
            self._library.items,
            recents=recents,
            hw_ready=self._library.hardware_ready,
            t=self._t,
        )
        self._sync_backdrop()
        self._sync_spec()
        self._sync_episodes()
        self._sync_siblings()

    def _on_backdrop(self, path: Path, pixbuf: GdkPixbuf.Pixbuf | None) -> None:
        if pixbuf is None or self._item is None or self._item.path != path:
            return
        self._big_path = path
        self._art.set_pixbuf(pixbuf)

    def _sync_backdrop(self) -> None:
        view = self._view
        if view is None:
            return
        item = view.item
        self._title_label.set_text(item.title)
        self._path_label.set_text(str(item.path))
        duration = (
            format_duration_ns(view.duration_ns) if view.duration_ns > 0 else "—"
        )
        self._sub_label.set_text(
            f"{self._t('detail_file_count', count=len(view.episodes))} · {duration}"
        )
        self._sync_chips()
        if view.resumable:
            self._play_label.set_text(
                self._t("detail_resume", time=format_duration_ns(view.position_ns))
            )
        else:
            self._play_label.set_text(self._t("detail_play"))
        self._replay_button.set_label(self._t("detail_replay"))
        # "Replay" only means something when the primary CTA would resume.
        self._replay_button.set_visible(view.resumable)

    def _sync_chips(self) -> None:
        while (child := self._chips.get_first_child()) is not None:
            self._chips.remove(child)
        view = self._view
        if view is None:
            return
        item = view.item
        meta = item.metadata
        chips: list[tuple[str, str]] = []
        folder = item.path.parent.name or str(item.path.parent)
        chips.append(("bd-chip", folder))
        chips.append(("bd-chip", self._t("detail_file_count", count=len(view.episodes))))
        if meta is not None and meta.width > 0 and meta.height > 0:
            chips.append(
                (
                    "bd-chip bd-chip-mono",
                    format_resolution(meta.width, meta.height, meta.fps),
                )
            )
        if meta is not None and meta.codec:
            chips.append(("bd-chip bd-chip-mono", normalise_codec(meta.codec)))
        for css, text in chips:
            label = Gtk.Label(label=text)
            for klass in css.split():
                label.add_css_class(klass)
            self._chips.append(label)

    def _sync_spec(self) -> None:
        view = self._view
        if view is None:
            return
        self._spec_title.set_text(self._t("detail_spec_title"))
        for index, (key_label, value_label) in enumerate(
            zip(self._spec_keys, self._spec_values)
        ):
            if index < len(view.facts):
                fact = view.facts[index]
                key_label.set_text(self._t(fact.key))
                value_label.set_text(fact.value)
                if fact.accent:
                    value_label.add_css_class("is-accent")
                else:
                    value_label.remove_css_class("is-accent")
            else:
                key_label.set_text("")
                value_label.set_text("")

    def _sync_episodes(self) -> None:
        view = self._view
        if view is None:
            return
        self._episodes_title.set_text(self._t("detail_episodes"))
        self._episodes_count.set_text(
            self._t("detail_file_count", count=len(view.episodes))
        )
        self._auto_next.set_text(self._t("detail_auto_next"))
        self._foot.set_text(
            self._t(
                "detail_foot",
                count=len(view.episodes),
                size=format_size(view.total_size),
            )
        )
        while (child := self._grid.get_first_child()) is not None:
            self._grid.remove(child)
        self._episode_cards.clear()
        for index, episode in enumerate(view.episodes):
            card = _EpisodeCard(
                episode,
                on_activate=self._play_episode,
                translator=self._t,
            )
            card.update(
                episode,
                pixbuf=self._library.poster_for(episode.item.path),
            )
            self._episode_cards[episode.item.path] = card
            self._grid.attach(
                card, index % EP_COLUMNS, index // EP_COLUMNS, 1, 1
            )
        # The opened clip may be on a row below the fold: scroll the episode
        # grid so the amber "now playing" card is visible when the page opens.
        GLib.idle_add(self._scroll_to_current)

    def _scroll_to_current(self) -> bool:
        view = self._view
        if view is None:
            return GLib.SOURCE_REMOVE
        card = self._episode_cards.get(view.item.path)
        if card is None:
            return GLib.SOURCE_REMOVE
        row = view.index // EP_COLUMNS
        height = card.get_height() or (EP_THUMB_HEIGHT + 37)
        spacing = self._grid.get_row_spacing()
        target = row * (height + spacing)
        adjustment = self._scroll.get_vadjustment()
        limit = max(0.0, adjustment.get_upper() - adjustment.get_page_size())
        adjustment.set_value(min(max(0.0, target), limit))
        return GLib.SOURCE_REMOVE

    def _sync_siblings(self) -> None:
        view = self._view
        while (child := self._rel_rows.get_first_child()) is not None:
            self._rel_rows.remove(child)
        if view is None:
            return
        siblings = view.siblings[:SIBLING_ROWS]
        self._rel_title.set_text(self._t("detail_folder"))
        self._rel_empty.set_text(self._t("detail_no_siblings"))
        self._rel_empty.set_visible(not siblings)
        for episode in siblings:
            self._rel_rows.append(
                self._build_sibling_row(episode)
            )

    def _build_sibling_row(self, episode: DetailEpisode) -> Gtk.Widget:
        button = Gtk.Button()
        button.add_css_class("rel-row")
        button.set_focus_on_click(False)
        button.set_tooltip_text(str(episode.item.path))
        button.connect(
            "clicked", lambda _b, item=episode.item: self._on_select(item)
        )
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        thumb = Poster(height=36, radius=6.0, show_duration=False)
        thumb.set_size_request(64, 36)
        thumb.set_halign(Gtk.Align.START)
        thumb.set_content(pixbuf=self._library.poster_for(episode.item.path))
        box.append(thumb)
        title = Gtk.Label(label=episode.item.title)
        title.add_css_class("rel-title")
        title.set_xalign(0.0)
        title.set_hexpand(True)
        title.set_ellipsize(Pango.EllipsizeMode.END)
        title.set_max_width_chars(22)
        duration = Gtk.Label(
            label=(
                format_duration_ns(episode.duration_ns)
                if episode.duration_ns > 0
                else format_duration_ns(episode.item.duration_ns)
            )
        )
        duration.add_css_class("rel-duration")
        duration.set_valign(Gtk.Align.CENTER)
        box.append(title)
        box.append(duration)
        button.set_child(box)
        return button

    # -- event handlers ----------------------------------------------------

    def _episode_playlist(self) -> list:
        view = self._view
        if view is None:
            return []
        return [episode.item.path for episode in view.episodes]

    def _on_play_current(self, _button: Gtk.Button) -> None:
        view = self._view
        if view is None:
            return
        paths = self._episode_playlist()
        self._on_play(paths, paths.index(view.item.path), None)

    def _on_replay_current(self, _button: Gtk.Button) -> None:
        """Replay ignores the resume position: start_position_ns=0."""
        view = self._view
        if view is None:
            return
        paths = self._episode_playlist()
        self._on_play(paths, paths.index(view.item.path), 0)

    def _play_episode(self, item: LibraryItem) -> None:
        paths = self._episode_playlist()
        if item.path not in paths:
            paths = [item.path]
        self._on_play(paths, paths.index(item.path), None)

    # -- public hooks ------------------------------------------------------

    @property
    def item(self) -> LibraryItem | None:
        return self._item

    @property
    def view(self) -> DetailView | None:
        return self._view

    def on_poster(self, path: Path, pixbuf: GdkPixbuf.Pixbuf | None) -> None:
        if pixbuf is None:
            return
        card = self._episode_cards.get(path)
        if card is not None:
            card.update(card.episode, pixbuf=pixbuf)
        if (
            self._item is not None
            and self._item.path == path
            and self._big_path is None
        ):
            self._art.set_pixbuf(pixbuf)

    def on_metadata(self, path: Path, metadata: object) -> None:
        self.refresh()

    def apply_language(self, translator: Translator) -> None:
        self._t = translator
        t = translator
        self._back_button.set_tooltip_text(t("detail_back"))
        self._spec_title.set_text(t("detail_spec_title"))
        self._rel_title.set_text(t("detail_folder"))
        self._rel_empty.set_text(t("detail_no_siblings"))
        if self._view is not None:
            self.refresh()
        else:
            self._episodes_title.set_text(t("detail_episodes"))
            self._auto_next.set_text(t("detail_auto_next"))
            self._replay_button.set_label(t("detail_replay"))
            self._play_label.set_text(t("detail_play"))
