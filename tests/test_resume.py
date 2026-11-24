"""Tests for the resume-position store (F4).

The store is deliberately GTK-free so the whole feature can be verified on a
development host; these tests only need ``tmp_path``.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from video_player.resume import (
    DEFAULT_MAX_ENTRIES,
    ResumeEntry,
    ResumeStore,
    default_state_path,
)

SECOND = 1_000_000_000


def _clip(tmp_path: Path, name: str = "clip.mp4", size: int = 4096) -> Path:
    path = tmp_path / name
    path.write_bytes(b"x" * size)
    return path


def _store(tmp_path: Path, **kwargs: object) -> ResumeStore:
    return ResumeStore(tmp_path / "state" / "resume.json", **kwargs)


# -- round trip ------------------------------------------------------------


def test_save_then_load_returns_position(tmp_path: Path) -> None:
    clip = _clip(tmp_path)
    store = _store(tmp_path)
    assert store.save(clip, 30 * SECOND, 120 * SECOND) is True
    # A fresh instance must read the same value: the point is persistence.
    assert _store(tmp_path).load(clip) == 30 * SECOND


def test_short_positions_are_not_stored(tmp_path: Path) -> None:
    clip = _clip(tmp_path)
    store = _store(tmp_path)
    assert store.save(clip, 3 * SECOND, 120 * SECOND) is False
    assert store.load(clip) is None


def test_tail_of_file_is_treated_as_finished(tmp_path: Path) -> None:
    clip = _clip(tmp_path)
    store = _store(tmp_path)
    # 110 s of a 120 s clip leaves less than the 15 s guard.
    assert store.save(clip, 110 * SECOND, 120 * SECOND) is False
    assert store.load(clip) is None


def test_unknown_duration_still_resumes(tmp_path: Path) -> None:
    clip = _clip(tmp_path)
    store = _store(tmp_path)
    assert store.save(clip, 20 * SECOND, 0) is True
    assert store.load(clip) == 20 * SECOND


def test_saving_in_the_tail_clears_the_previous_entry(tmp_path: Path) -> None:
    clip = _clip(tmp_path)
    store = _store(tmp_path)
    store.save(clip, 30 * SECOND, 120 * SECOND)
    store.save(clip, 118 * SECOND, 120 * SECOND)
    assert store.load(clip) is None


# -- file identity ---------------------------------------------------------


def test_resized_file_starts_over(tmp_path: Path) -> None:
    clip = _clip(tmp_path, size=4096)
    store = _store(tmp_path)
    store.save(clip, 30 * SECOND, 120 * SECOND)
    # Same name, different content (re-encoded clip): the old position is
    # meaningless and must not be applied.
    clip.write_bytes(b"y" * 8192)
    assert store.load(clip) is None


def test_missing_file_has_no_position(tmp_path: Path) -> None:
    clip = _clip(tmp_path)
    store = _store(tmp_path)
    store.save(clip, 30 * SECOND, 120 * SECOND)
    clip.unlink()
    assert store.load(clip) is None


# -- robustness ------------------------------------------------------------


def test_corrupt_file_is_ignored(tmp_path: Path) -> None:
    state = tmp_path / "state" / "resume.json"
    state.parent.mkdir(parents=True)
    state.write_text("{not json", encoding="utf-8")
    store = _store(tmp_path)
    assert store.entries() == {}
    clip = _clip(tmp_path)
    assert store.save(clip, 30 * SECOND, 120 * SECOND) is True


def test_malformed_entries_are_dropped(tmp_path: Path) -> None:
    state = tmp_path / "state" / "resume.json"
    state.parent.mkdir(parents=True)
    state.write_text(
        json.dumps(
            {
                "version": 1,
                "files": {
                    "/good": {
                        "size": 1,
                        "mtime_ns": 1,
                        "position_ns": 20 * SECOND,
                        "duration_ns": 60 * SECOND,
                        "updated_at": 1.0,
                    },
                    "/bad": {"position_ns": "twenty"},
                    "/worse": 42,
                },
            }
        ),
        encoding="utf-8",
    )
    entries = _store(tmp_path).entries()
    assert list(entries) == ["/good"]


def test_unwritable_path_does_not_raise(tmp_path: Path) -> None:
    clip = _clip(tmp_path)
    directory = tmp_path / "state"
    directory.mkdir()
    # Point the store at a directory: opening it for writing fails and the
    # store must degrade to "not persisted" instead of breaking playback.
    store = ResumeStore(directory)
    assert store.save(clip, 30 * SECOND, 120 * SECOND) is False


def test_entries_are_pruned_to_the_limit(tmp_path: Path) -> None:
    store = _store(tmp_path, max_entries=3)
    now = 0.0

    def clock() -> float:
        nonlocal now
        now += 1.0
        return now

    store = _store(tmp_path, max_entries=3, clock=clock)
    clips = [_clip(tmp_path, name=f"c{index}.mp4") for index in range(5)]
    for clip in clips:
        store.save(clip, 30 * SECOND, 120 * SECOND)
    entries = _store(tmp_path).entries()
    assert len(entries) == 3
    # The three newest survive, the first two are pruned.
    assert str(clips[0].resolve()) not in entries
    assert str(clips[4].resolve()) in entries


def test_clear_removes_the_entry(tmp_path: Path) -> None:
    clip = _clip(tmp_path)
    store = _store(tmp_path)
    store.save(clip, 30 * SECOND, 120 * SECOND)
    assert store.clear(clip) is True
    assert store.load(clip) is None
    # Clearing twice is a no-op, not an error.
    assert store.clear(clip) is False


def test_forget_ignores_file_identity(tmp_path: Path) -> None:
    clip = _clip(tmp_path)
    store = _store(tmp_path)
    store.save(clip, 30 * SECOND, 120 * SECOND)
    clip.write_bytes(b"changed")
    store.forget(clip)
    assert store.entries() == {}


def test_state_path_uses_xdg_state_home(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XDG_STATE_HOME", "/tmp/xdg-state-test")
    assert default_state_path() == Path("/tmp/xdg-state-test/video-player-demo/resume.json")


def test_entry_parsing_rejects_negative_values() -> None:
    assert ResumeEntry.from_dict({"position_ns": -1, "duration_ns": 10, "updated_at": 0}) is None
    assert ResumeEntry.from_dict({"position_ns": 1, "duration_ns": 10}) is not None


def test_entries_survive_a_reload_with_unicode_paths(tmp_path: Path) -> None:
    clip = _clip(tmp_path, name="会议记录 2026.mp4")
    store = _store(tmp_path)
    store.save(clip, 42 * SECOND, 300 * SECOND)
    assert _store(tmp_path).load(clip) == 42 * SECOND


def test_documented_default_limit() -> None:
    assert DEFAULT_MAX_ENTRIES >= 50
