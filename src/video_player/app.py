"""Command-line entry point for the video player."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

from . import __version__
from .config import Config, ConfigError
from .playlist import expand_media_args


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="video_player",
        description=(
            "GTK4 video player with automatic face beauty for Quectel Pi M2. "
            "Positional arguments are local video files or folders; folders are "
            "expanded to a playlist. Playback is hardware decoded on the RK3576 VPU."
        ),
    )
    parser.add_argument(
        "media",
        nargs="*",
        metavar="MEDIA",
        help="local video file(s) or folder(s) to play",
    )
    parser.add_argument(
        "--config",
        type=Path,
        metavar="FILE",
        help="configuration file (default: configs/player.example.json)",
    )
    parser.add_argument(
        "--demo",
        action="store_true",
        help="demo mode: fullscreen, autoplay, info panel and compare toggle on",
    )
    parser.add_argument(
        "--fullscreen",
        action="store_true",
        help="start the window in fullscreen",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="print the environment report and exit (no window is opened)",
    )
    parser.add_argument(
        "--require-board",
        action="store_true",
        help="treat missing RK3576 board features (VPU/NPU/GPU) as failures",
    )
    parser.add_argument(
        "--record",
        type=Path,
        metavar="FILE",
        help=(
            "record the window to FILE (MP4, 30 fps, UI included) while it plays; "
            "press R in the window to stop early"
        ),
    )
    parser.add_argument(
        "--record-duration",
        type=float,
        metavar="SECONDS",
        help=(
            "stop --record after SECONDS and close the window; used to shoot "
            "the demo video segments"
        ),
    )
    parser.add_argument(
        "--snapshot",
        type=Path,
        metavar="FILE",
        help="capture the window to FILE (PNG) and exit; used for evidence",
    )
    parser.add_argument(
        "--snapshot-delay",
        type=float,
        default=3.0,
        metavar="SECONDS",
        help="seconds to wait before --snapshot is taken (default: 3)",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.check:
        from .envcheck import collect, format_report

        report = collect()
        print(format_report(report))
        return 0 if report.ok(require_board=args.require_board) else 1

    try:
        config = Config.load(args.config)
    except ConfigError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 2

    try:
        media = expand_media_args(args.media)
    except ValueError as exc:
        print(f"media error: {exc}", file=sys.stderr)
        return 2

    existing = []
    for path in media:
        if path.exists():
            existing.append(path)
        else:
            print(f"warning: media file not found: {path}", file=sys.stderr)

    if args.demo and not existing:
        print(
            "error: --demo needs at least one existing media file or folder",
            file=sys.stderr,
        )
        return 2

    try:
        from .ui.window import run_window
    except ImportError as exc:  # pragma: no cover - depends on the host
        print(
            "GTK4 (PyGObject) is required to open the player window; install "
            "the board packages from docs/BOARD.md and retry: "
            f"{exc}",
            file=sys.stderr,
        )
        return 2

    return run_window(
        config,
        existing,
        demo=args.demo,
        fullscreen=args.fullscreen or args.demo,
        snapshot=args.snapshot,
        snapshot_delay=args.snapshot_delay,
        record=args.record,
        record_duration=args.record_duration,
    )


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
