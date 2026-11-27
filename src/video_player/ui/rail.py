"""The 88 px left rail shared by every ui-v6 browse page.

The rail lists the shell's sections -- home, library, recent, settings -- and
owns the two bits of machine status the mockup keeps at its foot (hardware
decode readiness, scan clock).  It holds no page state: clicking an entry calls
back into the browse shell, which decides which page and filter the entry maps
to.  Icons are Cairo paths because the board image ships a trimmed icon theme
where a missing symbolic name renders as a blank button.

2026-11-27 (ui-v6 scheme C): the 42x42 "面" mark at the top was removed by the
user's decision, together with its plate; the rail keeps padding-top 14 and the
nav keeps margin-top 24, so the entries move up into the space the mark used to
occupy.  The current entry is marked by scheme C's 2 px bone cursor instead of
a filled amber plate.
"""

from __future__ import annotations

from typing import Callable

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk  # noqa: E402

from ..i18n import Translator  # noqa: E402
from .paint import rounded_path  # noqa: E402
from .style import rgba  # noqa: E402

__all__ = ["RAIL_ITEMS", "Rail"]

#: Rail entries in top-to-bottom order.  ``settings`` is a disabled placeholder
#: until the settings page is drawn; it must not become a dead link.
RAIL_ITEMS = ("home", "library", "recent", "settings")


class _RailIcon(Gtk.DrawingArea):
    """21 px stroked line icon, drawn here so the trimmed board theme can't blank it."""

    def __init__(self, kind: str) -> None:
        super().__init__()
        self._kind = kind
        self._active = False
        self.set_size_request(21, 21)
        self.set_draw_func(self._draw)

    def set_active(self, active: bool) -> None:
        if active == self._active:
            return
        self._active = active
        self.queue_draw()

    def _draw(self, _area: Gtk.DrawingArea, cr: object, width: int, height: int) -> None:
        if width <= 0 or height <= 0:
            return
        color = rgba("play", 0.98) if self._active else rgba("dim", 0.95)
        cr.set_line_width(1.6)  # type: ignore[attr-defined]
        cr.set_line_cap(1)  # type: ignore[attr-defined]  # cairo.LINE_CAP_ROUND
        cr.set_line_join(1)  # type: ignore[attr-defined]
        cr.set_source_rgba(*color)  # type: ignore[attr-defined]
        scale = min(width, height) / 24.0
        cr.save()  # type: ignore[attr-defined]
        cr.translate((width - 24.0 * scale) / 2.0, (height - 24.0 * scale) / 2.0)  # type: ignore[attr-defined]
        cr.scale(scale, scale)  # type: ignore[attr-defined]
        if self._kind == "home":
            cr.move_to(4.0, 10.6)  # type: ignore[attr-defined]
            cr.line_to(12.0, 4.0)  # type: ignore[attr-defined]
            cr.line_to(20.0, 10.6)  # type: ignore[attr-defined]
            cr.line_to(20.0, 19.0)  # type: ignore[attr-defined]
            cr.move_to(9.0, 20.0)  # type: ignore[attr-defined]
            cr.line_to(9.0, 14.4)  # type: ignore[attr-defined]
            cr.line_to(15.0, 14.4)  # type: ignore[attr-defined]
            cr.line_to(15.0, 20.0)  # type: ignore[attr-defined]
            cr.stroke()  # type: ignore[attr-defined]
        elif self._kind == "library":
            for x, y in ((3.5, 3.5), (13.5, 3.5), (3.5, 13.5), (13.5, 13.5)):
                rounded_path(cr, x, y, 7.0, 7.0, 1.6)
            cr.stroke()  # type: ignore[attr-defined]
        elif self._kind == "recent":
            cr.arc(12.0, 12.0, 8.2, 0.0, 6.2831853)  # type: ignore[attr-defined]
            cr.stroke()  # type: ignore[attr-defined]
            cr.move_to(12.0, 7.4)  # type: ignore[attr-defined]
            cr.line_to(12.0, 12.0)  # type: ignore[attr-defined]
            cr.line_to(15.1, 13.9)  # type: ignore[attr-defined]
            cr.stroke()  # type: ignore[attr-defined]
        elif self._kind == "settings":
            cr.move_to(4.0, 7.5)  # type: ignore[attr-defined]
            cr.line_to(13.0, 7.5)  # type: ignore[attr-defined]
            cr.move_to(17.0, 7.5)  # type: ignore[attr-defined]
            cr.line_to(20.0, 7.5)  # type: ignore[attr-defined]
            cr.move_to(4.0, 16.5)  # type: ignore[attr-defined]
            cr.line_to(8.0, 16.5)  # type: ignore[attr-defined]
            cr.move_to(12.0, 16.5)  # type: ignore[attr-defined]
            cr.line_to(20.0, 16.5)  # type: ignore[attr-defined]
            cr.stroke()  # type: ignore[attr-defined]
            cr.arc(14.5, 7.5, 2.2, 0.0, 6.2831853)  # type: ignore[attr-defined]
            cr.stroke()  # type: ignore[attr-defined]
            cr.arc(9.5, 16.5, 2.2, 0.0, 6.2831853)  # type: ignore[attr-defined]
            cr.stroke()  # type: ignore[attr-defined]
        cr.restore()  # type: ignore[attr-defined]


