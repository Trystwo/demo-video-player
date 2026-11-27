"""ui-v6 playback chrome: top bar, telemetry row, chapter timeline and tabs.

These widgets are deliberately dumb: they draw and emit, and ``window.py``
owns every signal, translation and playback-side effect.  Keeping them here
means the player page can be rebuilt without touching the transport wiring
that was validated in v4.

Dimensions follow ``mockups/ui-v6``: 44 px round keys (52 px play), a 20 px hit
band around scheme C's 3 px rail with a 10 x 15 px vertical bar knob, 1 x 10 px
chapter ticks, a single-line telemetry row in the monospace face, and the 40 px
bone underline that marks the active drawer section (GTK CSS has no
``::before``/``::after``, so it is a small child widget).
"""

from __future__ import annotations

import math

import gi

gi.require_version("Gdk", "4.0")
gi.require_version("Gtk", "4.0")
from gi.repository import Gdk, GObject, Gtk, Pango  # noqa: E402

from .paint import rounded_path  # noqa: E402
from .style import rgba  # noqa: E402

__all__ = [
    "CONTROL_HEIGHT",
    "DRAWER_RULE_UNDERLINE",
    "PLAY_BUTTON_SIZE",
    "TIMELINE_HEIGHT",
    "ChapterTimeline",
    "DrawerTabRule",
    "PlayerTopBar",
    "TelemetryBar",
]

#: Every transport key is at least a 44 px touch target.
CONTROL_HEIGHT = 44
#: The mockup's single white circle, the only control bigger than the row.
PLAY_BUTTON_SIZE = 52
#: Hit band around the rail; the rail itself is drawn centred.
TIMELINE_HEIGHT = 20
_RAIL_HEIGHT = 3.0
#: Scheme C's scrubber is a vertical bar, not a round knob.
_KNOB_WIDTH = 10.0
_KNOB_HEIGHT = 15.0
_TICK_WIDTH = 1.0
_TICK_HEIGHT = 10.0
#: Width of the bone underline marking the active section of a drawer.
DRAWER_RULE_UNDERLINE = 40


class DrawerTabRule(Gtk.DrawingArea):
    """Scheme C's drawer-head rule: 1 px hairline + 40 px bone underline.

    The mockup draws this with a bottom border and a ``::after`` bar on the
    active tab; GTK CSS has neither pseudo-elements nor a way to paint a 40 px
    segment of a border, so one 2 px-tall drawing area owns both.
    """

    def __init__(self) -> None:
        super().__init__()
        self.set_size_request(-1, 2)
        self.set_hexpand(True)
        self.set_draw_func(self._draw)

    def _draw(self, _area: Gtk.DrawingArea, cr: object, width: int, height: int) -> None:
        if width <= 0 or height <= 0:
            return
        cr.set_source_rgba(*rgba("line", 1.0))  # type: ignore[attr-defined]
        cr.rectangle(0.0, float(height) - 1.0, float(width), 1.0)  # type: ignore[attr-defined]
        cr.fill()  # type: ignore[attr-defined]
        bar_w = min(float(DRAWER_RULE_UNDERLINE), float(width))
        rounded_path(cr, 0.0, float(height) - 2.0, bar_w, 2.0, 1.0)
        cr.set_source_rgba(*rgba("play", 1.0))  # type: ignore[attr-defined]
        cr.fill()  # type: ignore[attr-defined]


