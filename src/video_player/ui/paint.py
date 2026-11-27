"""Cairo-painted surfaces shared by the browse pages.

GTK4 does not clip a child's content to a CSS border radius, and every poster in
ui-v6 rounds its corners and layers a scrim, a duration plate and a progress
hairline over a cropped video frame.  Drawing them here keeps three things
consistent:

* the fallback (a file that has not been probed yet) looks like a deliberate
  placeholder instead of a broken image,
* the home hero and the detail backdrop use the same poster, crop and palette
  as the cards, and
* the board's trimmed icon theme never blanks a glyph: every mark is a Cairo
  path, not a symbolic icon.

Only draws; no product state lives here.
"""

from __future__ import annotations

import cairo
import gi

gi.require_version("Gdk", "4.0")
gi.require_version("GdkPixbuf", "2.0")
gi.require_version("Gtk", "4.0")
gi.require_version("Pango", "1.0")
gi.require_version("PangoCairo", "1.0")
from gi.repository import (  # noqa: E402
    Gdk,
    GdkPixbuf,
    Gtk,
    Pango,
    PangoCairo,
)

from .style import rgba  # noqa: E402

__all__ = [
    "BACKDROP_RADIUS",
    "BackdropArt",
    "GhostFrame",
    "Poster",
    "draw_pill",
    "paint_cover",
    "pango_layout",
    "rounded_path",
]


def rounded_path(
    cr: object, x: float, y: float, width: float, height: float, radius: float
) -> None:
    """Append a rounded rectangle to the current path."""
    radius = max(0.0, min(radius, width / 2.0, height / 2.0))
    cr.new_sub_path()  # type: ignore[attr-defined]
    cr.arc(x + width - radius, y + radius, radius, -1.5707963, 0.0)  # type: ignore[attr-defined]
    cr.arc(x + width - radius, y + height - radius, radius, 0.0, 1.5707963)  # type: ignore[attr-defined]
    cr.arc(x + radius, y + height - radius, radius, 1.5707963, 3.1415927)  # type: ignore[attr-defined]
    cr.arc(x + radius, y + radius, radius, 3.1415927, 4.7123890)  # type: ignore[attr-defined]
    cr.close_path()  # type: ignore[attr-defined]


def pango_layout(cr: object, text: str, font: str) -> object:
    """Build a Pango layout for *text* with a ``"Family size"`` description."""
    layout = PangoCairo.create_layout(cr)  # type: ignore[arg-type]
    layout.set_text(text, -1)
    layout.set_font_description(Pango.FontDescription(font))
    PangoCairo.update_layout(cr, layout)  # type: ignore[arg-type]
    return layout


def draw_pill(
    cr: object,
    text: str,
    *,
    right: float,
    bottom: float,
    font: str = "Sans 8.5",
    text_color: tuple[float, float, float, float] | None = None,
    box_color: tuple[float, float, float, float] | None = None,
    radius: float = 5.0,
    pad_x: float = 6.0,
    pad_y: float = 2.0,
    border_color: tuple[float, float, float, float] | None = None,
) -> None:
    """Draw a small rounded label whose bottom-right corner is (right, bottom).

    Scheme C's duration plate is a flat, almost transparent dark chip with a
    3 px radius and monospace digits (``mockups/ui-v6/skin-c.css``); the same
    primitive also draws the "finished" badge and the missing-file note.
    """
    layout = pango_layout(cr, text, font)
    text_w, text_h = layout.get_pixel_size()
    box_w = float(text_w) + pad_x * 2
    box_h = float(text_h) + pad_y * 2
    box_x = right - box_w
    box_y = bottom - box_h
    rounded_path(cr, box_x, box_y, box_w, box_h, radius)
    cr.set_source_rgba(*(box_color or rgba("scrim", 0.42)))  # type: ignore[attr-defined]
    cr.fill_preserve()  # type: ignore[attr-defined]
    if border_color is not None:
        cr.set_line_width(1.0)  # type: ignore[attr-defined]
        cr.set_source_rgba(*border_color)  # type: ignore[attr-defined]
        cr.stroke()  # type: ignore[attr-defined]
    else:
        cr.new_path()  # type: ignore[attr-defined]
    cr.set_source_rgba(*(text_color or rgba("play", 1.0)))  # type: ignore[attr-defined]
    cr.move_to(box_x + pad_x, box_y + pad_y)  # type: ignore[attr-defined]
    PangoCairo.show_layout(cr, layout)  # type: ignore[arg-type]


