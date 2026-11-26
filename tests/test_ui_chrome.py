"""Pure presentation helpers: codec labels, geometry and chrome visibility."""

from __future__ import annotations

import pytest

from video_player.ui.chrome import (
    BEAUTY_PAGE_OFF_OPACITY,
    BEAUTY_SPLIT_OFF_OPACITY,
    FACE_CORNER_ARM,
    beauty_page_state,
    chrome_state,
    contain_rect,
    corner_arms,
    format_clock,
    format_duration_ns,
    format_parameter,
    format_resolution,
    format_size,
    normalise_codec,
    percent_value_text,
    slider_value_text,
    video_meta_text,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("X-H264", "H.264"),
        ("x-h264", "H.264"),
        ("H264", "H.264"),
        ("h264", "H.264"),
        ("avc1", "H.264"),
        ("X-H265", "H.265"),
        ("x-h265", "H.265"),
        ("HEVC", "H.265"),
        ("hvc1", "H.265"),
        ("VP9", "VP9"),
        ("—", "—"),
        ("", "—"),
        (None, "—"),
    ],
)
def test_codec_names_are_normalised_for_the_ui(raw, expected) -> None:
    assert normalise_codec(raw) == expected


def test_chrome_state_hides_transport_without_media() -> None:
    empty = chrome_state(has_media=False, status=None)
    assert empty.empty
    assert not empty.transport
    assert not empty.info

    error = chrome_state(has_media=False, status="error")
    assert not error.empty, "an error card must not stack on the drop zone"
    assert not error.transport

    playing = chrome_state(has_media=True, status=None)
    assert not playing.empty
    assert playing.transport
    assert playing.info


def test_contain_rect_letterboxes_16_by_9_in_16_by_10() -> None:
    x, y, width, height = contain_rect(1920, 1080, 1280, 800)
    assert (x, y) == pytest.approx((0.0, 40.0))
    assert (width, height) == pytest.approx((1280.0, 720.0))


def test_contain_rect_letterboxes_pillarboxed_frame() -> None:
    x, y, width, height = contain_rect(1080, 1920, 1280, 800)
    assert (x, y) == pytest.approx((415.0, 0.0))
    assert (width, height) == pytest.approx((450.0, 800.0))


def test_contain_rect_degenerate_inputs_fall_back_to_area() -> None:
    assert contain_rect(0, 0, 640, 360) == (0.0, 0.0, 640.0, 360.0)
    assert contain_rect(1920, 1080, 0, 0) == (0.0, 0.0, 0.0, 0.0)


def test_corner_arms_are_eight_segments_at_the_four_corners() -> None:
    arms = corner_arms(10.0, 20.0, 100.0, 80.0, 22.0)
    assert len(arms) == 8
    # Top-left horizontal and vertical, top-right horizontal and vertical.
    assert arms[0] == (10.0, 20.0, 32.0, 20.0)
    assert arms[1] == (10.0, 20.0, 10.0, 42.0)
    assert arms[2] == (88.0, 20.0, 110.0, 20.0)
    assert arms[3] == (110.0, 20.0, 110.0, 42.0)
    # Bottom-right and bottom-left mirror the same shape.
    assert arms[4] == (88.0, 100.0, 110.0, 100.0)
    assert arms[6] == (10.0, 100.0, 32.0, 100.0)


def test_corner_arm_is_clamped_for_a_tiny_face() -> None:
    arms = corner_arms(0.0, 0.0, 12.0, 8.0)
    assert arms[0] == (0.0, 0.0, 4.0, 0.0)
    assert all(
        0.0 <= x1 <= 12.0 and 0.0 <= y1 <= 8.0 for x1, y1, _, _ in arms
    )
    assert FACE_CORNER_ARM == 22.0


def test_format_parameter_drops_trailing_zeroes() -> None:
    assert format_parameter(0.35) == "0.35"
    assert format_parameter(0.2) == "0.2"
    assert format_parameter(0.0) == "0"
    assert format_parameter(1.0) == "1"


