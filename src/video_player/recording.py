"""In-app 30 fps recorder: GPU video branch + a UI overlay canvas.

Why not record the whole window every frame?  On the RK3576 a full-window
snapshot through ``Gtk.WidgetPaintable`` costs ~36 ms (15 ms GL render plus
~22 ms dmabuf readback), which alone blows the 33 ms frame budget of 1080p30
playback.  Instead the recorder taps the already-GPU-resident video branch
with a ``tee`` and composites it, inside ``glvideomixer``, with a UI overlay
canvas that only has to be rebuilt at :data:`UI_FPS`, but is pushed to the
mixer at the full :data:`CANVAS_FPS` rate (videoaggregator only produces output
while every pad advances)::

    ... beauty ---- tee ---> queue -> glcolorscale -> capsfilter ---> mix.sink_1
                                                                    mix -> glcolorconvert
    canvas (10 fps) -> appsrc -> capsfilter (SystemMemory) ----> mix.sink_2      -> gldownload
                                                                    -> mpph264enc -> mp4mux

The canvas is a full-window RGBA image: the window chrome is pasted from
widget snapshots (a few hundred kilopixels in total, cheap) and the video
area is left fully transparent so the mixer's own video pad shows through.
Face boxes are drawn into the canvas by this module, mirroring
``ui.window._FaceOverlay`` (same colour, line width and CONTAIN fit).

Only the composition helpers are pure; :class:`Recorder` owns the GStreamer
elements and is created/stopped by :class:`video_player.backend.PlaybackBackend`.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Callable, Sequence

import gi

gi.require_version("Gst", "1.0")
gi.require_version("GstVideo", "1.0")
from gi.repository import GLib, Gst, GstVideo  # noqa: E402

import numpy as np  # noqa: E402

if TYPE_CHECKING:  # pragma: no cover - typing only
    from numpy.typing import NDArray

#: Canvas / output frame rate of the recording.
CANVAS_FPS = 30
#: Rate the UI canvas is rebuilt at.  The video branch keeps running at its own
#: frame rate (30 fps); ``glvideomixer`` repeats the last canvas in between.
UI_FPS = 10
#: Encoder target bitrate (1.5 MiB/s at 1280x800 keeps the demo crisp).
DEFAULT_BITRATE = 12_000_000
#: Same green as ``_FaceOverlay`` (cairo rgba 0.18 / 0.86 / 0.42 / 0.95).
FACE_BOX_COLOR = (46, 219, 107, 242)
FACE_BOX_WIDTH = 2
FACE_LANDMARK_RADIUS = 2

_VIDEO_QUEUE = "rec-video-queue"
_VIDEO_FLIP = "rec-video-flip"
_VIDEO_CAPS = "rec-video-caps"
_VIDEO_SCALE = "rec-video-scale"
_UI_SRC = "rec-ui-src"
_UI_CAPS = "rec-ui-caps"
_MIXER = "rec-mixer"
_CONVERT = "rec-colorconvert"
_OUT_CAPS = "rec-out-caps"
_DOWNLOAD = "rec-download"
_ENCODER = "rec-encoder"
_PARSE = "rec-parse"
_MUX = "rec-mux"
_SINK = "rec-sink"
_PREFIX = "rec-"

# The display sink applies the MP4 display matrix itself; the recorder taps the
# branch before the sink, so it has to rotate the untouched decoder frames the
# same way, otherwise a rotate-180 phone clip is recorded upside down.
ORIENTATION_METHODS = {
    90: getattr(GstVideo.VideoOrientationMethod, "90R"),
    180: getattr(GstVideo.VideoOrientationMethod, "180"),
    270: getattr(GstVideo.VideoOrientationMethod, "90L"),
}


@dataclass(frozen=True)
class Rect:
    """Integer rectangle in canvas coordinates."""

    x: int
    y: int
    width: int
    height: int

    @property
    def right(self) -> int:
        return self.x + self.width

    @property
    def bottom(self) -> int:
        return self.y + self.height

    def clip(self, width: int, height: int) -> "Rect | None":
        """Intersect with the ``0,0,width,height`` canvas; None when empty."""
        left = max(0, self.x)
        top = max(0, self.y)
        right = min(width, self.right)
        bottom = min(height, self.bottom)
        if right <= left or bottom <= top:
            return None
        return Rect(left, top, right - left, bottom - top)


def even_size(width: int, height: int) -> tuple[int, int]:
    """Round a canvas size down to even dimensions (H.264 4:2:0 requirement)."""
    return max(2, int(width) - int(width) % 2), max(2, int(height) - int(height) % 2)


def fit_rect(
    frame_width: int, frame_height: int, area: Rect
) -> Rect:
    """Fit a ``frame_width``x``frame_height`` frame inside ``area`` (CONTAIN).

    Mirrors ``Gtk.Picture`` with :attr:`Gtk.ContentFit.CONTAIN`: the frame keeps
    its aspect ratio, is scaled down until it fits and is centred.  The result
    is what the camera/display rendering shows, so the recording puts the video
    pad exactly there.
    """
    if frame_width <= 0 or frame_height <= 0:
        return Rect(area.x, area.y, 0, 0)
    if area.width <= 0 or area.height <= 0:
        return Rect(area.x, area.y, 0, 0)
    scale = min(area.width / frame_width, area.height / frame_height)
    width = max(1, round(frame_width * scale))
    height = max(1, round(frame_height * scale))
    return Rect(
        area.x + (area.width - width) // 2,
        area.y + (area.height - height) // 2,
        width,
        height,
    )


def face_geometry(
    faces: Sequence[object], frame_width: int, frame_height: int, area: Rect
) -> list[tuple[tuple[float, float, float, float], list[tuple[float, float]]]]:
    """Map detected faces from frame coordinates into canvas coordinates.

    ``faces`` are :class:`video_player.face.Face` instances (``x``, ``y``,
    ``width``, ``height`` and ``landmarks`` in frame pixels).  The mapping is
    the same CONTAIN fit as :func:`fit_rect`.
    """
    if frame_width <= 0 or frame_height <= 0 or area.width <= 0 or area.height <= 0:
        return []
    scale = min(area.width / frame_width, area.height / frame_height)
    offset_x = area.x + (area.width - frame_width * scale) / 2.0
    offset_y = area.y + (area.height - frame_height * scale) / 2.0
    result: list[tuple[tuple[float, float, float, float], list[tuple[float, float]]]] = []
    for face in faces:
        box = (
            offset_x + float(face.x) * scale,
            offset_y + float(face.y) * scale,
            float(face.width) * scale,
            float(face.height) * scale,
        )
        landmarks = [
            (offset_x + float(x) * scale, offset_y + float(y) * scale)
            for x, y in getattr(face, "landmarks", ())
        ]
        result.append((box, landmarks))
    return result


def _paint(canvas: "NDArray[np.uint8]", rect: Rect, color: tuple[int, int, int, int]) -> None:
    clipped = rect.clip(canvas.shape[1], canvas.shape[0])
    if clipped is None:
        return
    region = canvas[clipped.y : clipped.bottom, clipped.x : clipped.right]
    region[:, :, 0] = color[0]
    region[:, :, 1] = color[1]
    region[:, :, 2] = color[2]
    region[:, :, 3] = color[3]


def draw_face_boxes(
    canvas: "NDArray[np.uint8]",
    faces: Sequence[object],
    frame_width: int,
    frame_height: int,
    area: Rect,
    *,
    color: tuple[int, int, int, int] = FACE_BOX_COLOR,
    line_width: int = FACE_BOX_WIDTH,
    landmark_radius: int = FACE_LANDMARK_RADIUS,
) -> None:
    """Draw the detector overlay (boxes + landmarks) into an RGBA canvas."""
    for (x, y, width, height), landmarks in face_geometry(
        faces, frame_width, frame_height, area
    ):
        left = int(round(x))
        top = int(round(y))
        right = int(round(x + width)) - 1
        bottom = int(round(y + height)) - 1
        if right <= left or bottom <= top:
            continue
        _paint(canvas, Rect(left, top, right - left + 1, line_width), color)
        _paint(canvas, Rect(left, bottom - line_width + 1, right - left + 1, line_width), color)
        _paint(canvas, Rect(left, top, line_width, bottom - top + 1), color)
        _paint(canvas, Rect(right - line_width + 1, top, line_width, bottom - top + 1), color)
        radius = max(1, int(landmark_radius))
        for dot_x, dot_y in landmarks:
            centre_x = int(round(dot_x))
            centre_y = int(round(dot_y))
            span = radius * radius + radius
            for delta_y in range(-radius, radius + 1):
                for delta_x in range(-radius, radius + 1):
                    if delta_x * delta_x + delta_y * delta_y <= span - radius:
                        _paint(
                            canvas,
                            Rect(centre_x + delta_x, centre_y + delta_y, 1, 1),
                            color,
                        )


def canvas_from_rgba(image: "NDArray[np.uint8]") -> "NDArray[np.uint8]":
    """Validate/normalise an RGBA canvas array (contiguous ``uint8`` HxWx4)."""
    array = np.ascontiguousarray(image, dtype=np.uint8)
    if array.ndim != 3 or array.shape[2] != 4:
        raise ValueError(f"expected an (h, w, 4) RGBA array, got {array.shape!r}")
    return array


def _debug() -> bool:
    """True when ``REC_DEBUG=1`` is in the environment."""
    return bool(os.environ.get("REC_DEBUG"))


def _element(factory: str, name: str) -> Gst.Element:
    element = Gst.ElementFactory.make(factory, name)
    if element is None:
        raise RecordingError(f"missing GStreamer element: {factory}")
    return element


class RecordingError(RuntimeError):
    """Raised when the recording pipeline cannot be built."""


class Recorder:
    """Compose the video branch and the UI canvas into an MP4 file.

    The recorder lives inside the player pipeline: the backend passes its
    ``tee`` (the same one that feeds the display sink) and the recorder links
    a new branch off it.  Frame timing is taken from the video buffers: the UI
    canvas is pushed from the video streaming thread with the *same* PTS every
    ``CANVAS_FPS / UI_FPS`` frames, so the two inputs of the mixer stay locked
    together even when the main loop is busy.
    """

    def __init__(
        self,
        pipeline: Gst.Pipeline,
        tee: Gst.Element,
        path: str | Path,
        *,
        canvas_size: tuple[int, int],
        video_size: tuple[int, int] | None = None,
        rotation: int = 0,
        fps: int = CANVAS_FPS,
        bitrate: int = DEFAULT_BITRATE,
    ) -> None:
        self._pipeline = pipeline
        self._tee = tee
        self._path = Path(path)
        self._fps = max(1, int(fps))
        self._bitrate = max(0, int(bitrate))
        self._width, self._height = even_size(*canvas_size)
        if self._width <= 0 or self._height <= 0:
            raise RecordingError("the recording canvas has no size")
        self._video_size = even_size(*(video_size or canvas_size))
        if self._video_size[0] <= 0 or self._video_size[1] <= 0:
            raise RecordingError("the recorded video area has no size")
        self._rotation = int(rotation) % 360
        if self._rotation not in (0, 90, 180, 270):
            raise RecordingError(f"unsupported rotation {rotation!r}")
        self._elements: list[Gst.Element] = []
        self._ee_pad: Gst.Pad | None = None  # tee request pad
        self._video_queue: Gst.Element | None = None
        self._mixer: Gst.Element | None = None
        self._ui_src: Gst.Element | None = None
        self._video_pad: Gst.Pad | None = None
        self._ui_pad: Gst.Pad | None = None
        self._probe_id: int = 0
        self._encode_probe_id: int = 0
        self._eos_probe_id: int = 0
        self._mux_pad: Gst.Pad | None = None
        self._encoded_frames: int = 0
        self._ui_pushes: int = 0
        self._video_buffers: int = 0
        self._bus_handlers: list[int] = []
        self._canvas: Gst.Buffer | None = None
        self._frame_index = 0
        self._running = False
        self._stopping = False
        self._eos_reached = False
        self._stop_loop: GLib.MainLoop | None = None
        self._video_rect = Rect(0, 0, self._width, self._height)
        self._frame_size = (0, 0)
        #: Called with a human-readable message when the recorder fails.
        self.on_error: Callable[[str], None] | None = None

    # -- properties ---------------------------------------------------------

    @property
    def path(self) -> Path:
        return self._path

    @property
    def rotation(self) -> int:
        """Display rotation applied to the tapped frames, in degrees."""
        return self._rotation

    @property
    def stats(self) -> tuple[int, int, int]:
        """(encoded frames, UI canvas pushes, video buffers) since start."""
        return self._encoded_frames, self._ui_pushes, self._video_buffers

    @property
    def is_recording(self) -> bool:
        return self._running

    @property
    def canvas_size(self) -> tuple[int, int]:
        return self._width, self._height

    @property
    def video_rect(self) -> Rect:
        return self._video_rect

    # -- geometry -----------------------------------------------------------

    def set_video_geometry(self, rect: Rect, frame_size: tuple[int, int]) -> None:
        """Place (and scale) the video pad inside the canvas.

        ``rect`` is the CONTAIN-fitted frame rectangle in canvas coordinates;
        ``frame_size`` is the displayed frame size, used by face box drawing.
        """
        self._video_rect = rect
        self._frame_size = frame_size
        if self._video_pad is None:
            return
        self._video_pad.set_property("xpos", max(0, int(rect.x)))
        self._video_pad.set_property("ypos", max(0, int(rect.y)))
        self._video_pad.set_property("width", max(1, int(rect.width)))
        self._video_pad.set_property("height", max(1, int(rect.height)))

    def set_canvas(self, image: "NDArray[np.uint8]") -> None:
        """Replace the UI overlay canvas (called from the GTK main thread)."""
        array = canvas_from_rgba(image)
        if array.shape[0] != self._height or array.shape[1] != self._width:
            raise ValueError(
                f"canvas is {array.shape[1]}x{array.shape[0]}, "
                f"expected {self._width}x{self._height}"
            )
        frame = Gst.Buffer.new_wrapped(array.tobytes())
        frame.duration = Gst.util_uint64_scale(1, Gst.SECOND, self._fps)
        self._canvas = frame

    # -- lifecycle ----------------------------------------------------------

    def start(self) -> None:
        """Build the recording branch and attach it to the running pipeline."""
        if self._running:
            return
        pipeline = self._pipeline
        name = _PREFIX.rstrip("-")
        self._path.parent.mkdir(parents=True, exist_ok=True)
        caps_value = Gst.Caps.from_string(
            "video/x-raw(memory:GLMemory),format=RGBA,"
            f"width={self._video_size[0]},height={self._video_size[1]},texture-target=2D"
        )
        ui_caps_value = Gst.Caps.from_string(
            f"video/x-raw,format=RGBA,width={self._width},height={self._height},framerate={self._fps}/1"
        )
        try:
            video_queue = _element("queue", _VIDEO_QUEUE)
            video_flip = (
                _element("glvideoflip", _VIDEO_FLIP) if self._rotation else None
            )
            video_scale = _element("glcolorscale", _VIDEO_SCALE)
            video_caps = _element("capsfilter", _VIDEO_CAPS)
            ui_src = _element("appsrc", _UI_SRC)
            ui_caps = _element("capsfilter", _UI_CAPS)
            mixer = _element("glvideomixer", _MIXER)
            out_caps = _element("capsfilter", _OUT_CAPS)
            download = _element("gldownload", _DOWNLOAD)
            encoder = _element("mpph264enc", _ENCODER)
            parser = _element("h264parse", _PARSE)
            mux = _element("mp4mux", _MUX)
            sink = _element("filesink", _SINK)
        except RecordingError:
            raise
        if video_flip is not None:
            video_flip.set_property(
                "video-direction", ORIENTATION_METHODS[self._rotation]
            )
        video_caps.set_property("caps", caps_value)
        # leaky: if the recorder cannot keep up, drop frames for the file
        # instead of slowing the display branch that shares the tee.
        video_queue.set_property("leaky", 2)  # downstream
        video_queue.set_property("max-size-buffers", 2)
        video_queue.set_property("max-size-bytes", 0)
        video_queue.set_property("max-size-time", 0)
        ui_caps.set_property("caps", ui_caps_value)
        # RGBA, not NV12: glcolorconvert to NV12 measured 27-36 fps on the
        # board vs 90 fps for an RGBA download, and mpph264enc accepts RGBA.
        out_caps.set_property("caps", Gst.Caps.from_string(
            f"video/x-raw(memory:GLMemory),format=RGBA,width={self._width},"
            f"height={self._height},framerate={self._fps}/1"
        ))
        ui_src.set_property("caps", ui_caps_value)
        ui_src.set_property("format", Gst.Format.TIME)
        ui_src.set_property("is-live", False)
        ui_src.set_property("block", False)
        ui_src.set_property("max-buffers", 3)
        mixer.set_property("background", 0)  # black, like the player CSS
        try:
            encoder.set_property("bps", self._bitrate)
        except TypeError:  # pragma: no cover - older encoder builds
            pass
        sink.set_property("location", str(self._path))
        sink.set_property("sync", False)
        sink.set_property("async", False)

        tail: Gst.Element = download
        elements = [video_queue]
        chain: list[tuple[Gst.Element, Gst.Element]] = []
        source = video_queue
        if video_flip is not None:
            elements.append(video_flip)
            chain.append((source, video_flip))
            source = video_flip
        elements.extend([video_scale, video_caps, ui_src, ui_caps, mixer, out_caps])
        chain.extend(
            [
                (source, video_scale),
                (video_scale, video_caps),
                (ui_src, ui_caps),
                (mixer, out_caps),
            ]
        )
        elements.extend([download, encoder, parser, mux, sink])
        chain.extend(
            [
                (out_caps, download),
                (download, encoder),
                (encoder, parser),
                (parser, mux),
                (mux, sink),
            ]
        )
        for element in elements:
            pipeline.add(element)
        for upstream, downstream in chain:
            if not upstream.link(downstream):
                self._detach(elements)
                raise RecordingError(
                    f"cannot link {upstream.get_name()} to {downstream.get_name()}"
                )
        video_pad = mixer.request_pad_simple("sink_1")
        ui_pad = mixer.request_pad_simple("sink_2")
        if video_pad is None or ui_pad is None:
            self._detach(elements)
            raise RecordingError("glvideomixer has no sink pads")
        video_pad.set_property("zorder", 0)
        video_pad.set_property("width", self._video_size[0])
        video_pad.set_property("height", self._video_size[1])
        ui_pad.set_property("zorder", 1)
        ui_pad.set_property("xpos", 0)
        ui_pad.set_property("ypos", 0)
        ui_pad.set_property("width", self._width)
        ui_pad.set_property("height", self._height)
        self._video_pad = video_pad
        self._ui_pad = ui_pad
        result = video_caps.get_static_pad("src").link(video_pad)
        if result != Gst.PadLinkReturn.OK:
            self._detach(elements)
            raise RecordingError(
                f"cannot link the video scaler to the mixer: {result.value_nick} "
                f"src={video_caps.get_static_pad('src').get_current_caps()} "
                f"pad_template={video_pad.get_pad_template().get_caps()}"
            )
        ui_result = ui_caps.get_static_pad("src").link(ui_pad)
        if ui_result != Gst.PadLinkReturn.OK:
            self._detach(elements)
            raise RecordingError(
                f"cannot link the UI canvas to the mixer: {ui_result.value_nick} "
                f"src={ui_caps.get_static_pad('src').get_current_caps()} "
                f"pad={ui_pad.get_name()}"
            )
        self.set_video_geometry(self._video_rect, self._frame_size)

        tee_pad = self._tee.request_pad_simple("src_%u")
        if tee_pad is None:
            self._detach(elements)
            raise RecordingError("cannot request a pad from the video tee")
        self._ee_pad = tee_pad
        if tee_pad.link(video_queue.get_static_pad("sink")) != Gst.PadLinkReturn.OK:
            self._detach(elements)
            raise RecordingError("cannot link the video tee to the recorder")
        self._video_queue = video_queue
        self._mixer = mixer
        self._ui_src = ui_src
        self._elements = elements
        # Follow the already-PLAYING parent state, sinks first so no element
        # starts pushing into a NULL peer.
        for element in reversed(elements):
            element.sync_state_with_parent()
        probe = video_queue.get_static_pad("sink")
        if probe is not None:
            self._probe_id = probe.add_probe(Gst.PadProbeType.BUFFER, self._on_video_buffer)
        encode_pad = tail.get_static_pad("sink")
        if encode_pad is not None:
            self._encode_probe_id = encode_pad.add_probe(
                Gst.PadProbeType.BUFFER, self._on_encoded_buffer
            )
        # mp4mux only has request pads, so watch the parser's static src pad for
        # the EOS that comes out of the encoder once the branch drained.
        eos_pad = parser.get_static_pad("src")
        if eos_pad is not None:
            self._mux_pad = eos_pad
            self._eos_probe_id = eos_pad.add_probe(
                Gst.PadProbeType.EVENT_DOWNSTREAM, self._on_mux_event
            )
        # The backend already installed a signal watch on this bus, so plain
        # signal connections see the messages without a second watch.
        bus = pipeline.get_bus()
        self._bus_handlers = [bus.connect("message", self._on_bus)]
        self._frame_index = 0
        if _debug():
            print(
                f"[rec] probes video={self._probe_id} encode={self._encode_probe_id} id={id(self):#x}",
                flush=True,
            )
        self._stopping = False
        self._eos_reached = False
        self._running = True

    def stop(self, *, timeout_ms: int = 1500) -> None:
        """Finish the file: EOS the branch, wait for the muxer, tear down."""
        if not self._running or self._stopping:
            return
        self._stopping = True
        if _debug():
            print("[rec] stopping", flush=True)
        if self._video_queue is not None and self._probe_id:
            pad = self._video_queue.get_static_pad("sink")
            if pad is not None:
                pad.remove_probe(self._probe_id)
            self._probe_id = 0
        if self._ee_pad is not None:
            # Stop the tee from feeding the recorder; the display branch keeps
            # running untouched.
            self._ee_pad.set_active(False)
        if self._ui_src is not None:
            self._ui_src.emit("end-of-stream")
        if self._video_queue is not None:
            # Send EOS *into* the queue so it drains its buffered frames before
            # the event reaches the mixer (posting it on the src pad would cut
            # the tail off and leave the mp4 without a moov atom).
            self._video_queue.send_event(Gst.Event.new_eos())
        loop = GLib.MainLoop()
        self._stop_loop = loop
        timer = GLib.timeout_add(timeout_ms, self._on_stop_timeout, loop)
        try:
            if not self._eos_reached:
                loop.run()
        finally:
            self._stop_loop = None
        if timer:
            GLib.source_remove(timer)
        if self._mux_pad is not None and self._eos_probe_id:
            self._mux_pad.remove_probe(self._eos_probe_id)
            self._eos_probe_id = 0
        if _debug():
            print(f"[rec] stopped eos={self._eos_reached}", flush=True)
        if self._bus_handlers:
            bus = self._pipeline.get_bus()
            for handler in self._bus_handlers:
                bus.disconnect(handler)
            self._bus_handlers = []
        self._teardown()
        self._running = False
        self._stopping = False

    # -- internals ----------------------------------------------------------

    def _on_stop_timeout(self, loop: GLib.MainLoop) -> bool:
        loop.quit()
        return False

    def _on_mux_event(self, _pad: Gst.Pad, info: Gst.PadProbeInfo) -> Gst.PadProbeReturn:
        """Notice the EOS that reaches the muxer once both pads drained.

        The pipeline never posts EOS while the display branch keeps playing, so
        the bus is not usable as the stop signal.
        """
        event = info.get_event()
        if event is not None and event.type == Gst.EventType.EOS:
            self._eos_reached = True
            loop = self._stop_loop
            if self._stopping and loop is not None:
                loop.quit()
        return Gst.PadProbeReturn.OK

    def _on_bus(self, _bus: Gst.Bus, message: Gst.Message) -> bool:
        source = message.src
        name = source.get_name() if isinstance(source, Gst.Element) else ""
        if not name.startswith(_PREFIX):
            return True
        if message.type == Gst.MessageType.ERROR:
            error, debug = message.parse_error()
            detail = error.message if error is not None else "recording failed"
            if debug:
                detail = f"{detail} ({debug.splitlines()[0]})"
            self._dispatch_error(detail)
        return True

    def _dispatch_error(self, detail: str) -> None:
        if self.on_error is not None:
            GLib.idle_add(self.on_error, detail)

    def _on_encoded_buffer(
        self, _pad: Gst.Pad, _info: Gst.PadProbeInfo
    ) -> Gst.PadProbeReturn:
        self._encoded_frames += 1
        return Gst.PadProbeReturn.OK

    def _on_video_buffer(self, _pad: Gst.Pad, info: Gst.PadProbeInfo) -> Gst.PadProbeReturn:
        self._video_buffers += 1
        if _debug() and self._frame_index == 0:
            print(f"[rec] first video buffer on {self!r}", flush=True)
        if not self._running or self._stopping:
            return Gst.PadProbeReturn.OK
        self._frame_index += 1
        canvas = self._canvas
        if canvas is None:
            return Gst.PadProbeReturn.OK
        buffer = info.get_buffer()
        if buffer is None:
            return Gst.PadProbeReturn.OK
        # The mixer needs one UI frame per output frame: reuse the canvas
        # memory (copy() is a shallow metadata copy) and just stamp it with the
        # video buffer's timestamp.
        ui_frame = canvas.copy()
        ui_frame.pts = buffer.pts
        ui_frame.duration = Gst.util_uint64_scale(1, Gst.SECOND, self._fps)
        self._ui_pushes += 1
        result = self._ui_src.emit("push-buffer", ui_frame)
        if isinstance(result, Gst.FlowReturn) and result != Gst.FlowReturn.OK:
            self._dispatch_error(f"cannot push the UI canvas (flow {result.value_name})")
        return Gst.PadProbeReturn.OK

    def _teardown(self) -> None:
        elements, self._elements = self._elements, []
        for element in elements:
            element.set_state(Gst.State.NULL)
        if self._ee_pad is not None:
            pad, self._ee_pad = self._ee_pad, None
            pad.set_active(True)
            pad.unlink(pad.get_peer())
            self._tee.release_request_pad(pad)
        for element in elements:
            self._pipeline.remove(element)
        self._video_queue = None
        self._mixer = None
        self._ui_src = None
        self._video_pad = None
        self._ui_pad = None
        self._canvas = None

    def _detach(self, elements: Sequence[Gst.Element]) -> None:
        """Undo a partially built branch (start() failure path)."""
        for element in elements:
            element.set_state(Gst.State.NULL)
        if self._ee_pad is not None:
            pad, self._ee_pad = self._ee_pad, None
            self._tee.release_request_pad(pad)
        for element in elements:
            try:
                self._pipeline.remove(element)
            except Exception:  # pragma: no cover - best effort cleanup
                pass
