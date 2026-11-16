#!/usr/bin/env python3
"""Launch the video player from a checkout (desktop-entry friendly wrapper).

Adds ``src/`` to ``sys.path`` and forwards the arguments to
``video_player.app.main`` so a future desktop file only needs one command:

    python3 /opt/demo-video-player/scripts/launch_player.py [MEDIA ...]

Examples:
    scripts/launch_player.py ~/Videos/demo.mp4
    scripts/launch_player.py --demo ~/Videos/
"""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

DEMO_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = DEMO_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from video_player.app import main as app_main  # noqa: E402


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="launch_player.py",
        description="Launch the video player with optional media files or folders.",
    )
    parser.add_argument(
        "media", nargs="*", help="local video file(s) or folder(s) to play"
    )
    parser.add_argument("--config", type=Path, help="configuration file to load")
    parser.add_argument(
        "--demo", action="store_true", help="demo mode: fullscreen, autoplay"
    )
    parser.add_argument(
        "--fullscreen", action="store_true", help="start in fullscreen"
    )
    parser.add_argument(
        "--check", action="store_true", help="run the environment check instead"
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args, extra = build_parser().parse_known_args(argv)
    forwarded: list[str] = list(extra)
    if args.check:
        forwarded.append("--check")
    if args.demo:
        forwarded.append("--demo")
    if args.fullscreen:
        forwarded.append("--fullscreen")
    if args.config is not None:
        forwarded.extend(["--config", str(args.config)])
    forwarded.extend(args.media)
    return app_main(forwarded)


if __name__ == "__main__":
    sys.exit(main())
