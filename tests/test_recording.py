"""Unit tests for the recording geometry/canvas helpers.

``video_player.recording`` needs GStreamer *and* numpy, which the desktop CI
box may not have; both are skipped gracefully so the rest of the suite still
runs there.  Only pure helpers are covered here -- the GStreamer graph itself is
exercised on the board via ``--demo --record``.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

pytest.importorskip("gi")
pytest.importorskip("numpy")

gi = pytest.importorskip("gi")
gi.require_version("Gst", "1.0")
gi.require_version("GstVideo", "1.0")

import numpy as np
from gi.repository import Gst, GstVideo  # noqa: E402

from video_player.recording import (  # noqa: E402
    DEFAULT_BITRATE,
    FACE_BOX_COLOR,
    ORIENTATION_METHODS,
    Rect,
    Recorder,
    RecordingError,
    canvas_from_rgba,
    draw_face_boxes,
    even_size,
    face_geometry,
    fit_rect,
)


@dataclass(frozen=True)
class FakeFace:
    x: float
    y: float
    width: float
    height: float
    landmarks: tuple = ()


class TestRect:
    def test_right_and_bottom(self):
        rect = Rect(3, 4, 10, 20)
        assert rect.right == 13
        assert rect.bottom == 24

    def test_clip_inside_is_unchanged(self):
        assert Rect(2, 3, 4, 5).clip(100, 100) == Rect(2, 3, 4, 5)

    def test_clip_trims_negative_origin(self):
        assert Rect(-5, -6, 20, 20).clip(100, 100) == Rect(0, 0, 15, 14)

    def test_clip_trims_oversized_rect(self):
        assert Rect(80, 90, 40, 40).clip(100, 100) == Rect(80, 90, 20, 10)

    def test_clip_returns_none_when_outside(self):
        assert Rect(200, 0, 10, 10).clip(100, 100) is None
        assert Rect(0, -50, 10, 20).clip(100, 100) is None
        assert Rect(0, 0, 0, 10).clip(100, 100) is None


class TestEvenSize:
    def test_rounds_down_to_even(self):
        assert even_size(1281, 801) == (1280, 800)

    def test_keeps_even_values(self):
        assert even_size(1280, 720) == (1280, 720)

    def test_never_returns_zero(self):
        assert even_size(1, 0) == (2, 2)


class TestFitRect:
    def test_landscape_into_wider_window_is_pillarboxed(self):
        # 1920x1080 into a 924x690 area -> limited by width.
        assert fit_rect(1920, 1080, Rect(8, 8, 924, 690)) == Rect(8, 93, 924, 520)

    def test_portrait_into_landscape_area_is_letterboxed(self):
        # 720x1280 into a 924x690 area -> limited by height.
        assert fit_rect(720, 1280, Rect(8, 8, 924, 690)) == Rect(276, 8, 388, 690)

    def test_small_frame_is_centred(self):
        assert fit_rect(640, 480, Rect(8, 8, 924, 690)) == Rect(10, 8, 920, 690)

    def test_degenerate_sizes_return_empty_rect(self):
        empty = fit_rect(0, 1080, Rect(8, 8, 924, 690))
        assert empty.width == 0 and empty.height == 0
        assert fit_rect(1920, 1080, Rect(8, 8, 0, 0)).width == 0

    def test_matches_face_geometry_offset(self):
        area = Rect(8, 8, 924, 690)
        rect = fit_rect(1920, 1080, area)
        (box, _), = face_geometry([FakeFace(0, 0, 1920, 1080)], 1920, 1080, area)
        # face_geometry keeps the exact (fractional) fit; fit_rect rounds it.
        assert box[0] == pytest.approx(rect.x, abs=0.5)
        assert box[1] == pytest.approx(rect.y, abs=0.5)
        assert box[2] == pytest.approx(rect.width, abs=1.0)
        assert box[3] == pytest.approx(rect.height, abs=1.0)


class TestFaceGeometry:
    def test_maps_box_and_landmarks(self):
        area = Rect(8, 8, 924, 690)
        faces = [FakeFace(100, 100, 200, 200, landmarks=((150, 150),))]
        (box, landmarks), = face_geometry(faces, 1920, 1080, area)
        scale = 924 / 1920
        offset_y = 8 + (690 - 1080 * scale) / 2
        assert box == pytest.approx((8 + 100 * scale, offset_y + 100 * scale, 200 * scale, 200 * scale))
        assert landmarks[0][0] == pytest.approx(8 + 150 * scale)
        assert landmarks[0][1] == pytest.approx(offset_y + 150 * scale)

    def test_rejects_degenerate_input(self):
        area = Rect(0, 0, 100, 100)
        assert face_geometry([FakeFace(1, 2, 3, 4)], 0, 1080, area) == []
        assert face_geometry([FakeFace(1, 2, 3, 4)], 1920, 1080, Rect(0, 0, 0, 0)) == []

    def test_no_faces_is_empty(self):
        assert face_geometry([], 1920, 1080, Rect(0, 0, 100, 100)) == []


class TestDrawFaceBoxes:
    def make_canvas(self, size=100):
        return np.zeros((size, size, 4), dtype=np.uint8)

    def test_draws_all_four_border_edges(self):
        canvas = self.make_canvas()
        draw_face_boxes(canvas, [FakeFace(10, 10, 40, 40)], 100, 100, Rect(0, 0, 100, 100))
        colour = np.array(FACE_BOX_COLOR[:3])
        # top edge, bottom edge, left edge, right edge (rect 10..49)
        np.testing.assert_array_equal(canvas[10, 25, :3], colour)
        np.testing.assert_array_equal(canvas[49, 25, :3], colour)
        np.testing.assert_array_equal(canvas[25, 10, :3], colour)
        np.testing.assert_array_equal(canvas[25, 49, :3], colour)

    def test_leaves_interior_untouched(self):
        canvas = self.make_canvas()
        draw_face_boxes(canvas, [FakeFace(10, 10, 40, 40)], 100, 100, Rect(0, 0, 100, 100))
        assert canvas[30, 30, 3] == 0

    def test_draws_landmarks(self):
        canvas = self.make_canvas()
        face = FakeFace(10, 10, 40, 40, landmarks=((30, 30),))
        draw_face_boxes(canvas, [face], 100, 100, Rect(0, 0, 100, 100))
        assert canvas[30, 30, 3] == FACE_BOX_COLOR[3]

    def test_clips_boxes_at_canvas_edge(self):
        canvas = self.make_canvas()
        draw_face_boxes(canvas, [FakeFace(-20, -20, 40, 40)], 100, 100, Rect(0, 0, 100, 100))
        assert canvas[:, :, 3].max() == FACE_BOX_COLOR[3]

    def test_skips_degenerate_boxes(self):
        canvas = self.make_canvas()
        draw_face_boxes(canvas, [FakeFace(10, 10, 0, 0)], 100, 100, Rect(0, 0, 100, 100))
        assert canvas[:, :, 3].max() == 0


class TestCanvasFromRgba:
    def test_accepts_contiguous_rgba(self):
        image = np.zeros((4, 5, 4), dtype=np.uint8)
        array = canvas_from_rgba(image)
        assert array.shape == (4, 5, 4)
        assert array.dtype == np.uint8
        assert array.flags["C_CONTIGUOUS"]

    def test_makes_non_contiguous_input_contiguous(self):
        image = np.zeros((8, 8, 4), dtype=np.uint8)[::2, ::2]
        array = canvas_from_rgba(image)
        assert array.shape == (4, 4, 4)
        assert array.flags["C_CONTIGUOUS"]

    def test_rejects_wrong_channels(self):
        with pytest.raises(ValueError):
            canvas_from_rgba(np.zeros((4, 5, 3), dtype=np.uint8))

    def test_rejects_2d_input(self):
        with pytest.raises(ValueError):
            canvas_from_rgba(np.zeros((4, 5), dtype=np.uint8))


class _ProbeInfo:
    """Minimal ``Gst.PadProbeInfo`` stand-in that only carries a buffer."""

    def __init__(self, buffer):
        self._buffer = buffer

    def get_buffer(self):
        return self._buffer


class TestRecorderTiming:
    """The recorder only needs its pipeline/tee when ``start()`` runs."""

    def test_pushes_one_ui_frame_per_video_frame(self):
        recorder = Recorder(None, None, "/tmp/x.mp4", canvas_size=(4, 4))
        recorder._running = True
        recorder._canvas = Gst.Buffer.new_wrapped(b"\x00" * 64)
        pushes = []

        class FakeSrc:
            def emit(self, name, frame=None):
                # Read the metadata right away: the shallow copies share their
                # allocation, so keeping the buffer around would print the
                # latest PTS for every entry.
                pushes.append((name, frame.pts, frame.duration))
                return Gst.FlowReturn.OK

        recorder._ui_src = FakeSrc()
        for pts in (0, 33_333_333, 66_666_666):
            buffer = Gst.Buffer.new_wrapped(b"\x00" * 16)
            buffer.pts = pts
            recorder._on_video_buffer(None, _ProbeInfo(buffer))

        assert [name for name, _, _ in pushes] == ["push-buffer"] * 3
        assert [pts for _, pts, _ in pushes] == [0, 33_333_333, 66_666_666]
        assert all(duration == Gst.util_uint64_scale(1, Gst.SECOND, 30)
                   for _, _, duration in pushes)
        assert recorder.stats == (0, 3, 3)

    def test_does_not_push_before_the_first_canvas(self):
        recorder = Recorder(None, None, "/tmp/x.mp4", canvas_size=(4, 4))
        recorder._running = True
        recorder._ui_src = None
        buffer = Gst.Buffer.new_wrapped(b"\x00" * 16)
        buffer.pts = 0
        recorder._on_video_buffer(None, _ProbeInfo(buffer))
        assert recorder.stats == (0, 0, 1)

    def test_canvas_size_is_even(self):
        recorder = Recorder(None, None, "/tmp/x.mp4", canvas_size=(1281, 801))
        assert recorder.canvas_size == (1280, 800)

    def test_default_geometry_covers_the_canvas(self):
        recorder = Recorder(None, None, "/tmp/x.mp4", canvas_size=(1280, 800))
        assert recorder.video_rect == Rect(0, 0, 1280, 800)

    def test_path_is_exposed(self):
        recorder = Recorder(None, None, "/tmp/out.mp4", canvas_size=(640, 480))
        assert recorder.path.name == "out.mp4"
        assert recorder.is_recording is False
        assert recorder._bitrate == DEFAULT_BITRATE

    def test_rotation_defaults_to_none(self):
        recorder = Recorder(None, None, "/tmp/x.mp4", canvas_size=(640, 480))
        assert recorder.rotation == 0

    @pytest.mark.parametrize("rotation", [0, 90, 180, 270, -90])
    def test_rotation_is_normalised(self, rotation):
        recorder = Recorder(
            None, None, "/tmp/x.mp4", canvas_size=(640, 480), rotation=rotation
        )
        assert recorder.rotation == rotation % 360

    @pytest.mark.parametrize("rotation,method", [(90, "90R"), (180, "180"), (270, "90L")])
    def test_rotation_maps_to_a_flip_method(self, rotation, method):
        assert ORIENTATION_METHODS[rotation] == getattr(
            GstVideo.VideoOrientationMethod, method
        )

    def test_rejects_unsupported_rotation(self):
        with pytest.raises(RecordingError):
            Recorder(None, None, "/tmp/x.mp4", canvas_size=(640, 480), rotation=45)


class TestVideoOnlyMode:
    """The pure-video recorder writes the decoded picture without a compositor."""

    def test_video_size_becomes_the_canvas(self):
        recorder = Recorder(
            None, None, "/tmp/x.mp4", video_only=True, video_size=(1921, 1081)
        )
        assert recorder.video_only
        assert recorder.canvas_size == (1920, 1080)

    def test_missing_video_size_is_clamped_like_any_canvas(self):
        recorder = Recorder(None, None, "/tmp/x.mp4", video_only=True)
        assert recorder.canvas_size == (2, 2)

    def test_overlay_mode_requires_a_canvas(self):
        recorder = Recorder(None, None, "/tmp/x.mp4", video_only=False, video_size=(1920, 1080))
        # the video size doubles as the canvas when no canvas is given
        assert recorder.canvas_size == (1920, 1080)

    def test_rotation_is_kept_for_logging_but_not_required(self):
        recorder = Recorder(
            None, None, "/tmp/x.mp4", video_only=True, video_size=(640, 480), rotation=180
        )
        assert recorder.rotation == 180