def paint_cover(
    cr: object,
    width: float,
    height: float,
    pixbuf: GdkPixbuf.Pixbuf,
    *,
    focus: float = 0.5,
) -> None:
    """Draw *pixbuf* like ``object-fit: cover`` with a vertical focal point.

    ``focus`` is the fraction of the source kept at the top (0.0 = top edge,
    0.5 = centred), the equivalent of the mockups' ``object-position: 50% 12%``.
    """
    pix_w = max(1, pixbuf.get_width())
    pix_h = max(1, pixbuf.get_height())
    scale = max(width / pix_w, height / pix_h)
    draw_w, draw_h = pix_w * scale, pix_h * scale
    offset_x = (width - draw_w) / 2.0
    overflow = draw_h - height
    offset_y = -overflow * max(0.0, min(1.0, focus))
    cr.save()  # type: ignore[attr-defined]
    cr.translate(offset_x, offset_y)  # type: ignore[attr-defined]
    cr.scale(scale, scale)  # type: ignore[attr-defined]
    Gdk.cairo_set_source_pixbuf(cr, pixbuf, 0.0, 0.0)
    cr.paint()  # type: ignore[attr-defined]
    cr.restore()  # type: ignore[attr-defined]


def _linear(
    x0: float, y0: float, x1: float, y1: float, stops: tuple[tuple[float, tuple[float, float, float, float]], ...]
) -> cairo.LinearGradient:
    gradient = cairo.LinearGradient(x0, y0, x1, y1)
    for offset, color in stops:
        gradient.add_color_stop_rgba(offset, *color)
    return gradient


def _fill_rect(cr: object, x: float, y: float, width: float, height: float, pattern: object) -> None:
    cr.save()  # type: ignore[attr-defined]
    cr.rectangle(x, y, width, height)  # type: ignore[attr-defined]
    cr.set_source(pattern)  # type: ignore[attr-defined]
    cr.fill()  # type: ignore[attr-defined]
    cr.restore()  # type: ignore[attr-defined]


