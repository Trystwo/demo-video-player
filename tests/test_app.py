"""Tests for the command-line entry point (argument contract, error paths)."""

from __future__ import annotations

from pathlib import Path

import pytest

from video_player import __version__
from video_player.app import build_parser, main


def test_parser_defaults() -> None:
    args = build_parser().parse_args([])
    assert args.media == []
    assert args.config is None
    assert args.demo is False
    assert args.fullscreen is False
    assert args.check is False
    assert args.require_board is False
    assert args.snapshot is None
    assert args.snapshot_delay == 3.0
    assert args.lang is None


def test_parser_accepts_media_and_flags() -> None:
    args = build_parser().parse_args(
        [
            "--demo",
            "--config",
            "custom.json",
            "--snapshot",
            "out/ui.png",
            "--snapshot-delay",
            "1.5",
            "--lang",
            "en",
            "clip.mp4",
            "folder",
        ]
    )
    assert args.media == ["clip.mp4", "folder"]
    assert args.demo is True
    assert args.config == Path("custom.json")
    assert args.snapshot == Path("out/ui.png")
    assert args.snapshot_delay == 1.5
    assert args.lang == "en"


def test_version_flag_prints_package_version(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as excinfo:
        build_parser().parse_args(["--version"])
    assert excinfo.value.code == 0
    assert __version__ in capsys.readouterr().out


def test_config_error_returns_2(capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    missing = tmp_path / "missing.json"
    assert main(["--config", str(missing)]) == 2
    assert "config error" in capsys.readouterr().err


def test_unsupported_media_file_returns_2(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    subtitle = tmp_path / "subtitle.ass"
    subtitle.write_bytes(b"stub")
    assert main([str(subtitle)]) == 2
    assert "media error" in capsys.readouterr().err


def test_demo_without_existing_media_returns_2(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--demo", "/nonexistent/demo.mp4"]) == 2
    captured = capsys.readouterr()
    assert "warning: media file not found" in captured.err
    assert "error: --demo needs at least one existing media" in captured.err