def test_slider_value_labels_match_the_mockup() -> None:
    assert slider_value_text(60) == "60"
    assert slider_value_text(39.6) == "40"
    assert percent_value_text(0.5) == "50%"
    assert percent_value_text(0.125) == "12%"


def test_beauty_page_state_greys_everything_when_beauty_is_off() -> None:
    state = beauty_page_state(
        enabled=False, compare_mode=True, show_faces=True
    )
    assert state.page_off
    assert not state.body_sensitive
    assert state.body_opacity == BEAUTY_PAGE_OFF_OPACITY == 0.34
    assert state.cascade_visible
    assert not state.note_visible
    # The cascade promise: a stale config never lights A|B or the boxes.
    assert state.compare is False
    assert state.faces is False
    assert not state.split_sensitive
    assert state.split_opacity == BEAUTY_SPLIT_OFF_OPACITY == 0.4
    assert not state.dock_compare_sensitive
    assert state.dock_compare_label == "compare_needs_beauty"


def test_beauty_page_state_is_live_but_split_waits_for_ab() -> None:
    state = beauty_page_state(
        enabled=True, compare_mode=False, show_faces=False
    )
    assert not state.page_off
    assert state.body_sensitive
    assert state.body_opacity == 1.0
    assert not state.cascade_visible
    assert state.note_visible
    assert not state.split_sensitive
    assert state.split_opacity == BEAUTY_SPLIT_OFF_OPACITY
    assert state.dock_compare_sensitive
    assert state.dock_compare_label == "compare"


def test_beauty_page_state_full_split_is_sensitive() -> None:
    state = beauty_page_state(enabled=True, compare_mode=True, show_faces=True)
    assert state.compare and state.faces
    assert state.split_sensitive
    assert state.split_opacity == 1.0


# -- library presentation helpers ------------------------------------------


@pytest.mark.parametrize(
    ("nanoseconds", "expected"),
    [
        (0, "00:00"),
        (9_000_000_000, "00:09"),
        (59 * 10**9, "00:59"),
        (11 * 60 * 10**9 + 24 * 10**9, "11:24"),
        (3_600 * 10**9, "1:00:00"),
        (2 * 3600 * 10**9 + 5 * 60 * 10**9 + 7 * 10**9, "2:05:07"),
        (-5, "00:00"),
    ],
)
def test_format_duration_ns(nanoseconds, expected) -> None:
    assert format_duration_ns(nanoseconds) == expected


@pytest.mark.parametrize(
    ("size", "expected"),
    [
        (0, "—"),
        (-1, "—"),
        (512, "512 B"),
        (1024, "1.0 KB"),
        (1_500_000, "1.4 MB"),
        (66_742_724, "63.7 MB"),
        (2 * 1024**3, "2.0 GB"),
    ],
)
def test_format_size(size, expected) -> None:
    assert format_size(size) == expected


def test_format_clock_handles_missing_values() -> None:
    assert format_clock(0.0) == "—"
    clock = format_clock(1_700_000_000.0)
    assert len(clock) == 5 and clock[2] == ":"


@pytest.mark.parametrize(
    ("width", "height", "fps", "expected"),
    [
        (1920, 1080, 30, "1080p30"),
        (1920, 1080, 29.97, "1080p30"),
        (1280, 720, 0, "720p"),
        (1280, 800, 0, "1280×800"),
        (0, 0, 0, "—"),
    ],
)
def test_format_resolution(width, height, fps, expected) -> None:
    assert format_resolution(width, height, fps) == expected


def test_video_meta_text_joins_known_parts_only() -> None:
    assert (
        video_meta_text(width=1920, height=1080, fps=30, codec="h265", size=66_742_724)
        == "1080p30 · H.265 · 63.7 MB"
    )
    assert video_meta_text(size=1024) == "1.0 KB"
    assert video_meta_text() == "—"
    # The probe stores the caps subtype; the display form comes from the same
    # normaliser the live info row uses.
    assert video_meta_text(width=1280, height=720, codec="vp9") == "720p · VP9"