class _ActiveBar(Gtk.DrawingArea):
    """Scheme C's 2 x 40 bone cursor at the left edge of the current entry.

    GTK CSS has no ``::before``, so the mockup's inset bar is a child widget;
    it is hidden unless the entry is current and takes no layout space.
    """

    def __init__(self) -> None:
        super().__init__()
        self.set_size_request(2, 40)
        self.set_halign(Gtk.Align.START)
        self.set_valign(Gtk.Align.CENTER)
        self.set_draw_func(self._draw)
        self.set_visible(False)

    def _draw(self, _area: Gtk.DrawingArea, cr: object, width: int, height: int) -> None:
        if width <= 0 or height <= 0:
            return
        cr.set_source_rgba(*rgba("play", 1.0))  # type: ignore[attr-defined]
        rounded_path(cr, 0.0, 0.0, float(width), float(height), 1.0)
        cr.fill()  # type: ignore[attr-defined]


class _RailButton(Gtk.ToggleButton):
    """Rail entry: icon over a one-word label, with the bone current cursor."""

    def __init__(self, kind: str) -> None:
        super().__init__()
        self.add_css_class("rail-item")
        self.kind = kind
        self.set_focus_on_click(False)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self.icon = _RailIcon(kind)
        self.icon.set_halign(Gtk.Align.CENTER)
        self.label = Gtk.Label()
        self.label.add_css_class("rail-label")
        self.label.set_halign(Gtk.Align.CENTER)
        box.append(self.icon)
        box.append(self.label)
        self.bar = _ActiveBar()
        overlay = Gtk.Overlay()
        overlay.set_child(box)
        overlay.add_overlay(self.bar)
        self.set_child(overlay)

    def set_active_visual(self, active: bool) -> None:
        self.icon.set_active(active)
        self.bar.set_visible(active)


class Rail(Gtk.Box):
    """The left navigation column: four entries plus machine status."""

    def __init__(
        self,
        *,
        on_nav: Callable[[str], None],
        translator: Translator,
        hw_ready: bool = False,
    ) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.add_css_class("rail")
        self.set_size_request(88, -1)
        self.set_valign(Gtk.Align.FILL)
        self._on_nav = on_nav
        self._t = translator
        self._hw_ready = hw_ready
        self._updating = False
        self._build()

    # -- construction ------------------------------------------------------

    def _build(self) -> None:
        # ui-v6: no mark plate here any more.  The nav keeps the rail's 14 px
        # padding and its own 24 px margin, so it starts 38 px into the rail --
        # 44 px higher than before (the removed mark box was 42 px plus a 2 px
        # top margin; the design's "42 px" counts the plate only).
        nav = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        nav.set_halign(Gtk.Align.CENTER)
        nav.set_margin_top(24)
        self._buttons: dict[str, _RailButton] = {}
        for kind in RAIL_ITEMS:
            button = _RailButton(kind)
            button.set_halign(Gtk.Align.CENTER)
            button.set_margin_start(0)
            button.set_margin_end(0)
            nav.append(button)
            self._buttons[kind] = button
            button.connect("toggled", self._on_toggled, kind)
        self._buttons["library"].set_group(self._buttons["recent"])
        self._buttons["home"].set_group(self._buttons["recent"])
        self.append(nav)

        spacer = Gtk.Box()
        spacer.set_vexpand(True)
        self.append(spacer)

        foot = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        foot.set_halign(Gtk.Align.CENTER)
        foot.set_margin_bottom(6)
        status = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        status.set_halign(Gtk.Align.CENTER)
        self._status_dot = Gtk.Label(label="●")
        self._status_dot.add_css_class("rail-dot")
        self._status = Gtk.Label()
        self._status.add_css_class("rail-status")
        status.append(self._status_dot)
        status.append(self._status)
        foot.append(status)
        self._clock = Gtk.Label(label="")
        self._clock.add_css_class("rail-clock")
        self._clock.set_halign(Gtk.Align.CENTER)
        foot.append(self._clock)
        self.append(foot)
        self.apply_language(self._t)

    # -- public API --------------------------------------------------------

    def apply_language(self, translator: Translator) -> None:
        self._t = translator
        t = translator
        for kind, button in self._buttons.items():
            button.label.set_text(t(f"nav_{kind}"))
        self._buttons["home"].set_tooltip_text(t("nav_home"))
        self._buttons["library"].set_tooltip_text(t("library_title"))
        self._buttons["recent"].set_tooltip_text(t("recent_title"))
        self._buttons["settings"].set_tooltip_text(t("settings_tooltip"))
        self._status.set_text(t("rail_ready") if self._hw_ready else t("rail_sw_only"))
        if self._hw_ready:
            self._status_dot.remove_css_class("is-off")
        else:
            self._status_dot.add_css_class("is-off")

    def set_clock(self, text: str) -> None:
        self._clock.set_text(text)

    def set_active(self, kind: str) -> None:
        """Mark one entry as current without re-entering the toggled signal."""
        if kind not in self._buttons:
            kind = "home"
        self._updating = True
        try:
            for name, button in self._buttons.items():
                active = name == kind
                if button.get_active() != active:
                    button.set_active(active)
        finally:
            self._updating = False
        for name, button in self._buttons.items():
            button.set_active_visual(name == kind)

    @property
    def active(self) -> str:
        for name, button in self._buttons.items():
            if button.get_active():
                return name
        return ""

    # -- events ------------------------------------------------------------

    def _on_toggled(self, button: _RailButton, kind: str) -> None:
        if self._updating or not button.get_active():
            return
        self._on_nav(kind)
