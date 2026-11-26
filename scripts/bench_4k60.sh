#!/usr/bin/env bash
# 4K60 playback benchmark for the Quectel Pi M2 (RK3576) video player.
# Run ON THE BOARD from a physical GUI session:
#   bash scripts/bench_4k60.sh
# Requires the 4K60 clips in /tmp/4k60 (see docs/BOARD.md) and the installed
# launcher video-player-demo. All logs land in /tmp/4k60/results/.
set -u

export XDG_RUNTIME_DIR=/run/user/1000
export WAYLAND_DISPLAY=wayland-0
export GST_GL_PLATFORM=egl
export VIDEO_PLAYER_PERF_LOG=1

DIR=/tmp/4k60
OUT=$DIR/results
mkdir -p "$OUT"
PLAYER=${PLAYER:-video-player-demo}

if [ ! -f "$DIR/4k60_h264_face.mp4" ]; then
  echo "missing 4K60 clips in $DIR - run the generator first" >&2
  exit 1
fi

python3 - "$OUT" <<'PY'
import copy, json, os, sys
out = sys.argv[1]
base = {
    "version": 1,
    "player": {
        "hw_decode": True,
        "render_sink": "gtk4paintablesink",
        "video_convert": "auto",
        "texture_max_dimension": 1280,
        "volume": 0,
        "speed": 1.0,
        "resume": False,
        "recent_files": False,
    },
    "beauty": {
        "enabled": True,
        "smoothing": 60,
        "whitening": 40,
        "face_slim": 0.0,
        "eye_enlarge": 0.0,
        "compare_mode": False,
        "feather_px": 24,
        "expand": 0.12,
        "skin_guard": True,
        "split": 0.5,
        "debug_mask": False,
    },
    "detection": {
        "enabled": True,
        "model_path": "models/yunet_rk3576.rknn",
        "interval_frames": 3,
        "max_faces": 3,
        "min_score": 0.5,
        "track_fill": True,
        "core_mask": 0,
        "hold_ms": 600,
    },
    "debug": {"show_info_panel": True, "fps_window": 60, "log_level": "info"},
}
cases = [
    ("A_default_1280_nodetect", 1280, False),
    ("B_full4k_nodetect", 0, False),
    ("C_1280_detect", 1280, True),
]
for name, dim, det in cases:
    cfg = copy.deepcopy(base)
    cfg["player"]["texture_max_dimension"] = dim
    cfg["detection"]["enabled"] = det
    with open(os.path.join(out, name + ".json"), "w") as fh:
        json.dump(cfg, fh, indent=2)
    print("config: " + name + "  texture_max_dimension=" + str(dim) + "  detection=" + str(det))
PY

CLIP=$DIR/4k60_h264_face.mp4
REPEAT="$CLIP $CLIP $CLIP"

for case in A_default_1280_nodetect B_full4k_nodetect C_1280_detect; do
  echo "=== CASE $case (H.264 face, 3x8s) ==="
  timeout 120 "$PLAYER" --demo \
      --config "$OUT/$case.json" \
      --snapshot "$OUT/shot_$case.png" --snapshot-delay 12 \
      $REPEAT > "$OUT/perf_$case.log" 2>&1
  echo "rc=$?"
  grep "PERF" "$OUT/perf_$case.log" | tail -12
done

echo "=== CASE H265 (H.265 testcard, default config, 3x8s) ==="
H265=$DIR/4k60_h265_testcard.mp4
timeout 120 "$PLAYER" --demo \
    --config "$OUT/A_default_1280_nodetect.json" \
    --snapshot "$OUT/shot_h265.png" --snapshot-delay 12 \
    "$H265" "$H265" "$H265" > "$OUT/perf_h265.log" 2>&1
echo "rc=$?"
grep "PERF" "$OUT/perf_h265.log" | tail -12

echo "=== headless decode throughput (VPU only, no GTK) ==="
for f in "$DIR"/*.mp4; do
  codec=$(ffprobe -v error -select_streams v:0 -show_entries stream=codec_name -of csv=p=0 "$f")
  case "$codec" in
    h264) parser=h264parse ;;
    hevc) parser=h265parse ;;
    *) echo "skip $(basename "$f") ($codec)"; continue ;;
  esac
  echo "-- $(basename "$f") ($codec)"
  t0=$(date +%s%N)
  gst-launch-1.0 -e filesrc location="$f" ! qtdemux ! "$parser" ! mppvideodec \
      ! fakesink sync=false > /dev/null 2>&1
  t1=$(date +%s%N)
  echo "   wall_ms=$(( (t1 - t0) / 1000000 )) for 8s/480 frames"
done

echo "=== results ==="
ls -lh "$OUT"
echo DONE
