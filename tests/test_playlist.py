"""Tests for CLI media expansion and the playlist cursor (F4)."""

from __future__ import annotations

from pathlib import Path

import pytest

from video_player.playlist import Playlist, expand_media_args, is_video


def _touch(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"stub")
    return path


def test_expand_media_args_with_no_arguments() -> None:
    assert expand_media_args([]) == []


def test_expand_media_args_expands_directory_in_name_order(tmp_path: Path) -> None:
    _touch(tmp_path / "b_clip.mp4")
    _touch(tmp_path / "a_clip.MKV")
    _touch(tmp_path / "notes.txt")
    nested = tmp_path / "nested"
    _touch(nested / "deep.mp4")

    assert expand_media_args([tmp_path]) == [
        tmp_path / "a_clip.MKV",
        tmp_path / "b_clip.mp4",
    ]


def test_expand_media_args_keeps_explicit_order_and_deduplicates(tmp_path: Path) -> None:
    first = _touch(tmp_path / "first.mp4")
    second = _touch(tmp_path / "second.mkv")

    expanded = expand_media_args([first, second, first, str(second)])
    assert expanded == [first, second]


def test_expand_media_args_keeps_missing_explicit_file(tmp_path: Path) -> None:
    missing = tmp_path / "not-yet-recorded.mp4"
    assert expand_media_args([missing]) == [missing]


def test_expand_media_args_rejects_existing_non_video(tmp_path: Path) -> None:
    subtitle = _touch(tmp_path / "subtitle.ass")
    with pytest.raises(ValueError, match="unsupported media file"):
        expand_media_args([subtitle])


def test_is_video_is_case_insensitive() -> None:
    assert is_video("clip.mp4")
    assert is_video(Path("/tmp/CLIP.MKV"))
    assert not is_video("subtitle.srt")


def test_playlist_of_nothing() -> None:
    playlist = Playlist()
    assert len(playlist) == 0
    assert not playlist
    assert playlist.current is None
    assert playlist.index == 0
    assert list(playlist) == []
    assert playlist.next() is None
    assert playlist.previous() is None


def test_playlist_cursor_walks_forward_and_back(tmp_path: Path) -> None:
    clips = [_touch(tmp_path / f"{name}.mp4") for name in ("a", "b", "c")]
    playlist = Playlist(clips)
    assert playlist.files == tuple(clips)
    assert playlist.current == clips[0]
    assert playlist.has_previous() is False
    assert playlist.has_next() is True

    assert playlist.next() == clips[1]
    assert playlist.index == 1
    assert playlist.has_previous() is True

    assert playlist.next() == clips[2]
    assert playlist.next() is None
    assert playlist.index == 2

    assert playlist.previous() == clips[1]
    assert playlist.previous() == clips[0]
    assert playlist.previous() is None


def test_playlist_select_and_labels(tmp_path: Path) -> None:
    clips = [_touch(tmp_path / f"clip-{index}.mp4") for index in range(3)]
    playlist = Playlist(clips)
    assert playlist.select(2) == clips[2]
    assert playlist.index == 2
    assert playlist.label(1) == "clip-1.mp4"
    with pytest.raises(IndexError):
        playlist.select(3)
    with pytest.raises(IndexError):
        playlist.select(-1)


def test_playlist_add_is_idempotent(tmp_path: Path) -> None:
    first = _touch(tmp_path / "first.mp4")
    second = _touch(tmp_path / "second.mp4")
    playlist = Playlist([first])
    assert playlist.add(second) == 1
    assert playlist.add(first) == 0
    assert playlist.add(second) == 1
    assert len(playlist) == 2
