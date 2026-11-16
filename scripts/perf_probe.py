#!/usr/bin/env python3
"""Board-side performance probe: measure real frame delivery into the GTK sink.

Run on the board (Wayland session env required), e.g.:

    XDG_RUNTIME_DIR=/run/user/1000 WAYLAND_DISPLAY=wayland-0 \
        python3 perf_probe.py

Env knobs:
    SIZE=1080|720     sample resolution
    WIDTH/HEIGHT      window size (default 1280x800)
    FULL=1|0          fullscreen window
    SHOW=1|0          present window or keep it hidden (no GTK painting)
    SYNC=1|0          sink sync property
    WRAP=1|0          wrap sink in fpsdisplaysink (with sync propagated)
    DURATION=8        measurement seconds

Prints, once per second: buffers entering the sink, sink fps, GTK snapshot
count, frame-clock tick count, pipeline position, wall clock, CPU seconds.
"""
import os
import resource
import sys
import time

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gst", "1.0")
from gi.repository import Gdk, GLib, Gst, Gtk  # noqa: E402

Gst.init(None)

SIZE = os.environ.get("SIZE", "1080")
WIDTH = int(os.environ.get("WIDTH", "1280"))
HEIGHT = int(os.environ.get("HEIGHT", "800"))
FULL = os.environ.get("FULL", "1") == "1"
SHOW = os.environ.get("SHOW", "1") == "1"
SYNC = os.environ.get("SYNC", "1") == "1"
WRAP = os.environ.get("WRAP", "0") == "1"
DURATION = float(os.environ.get("DURATION", "8"))
CONV = os.environ.get("CONV", "sw")

SAMPLE = "/tmp/m1_sample.mp4" if SIZE == "1080" else "/tmp/m1_sample_720.mp4"

state = {
    "buffers": 0,
    "snapshots": 0,
    "ticks": 0,
    "start": 0.0,
    "last": (0, 0, 0.0, 0.0),
    "pipeline": None,
    "sink": None,
    "app": None,
}


def cpu_seconds():
    r = resource.getrusage(resource.RUSAGE_SELF)
    return r.ru_utime + r.ru_stime


class CountingPicture(Gtk.Picture):
    def do_snapshot(self, snapshot):
        state["snapshots"] += 1
        Gtk.Picture.do_snapshot(self, snapshot)


def build_pipeline():
    pipeline = Gst.Pipeline.new("perf")
    src = Gst.ElementFactory.make("filesrc", "src")
    src.set_property("location", SAMPLE)
    demux = Gst.ElementFactory.make("qtdemux", "demux")
    queue = Gst.ElementFactory.make("queue", "vq")
    parser = Gst.ElementFactory.make("h264parse", "parser")
    dec = Gst.ElementFactory.make("mppvideodec", "dec")
    conv = Gst.ElementFactory.make("videoconvert", "conv")
    caps = Gst.ElementFactory.make("capsfilter", "caps")
    caps.set_property("caps", Gst.Caps.from_string("video/x-raw,format=RGBA"))
    convs = [conv, caps]
    if CONV == "gl":
        glupload = Gst.ElementFactory.make("glupload", "glupload")
        glcolorconvert = Gst.ElementFactory.make("glcolorconvert", "glcolorconvert")
        glcaps = Gst.ElementFactory.make("capsfilter", "glcaps")
        glcaps.set_property(
            "caps",
            Gst.Caps.from_string("video/x-raw(memory:GLMemory),format=RGBA"),
        )
        convs = [glupload, glcolorconvert, glcaps]
    sink = Gst.ElementFactory.make("gtk4paintablesink", "sink")
    sink.set_property("sync", SYNC)

    elements = [src, demux, queue, parser, dec, *convs]
    if WRAP:
        fps_sink = Gst.ElementFactory.make("fpsdisplaysink", "fpssink")
        fps_sink.set_property("video-sink", sink)
        fps_sink.set_property("text-overlay", False)
        fps_sink.set_property("sync", SYNC)
        fps_sink.connect(
            "fps-measurements",
            lambda s, fps, _d, _a: print(f"  [fpsdisplaysink] fps={fps:.1f}", flush=True),
        )
        elements.append(fps_sink)
    else:
        elements.append(sink)
    state["sink"] = sink

    for element in elements:
        pipeline.add(element)
    src.link(demux)
    chain = [queue, parser, dec, *convs, elements[-1]]
    for upstream, downstream in zip(chain, chain[1:]):
        if not upstream.link(downstream):
            print(f"LINK-FAILED {upstream.get_name()} -> {downstream.get_name()}", flush=True)

    probe_pad = sink.get_static_pad("sink")

    def on_buffer(_pad, _info):
        state["buffers"] += 1
        return Gst.PadProbeReturn.OK

    probe_pad.add_probe(Gst.PadProbeType.BUFFER, on_buffer)
    state["pipeline"] = pipeline
    return pipeline


