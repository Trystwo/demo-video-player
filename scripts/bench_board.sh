#!/bin/bash
# Board-side conversion benchmark matrix (GStreamer 1.26, RK3576).
export XDG_RUNTIME_DIR=/run/user/1000
export WAYLAND_DISPLAY=wayland-0
SAMPLE=/tmp/m1_sample.mp4

run() {
  local name="$1"; shift
  local s e rc
  s=$(date +%s.%N)
  timeout 60 gst-launch-1.0 -q filesrc location="$SAMPLE" ! qtdemux name=d d.video_0 ! queue ! h264parse ! mppvideodec "$@" >/dev/null 2>/tmp/bench_err.txt
  rc=$?
  e=$(date +%s.%N)
  echo "$name rc=$rc wall=$(echo "$e - $s" | bc)"
  grep -m1 -E "ERROR|not-negotiated|WARNING" /tmp/bench_err.txt && sed -n '1,2p' /tmp/bench_err.txt
}

# decode only (baseline)
run G0_decode_only ! fakesink sync=false
# force decoder output into system memory NV12 (cost of dmabuf->cpu copy)
run G1_sysmem_nv12_fake caps='video/x-raw(memory:SystemMemory),format=NV12' ! fakesink sync=false
# forced system memory + videoconvert to RGBA + fakesink (isolate conversion)
run G2_sysmem_convRGBA_fake caps='video/x-raw(memory:SystemMemory),format=NV12' ! videoconvert ! 'video/x-raw,format=RGBA' ! fakesink sync=false
# dmabuf NV12 + videoconvert RGBA + fakesink (current player path)
run G3_dmabuf_convRGBA_fake ! videoconvert ! 'video/x-raw,format=RGBA' ! fakesink sync=false
# dmabuf NV12 + videoconvert n-threads=8 RGBA + fakesink
run G3b_dmabuf_convRGBA_mt ! videoconvert n-threads=8 ! 'video/x-raw,format=RGBA' ! fakesink sync=false
# GPU path: glupload + glcolorconvert -> GLMemory RGBA -> fakesink
run G4_gl_fake ! glupload ! glcolorconvert ! 'video/x-raw(memory:GLMemory),format=RGBA' ! fakesink sync=false
# GPU path -> gtk4paintablesink
run G5_gl_gtk ! glupload ! glcolorconvert ! 'video/x-raw(memory:GLMemory),format=RGBA' ! gtk4paintablesink sync=false
# plain conversion to RGB (24-bit) instead of RGBA
run G6_convRGB_gtk ! videoconvert ! 'video/x-raw,format=RGB' ! gtk4paintablesink sync=false
