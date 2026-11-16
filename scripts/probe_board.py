#!/usr/bin/env python3
"""Print the environment report for the video player demo.

Thin wrapper around ``video_player.envcheck`` that does not depend on the
working directory: useful from an SSH session on the board and for pasting the
Markdown table into docs/BOARD.md.

Examples:
    python3 scripts/probe_board.py            # plain text report
    python3 scripts/probe_board.py --json     # machine-readable report
    python3 scripts/probe_board.py --markdown --require-board
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

DEMO_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = DEMO_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from video_player.envcheck import (  # noqa: E402
    collect,
    format_markdown,
    format_report,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="probe_board.py",
        description="Print the environment report for the video player demo.",
    )
    output = parser.add_mutually_exclusive_group()
    output.add_argument("--json", action="store_true", help="print a JSON report")
    output.add_argument(
        "--markdown", action="store_true", help="print a Markdown table (docs/BOARD.md)"
    )
    parser.add_argument(
        "--require-board",
        action="store_true",
        help="treat missing RK3576 board features as failures",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    report = collect(DEMO_ROOT)

    if args.json:
        print(json.dumps(report.to_dict(), indent=2, ensure_ascii=False))
    elif args.markdown:
        print(format_markdown(report))
    else:
        print(format_report(report))

    return 0 if report.ok(require_board=args.require_board) else 1


if __name__ == "__main__":
    sys.exit(main())
