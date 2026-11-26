"""Home / detail view models: real data, no GTK.

The rules under test are the ones the mockups left to the implementation:
which clip gets the hero, what counts as an "episode", how the clip-facts card
is derived, and what the page does when the history or the probe is empty.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from video_player.browse import (
    DETAIL_FACT_KEYS,
    HOME_CONTINUE_LIMIT,
    RESUME_MIN_NS,
    DetailFact,
    bitrate_text,
    detail_view,
    episode_items,
    hardware_decodable,
    home_view,
    progress_map,
    remaining_text,
)
from video_player.i18n import Translator
from video_player.library import LibraryItem, VideoMetadata
from video_player.recents import RecentItem

T = Translator("zh")


def _item(
    path: Path,
    *,
    size: int = 1_000_000,
    mtime: float = 0.0,
    metadata: VideoMetadata | None = None,
) -> LibraryItem:
    return LibraryItem(path=Path(path), size=size, mtime=mtime, metadata=metadata)


def _meta(
    *, duration: int = 60_000_000_000, width: int = 1920, height: int = 1080,
    fps: float = 30.0, codec: str = "h264",
) -> VideoMetadata:
    return VideoMetadata(
        duration_ns=duration, width=width, height=height, fps=fps, codec=codec
    )


@pytest.fixture()
def folder(tmp_path: Path) -> Path:
    root = tmp_path / "片库"
    root.mkdir()
    return root


@pytest.fixture()
def items(folder: Path) -> list[LibraryItem]:
    return [
        _item(folder / "b.mp4", size=2_000_000, mtime=30.0, metadata=_meta()),
        _item(folder / "a.mp4", size=1_000_000, mtime=10.0, metadata=_meta(duration=30_000_000_000)),
        _item(folder / "c.mp4", size=3_000_000, mtime=20.0, metadata=_meta(codec="h265")),
    ]


# -- home -------------------------------------------------------------------


def test_home_continue_follows_history_and_keeps_progress(items) -> None:
    history = [
        RecentItem(path=items[1].path, duration_ns=30_000_000_000, position_ns=9_000_000_000),
        RecentItem(path=items[0].path, duration_ns=60_000_000_000, position_ns=60_000_000_000),
    ]
    view = home_view(items, history)
    assert [card.item.path.name for card in view.continue_cards] == ["a.mp4", "b.mp4"]
    first = view.continue_cards[0]
    assert first.progress == pytest.approx(0.3)
    assert first.remaining_ns == 21_000_000_000
    assert not first.finished
    assert view.continue_cards[1].finished
    assert view.hero is first
    assert view.hero_is_resume


def test_home_ignores_history_entries_outside_the_library(folder, items) -> None:
    outside = RecentItem(path=folder / "not-scanned.mp4", position_ns=1, duration_ns=2)
    view = home_view(items, [outside])
    assert view.continue_cards == ()
    # The hero falls back to the newest library file instead of the stranger.
    assert view.hero is not None
    assert view.hero.item.path.name == "b.mp4"
    assert not view.hero_is_resume


def test_home_added_row_is_newest_first_and_capped(items) -> None:
    view = home_view(items)
    assert [item.path.name for item in view.added_items] == ["b.mp4", "c.mp4", "a.mp4"]
    capped = home_view(items, limit=2)
    assert [item.path.name for item in capped.added_items] == ["b.mp4", "c.mp4"]
    assert HOME_CONTINUE_LIMIT == 4


def test_home_is_empty_without_items() -> None:
    view = home_view([])
    assert view.empty
    assert view.hero is None
    assert view.added_items == ()


def test_home_card_takes_duration_from_scanned_metadata(items) -> None:
    # The history may predate the probe (duration 0); the file's metadata wins.
    entry = RecentItem(path=items[0].path, duration_ns=0, position_ns=6_000_000_000)
    card = home_view(items, [entry]).continue_cards[0]
    assert card.duration_ns == 60_000_000_000
    assert card.progress == pytest.approx(0.1)


# -- episodes ---------------------------------------------------------------


def test_episode_items_are_the_same_folder_sorted_by_name(folder, items) -> None:
    other = _item(folder.parent / "elsewhere" / "z.mp4")
    episodes = episode_items(items[1], [*items, other])
    assert [item.path.name for item in episodes] == ["a.mp4", "b.mp4", "c.mp4"]
    assert other not in episodes


def test_episode_items_include_the_item_when_absent(folder) -> None:
    lone = _item(folder / "solo.mp4")
    episodes = episode_items(lone, [])
    assert episodes == (lone,)


# -- detail -----------------------------------------------------------------


def test_detail_view_facts_come_from_probe_and_size(folder, items) -> None:
    view = detail_view(items[0], items, hw_ready=True, t=T)
    assert [fact.key for fact in view.facts[:-1]] == list(DETAIL_FACT_KEYS)
    values = {fact.key: fact.value for fact in view.facts}
    assert values["spec_codec"] == "H.264"
    assert values["spec_picture"] == "1920 × 1080"
    assert values["spec_fps"] == "30.000 fps"
    assert values["spec_duration"] == "01:00"
    assert values["spec_size"] == "1.9 MB"
    # 2 MB * 8 / 60 s = 0.27 Mbps
    assert values["spec_bitrate"].endswith("Mbps")
    decode = view.facts[-1]
    assert decode.key == "spec_decode"
    assert decode.value == T("spec_decode_hw")
    assert decode.accent is True


def test_detail_view_reports_software_decode_without_the_vpu(items) -> None:
    view = detail_view(items[0], items, hw_ready=False, t=T)
    assert view.facts[-1].value == T("spec_decode_sw")
    assert view.facts[-1].accent is False


def test_detail_view_episode_grid_marks_the_current_clip(items) -> None:
    view = detail_view(items[2], items, t=T)
    assert [ep.item.path.name for ep in view.episodes] == ["a.mp4", "b.mp4", "c.mp4"]
    assert view.index == 2
    assert view.current.item.path.name == "c.mp4"
    assert [ep.item.path.name for ep in view.siblings] == ["a.mp4", "b.mp4"]
    assert view.total_size == 6_000_000
    assert view.folder_name == "片库"


def test_detail_view_resume_state_uses_history(items) -> None:
    history = [
        RecentItem(path=items[0].path, duration_ns=60_000_000_000, position_ns=15_000_000_000),
        RecentItem(path=items[2].path, duration_ns=60_000_000_000, position_ns=60_000_000_000),
    ]
    view = detail_view(items[0], items, recents=history, hw_ready=True, t=T)
    assert view.progress == pytest.approx(0.25)
    assert view.remaining_ns == 45_000_000_000
    assert not view.finished
    assert view.episodes[1].progress == pytest.approx(0.25)
    assert view.episodes[2].finished


def test_detail_view_unknown_metadata_reads_as_dashes(folder) -> None:
    bare = _item(folder / "bare.mp4", size=0, metadata=None)
    view = detail_view(bare, [bare], t=T)
    values = {fact.key: fact.value for fact in view.facts}
    assert values["spec_codec"] == "—"
    assert values["spec_picture"] == "—"
    assert values["spec_fps"] == "—"
    assert values["spec_duration"] == "—"
    assert values["spec_size"] == "—"
    assert values["spec_bitrate"] == "—"
    assert view.episodes == (view.current,)


def test_detail_view_accepts_an_english_translator(items) -> None:
    view = detail_view(items[0], items, hw_ready=True, t=Translator("en"))
    assert view.facts[-1].value == "VPU decode"


# -- helpers ----------------------------------------------------------------


@pytest.mark.parametrize(
    ("codec", "ready", "expected"),
    [
        ("h264", True, True),
        ("x-h265", True, True),
        ("hevc", True, True),
        ("vp9", True, False),
        ("h264", False, False),
        ("", True, False),
    ],
)
def test_hardware_decodable(codec, ready, expected) -> None:
    assert hardware_decodable(codec, ready) is expected


@pytest.mark.parametrize(
    ("size", "duration", "expected"),
    [
        (0, 0, "—"),
        (1_000_000, 0, "—"),
        (66_742_724, 37_650_000_000, "14.2 Mbps"),
        (4_000_000, 10_000_000_000, "3.2 Mbps"),
        (12_000_000, 10_000_000_000, "9.6 Mbps"),
    ],
)
def test_bitrate_text(size, duration, expected) -> None:
    assert bitrate_text(size, duration) == expected


def test_remaining_text() -> None:
    assert remaining_text(0, 0) == ""
    assert remaining_text(9_000_000_000, 30_000_000_000) == "00:21"
    assert remaining_text(40_000_000_000, 30_000_000_000) == "00:00"
    # Sub-second remainders round up instead of showing a full bar at 00:00.
    assert remaining_text(6_218_667_000, 7_066_667_000) == "00:01"
    assert remaining_text(6_000_000_000, 6_000_000_000) == "00:00"


def test_resume_needs_a_meaningful_position(items) -> None:
    # Below one second "continue at 00:00" is noise; a finished clip starts over.
    tiny = RecentItem(path=items[0].path, duration_ns=60_000_000_000, position_ns=500_000_000)
    real = RecentItem(path=items[0].path, duration_ns=60_000_000_000, position_ns=5_000_000_000)
    done = RecentItem(path=items[0].path, duration_ns=60_000_000_000, position_ns=59_000_000_000)
    assert not home_view(items, [tiny]).continue_cards[0].resumable
    assert home_view(items, [real]).continue_cards[0].resumable
    assert home_view(items, [real]).hero_is_resume
    assert not home_view(items, [done]).continue_cards[0].resumable
    assert RESUME_MIN_NS == 1_000_000_000
    assert not detail_view(items[0], items, recents=[tiny], t=T).resumable
    assert detail_view(items[0], items, recents=[real], t=T).resumable
    assert not detail_view(items[0], items, recents=[done], t=T).resumable


def test_progress_map_last_entry_wins() -> None:
    old = RecentItem(path=Path("/a.mp4"), position_ns=1, duration_ns=10)
    new = RecentItem(path=Path("/a.mp4"), position_ns=5, duration_ns=10)
    assert progress_map([old, new])[Path("/a.mp4")].position_ns == 5


def test_detail_fact_is_a_value_object() -> None:
    fact = DetailFact("spec_codec", "H.264")
    assert fact == DetailFact("spec_codec", "H.264")
    assert fact.accent is False
