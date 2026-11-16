#!/usr/bin/env python3
"""Headless measurement of the sampled face-detection tap (board tool).

Runs the M1 playback pipeline through ``fakesink sync=true`` (real-time pacing)
and reports delivered fps plus detection latency over a steady-state window.

Usage::

    python3 scripts/measure_tap.py MEDIA [--no-detection] [--seconds 8]
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import gi

gi.require_version("Gst", "1.0")
from gi.repository import GLib, Gst  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from video_player.config import Config  # noqa: E402
from video_player.detection import FaceDetectionTap  # noqa: E402


def build_pipeline(media: str) -> Gst.Pipeline:
    return Gst.parse_launch(
        f'filesrc location="{media}" ! qtdemux ! h264parse ! mppvideodec name=dec '
        "! glupload ! glcolorconvert ! video/x-raw(memory:GLMemory),format=RGBA "
        "! fpsdisplaysink name=fps sync=true video-sink=fakesink"
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("media")
    parser.add_argument("--model", default=str(ROOT / "models" / "yunet_rk3576.rknn"))
    parser.add_argument("--no-detection", action="store_true")
    parser.add_argument("--seconds", type=float, default=8.0)
    parser.add_argument("--warmup", type=float, default=3.0)
    args = parser.parse_args()

    Gst.init(None)
    config = Config()
    config.detection.model_path = args.model
    config.detection.enabled = not args.no_detection

    pipeline = build_pipeline(args.media)
    decoder = pipeline.get_by_name("dec")
    assert decoder is not None
    fps_sink = pipeline.get_by_name("fps")

    counters = {"buffers": 0}
    detections: list[tuple[float, float, int]] = []

    def count_buffer(pad: Gst.Pad, info: Gst.PadProbeInfo) -> Gst.PadProbeReturn:
        counters["buffers"] += 1
        return Gst.PadProbeReturn.OK

    sink_pad = pipeline.get_by_name("fps").get_static_pad("sink")
    sink_pad.add_probe(Gst.PadProbeType.BUFFER, count_buffer)

    tap = None
    if not args.no_detection:
        tap = FaceDetectionTap(
            config,
            lambda result: detections.append(
                (time.monotonic(), result.inference_ms, len(result.faces))
            ),
            on_error=lambda message: print("DETECTION ERROR:", message),
        )
        tap.start(decoder.get_static_pad("src"))
        print(f"tap started: interval={tap.interval}")

    pipeline.set_state(Gst.State.PLAYING)
    loop = GLib.MainLoop()
    started = time.monotonic()
    mark = {"t": started, "buffers": 0}

    def tick() -> bool:
        now = time.monotonic()
        elapsed = now - started
        if elapsed >= args.warmup and mark["t"] == started:
            mark["t"] = now
            mark["buffers"] = counters["buffers"]
            print(f"steady window starts at {elapsed:.1f}s")
        if elapsed >= args.warmup + args.seconds:
            window = now - mark["t"]
            delivered = counters["buffers"] - mark["buffers"]
            print(
                f"buffers={delivered} window={window:.2f}s fps={delivered / window:.1f}"
            )
            if detections:
                recent = [d for d in detections if d[0] >= mark["t"]]
                if recent:
                    avg_ms = sum(d[1] for d in recent) / len(recent)
                    print(
                        f"detections={len(recent)} interval_fps={len(recent) / window:.1f} "
                        f"avg_ms={avg_ms:.1f} faces={[d[2] for d in recent]}"
                    )
            loop.quit()
            return False
        return True

    GLib.timeout_add(100, tick)
    pipeline.get_bus().add_signal_watch()
    pipeline.get_bus().connect(
        "message",
        lambda _bus, message: (
            print("ERROR:", message.parse_error()[1])
            if message.type == Gst.MessageType.ERROR
            else None
        ),
    )
    loop.run()
    pipeline.set_state(Gst.State.NULL)
    if tap is not None:
        tap.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
