#!/usr/bin/env python3
"""Feasibility probe: sample decoded NV12 frames, convert, run YuNet on NPU.

Measures the cost of the M2 detection tap without touching the player:
``filesrc ! qtdemux ! h264parse ! mppvideodec ! fakesink`` with a buffer probe on
the decoder source pad.  Every ``--interval`` frame the NV12 buffer is copied,
converted to BGR (OpenCV), resized to 640x640 and run through the RKNN model.

    python3 scripts/probe_face_pipeline.py /tmp/m2_face.mp4 --model models/yunet_rk3576.rknn
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

import gi  # noqa: E402

gi.require_version("Gst", "1.0")
from gi.repository import GLib, Gst  # noqa: E402

import cv2  # noqa: E402
import numpy as np  # noqa: E402

from video_player.face_yunet import INPUT_SIZE, decode_yunet_outputs, map_faces_to_frame  # noqa: E402
from video_player.rknn_runtime import RknnRuntime  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("video")
    parser.add_argument("--model", required=True)
    parser.add_argument("--interval", type=int, default=3)
    parser.add_argument("--max-frames", type=int, default=120, help="stop after this many buffers")
    args = parser.parse_args(argv)

    Gst.init(None)
    runtime = RknnRuntime(args.model)
    pipeline = Gst.parse_launch(
        f'filesrc location="{args.video}" ! qtdemux ! h264parse ! mppvideodec name=dec ! fakesink sync=false'
    )
    decoder = pipeline.get_by_name("dec")
    assert decoder is not None

    counters = {"buffers": 0, "detections": 0, "faces": 0, "map_ms": 0.0, "convert_ms": 0.0, "npu_ms": 0.0, "decode_ms": 0.0}
    started = time.perf_counter()

    def on_buffer(_pad: Gst.Pad, info: Gst.PadProbeInfo):
        buffer = info.get_buffer()
        if buffer is None:
            return Gst.PadProbeReturn.OK
        counters["buffers"] += 1
        if counters["buffers"] % args.interval:
            return Gst.PadProbeReturn.OK
        caps = _pad.get_current_caps()
        if caps is None:
            return Gst.PadProbeReturn.OK
        structure = caps.get_structure(0)
        width = structure.get_value("width")
        height = structure.get_value("height")
        t0 = time.perf_counter()
        data = buffer.extract_dup(0, buffer.get_size())
        counters["map_ms"] += (time.perf_counter() - t0) * 1000
        array = np.frombuffer(data, dtype=np.uint8)
        # NV12 buffers are padded to the decoder's stride, which is not exposed
        # in the caps; derive it from the buffer size (H*W*3/2 with stride).
        stride = array.size // (height * 3 // 2)
        if stride < width or array.size < stride * height * 3 // 2:
            return Gst.PadProbeReturn.OK
        y = array[: stride * height].reshape(height, stride)[:, :width]
        uv = array[stride * height : stride * height * 3 // 2].reshape(height // 2, stride)[:, :width]
        y = np.ascontiguousarray(y)
        uv = np.ascontiguousarray(uv).reshape(height // 2, width // 2, 2)
        t0 = time.perf_counter()
        bgr = cv2.cvtColorTwoPlane(y, uv, cv2.COLOR_YUV2BGR_NV12)
        model_input = cv2.resize(bgr, (INPUT_SIZE, INPUT_SIZE), interpolation=cv2.INTER_LINEAR)
        counters["convert_ms"] += (time.perf_counter() - t0) * 1000
        t0 = time.perf_counter()
        raw = runtime.infer(model_input)
        counters["npu_ms"] += (time.perf_counter() - t0) * 1000
        faces = map_faces_to_frame(decode_yunet_outputs(raw, score_threshold=0.5), width, height)
        counters["detections"] += 1
        counters["faces"] += len(faces)
        if counters["detections"] <= 5:
            print(f"  frame {counters['buffers']}: {len(faces)} face(s) {[(round(f.score, 2), round(f.x), round(f.y)) for f in faces]}")
        if counters["buffers"] >= args.max_frames:
            pipeline.set_state(Gst.State.NULL)
        return Gst.PadProbeReturn.OK

    decoder.get_static_pad("src").add_probe(Gst.PadProbeType.BUFFER, on_buffer)
    pipeline.set_state(Gst.State.PLAYING)
    bus = pipeline.get_bus()
    loop = GLib.MainLoop()
    bus.add_signal_watch()

    def on_message(_bus, message):
        if message.type in (Gst.MessageType.EOS, Gst.MessageType.ERROR):
            pipeline.set_state(Gst.State.NULL)
            loop.quit()

    bus.connect("message", on_message)
    GLib.timeout_add(30000, lambda: (loop.quit(), False)[1])
    loop.run()
    elapsed = time.perf_counter() - started
    detections = max(1, counters["detections"])
    print(
        f"buffers={counters['buffers']} detections={counters['detections']} faces={counters['faces']} "
        f"elapsed={elapsed:.2f}s"
    )
    print(
        f"per detection: map={counters['map_ms'] / detections:.2f} ms, "
        f"convert={counters['convert_ms'] / detections:.2f} ms, "
        f"npu={counters['npu_ms'] / detections:.2f} ms"
    )
    runtime.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