class Poster(Gtk.DrawingArea):
    """Rounded poster with a duration chip, a badge and a progress hairline."""

    #: Card thumbnails are 16:9 slices of the frame; 132 px is the height the
    #: library grid settled on so three rows fit a 1280x768 window.
    HEIGHT = 132
    #: Scheme C posters are cut squarer than ui-v5's 10 px.
    RADIUS = 8.0

    def __init__(
        self,
        *,
        height: int = HEIGHT,
        radius: float = RADIUS,
        show_duration: bool = True,
    ) -> None:
        super().__init__()
        self.HEIGHT = int(height)
        self.RADIUS = float(radius)
        self._show_duration = show_duration
        self.set_size_request(-1, self.HEIGHT)
        self.set_hexpand(True)
        self.set_draw_func(self._draw)
        self._pixbuf: GdkPixbuf.Pixbuf | None = None
        self._duration = ""
        self._badge = ""
        self._badge_accent = False
        self._progress = 0.0
        self._missing_text = ""
        self._missing = False
        self._current = False

    def set_content(
        self,
        *,
        pixbuf: GdkPixbuf.Pixbuf | None,
        duration: str = "",
        badge: str = "",
        badge_accent: bool = False,
        progress: float = 0.0,
        missing_text: str = "",
        missing: bool = False,
        current: bool = False,
    ) -> None:
        self._pixbuf = pixbuf
        self._duration = duration
        self._badge = badge
        self._badge_accent = badge_accent
        self._progress = max(0.0, min(1.0, float(progress)))
        self._missing_text = missing_text
        self._missing = missing
        self._current = current
        self.queue_draw()

    def set_poster(self, pixbuf: GdkPixbuf.Pixbuf | None) -> None:
        """Swap only the frame; the pills keep their state (backdrop upgrade)."""
        self._pixbuf = pixbuf
        self.queue_draw()

    def _draw(self, _area: Gtk.DrawingArea, cr: object, width: int, height: int) -> None:
        if width <= 0 or height <= 0:
            return
        w, h = float(width), float(height)
        cr.set_line_cap(1)  # type: ignore[attr-defined]
        rounded_path(cr, 0.5, 0.5, w - 1.0, h - 1.0, self.RADIUS)
        cr.save()  # type: ignore[attr-defined]
        cr.clip()  # type: ignore[attr-defined]
        cr.set_source_rgba(*rgba("video", 1.0))  # type: ignore[attr-defined]
        cr.paint()  # type: ignore[attr-defined]
        if self._pixbuf is not None:
            paint_cover(cr, w, h, self._pixbuf)
        else:
            self._paint_fallback(cr, w, h)
        self._paint_scrim(cr, w, h)
        if self._progress > 0.0:
            self._paint_progress(cr, w, h)
        cr.restore()  # type: ignore[attr-defined]
        rounded_path(cr, 0.5, 0.5, w - 1.0, h - 1.0, self.RADIUS)
        cr.set_line_width(1.0)  # type: ignore[attr-defined]
        if self._current:
            cr.set_source_rgba(*rgba("play", 0.95))  # type: ignore[attr-defined]
        else:
            cr.set_source_rgba(*rgba("thumb_line", 1.0))  # type: ignore[attr-defined]
        cr.stroke()  # type: ignore[attr-defined]
        if self._duration and self._show_duration and not self._missing:
            draw_pill(
                cr,
                self._duration,
                right=w - 8.0,
                bottom=h - 8.0,
                font="DejaVu Sans Mono 8",
                text_color=rgba("play", 0.98),
                box_color=rgba("scrim", 0.42),
                radius=3.0,
                pad_x=4.0,
                pad_y=2.5,
            )
        if self._badge:
            self._paint_badge(cr)
        if self._missing:
            draw_pill(cr, self._missing_text or "!", right=w - 8.0, bottom=h - 8.0)

    def _paint_badge(self, cr: object) -> None:
        layout = pango_layout(cr, self._badge, "Sans 7.5")
        text_w, text_h = layout.get_pixel_size()
        pad_x, pad_y = 5.0, 2.5
        box_w = float(text_w) + pad_x * 2
        box_h = float(text_h) + pad_y * 2
        rounded_path(cr, 8.0, 8.0, box_w, box_h, 4.0)
        cr.set_source_rgba(*rgba("scrim", 0.55))  # type: ignore[attr-defined]
        cr.fill_preserve()  # type: ignore[attr-defined]
        cr.set_line_width(1.0)  # type: ignore[attr-defined]
        cr.set_source_rgba(*rgba("play", 0.16))  # type: ignore[attr-defined]
        cr.stroke()  # type: ignore[attr-defined]
        colour = rgba("accent_hi", 0.98) if self._badge_accent else rgba("fg", 0.96)
        cr.set_source_rgba(*colour)  # type: ignore[attr-defined]
        cr.move_to(8.0 + pad_x, 8.0 + pad_y)  # type: ignore[attr-defined]
        PangoCairo.show_layout(cr, layout)  # type: ignore[arg-type]

    def _paint_fallback(self, cr: object, w: float, h: float) -> None:
        """No poster yet: a quiet gradient plate and a play triangle."""
        cr.set_source_rgba(*rgba("panel_2", 1.0))  # type: ignore[attr-defined]
        cr.rectangle(0.0, 0.0, w, h)  # type: ignore[attr-defined]
        cr.fill()  # type: ignore[attr-defined]
        cr.set_source_rgba(*rgba("panel_3", 0.85))  # type: ignore[attr-defined]
        cr.rectangle(0.0, h * 0.55, w, h * 0.45)  # type: ignore[attr-defined]
        cr.fill()  # type: ignore[attr-defined]
        self._paint_play_triangle(cr, w / 2.0, h / 2.0, min(w, h) * 0.11)

    @staticmethod
    def _paint_play_triangle(cr: object, cx: float, cy: float, size: float) -> None:
        cr.move_to(cx - size * 0.55, cy - size)  # type: ignore[attr-defined]
        cr.line_to(cx + size, cy)  # type: ignore[attr-defined]
        cr.line_to(cx - size * 0.55, cy + size)  # type: ignore[attr-defined]
        cr.close_path()  # type: ignore[attr-defined]
        cr.set_source_rgba(*rgba("dim", 0.70))  # type: ignore[attr-defined]
        cr.fill()  # type: ignore[attr-defined]

    @staticmethod
    def _paint_scrim(cr: object, w: float, h: float) -> None:
        """Scheme C: one 50 px gradient, no hard bands.

        ``linear-gradient(0deg, rgba(4,6,7,.56), rgba(4,6,7,.14) 44%,
        rgba(4,6,7,0))`` over the bottom 50 px of the frame.
        """
        top = max(0.0, h - 50.0)
        gradient = _linear(
            0.0,
            top,
            0.0,
            h,
            (
                (0.0, rgba("scrim", 0.0)),
                (0.56, rgba("scrim", 0.14)),
                (1.0, rgba("scrim", 0.56)),
            ),
        )
        _fill_rect(cr, 0.0, top, w, h - top, gradient)

    def _paint_progress(self, cr: object, w: float, h: float) -> None:
        """Scheme C's 2 px amber data line, no idle trough."""
        bar_h = 2.0
        cr.set_source_rgba(*rgba("accent", 0.95))  # type: ignore[attr-defined]
        cr.rectangle(0.0, h - bar_h, w * self._progress, bar_h)  # type: ignore[attr-defined]
        cr.fill()  # type: ignore[attr-defined]


