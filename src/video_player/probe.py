"""GStreamer probing: file metadata and library thumbnails.

The library page needs two things the data layer must not do itself: durations
and resolutions (a synchronous ``GstPbutils.Discoverer`` run) and one poster
frame per card (a ``playbin`` preroll pulled from an ``appsink``).  Both decode
video, so both run on a worker thread and deliver results through
``GLib.idle_add``; the GTK main thread only ever swaps a cached PNG or a
metadata record into a card.

Why not the asynchronous ``discover_uri_async`` API?  On this stack its
``discovered`` signal never fires (the ``GstDiscovererInfo`` argument does not
marshal through PyGObject here), while the synchronous ``discover_uri`` works
and is trivially wrapped in a thread.  See ``docs/PITFALLS.md``.

Everything here imports GStreamer lazily, so ``import video_player.probe`` works
on a host without GI (the tests skip the parts that need it).
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
import threading
from typing import Callable, Iterable, Sequence

from .library import VideoMetadata

__all__ = [
    "DEFAULT_THUMB_WIDTH",
    "MetadataWorker",
    "ThumbnailWorker",
    "default_thumbnail_dir",
    "extract_thumbnail",
    "probe_metadata",
    "thumbnail_cache_path",
]

#: Posters are generated at 2x the 273 px card and letterboxed by the UI.
DEFAULT_THUMB_WIDTH = 546

#: Pull one frame a little into the clip: the very first frame of a fade-in
#: demo clip is black, which makes every card look empty.
_SEEK_FRACTION = 0.1


def default_thumbnail_dir() -> Path:
    """XDG cache folder for generated posters."""
    base = os.environ.get("XDG_CACHE_HOME")
    root = Path(base) if base else Path.home() / ".cache"
    return root / "video-player-demo" / "thumbs"


def thumbnail_cache_path(
    path: Path | str,
    size: int,
    mtime: float,
    *,
    width: int = DEFAULT_THUMB_WIDTH,
    directory: Path | str | None = None,
) -> Path:
    """Stable cache path for one file version's poster."""
    key = f"{path}|{int(size)}|{float(mtime):.6f}|{int(width)}"
    digest = hashlib.sha1(key.encode("utf-8")).hexdigest()
    target = Path(directory) if directory is not None else default_thumbnail_dir()
    return target / f"{digest}.png"


# -- metadata ---------------------------------------------------------------


def probe_metadata(
    path: Path | str,
    *,
    timeout_s: float = 5.0,
    discoverer: object | None = None,
) -> VideoMetadata | None:
    """Read duration / resolution / fps / codec, or None when unreadable.

    ``discoverer`` lets a worker reuse one ``GstPbutils.Discoverer`` across
    files; pass None for a one-off probe (tests, tools).
    """
    uri = _file_uri(path)
    if uri is None:
        return None
    try:
        if discoverer is None:
            discoverer = _new_discoverer(timeout_s)
        info = discoverer.discover_uri(uri)
    except Exception:  # noqa: BLE001 - a bad file must never break the grid
        return None
    if info is None:
        return None
    try:
        duration = max(0, int(info.get_duration()))
        width = height = 0
        fps = 0.0
        codec = ""
        streams = info.get_video_streams() or ()
        if streams:
            stream = streams[0]
            width = max(0, int(stream.get_width()))
            height = max(0, int(stream.get_height()))
            denom = int(stream.get_framerate_denom()) or 1
            fps = float(stream.get_framerate_num()) / float(denom)
            codec = _codec_from_stream(stream)
    except Exception:  # noqa: BLE001
        return None
    if duration == 0 and width == 0 and height == 0:
        return None
    return VideoMetadata(
        duration_ns=duration,
        width=width,
        height=height,
        fps=round(fps, 3),
        codec=codec,
    )


def _new_discoverer(timeout_s: float) -> object:
    import gi

    gi.require_version("Gst", "1.0")
    gi.require_version("GstPbutils", "1.0")
    from gi.repository import Gst, GstPbutils

    Gst.init(None)
    return GstPbutils.Discoverer.new(max(1, int(timeout_s * Gst.SECOND)))


def _codec_from_stream(stream: object) -> str:
    try:
        caps = stream.get_caps()
        structure = caps.get_structure(0)
        name = str(structure.get_name())  # e.g. video/x-h264
    except Exception:  # noqa: BLE001
        return ""
    if "/" in name:
        name = name.split("/", 1)[1]
    # GStreamer spells the codec caps as ``video/x-h264`` / ``video/x-vp9``; the
    # leading ``x-`` is the "non-standardised fourcc" marker, not the codec.
    if name.startswith("x-"):
        name = name[2:]
    return name.lower()


# -- thumbnails -------------------------------------------------------------


