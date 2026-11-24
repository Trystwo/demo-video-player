"""Tests for the recently-played store (F4).

Like the resume store this module is GTK-free on purpose, so the history
behaviour (ordering, de-duplication, pruning, corrupt files) is verified on a
development host rather than only on the board.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from video_player.recents import (
    DEFAULT_MAX_RECENTS,
    RecentItem,
    RecentsStore,
    default_state_path,
)

SECOND = 1_000_000_000


def _clip(tmp_path: Path, name: str) -> Path:
    path = tmp_path / name
    path.write_bytes(b"x" * 512)
    return path


def _store(tmp_path: Path, **kwargs: object) -> RecentsStore:
    return RecentsStore(tmp_path / "state" / "recent.json", **kwargs)


class _Clock:
    """Monotonic fake clock so ordering assertions are deterministic."""

    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        self.now += 1.0
        return self.now


# -- ordering and persistence ---------------------------------------------


def test_newest_entry_comes_first(tmp_path: Path) -> None:
    first = _clip(tmp_path, "a.mp4")
    second = _clip(tmp_path, "b.mp4")
    store = _store(tmp_path, clock=_Clock())
    store.touch(first)
    store.touch(second)
    assert [item.name for item in store.items()] == ["b.mp4", "a.mp4"]


def test_history_survives_a_new_instance(tmp_path: Path) -> None:
    clip = _clip(tmp_path, "a.mp4")
    store = _store(tmp_path)
    assert store.touch(clip, duration_ns=60 * SECOND, position_ns=12 * SECOND)
    reloaded = _store(tmp_path).items()
    assert [item.name for item in reloaded] == ["a.mp4"]
    assert reloaded[0].position_ns == 12 * SECOND
    assert reloaded[0].duration_ns == 60 * SECOND


def test_touching_again_moves_to_front_without_duplicating(tmp_path: Path) -> None:
    first = _clip(tmp_path, "a.mp4")
    second = _clip(tmp_path, "b.mp4")
    store = _store(tmp_path, clock=_Clock())
    store.touch(first)
    store.touch(second)
    store.touch(first, duration_ns=30 * SECOND, position_ns=10 * SECOND)
    names = [item.name for item in store.items()]
    assert names == ["a.mp4", "b.mp4"]
    assert len(names) == len(set(names))


def test_duration_is_kept_when_a_later_touch_omits_it(tmp_path: Path) -> None:
    """Opening a file touches it before the duration is known."""
    clip = _clip(tmp_path, "a.mp4")
    store = _store(tmp_path, clock=_Clock())
    store.touch(clip, duration_ns=90 * SECOND, position_ns=45 * SECOND)
    store.touch(clip)
    item = store.items()[0]
    assert item.duration_ns == 90 * SECOND
    assert item.position_ns == 45 * SECOND


# -- pruning and removal ---------------------------------------------------


def test_oldest_entries_are_dropped_at_the_cap(tmp_path: Path) -> None:
    store = _store(tmp_path, max_entries=3, clock=_Clock())
    clips = [_clip(tmp_path, f"{index}.mp4") for index in range(5)]
    for clip in clips:
        store.touch(clip)
    assert [item.name for item in store.items()] == ["4.mp4", "3.mp4", "2.mp4"]


def test_remove_drops_one_entry(tmp_path: Path) -> None:
    first = _clip(tmp_path, "a.mp4")
    second = _clip(tmp_path, "b.mp4")
    store = _store(tmp_path, clock=_Clock())
    store.touch(first)
    store.touch(second)
    assert store.remove(first) is True
    assert [item.name for item in store.items()] == ["b.mp4"]
    # Removing something absent is a no-op, not an error.
    assert store.remove(first) is False


def test_clear_empties_the_history(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.touch(_clip(tmp_path, "a.mp4"))
    assert store.clear() is True
    assert store.items() == ()
    assert _store(tmp_path).items() == ()
    assert store.clear() is False


# -- filtering -------------------------------------------------------------


def test_only_existing_hides_deleted_files_but_keeps_them_stored(tmp_path: Path) -> None:
    kept = _clip(tmp_path, "kept.mp4")
    gone = _clip(tmp_path, "gone.mp4")
    store = _store(tmp_path, clock=_Clock())
    store.touch(kept)
    store.touch(gone)
    gone.unlink()
    assert [item.name for item in store.items(only_existing=True)] == ["kept.mp4"]
    assert len(store.items()) == 2


def test_touch_refuses_missing_files(tmp_path: Path) -> None:
    store = _store(tmp_path)
    assert store.touch(tmp_path / "nope.mp4") is False
    assert store.items() == ()


# -- robustness ------------------------------------------------------------


def test_corrupt_file_is_ignored(tmp_path: Path) -> None:
    path = tmp_path / "state" / "recent.json"
    path.parent.mkdir(parents=True)
    path.write_text("{not json", encoding="utf-8")
    assert _store(tmp_path).items() == ()


def test_malformed_rows_are_dropped_individually(tmp_path: Path) -> None:
    path = tmp_path / "state" / "recent.json"
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "files": [
                    {"path": str(tmp_path / "ok.mp4"), "duration_ns": 10},
                    {"path": ""},
                    {"duration_ns": 10},
                    "nonsense",
                    {"path": str(tmp_path / "ok.mp4")},  # duplicate
                ],
            }
        ),
        encoding="utf-8",
    )
    items = _store(tmp_path).items()
    assert [item.path.name for item in items] == ["ok.mp4"]


def test_unwritable_state_file_never_raises(tmp_path: Path) -> None:
    """A read-only home must not break playback; the history just stops."""
    blocker = tmp_path / "state"
    blocker.write_text("i am a file, not a directory", encoding="utf-8")
    store = RecentsStore(blocker / "recent.json")
    assert store.touch(_clip(tmp_path, "a.mp4")) is False


# -- item helpers ----------------------------------------------------------


def test_progress_is_clamped_and_zero_without_duration() -> None:
    assert RecentItem(Path("a.mp4")).progress == 0.0
    half = RecentItem(Path("a.mp4"), duration_ns=100, position_ns=50)
    assert half.progress == pytest.approx(0.5)
    over = RecentItem(Path("a.mp4"), duration_ns=100, position_ns=500)
    assert over.progress == pytest.approx(1.0)


def test_defaults_are_sane() -> None:
    assert DEFAULT_MAX_RECENTS >= 5
    assert default_state_path().name == "recent.json"