class GhostFrame(Gtk.DrawingArea):
    """Empty-state viewfinder: neutral rounded plate with four corner marks."""

    WIDTH = 232
    HEIGHT = 130

    def __init__(self) -> None:
        super().__init__()
        self.set_size_request(self.WIDTH, self.HEIGHT)
        self.set_halign(Gtk.Align.CENTER)
        self.set_draw_func(self._draw)

    def _draw(self, _area: Gtk.DrawingArea, cr: object, width: int, height: int) -> None:
        if width <= 0 or height <= 0:
            return
        w, h = float(width), float(height)
        rounded_path(cr, 0.5, 0.5, w - 1.0, h - 1.0, 8.0)
        cr.set_source_rgba(*rgba("panel", 0.85))  # type: ignore[attr-defined]
        cr.fill_preserve()  # type: ignore[attr-defined]
        cr.set_line_width(1.0)  # type: ignore[attr-defined]
        cr.set_source_rgba(*rgba("line_2", 0.9))  # type: ignore[attr-defined]
        cr.stroke()  # type: ignore[attr-defined]
        arm = 17.0
        cr.set_line_width(2.0)  # type: ignore[attr-defined]
        cr.set_source_rgba(*rgba("line_3", 0.95))  # type: ignore[attr-defined]
        for x1, y1, x2, y2 in (
            (0.0, 0.0, arm, 0.0),
            (0.0, 0.0, 0.0, arm),
            (w, 0.0, w - arm, 0.0),
            (w, 0.0, w, arm),
            (0.0, h, arm, h),
            (0.0, h, 0.0, h - arm),
            (w, h, w - arm, h),
            (w, h, w, h - arm),
        ):
            cr.move_to(x1, y1)  # type: ignore[attr-defined]
            cr.line_to(x2, y2)  # type: ignore[attr-defined]
        cr.stroke()  # type: ignore[attr-defined]
        cx, cy, size = w / 2.0, h / 2.0, 19.0
        cr.move_to(cx - size * 0.55, cy - size)  # type: ignore[attr-defined]
        cr.line_to(cx + size, cy)  # type: ignore[attr-defined]
        cr.line_to(cx - size * 0.55, cy + size)  # type: ignore[attr-defined]
        cr.close_path()  # type: ignore[attr-defined]
        cr.set_source_rgba(*rgba("dim", 0.85))  # type: ignore[attr-defined]
        cr.fill()  # type: ignore[attr-defined]


#: Home hero and detail backdrop share scheme C's 10 px corner radius.
BACKDROP_RADIUS = 10.0


