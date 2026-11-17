#!/usr/bin/env python3
"""Launch the video player from a checkout (desktop-entry friendly wrapper).

Adds ``src/`` to ``sys.path`` and forwards the arguments unchanged to
``video_player.app.main`` so a future desktop file only needs one command:

    python3 /opt/demo-video-player/scripts/launch_player.py [OPTIONS] [MEDIA ...]

Examples:
    scripts/launch_player.py ~/Videos/demo.mp4
    scripts/launch_player.py --demo ~/Videos/
    scripts/launch_player.py --demo clip.mp4 --snapshot /tmp/shot.png --snapshot-delay 6

``argparse`` is deliberately *not* used to pre-parse the arguments here: the
launcher does not know the application's options, and ``parse_known_args`` treats
the value of an unknown option (``--snapshot out.png``) as a positional media
argument, which silently swallowed the snapshot path.  Every option is parsed by
``video_player.app`` instead.
"""

from __future__ import annotations

from pathlib import Path
import sys

DEMO_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = DEMO_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from video_player.app import main as app_main  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    return app_main(list(sys.argv[1:] if argv is None else argv))


if __name__ == "__main__":
    sys.exit(main())