class ChapterTimeline(Gtk.DrawingArea):
    """Seek rail with amber played track, knob and optional chapter ticks.

    It exposes the small part of ``Gtk.Scale`` the transport code already
    uses (``set_value`` / ``get_value`` on a 0–1000 scale, plus a
    ``value-changed`` signal), so seeking keeps one code path while the visual
    is the mockup's rail instead of an Adwaita trough.
    """

    __gsignals__ = {
        "value-changed": (GObject.SignalFlags.RUN_LAST, None, ()),
    }

    def __init__(self) -> None:
        super().__init__()
        self.add_css_class("chapter-timeline")
        self.set_hexpand(True)
        self.set_size_request(-1, TIMELINE_HEIGHT)
        self.set_draw_func(self._draw)
        self.set_focusable(True)

        self._fraction = 0.0
        self._chapters: tuple = ()
        self._dragging = False
        self._drag_start_x = 0.0

        click = Gtk.GestureClick()
        click.set_button(Gdk.BUTTON_PRIMARY)
        click.connect("pressed", self._on_pressed)
        self.add_controller(click)

        drag = Gtk.GestureDrag()
        drag.set_button(Gdk.BUTTON_PRIMARY)
        drag.connect("drag-begin", self._on_drag_begin)
        drag.connect("drag-update", self._on_drag_update)
        drag.connect("drag-end", self._on_drag_end)
        self.add_controller(drag)

    # -- value contract (Gtk.Scale subset) --------------------------------

    def get_value(self) -> float:
        return self._fraction * 1000.0

    def set_value(self, value: float) -> None:
        try:
            fraction = float(value) / 1000.0
        except (TypeError, ValueError):
            return
        self.set_fraction(fraction)

    def set_fraction(self, fraction: float) -> None:
        """Programmatic update used by the 200 ms tick; never emits."""
        value = max(0.0, min(1.0, float(fraction)))
        if abs(value - self._fraction) < 1e-6:
            return
        self._fraction = value
        self.queue_draw()

    def get_fraction(self) -> float:
        return self._fraction

    # -- chapters ---------------------------------------------------------

    @property
    def chapters(self) -> tuple:
        return self._chapters

    def set_chapters(self, marks: tuple) -> None:
        """Set :class:`~video_player.ui.chapters.ChapterMark` positions."""
        self._chapters = tuple(marks)
        self.queue_draw()

    # -- interaction ------------------------------------------------------

    def _fraction_at(self, x: float) -> float:
        width = self.get_width()
        if width <= 0:
            return 0.0
        return max(0.0, min(1.0, float(x) / float(width)))

    def _emit_seek(self, fraction: float) -> None:
        value = max(0.0, min(1.0, float(fraction)))
        self.set_fraction(value)
        self.emit("value-changed")

    def seek_fraction(self, fraction: float) -> None:
        """Public seek action (also used by the interaction probe)."""
        self._emit_seek(fraction)

    def _on_pressed(
        self,
        _gesture: Gtk.GestureClick,
        _n_press: int,
        x: float,
        _y: float,
    ) -> None:
        self.grab_focus()
        self._emit_seek(self._fraction_at(x))

    def _on_drag_begin(
        self, _gesture: Gtk.GestureDrag, start_x: float, _start_y: float
    ) -> None:
        self._dragging = True
        self._drag_start_x = float(start_x)
        self.grab_focus()
        self._emit_seek(self._fraction_at(start_x))

    def _on_drag_update(
        self, _gesture: Gtk.GestureDrag, _offset_x: float, _offset_y: float
    ) -> None:
        if not self._dragging:
            return
        # ``Gtk.GestureDrag`` reports the offset from the drag start; keep the
        # start so the update does not depend on a synthetic event position.
        self._emit_seek(self._fraction_at(self._drag_start_x + _offset_x))

    def _on_drag_end(self, _gesture: Gtk.GestureDrag, _x: float, _y: float) -> None:
        self._dragging = False

    # -- painting ---------------------------------------------------------

    @staticmethod
    def _rounded_rect(
        cr: object, x: float, y: float, width: float, height: float, radius: float
    ) -> None:
        if width <= 0 or height <= 0:
            return
        radius = max(0.0, min(radius, height / 2.0, width / 2.0))
        cr.new_sub_path()
        cr.arc(x + width - radius, y + radius, radius, -math.pi / 2, 0)
        cr.arc(x + width - radius, y + height - radius, radius, 0, math.pi / 2)
        cr.arc(x + radius, y + height - radius, radius, math.pi / 2, math.pi)
        cr.arc(x + radius, y + radius, radius, math.pi, 3 * math.pi / 2)
        cr.close_path()

    def _draw(self, _area: Gtk.DrawingArea, cr: object, width: int, height: int) -> None:
        if width <= 0 or height <= 0:
            return
        center = height / 2.0
        rail_height = _RAIL_HEIGHT
        top = center - rail_height / 2.0

        # Idle rail.  The chapter ticks are drawn above it, as in the mockup.
        cr.set_source_rgba(*rgba("fg", 0.16))
        self._rounded_rect(cr, 0.0, top, float(width), rail_height, rail_height / 2.0)
        cr.fill()

        if self._chapters:
            cr.set_source_rgba(*rgba("fg", 0.28))
            for mark in self._chapters:
                x = mark.fraction * width
                if x < 0.5 or x > width - 0.5:
                    continue
                cr.rectangle(
                    x - _TICK_WIDTH / 2.0,
                    center - _TICK_HEIGHT / 2.0,
                    _TICK_WIDTH,
                    _TICK_HEIGHT,
                )
            cr.fill()

        played = max(0.0, min(1.0, self._fraction)) * width
        if played > 0:
            cr.set_source_rgba(*rgba("accent", 0.96))
            self._rounded_rect(cr, 0.0, top, played, rail_height, rail_height / 2.0)
            cr.fill()

        # Scheme C scrubber: a 10 x 15 bone bar centred on the played edge.
        knob_x = max(_KNOB_WIDTH / 2.0, min(float(width) - _KNOB_WIDTH / 2.0, played))
        rounded_path(
            cr,
            knob_x - _KNOB_WIDTH / 2.0,
            center - _KNOB_HEIGHT / 2.0,
            _KNOB_WIDTH,
            _KNOB_HEIGHT,
            2.0,
        )
        cr.set_source_rgba(*rgba("play", 1.0))
        cr.fill()


