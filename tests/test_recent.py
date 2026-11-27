"""Recent page model and watch-time formatting: real data, no GTK.

The page itself is a widget tree (the snapshot probe covers it); what a refactor
silently breaks is the arithmetic: which clip belongs in "继续观看" versus
"已看完", that the order follows watch time and not the file mtime, that a
history entry for a file outside the scanned folders cannot promise a card, and
the relative-time buckets the card meta uses.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from video_player.browse import recent_view
from video_player.i18n import Translator
from video_player.library import LibraryItem, VideoMetadata
from video_player.recents import RecentItem
from video_player.ui.chrome import format_watch_time

T = Translator("zh")
T_EN = Translator("en")

NS = 1_000_000_000


def _item(path: Path, *, duration_ns: int = 0, mtime: float = 0.0) -> LibraryItem:
    metadata = VideoMetadata(duration_ns=duration_ns) if duration_ns else None
    return LibraryItem(path=path, size=1, mtime=mtime, metadata=metadata)


@pytest.fixture()
def folder(tmp_path: Path) -> Path:
    root = tmp_path / "片库"
    root.mkdir()
    return root


@pytest.fixture()
def items(folder: Path) -> list[LibraryItem]:
    return [
        _item(folder / "a.mp4", duration_ns=30 * NS, mtime=30.0),
        _item(folder / "b.mp4", duration_ns=60 * NS, mtime=20.0),
        _item(folder / "c.mp4", duration_ns=90 * NS, mtime=10.0),
    ]


# -- recent_view ------------------------------------------------------------


def test_recent_view_splits_continue_and_finished(folder, items) -> None:
    history = [
        # Deliberately oldest-first: the page sorts by watch time itself.
        RecentItem(
            path=items[2].path, duration_ns=90 * NS, position_ns=NS // 2, updated_at=100.0
        ),
        RecentItem(
            path=items[1].path, duration_ns=60 * NS, position_ns=60 * NS, updated_at=200.0
        ),
        RecentItem(
            path=items[0].path, duration_ns=30 * NS, position_ns=9 * NS, updated_at=300.0
        ),
    ]
    view = recent_view(items, history)
    assert [card.item.path for card in view.continue_cards] == [
        items[0].path,
        items[2].path,
    ]
    assert [card.item.path for card in view.finished_cards] == [items[1].path]
    assert view.total == 3
    assert view.empty is False
    # "继续观看 2 · 已看完 1" in the page header.
    assert [key for key, _cards in view.groups()] == ["continue", "finished"]


def test_recent_view_orders_by_watch_time_not_file_mtime(folder) -> None:
    # mtime runs the opposite way: the card order must follow updated_at.
    newest_mtime = _item(folder / "newest-mtime.mp4", duration_ns=30 * NS, mtime=900.0)
    oldest_mtime = _item(folder / "oldest-mtime.mp4", duration_ns=30 * NS, mtime=1.0)
    history = [
        RecentItem(path=newest_mtime.path, duration_ns=30 * NS, position_ns=NS, updated_at=10.0),
        RecentItem(path=oldest_mtime.path, duration_ns=30 * NS, position_ns=NS, updated_at=20.0),
    ]
    view = recent_view([newest_mtime, oldest_mtime], history)
    assert [card.item.path.name for card in view.continue_cards] == [
        "oldest-mtime.mp4",
        "newest-mtime.mp4",
    ]
    assert [card.updated_at for card in view.continue_cards] == [20.0, 10.0]


def test_recent_view_ignores_history_outside_the_library(folder, items) -> None:
    stranger = RecentItem(
        path=folder / "not-scanned.mp4", duration_ns=60 * NS, position_ns=NS, updated_at=99.0
    )
    view = recent_view(
        items,
        [stranger, RecentItem(path=items[0].path, duration_ns=30 * NS, position_ns=NS, updated_at=1.0)],
    )
    assert [card.item.path for card in view.continue_cards] == [items[0].path]
    assert view.total == 1


def test_recent_view_keeps_one_card_per_file(folder, items) -> None:
    history = [
        RecentItem(path=items[0].path, duration_ns=30 * NS, position_ns=NS, updated_at=5.0),
        RecentItem(path=items[0].path, duration_ns=30 * NS, position_ns=2 * NS, updated_at=50.0),
    ]
    view = recent_view(items, history)
    assert view.total == 1
    # The newest entry wins, so the card shows the latest position.
    assert view.continue_cards[0].position_ns == 2 * NS


def test_recent_view_counts_only_resumable_cards(items) -> None:
    history = [
        # A position below one second is not "continue watching".
        RecentItem(path=items[0].path, duration_ns=30 * NS, position_ns=NS // 2, updated_at=3.0),
        RecentItem(path=items[1].path, duration_ns=60 * NS, position_ns=5 * NS, updated_at=2.0),
        RecentItem(path=items[2].path, duration_ns=90 * NS, position_ns=90 * NS, updated_at=1.0),
    ]
    view = recent_view(items, history)
    assert len(view.continue_cards) == 2
    assert view.resumable_count == 1


def test_recent_view_is_empty_without_history(items) -> None:
    view = recent_view(items, [])
    assert view.empty is True
    assert view.total == 0
    assert view.groups() == ()


def test_recent_view_accepts_items_without_metadata(folder) -> None:
    # A file that has not been probed yet still gets a card; the duration is
    # unknown, so the resume CTA is simply absent.
    item = _item(folder / "pending.mp4", mtime=1.0)
    view = recent_view([item], [RecentItem(path=item.path, position_ns=NS, updated_at=1.0)])
    card = view.continue_cards[0]
    assert card.duration_ns == 0
    assert card.resumable is True


# -- format_watch_time ------------------------------------------------------


def _epoch(year: int, month: int, day: int, hour: int = 0, minute: int = 0) -> float:
    return datetime(year, month, day, hour, minute).timestamp()


def test_format_watch_time_buckets() -> None:
    now = _epoch(2026, 11, 27, 19, 30)
    assert format_watch_time(now - 30, t=T, now=now) == "刚刚观看"
    assert format_watch_time(_epoch(2026, 11, 27, 9, 0), t=T, now=now) == "今天 09:00"
    assert format_watch_time(_epoch(2026, 11, 26, 21, 40), t=T, now=now) == "昨天 21:40"
    assert format_watch_time(_epoch(2026, 2, 3, 18, 45), t=T, now=now) == "2 月 3 日"
    assert (
        format_watch_time(_epoch(2025, 3, 4, 8, 0), t=T, now=now)
        == "2025 年 3 月 4 日"
    )
    # A history entry written before ``updated_at`` existed reads as unknown.
    assert format_watch_time(0.0, t=T, now=now) == ""


def test_format_watch_time_english() -> None:
    now = _epoch(2026, 11, 27, 19, 30)
    assert format_watch_time(now - 30, t=T_EN, now=now) == "Just now"
    assert format_watch_time(_epoch(2026, 11, 27, 9, 0), t=T_EN, now=now) == "Today 09:00"
    assert (
        format_watch_time(_epoch(2026, 11, 26, 21, 40), t=T_EN, now=now)
        == "Yesterday 21:40"
    )
    assert format_watch_time(_epoch(2026, 2, 3, 18, 45), t=T_EN, now=now) == "2/3"
    assert (
        format_watch_time(_epoch(2025, 3, 4, 8, 0), t=T_EN, now=now)
        == "2025/3/4"
    )


# -- widget contract --------------------------------------------------------


def test_recent_page_reads_the_library_caches() -> None:
    """The page must not start its own workers: posters/metadata are shared."""
    source = (
        Path(__file__).resolve().parents[1] / "src" / "video_player" / "ui" / "recent_page.py"
    ).read_text(encoding="utf-8")
    assert "self._library.poster_for(" in source
    assert "self._library.item_for(" in source
    assert "ThumbnailWorker" not in source
    assert "MetadataWorker" not in source
    # Clearing history is a confirm dialog, not a one-tap destructive action.
    assert "Gtk.AlertDialog()" in source
    assert "self._recents.clear()" in source
