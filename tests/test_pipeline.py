"""Tests for the M1 element selection (demuxer / parser / decoder / URI)."""

from __future__ import annotations

from pathlib import Path

import pytest

from video_player.pipeline import (
    DEMUXERS,
    PARSERS,
    SW_DECODERS,
    VIDEO_SUFFIXES,
    decoder_candidates,
    decoder_for,
    demux_for,
    is_stream_uri,
    parser_for,
    to_uri,
)


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("clip.mp4", "qtdemux"),
        ("clip.M4V", "qtdemux"),
        ("movie.mov", "qtdemux"),
        ("movie.qt", "qtdemux"),
        ("show.mkv", "matroskademux"),
        ("clip.webm", "matroskademux"),
        ("rec.ts", "tsdemux"),
        ("rec.m2ts", "tsdemux"),
        ("rec.mts", "tsdemux"),
    ],
)
def test_demux_for_picks_container_demuxer(name: str, expected: str) -> None:
    assert demux_for(Path("/tmp") / name) == expected


def test_demux_for_rejects_unknown_container() -> None:
    with pytest.raises(ValueError, match="unsupported container"):
        demux_for(Path("/tmp/clip.avi"))
    with pytest.raises(ValueError, match="unsupported container"):
        demux_for(Path("/tmp/clip"))


def test_demux_for_prefers_real_container_over_suffix(tmp_path: Path) -> None:
    """A renamed file must not select a demuxer that cannot parse byte 0."""
    mp4_as_mkv = tmp_path / "renamed.mkv"
    mp4_as_mkv.write_bytes(b"\x00\x00\x00\x18ftypisom" + b"\x00" * 8)
    assert demux_for(mp4_as_mkv) == "qtdemux"

    mkv_as_mp4 = tmp_path / "renamed.mp4"
    mkv_as_mp4.write_bytes(b"\x1a\x45\xdf\xa3" + b"\x00" * 12)
    assert demux_for(mkv_as_mp4) == "matroskademux"


def test_demux_for_keeps_suffix_when_content_unavailable() -> None:
    """Missing / unrecognised files keep the original pure suffix lookup."""
    assert demux_for(Path("/tmp/no-such-dir/clip.mkv")) == "matroskademux"
    assert demux_for(Path("/tmp/no-such-dir/clip.ts")) == "tsdemux"


def test_video_suffixes_match_demuxer_table() -> None:
    assert VIDEO_SUFFIXES == tuple(sorted(DEMUXERS))
    assert ".mp4" in VIDEO_SUFFIXES
    assert ".mkv" in VIDEO_SUFFIXES


@pytest.mark.parametrize(
    ("caps_name", "expected"),
    [
        ("video/x-h264", "h264parse"),
        ("video/x-h265", "h265parse"),
        ("video/x-vp9", "vp9parse"),
        ("video/x-av1", "av1parse"),
        ("video/x-mpeg2", "mpegvideoparse"),
        ("video/x-h263", "h263parse"),
        ("video/x-vp8", "vp8parse"),
    ],
)
def test_parser_for_known_codecs(caps_name: str, expected: str) -> None:
    assert parser_for(caps_name) == expected


def test_parser_for_unknown_codec_raises() -> None:
    with pytest.raises(ValueError, match="unsupported video codec"):
        parser_for("video/x-unknown")


def test_decoder_for_prefers_hardware_vpu_by_default() -> None:
    assert decoder_for("video/x-h264") == "mppvideodec"
    assert decoder_for("video/x-h265") == "mppvideodec"


def test_decoder_for_software_fallback() -> None:
    assert decoder_for("video/x-h264", hw_decode=False) == "avdec_h264"
    assert decoder_for("video/x-h265", hw_decode=False) == "avdec_h265"
    assert decoder_for("video/x-vp9", hw_decode=False) == "vp9dec"
    assert decoder_for("video/x-av1", hw_decode=False) == "dav1ddec"
    assert decoder_for("video/x-mpeg2", hw_decode=False) == "mpeg2dec"


def test_decoder_tables_are_covered_by_parsers() -> None:
    from video_player.pipeline import HW_DECODERS

    assert set(HW_DECODERS) <= set(PARSERS)
    assert set(SW_DECODERS) <= set(PARSERS)


def test_decoder_for_codec_without_decoder_raises() -> None:
    with pytest.raises(ValueError, match="unsupported video codec"):
        decoder_for("video/x-h263", hw_decode=False)


def test_to_uri_resolves_local_paths(tmp_path: Path) -> None:
    clip = tmp_path / "clip.mp4"
    clip.write_bytes(b"stub")
    assert to_uri(clip) == clip.resolve().as_uri()
    assert to_uri(str(clip)) == clip.resolve().as_uri()


def test_to_uri_passes_remote_uris_through() -> None:
    assert to_uri("https://example.com/live.m3u8") == "https://example.com/live.m3u8"


def test_is_stream_uri() -> None:
    assert is_stream_uri("https://example.com/live.m3u8")
    assert is_stream_uri("rtsp://camera.local/stream")
    assert is_stream_uri("file:///tmp/clip.mp4") is False
    assert is_stream_uri(Path("/tmp/clip.mp4")) is False


def test_decoder_candidates_auto_prefers_hardware_then_software() -> None:
    assert decoder_candidates("video/x-h264", "auto") == ("mppvideodec", "avdec_h264")
    assert decoder_candidates("video/x-h265") == ("mppvideodec", "avdec_h265")
    # VP9 has no VPU decoder, so auto is software-only instead of an error.
    assert decoder_candidates("video/x-vp9", "auto") == ("vp9dec",)


def test_decoder_candidates_forced_modes_offer_a_single_factory() -> None:
    assert decoder_candidates("video/x-h264", "hw") == ("mppvideodec",)
    assert decoder_candidates("video/x-h264", "sw") == ("avdec_h264",)
    with pytest.raises(ValueError, match="unsupported video codec"):
        decoder_candidates("video/x-vp9", "hw")


def test_decoder_candidates_reject_unknown_mode() -> None:
    with pytest.raises(ValueError, match="unknown decode mode"):
        decoder_candidates("video/x-h264", "gpu")
