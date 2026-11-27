"""ui-v5 settings page: language, decode mode, scan folders and about.

The mockups left the rail's fourth entry greyed out ("settings page not drawn
yet"); this page is the minimal replacement the product asked for.  It is
built from the same pieces as the other browse pages -- ``.card-box`` cards,
``.spec-row`` lines and ``.tool`` buttons -- so it inherits the amber theme
without a new visual language.

The page never owns state: language and decode mode travel back to the window
through callbacks (the window persists the language and reopens the clip for a
decode change), and the folder list is read from the library page, which keeps
the XDG state file.  :meth:`SettingsPage.refresh` re-reads all three.
"""

from __future__ import annotations

import platform
from pathlib import Path
from typing import Callable, Protocol

import gi

gi.require_version("Gdk", "4.0")
gi.require_version("Pango", "1.0")
gi.require_version("Gtk", "4.0")
from gi.repository import Gtk, Pango  # noqa: E402

from .. import __version__  # noqa: E402
from ..config import DECODE_MODES, Config  # noqa: E402
from ..diagnostics import cache_dir, log_action  # noqa: E402
from ..i18n import Translator  # noqa: E402
from ..library import default_library_state_path  # noqa: E402
from ..settings import LANGUAGE_OPTIONS, FolderRow, about_rows, folder_rows  # noqa: E402

__all__ = ["SettingsPage"]

#: Page padding mirrors the library/home pages so the cards line up.
PAGE_PAD_X = 26
PAGE_PAD_TOP = 12
PAGE_PAD_BOTTOM = 10

#: Gap between the cards.
CARD_GAP = 12


class _LibraryLike(Protocol):
    """The slice of :class:`~video_player.ui.library_page.LibraryPage` used here."""

    def directories(self) -> tuple[Path, ...]: ...

    @property
    def store_directories(self) -> tuple[Path, ...]: ...

    def choose_directory(self) -> None: ...

    def rescan(self) -> None: ...

    def remove_directory(self, path: Path) -> None: ...


def _gstreamer_version() -> str:
    """GStreamer version string, or ``—`` when the runtime is unavailable."""
    try:
        gi.require_version("Gst", "1.0")
        from gi.repository import Gst  # noqa: PLC0415 - optional at runtime

        return Gst.version_string()
    except Exception:  # noqa: BLE001 - the about card must never crash
        return "—"


