"""GStreamer playback backend (M1).

The backend owns one :class:`Gst.Pipeline` for the current file.  It builds the
video/audio branches dynamically when the demuxer announces its pads:

* video: ``queue ! <parser> ! <decoder> ! <converter> ! fpsdisplaysink`` with
  ``gtk4paintablesink`` as the actual video sink, so the window embeds the
  frames through a ``GdkPaintable``.  The converter is ``glupload !
  glcolorconvert ! capsfilter`` (GPU colour conversion) when available and
  ``videoconvert`` otherwise; see :func:`PlaybackBackend._video_converters`;
* audio: ``queue ! decodebin ! audioconvert ! audioresample ! volume !
  autoaudiosink``.

The UI only talks to this class: it exposes the paintable, transport controls,
seeking, rate and volume, plus callbacks for state/EOS/errors/video info/fps.
All callbacks are dispatched through the GTK main loop.
"""

from __future__ import annotations

from collections import deque
from pathlib import Path
import time
from typing import Callable

import gi

gi.require_version("Gst", "1.0")
from gi.repository import GLib, Gst  # noqa: E402

from .config import Config
from .detection import FaceDetectionTap
from .face import DetectionResult
from .pipeline import decoder_for, demux_for, is_stream_uri, parser_for

__all__ = ["PlaybackBackend", "PlaybackError"]

GST_SECOND = Gst.SECOND
SEEK_FLAGS = Gst.SeekFlags.FLUSH | Gst.SeekFlags.ACCURATE


class PlaybackError(RuntimeError):
    """Raised when playback cannot be started (bad file, missing element)."""


def _make(factory: str, name: str) -> Gst.Element:
    element = Gst.ElementFactory.make(factory, name)
    if element is None:
        raise PlaybackError(
            f"GStreamer element {factory!r} is not installed; "
            "install the board packages from docs/BOARD.md and retry"
        )
    return element


