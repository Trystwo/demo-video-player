"""Tests for sidecar subtitle parsing and lookup (F5)."""

from __future__ import annotations

from pathlib import Path

from video_player.subtitle import Cue, SubtitleTrack, find_sidecar, load_srt, parse_srt

SECOND = 1_000_000_000

SAMPLE = """1
00:00:01,000 --> 00:00:03,500
第一句字幕

2
00:00:04,000 --> 00:00:06,000
Second line
continues here
"""


# -- parsing ---------------------------------------------------------------


def test_parse_basic_cues() -> None:
    cues = parse_srt(SAMPLE)
    assert len(cues) == 2
    assert cues[0] == Cue(start_ns=1 * SECOND, end_ns=3_500_000_000, text="第一句字幕")
    assert cues[1].text == "Second line\ncontinues here"


def test_parse_tolerates_missing_sequence_numbers() -> None:
    cues = parse_srt("00:00:02,000 --> 00:00:03,000\nNo index\n")
    assert len(cues) == 1
    assert cues[0].start_ns == 2 * SECOND


def test_parse_tolerates_dot_milliseconds_and_short_clock() -> None:
    cues = parse_srt("00:01.500 --> 00:02.000\nshort form\n")
    assert len(cues) == 1
    assert cues[0].start_ns == 1_500_000_000
    assert cues[0].end_ns == 2 * SECOND


def test_parse_strips_markup() -> None:
    cues = parse_srt("1\n00:00:01,000 --> 00:00:02,000\n<i>italic</i> word\n")
    assert cues[0].text == "italic word"


def test_parse_sorts_out_of_order_blocks() -> None:
    text = (
        "2\n00:00:05,000 --> 00:00:06,000\nlate\n\n"
        "1\n00:00:01,000 --> 00:00:02,000\nearly\n"
    )
    assert [cue.text for cue in parse_srt(text)] == ["early", "late"]


def test_parse_skips_broken_blocks() -> None:
    text = SAMPLE + "\nnot a cue at all\n\n" + "3\n00:00:09,000 --> 00:00:09,000\nzero\n"
    cues = parse_srt(text)
    assert len(cues) == 2  # the junk block and the zero-length cue are dropped


def test_parse_handles_bom_and_crlf() -> None:
    raw = "\ufeff1\r\n00:00:01,000 --> 00:00:02,000\r\nhello\r\n"
    cues = parse_srt(raw)
    assert len(cues) == 1
    assert cues[0].text == "hello"


def test_parse_empty_input() -> None:
    assert parse_srt("") == []


# -- file loading ----------------------------------------------------------


def test_load_srt_decodes_gb18030(tmp_path: Path) -> None:
    path = tmp_path / "gbk.srt"
    path.write_bytes("1\n00:00:01,000 --> 00:00:02,000\n中文字幕\n".encode("gb18030"))
    cues = load_srt(path)
    assert cues[0].text == "中文字幕"


def test_load_srt_missing_file_is_empty(tmp_path: Path) -> None:
    assert load_srt(tmp_path / "nope.srt") == []


def test_find_sidecar_prefers_plain_srt(tmp_path: Path) -> None:
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"x")
    (tmp_path / "clip.zh.srt").write_text("", encoding="utf-8")
    assert find_sidecar(video) is None or find_sidecar(video).name == "clip.zh.srt"
    (tmp_path / "clip.srt").write_text("", encoding="utf-8")
    assert find_sidecar(video) == tmp_path / "clip.srt"


def test_find_sidecar_without_subtitles(tmp_path: Path) -> None:
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"x")
    assert find_sidecar(video) is None


# -- lookup ----------------------------------------------------------------


def test_text_at_boundaries() -> None:
    track = SubtitleTrack(parse_srt(SAMPLE))
    assert track.text_at(0) is None
    assert track.text_at(1 * SECOND) == "第一句字幕"  # start is inclusive
    assert track.text_at(3_499_000_000) == "第一句字幕"
    assert track.text_at(3_500_000_000) is None  # end is exclusive
    assert track.text_at(5 * SECOND).startswith("Second")


def test_text_at_empty_track() -> None:
    assert SubtitleTrack().text_at(5 * SECOND) is None


def test_offset_shifts_cues() -> None:
    track = SubtitleTrack(parse_srt(SAMPLE), offset_ns=2 * SECOND)
    assert track.text_at(1 * SECOND) is None
    assert track.text_at(3 * SECOND) == "第一句字幕"
    assert track.shifted(0).text_at(1 * SECOND) == "第一句字幕"


def test_lookup_is_logarithmic_for_large_tracks() -> None:
    cues = [Cue(start_ns=i * SECOND, end_ns=(i + 1) * SECOND, text=str(i)) for i in range(5000)]
    track = SubtitleTrack(cues)
    assert track.text_at(4321 * SECOND) == "4321"
    assert len(track) == 5000
