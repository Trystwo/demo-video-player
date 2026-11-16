"""Allow ``python3 -m video_player`` to run the application."""

from __future__ import annotations

import sys

from video_player.app import main

if __name__ == "__main__":
    sys.exit(main())