class PlayerTopBar(Gtk.Box):
    """Picture-top chrome: back, title/subtitle, episodes and more.

    Windowed playback shows the GTK header bar (real window buttons), so the
    clip title lives there and this bar runs in *compact* mode: the titles box
    stays as the spacer that pushes the chips right, but its two labels are
    hidden.  Fullscreen hides the header bar and restores the labels.
    ``episodes_button`` is a toggle mirroring the right-hand episodes sidebar.
    """

    def __init__(
        self,
        *,
        back_tooltip: str = "",
        episodes_label: str = "",
        more_tooltip: str = "",
    ) -> None:
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL, spacing=14)
        self.add_css_class("play-top")
        self.set_hexpand(True)
        self.set_valign(Gtk.Align.START)
        self.set_margin_top(20)
        self.set_margin_start(22)
        self.set_margin_end(22)

        self.back_button = Gtk.Button()
        self.back_button.add_css_class("play-round")
        self.back_button.set_valign(Gtk.Align.CENTER)
        self.back_button.set_tooltip_text(back_tooltip)
        back_glyph = Gtk.Label(label="\u2039")
        back_glyph.add_css_class("play-back-glyph")
        self.back_button.set_child(back_glyph)
        self.append(self.back_button)

        titles = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
        titles.set_valign(Gtk.Align.CENTER)
        titles.set_hexpand(True)
        #: Kept visible in compact mode: it is the spacer that keeps the chips
        #: on the right when the title labels are hidden.
        self.titles = titles
        self._compact = False
        self.title_label = Gtk.Label()
        self.title_label.add_css_class("play-title")
        self.title_label.set_xalign(0.0)
        self.title_label.set_ellipsize(Pango.EllipsizeMode.END)
        self.title_label.set_max_width_chars(52)
        self.subtitle_label = Gtk.Label()
        self.subtitle_label.add_css_class("play-subtitle")
        self.subtitle_label.set_xalign(0.0)
        self.subtitle_label.set_ellipsize(Pango.EllipsizeMode.END)
        self.subtitle_label.set_max_width_chars(64)
        titles.append(self.title_label)
        titles.append(self.subtitle_label)
        self.append(titles)

        self.episodes_button = Gtk.ToggleButton()
        self.episodes_button.add_css_class("play-top-chip")
        self.episodes_button.set_valign(Gtk.Align.CENTER)
        self.episodes_button.set_focus_on_click(False)
        chip = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=7)
        glyph = Gtk.Label(label="\u2630")
        glyph.add_css_class("play-chip-glyph")
        self.episodes_label = Gtk.Label(label=episodes_label)
        self.episodes_label.add_css_class("play-chip-label")
        chip.append(glyph)
        chip.append(self.episodes_label)
        self.episodes_button.set_child(chip)
        self.append(self.episodes_button)

        self.more_button = Gtk.MenuButton()
        self.more_button.add_css_class("play-round")
        self.more_button.set_valign(Gtk.Align.CENTER)
        self.more_button.set_always_show_arrow(False)
        self.more_button.set_tooltip_text(more_tooltip)
        more_glyph = Gtk.Label(label="\u22ee")
        more_glyph.add_css_class("play-more-glyph")
        self.more_button.set_child(more_glyph)
        self.append(self.more_button)

    def set_title(self, text: str) -> None:
        self.title_label.set_text(text)

    def set_subtitle(self, text: str) -> None:
        self.subtitle_label.set_text(text)
        self.subtitle_label.set_visible(bool(text) and not self._compact)

    def set_episodes_label(self, text: str) -> None:
        self.episodes_label.set_text(text)

    def set_compact(self, compact: bool) -> None:
        """Windowed mode: hide the labels but keep the titles box as spacer.

        ``_refresh_subtitle`` runs on every telemetry refresh and would make the
        subtitle visible again, so compactness is a state here rather than a
        one-off ``set_visible(False)`` in the window.
        """
        compact = bool(compact)
        if compact == self._compact:
            return
        self._compact = compact
        self.title_label.set_visible(not compact)
        self.subtitle_label.set_visible(
            not compact and bool(self.subtitle_label.get_text())
        )

    def is_compact(self) -> bool:
        return self._compact

    def set_episodes_active(self, active: bool) -> None:
        active = bool(active)
        if self.episodes_button.get_active() != active:
            self.episodes_button.set_active(active)