class BackdropArt(Gtk.DrawingArea):
    """Full-width poster plate behind the home hero / detail text block.

    The mockups layer a left, a bottom and a top scrim over the frame so the
    text stays readable without flattening the whole picture.  ``kind`` only
    changes the scrim recipe and the crop focus; everything else is shared.
    """

    def __init__(self, kind: str, height: int) -> None:
        super().__init__()
        if kind not in ("hero", "detail"):
            raise ValueError(f"unknown backdrop kind: {kind!r}")
        self.kind = kind
        self.set_size_request(-1, int(height))
        self.set_hexpand(True)
        self.set_draw_func(self._draw)
        self._pixbuf: GdkPixbuf.Pixbuf | None = None

    def set_pixbuf(self, pixbuf: GdkPixbuf.Pixbuf | None) -> None:
        self._pixbuf = pixbuf
        self.queue_draw()

    def _draw(self, _area: Gtk.DrawingArea, cr: object, width: int, height: int) -> None:
        if width <= 0 or height <= 0:
            return
        w, h = float(width), float(height)
        cr.set_line_cap(1)  # type: ignore[attr-defined]
        rounded_path(cr, 0.5, 0.5, w - 1.0, h - 1.0, BACKDROP_RADIUS)
        cr.save()  # type: ignore[attr-defined]
        cr.clip()  # type: ignore[attr-defined]
        cr.set_source_rgba(*rgba("bg_deep", 1.0))  # type: ignore[attr-defined]
        cr.paint()  # type: ignore[attr-defined]
        if self._pixbuf is not None:
            paint_cover(cr, w, h, self._pixbuf, focus=0.12 if self.kind == "hero" else 0.16)
        else:
            self._paint_empty_plate(cr, w, h)
        if self.kind == "hero":
            self._paint_hero_scrims(cr, w, h)
        else:
            self._paint_detail_scrims(cr, w, h)
        cr.restore()  # type: ignore[attr-defined]
        rounded_path(cr, 0.5, 0.5, w - 1.0, h - 1.0, BACKDROP_RADIUS)
        cr.set_line_width(1.0)  # type: ignore[attr-defined]
        cr.set_source_rgba(*rgba("line", 0.95))  # type: ignore[attr-defined]
        cr.stroke()  # type: ignore[attr-defined]

    @staticmethod
    def _paint_empty_plate(cr: object, w: float, h: float) -> None:
        _fill_rect(
            cr,
            0.0,
            0.0,
            w,
            h,
            _linear(
                0.0,
                0.0,
                w,
                h,
                (
                    (0.0, rgba("panel", 1.0)),
                    (1.0, rgba("bg_deep", 1.0)),
                ),
            ),
        )
        # A faint viewfinder suggests "picture goes here" without drawing a
        # fake poster the library does not have.
        cx, cy = w / 2.0, h / 2.0
        size = min(w, h) * 0.10
        cr.set_line_width(1.5)  # type: ignore[attr-defined]
        cr.set_source_rgba(*rgba("line_3", 0.55))  # type: ignore[attr-defined]
        rounded_path(cr, cx - size * 2.2, cy - size * 0.9, size * 4.4, size * 1.8, 6.0)
        cr.stroke()  # type: ignore[attr-defined]

    @staticmethod
    def _paint_hero_scrims(cr: object, w: float, h: float) -> None:
        # Scheme C hero veil: a long left-to-right falloff plus a soft bottom
        # anchor, both from the page's near-black plate colour.
        _fill_rect(
            cr,
            0.0,
            0.0,
            w,
            h,
            _linear(
                0.0,
                0.0,
                w,
                0.0,
                (
                    (0.0, rgba("bg_deep", 0.97)),
                    (0.36, rgba("bg_deep", 0.92)),
                    (0.56, rgba("bg_deep", 0.62)),
                    (0.74, rgba("bg_deep", 0.18)),
                    (0.88, rgba("bg_deep", 0.0)),
                    (1.0, rgba("bg_deep", 0.0)),
                ),
            ),
        )
        _fill_rect(
            cr,
            0.0,
            0.0,
            w,
            h,
            _linear(
                0.0,
                h,
                0.0,
                0.0,
                (
                    (0.0, rgba("bg_deep", 0.68)),
                    (0.38, rgba("bg_deep", 0.10)),
                    (0.64, rgba("bg_deep", 0.0)),
                ),
            ),
        )
        # Device line sits top-right: a short top scrim keeps it legible.
        _fill_rect(
            cr,
            0.0,
            0.0,
            w,
            h * 0.26,
            _linear(
                0.0,
                0.0,
                0.0,
                h * 0.26,
                (
                    (0.0, rgba("bg_deep", 0.36)),
                    (1.0, rgba("bg_deep", 0.0)),
                ),
            ),
        )

    @staticmethod
    def _paint_detail_scrims(cr: object, w: float, h: float) -> None:
        # Bottom scrim melts into the page background so the text block reads as
        # part of the page, not as a caption box (mockups/ui-v5/detail.html).
        _fill_rect(
            cr,
            0.0,
            0.0,
            w,
            h,
            _linear(
                0.0,
                h,
                0.0,
                0.0,
                (
                    (0.0, rgba("bg", 1.0)),
                    (0.22, rgba("bg", 0.94)),
                    (0.52, rgba("bg", 0.55)),
                    (0.78, rgba("bg", 0.10)),
                    (1.0, rgba("bg", 0.22)),
                ),
            ),
        )
        _fill_rect(
            cr,
            0.0,
            0.0,
            w,
            h,
            _linear(
                0.0,
                0.0,
                w,
                0.0,
                (
                    (0.0, rgba("bg", 0.88)),
                    (0.34, rgba("bg", 0.52)),
                    (0.62, rgba("bg", 0.06)),
                    (1.0, rgba("bg", 0.0)),
                ),
            ),
        )
        top = min(h, 120.0)
        _fill_rect(
            cr,
            0.0,
            0.0,
            w,
            top,
            _linear(
                0.0,
                0.0,
                0.0,
                top,
                (
                    (0.0, rgba("bg_deep", 0.62)),
                    (1.0, rgba("bg_deep", 0.0)),
                ),
            ),
        )