def link_demux(pipeline, demux):
    def on_pad_added(_demux, pad):
        if pad.get_name().startswith("video"):
            queue = pipeline.get_by_name("vq")
            pad.link(queue.get_static_pad("sink"))

    demux.connect("pad-added", on_pad_added)


def query_position(pipeline):
    try:
        ok, pos = pipeline.query_position(Gst.Format.TIME)
        return pos / Gst.SECOND if ok else 0.0
    except Exception:
        return 0.0


def tick():
    now = time.monotonic()
    last_b, last_s, last_w, last_c = state["last"]
    b, s = state["buffers"], state["snapshots"]
    wall_delta = now - last_w
    cpu_delta = cpu_seconds() - last_c
    pos = query_position(state["pipeline"])
    ticks = state["ticks"]
    print(
        f"t={now - state['start']:5.1f}s buffers/s={(b - last_b) / max(wall_delta, 1e-6):5.1f} "
        f"frames_total={b:4d} gtksnap/s={(s - last_s) / max(wall_delta, 1e-6):5.1f} "
        f"clock_ticks/s={ticks:3d} pos={pos:5.2f}s wall={now - state['start']:5.2f}s "
        f"cpu={(cpu_delta / max(wall_delta, 1e-6)) * 100:5.0f}%",
        flush=True,
    )
    state["last"] = (b, s, now, cpu_seconds())
    state["ticks"] = 0
    return True


def on_activate(app):
    pipeline = build_pipeline()
    demux = pipeline.get_by_name("demux")
    link_demux(pipeline, demux)

    paintable = state["sink"].get_property("paintable")
    picture = CountingPicture()
    picture.set_paintable(paintable)
    picture.set_hexpand(True)
    picture.set_vexpand(True)

    window = Gtk.ApplicationWindow(application=app)
    window.set_default_size(WIDTH, HEIGHT)
    window.set_child(picture)
    window.set_title(f"perf {SIZE}p sync={SYNC} wrap={WRAP} show={SHOW}")
    if FULL:
        window.fullscreen()
    if SHOW:
        window.present()
    else:
        window.set_visible(False)

    clock = window.get_frame_clock()

    def on_clock_tick(_clock):
        state["ticks"] += 1
        return True

    if clock is not None:
        try:
            clock.add_tick_callback(on_clock_tick)
        except AttributeError:
            try:
                clock.connect("update", on_clock_tick)
            except Exception:
                pass

    pipeline.set_state(Gst.State.PLAYING)
    state["start"] = time.monotonic()
    state["last"] = (0, 0, state["start"], cpu_seconds())
    GLib.timeout_add_seconds(1, tick)

    def finish():
        print(
            f"FINAL size={SIZE}p full={FULL} show={SHOW} sync={SYNC} wrap={WRAP} conv={CONV} "
            f"buffers={state['buffers']} gtksnap={state['snapshots']} "
            f"wall={time.monotonic() - state['start']:.2f}s",
            flush=True,
        )
        pipeline.set_state(Gst.State.NULL)
        app.quit()

    GLib.timeout_add(int(DURATION * 1000), finish)


def main():
    app = Gtk.Application(application_id=f"org.quectel.perfprobe{os.getpid()}")
    app.connect("activate", on_activate)
    sys.exit(app.run([]))


if __name__ == "__main__":
    main()