class PlaybackBackend:
    """Thin, UI-agnostic wrapper around one GStreamer playback pipeline."""

    def __init__(self, config: Config) -> None:
        Gst.init(None)
        self._config = config
        self._pipeline: Gst.Pipeline | None = None
        self._bus: Gst.Bus | None = None
        self._bus_handler: int | None = None
        self._video_sink: Gst.Element | None = None
        self._fps_sink: Gst.Element | None = None
        self._volume_element: Gst.Element | None = None
        self._paintable = None
        self._decoder_name: str | None = None
        self._converter_name: str | None = None
        self._rate = 1.0
        self._playing = False
        self._last_fps = 0.0
        self._frame_times: deque[float] = deque(maxlen=240)
        self._tap: FaceDetectionTap | None = None
        self._detection_failed = False
        self.video_info: dict[str, object] = {}
        self.uri: str | None = None

        #: Called with no arguments when the pipeline reaches end of stream.
        self.on_eos: Callable[[], None] | None = None
        #: Called with a human-readable message when playback fails.
        self.on_error: Callable[[str], None] | None = None
        #: Called with ``"playing"`` or ``"paused"`` after a state change.
        self.on_state: Callable[[str], None] | None = None
        #: Called with a fresh copy of :attr:`video_info`.
        self.on_video_info: Callable[[dict[str, object]], None] | None = None
        #: Called with the average fps (rounded to one decimal) about once a second.
        self.on_fps: Callable[[float], None] | None = None
        #: Called with a fresh :class:`DetectionResult` when the NPU finishes a frame.
        self.on_detection: Callable[[DetectionResult], None] | None = None
        #: Called with a human-readable message when face detection cannot run.
        self.on_detection_error: Callable[[str], None] | None = None

    # -- properties --------------------------------------------------------

    @property
    def paintable(self):
        """The ``GdkPaintable`` produced by ``gtk4paintablesink`` (or None)."""
        return self._paintable

    @property
    def decoder_name(self) -> str | None:
        return self._decoder_name

    @property
    def last_fps(self) -> float:
        return self._last_fps

    @property
    def is_playing(self) -> bool:
        return self._playing

    @property
    def has_media(self) -> bool:
        return self._pipeline is not None

    # -- playback control --------------------------------------------------

    def open(self, media: str | Path, *, autoplay: bool = True) -> None:
        """Open a local media file, replacing any current pipeline.

        Raises:
            PlaybackError: for missing files, stream URIs or missing elements.
        """
        text = str(media)
        if is_stream_uri(text):
            raise PlaybackError(
                "this release plays local files only; network streams are out of scope"
            )
        path = Path(text).expanduser()
        if not path.is_file():
            raise PlaybackError(f"media file not found: {path}")
        # Relative CLI arguments must still become a valid file:// URI for logs
        # and for the playlist label; Gst itself receives the resolved path.
        path = path.resolve()

        self.close()
        demux_factory = demux_for(path)
        pipeline = Gst.Pipeline.new("player")
        source = _make("filesrc", "source")
        source.set_property("location", str(path))
        demux = _make(demux_factory, "demux")
        pipeline.add(source)
        pipeline.add(demux)
        if not source.link(demux):
            raise PlaybackError(f"cannot link filesrc to {demux_factory}")
        demux.connect("pad-added", self._on_demux_pad_added)

        self._pipeline = pipeline
        self.uri = path.as_uri()
        self._build_video_output()
        self._prepare_detection()

        self._bus = pipeline.get_bus()
        self._bus.add_signal_watch()
        self._bus_handler = self._bus.connect("message", self._on_bus_message)

        self._rate = 1.0
        self._playing = False
        self.video_info = {}
        target = Gst.State.PLAYING if autoplay else Gst.State.PAUSED
        self._pipeline.set_state(target)

    def play(self) -> None:
        if self._pipeline is not None:
            self._pipeline.set_state(Gst.State.PLAYING)
            self._playing = True

    def pause(self) -> None:
        if self._pipeline is not None:
            self._pipeline.set_state(Gst.State.PAUSED)
            self._playing = False

    def toggle_play(self) -> None:
        if not self.is_playing:
            self.play()
        else:
            self.pause()

    def seek_ns(self, position_ns: int) -> None:
        """Seek to an absolute position; silently ignored without a pipeline."""
        if self._pipeline is None:
            return
        if position_ns < 0:
            position_ns = 0
        self._pipeline.seek(
            self._rate,
            SEEK_FLAGS,
            Gst.Format.TIME,
            Gst.SeekType.SET,
            position_ns,
            Gst.SeekType.NONE,
            -1,
        )

    def seek_fraction(self, fraction: float) -> None:
        duration = self.duration_ns()
        if duration <= 0:
            return
        fraction = max(0.0, min(1.0, fraction))
        self.seek_ns(int(duration * fraction))

    def set_rate(self, rate: float) -> None:
        """Change playback speed, keeping the current position."""
        rate = max(0.25, min(4.0, float(rate)))
        if rate == self._rate:
            return
        position = self.position_ns()
        self._rate = rate
        if self._pipeline is not None and position > 0:
            self._pipeline.seek(
                rate,
                SEEK_FLAGS,
                Gst.Format.TIME,
                Gst.SeekType.SET,
                position,
                Gst.SeekType.NONE,
                -1,
            )

    def set_volume(self, percent: int) -> None:
        """Set volume from the user-facing 0-100 scale."""
        value = max(0.0, min(100.0, float(percent))) / 100.0
        if self._volume_element is not None:
            self._volume_element.set_property("volume", value)

    def position_ns(self) -> int:
        if self._pipeline is None:
            return 0
        success, position = self._pipeline.query_position(Gst.Format.TIME)
        return int(position) if success else 0

    def duration_ns(self) -> int:
        if self._pipeline is None:
            return 0
        success, duration = self._pipeline.query_duration(Gst.Format.TIME)
        return int(duration) if success else 0

    def close(self) -> None:
        """Stop playback and release all GStreamer resources."""
        self._stop_detection()
        pipeline, self._pipeline = self._pipeline, None
        if self._bus is not None:
            if self._bus_handler is not None:
                self._bus.disconnect(self._bus_handler)
                self._bus_handler = None
            self._bus.remove_signal_watch()
            self._bus = None
        if pipeline is not None:
            pipeline.set_state(Gst.State.NULL)
        self._video_sink = None
        self._fps_sink = None
        self._volume_element = None
        self._paintable = None
        self._decoder_name = None
        self._converter_name = None
        self._playing = False
        self._last_fps = 0.0
        self._frame_times.clear()
        self.video_info = {}
        self.uri = None

    # -- pipeline construction --------------------------------------------

    def _build_video_output(self) -> None:
        """Create the video sink (embedded paintable sink when available)."""
        pipeline = self._pipeline
        assert pipeline is not None
        configured = self._config.player.render_sink
        embed = None
        if configured == "gtk4paintablesink":
            embed = Gst.ElementFactory.make("gtk4paintablesink", "video-sink")

        if embed is None:
            # Off-board / fallback: plain sink, the UI shows an external window.
            fallback = Gst.ElementFactory.make(configured, "video-sink")
            if fallback is None:
                fallback = Gst.ElementFactory.make("autovideosink", "video-sink")
            if fallback is None:
                raise PlaybackError("no usable video sink (gtk4paintablesink/autovideosink)")
            pipeline.add(fallback)
            self._video_sink = fallback
            self._fps_sink = fallback
            return

        self._video_sink = embed
        fps = Gst.ElementFactory.make("fpsdisplaysink", "fps-sink")
        if fps is None:
            pipeline.add(embed)
            self._fps_sink = embed
            self._paintable = embed.get_property("paintable")
            return
        fps.set_property("video-sink", embed)
        fps.set_property("text-overlay", False)
        fps.set_property("signal-fps-measurements", True)
        fps.connect("fps-measurements", self._on_fps_measurements)
        pipeline.add(fps)
        self._fps_sink = fps
        self._paintable = embed.get_property("paintable")

    def _video_converters(self) -> list[Gst.Element]:
        """Return the colour-conversion elements for the video branch.

        GPU conversion is required on the RK3576 board: CPU ``videoconvert``
        only reaches ~15 fps at 1080p because the decoded NV12 DMABuf has to be
        mapped and converted on the CPU.  ``glupload`` imports the DMABuf and
        ``glcolorconvert`` converts to RGBA on the GPU, which sustains 30 fps.
        """
        mode = self._config.player.video_convert
        if mode in ("auto", "gl"):
            upload = Gst.ElementFactory.make("glupload", "video-upload")
            convert = Gst.ElementFactory.make("glcolorconvert", "video-glcolorconvert")
            caps = Gst.ElementFactory.make("capsfilter", "video-glcaps")
            if upload is not None and convert is not None and caps is not None:
                caps.set_property(
                    "caps",
                    Gst.Caps.from_string("video/x-raw(memory:GLMemory),format=RGBA"),
                )
                return [upload, convert, caps]
            if mode == "gl":
                raise PlaybackError(
                    "player.video_convert=gl but glupload/glcolorconvert "
                    "are not installed (install gstreamer1.0-plugins-base)"
                )
        return [_make("videoconvert", "video-convert")]

    def _on_demux_pad_added(self, _demux, pad: Gst.Pad) -> None:
        caps = pad.get_current_caps() or pad.query_caps(None)
        if caps is None or caps.get_size() == 0:
            return
        structure = caps.get_structure(0)
        name = structure.get_name()
        if name.startswith("video/"):
            self._build_video_branch(pad, name, structure)
        elif name.startswith("audio/"):
            self._build_audio_branch(pad)

    def _build_video_branch(
        self, pad: Gst.Pad, caps_name: str, structure: Gst.Structure
    ) -> None:
        try:
            parser_factory = parser_for(caps_name)
            decoder_factory = decoder_for(caps_name, self._config.player.hw_decode)
            converters = self._video_converters()
        except ValueError as exc:
            self._fail(str(exc))
            return
        except PlaybackError as exc:
            self._fail(str(exc))
            return
        try:
            queue = _make("queue", "video-queue")
            parser = _make(parser_factory, "video-parser")
            decoder = _make(decoder_factory, "video-decoder")
        except PlaybackError as exc:
            self._fail(str(exc))
            return
        pipeline = self._pipeline
        if pipeline is None:
            return
        elements = (queue, parser, decoder, *converters)
        for element in elements:
            pipeline.add(element)
        chain = [queue, parser, decoder, *converters]
        if self._fps_sink is not None:
            chain.append(self._fps_sink)
        for upstream, downstream in zip(chain, chain[1:]):
            if not upstream.link(downstream):
                self._fail(
                    f"cannot link {upstream.get_name()} to {downstream.get_name()}"
                )
                return
        provider_pad = queue.get_static_pad("sink")
        if provider_pad is None or pad.link(provider_pad) != Gst.PadLinkReturn.OK:
            self._fail("cannot link demuxer video pad to the video queue")
            return
        for element in elements:
            # Branches are added while the pipeline is already changing state
            # (pads appear asynchronously), so each new element must follow the
            # parent target state explicitly.
            element.sync_state_with_parent()

        self._decoder_name = decoder_factory
        self._converter_name = converters[-1].get_name() if converters else None
        if self._fps_sink is not None:
            fps_pad = self._fps_sink.get_static_pad("sink")
            if fps_pad is not None:
                fps_pad.add_probe(Gst.PadProbeType.BUFFER, self._on_fps_buffer)
        self._start_detection(decoder)
        info: dict[str, object] = {
            "codec": caps_name.split("/")[-1].upper(),
            "decoder": decoder_factory,
        }
        for key in ("width", "height"):
            value = structure.get_value(key)
            if isinstance(value, int) and value > 0:
                info[key] = value
        framerate_ok, numerator, denominator = structure.get_fraction("framerate")
        if framerate_ok and denominator:
            info["fps"] = round(numerator / denominator, 2)
        self.video_info = info
        self._dispatch(self.on_video_info, dict(info))

    def _prepare_detection(self) -> None:
        """Load the detector before playback so the first frame is not delayed."""
        if not self._config.detection.enabled or self._detection_failed:
            return
        if self._tap is None:
            self._tap = FaceDetectionTap(
                self._config,
                self._on_detection_result,
                on_error=self._on_detection_error,
            )
        try:
            self._tap.prepare()
        except Exception as exc:  # a missing model must not stop playback
            self._detection_failed = True
            self._dispatch(self.on_detection_error, f"face detection unavailable: {exc}")

    def _start_detection(self, decoder: Gst.Element) -> None:
        """Attach the sampled NPU detector to the decoder output (M2)."""
        if not self._config.detection.enabled or self._detection_failed:
            return
        pad = decoder.get_static_pad("src")
        if pad is None:
            return
        if self._tap is None:
            self._prepare_detection()
        if self._tap is None or self._detection_failed:
            return
        try:
            self._tap.start(pad)
        except Exception as exc:  # pragma: no cover - model load errors handled above
            self._dispatch(self.on_detection_error, f"face detection unavailable: {exc}")

    def _stop_detection(self) -> None:
        tap, self._tap = self._tap, None
        if tap is not None:
            tap.stop()

    def _on_fps_buffer(
        self, _pad: Gst.Pad, info: Gst.PadProbeInfo
    ) -> Gst.PadProbeReturn:
        buffer = info.get_buffer()
        if buffer is not None:
            pts = buffer.pts
            if pts is not None and pts >= 0 and pts != Gst.CLOCK_TIME_NONE:
                self._frame_times.append(pts / Gst.SECOND)
            else:
                self._frame_times.append(time.monotonic())
        return Gst.PadProbeReturn.OK

    def measured_fps(self, window_seconds: float = 1.5) -> float:
        """Delivered frame rate over the trailing window (instantaneous fps)."""
        if len(self._frame_times) < 2:
            return 0.0
        last = self._frame_times[-1]
        start = last - window_seconds
        recent = [t for t in self._frame_times if t >= start]
        if len(recent) < 2:
            return 0.0
        span = recent[-1] - recent[0]
        return round((len(recent) - 1) / span, 1) if span > 0 else 0.0

    def _on_detection_result(self, result: DetectionResult) -> None:
        self._dispatch(self.on_detection, result)

    def _on_detection_error(self, message: str) -> None:
        self._dispatch(self.on_detection_error, message)

    def _build_audio_branch(self, pad: Gst.Pad) -> None:
        pipeline = self._pipeline
        if pipeline is None:
            return
        try:
            queue = _make("queue", "audio-queue")
            decode = _make("decodebin", "audio-decode")
            convert = _make("audioconvert", "audio-convert")
            resample = _make("audioresample", "audio-resample")
            volume = _make("volume", "audio-volume")
            output = self._choose_audio_sink()
        except PlaybackError as exc:
            self._fail(str(exc))
            return
        volume.set_property("volume", self._config.player.volume / 100.0)
        for element in (queue, decode, convert, resample, volume, output):
            pipeline.add(element)
        if not queue.link(decode):
            self._fail("cannot link audio queue to decodebin")
            return
        for upstream, downstream in (
            (convert, resample),
            (resample, volume),
            (volume, output),
        ):
            if not upstream.link(downstream):
                self._fail(
                    f"cannot link {upstream.get_name()} to {downstream.get_name()}"
                )
                return
        convert_pad = convert.get_static_pad("sink")
        if convert_pad is None:
            return
        decode.connect("pad-added", self._link_decoded_audio, convert_pad)
        queue_pad = queue.get_static_pad("sink")
        if queue_pad is None or pad.link(queue_pad) != Gst.PadLinkReturn.OK:
            self._fail("cannot link audio demuxer pad to audio queue")
            return
        for element in (queue, decode, convert, resample, volume, output):
            # See _build_video_branch: late branches must sync with the parent.
            element.sync_state_with_parent()
        self._volume_element = volume

    @staticmethod
    def _link_decoded_audio(
        _decode: Gst.Element, pad: Gst.Pad, convert_pad: Gst.Pad
    ) -> None:
        if pad.get_current_caps() is None:
            return
        if pad.link(convert_pad) != Gst.PadLinkReturn.OK:
            return

    @staticmethod
    def _choose_audio_sink() -> Gst.Element:
        for factory in ("autoaudiosink", "pulsesink", "fakesink"):
            element = Gst.ElementFactory.make(factory, "audio-sink")
            if element is not None:
                return element
        raise PlaybackError("no usable audio sink (autoaudiosink/pulsesink/fakesink)")

    # -- bus / signals -----------------------------------------------------

    def _on_bus_message(self, _bus: Gst.Bus, message: Gst.Message) -> None:
        message_type = message.type
        if message_type == Gst.MessageType.EOS:
            self._playing = False
            self._dispatch(self.on_eos)
        elif message_type == Gst.MessageType.ERROR:
            error, debug = message.parse_error()
            detail = f"{error.message}"
            if debug:
                detail = f"{detail} ({debug.splitlines()[0]})"
            self._dispatch(self.on_error, detail)
        elif message_type == Gst.MessageType.STATE_CHANGED and message.src is self._pipeline:
            _old, new, _pending = message.parse_state_changed()
            if new == Gst.State.PLAYING:
                self._playing = True
                self._dispatch(self.on_state, "playing")
            elif new == Gst.State.PAUSED:
                self._playing = False
                self._dispatch(self.on_state, "paused")

    def _on_fps_measurements(
        self, _sink: Gst.Element, _fps: float, _droprate: float, average_fps: float
    ) -> None:
        self._last_fps = round(float(average_fps), 1)
        self._dispatch(self.on_fps, self._last_fps)

    # -- helpers -----------------------------------------------------------

    @staticmethod
    def _dispatch(callback: Callable | None, *args: object) -> None:
        if callback is not None:
            GLib.idle_add(callback, *args)

    def _fail(self, message: str) -> None:
        self._dispatch(self.on_error, message)