class SettingsPage(Gtk.Box):
    """The settings surface: three editable cards plus a read-only about card."""

    def __init__(
        self,
        *,
        translator: Translator,
        config: Config,
        library: _LibraryLike,
        hw_ready: bool = False,
        on_language: Callable[[str], None] | None = None,
        on_decode: Callable[[str], None] | None = None,
    ) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.add_css_class("settings-page")
        self._t = translator
        self._config = config
        self._library = library
        self._hw_ready = bool(hw_ready)
        self._on_language = on_language or (lambda language: None)
        self._on_decode = on_decode or (lambda mode: None)
        self._updating_language = False
        self._updating_decode = False
        #: ``(label, key)`` pairs re-translated by :meth:`apply_language`.
        self._text_labels: list[tuple[Gtk.Label, str]] = []

        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_vexpand(True)
        scroll.set_hexpand(True)
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=CARD_GAP)
        content.add_css_class("settings-content")
        content.set_margin_top(PAGE_PAD_TOP)
        content.set_margin_bottom(PAGE_PAD_BOTTOM)
        content.set_margin_start(PAGE_PAD_X)
        content.set_margin_end(PAGE_PAD_X)
        scroll.set_child(content)
        self.append(scroll)
        self._content = content

        self._language_card = self._build_language_card()
        self._decode_card = self._build_decode_card()
        self._folders_card = self._build_folders_card()
        self._about_card = self._build_about_card()
        for card in (
            self._language_card,
            self._decode_card,
            self._folders_card,
            self._about_card,
        ):
            content.append(card)
        self.refresh()

    # -- construction helpers ---------------------------------------------

    def _text(self, key: str, css: str = "settings-card-note") -> Gtk.Label:
        label = Gtk.Label()
        label.add_css_class(css)
        label.set_xalign(0.0)
        label.set_wrap(True)
        label.set_text(self._t(key))
        self._text_labels.append((label, key))
        return label

    def _card(self, title_key: str, note_key: str | None = None) -> tuple[Gtk.Box, Gtk.Box]:
        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        card.add_css_class("card-box")
        card.add_css_class("settings-card")
        title = Gtk.Label()
        title.add_css_class("card-box-title")
        title.set_xalign(0.0)
        title.set_text(self._t(title_key))
        self._text_labels.append((title, title_key))
        card.append(title)
        if note_key is not None:
            card.append(self._text(note_key))
        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        card.append(body)
        return card, body

    def _segmented(
        self,
        options: list[tuple[str, str]],
        on_toggled: Callable[[Gtk.ToggleButton, str], None],
    ) -> tuple[Gtk.Box, dict[str, Gtk.ToggleButton]]:
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        row.add_css_class("settings-seg")
        row.set_halign(Gtk.Align.START)
        buttons: dict[str, Gtk.ToggleButton] = {}
        first: Gtk.ToggleButton | None = None
        for value, label in options:
            button = Gtk.ToggleButton(label=label)
            button.add_css_class("settings-seg-button")
            if first is None:
                first = button
            else:
                button.set_group(first)
            button.connect("toggled", on_toggled, value)
            row.append(button)
            buttons[value] = button
        return row, buttons

    def _build_language_card(self) -> Gtk.Box:
        card, body = self._card("settings_language_title")
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=16)
        row.append(self._text("settings_language_hint"))
        self._language_seg, self._language_buttons = self._segmented(
            [(lang, self._t(f"settings_lang_{lang}")) for lang in LANGUAGE_OPTIONS],
            self._on_language_toggled,
        )
        self._language_seg.set_halign(Gtk.Align.END)
        self._language_seg.set_hexpand(True)
        row.append(self._language_seg)
        body.append(row)
        return card

    def _build_decode_card(self) -> Gtk.Box:
        card, body = self._card("decode_mode", "settings_decode_hint")
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=16)
        row.append(self._text("decode_note"))
        self._decode_seg, self._decode_buttons = self._segmented(
            [(mode, self._t(f"decode_{mode}")) for mode in DECODE_MODES],
            self._on_decode_toggled,
        )
        self._decode_seg.set_halign(Gtk.Align.END)
        self._decode_seg.set_hexpand(True)
        row.append(self._decode_seg)
        body.append(row)
        return card

    def _build_folders_card(self) -> Gtk.Box:
        card, body = self._card("settings_folders_title", "settings_folders_hint")
        self._folder_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        body.append(self._folder_box)

        footer = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        footer.set_margin_top(2)
        choose = Gtk.Button(label=self._t("open_folder"))
        choose.add_css_class("tool")
        choose.connect("clicked", self._on_choose_folder)
        rescan = Gtk.Button(label=self._t("library_rescan"))
        rescan.add_css_class("tool")
        rescan.connect("clicked", self._on_rescan)
        self._choose_button = choose
        self._rescan_button = rescan
        footer.append(choose)
        footer.append(rescan)
        body.append(footer)
        return card

    def _build_about_card(self) -> Gtk.Box:
        card, body = self._card("settings_about_title", "settings_about_hint")
        self._about_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        body.append(self._about_box)
        return card

    # -- public API --------------------------------------------------------

    def refresh(self) -> None:
        """Re-read language, decode mode, folders and the about facts."""
        self._sync_language()
        self._sync_decode()
        self._rebuild_folders()
        self._rebuild_about()

    def sync_decode_mode(self) -> None:
        """Mirror ``config.player.decode_mode`` (the window may change it)."""
        self._sync_decode()

    def apply_language(self, translator: Translator) -> None:
        """Re-label the static widgets after the window switched language."""
        self._t = translator
        for label, key in self._text_labels:
            label.set_text(self._t(key))
        for lang in LANGUAGE_OPTIONS:
            button = self._language_buttons[lang]
            button.set_label(self._t(f"settings_lang_{lang}"))
        for mode in DECODE_MODES:
            self._decode_buttons[mode].set_label(self._t(f"decode_{mode}"))
        self._choose_button.set_label(self._t("open_folder"))
        self._rescan_button.set_label(self._t("library_rescan"))
        self._sync_language()
        self._rebuild_folders()
        self._rebuild_about()

    # -- state sync --------------------------------------------------------

    def _sync_language(self) -> None:
        self._updating_language = True
        try:
            wanted = self._t.language
            for lang, button in self._language_buttons.items():
                if button.get_active() != (lang == wanted):
                    button.set_active(lang == wanted)
        finally:
            self._updating_language = False

    def _sync_decode(self) -> None:
        self._updating_decode = True
        try:
            wanted = self._config.player.decode_mode
            for mode, button in self._decode_buttons.items():
                if button.get_active() != (mode == wanted):
                    button.set_active(mode == wanted)
        finally:
            self._updating_decode = False

    # -- folders / about ---------------------------------------------------

    def _rebuild_folders(self) -> None:
        while (child := self._folder_box.get_first_child()) is not None:
            self._folder_box.remove(child)
        rows = folder_rows(
            self._config.library.directories,
            self._library.store_directories,
        )
        if not rows:
            empty = Gtk.Label(label=self._t("library_no_dirs"))
            empty.add_css_class("settings-hint")
            empty.set_xalign(0.0)
            self._folder_box.append(empty)
            return
        for row in rows:
            self._folder_box.append(self._folder_row(row))

    def _folder_row(self, row: FolderRow) -> Gtk.Widget:
        line = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        line.add_css_class("settings-dir-row")
        path = Gtk.Label(label=str(row.path))
        path.add_css_class("settings-dir-path")
        path.set_xalign(0.0)
        path.set_hexpand(True)
        path.set_ellipsize(Pango.EllipsizeMode.MIDDLE)
        path.set_tooltip_text(str(row.path))
        line.append(path)
        if not row.exists:
            badge = Gtk.Label(label=self._t("settings_folder_missing"))
            badge.add_css_class("settings-badge")
            badge.add_css_class("is-missing")
            line.append(badge)
        elif row.from_config:
            badge = Gtk.Label(label=self._t("settings_folder_config"))
            badge.add_css_class("settings-badge")
            line.append(badge)
        if row.removable:
            remove = Gtk.Button(label="×")
            remove.add_css_class("dir-remove")
            remove.set_tooltip_text(self._t("library_remove_dir"))
            remove.set_focus_on_click(False)
            remove.connect("clicked", self._on_remove_folder, row.path)
            line.append(remove)
        return line

    def _rebuild_about(self) -> None:
        while (child := self._about_box.get_first_child()) is not None:
            self._about_box.remove(child)
        facts = about_rows(
            version=__version__,
            platform_name=platform.system(),
            machine=platform.machine(),
            kernel=platform.release(),
            gtk=(
                f"{Gtk.get_major_version()}."
                f"{Gtk.get_minor_version()}."
                f"{Gtk.get_micro_version()}"
            ),
            gstreamer=_gstreamer_version(),
            decode_text=self._t(
                "settings_about_decode_hw"
                if self._hw_ready
                else "settings_about_decode_sw"
            ),
            state_path=default_library_state_path(),
            cache_path=cache_dir(),
        )
        for fact in facts:
            line = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
            line.add_css_class("spec-row")
            key = Gtk.Label()
            key.add_css_class("spec-key")
            key.set_xalign(0.0)
            key.set_size_request(96, -1)
            key.set_text(self._t(fact.key))
            value = Gtk.Label(label=fact.value)
            value.add_css_class("spec-value")
            value.set_xalign(0.0)
            value.set_hexpand(True)
            value.set_selectable(True)
            value.set_ellipsize(Pango.EllipsizeMode.MIDDLE)
            value.set_tooltip_text(fact.value)
            line.append(key)
            line.append(value)
            self._about_box.append(line)

    # -- events ------------------------------------------------------------

    def _on_language_toggled(self, button: Gtk.ToggleButton, language: str) -> None:
        if self._updating_language or not button.get_active():
            return
        if language != self._t.language:
            self._on_language(language)
        self._sync_language()

    def _on_decode_toggled(self, button: Gtk.ToggleButton, mode: str) -> None:
        if self._updating_decode or not button.get_active():
            return
        if mode != self._config.player.decode_mode:
            self._on_decode(mode)
        self._sync_decode()

    def _on_choose_folder(self, _button: Gtk.Button) -> None:
        log_action("settings_choose_folder")
        self._library.choose_directory()

    def _on_rescan(self, _button: Gtk.Button) -> None:
        log_action("settings_rescan")
        self._library.rescan()

    def _on_remove_folder(self, _button: Gtk.Button, path: Path) -> None:
        self._library.remove_directory(path)
        self.refresh()
