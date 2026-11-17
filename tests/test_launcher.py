"""Tests for ``scripts/launch_player.py`` argument forwarding.

The launcher exists only to put ``src/`` on ``sys.path``; it must hand every
argument to :func:`video_player.app.main` untouched.  A regression test is worth
having because ``argparse.parse_known_args`` (the previous implementation)
silently consumed the value of an unknown option such as ``--snapshot out.png``
as a positional media path, so ``--snapshot`` never reached the application.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "launch_player.py"


def _load_launcher():
    spec = importlib.util.spec_from_file_location("launch_player", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_launcher_forwards_every_argument_verbatim(monkeypatch: pytest.MonkeyPatch) -> None:
    launcher = _load_launcher()
    seen: list[list[str]] = []

    def fake_main(argv: list[str]) -> int:
        seen.append(list(argv))
        return 7

    monkeypatch.setattr(launcher, "app_main", fake_main)
    argv = [
        "--demo",
        "clip.mp4",
        "--snapshot",
        "/tmp/shot.png",
        "--snapshot-delay",
        "6",
        "--config",
        "custom.json",
    ]
    assert launcher.main(argv) == 7
    assert seen == [argv]


def test_launcher_makes_src_importable(monkeypatch: pytest.MonkeyPatch) -> None:
    launcher = _load_launcher()
    src_dir = str(Path(__file__).resolve().parents[1] / "src")
    monkeypatch.setattr(launcher, "app_main", lambda _argv: 0)
    # Importing the module already inserted the path.
    assert src_dir in launcher.sys.path