def extract_thumbnail(
    path: Path | str,
    *,
    width: int = DEFAULT_THUMB_WIDTH,
    timeout_s: float = 8.0,
) -> bytes | None:
    """Render one NTSC-safe frame to PNG bytes, or None when unreadable."""
    import gi

    gi.require_version("Gst", "1.0")
    gi.require_version("GstVideo", "1.0")
    gi.require_version("GdkPixbuf", "2.0")
    from gi.repository import GdkPixbuf, GLib, Gst, GstVideo

    Gst.init(None)
    uri = _file_uri(path)
    if uri is None:
        return None
    play = Gst.ElementFactory.make("playbin", "thumb-play")
    sink = Gst.ElementFactory.make("appsink", "thumb-sink")
    pipeline = Gst.Pipeline.new("thumb-pipeline")
    if play is None or sink is None or pipeline is None:
        return None
    sink.set_property("sync", False)
    sink.set_property("max-buffers", 1)
    sink.set_property("caps", _thumbnail_caps(Gst, width))
    play.set_property("uri", uri)
    play.set_property("video-sink", sink)
    pipeline.add(play)
    try:
        pipeline.set_state(Gst.State.PAUSED)
        if not _wait_for_preroll(Gst, pipeline, timeout_s):
            return None
        duration_ok, duration = play.query_duration(Gst.Format.TIME)
        if duration_ok and duration > 0:
            play.seek_simple(
                Gst.Format.TIME,
                Gst.SeekFlags.FLUSH | Gst.SeekFlags.KEY_UNIT,
                int(duration * _SEEK_FRACTION),
            )
            _wait_for_preroll(Gst, pipeline, timeout_s)
        sample = sink.emit("try-pull-preroll", max(1, int(timeout_s)) * Gst.SECOND)
        if sample is None:
            sample = sink.emit("try-pull-sample", 2 * Gst.SECOND)
        if sample is None:
            return None
        return _sample_to_png(GdkPixbuf, GLib, Gst, GstVideo, sample)
    finally:
        pipeline.set_state(Gst.State.NULL)


def _thumbnail_caps(Gst: object, width: int) -> object:
    return Gst.Caps.from_string(
        f"video/x-raw,format=RGBA,width={max(64, int(width))},"
        "pixel-aspect-ratio=1/1"
    )


def _wait_for_preroll(Gst: object, pipeline: object, timeout_s: float) -> bool:
    """Poll the pipeline bus until preroll completes, the file errors or time is up."""
    import time

    deadline = time.monotonic() + max(1.0, timeout_s)
    bus = pipeline.get_bus()
    while time.monotonic() < deadline:
        message = bus.timed_pop_filtered(
            100 * Gst.MSECOND,
            Gst.MessageType.ASYNC_DONE | Gst.MessageType.ERROR | Gst.MessageType.EOS,
        )
        if message is None:
            continue
        if message.type == Gst.MessageType.ERROR:
            return False
        return True
    return False


def _sample_to_png(
    GdkPixbuf: object,
    GLib: object,
    Gst: object,
    GstVideo: object,
    sample: object,
) -> bytes | None:
    """Copy the RGBA sample honouring its row stride and encode PNG."""
    buffer = sample.get_buffer()
    caps = sample.get_caps()
    if buffer is None or caps is None:
        return None
    try:
        info = GstVideo.VideoInfo.new_from_caps(caps)
        width, height = int(info.width), int(info.height)
        strides = info.stride
        stride = int(strides[0]) if hasattr(strides, "__getitem__") else int(strides)
    except Exception:  # noqa: BLE001
        return None
    if width <= 0 or height <= 0 or stride < width * 4:
        return None
    mapped, mapinfo = buffer.map(Gst.MapFlags.READ)
    if not mapped:
        return None
    try:
        data = bytes(mapinfo.data)
    finally:
        buffer.unmap(mapinfo)
    if len(data) < stride * height:
        return None
    try:
        pixbuf = GdkPixbuf.Pixbuf.new_from_bytes(
            GLib.Bytes.new(data),
            GdkPixbuf.Colorspace.RGB,
            True,
            8,
            width,
            height,
            stride,
        )
        ok, payload = pixbuf.save_to_bufferv("png", [], [])
    except Exception:  # noqa: BLE001
        return None
    return bytes(payload) if ok else None


# -- workers ----------------------------------------------------------------


class ThumbRequest:
    """One poster job: which file version and where its PNG belongs."""

    __slots__ = ("path", "cache_path")

    def __init__(self, path: Path | str, cache_path: Path | str) -> None:
        self.path = Path(path)
        self.cache_path = Path(cache_path)


