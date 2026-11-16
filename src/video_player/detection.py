"""Sampled face detection on decoded frames (M2).

The decoder hands the video branch NV12 frames wrapped in DMABuf memory.
Copying one 1080p frame out of the buffer costs ~3 ms, and a YuNet inference
takes ~31 ms on the RK3576 NPU.  Running either in the GStreamer streaming
thread would stall playback, so this tap:

* copies every ``detection.interval_frames``-th frame in a cheap pad probe
  (at most one sample is queued; older samples are skipped, never buffered);
* converts NV12 -> BGR and runs the RKNN detector in a dedicated worker thread;
* delivers the result to the UI through the GTK main loop.

The decoding helpers live in :mod:`video_player.face_yunet`; the RKNN bindings
live in :mod:`video_player.rknn_runtime`.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from typing import Callable

import gi

gi.require_version("Gst", "1.0")
from gi.repository import GLib, Gst  # noqa: E402

from .config import Config
from .face import DetectionResult, RknnFaceDetector, resolve_model_path

__all__ = ["FaceDetectionTap", "nv12_to_bgr", "rotate_bgr", "rotation_from_tag"]

_QUEUE_WAIT_SECONDS = 0.5


def nv12_to_bgr(
    data: bytes, width: int, height: int, buffer_size: int | None = None
) -> "object":
    """Convert an NV12 frame with padded stride to a BGR ``numpy`` array.

    ``buffer_size`` is the GstBuffer size; the RK3576 decoder pads rows and
    the frame height, so both the stride and the plane offsets are inferred
    from the buffer size rather than assumed to be ``width``.
    """
    import numpy as np

    import cv2  # imported lazily: only needed when detection is enabled

    if width <= 0 or height <= 0 or height % 2:
        raise ValueError(f"invalid NV12 frame size {width}x{height}")
    size = buffer_size if buffer_size is not None else len(data)
    # The RK3576 decoder pads both the row stride (e.g. 1920 -> 1984) and the
    # frame height (e.g. 1080 -> 1088).  caps only carry width/height, so infer
    # the padded height by looking for the (h * 1.5) divisor of the buffer size.
    candidates: list[tuple[int, int, int]] = []  # (stride pad, height pad, stride)
    for candidate_height in range(height, height + 33, 2):
        plane = candidate_height * 3 // 2
        if size % plane:
            continue
        candidate_stride = size // plane
        if candidate_stride < width or candidate_stride * plane != size:
            continue
        # The Rockchip decoder aligns rows to 64 bytes and frames to 8 lines.
        if candidate_stride % 16 or candidate_height % 8:
            continue
        if candidate_stride - width > 256:
            continue
        candidates.append((candidate_stride - width, candidate_height - height, candidate_stride))
    if not candidates:
        raise ValueError(
            f"unexpected NV12 buffer layout: {size} bytes for {width}x{height}"
        )
    _, height_padding, stride = min(candidates)
    padded_height = height + height_padding
    array = np.frombuffer(data, dtype=np.uint8)
    y_plane = array[: stride * padded_height].reshape(padded_height, stride)[:height, :width]
    uv_offset = stride * padded_height
    uv_plane = (
        array[uv_offset : uv_offset + stride * (height // 2)]
        .reshape(height // 2, stride)[:, :width]
    )
    uv_plane = np.ascontiguousarray(uv_plane).reshape(height // 2, width // 2, 2)
    # pylint: disable-next=no-member  # cv2 stubs are incomplete
    return cv2.cvtColorTwoPlane(
        np.ascontiguousarray(y_plane), uv_plane, cv2.COLOR_YUV2BGR_NV12
    )


def rotation_from_tag(value: str) -> int:
    """Map a GStreamer ``image-orientation`` tag to clockwise degrees.

    ``qtdemux`` reports the MP4 display matrix as a tag; the RK3576 decoder
    ignores it, so decoded buffers arrive rotated while the sink rotates the
    picture for display.  Our detector must see the same upright frame as the
    viewer, otherwise boxes end up shifted (a 180 degree phone video used to
    detect only the upper half of a face).
    """
    return {"rotate-90": 90, "rotate-180": 180, "rotate-270": 270}.get(value, 0)


def rotate_bgr(frame, degrees: int):
    """Rotate a BGR frame clockwise by 0/90/180/270 degrees.

    ``degrees`` follows :func:`rotation_from_tag`; unknown angles are ignored.
    """
    degrees %= 360
    if not degrees:
        return frame
    import cv2  # imported lazily: only needed when detection is enabled

    method = {
        90: cv2.ROTATE_90_CLOCKWISE,
        180: cv2.ROTATE_180,
        270: cv2.ROTATE_90_COUNTERCLOCKWISE,
    }.get(degrees)
    if method is None:
        return frame
    return cv2.rotate(frame, method)


class FaceDetectionTap:
    """Sample decoded NV12 frames and run the RKNN detector off-thread."""

    def __init__(
        self,
        config: Config,
        on_result: Callable[[DetectionResult], None],
        *,
        on_error: Callable[[str], None] | None = None,
        rotation: Callable[[], int] | None = None,
    ) -> None:
        self._config = config
        self._on_result = on_result
        self._on_error = on_error
        # Returns the stream rotation (degrees clockwise) needed to make the
        # decoded frame upright; queried per sample because tags arrive late.
        self._rotation = rotation or (lambda: 0)
        self._detector: RknnFaceDetector | None = None
        self._thread: threading.Thread | None = None
        self._samples: deque[tuple[bytes, int, int, int, int]] = deque(maxlen=1)
        self._cond = threading.Condition()
        self._running = False
        self._pad: Gst.Pad | None = None
        self._probe_id: int = 0
        self._frame_index = 0
        self._interval = max(1, config.detection.interval_frames)

    # -- lifecycle ---------------------------------------------------------

    def prepare(self) -> None:
        """Load the model up front so playback is not delayed by the NPU.

        The window opens the media right after the pipeline is built; loading
        YuNet there would push the first frames back by ~2 s and drag down the
        cumulative fps counter.  ``start`` calls this again (idempotent).
        """
        if self._detector is not None:
            return
        model_path = resolve_model_path(self._config.detection.model_path)
        detector = RknnFaceDetector(
            str(model_path),
            score_threshold=self._config.detection.min_score,
            max_faces=self._config.detection.max_faces,
            core_mask=self._config.detection.core_mask,
        )
        detector.load()
        self._detector = detector

    def start(self, pad: Gst.Pad) -> None:
        """Attach the sample probe; the model is loaded by :meth:`prepare`."""
        self.prepare()
        self._running = True
        self._thread = threading.Thread(
            target=self._worker, name="face-detection", daemon=True
        )
        self._thread.start()
        self._pad = pad
        self._probe_id = pad.add_probe(Gst.PadProbeType.BUFFER, self._on_buffer)

    def stop(self) -> None:
        """Remove the probe, stop the worker and release the RKNN context."""
        with self._cond:
            self._running = False
            self._samples.clear()
            self._cond.notify_all()
        if self._pad is not None and self._probe_id:
            try:
                self._pad.remove_probe(self._probe_id)
            except Exception:  # pragma: no cover - probe already gone
                pass
        self._pad = None
        self._probe_id = 0
        if self._thread is not None:
            self._thread.join(timeout=3.0)
            self._thread = None
        if self._detector is not None:
            self._detector.close()
            self._detector = None

    @property
    def interval(self) -> int:
        return self._interval

    @property
    def running(self) -> bool:
        return self._running

    # -- GST callbacks -----------------------------------------------------

    def _on_buffer(self, pad: Gst.Pad, info: Gst.PadProbeInfo) -> Gst.PadProbeReturn:
        buffer = info.get_buffer()
        if buffer is None:
            return Gst.PadProbeReturn.OK
        self._frame_index += 1
        if self._frame_index % self._interval:
            return Gst.PadProbeReturn.OK
        with self._cond:
            if self._samples:
                # The worker has not consumed the previous sample yet; dropping
                # this one keeps latency bounded and the streaming thread free.
                return Gst.PadProbeReturn.OK
        caps = pad.get_current_caps()
        if caps is None or caps.get_size() == 0:
            return Gst.PadProbeReturn.OK
        structure = caps.get_structure(0)
        if str(structure.get_value("format")) != "NV12":
            return Gst.PadProbeReturn.OK
        width = int(structure.get_value("width"))
        height = int(structure.get_value("height"))
        try:
            data = buffer.extract_dup(0, buffer.get_size())
        except Exception:
            return Gst.PadProbeReturn.OK
        with self._cond:
            if not self._running:
                return Gst.PadProbeReturn.OK
            self._samples.append((data, width, height, buffer.get_size(), self._frame_index))
            self._cond.notify()
        return Gst.PadProbeReturn.OK

    # -- worker ------------------------------------------------------------

    def _worker(self) -> None:
        while True:
            with self._cond:
                while self._running and not self._samples:
                    self._cond.wait(_QUEUE_WAIT_SECONDS)
                if not self._running:
                    return
                data, width, height, size, frame_index = self._samples.popleft()
            started = time.perf_counter()
            try:
                bgr = nv12_to_bgr(data, width, height, size)
                degrees = int(self._rotation() or 0) % 360
                if degrees:
                    bgr = rotate_bgr(bgr, degrees)
                assert self._detector is not None
                faces = self._detector.detect(bgr)
            except Exception as exc:  # detector errors must not kill playback
                self._fail(f"face detection failed: {exc}")
                return
            result = DetectionResult(
                faces=tuple(faces),
                frame_index=frame_index,
                inference_ms=round((time.perf_counter() - started) * 1000, 1),
            )
            GLib.idle_add(self._emit_result, result)

    def _emit_result(self, result: DetectionResult) -> bool:
        if self._running and self._on_result is not None:
            self._on_result(result)
        return False

    def _fail(self, message: str) -> None:
        self.stop()
        if self._on_error is not None:
            GLib.idle_add(self._emit_error, message)

    def _emit_error(self, message: str) -> bool:
        self._on_error(message)  # type: ignore[misc]
        return False
