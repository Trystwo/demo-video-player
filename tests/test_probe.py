"""Probing helpers: poster cache keys and graceful failures.

The actual decode is exercised on the board and by the local QC harness; these
tests keep the cache contract and the "never raise into the UI" promise.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from video_player.probe import (
    DEFAULT_THUMB_WIDTH,
    default_thumbnail_dir,
    hardware_decode_ready,
    probe_metadata,
    thumbnail_cache_path,
)


def test_thumbnail_cache_path_is_stable_and_keyed_by_file_version(tmp_path: Path) -> None:
    media = Path("/lib/clip.mp4")
    first = thumbnail_cache_path(media, 1024, 1_700_000_000.0, directory=tmp_path)
    again = thumbnail_cache_path(media, 1024, 1_700_000_000.0, directory=tmp_path)
    assert first == again
    assert first.parent == tmp_path
    assert first.suffix == ".png"
    assert len(first.stem) == 40  # sha1 hex

    # A new size, mtime or poster width is a different cache entry.
    assert thumbnail_cache_path(media, 1025, 1_700_000_000.0, directory=tmp_path) != first
    assert thumbnail_cache_path(media, 1024, 1_700_000_001.0, directory=tmp_path) != first
    assert (
        thumbnail_cache_path(media, 1024, 1_700_000_000.0, width=272, directory=tmp_path)
        != first
    )


def test_thumbnail_width_is_two_cards_wide() -> None:
    assert DEFAULT_THUMB_WIDTH == 546


def test_default_thumbnail_dir_follows_xdg_cache(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    assert default_thumbnail_dir() == tmp_path / "video-player-demo" / "thumbs"


def test_probe_missing_file_returns_none(tmp_path: Path) -> None:
    assert probe_metadata(tmp_path / "missing.mp4", timeout_s=1) is None


def test_hardware_decode_ready_is_a_boolean() -> None:
    assert isinstance(hardware_decode_ready(), bool)