class _Worker:
    """One background thread feeding ``GLib.idle_add`` callbacks.

    A worker can be restarted (the library page rescans on every visit); the
    generation counter makes callbacks from a superseded run no-ops instead of
    painting stale metadata into the new grid.
    """

    def __init__(self, name: str) -> None:
        self._name = name
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._closed = False
        self._generation = 0

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def stop(self) -> None:
        self._closed = True
        self._stop.set()

    def cancel(self) -> None:
        """Stop the current run but keep the worker reusable (page switch)."""
        self._stop.set()
        self._generation += 1

    def _launch(self, target: Callable[[int], None]) -> int:
        self._stop.clear()
        self._generation += 1
        generation = self._generation
        self._thread = threading.Thread(
            target=target,
            args=(generation,),
            name=self._name,
            daemon=True,
        )
        self._thread.start()
        return generation

    def _alive(self, generation: int) -> bool:
        return not self._closed and generation == self._generation and not self._stop.is_set()

    def _deliver(self, generation: int, callback: Callable[[], None]) -> bool:
        if self._alive(generation):
            callback()
        return False


class MetadataWorker(_Worker):
    """Sequential metadata probing; each result is delivered on the main loop."""

    def __init__(
        self,
        on_result: Callable[[Path, VideoMetadata | None], None],
        *,
        timeout_s: float = 5.0,
    ) -> None:
        super().__init__("library-metadata")
        self._on_result = on_result
        self._timeout_s = timeout_s
        self._pending = 0

    @property
    def pending(self) -> int:
        return self._pending

    def start(self, paths: Iterable[Path]) -> None:
        """Probe *paths* in order, replacing any run that is still in flight."""
        queue = tuple(Path(path) for path in paths)
        self._pending = len(queue)
        if not queue:
            return
        self._launch(lambda generation: self._run(generation, queue))

    def _run(self, generation: int, queue: tuple[Path, ...]) -> None:
        import gi

        gi.require_version("Gst", "1.0")
        gi.require_version("GstPbutils", "1.0")
        gi.require_version("GLib", "2.0")
        from gi.repository import GLib

        discoverer = None
        try:
            discoverer = _new_discoverer(self._timeout_s)
        except Exception:  # noqa: BLE001 - per-file discoverers still work
            discoverer = None
        for path in queue:
            if not self._alive(generation):
                break
            metadata = probe_metadata(
                path, timeout_s=self._timeout_s, discoverer=discoverer
            )
            self._pending = max(0, self._pending - 1)
            if not self._alive(generation):
                break
            GLib.idle_add(self._emit, generation, path, metadata)

    def _emit(
        self, generation: int, path: Path, metadata: VideoMetadata | None
    ) -> bool:
        return self._deliver(generation, lambda: self._on_result(path, metadata))


class ThumbnailWorker(_Worker):
    """Sequential poster extraction; results are cached PNG paths."""

    def __init__(
        self,
        on_result: Callable[[Path, Path | None], None],
        *,
        width: int = DEFAULT_THUMB_WIDTH,
        cache_dir: Path | str | None = None,
        timeout_s: float = 8.0,
    ) -> None:
        super().__init__("library-thumbs")
        self._on_result = on_result
        self._width = int(width)
        self._cache_dir = (
            Path(cache_dir) if cache_dir is not None else default_thumbnail_dir()
        )
        self._timeout_s = timeout_s
        self._pending = 0

    @property
    def pending(self) -> int:
        return self._pending

    def start(self, requests: Sequence[ThumbRequest]) -> None:
        queue = tuple(requests)
        self._pending = len(queue)
        if not queue:
            return
        self._launch(lambda generation: self._run(generation, queue))

    def _run(self, generation: int, queue: tuple[ThumbRequest, ...]) -> None:
        import gi

        gi.require_version("GLib", "2.0")
        from gi.repository import GLib

        for request in queue:
            if not self._alive(generation):
                break
            target = request.cache_path
            payload: Path | None = target if target.is_file() else None
            if payload is None:
                png = extract_thumbnail(
                    request.path, width=self._width, timeout_s=self._timeout_s
                )
                if png is not None and _write_atomic(target, png):
                    payload = target
            self._pending = max(0, self._pending - 1)
            if not self._alive(generation):
                break
            GLib.idle_add(self._emit, generation, request.path, payload)

    def _emit(self, generation: int, path: Path, payload: Path | None) -> bool:
        return self._deliver(generation, lambda: self._on_result(path, payload))


def _write_atomic(target: Path, payload: bytes) -> bool:
    tmp = target.with_suffix(target.suffix + ".tmp")
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_bytes(payload)
        os.replace(tmp, target)
    except OSError:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        return False
    return True


def _file_uri(path: Path | str) -> str | None:
    """``GLib.filename_to_uri`` without importing GLib at module scope."""
    try:
        import gi

        gi.require_version("GLib", "2.0")
        from gi.repository import GLib

        return str(GLib.filename_to_uri(str(Path(path).expanduser())))
    except Exception:  # noqa: BLE001
        return None


def hardware_decode_ready() -> bool:
    """True when the RK3576 VPU decoder element is registered (rail status)."""
    try:
        import gi

        gi.require_version("Gst", "1.0")
        from gi.repository import Gst

        Gst.init(None)
        return Gst.ElementFactory.find("mppvideodec") is not None
    except Exception:  # noqa: BLE001
        return False
