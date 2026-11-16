#!/usr/bin/env bash
# Environment check / deployment helper for the Quectel Pi M2 video player demo.
#
# During the scaffold stage this script only prepares an optional virtual
# environment and validates the runtime environment.  Playback, NPU detection
# and GPU beauty are implemented in M1-M4 (see docs/PRD.md); the full deployment
# contract ("Deployment complete", system packages, desktop entry) lands with M4.
#
# The script never runs apt and never installs system packages as root: board
# packages are documented in docs/BOARD.md and README.md.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="$SCRIPT_DIR"
PYTHON_BIN="${PYTHON_BIN:-python3}"
VENV_DIR=""
REQUIRE_BOARD=0
DRY_RUN=0

usage() {
  cat <<'EOF'
Usage: ./install.sh [options]

Options:
  --python PATH        Python interpreter to use (default: python3)
  --venv DIR           Create/refresh a virtual environment in DIR (optional)
  --require-board      Fail when RK3576 board features are missing
  --dry-run            Print the commands that would run, then exit
  -h, --help           Show this help and exit

On the board, --require-board is recommended: it turns the board-only checks
(mppvideodec, waylandsink, librknnrt, librga, RKNN models) into hard failures.
EOF
}

log() { printf '[install] %s\n' "$*"; }
fail() { printf '[install] error: %s\n' "$*" >&2; exit 1; }

run() {
  if [ "$DRY_RUN" -eq 1 ]; then
    printf '[dry-run] %s\n' "$*"
    return 0
  fi
  "$@"
}

while [ $# -gt 0 ]; do
  case "$1" in
    --python)
      [ $# -ge 2 ] || fail "--python needs a value"
      PYTHON_BIN="$2"
      shift 2
      ;;
    --python=*)
      PYTHON_BIN="${1#*=}"
      shift
      ;;
    --venv)
      [ $# -ge 2 ] || fail "--venv needs a value"
      VENV_DIR="$2"
      shift 2
      ;;
    --venv=*)
      VENV_DIR="${1#*=}"
      shift
      ;;
    --require-board)
      REQUIRE_BOARD=1
      shift
      ;;
    --dry-run)
      DRY_RUN=1
      shift
      ;;
    -h | --help)
      usage
      exit 0
      ;;
    *)
      fail "unknown option: $1 (try --help)"
      ;;
  esac
done

command -v "$PYTHON_BIN" >/dev/null 2>&1 || fail "Python interpreter not found: $PYTHON_BIN"
PY_VERSION="$("$PYTHON_BIN" -c 'import sys; print("%d.%d" % sys.version_info[:2])')"
PY_VERSION_CODE="$("$PYTHON_BIN" -c 'import sys; print(sys.version_info[0] * 100 + sys.version_info[1])')"
[ "$PY_VERSION_CODE" -ge 310 ] || fail "Python >= 3.10 is required, found $PY_VERSION"

log "project directory: $APP_DIR"
log "python: $PYTHON_BIN ($PY_VERSION)"

command -v gst-inspect-1.0 >/dev/null 2>&1 || \
  log "warning: gst-inspect-1.0 not found; install GStreamer tools (see README)"
if [ -z "${WAYLAND_DISPLAY:-}" ] && [ -z "${DISPLAY:-}" ]; then
  log "warning: no graphical session detected; the environment check still works headless"
fi

PYTHON_RUN="$PYTHON_BIN"
if [ -n "$VENV_DIR" ]; then
  if [ ! -d "$VENV_DIR" ]; then
    log "creating virtual environment: $VENV_DIR (with system site packages)"
    run "$PYTHON_BIN" -m venv --system-site-packages "$VENV_DIR"
  else
    log "reusing virtual environment: $VENV_DIR"
  fi
  PYTHON_RUN="$VENV_DIR/bin/python"
  if [ "$DRY_RUN" -eq 0 ]; then
    PTH_DIR="$("$PYTHON_RUN" -c 'import site; print(site.getsitepackages()[0])')" || \
      fail "cannot locate site-packages inside $VENV_DIR"
    printf '%s\n' "$APP_DIR/src" > "$PTH_DIR/_video_player_src.pth"
    log "registered src path in $PTH_DIR/_video_player_src.pth"
    if [ -f "$APP_DIR/requirements.txt" ]; then
      "$PYTHON_RUN" -m pip install --disable-pip-version-check -q -r "$APP_DIR/requirements.txt" || \
        log "warning: pip install failed; installing NumPy is optional on the board (apt python3-numpy)"
    fi
  fi
fi

CHECK_CMD=(-m video_player --check)
if [ "$REQUIRE_BOARD" -eq 1 ]; then
  CHECK_CMD+=(--require-board)
fi

log "running environment check"
if [ "$DRY_RUN" -eq 1 ]; then
  printf '[dry-run] PYTHONPATH=%s %s %s\n' "$APP_DIR/src" "$PYTHON_RUN" "${CHECK_CMD[*]}"
  exit 0
fi

set +e
PYTHONPATH="$APP_DIR/src${PYTHONPATH:+:$PYTHONPATH}" \
  "$PYTHON_RUN" "${CHECK_CMD[@]}"
CHECK_STATUS=$?
set -e

if [ "$CHECK_STATUS" -ne 0 ]; then
  printf '\nEnvironment check failed (exit %d).\n' "$CHECK_STATUS" >&2
  printf 'Required packages and expected board features are listed in docs/BOARD.md.\n' >&2
  exit "$CHECK_STATUS"
fi

cat <<'EOF'

Environment check complete.

The player skeleton is ready for M1 (hardware-decoded playback). Next steps:
  1. Read docs/BOARD.md and record the measured board baseline for QUECPI-627.
  2. Launch the UI skeleton:   PYTHONPATH=src python3 -m video_player
  3. Re-run the environment check any time:   python3 -m video_player --check

The scaffold stage only validates the environment; the full deployment
(system packages, desktop entry, "Deployment complete") is tracked as M4.
EOF
