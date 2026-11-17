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
    SAMPLE=path       override the sample clip
    BEAUTY=0|1|2|3    GPU beauty cost breakdown (CONV=gl only), see below
    SCALE=WxH         insert glcolorscale before the sink (CONV=gl only),
                      e.g. SCALE=1280x720 to pre-shrink the texture
    CPUONLY=1         skip the window/presenter (pipeline-only throughput)

``BEAUTY`` inserts a ``glshader`` after the GL converter to separate the cost of
the element from the cost of the effect:

    0  no shader (baseline chain)
    1  full beauty fragment shader (two synthetic faces, strengths 60/40)
    2  beauty shader with ``u_active = 0`` (compiled, but early pass-through)
    3  trivial copy shader (element overhead only)

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
BEAUTY = os.environ.get("BEAUTY", "0")
CPUONLY = os.environ.get("CPUONLY", "0") == "1"
SCALE = os.environ.get("SCALE", "")

COPY_SHADER = """
precision highp float;
varying vec2 v_texcoord;
uniform sampler2D tex;
void main() {
    gl_FragColor = texture2D(tex, v_texcoord);
}
"""

SAMPLE = os.environ.get("SAMPLE") or (
    "/tmp/m1_sample.mp4" if SIZE == "1080" else "/tmp/m1_sample_720.mp4"
)

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


def _build_beauty_element():
    """glshader element with the real beauty shader (or copy/disabled variants)."""
    src_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src")
    if src_dir not in sys.path:
        sys.path.insert(0, src_dir)
    from video_player.backend import _uniform_structure  # noqa: PLC0415
    from video_player.beauty import BEAUTY_FRAGMENT_SHADER, BeautyParams  # noqa: PLC0415
    from video_player.config import BeautyConfig  # noqa: PLC0415

    shader = Gst.ElementFactory.make("glshader", "beauty")
    if shader is None:
        print("glshader missing", flush=True)
        return None
    shader.set_property("fragment", COPY_SHADER if BEAUTY == "3" else BEAUTY_FRAGMENT_SHADER)
    frame = (1280, 720) if SIZE == "720" else (1920, 1080)
    values = BeautyParams.from_config(BeautyConfig()).uniforms(
        frame_size=frame,
        texture_size=frame,
        faces=[(0.22, 0.12, 0.30, 0.48), (0.58, 0.18, 0.22, 0.34)],
        active=BEAUTY != "2",
    )
    shader.set_property("uniforms", _uniform_structure(values))

    def on_sink(_pad, _info):
        state["beauty_start"] = time.perf_counter()
        return Gst.PadProbeReturn.OK

    def on_src(_pad, _info):
        started = state.get("beauty_start", 0.0)
        if started:
            elapsed = (time.perf_counter() - started) * 1000.0
            state.setdefault("beauty_ms", []).append(elapsed)
            del state["beauty_ms"][:-120]
        return Gst.PadProbeReturn.OK

    sink_pad = shader.get_static_pad("sink")
    src_pad = shader.get_static_pad("src")
    if sink_pad is not None:
        sink_pad.add_probe(Gst.PadProbeType.BUFFER, on_sink)
    if src_pad is not None:
        src_pad.add_probe(Gst.PadProbeType.BUFFER, on_src)
    return shader


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
        if SCALE:
            sw, sh = SCALE.lower().split("x")
            glscale = Gst.ElementFactory.make("glcolorscale", "glscale")
            glscalecaps = Gst.ElementFactory.make("capsfilter", "glscalecaps")
            glscalecaps.set_property(
                "caps",
                Gst.Caps.from_string(
                    "video/x-raw(memory:GLMemory),format=RGBA,"
                    f"width={sw},height={sh},texture-target=2D"
                ),
            )
            convs.extend([glscale, glscalecaps])
    if BEAUTY != "0":
        if CONV != "gl":
            print("BEAUTY needs CONV=gl", flush=True)
        else:
            shader = _build_beauty_element()
            if shader is not None:
                convs.append(shader)
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
    samples = state.get("beauty_ms") or []
    beauty = (
        f" beauty_ms={sum(samples) / len(samples):5.2f}" if samples else ""
    )
    print(
        f"t={now - state['start']:5.1f}s buffers/s={(b - last_b) / max(wall_delta, 1e-6):5.1f} "
        f"frames_total={b:4d} gtksnap/s={(s - last_s) / max(wall_delta, 1e-6):5.1f} "
        f"clock_ticks/s={ticks:3d} pos={pos:5.2f}s wall={now - state['start']:5.2f}s "
        f"cpu={(cpu_delta / max(wall_delta, 1e-6)) * 100:5.0f}%{beauty}",
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
            f"beauty={BEAUTY} "
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