class TelemetryBar(Gtk.Box):
    """One-line live telemetry, mirroring the mockup's ``硬解 | H.264 | …`` row.

    Values are supplied by ``PlayerWindow``; this widget holds no backend
    reference.  The CPU figure is process CPU (all threads), measured once a
    second in the window.
    """

    def __init__(self) -> None:
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        self.add_css_class("telemetry-row")
        self.set_valign(Gtk.Align.CENTER)

        self.decode_dot = Gtk.Label(label="\u25cf")
        self.decode_dot.add_css_class("telemetry-dot")
        self.decode_value = self._label("telemetry-value")
        self.codec_value = self._label("telemetry-value")
        self.resolution_value = self._label("telemetry-value")
        self.fps_value = self._label("telemetry-value")
        self.face_value = self._label("telemetry-live")
        self.beauty_value = self._label("telemetry-live")
        self.beauty_value.add_css_class("telemetry-accent")
        self.cpu_value = self._label("telemetry-cpu")
        self.cpu_value.set_xalign(1.0)

        self.append(self.decode_dot)
        self.append(self.decode_value)
        for widget in (
            self.codec_value,
            self.resolution_value,
            self.fps_value,
            self.face_value,
            self.beauty_value,
        ):
            self.append(self._separator())
            self.append(widget)
        spacer = Gtk.Box()
        spacer.set_hexpand(True)
        self.append(spacer)
        self.append(self._separator())
        self.append(self.cpu_value)

    @staticmethod
    def _label(css_class: str) -> Gtk.Label:
        label = Gtk.Label(label="\u2014")
        label.add_css_class(css_class)
        label.set_xalign(0.0)
        label.set_selectable(False)
        return label

    @staticmethod
    def _separator() -> Gtk.Separator:
        line = Gtk.Separator(orientation=Gtk.Orientation.VERTICAL)
        line.add_css_class("telemetry-sep")
        return line

    def set_values(
        self,
        *,
        decode: str,
        codec: str,
        resolution: str,
        fps: str,
        face: str,
        beauty: str,
        cpu: str,
        hardware: bool,
    ) -> None:
        self.decode_value.set_text(decode)
        self.codec_value.set_text(codec)
        self.resolution_value.set_text(resolution)
        self.fps_value.set_text(fps)
        self.face_value.set_text(face)
        self.beauty_value.set_text(beauty)
        self.cpu_value.set_text(cpu)
        if hardware:
            self.decode_dot.remove_css_class("is-off")
        else:
            self.decode_dot.add_css_class("is-off")
