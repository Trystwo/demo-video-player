"""Optional chapter sidecar parsing and timeline projection."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from video_player.ui.chapters import (
    MAX_CHAPTERS,
    Chapter,
    ChapterMark,
    chapter_marks,
    load_chapters,
    parse_chapters,
    sidecar_path,
)


def test_sidecar_name_keeps_the_clip_suffix() -> None:
    assert sidecar_path("/lib/clip.mp4") == Path("/lib/clip.mp4.chapters.json")
    assert sidecar_path(Path("/lib/a.b.mkv")).name == "a.b.mkv.chapters.json"


def test_parse_accepts_the_documented_object_and_a_bare_list() -> None:
    chapters = parse_chapters(
        {"chapters": [{"start": 12.5, "title": "  Act two  "}, {"start": 3}]}
    )
    assert chapters == (Chapter(3_000_000_000, ""), Chapter(12_500_000_000, "Act two"))
    bare = parse_chapters([{"start_ns": 2_000_000_000, "title": "intro"}])
    assert bare == (Chapter(2_000_000_000, "intro"),)


def test_parse_drops_bad_entries_and_deduplicates() -> None:
    chapters = parse_chapters(
        {
            "chapters": [
                {"start": 5},
                {"start": -1},
                {"start": "nope"},
                {"title": "missing start"},
                "not-a-dict",
                {"start": 5, "title": "duplicate"},
                {"start": 1},
            ]
        }
    )
    assert chapters == (Chapter(1_000_000_000, ""), Chapter(5_000_000_000, ""))


def test_parse_caps_the_number_of_marks() -> None:
    payload = [{"start": index} for index in range(MAX_CHAPTERS + 20)]
    chapters = parse_chapters(payload)
    assert len(chapters) == MAX_CHAPTERS
    assert chapters[0].start_ns == 0


def test_parse_rejects_non_list_payloads() -> None:
    for payload in (None, 3, "chapters", {"chapters": {"start": 1}}):
        assert parse_chapters(payload) == ()


def test_load_ignores_missing_and_broken_sidecars(tmp_path: Path) -> None:
    clip = tmp_path / "clip.mp4"
    clip.write_bytes(b"")
    assert load_chapters(clip) == ()

    sidecar_path(clip).write_text("{not json", encoding="utf-8")
    assert load_chapters(clip) == ()

    sidecar_path(clip).write_text(
        json.dumps({"chapters": [{"start": 2, "title": "two"}]}), encoding="utf-8"
    )
    assert load_chapters(clip) == (Chapter(2_000_000_000, "two"),)


def test_marks_project_onto_the_clip_and_drop_the_edges() -> None:
    chapters = (
        Chapter(0),
        Chapter(15_000_000_000),
        Chapter(30_000_000_000),
        Chapter(45_000_000_000),
        Chapter(60_000_000_000),
    )
    marks = chapter_marks(chapters, 60_000_000_000)
    assert [round(mark.fraction, 3) for mark in marks] == [0.25, 0.5, 0.75]
    assert chapter_marks(chapters, 0) == ()


def test_marks_keep_order_and_titles() -> None:
    marks = chapter_marks(
        [Chapter(20_000_000_000, "end"), Chapter(10_000_000_000, "start")],
        40_000_000_000,
    )
    assert marks == (
        ChapterMark(0.25, "start"),
        ChapterMark(0.5, "end"),
    )
    # A ChapterMark input is passed through; this is what the timeline holds.
    assert chapter_marks((ChapterMark(0.4, "x"),), 1_000) == (
        ChapterMark(0.4, "x"),
    )


@pytest.mark.parametrize("fraction", [float("nan"), float("inf")])
def test_non_finite_mark_fractions_are_dropped(fraction: float) -> None:
    assert chapter_marks((ChapterMark(fraction, "x"),), 1_000) == ()
