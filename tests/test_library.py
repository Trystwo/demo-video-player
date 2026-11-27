"""Library data layer: scanning, the metadata cache, filters and sorting."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from video_player.library import (
    FILTERS,
    FINISHED_THRESHOLD,
    MAX_ITEMS,
    SORTS,
    LibraryItem,
    LibraryStore,
    VideoMetadata,
    default_library_state_path,
    filter_items,
    library_counts,
    scan_library,
    sort_items,
)


@pytest.fixture()
def library_dir(tmp_path: Path) -> Path:
    folder = tmp_path / "lib"
    folder.mkdir()
    for name in ("beta.mp4", "alpha.mkv", "gamma.MP4"):
        (folder / name).write_bytes(b"\x00" * 16)
    (folder / "notes.txt").write_text("not a video", encoding="utf-8")
    (folder / "nested").mkdir()
    (folder / "nested" / "inner.mp4").write_bytes(b"\x00" * 16)
    return folder


def test_scan_lists_supported_files_one_level_deep(library_dir: Path) -> None:
    items = scan_library([library_dir])
    names = [item.path.name for item in items]
    assert names == ["alpha.mkv", "beta.mp4", "gamma.MP4"]
    # Sub-folders are not walked; a non-video file is ignored.
    assert "inner.mp4" not in names
    assert "notes.txt" not in names
    assert all(item.path.is_absolute() for item in items)
    assert all(item.size > 0 for item in items)


def test_scan_skips_missing_roots_and_dedupes(library_dir: Path, tmp_path: Path) -> None:
    missing = tmp_path / "gone"
    items = scan_library([library_dir, missing, library_dir])
    assert len(items) == 3
    assert len({item.path for item in items}) == 3


def test_scan_reports_mtime_and_size(library_dir: Path) -> None:
    target = library_dir / "beta.mp4"
    os.utime(target, (1_700_000_000.0, 1_700_000_000.0))
    items = scan_library([library_dir], limit=1)
    assert len(items) == 1
    assert items[0].path.name == "alpha.mkv"
    beta = scan_library([library_dir])[1]
    assert beta.mtime == pytest.approx(1_700_000_000.0)
    assert beta.size == 16


def test_scan_honours_the_item_limit(library_dir: Path) -> None:
    items = scan_library([library_dir], limit=2)
    assert len(items) == 2
    assert MAX_ITEMS >= 100


def test_metadata_cache_round_trips_and_invalidates(library_dir: Path, tmp_path: Path) -> None:
    store = LibraryStore(tmp_path / "library.json")
    path = library_dir / "beta.mp4"
    stat = path.stat()
    meta = VideoMetadata(duration_ns=9_000_000_000, width=1920, height=1080, fps=30, codec="h265")
    assert store.metadata_for(path, stat.st_size, stat.st_mtime) is None
    assert store.remember_metadata(path, stat.st_size, stat.st_mtime, meta) is True
    # Same size+mtime hits; a different size or mtime misses.
    assert store.metadata_for(path, stat.st_size, stat.st_mtime) == meta
    assert store.metadata_for(path, stat.st_size + 1, stat.st_mtime) is None
    assert store.metadata_for(path, stat.st_size, stat.st_mtime + 2.0) is None
    # The scan attaches the cached value.
    item = scan_library([library_dir], store)[1]
    assert item.metadata == meta
    assert item.duration_ns == 9_000_000_000


def test_metadata_cache_survives_a_save_load_round_trip(library_dir: Path, tmp_path: Path) -> None:
    state = tmp_path / "state" / "library.json"
    store = LibraryStore(state)
    assert store.add_directory(library_dir) is True
    path = library_dir / "beta.mp4"
    stat = path.stat()
    meta = VideoMetadata(duration_ns=1_000_000_000, width=1280, height=720, fps=25, codec="vp9")
    store.remember_metadata(path, stat.st_size, stat.st_mtime, meta)
    assert store.save() is True

    reopened = LibraryStore(state)
    assert reopened.directories == (library_dir.resolve(),)
    assert reopened.metadata_for(path, stat.st_size, stat.st_mtime) == meta


def test_store_rejects_files_and_duplicate_folders(library_dir: Path, tmp_path: Path) -> None:
    store = LibraryStore(tmp_path / "library.json")
    assert store.add_directory(library_dir) is True
    assert store.add_directory(library_dir) is False
    assert store.add_directory(library_dir / "beta.mp4") is False
    assert store.add_directory(tmp_path / "missing") is False
    assert store.directories == (library_dir.resolve(),)
    assert store.remove_directory(library_dir) is True
    assert store.remove_directory(library_dir) is False
    assert store.directories == ()


def test_corrupt_state_file_degrades_to_empty(tmp_path: Path) -> None:
    state = tmp_path / "library.json"
    state.write_text("{not json", encoding="utf-8")
    store = LibraryStore(state)
    assert store.directories == ()
    assert store.metadata_for("/tmp/x.mp4", 1, 1.0) is None
    # Saving over the broken file still works.
    assert store.save() is True
    assert json.loads(state.read_text(encoding="utf-8"))["version"] == 1


def test_state_file_skips_malformed_entries(tmp_path: Path) -> None:
    state = tmp_path / "library.json"
    state.write_text(
        json.dumps(
            {
                "version": 1,
                "directories": ["", 5, str(tmp_path)],
                "metadata": {
                    "/tmp/a.mp4": {"size": "x"},
                    "/tmp/b.mp4": {"size": 1, "mtime": 1.0, "duration_ns": 0, "width": 0, "height": 0},
                },
            }
        ),
        encoding="utf-8",
    )
    store = LibraryStore(state)
    assert store.directories == (tmp_path.resolve(),)
    assert store.metadata_for("/tmp/b.mp4", 1, 1.0) is None  # empty probe is not cached


def test_default_state_path_is_under_xdg_state(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    assert default_library_state_path() == tmp_path / "video-player-demo" / "library.json"


def _item(name: str, *, duration_ns: int = 0, mtime: float = 0.0) -> LibraryItem:
    metadata = (
        VideoMetadata(duration_ns=duration_ns) if duration_ns else None
    )
    return LibraryItem(path=Path("/lib") / name, size=1, mtime=mtime, metadata=metadata)


def test_filter_keys_cover_the_chips() -> None:
    # "最近观看" is the browse shell's own page now, not a library chip.
    assert FILTERS == ("all", "finished")
    assert set(SORTS) == {"name", "duration", "added"}


def test_filter_items_selects_finished() -> None:
    items = [_item("a.mp4"), _item("b.mp4"), _item("c.mp4")]
    finished = {items[2].path}
    assert filter_items(items, "all") == items
    assert filter_items(items, "finished", finished_paths=finished) == [items[2]]
    assert filter_items(items, "finished") == []


def test_filter_rejects_unknown_keys() -> None:
    with pytest.raises(ValueError):
        filter_items([], "series")
    with pytest.raises(ValueError):
        sort_items([], "views")


def test_counts_follow_the_filters() -> None:
    items = [_item("a.mp4"), _item("b.mp4")]
    counts = library_counts(items, finished_paths={items[1].path})
    assert counts == {"all": 2, "finished": 1}


def test_sort_by_name_is_case_insensitive() -> None:
    items = [_item("b.mp4"), _item("A.mp4"), _item("c.mkv")]
    assert [item.path.name for item in sort_items(items, "name")] == [
        "A.mp4",
        "b.mp4",
        "c.mkv",
    ]


def test_sort_by_added_is_newest_first() -> None:
    items = [_item("old.mp4", mtime=10.0), _item("new.mp4", mtime=30.0)]
    assert [item.path.name for item in sort_items(items, "added")] == [
        "new.mp4",
        "old.mp4",
    ]


def test_sort_by_duration_puts_unknown_last() -> None:
    items = [
        _item("short.mp4", duration_ns=5),
        _item("unknown.mp4"),
        _item("long.mp4", duration_ns=50),
    ]
    assert [item.path.name for item in sort_items(items, "duration")] == [
        "long.mp4",
        "short.mp4",
        "unknown.mp4",
    ]


def test_finished_threshold_is_a_fraction() -> None:
    assert 0.0 < FINISHED_THRESHOLD < 1.0


def test_metadata_from_dict_tolerates_junk() -> None:
    assert VideoMetadata.from_dict(None) is None
    assert VideoMetadata.from_dict({"duration_ns": "x"}) is None
    assert VideoMetadata.from_dict({"width": -1}) is None
    assert VideoMetadata.from_dict({"duration_ns": 0, "width": 0, "height": 0}) is None
    meta = VideoMetadata.from_dict({"duration_ns": 1, "width": 2, "height": 3, "fps": 25, "codec": "h264"})
    assert meta == VideoMetadata(duration_ns=1, width=2, height=3, fps=25, codec="h264")
