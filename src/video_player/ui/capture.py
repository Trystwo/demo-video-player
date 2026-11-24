"""Snapshot GTK widgets into RGBA arrays and compose the recording canvas.

The recorder (:mod:`video_player.recording`) needs a full-window RGBA canvas at
10 fps while the video keeps running at 30 fps.  Capturing the whole window
every frame is far too slow (~36 ms on the RK3576), but the window decomposes
into a few small pieces:

* a background snapshot (titlebar, panel surfaces, margins): ~36 ms, captured
  once and refreshed only when the layout changes,
* dynamic chrome (sidebar, info line, control bar, hint): 2-10 ms each,
* the video surface, which must stay fully transparent so the mixer's video
  pad shows through (the recorder places that pad exactly over it).

The background snapshot contains the *old* text of the dynamic labels, so every
layer is painted over a fresh patch of backdrop colour sampled from the
background before its pixels are blended in; that removes the stale glyphs.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Sequence

import gi

gi.require_version("Gdk", "4.0")
gi.require_version("Gtk", "4.0")
from gi.repository import Gdk, Gtk  # noqa: E402

import numpy as np  # noqa: E402

from ..recording import Rect, canvas_from_rgba, draw_face_boxes, even_size  # noqa: E402


def capture_widget(widget: Gtk.Widget) -> "np.ndarray | None":
    """Rasterise a mapped widget into a contiguous RGBA ``uint8`` array.

    ``Gtk.WidgetPaintable`` renders any mapped widget into a render node; the
    window's own GSK renderer turns it into a texture.  ``texture.download()``
    is not usable through PyGObject (it silently returns zeroed buffers), so
    the pixels come from the deprecated ``Gdk.pixbuf_get_from_texture()``.
    """
    if widget is None or not widget.get_mapped():
        return None
    width = widget.get_width()
    height = widget.get_height()
    if width <= 0 or height <= 0:
        return None
    native = widget.get_native()
    renderer = native.get_renderer() if native is not None else None
    if renderer is None:
        return None
    paintable = Gtk.WidgetPaintable.new(widget)
    snapshot = Gtk.Snapshot()
    paintable.snapshot(snapshot, width, height)
    node = snapshot.to_node()
    if node is None:
        return None
    texture = renderer.render_texture(node, None)
    if texture is None:
        return None
    pixbuf = Gdk.pixbuf_get_from_texture(texture)
    if pixbuf is None:
        return None
    pix_width = pixbuf.get_width()
    pix_height = pixbuf.get_height()
    channels = min(pixbuf.get_n_channels(), 4)
    stride = pixbuf.get_rowstride()
    if pix_width <= 0 or pix_height <= 0 or channels < 3:
        return None
    data = pixbuf.get_pixels()
    length = stride * pix_height
    if len(data) < length:
        return None
    raw = np.frombuffer(data, dtype=np.uint8, count=length).reshape(pix_height, stride)
    pixels = raw[:, : pix_width * channels].reshape(pix_height, pix_width, channels)
    if channels == 4:
        return np.ascontiguousarray(pixels)
    image = np.empty((pix_height, pix_width, 4), dtype=np.uint8)
    image[:, :, :3] = pixels
    image[:, :, 3] = 255
    return image


def widget_bounds(root: Gtk.Widget, widget: Gtk.Widget | None) -> Rect | None:
    """Widget allocation in ``root`` coordinates (canvas pixels)."""
    if widget is None or not widget.get_mapped():
        return None
    try:
        ok, bounds = widget.compute_bounds(root)
    except (TypeError, AttributeError):  # pragma: no cover - version drift
        return None
    if not ok or bounds is None:
        return None
    x = int(round(bounds.origin.x))
    y = int(round(bounds.origin.y))
    width = int(round(bounds.size.width))
    height = int(round(bounds.size.height))
    if width <= 0 or height <= 0:
        return None
    return Rect(x, y, width, height)


@dataclass
class Layer:
    """A cached chrome snapshot hanging at a fixed canvas rectangle."""

    name: str
    widget: Gtk.Widget
    image: "np.ndarray | None" = None
    rect: Rect | None = None
    backdrop: tuple[int, int, int, int] = (0, 0, 0, 255)


class CanvasBuilder:
    """Compose the recorder canvas: flat backdrop + chrome layers + boxes.

    Snapshotting the whole toplevel is possible but costs 35-70 ms and is
    flaky (``Gtk.WidgetPaintable.to_node()`` intermittently returns ``None``
    for a mapped window), so the canvas is rebuilt from its parts instead: a
    flat backdrop in the theme window colour, the chrome widgets captured
    individually (2-10 ms each), and a fully transparent video area that the
    mixer's video pad fills.
    """

    def __init__(self, window: Gtk.Widget) -> None:
        self._window = window
        self._layers: list[Layer] = []
        self._background: "np.ndarray | None" = None
        self._backdrop: tuple[int, int, int, int] = (0xFA, 0xFA, 0xFA, 255)
        self._video_widget: Gtk.Widget | None = None
        self._video_area: Rect | None = None
        self._size = (0, 0)
        self._base: "np.ndarray | None" = None
        self._base_frame: tuple[int, int] = (0, 0)
        self._dirty = True

    # -- configuration ------------------------------------------------------

    def set_backdrop(self, colour: tuple[int, int, int, int]) -> None:
        """Fill colour for the parts of the window no layer captures."""
        self._backdrop = (
            int(colour[0]) & 0xFF,
            int(colour[1]) & 0xFF,
            int(colour[2]) & 0xFF,
            255,
        )

    def add_layer(self, name: str, widget: Gtk.Widget) -> Layer:
        layer = Layer(name=name, widget=widget)
        self._layers.append(layer)
        return layer

    def set_video_widget(self, widget: Gtk.Widget) -> None:
        self._video_widget = widget

    @property
    def size(self) -> tuple[int, int]:
        return self._size

    @property
    def video_area(self) -> Rect | None:
        return self._video_area

    @property
    def is_ready(self) -> bool:
        return self._background is not None and self._video_area is not None

    # -- snapshots ----------------------------------------------------------

    def refresh_background(self) -> bool:
        """(Re)build the backdrop and canvas geometry, then the layers."""
        width = self._window.get_width()
        height = self._window.get_height()
        if width <= 0 or height <= 0:
            return False
        size = even_size(width, height)
        self._size = size
        self._video_area = widget_bounds(self._window, self._video_widget)
        image = np.empty((size[1], size[0], 4), dtype=np.uint8)
        image[:, :, 0] = self._backdrop[0]
        image[:, :, 1] = self._backdrop[1]
        image[:, :, 2] = self._backdrop[2]
        image[:, :, 3] = 255
        self._background = image
        for layer in self._layers:
            layer.image = None
            layer.rect = None
        self._base = None
        self._dirty = True
        return self.refresh()

    def refresh(self, names: "Sequence[str] | None" = None) -> bool:
        """Re-capture the chrome layers (fast path).

        ``names`` restricts the work to a subset of layers so the caller can
        capture the big, rarely changing pieces (the sidebar) less often than
        the small ones.  Returns True when at least one snapshot changed.
        """
        if self._background is None:
            return False
        self._video_area = widget_bounds(self._window, self._video_widget)
        changed = False
        for layer in self._layers:
            if names is not None and layer.name not in names:
                continue
            rect = widget_bounds(self._window, layer.widget)
            image = capture_widget(layer.widget)
            if rect is None or image is None:
                if layer.rect is not None or layer.image is not None:
                    layer.rect = None
                    layer.image = None
                    changed = True
                continue
            if (
                layer.rect == rect
                and layer.image is not None
                and layer.image.shape == image.shape
                and layer.image.size > 0
                and _same_pixels(layer.image, image)
            ):
                continue
            layer.rect = rect
            layer.image = image
            changed = True
        if changed:
            self._dirty = True
        return changed

    # -- composition --------------------------------------------------------

    def _rebuild_base(self, frame_size: tuple[int, int]) -> None:
        """Compose backdrop + chrome + video hole once, then reuse it.

        Pasting the sidebar costs ~30 ms, so the expensive composition is
        cached; per-tick work is just a 4 MB copy plus the (cheap) face boxes.
        """
        if self._background is None:
            return
        canvas = canvas_from_rgba(self._background.copy())
        area = self._video_area
        if area is not None:
            clipped = area.clip(canvas.shape[1], canvas.shape[0])
            if clipped is not None:
                # the window paints the video surface black; letterbox bars
                # around the fitted frame stay opaque, only the frame itself
                # is punched through for the mixer's video pad.
                canvas[clipped.y : clipped.bottom, clipped.x : clipped.right, 0:3] = 0
                canvas[clipped.y : clipped.bottom, clipped.x : clipped.right, 3] = 255
                if frame_size[0] > 0 and frame_size[1] > 0:
                    from ..recording import fit_rect

                    fitted = fit_rect(frame_size[0], frame_size[1], clipped)
                    shown = fitted.clip(canvas.shape[1], canvas.shape[0])
                    if shown is not None:
                        canvas[shown.y : shown.bottom, shown.x : shown.right, 3] = 0
        for layer in self._layers:
            if layer.image is not None and layer.rect is not None:
                _paste_layer(canvas, layer)
        self._base = canvas
        self._base_frame = frame_size
        self._dirty = False

    def compose(
        self,
        *,
        faces: Sequence[object] = (),
        frame_size: tuple[int, int] = (0, 0),
        draw_overlay: bool = True,
    ) -> "np.ndarray | None":
        """Build the RGBA canvas for the recorder.

        The fitted video rectangle is left transparent so the mixer's video pad
        shows through; face boxes are drawn over it when ``draw_overlay`` is
        set, matching the live ``_FaceOverlay``.
        """
        if self._background is None:
            return None
        if self._base is None or self._dirty or self._base_frame != frame_size:
            self._rebuild_base(frame_size)
        if self._base is None:
            return None
        canvas = self._base.copy()
        area = self._video_area
        if (
            draw_overlay
            and faces
            and area is not None
            and frame_size[0] > 0
            and frame_size[1] > 0
        ):
            draw_face_boxes(canvas, faces, frame_size[0], frame_size[1], area)
        return canvas

    def layer_report(self) -> list[str]:
        """One short line per layer, for ``REC_DEBUG`` diagnostics."""
        report = []
        for layer in self._layers:
            rect = layer.rect
            image = layer.image
            shape = None if image is None else image.shape
            report.append(f"{layer.name} rect={rect} image={shape}")
        return report

    # -- composition --------------------------------------------------------

def _same_pixels(first: "np.ndarray", second: "np.ndarray") -> bool:
    """True when two RGBA snapshots are pixel-identical."""
    if first.shape != second.shape:
        return False
    return bool(np.array_equal(first, second))


def _paste_layer(canvas: "np.ndarray", layer: Layer) -> None:
    """Alpha-blend a chrome snapshot over the (flat) canvas backdrop."""
    rect = layer.rect
    image = layer.image
    if rect is None or image is None:
        return
    height, width = canvas.shape[0], canvas.shape[1]
    # ``compute_bounds`` reports the widget allocation, which can be a couple of
    # pixels taller/wider than the rasterised snapshot (padding), so trust the
    # image size and keep the allocation's origin.
    size = Rect(rect.x, rect.y, min(rect.width, image.shape[1]), min(rect.height, image.shape[0]))
    clipped = size.clip(width, height)
    if clipped is None:
        return
    top = clipped.y - rect.y
    left = clipped.x - rect.x
    patch = image[top : top + clipped.height, left : left + clipped.width]
    if patch.shape[0] != clipped.height or patch.shape[1] != clipped.width:
        return
    target = canvas[clipped.y : clipped.bottom, clipped.x : clipped.right]
    alpha = patch[:, :, 3].astype(np.float32) / 255.0
    alpha = alpha[:, :, None]
    blended = (
        patch[:, :, :3].astype(np.float32) * alpha
        + target[:, :, :3].astype(np.float32) * (1.0 - alpha)
    )
    target[:, :, :3] = blended.astype(np.uint8)
    target[:, :, 3] = 255
