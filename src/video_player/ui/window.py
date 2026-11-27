"""GTK4 player window for M1.

Layout: embedded video surface (``gtk4paintablesink`` paintable), transport
bar, playlist sidebar and the beauty panel.  Playback is fully wired in this
module through :class:`video_player.backend.PlaybackBackend`; the beauty
controls only update the configuration until the GPU pipeline lands in M3.
"""

from __future__ import annotations

from pathlib import Path
import os
import time
import resource
import sys
from typing import Sequence

import gi

gi.require_version("Gdk", "4.0")
gi.require_version("Gtk", "4.0")
gi.require_version("Pango", "1.0")
gi.require_version("PangoCairo", "1.0")
from gi.repository import (  # noqa: E402
    Gdk,
    Gio,
    GLib,
    Gtk,
    Pango,
    PangoCairo,
)

from ..backend import PlaybackBackend, PlaybackError  # noqa: E402
from ..beauty import BeautyParams  # noqa: E402
from ..config import Config  # noqa: E402
from ..diagnostics import install_crash_logging, log_action  # noqa: E402
from ..face import DetectionResult, Face  # noqa: E402
from ..i18n import LANGUAGES, Translator, save_language  # noqa: E402
from ..pipeline import DECODE_MODES, VIDEO_SUFFIXES  # noqa: E402
from ..playlist import Playlist, expand_media_args  # noqa: E402
from ..recents import RecentItem, RecentsStore  # noqa: E402
from ..resume import ResumeStore  # noqa: E402
from ..subtitle import SubtitleTrack, find_sidecar, load_srt  # noqa: E402
from .chrome import (  # noqa: E402
    FACE_CORNER_ARM,
    beauty_page_state,
    chrome_state,
    contain_rect,
    corner_arms,
    normalise_codec,
    percent_value_text,
    slider_value_text,
)
from .browse import BrowseShell  # noqa: E402
from .chapters import Chapter, chapter_marks, load_chapters  # noqa: E402
from .player_chrome import (  # noqa: E402
    CONTROL_HEIGHT,
    PLAY_BUTTON_SIZE,
    TIMELINE_HEIGHT,
    ChapterTimeline,
    DrawerTabRule,
    PlayerTopBar,
    TelemetryBar,
)
from .style import CSS, rgba  # noqa: E402
from .taps import DOUBLE_TAP_MS, DOUBLE, TapTracker  # noqa: E402

APP_ID = "io.quectel.pi.VideoPlayer"
GST_SECOND = 1_000_000_000

SPEED_CHOICES = ("0.5×", "0.75×", "1.0×", "1.25×", "1.5×", "2.0×")
SPEED_VALUES = (0.5, 0.75, 1.0, 1.25, 1.5, 2.0)

#: Attempts to attach the recording branch; the first ones can fail while the
#: pipeline is still bringing up its GL context.
RECORD_START_RETRIES = 10

#: How often the playback position is persisted while playing.  Two seconds is
#: well below the 10 s minimum the store keeps, and cheap enough to ignore.
RESUME_SAVE_INTERVAL = 2.0

#: Pointer band at the bottom of the window that counts as "over the dock".
#: The fullscreen dock measures 120 px on the board (plus a 12 px inset), so
#: 140 px catches the pointer just before it reaches the controls without
#: keeping the HUD up from mid-screen.
DOCK_HOVER_BAND = 170

#: Subtitle lift while the dock is on screen: the dock is an overlay child
#: added after the subtitle layer, so without this the bar would cover it.
#: The P4 dock is ~150 px tall (telemetry + 20 px rail + 52 px play row).
DOCK_SUBTITLE_CLEARANCE = 168

#: Idle seconds before the fullscreen transport fades away.  Long enough to
#: read the timecode after a seek, short enough that it is gone by the time the
#: viewer looks back at the picture.
DOCK_IDLE_SECONDS = 2.5

#: The fullscreen telemetry line stays readable after the dock fades, but it
#: must not compete with the picture; 62 % matches the v3 mockup.
TELEMETRY_IDLE_OPACITY = 0.62

#: Top-level pages of the ui-v5 shell: the library browser and the player.
PAGE_NAMES = ("browse", "player")

#: The ui-v5 palette tokens and the widget rules live in
#: :mod:`video_player.ui.style` (single source for CSS and the Cairo overlays).


class _FaceOverlay(Gtk.DrawingArea):
    """Transparent canvas that draws detected faces on top of the video.

    ``Gtk.Picture`` uses :attr:`Gtk.ContentFit.CONTAIN`, so the video is scaled
    down and centred inside the widget allocation; the same transform is
    applied here to map frame coordinates to widget coordinates.  The box is a
    four-corner bracket instead of a full rectangle: it marks the face without
    drawing over the picture the way a full box did in earlier UIs.
    """

    def __init__(self) -> None:
        super().__init__()
        self.set_can_target(False)
        self.set_draw_func(self._draw)
        self._faces: tuple[Face, ...] = ()
        self._frame_width = 0.0
        self._frame_height = 0.0
        self._caption = "Face"

    def set_faces(
        self, faces: tuple[Face, ...], frame_width: float, frame_height: float
    ) -> None:
        self._faces = faces
        self._frame_width = frame_width
        self._frame_height = frame_height
        self.queue_draw()

    def set_caption(self, caption: str) -> None:
        self._caption = caption
        self.queue_draw()

    def _draw(
        self, _area: Gtk.DrawingArea, cr: object, width: int, height: int
    ) -> None:
        if not self._faces or self._frame_width <= 0 or self._frame_height <= 0:
            return
        origin_x, origin_y, display_w, display_h = contain_rect(
            self._frame_width, self._frame_height, float(width), float(height)
        )
        scale_x = display_w / self._frame_width
        scale_y = display_h / self._frame_height
        cr.set_line_width(2.0)  # type: ignore[attr-defined]
        cr.set_line_cap(1)  # type: ignore[attr-defined]  # cairo.LINE_CAP_ROUND
        # Pale steel blue: an annotation on the picture, not a second accent.
        cr.set_source_rgba(*rgba("annotation", 0.95))  # type: ignore[attr-defined]
        for face in self._faces:
            x = origin_x + face.x * scale_x
            y = origin_y + face.y * scale_y
            w = face.width * scale_x
            h = face.height * scale_y
            for x1, y1, x2, y2 in corner_arms(x, y, w, h, FACE_CORNER_ARM):
                cr.move_to(x1, y1)  # type: ignore[attr-defined]
                cr.line_to(x2, y2)  # type: ignore[attr-defined]
                cr.stroke()  # type: ignore[attr-defined]
            self._draw_confidence(cr, face, x, y, width, height)

    def _draw_confidence(
        self,
        cr: object,
        face: Face,
        x: float,
        y: float,
        width: int,
        height: int,
    ) -> None:
        text = f"{self._caption} {face.score:.2f}"
        layout = PangoCairo.create_layout(cr)  # type: ignore[arg-type]
        layout.set_text(text, -1)
        layout.set_font_description(Pango.FontDescription("Sans 9"))
        PangoCairo.update_layout(cr, layout)  # type: ignore[arg-type]
        text_w, text_h = layout.get_pixel_size()
        pad_x, pad_y = 6.0, 2.0
        box_w = text_w + pad_x * 2
        box_h = text_h + pad_y * 2
        box_x = max(0.0, min(x, width - box_w))
        box_y = y - box_h - 4.0
        if box_y < 0.0:
            box_y = min(float(height - box_h), y + 4.0)
        self._rounded_rect(cr, box_x, box_y, box_w, box_h, 5.0)
        cr.set_source_rgba(*rgba("bg", 0.84))  # type: ignore[attr-defined]
        cr.fill()  # type: ignore[attr-defined]
        cr.set_source_rgba(*rgba("annotation", 0.85))  # type: ignore[attr-defined]
        cr.move_to(box_x + pad_x, box_y + pad_y)  # type: ignore[attr-defined]
        PangoCairo.show_layout(cr, layout)  # type: ignore[arg-type]

    @staticmethod
    def _rounded_rect(
        cr: object, x: float, y: float, width: float, height: float, radius: float
    ) -> None:
        radius = max(0.0, min(radius, width / 2.0, height / 2.0))
        cr.new_sub_path()  # type: ignore[attr-defined]
        cr.arc(x + width - radius, y + radius, radius, -1.5707963, 0.0)  # type: ignore[attr-defined]
        cr.arc(x + width - radius, y + height - radius, radius, 0.0, 1.5707963)  # type: ignore[attr-defined]
        cr.arc(x + radius, y + height - radius, radius, 1.5707963, 3.1415927)  # type: ignore[attr-defined]
        cr.arc(x + radius, y + radius, radius, 3.1415927, 4.7123890)  # type: ignore[attr-defined]
        cr.close_path()  # type: ignore[attr-defined]


class _CompareOverlay(Gtk.DrawingArea):
    """Split-view captions: 原图 / 美颜 + the active beauty parameters.

    The shader owns the split line; this layer only annotates it, so the labels
    follow the same ``u_split`` fraction and the actual letterboxed video
    rectangle without touching the video pipeline.
    """

    def __init__(self) -> None:
        super().__init__()
        self.set_can_target(False)
        self.set_draw_func(self._draw)
        self._visible = False
        self._split = 0.5
        self._frame_size = (0.0, 0.0)
        self._labels = ("Original", "Beautified")
        self._params = ""
        self.set_visible(False)

    def set_state(
        self,
        *,
        visible: bool,
        split: float,
        frame_width: float,
        frame_height: float,
        labels: tuple[str, str],
        params: str,
    ) -> None:
        changed = (
            self._visible != visible
            or self._split != split
            or self._frame_size != (frame_width, frame_height)
            or self._labels != labels
            or self._params != params
        )
        self._visible = visible
        self._split = max(0.0, min(1.0, split))
        self._frame_size = (frame_width, frame_height)
        self._labels = labels
        self._params = params
        self.set_visible(visible)
        if changed:
            self.queue_draw()

    def _draw(
        self, _area: Gtk.DrawingArea, cr: object, width: int, height: int
    ) -> None:
        if not self._visible or self._frame_size[0] <= 0 or self._frame_size[1] <= 0:
            return
        origin_x, origin_y, display_w, display_h = contain_rect(
            self._frame_size[0],
            self._frame_size[1],
            float(width),
            float(height),
        )
        split_x = origin_x + display_w * self._split
        top = origin_y + 14.0
        gap = 8.0
        original = self._labels[0]
        beautified = self._labels[1]
        original_w = self._capsule_width(cr, original)
        beautified_w = self._capsule_width(cr, beautified)
        left_x = split_x - gap - original_w
        right_x = split_x + gap
        left_x = max(origin_x, left_x)
        right_x = min(origin_x + display_w - beautified_w, right_x)
        self._draw_capsule(cr, original, left_x, top)
        self._draw_capsule(cr, beautified, right_x, top)
        if self._params:
            layout = PangoCairo.create_layout(cr)  # type: ignore[arg-type]
            layout.set_text(self._params, -1)
            layout.set_font_description(Pango.FontDescription("Sans 9"))
            PangoCairo.update_layout(cr, layout)  # type: ignore[arg-type]
            text_w, _text_h = layout.get_pixel_size()
            # Bright content at the split line: a 1 px dark outline keeps the
            # caption readable without putting another panel on the picture.
            for dx, dy in ((-1.0, 0.0), (1.0, 0.0), (0.0, -1.0), (0.0, 1.0)):
                cr.set_source_rgba(*rgba("video", 0.72))  # type: ignore[attr-defined]
                cr.move_to(  # type: ignore[attr-defined]
                    split_x - text_w / 2.0 + dx, top + 34.0 + dy
                )
                PangoCairo.show_layout(cr, layout)  # type: ignore[arg-type]
            cr.set_source_rgba(*rgba("fg", 0.98))  # type: ignore[attr-defined]
            cr.move_to(split_x - text_w / 2.0, top + 34.0)  # type: ignore[attr-defined]
            PangoCairo.show_layout(cr, layout)  # type: ignore[arg-type]

    @staticmethod
    def _capsule_width(cr: object, text: str) -> float:
        layout = PangoCairo.create_layout(cr)  # type: ignore[arg-type]
        layout.set_text(text, -1)
        layout.set_font_description(Pango.FontDescription("Sans 9.5"))
        PangoCairo.update_layout(cr, layout)  # type: ignore[arg-type]
        text_w, _ = layout.get_pixel_size()
        return float(text_w + 24)

    def _draw_capsule(self, cr: object, text: str, x: float, y: float) -> None:
        layout = PangoCairo.create_layout(cr)  # type: ignore[arg-type]
        layout.set_text(text, -1)
        layout.set_font_description(Pango.FontDescription("Sans 9.5"))
        PangoCairo.update_layout(cr, layout)  # type: ignore[arg-type]
        text_w, text_h = layout.get_pixel_size()
        width = text_w + 24
        height = text_h + 10
        _FaceOverlay._rounded_rect(cr, x, y, width, height, 10.0)
        cr.set_source_rgba(*rgba("bg", 0.82))  # type: ignore[attr-defined]
        cr.fill()  # type: ignore[attr-defined]
        cr.set_source_rgba(*rgba("play", 0.14))  # type: ignore[attr-defined]
        cr.set_line_width(1.0)  # type: ignore[attr-defined]
        _FaceOverlay._rounded_rect(cr, x + 0.5, y + 0.5, width - 1, height - 1, 9.5)
        cr.stroke()  # type: ignore[attr-defined]
        cr.set_source_rgba(*rgba("annotation", 0.96))  # type: ignore[attr-defined]
        cr.move_to(x + 12.0, y + 5.0)  # type: ignore[attr-defined]
        PangoCairo.show_layout(cr, layout)  # type: ignore[arg-type]


class _ProgressLine(Gtk.DrawingArea):
    """Two-pixel progress hairline that survives the dock fade.

    A plain Gtk.ProgressBar picks up theme min-widths and logs "min width -2"
    warnings on GTK 4.14; the line is a single rect, so drawing it directly is
    both exact and quiet.
    """

    def __init__(self) -> None:
        super().__init__()
        self.set_can_target(False)
        self.set_draw_func(self._draw)
        self.set_size_request(-1, 2)
        self._fraction = 0.0

    def set_fraction(self, fraction: float) -> None:
        self._fraction = max(0.0, min(1.0, float(fraction)))
        self.queue_draw()

    def get_fraction(self) -> float:
        return self._fraction

    def _draw(
        self, _area: Gtk.DrawingArea, cr: object, width: int, height: int
    ) -> None:
        if width <= 0 or height <= 0:
            return
        cr.set_source_rgba(*rgba("play", 0.12))  # type: ignore[attr-defined]
        cr.rectangle(0, 0, width, height)  # type: ignore[attr-defined]
        cr.fill()  # type: ignore[attr-defined]
        filled = width * self._fraction
        if filled <= 0:
            return
        cr.set_source_rgba(*rgba("accent", 0.96))  # type: ignore[attr-defined]
        cr.rectangle(0, 0, filled, height)  # type: ignore[attr-defined]
        cr.fill()  # type: ignore[attr-defined]


def _format_time(nanoseconds: int) -> str:
    seconds = max(0, int(nanoseconds // GST_SECOND))
    hours, remainder = divmod(seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours:
        return f"{hours:d}:{minutes:02d}:{seconds:02d}"
    return f"{minutes:02d}:{seconds:02d}"


def _closest_speed_index(speed: float) -> int:
    return min(range(len(SPEED_VALUES)), key=lambda index: abs(SPEED_VALUES[index] - speed))


def _labelled_row(label: Gtk.Label, widget: Gtk.Widget) -> Gtk.Widget:
    row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
    label.set_xalign(0.0)
    label.set_hexpand(True)
    row.append(label)
    row.append(widget)
    return row


def _slider_block(label: Gtk.Label, scale: Gtk.Scale, value: Gtk.Label) -> Gtk.Widget:
    """Title + value on one line, slider underneath (v4 beauty page).

    ``Gtk.Scale`` draws its own value at the end of the trough, which is a
    different position on every row; the mockup puts the number on the title
    line instead, so the scale itself draws nothing and this label carries it.
    """
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
    box.add_css_class("beauty-slider")
    top = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
    label.set_xalign(0.0)
    label.set_hexpand(True)
    label.add_css_class("beauty-slider-label")
    value.set_xalign(1.0)
    value.add_css_class("beauty-slider-value")
    top.append(label)
    top.append(value)
    box.append(top)
    box.append(scale)
    return box


class PlayerWindow(Gtk.ApplicationWindow):
    """Main window: video surface + transport bar + playlist/beauty sidebar."""

    def __init__(
        self,
        application: Gtk.Application,
        config: Config,
        media: Sequence[Path] = (),
        *,
        demo: bool = False,
        fullscreen: bool = False,
        record: Path | None = None,
        record_duration: float | None = None,
        language: str | None = None,
    ) -> None:
        super().__init__(application=application)
        self.add_css_class("cinema-window")
        self.config = config
        self.demo = demo
        # An explicit ``detection.enabled=true`` (custom --config) always
        # wins; only the shipped default may be toggled on demand.
        self._detection_opted_in = config.detection.enabled
        self._t = Translator(language)
        self.playlist = Playlist(media)
        # Resume positions describe the viewer's progress, not the demo's tuned
        # parameters, so they live in XDG state rather than the project config
        # (``player.resume = false`` disables the feature entirely).
        self._resume = ResumeStore() if config.player.resume else None
        self._resume_saved_at = 0.0
        # History of opened files, separate from the resume positions:
        # a clip watched to the end leaves the resume map but must stay
        # in the history (see recents.py).
        self._recents = (
            RecentsStore() if config.player.recent_files else None
        )
        self._recent_paths: list[Path] = []
        #: Sidecar subtitles for the current clip (None when there is no .srt).
        self._subtitles: SubtitleTrack | None = None
        self._subtitles_enabled = True
        self._subtitle_path: Path | None = None
        self._subtitle_text: str | None = None
        #: Guards the track dropdown while its model is rebuilt, otherwise
        #: ``set_selected`` would look like a user choice and re-seek.
        self._updating_audio = False
        # ``--record`` bypasses the on-screen video sink so the recorder
        # owns the GPU and the recorded file stays a smooth 30 fps.
        self.backend = PlaybackBackend(config, display_bypass=record is not None)
        self._fps = 0.0
        self._duration_ns = 0
        self._updating_seek = False
        self._updating_decode = False
        # Idle source ids for deferred pipeline rebuilds; 0 when idle.
        self._decode_reopen_id = 0
        self._detection_reopen_id = 0
        self._timer_id = 0
        self._faces: tuple[Face, ...] = ()
        self._detection_ms = 0.0
        self._detection_error: str | None = None
        self._beauty_error: str | None = None
        # Player first: face boxes (and the NPU detector behind them) are opt-in.
        self._show_faces = False
        # v4: A|B and the boxes are children of the beauty switch.  A config
        # that still remembers compare_mode while beauty is off must not pop
        # the split back on when beauty is enabled later; the page notice
        # promises exactly that.
        if not config.beauty.enabled:
            config.beauty.compare_mode = False
        # Guards the beauty switch and the dock pill from echoing each other.
        self._updating_toggles = False
        # P4: the beauty controls live in a right-hand drawer.  The drawer is
        # closed on start and stays open across clip changes; the cascade state
        # still comes from the single `config.beauty` source of truth.
        self._drawer_open = False
        #: Docked 选集 column.  Windowed playback opens it by default; the
        #: recorder keeps it collapsed so the captured canvas stays unchanged.
        self._episodes_open = record is None
        #: Which tab of the windowed panel is active ("episodes"/"beauty").
        self._panel_tab = "episodes"
        #: Where the secondary controls live: "panel" while windowed, "dock"
        #: in fullscreen/--record.  None until the first sync.
        self._secondary_placement: str | None = None
        #: Own double-tap grouping: GTK's n_press is not a product contract on
        #: the touch panel (see ui/taps.py).
        self._tap_tracker = TapTracker()
        self._tap_timeout_id = 0
        self._tap_path: Path | None = None
        self._ignore_surface_tap_until = 0.0
        self._last_detection_label_at = 0.0
        # Optional chapter sidecar for the current clip; projected onto the
        # timeline once the duration is known.
        self._chapters: tuple[Chapter, ...] = ()
        self._chapter_duration_ns = 0
        # Process CPU (all threads), sampled once a second for the telemetry
        # row.  `ru_utime + ru_stime` is real process time, not an estimate.
        self._cpu_percent = 0.0
        self._cpu_sampled_at = time.monotonic()
        _usage = resource.getrusage(resource.RUSAGE_SELF)
        self._cpu_usage = _usage.ru_utime + _usage.ru_stime
        self._current_path: Path | None = None
        self._playback_path: Path | None = None
        self._playback_status: str | None = None
        self._playback_status_message = ""
        self._playback_started = False
        self._hint_key: str | None = None
        self._hint_values: dict[str, object] = {}
        # Fullscreen ("immersive") mode re-parents the info line, the transport
        # and the hint into a floating dock over the picture, which hides
        # itself after DOCK_IDLE_SECONDS of stillness.  ``--record`` keeps the
        # windowed layout: the recorder composes its canvas from the widget
        # allocations, so moving or hiding them would blank the recording.
        self._immersive = False
        self._dock_hover = False
        self._dock_pointer: tuple[float, float] | None = None
        self._dock_deadline = 0.0
        self._record_path = record
        self._record_duration = record_duration
        self._record_deadline = 0.0
        self._record_done = False
        self._recorder = None
        self._canvas_builder = None
        self._record_timer_id = 0
        self._record_attempts = 0
        self._record_failures = 0
        self._record_ticks = 0
        self._record_tick_ms = 0.0
        self._record_error: str | None = None
        self._perf_log = os.environ.get("VIDEO_PLAYER_PERF_LOG") == "1"
        self._perf_last = 0.0
        self._perf_cpu = 0.0
        self._perf_seconds = 0
        if self._perf_log:
            usage = resource.getrusage(resource.RUSAGE_SELF)
            self._perf_cpu = usage.ru_utime + usage.ru_stime

        self.backend.on_eos = self._on_eos
        self.backend.on_error = self._show_error
        self.backend.on_state = self._on_state_changed
        self.backend.on_video_info = self._on_video_info
        self.backend.on_fps = self._on_fps
        self.backend.on_detection = self._on_detection
        self.backend.on_detection_error = self._on_detection_error
        self.backend.on_beauty_error = self._on_beauty_error
        self.backend.on_decode_fallback = self._on_decode_fallback
        self.backend.on_recording_error = self._on_recording_error

        self.set_default_size(1280, 800)

        header = Gtk.HeaderBar()
        header.add_css_class("cinema-headerbar")
        self.set_titlebar(header)
        #: Kept as an attribute so the recording canvas can paint the title bar
        #: on the browse pages; the player page hides the header and paints its
        #: own top chrome over the picture.
        self._headerbar = header

        # Browse-shell title.  Windowed playback keeps this header visible
        # (clip name + window buttons), so the picture top bar hides its own
        # title labels (compact) and only keeps back/episodes/more.
        title_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        title_box.set_halign(Gtk.Align.CENTER)
        self._title_label = Gtk.Label()
        self._title_label.add_css_class("header-title")
        self._title_label.set_ellipsize(Pango.EllipsizeMode.END)
        self._title_label.set_max_width_chars(56)
        self._header_subtitle_label = Gtk.Label()
        self._header_subtitle_label.add_css_class("header-subtitle")
        self._header_subtitle_label.set_visible(False)
        title_box.append(self._title_label)
        title_box.append(self._header_subtitle_label)
        header.set_title_widget(title_box)
        #: Kept for the windowed player page: the header bar stays visible
        #: there, so its title widget must be restored when leaving the page.
        self._browse_title_box = title_box
        self._header_open_button = self._build_header_open_menu()
        header.pack_start(self._header_open_button)
        self._header_subtitle_cache = ""
        self._hint_timeout = 0

        # No language switch up here: the settings page owns the language
        # choice (2026-11-27), and the player keeps its own entry in the
        # "more" menu because the header is hidden there.

        # Build the player page first: the browse shell's "open file" action
        # uses the open button that lives in the player's more menu.
        self._player_page = self._build_player_area()

        # ui-v5 shell: one stack, both halves built once, only visibility moves.
        # ``--record`` and ``--demo`` start on the player page (the recorder
        # composes the windowed player layout); a plain launch with no media
        # lands on the browse shell's home page.
        self._browse = BrowseShell(
            config=config,
            translator=self._t,
            recents=self._recents,
            on_play=self._play_paths,
            on_open_file=lambda: self._on_open_clicked(self._open_button),
            on_changed=self._on_library_changed,
            on_drop_paths=self._on_library_dropped,
            on_language=self._set_language,
            on_decode=self._set_decode_mode,
        )
        #: Alias kept for the QC probes: the library page inside the shell.
        self._library = self._browse.library
        self._stack = Gtk.Stack()
        self._stack.set_transition_type(Gtk.StackTransitionType.NONE)
        self._stack.add_named(self._player_page, "player")
        self._stack.add_named(self._browse, "browse")
        self._page = (
            "player"
            if (self.playlist or demo or record is not None)
            else "browse"
        )
        self._stack.set_visible_child_name(self._page)
        self.set_child(self._stack)

        self._apply_demo_mode()

        controller = Gtk.EventControllerKey()
        controller.connect("key-pressed", self._on_key_pressed)
        self.add_controller(controller)

        # Motion drives the fullscreen HUD; the controller sits on the window
        # so events over the dock itself (buttons, scales) still reach it.
        motion = Gtk.EventControllerMotion()
        motion.connect("motion", self._on_pointer_motion)
        self.add_controller(motion)
        # The window manager can go fullscreen on its own (F11, a shell
        # binding), so the layout follows the property rather than the button.
        self.connect("notify::fullscreened", self._on_fullscreen_notified)

        self._timer_id = GLib.timeout_add(200, self._tick)
        self._closed = False
        # GTK4 no longer emits GtkWidget::destroy, so the teardown hook is
        # close-request; it runs synchronously from Window.close()/destroy().
        self.connect("close-request", self._on_close_request)

        if fullscreen or demo:
            self.fullscreen()
            self._sync_immersive()
        if self.playlist:
            GLib.idle_add(self._start_index, 0)
        self._apply_language()
        self._sync_page_chrome()
        if self._page == "browse":
            GLib.idle_add(self._refresh_browse)
        log_action(
            "window_open",
            media=len(media),
            demo=demo,
            fullscreen=fullscreen,
            language=self._t.language,
            record=str(record) if record is not None else "",
        )

    # -- layout ---------------------------------------------------------

    def _build_player_area(self) -> Gtk.Widget:
        """Build the ui-v5 player page (P4 picture-first, P5 windowed shell).

        A horizontal split: the picture *stage* (one overlay with the top bar,
        telemetry/timeline/44 px transport, veils and the beauty drawer) plus
        the docked 选集 column.  Windowed playback also keeps the real GTK
        header bar, so the page is visibly a window; fullscreen hides the
        header bar and collapses the column, leaving the P4 picture-first
        layout.  ``--record`` keeps the P4 no-header/no-sidebar canvas.
        """
        stage = Gtk.Overlay()
        stage.add_css_class("play-stage")
        stage.set_hexpand(True)
        stage.set_vexpand(True)
        self._player_box = stage

        video = Gtk.Overlay()
        video.add_css_class("video-surface")
        video.add_css_class("play-video-surface")
        video.set_hexpand(True)
        video.set_vexpand(True)
        self._player_overlay = video
        #: The recorder punches its transparent video hole at this widget; it
        #: is exactly the 16:9 frame, not the letterbox around it.
        self._video_surface = video

        aspect = Gtk.AspectFrame(ratio=16.0 / 9.0, obey_child=False)
        aspect.set_xalign(0.5)
        aspect.set_yalign(0.5)
        aspect.set_hexpand(True)
        aspect.set_vexpand(True)
        aspect.add_css_class("play-aspect")
        aspect.set_child(video)
        stage.set_child(aspect)

        self._picture = Gtk.Picture()
        self._picture.set_hexpand(True)
        self._picture.set_vexpand(True)
        self._picture.set_can_shrink(True)
        self._picture.set_content_fit(Gtk.ContentFit.CONTAIN)
        video.set_child(self._picture)

        # Split captions sit above the frame but below the face brackets.
        self._compare_overlay = _CompareOverlay()
        self._compare_overlay.set_hexpand(True)
        self._compare_overlay.set_vexpand(True)
        video.add_overlay(self._compare_overlay)

        self._face_overlay = _FaceOverlay()
        self._face_overlay.set_hexpand(True)
        self._face_overlay.set_vexpand(True)
        video.add_overlay(self._face_overlay)

        self._empty_state = self._build_empty_state()
        video.add_overlay(self._empty_state)
        self._build_playback_status(video)

        # Subtitles are drawn as a GTK layer, not by a GStreamer textoverlay:
        # the video path stays untouched (see subtitle.py for the reasoning).
        self._subtitle_label = Gtk.Label()
        self._subtitle_label.add_css_class("subtitle-overlay")
        self._subtitle_label.set_halign(Gtk.Align.CENTER)
        self._subtitle_label.set_valign(Gtk.Align.END)
        self._subtitle_label.set_justify(Gtk.Justification.CENTER)
        self._subtitle_label.set_wrap(True)
        self._subtitle_label.set_margin_bottom(24)
        self._subtitle_label.set_margin_start(24)
        self._subtitle_label.set_margin_end(24)
        self._subtitle_label.set_can_target(False)
        self._subtitle_label.set_visible(False)
        video.add_overlay(self._subtitle_label)

        # A touch panel produces no pointer motion, so on the board a tap is
        # the only way to summon the fullscreen HUD.  Tapping never hides it:
        # a tap that lands on a button must not make the bar disappear.
        tap = Gtk.GestureClick()
        tap.set_button(Gdk.BUTTON_PRIMARY)
        tap.connect("pressed", self._on_surface_pressed)
        video.add_controller(tap)

        # Drop a file or folder anywhere on the picture; the empty state makes
        # that affordance explicit, but it keeps working during playback.
        drop = Gtk.DropTarget.new(Gdk.FileList, Gdk.DragAction.COPY)
        drop.connect("drop", self._on_files_dropped)
        drop.connect("enter", self._on_drop_enter)
        drop.connect("leave", self._on_drop_leave)
        video.add_controller(drop)

        self._top_veil = Gtk.Box()
        self._top_veil.add_css_class("play-veil-top")
        self._top_veil.set_valign(Gtk.Align.START)
        self._top_veil.set_halign(Gtk.Align.FILL)
        self._top_veil.set_size_request(-1, 190)
        self._top_veil.set_can_target(False)
        stage.add_overlay(self._top_veil)

        self._bottom_veil = Gtk.Box()
        self._bottom_veil.add_css_class("play-veil-bottom")
        self._bottom_veil.set_valign(Gtk.Align.END)
        self._bottom_veil.set_halign(Gtk.Align.FILL)
        self._bottom_veil.set_size_request(-1, 320)
        self._bottom_veil.set_can_target(False)
        stage.add_overlay(self._bottom_veil)

        self._top_bar = PlayerTopBar(
            back_tooltip=self._t("detail_back"),
            episodes_label=self._t("detail_episodes"),
            more_tooltip=self._t("play_more"),
        )
        self._back_button = self._top_bar.back_button
        self._back_button.connect("clicked", self._on_back_clicked)
        self._episodes_button = self._top_bar.episodes_button
        self._episodes_label = self._top_bar.episodes_label
        # The chip is icon-only: the words live in the panel tabs and in the
        # tooltip, so the top bar does not spend width on them.  The dot keeps
        # the beauty state visible while the panel is closed.
        self._episodes_label.set_visible(False)
        self._panel_dot = Gtk.Label(label="\u25cf")
        self._panel_dot.add_css_class("play-chip-dot")
        self._panel_dot.set_valign(Gtk.Align.CENTER)
        self._panel_dot.set_visible(False)
        chip = self._episodes_button.get_child()
        if chip is not None:
            chip.append(self._panel_dot)
        self._episodes_button.set_tooltip_text(self._t("panel_tooltip"))
        self._play_title_label = self._top_bar.title_label
        self._play_subtitle_label = self._top_bar.subtitle_label
        self._episodes_button.connect("clicked", self._on_episodes_clicked)
        self._top_more_button = self._top_bar.more_button
        self._more_popover = self._build_more_popover()
        self._top_more_button.set_popover(self._more_popover)
        stage.add_overlay(self._top_bar)

        # The beauty page is built once and re-hosted: the panel tab while
        # windowed, the overlay drawer in fullscreen/--record.
        self._beauty_page = self._build_beauty_page()
        self._build_transport_dock(stage)
        self._build_beauty_drawer(stage)
        self._build_immersive_chrome(stage)
        self._build_hint_toast(stage)

        # Windowed click-outside guard: closes the beauty drawer without
        # letting the click reach the transport or the picture.
        guard = Gtk.GestureClick()
        guard.set_button(Gdk.BUTTON_PRIMARY)
        guard.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        guard.connect("pressed", self._on_stage_pressed)
        stage.add_controller(guard)

        # The episodes sidebar is a real layout column (windowed only): the
        # 16:9 picture shrinks to the remaining width and the transport
        # reflows.  Fullscreen and --record collapse it.
        split = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        split.add_css_class("play-split")
        split.append(stage)
        self._player_split = split
        self._episodes_revealer = self._build_episodes_sidebar()
        split.append(self._episodes_revealer)

        self._sync_chrome_visibility()
        self._sync_subtitle_margin()
        return split

    def _build_info_bar(self) -> Gtk.Widget:
        """One-line telemetry row inside the picture's bottom dock."""
        bar = TelemetryBar()
        self._info_bar = bar
        self._info_values = {
            "decode": bar.decode_value,
            "codec": bar.codec_value,
            "format": bar.resolution_value,
            "fps": bar.fps_value,
        }
        self._info_face = bar.face_value
        self._info_beauty = bar.beauty_value
        self._info_dot = bar.decode_dot
        self._info_cpu = bar.cpu_value
        bar.set_visible(self.config.debug.show_info_panel)
        return bar

    def _build_empty_state(self) -> Gtk.Widget:
        """Full-picture drop zone shown only while no clip is open."""
        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        outer.set_hexpand(True)
        outer.set_vexpand(True)
        outer.set_can_target(True)
        outer.add_css_class("empty-state")

        dropzone = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        dropzone.add_css_class("dropzone")
        dropzone.set_hexpand(True)
        dropzone.set_vexpand(True)
        dropzone.set_margin_top(12)
        dropzone.set_margin_bottom(12)
        dropzone.set_margin_start(12)
        dropzone.set_margin_end(12)
        self._dropzone = dropzone

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        content.set_halign(Gtk.Align.CENTER)
        content.set_valign(Gtk.Align.CENTER)
        content.set_vexpand(True)

        icon = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        icon.add_css_class("dropzone-icon")
        icon.set_halign(Gtk.Align.CENTER)
        icon_image = Gtk.Image.new_from_icon_name("media-playback-start-symbolic")
        icon_image.set_pixel_size(28)
        icon.append(icon_image)
        content.append(icon)

        self._empty_title = Gtk.Label(label=self._t("empty_title"))
        self._empty_title.add_css_class("dropzone-title")
        self._empty_title.set_justify(Gtk.Justification.CENTER)
        content.append(self._empty_title)

        self._empty_subtitle = Gtk.Label(label=self._t("empty_subtitle"))
        self._empty_subtitle.add_css_class("dropzone-subtitle")
        self._empty_subtitle.set_justify(Gtk.Justification.CENTER)
        content.append(self._empty_subtitle)

        cta = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        cta.set_halign(Gtk.Align.CENTER)
        self._empty_file_button = Gtk.Button(label=self._t("empty_choose_file"))
        self._empty_file_button.add_css_class("drop-primary")
        self._empty_file_button.connect("clicked", self._on_open_clicked)
        self._empty_folder_button = Gtk.Button(label=self._t("empty_choose_folder"))
        self._empty_folder_button.add_css_class("drop-secondary")
        self._empty_folder_button.connect("clicked", self._on_open_folder_clicked)
        cta.append(self._empty_file_button)
        cta.append(self._empty_folder_button)
        content.append(cta)

        self._empty_hint = Gtk.Label(label=self._t("empty_drop_hint"))
        self._empty_hint.add_css_class("dropzone-hint")
        self._empty_hint.set_justify(Gtk.Justification.CENTER)
        content.append(self._empty_hint)
        dropzone.append(content)

        capabilities = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        capabilities.set_halign(Gtk.Align.CENTER)
        capabilities.set_valign(Gtk.Align.END)
        capabilities.set_margin_bottom(18)
        self._capability_labels: list[Gtk.Label] = []
        for key in ("empty_chip_vpu", "empty_chip_npu", "empty_chip_beauty"):
            chip = Gtk.Label(label=self._t(key))
            chip.add_css_class("capability-chip")
            capabilities.append(chip)
            self._capability_labels.append(chip)
        dropzone.append(capabilities)
        outer.append(dropzone)
        outer.set_visible(False)
        return outer

    def _build_playback_status(self, overlay: Gtk.Overlay) -> None:
        """Build the persistent, video-only status card used by playback states."""
        self._status_card = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL, spacing=10
        )
        self._status_card.set_halign(Gtk.Align.CENTER)
        self._status_card.set_valign(Gtk.Align.CENTER)
        self._status_card.set_hexpand(False)
        self._status_card.set_vexpand(False)
        self._status_card.set_visible(False)
        self._status_card.add_css_class("playback-status-card")

        heading = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        heading.set_halign(Gtk.Align.CENTER)
        self._status_spinner = Gtk.Spinner()
        self._status_spinner.set_size_request(28, 28)
        self._status_spinner.add_css_class("status-spinner")
        self._status_icon = Gtk.Image()
        self._status_icon.set_pixel_size(34)
        self._status_icon.add_css_class("status-symbol")
        self._status_title = Gtk.Label()
        self._status_title.set_xalign(0.0)
        self._status_title.add_css_class("status-title")
        heading.append(self._status_spinner)
        heading.append(self._status_icon)
        heading.append(self._status_title)
        self._status_card.append(heading)

        self._status_message = Gtk.Label()
        self._status_message.set_wrap(True)
        self._status_message.set_justify(Gtk.Justification.CENTER)
        self._status_message.set_max_width_chars(48)
        self._status_message.set_selectable(True)
        self._status_message.add_css_class("status-message")
        self._status_card.append(self._status_message)

        self._status_actions = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL, spacing=8
        )
        self._status_actions.set_halign(Gtk.Align.CENTER)
        self._status_actions.add_css_class("status-actions")
        self._status_primary_button = Gtk.Button()
        self._status_primary_button.add_css_class("accent-button")
        self._status_primary_button.connect(
            "clicked", self._on_status_primary_clicked
        )
        self._status_secondary_button = Gtk.Button()
        self._status_secondary_button.add_css_class("secondary-action")
        self._status_secondary_button.connect(
            "clicked", self._on_status_secondary_clicked
        )
        self._status_actions.append(self._status_primary_button)
        self._status_actions.append(self._status_secondary_button)
        self._status_card.append(self._status_actions)
        overlay.add_overlay(self._status_card)

    def _build_transport_dock(self, stage: Gtk.Overlay) -> None:
        """Bottom overlay: telemetry, chapter timeline and the 44 px key row.

        The same block is the windowed transport and the fullscreen dock; only
        its revealer changes behaviour when the window goes fullscreen, so the
        two layouts cannot drift apart.  ``--record`` captures this widget as
        one layer, which keeps the picture hole free for the mixer.
        """
        dock = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        dock.add_css_class("play-dock")
        dock.set_margin_start(24)
        dock.set_margin_end(24)
        dock.set_margin_bottom(14)
        self._dock = dock
        # The transport block (recording layer + visibility switch) is the
        # whole telemetry/rail/buttons stack, not just the button row.
        self._controls_bar = dock

        self._info_bar = self._build_info_bar()
        dock.append(self._info_bar)

        seek_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        seek_row.add_css_class("play-seek-row")
        self._elapsed_label = Gtk.Label(label="00:00")
        self._elapsed_label.add_css_class("play-time")
        self._elapsed_label.set_xalign(0.0)
        seek_row.append(self._elapsed_label)

        self._timeline = ChapterTimeline()
        self._timeline.set_tooltip_text(self._t("seek"))
        self._timeline.connect("value-changed", self._on_seek_changed)
        #: Alias kept for the tick/seek code that used the v4 Gtk.Scale; the
        #: custom rail implements the same 0–1000 set_value/get_value subset.
        self._seek = self._timeline
        seek_row.append(self._timeline)

        self._total_label = Gtk.Label(label="00:00")
        self._total_label.add_css_class("play-time")
        self._total_label.set_xalign(1.0)
        seek_row.append(self._total_label)
        dock.append(seek_row)

        dock.append(self._build_controls())

        revealer = Gtk.Revealer()
        revealer.set_transition_type(Gtk.RevealerTransitionType.CROSSFADE)
        revealer.set_transition_duration(200)
        revealer.set_halign(Gtk.Align.FILL)
        revealer.set_valign(Gtk.Align.END)
        revealer.set_child(dock)
        revealer.set_reveal_child(True)
        revealer.set_visible(True)
        self._dock_revealer = revealer
        stage.add_overlay(revealer)

    def _build_immersive_chrome(self, stage: Gtk.Overlay) -> None:
        """Always-on fullscreen telemetry and the bottom progress hairline.

        The top bar and the dock fade in immersive mode; these two stay so the
        viewer keeps the clip identity and a sense of position.  In windowed
        mode they are hidden because the dock already carries the same data.
        """
        telemetry = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        telemetry.set_halign(Gtk.Align.START)
        telemetry.set_valign(Gtk.Align.START)
        telemetry.set_margin_top(16)
        telemetry.set_margin_start(20)
        telemetry.set_can_target(False)
        telemetry.set_visible(False)
        self._telemetry_main = Gtk.Label(label="—")
        self._telemetry_main.add_css_class("fullscreen-telemetry")
        self._telemetry_main.set_xalign(0.0)
        self._telemetry_main.set_selectable(False)
        self._telemetry_live = Gtk.Label(label="")
        self._telemetry_live.add_css_class("fullscreen-telemetry")
        self._telemetry_live.add_css_class("fullscreen-telemetry-live")
        self._telemetry_live.set_xalign(0.0)
        self._telemetry_live.set_selectable(False)
        dot = Gtk.Label(label="●")
        dot.add_css_class("info-dot")
        dot.set_valign(Gtk.Align.CENTER)
        self._telemetry_dot = dot
        telemetry.append(self._telemetry_main)
        telemetry.append(dot)
        telemetry.append(self._telemetry_live)
        self._telemetry_box = telemetry
        stage.add_overlay(telemetry)

        self._fullscreen_progress = _ProgressLine()
        self._fullscreen_progress.set_halign(Gtk.Align.FILL)
        self._fullscreen_progress.set_valign(Gtk.Align.END)
        self._fullscreen_progress.set_hexpand(True)
        self._fullscreen_progress.set_visible(False)
        stage.add_overlay(self._fullscreen_progress)

    def _build_hint_toast(self, stage: Gtk.Overlay) -> None:
        """Transient status text over the picture instead of a fixed row."""
        self._hint_label = Gtk.Label(label="")
        self._hint_label.set_justify(Gtk.Justification.CENTER)
        self._hint_label.set_max_width_chars(64)
        self._hint_label.set_wrap(True)
        self._hint_label.set_can_target(False)
        self._hint_label.add_css_class("hint-toast")

        self._hint_revealer = Gtk.Revealer()
        self._hint_revealer.set_transition_type(Gtk.RevealerTransitionType.CROSSFADE)
        self._hint_revealer.set_transition_duration(180)
        self._hint_revealer.set_child(self._hint_label)
        self._hint_revealer.set_halign(Gtk.Align.CENTER)
        self._hint_revealer.set_valign(Gtk.Align.START)
        # Clear the top bar; the old header row used to own this height.
        self._hint_revealer.set_margin_top(84)
        self._hint_revealer.set_can_target(False)
        self._hint_revealer.set_visible(False)
        stage.add_overlay(self._hint_revealer)

    def _build_controls(self) -> Gtk.Widget:
        """44 px transport keys; the play key is the mockup's 52 px circle.

        One row in every mode.  The secondary controls (字幕/美颜) do not live
        here while the panel is available: the panel carries 美颜 and the
        picture top bar carries 字幕 (see ``_sync_secondary_placement``).
        """
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        row.add_css_class("play-controls")
        row.set_size_request(-1, PLAY_BUTTON_SIZE + 2)

        def button(glyph: str | None = None, icon: str | None = None) -> Gtk.Button:
            widget = Gtk.Button()
            widget.add_css_class("play-control")
            widget.set_valign(Gtk.Align.CENTER)
            widget.set_size_request(CONTROL_HEIGHT, CONTROL_HEIGHT)
            if icon is not None:
                widget.set_child(Gtk.Image.new_from_icon_name(icon))
            elif glyph is not None:
                label = Gtk.Label(label=glyph)
                label.add_css_class("play-control-glyph")
                widget.set_child(label)
            return widget

        self._prev_button = button(icon="media-skip-backward-symbolic")
        self._prev_button.connect("clicked", self._on_previous_clicked)
        row.append(self._prev_button)

        self._back15_button = button(glyph="\u221215")
        self._back15_button.add_css_class("play-skip")
        self._back15_button.connect("clicked", self._on_seek_back_clicked)
        row.append(self._back15_button)

        self._play_button = Gtk.Button()
        self._play_button.add_css_class("play-primary")
        self._play_button.set_valign(Gtk.Align.CENTER)
        self._play_button.set_size_request(PLAY_BUTTON_SIZE, PLAY_BUTTON_SIZE)
        self._play_button.set_icon_name("media-playback-start-symbolic")
        self._play_button.connect("clicked", self._on_play_clicked)
        row.append(self._play_button)

        self._forward15_button = button(glyph="+15")
        self._forward15_button.add_css_class("play-skip")
        self._forward15_button.connect("clicked", self._on_seek_forward_clicked)
        row.append(self._forward15_button)

        self._next_button = button(icon="media-skip-forward-symbolic")
        self._next_button.connect("clicked", self._on_next_clicked)
        row.append(self._next_button)

        self._stop_button = button(icon="media-playback-stop-symbolic")
        self._stop_button.add_css_class("play-ghost")
        self._stop_button.connect("clicked", self._on_stop_clicked)
        row.append(self._stop_button)

        row.append(self._divider())

        self._speed = Gtk.DropDown.new_from_strings(list(SPEED_CHOICES))
        self._speed.set_selected(_closest_speed_index(self.config.player.speed))
        self._speed.add_css_class("play-speed")
        self._speed.set_size_request(84, CONTROL_HEIGHT)
        self._speed.set_valign(Gtk.Align.CENTER)
        self._speed.connect("notify::selected", self._on_speed_changed)
        row.append(self._speed)

        row.append(self._divider())
        # Kept out of the sidebar: decoding is part of playback and must stay
        # reachable in fullscreen, so the segmented control stays in the row.
        row.append(self._build_decode_segments())

        initial_volume = int(round(self.config.player.volume))
        volume_icon = (
            "audio-volume-muted-symbolic"
            if initial_volume == 0
            else "audio-volume-low-symbolic"
            if initial_volume < 35
            else "audio-volume-high-symbolic"
        )
        self._volume_icon = Gtk.Image.new_from_icon_name(volume_icon)
        self._volume_icon.set_pixel_size(16)
        self._volume_icon.add_css_class("volume-icon")
        row.append(self._volume_icon)

        self._volume = Gtk.Scale.new_with_range(
            Gtk.Orientation.HORIZONTAL, 0, 100, 1
        )
        self._volume.set_size_request(74, -1)
        self._volume.set_value(self.config.player.volume)
        self._volume.set_draw_value(False)
        self._volume.set_valign(Gtk.Align.CENTER)
        self._volume.connect("value-changed", self._on_volume_changed)
        row.append(self._volume)

        self._transport_spacer = Gtk.Box()
        self._transport_spacer.set_hexpand(True)
        row.append(self._transport_spacer)

        self._subtitle_button = Gtk.MenuButton(label=self._t("play_subtitles"))
        self._subtitle_button.add_css_class("play-control")
        self._subtitle_button.add_css_class("play-text-control")
        self._subtitle_button.set_valign(Gtk.Align.CENTER)
        self._subtitle_button.set_tooltip_text(self._t("subtitles_tooltip"))
        self._subtitle_button.set_popover(self._build_subtitle_popover())
        row.append(self._subtitle_button)

        # Beauty / compare: the pill is the drawer entrance and carries the
        # effect state in its label ("美颜 · 开 / 关"), like the v4 dock pill.
        self._dock_beauty_button = Gtk.ToggleButton(label=self._t("beauty"))
        self._dock_beauty_button.add_css_class("play-pill")
        self._dock_beauty_button.set_valign(Gtk.Align.CENTER)
        self._dock_beauty_button.set_active(self.config.beauty.enabled)
        self._dock_beauty_button.set_visible(False)
        self._dock_beauty_button.connect("clicked", self._on_beauty_pill_clicked)
        row.append(self._dock_beauty_button)

        self._immersive_tools = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL, spacing=4
        )
        self._immersive_tools.set_valign(Gtk.Align.CENTER)
        self._immersive_tools.set_visible(False)
        self._dock_compare_button = Gtk.ToggleButton(label=self._t("compare"))
        self._dock_compare_button.add_css_class("play-pill")
        self._dock_compare_button.set_valign(Gtk.Align.CENTER)
        self._dock_compare_button.set_active(self.config.beauty.compare_mode)
        self._dock_compare_button.connect(
            "toggled", self._on_dock_compare_toggled
        )
        self._immersive_tools.append(self._dock_compare_button)
        row.append(self._immersive_tools)

        self._fullscreen_button = button(icon="view-fullscreen-symbolic")
        self._fullscreen_button.connect("clicked", self._on_fullscreen_clicked)
        row.append(self._fullscreen_button)

        self._transport_row = row
        return row

    @staticmethod
    def _divider() -> Gtk.Separator:
        line = Gtk.Separator(orientation=Gtk.Orientation.VERTICAL)
        line.add_css_class("play-divider")
        return line

    def _build_episodes_sidebar(self) -> Gtk.Revealer:
        """The right-hand playback panel (windowed playback only).

        A real layout column: it takes 360 px and the 16:9 picture keeps its
        ratio in the remaining width.  Two tabs share the column - the episode
        list and the beauty controls; the overlay drawer is only used when the
        column is unavailable (fullscreen/``--record``).  The revealer makes the
        width change read as a slide rather than a jump.
        """
        panel = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        panel.add_css_class("sidebar-panel")
        panel.add_css_class("episodes-panel")
        panel.set_size_request(360, -1)
        panel.set_vexpand(True)
        panel.set_valign(Gtk.Align.FILL)
        self._episodes_panel = panel

        head = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        head.add_css_class("drawer-head")
        tabs = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=2)
        tabs.add_css_class("sidebar-tabs")
        tabs.set_hexpand(True)
        self._panel_tab_episodes = Gtk.ToggleButton(
            label=self._t("detail_episodes")
        )
        self._panel_tab_beauty = Gtk.ToggleButton(label=self._t("beauty"))
        # Radio behaviour: one tab is always active, so the panel never shows
        # an empty page after clicking the active label.
        self._panel_tab_beauty.set_group(self._panel_tab_episodes)
        for button in (self._panel_tab_episodes, self._panel_tab_beauty):
            button.set_hexpand(True)
            button.set_focus_on_click(False)
            button.connect("toggled", self._on_panel_tab_toggled)
            tabs.append(button)
        self._panel_tab_episodes.set_active(True)
        self._episodes_close_button = Gtk.Button(label="\u2715")
        self._episodes_close_button.add_css_class("drawer-close")
        self._episodes_close_button.set_valign(Gtk.Align.CENTER)
        self._episodes_close_button.set_tooltip_text(self._t("close"))
        self._episodes_close_button.connect(
            "clicked", lambda _button: self._set_episodes_open(False)
        )
        head.append(tabs)
        head.append(self._episodes_close_button)
        panel.append(head)

        episodes = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        episodes.set_vexpand(True)
        heading = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self._playlist_title = Gtk.Label()
        self._playlist_title.set_xalign(0.0)
        self._playlist_title.add_css_class("section-heading")
        self._playlist_title.add_css_class("popover-heading")
        heading.append(self._playlist_title)
        heading_spacer = Gtk.Box()
        heading_spacer.set_hexpand(True)
        heading.append(heading_spacer)
        self._episodes_count_label = Gtk.Label(label="")
        self._episodes_count_label.add_css_class("popover-meta")
        heading.append(self._episodes_count_label)
        episodes.append(heading)

        self._playlist_list = Gtk.ListBox()
        self._playlist_list.add_css_class("cinema-playlist")
        self._playlist_list.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self._playlist_list.set_activate_on_single_click(True)
        self._playlist_list.connect("row-activated", self._on_row_activated)
        self._playlist_scroll = Gtk.ScrolledWindow()
        self._playlist_scroll.add_css_class("playlist-scroll")
        self._playlist_scroll.set_policy(
            Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC
        )
        self._playlist_scroll.set_child(self._playlist_list)
        self._playlist_scroll.set_vexpand(True)
        episodes.append(self._playlist_scroll)
        self._playlist_empty = Gtk.Label(label=self._t("playlist_empty"))
        self._playlist_empty.add_css_class("empty-playlist")
        self._playlist_empty.set_justify(Gtk.Justification.CENTER)
        episodes.append(self._playlist_empty)

        self._playlist_add_button = Gtk.Button(label=self._t("playlist_add"))
        self._playlist_add_button.add_css_class("playlist-add")
        self._playlist_add_button.connect("clicked", self._on_episodes_add)
        episodes.append(self._playlist_add_button)

        self._recents_title = Gtk.Label()
        self._recents_title.set_xalign(0.0)
        self._recents_title.add_css_class("popover-heading")
        episodes.append(self._recents_title)

        self._recents_list = Gtk.ListBox()
        self._recents_list.add_css_class("cinema-playlist")
        self._recents_list.set_selection_mode(Gtk.SelectionMode.NONE)
        self._recents_list.set_activate_on_single_click(True)
        self._recents_list.connect("row-activated", self._on_recent_activated)
        self._recents_scroll = Gtk.ScrolledWindow()
        self._recents_scroll.add_css_class("playlist-scroll")
        self._recents_scroll.set_policy(
            Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC
        )
        self._recents_scroll.set_child(self._recents_list)
        episodes.append(self._recents_scroll)
        self._recents_empty = Gtk.Label()
        self._recents_empty.set_xalign(0.0)
        self._recents_empty.add_css_class("recents-empty")
        episodes.append(self._recents_empty)

        self._panel_stack = Gtk.Stack()
        self._panel_stack.set_vexpand(True)
        self._panel_stack.set_hexpand(True)
        self._panel_stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        self._panel_stack.set_transition_duration(120)
        self._panel_stack.add_named(episodes, "episodes")
        self._panel_stack.add_named(self._beauty_page, "beauty")
        panel.append(self._panel_stack)

        revealer = Gtk.Revealer()
        revealer.set_transition_type(Gtk.RevealerTransitionType.SLIDE_LEFT)
        revealer.set_transition_duration(180)
        revealer.set_halign(Gtk.Align.FILL)
        revealer.set_valign(Gtk.Align.FILL)
        # The panel's head expands, which would make the box give the whole
        # column extra width; pin the expand flag off explicitly.
        revealer.set_hexpand_set(True)
        revealer.set_hexpand(False)
        revealer.set_child(panel)
        revealer.set_reveal_child(False)
        revealer.set_visible(False)
        self._sync_playlist()
        return revealer

    def _on_panel_tab_toggled(self, button: Gtk.ToggleButton) -> None:
        """Switch the panel page; the tab buttons behave like radios."""
        if self._updating_toggles:
            return
        name = "beauty" if button is self._panel_tab_beauty else "episodes"
        other = (
            self._panel_tab_episodes if name == "beauty" else self._panel_tab_beauty
        )
        if not button.get_active():
            if other.get_active():
                return
            # Clicking the active tab must not leave the panel without a
            # selected tab; the group allows a programmatic "none active".
            self._updating_toggles = True
            try:
                button.set_active(True)
            finally:
                self._updating_toggles = False
        elif other.get_active():
            self._updating_toggles = True
            try:
                other.set_active(False)
            finally:
                self._updating_toggles = False
        self._panel_tab = name
        stack = getattr(self, "_panel_stack", None)
        if stack is not None and stack.get_child_by_name(name) is not None:
            stack.set_visible_child_name(name)

    def _set_panel_tab(self, name: str) -> None:
        """Select a panel tab (also used when the panel becomes available)."""
        name = "beauty" if name == "beauty" else "episodes"
        self._panel_tab = name
        stack = getattr(self, "_panel_stack", None)
        if stack is not None and stack.get_child_by_name(name) is not None:
            stack.set_visible_child_name(name)
        button = (
            self._panel_tab_beauty if name == "beauty" else self._panel_tab_episodes
        )
        if not button.get_active():
            self._updating_toggles = True
            try:
                button.set_active(True)
            finally:
                self._updating_toggles = False

    def _on_episodes_clicked(self, button: Gtk.ToggleButton) -> None:
        """The chip only mirrors state; the panel is the source of truth."""
        self._updating_toggles = True
        try:
            button.set_active(self._episodes_open)
        finally:
            self._updating_toggles = False
        self._set_episodes_open(not self._episodes_open)

    def _set_episodes_open(self, opened: bool) -> None:
        opened = bool(opened)
        if opened == self._episodes_open:
            self._sync_episodes_layout()
            return
        self._episodes_open = opened
        # The panel and the overlay drawer share the beauty page, so they can
        # never be open together (only one of them is available per mode).
        if opened and self._drawer_open:
            self._set_beauty_drawer_open(False)
        self._sync_episodes_layout()
        log_action("episodes_sidebar", open=opened)

    def _sync_episodes_layout(self) -> None:
        """Gate the docked panel and re-host the secondary controls.

        Windowed playback has the panel, so 字幕 moves to the picture top bar
        and 美颜 to the panel tab.  Fullscreen and ``--record`` collapse the
        panel, so both return to the one transport row (and the drawer).
        """
        revealer = getattr(self, "_episodes_revealer", None)
        if revealer is None:
            return
        available = (
            self._page == "player"
            and not self._immersive
            and self._record_path is None
        )
        revealer.set_visible(available)
        open_docked = bool(available and self._episodes_open)
        revealer.set_reveal_child(open_docked)
        self._sync_secondary_placement(available)
        if hasattr(self, "_top_bar"):
            self._top_bar.set_episodes_active(open_docked)
        if hasattr(self, "_episodes_button"):
            # A panel chip that can only change invisible state would be a
            # dead control while the column is unavailable (e.g. --record).
            self._episodes_button.set_visible(available)

    def _sync_secondary_placement(self, available: bool) -> None:
        """Move 字幕 and 美颜 to the hosts the current mode provides.

        The widgets are moved, never duplicated, so the subtitle popover and
        the beauty state survive every transition.  Windowed: 字幕 in the
        picture top bar, 美颜 in the panel tab.  Otherwise: both in the one
        transport row, with 美颜 keeping its overlay drawer.
        """
        page = getattr(self, "_beauty_page", None)
        if page is None:
            return
        where = "panel" if available else "dock"
        if where == self._secondary_placement:
            return
        if available and self._drawer_open:
            self._set_beauty_drawer_open(False)
        self._secondary_placement = where

        self._subtitle_button.unparent()
        if available:
            self._top_bar.insert_child_after(
                self._subtitle_button, self._top_bar.titles
            )
            self._subtitle_button.remove_css_class("play-control")
            self._subtitle_button.remove_css_class("play-text-control")
            self._subtitle_button.add_css_class("play-top-chip")
        else:
            self._transport_row.insert_child_after(
                self._subtitle_button, self._transport_spacer
            )
            self._subtitle_button.remove_css_class("play-top-chip")
            self._subtitle_button.add_css_class("play-control")
            self._subtitle_button.add_css_class("play-text-control")
        self._dock_beauty_button.set_visible(not available)

        parent = page.get_parent()
        if parent is self._panel_stack:
            # Gtk.Stack keeps the page name until the child is removed
            # explicitly; unparent alone leaves a duplicate for add_named.
            self._panel_stack.remove(page)
        elif parent is not None:
            page.unparent()
        if available:
            existing = self._panel_stack.get_child_by_name("beauty")
            if existing is not None:
                self._panel_stack.remove(existing)
            self._panel_stack.add_named(page, "beauty")
            self._set_panel_tab(self._panel_tab)
            page.remove_css_class("beauty-drawer")
            page.set_margin_top(0)
            page.set_margin_bottom(0)
            page.set_margin_end(0)
            page.set_size_request(-1, -1)
            self._beauty_header.set_visible(False)
            self._drawer_close_button.set_visible(False)
            self._beauty_tab_rule.set_visible(False)
        else:
            self._drawer_revealer.set_child(page)
            page.add_css_class("beauty-drawer")
            page.set_margin_top(76)
            page.set_margin_bottom(112)
            page.set_margin_end(24)
            page.set_size_request(352, -1)
            self._beauty_header.set_visible(True)
            self._drawer_close_button.set_visible(True)
            self._beauty_tab_rule.set_visible(True)

    def _on_episodes_add(self, _button: Gtk.Button) -> None:
        self._on_open_clicked(self._open_button)

    def _build_more_popover(self) -> Gtk.Popover:
        """Window-level actions that used to sit in the header bar."""
        popover = Gtk.Popover()
        popover.add_css_class("play-popover")
        popover.add_css_class("more-popover")
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        box.set_margin_top(12)
        box.set_margin_bottom(12)
        box.set_margin_start(12)
        box.set_margin_end(12)
        box.set_size_request(300, -1)

        self._open_button = self._menu_button(
            self._t("open"), self._on_open_clicked, glyph="\u25b6"
        )
        self._open_label = self._open_button.get_child().get_last_child()
        box.append(self._open_button)
        self._open_folder_button = self._menu_button(
            self._t("open_folder"), self._on_open_folder_clicked, glyph="\u25a3"
        )
        box.append(self._open_folder_button)

        self._language_button = self._menu_button(
            self._t("switch_language"),
            self._on_language_clicked,
            glyph="\u25ce",
        )
        box.append(self._language_button)

        box.append(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL))

        self._record_button = self._menu_button(
            self._t("recording_toggle"), self._on_record_menu_clicked, glyph="\u25cf"
        )
        box.append(self._record_button)

        self._close_button = self._menu_button(
            self._t("close_window"), self._on_close_menu_clicked, glyph="\u2715"
        )
        box.append(self._close_button)

        self._more_shortcuts_label = Gtk.Label()
        self._more_shortcuts_label.add_css_class("menu-shortcuts")
        self._more_shortcuts_label.set_xalign(0.0)
        self._more_shortcuts_label.set_wrap(True)
        box.append(self._more_shortcuts_label)

        popover.set_child(box)
        return popover

    def _menu_button(self, text: str, handler, *, glyph: str = "") -> Gtk.Button:
        button = Gtk.Button()
        button.add_css_class("menu-row")
        button.set_halign(Gtk.Align.FILL)
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        glyph_label = Gtk.Label(label=glyph)
        glyph_label.add_css_class("menu-row-glyph")
        label = Gtk.Label(label=text)
        label.set_xalign(0.0)
        label.set_hexpand(True)
        label.add_css_class("menu-row-label")
        box.append(glyph_label)
        box.append(label)
        button.set_child(box)
        button.label_widget = label
        button.connect("clicked", handler)
        return button

    def _build_header_open_menu(self) -> Gtk.MenuButton:
        """Browse header action: open a file or a folder from every page.

        The empty states keep their own buttons; this is the persistent entry
        that used to disappear as soon as the library had one clip.
        """
        button = Gtk.MenuButton()
        button.add_css_class("header-open")
        button.set_tooltip_text(self._t("open"))
        content = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        glyph = Gtk.Label(label="\uff0b")
        glyph.add_css_class("header-open-glyph")
        self._header_open_label = Gtk.Label(label=self._t("open"))
        self._header_open_label.add_css_class("header-open-label")
        content.append(glyph)
        content.append(self._header_open_label)
        button.set_child(content)

        popover = Gtk.Popover()
        popover.add_css_class("play-popover")
        popover.add_css_class("header-open-popover")
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        box.set_margin_top(10)
        box.set_margin_bottom(10)
        box.set_margin_start(10)
        box.set_margin_end(10)
        box.set_size_request(240, -1)
        self._header_open_file_row = self._menu_button(
            self._t("open_video"), self._on_header_open_file, glyph="\u25b6"
        )
        box.append(self._header_open_file_row)
        self._header_open_folder_row = self._menu_button(
            self._t("open_folder"), self._on_header_open_folder, glyph="\u25a3"
        )
        box.append(self._header_open_folder_row)
        popover.set_child(box)
        self._header_open_popover = popover
        button.set_popover(popover)
        return button

    def _on_header_open_file(self, button: Gtk.Button) -> None:
        self._header_open_popover.popdown()
        self._on_open_clicked(button)

    def _on_header_open_folder(self, button: Gtk.Button) -> None:
        self._header_open_popover.popdown()
        self._on_open_folder_clicked(button)

    def _on_record_menu_clicked(self, _button: Gtk.Button) -> None:
        self._more_popover.popdown()
        self._toggle_recording()

    def _on_close_menu_clicked(self, _button: Gtk.Button) -> None:
        self._more_popover.popdown()
        self.close()

    def _on_beauty_pill_clicked(self, button: Gtk.ToggleButton) -> None:
        """The pill is the drawer entrance; its check state mirrors the effect.

        A click changes the toggle before this handler runs; reset it so the
        pill keeps reporting ``config.beauty.enabled`` rather than the drawer
        state, then open/close the drawer.
        """
        self._updating_toggles = True
        try:
            button.set_active(self.config.beauty.enabled)
        finally:
            self._updating_toggles = False
        self._toggle_beauty_drawer()

    def _on_seek_back_clicked(self, _button: Gtk.Button) -> None:
        if self.backend.has_media:
            self.backend.seek_ns(
                max(0, self.backend.position_ns() - 15 * GST_SECOND)
            )

    def _on_seek_forward_clicked(self, _button: Gtk.Button) -> None:
        if self.backend.has_media:
            self.backend.seek_ns(
                self.backend.position_ns() + 15 * GST_SECOND
            )

    def _build_decode_segments(self) -> Gtk.Widget:
        """Three segments rather than a switch: "auto" is a real third option."""
        self._decode_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=2)
        self._decode_row.add_css_class("decode-segments")
        self._decode_row.set_valign(Gtk.Align.CENTER)
        self._decode_row.set_tooltip_text(self._t("decode_mode"))
        self._decode_buttons: dict[str, Gtk.ToggleButton] = {}
        first_button: Gtk.ToggleButton | None = None
        for mode in DECODE_MODES:
            segment = Gtk.ToggleButton()
            if first_button is None:
                first_button = segment
            else:
                segment.set_group(first_button)
            segment.set_label(self._t(f"decode_{mode}"))
            # Set the initial state before connecting: a grouped toggle emits
            # "toggled" for every set_active(), including this one.
            segment.set_active(self.config.player.decode_mode == mode)
            segment.connect("toggled", self._on_decode_toggled, mode)
            self._decode_row.append(segment)
            self._decode_buttons[mode] = segment
        # Handlers are connected now: normalise the group through the guarded
        # path so a stale state cannot leave two segments active.
        self._sync_decode_buttons()
        return self._decode_row

    def _build_beauty_drawer(self, stage: Gtk.Overlay) -> None:
        """Right-hand overlay shell for the beauty page.

        The page itself is built once by :meth:`_build_beauty_page` and is
        re-hosted here only when the docked panel is unavailable (fullscreen
        and ``--record``); the shell therefore starts without a child.
        """
        revealer = Gtk.Revealer()
        revealer.set_transition_type(Gtk.RevealerTransitionType.SLIDE_LEFT)
        revealer.set_transition_duration(180)
        revealer.set_halign(Gtk.Align.END)
        revealer.set_valign(Gtk.Align.FILL)
        revealer.set_reveal_child(False)
        revealer.set_visible(True)
        self._drawer_revealer = revealer
        self._drawer_panel = self._beauty_page
        stage.add_overlay(revealer)

    def _toggle_beauty_drawer(self) -> None:
        self._set_beauty_drawer_open(not self._drawer_open)

    def _set_beauty_drawer_open(self, opened: bool) -> None:
        opened = bool(opened)
        if opened == self._drawer_open:
            return
        if opened and getattr(self, "_secondary_placement", "dock") != "dock":
            # While windowed the page lives in the panel tab; there is no
            # drawer to open (the pill is hidden in that mode).
            return
        self._drawer_open = opened
        if opened:
            # One right-hand column at a time; do not recurse through
            # ``_set_episodes_open(False)`` (it only closes the drawer when
            # *opening* the episodes column).
            if self._episodes_open:
                self._episodes_open = False
                self._sync_episodes_layout()
        self._drawer_revealer.set_reveal_child(opened)
        log_action("beauty_drawer", open=opened)

    def _build_beauty_page(self) -> Gtk.Widget:
        """The beauty controls: master switch, strengths, settings.

        Built once and re-hosted by :meth:`_sync_secondary_placement`: the
        windowed panel tab or the fullscreen/``--record`` overlay drawer.  The
        widget names stay ``_beauty_page``/``_beauty_body`` because the v4
        cascade contract (grey the body, clear A|B and the boxes) is unchanged.
        The drawer margins/classes are set by the placement code, so the page
        itself has none.
        """
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        page.add_css_class("sidebar-panel")
        page.add_css_class("beauty-page")
        page.set_vexpand(True)
        page.set_valign(Gtk.Align.FILL)
        self._beauty_page = page

        head = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        head.add_css_class("beauty-head")
        head.add_css_class("drawer-head")
        titles = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        titles.set_hexpand(True)
        self._beauty_header = Gtk.Label(label=self._t("beauty"))
        self._beauty_header.add_css_class("beauty-title")
        self._beauty_header.set_xalign(0.0)
        titles.append(self._beauty_header)

        status = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self._beauty_status_dot = Gtk.Label(label="●")
        self._beauty_status_dot.add_css_class("beauty-status-dot")
        self._beauty_status_label = Gtk.Label()
        self._beauty_status_label.add_css_class("beauty-status")
        self._beauty_status_label.set_xalign(0.0)
        self._beauty_status_label.set_wrap(True)
        self._beauty_status_label.set_max_width_chars(34)
        status.append(self._beauty_status_dot)
        status.append(self._beauty_status_label)
        titles.append(status)
        self._drawer_face_label = Gtk.Label()
        self._drawer_face_label.add_css_class("beauty-drawer-face")
        self._drawer_face_label.set_xalign(0.0)
        self._drawer_face_label.set_ellipsize(Pango.EllipsizeMode.END)
        self._drawer_face_label.set_max_width_chars(30)
        titles.append(self._drawer_face_label)
        head.append(titles)

        self._beauty_switch = Gtk.Switch()
        self._beauty_switch.set_hexpand(False)
        self._beauty_switch.set_valign(Gtk.Align.START)
        self._beauty_switch.set_margin_top(4)
        self._beauty_switch.set_active(self.config.beauty.enabled)
        self._beauty_switch.connect("notify::active", self._on_beauty_toggled)
        head.append(self._beauty_switch)

        self._drawer_close_button = Gtk.Button(label="\u2715")
        self._drawer_close_button.add_css_class("drawer-close")
        self._drawer_close_button.set_valign(Gtk.Align.START)
        self._drawer_close_button.set_tooltip_text(self._t("close"))
        self._drawer_close_button.connect(
            "clicked", lambda _button: self._set_beauty_drawer_open(False)
        )
        head.append(self._drawer_close_button)
        page.append(head)
        self._beauty_tab_rule = DrawerTabRule()
        page.append(self._beauty_tab_rule)

        # Shown only while the master switch is off: names what was cascaded
        # and warns that re-enabling beauty does not bring it back.
        self._beauty_cascade = Gtk.Label()
        self._beauty_cascade.add_css_class("beauty-cascade")
        self._beauty_cascade.set_xalign(0.0)
        self._beauty_cascade.set_wrap(True)
        page.append(self._beauty_cascade)

        # The drawer scrolls below its fixed title, so "More settings" can
        # never push the panel past the 768 px board height.
        scroll = Gtk.ScrolledWindow()
        scroll.add_css_class("drawer-scroll")
        scroll.set_vexpand(True)
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self._drawer_scroll = scroll

        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        body.set_margin_top(2)
        body.set_margin_bottom(2)
        self._beauty_body = body
        body.append(self._build_beauty_strengths())
        body.append(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL))
        self._beauty_settings_heading = Gtk.Label()
        self._beauty_settings_heading.add_css_class("beauty-section-heading")
        self._beauty_settings_heading.set_xalign(0.0)
        body.append(self._beauty_settings_heading)
        body.append(self._build_beauty_settings())
        body.append(self._build_beauty_more())

        self._note_label = Gtk.Label()
        self._note_label.add_css_class("beauty-note")
        self._note_label.set_xalign(0.0)
        self._note_label.set_wrap(True)
        body.append(self._note_label)

        self._beauty_shortcuts = Gtk.Label()
        self._beauty_shortcuts.add_css_class("sidebar-shortcuts")
        self._beauty_shortcuts.set_xalign(0.0)
        self._beauty_shortcuts.set_wrap(True)
        body.append(self._beauty_shortcuts)

        scroll.set_child(body)
        page.append(scroll)

        # Wheel events over a Gtk.Scale adjust that scale by default.  In the
        # drawer the wheel should scroll the panel instead (see PITFALLS).
        for scale in (
            self._smoothing,
            self._whitening,
            self._split,
            self._face_slim,
            self._eye_enlarge,
        ):
            self._install_page_wheel_scroll(scale)
        self._sync_beauty_page_state()
        return page

    def _build_beauty_strengths(self) -> Gtk.Widget:
        """Smoothing and whitening: title + value above a full-width trough."""
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)

        self._smoothing = Gtk.Scale.new_with_range(
            Gtk.Orientation.HORIZONTAL, 0, 100, 1
        )
        self._smoothing.set_value(self.config.beauty.smoothing)
        self._smoothing.set_draw_value(False)
        self._smoothing.set_hexpand(True)
        self._smoothing.connect("value-changed", self._on_smoothing_changed)
        self._smoothing_label = Gtk.Label()
        self._smoothing_value = Gtk.Label(
            label=slider_value_text(self.config.beauty.smoothing)
        )
        box.append(
            _slider_block(self._smoothing_label, self._smoothing, self._smoothing_value)
        )

        self._whitening = Gtk.Scale.new_with_range(
            Gtk.Orientation.HORIZONTAL, 0, 100, 1
        )
        self._whitening.set_value(self.config.beauty.whitening)
        self._whitening.set_draw_value(False)
        self._whitening.set_hexpand(True)
        self._whitening.connect("value-changed", self._on_whitening_changed)
        self._whitening_label = Gtk.Label()
        self._whitening_value = Gtk.Label(
            label=slider_value_text(self.config.beauty.whitening)
        )
        box.append(
            _slider_block(self._whitening_label, self._whitening, self._whitening_value)
        )
        return box

    def _build_beauty_settings(self) -> Gtk.Widget:
        """The v4 settings card: A|B, split position, face boxes.

        A|B used to be a full-width button above the sliders; it is a display
        option, so it belongs with the other output switches.
        """
        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        card.add_css_class("beauty-settings")

        self._compare_switch = Gtk.Switch()
        self._compare_switch.set_active(self.config.beauty.compare_mode)
        self._compare_switch.connect("notify::active", self._on_compare_toggled)
        self._compare_title = Gtk.Label()
        self._compare_hint = Gtk.Label()
        card.append(
            self._settings_row(
                self._compare_title, self._compare_hint, self._compare_switch
            )
        )

        card.append(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL))

        # Split position only means something while A|B is on; the row dims
        # instead of staying a full-width control when the switch is off.
        self._split_row = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self._split_row.add_css_class("beauty-split-row")
        split_top = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self._split_label = Gtk.Label()
        self._split_label.add_css_class("split-title")
        self._split_label.set_xalign(0.0)
        self._split_label.set_hexpand(True)
        self._split_value = Gtk.Label(
            label=percent_value_text(self.config.beauty.split)
        )
        self._split_value.add_css_class("split-value")
        self._split_value.set_xalign(1.0)
        split_top.append(self._split_label)
        split_top.append(self._split_value)
        self._split_row.append(split_top)
        self._split = Gtk.Scale.new_with_range(
            Gtk.Orientation.HORIZONTAL, 0.1, 0.9, 0.05
        )
        self._split.set_value(self.config.beauty.split)
        self._split.set_draw_value(False)
        self._split.set_hexpand(True)
        self._split.connect("value-changed", self._on_split_changed)
        self._split_row.append(self._split)
        card.append(self._split_row)

        card.append(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL))

        self._faces_switch = Gtk.Switch()
        self._faces_switch.set_active(self._show_faces)
        self._faces_switch.connect("notify::active", self._on_show_faces_toggled)
        self._faces_label = Gtk.Label()
        self._faces_hint = Gtk.Label()
        card.append(
            self._settings_row(self._faces_label, self._faces_hint, self._faces_switch)
        )
        return card

    def _settings_row(
        self, title: Gtk.Label, hint: Gtk.Label, control: Gtk.Widget
    ) -> Gtk.Widget:
        """One card line: title + faint subtitle on the left, switch right."""
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        row.add_css_class("settings-row")
        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        text.set_hexpand(True)
        title.set_xalign(0.0)
        title.add_css_class("settings-title")
        hint.set_xalign(0.0)
        hint.add_css_class("settings-hint")
        text.append(title)
        text.append(hint)
        control.set_valign(Gtk.Align.CENTER)
        row.append(text)
        row.append(control)
        return row

    def _build_beauty_more(self) -> Gtk.Widget:
        """Collapsed "More settings" card: the experimental warp sliders."""
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)

        self._more_button = Gtk.ToggleButton()
        self._more_button.add_css_class("beauty-more-row")
        self._more_button.set_hexpand(True)
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self._more_title = Gtk.Label()
        self._more_title.add_css_class("beauty-more-title")
        self._more_title.set_xalign(0.0)
        self._more_hint = Gtk.Label()
        self._more_hint.add_css_class("beauty-more-hint")
        self._more_hint.set_xalign(1.0)
        self._more_hint.set_hexpand(True)
        self._more_chevron = Gtk.Label(label="▸")
        self._more_chevron.add_css_class("beauty-more-chevron")
        row.append(self._more_title)
        row.append(self._more_hint)
        row.append(self._more_chevron)
        self._more_button.set_child(row)
        self._more_button.connect("toggled", self._on_beauty_more_toggled)
        box.append(self._more_button)

        self._more_revealer = Gtk.Revealer()
        self._more_revealer.set_transition_type(
            Gtk.RevealerTransitionType.SLIDE_DOWN
        )
        self._more_revealer.set_transition_duration(160)
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        content.set_margin_top(8)

        self._experimental_label = Gtk.Label()
        self._experimental_label.set_xalign(0.0)
        self._experimental_label.add_css_class("experimental-heading")
        content.append(self._experimental_label)

        self._face_slim = Gtk.Scale.new_with_range(
            Gtk.Orientation.HORIZONTAL, 0, 100, 1
        )
        self._face_slim.set_value(self.config.beauty.face_slim)
        self._face_slim.set_draw_value(False)
        self._face_slim.set_hexpand(True)
        self._face_slim.connect("value-changed", self._on_face_slim_changed)
        self._face_slim_label = Gtk.Label()
        self._face_slim_value = Gtk.Label(
            label=slider_value_text(self.config.beauty.face_slim)
        )
        content.append(
            _slider_block(self._face_slim_label, self._face_slim, self._face_slim_value)
        )

        self._eye_enlarge = Gtk.Scale.new_with_range(
            Gtk.Orientation.HORIZONTAL, 0, 100, 1
        )
        self._eye_enlarge.set_value(self.config.beauty.eye_enlarge)
        self._eye_enlarge.set_draw_value(False)
        self._eye_enlarge.set_hexpand(True)
        self._eye_enlarge.connect("value-changed", self._on_eye_enlarge_changed)
        self._eye_enlarge_label = Gtk.Label()
        self._eye_enlarge_value = Gtk.Label(
            label=slider_value_text(self.config.beauty.eye_enlarge)
        )
        content.append(
            _slider_block(
                self._eye_enlarge_label, self._eye_enlarge, self._eye_enlarge_value
            )
        )

        self._experimental_note_label = Gtk.Label()
        self._experimental_note_label.set_xalign(0.0)
        self._experimental_note_label.set_wrap(True)
        self._experimental_note_label.add_css_class("dim-label")
        content.append(self._experimental_note_label)

        self._more_revealer.set_child(content)
        box.append(self._more_revealer)
        return box

    def _on_beauty_more_toggled(self, button: Gtk.ToggleButton) -> None:
        opened = button.get_active()
        self._more_revealer.set_reveal_child(opened)
        self._more_chevron.set_text("▾" if opened else "▸")

    def _install_page_wheel_scroll(self, scale: Gtk.Scale) -> None:
        """Make the wheel over a drawer scale scroll the drawer page."""
        controller = Gtk.EventControllerScroll.new(
            Gtk.EventControllerScrollFlags.VERTICAL
        )
        # Capture before GtkScale's own bubble-phase scroll handler.
        controller.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        controller.connect("scroll", self._on_page_wheel_scroll)
        scale.add_controller(controller)

    def _on_page_wheel_scroll(
        self,
        _controller: Gtk.EventControllerScroll,
        _dx: float,
        dy: float,
    ) -> bool:
        if dy == 0.0:
            return False
        drawer_scroll = getattr(self, "_drawer_scroll", None)
        if drawer_scroll is None:
            return False
        adjustment = drawer_scroll.get_vadjustment()
        if adjustment is None:
            return False
        upper = adjustment.get_upper()
        page_size = adjustment.get_page_size()
        if upper <= page_size:
            return False
        lower = adjustment.get_lower()
        value = adjustment.get_value() + dy * 48.0
        adjustment.set_value(max(lower, min(value, upper - page_size)))
        return True

    # -- language -------------------------------------------------------

    def _apply_language(self) -> None:
        """Retranslate every long-lived widget after a language change."""
        t = self._t
        self._update_title()

        self._open_button.label_widget.set_text(t("open"))
        self._open_button.set_tooltip_text(t("open_tooltip"))
        self._open_folder_button.label_widget.set_text(t("open_folder"))
        self._language_button.label_widget.set_text(t("switch_language"))
        self._language_button.set_tooltip_text(t("switch_language_tooltip"))
        self._record_button.label_widget.set_text(t("recording_toggle"))
        self._close_button.label_widget.set_text(t("close_window"))
        self._more_shortcuts_label.set_text(t("player_shortcuts"))
        self._back_button.set_tooltip_text(t("play_back"))
        self._episodes_button.set_tooltip_text(t("panel_tooltip"))
        self._top_more_button.set_tooltip_text(t("play_more"))
        self._episodes_label_text = t("detail_episodes")
        self._top_bar.set_episodes_label(self._episodes_label_text)
        if hasattr(self, "_panel_tab_episodes"):
            self._panel_tab_episodes.set_label(t("detail_episodes"))
            self._panel_tab_beauty.set_label(t("beauty"))
        self._drawer_close_button.set_tooltip_text(t("close"))
        if hasattr(self, "_header_open_label"):
            self._header_open_label.set_text(t("open"))
            self._header_open_button.set_tooltip_text(t("open"))
            self._header_open_file_row.label_widget.set_text(t("open_video"))
            self._header_open_folder_row.label_widget.set_text(t("open_folder"))
        if hasattr(self, "_episodes_close_button"):
            self._episodes_close_button.set_tooltip_text(t("close"))

        self._empty_title.set_text(t("empty_title"))
        self._empty_subtitle.set_text(t("empty_subtitle"))
        self._empty_file_button.set_label(t("empty_choose_file"))
        self._empty_folder_button.set_label(t("empty_choose_folder"))
        self._empty_hint.set_text(t("empty_drop_hint"))
        for label, key in zip(
            self._capability_labels,
            ("empty_chip_vpu", "empty_chip_npu", "empty_chip_beauty"),
        ):
            label.set_text(t(key))

        self._recents_title.set_text(t("recents"))
        self._playlist_title.set_text(t("playlist"))
        self._playlist_empty.set_text(t("playlist_empty"))
        self._playlist_add_button.set_label(t("playlist_add"))
        # Row captions (the time range) are translated too.
        self._sync_recents()
        self._sync_playlist()
        self._decode_row.set_tooltip_text(t("decode_mode"))
        for mode, segment in self._decode_buttons.items():
            segment.set_label(t(f"decode_{mode}"))
        self._beauty_header.set_text(t("beauty"))
        self._smoothing_label.set_text(t("smoothing"))
        self._whitening_label.set_text(t("whitening"))
        self._beauty_settings_heading.set_text(t("beauty_settings"))
        self._compare_title.set_text(t("compare_split"))
        self._compare_hint.set_text(t("compare_split_hint"))
        self._compare_switch.set_tooltip_text(t("compare_tooltip"))
        self._split_label.set_text(t("split_position"))
        self._faces_label.set_text(t("show_face_boxes"))
        self._faces_hint.set_text(t("show_face_boxes_hint"))
        self._note_label.set_text(t("note"))
        self._beauty_cascade.set_text(t("beauty_cascade"))
        self._more_title.set_text(t("beauty_more"))
        self._more_hint.set_text(t("beauty_more_hint"))
        self._experimental_label.set_text(t("experimental"))
        self._face_slim_label.set_text(t("face_slim"))
        self._eye_enlarge_label.set_text(t("eye_enlarge"))
        self._experimental_note_label.set_text(t("experimental_note"))
        self._beauty_shortcuts.set_text(t("beauty_shortcuts"))
        self._face_overlay.set_caption(t("face_prefix"))
        self._sync_beauty_toggles()
        self._sync_compare_toggles()
        self._sync_beauty_page_state()
        self._browse.apply_language(self._t)
        self._sync_page_chrome()

        self._play_button.set_tooltip_text(t("play_pause"))
        self._stop_button.set_tooltip_text(t("stop_playback"))
        self._prev_button.set_tooltip_text(t("previous"))
        self._next_button.set_tooltip_text(t("next"))
        self._timeline.set_tooltip_text(t("seek"))
        self._volume.set_tooltip_text(
            f"{t('volume')} {int(round(self._volume.get_value()))}%"
        )
        self._speed.set_tooltip_text(t("speed"))
        self._refresh_fullscreen_button()
        self._subtitle_button.set_label(t("play_subtitles"))
        self._subtitle_button.set_tooltip_text(t("subtitles_tooltip"))
        self._back15_button.set_tooltip_text(t("play_back15"))
        self._forward15_button.set_tooltip_text(t("play_forward15"))
        self._subtitles_menu_label.set_text(t("show_subtitles"))
        self._open_subtitle_button.set_label(t("open_subtitle"))
        self._audio_label.set_text(t("audio_track"))
        self._refresh_subtitle_status()
        self._refresh_audio_tracks()

        self._refresh_beauty_status()
        if self.backend.has_media:
            self._update_info_label()
        else:
            self._reset_info_chips()
        self._sync_compare_overlay()
        if self._hint_key is not None:
            self._hint_label.set_text(t(self._hint_key, **self._hint_values))
        self._render_playback_status()

    def _set_playback_status(self, status: str | None, message: str = "") -> None:
        self._playback_status = status
        self._playback_status_message = message
        self._render_playback_status()
        builder = self._canvas_builder
        if builder is not None:
            builder.refresh(("playback-status",))

    def _render_playback_status(self) -> None:
        """Refresh the status card text, actions, and visibility for current state."""
        if not hasattr(self, "_status_card"):
            return
        status = self._playback_status
        self._sync_chrome_visibility()
        if status is None:
            self._status_spinner.stop()
            self._status_card.set_visible(False)
            return

        t = self._t
        state_classes = (
            "status-loading",
            "status-paused",
            "status-error",
            "status-ended",
        )
        for css_class in state_classes:
            self._status_card.remove_css_class(css_class)
        self._status_icon.remove_css_class("status-error-symbol")
        self._status_card.add_css_class(f"status-{status}")

        title_keys = {
            "loading": "status_loading_title",
            "paused": "status_paused_title",
            "error": "status_error_title",
            "ended": "status_ended_title",
        }
        message_keys = {
            "loading": "status_loading_message",
            "paused": "status_paused_message",
            "error": "status_error_message",
            "ended": "status_ended_message",
        }
        icon_names = {
            "paused": "media-playback-start-symbolic",
            "error": "dialog-error-symbolic",
            "ended": "media-playback-stop-symbolic",
        }
        if status not in title_keys:
            raise ValueError(f"unknown playback status: {status!r}")

        self._status_title.set_text(t(title_keys[status]))
        self._status_message.set_text(
            self._playback_status_message or t(message_keys[status])
        )
        is_loading = status == "loading"
        self._status_spinner.set_visible(is_loading)
        if is_loading:
            self._status_spinner.start()
            self._status_icon.set_visible(False)
        else:
            self._status_spinner.stop()
            self._status_icon.set_from_icon_name(icon_names[status])
            self._status_icon.set_visible(True)
        if status == "error":
            self._status_icon.add_css_class("status-error-symbol")

        primary_keys = {
            "paused": "resume_playback",
            "error": "retry_playback",
            "ended": "replay_video",
        }
        primary_key = primary_keys.get(status)
        self._status_primary_button.set_visible(primary_key is not None)
        self._status_secondary_button.set_visible(status in ("error", "ended"))
        self._status_actions.set_visible(primary_key is not None)
        if primary_key is not None:
            self._status_primary_button.set_label(t(primary_key))
        self._status_secondary_button.set_label(t("return_to_playlist"))
        self._status_card.set_visible(True)

    def _sync_chrome_visibility(self) -> None:
        """Show the empty drop zone or the playback chrome for the current state.

        The v3 empty state has no transport at all: no clip means no progress,
        volume or decode controls to operate.  Loading / error cards hide the
        drop zone so two empty-state panels never stack.
        """
        if not hasattr(self, "_empty_state"):
            return
        state = chrome_state(self.backend.has_media, self._playback_status)
        self._empty_state.set_visible(state.empty)
        # In windowed mode the dock is always up; in immersive mode its
        # revealer is owned by the idle HUD.  A clip-less player hides it.
        revealer = getattr(self, "_dock_revealer", None)
        if revealer is not None:
            revealer.set_visible(state.transport)
            if not self._immersive:
                revealer.set_reveal_child(state.transport)
        self._info_bar.set_visible(
            state.info and bool(self.config.debug.show_info_panel)
        )
        if hasattr(self, "_fullscreen_progress"):
            self._fullscreen_progress.set_visible(self._immersive and state.info)
        self._sync_veils()

    def _sync_veils(self) -> None:
        """Windowed keeps both scrims; fullscreen dims only while the dock is up."""
        if not hasattr(self, "_top_veil"):
            return
        if not self.backend.has_media:
            self._top_veil.set_visible(False)
            self._bottom_veil.set_visible(False)
            return
        if self._immersive:
            self._top_veil.set_visible(False)
            self._bottom_veil.set_visible(
                self._dock_revealer.get_reveal_child()
            )
        else:
            self._top_veil.set_visible(True)
            self._bottom_veil.set_visible(True)

    def _sync_compare_overlay(self) -> None:
        """Feed the split captions from the live beauty config and frame size."""
        if not hasattr(self, "_compare_overlay"):
            return
        beauty = self.config.beauty
        info = self.backend.video_info
        frame_width = float(info.get("width") or 0)
        frame_height = float(info.get("height") or 0)
        visible = bool(
            self.backend.has_media
            and beauty.compare_mode
            and beauty.enabled
            and frame_width > 0
            and frame_height > 0
        )
        params = BeautyParams.from_config(beauty)
        params_text = self._t(
            "compare_params",
            smoothing=params.smoothing,
            whitening=params.whitening,
        )
        self._compare_overlay.set_state(
            visible=visible,
            split=float(beauty.split),
            frame_width=frame_width,
            frame_height=frame_height,
            labels=(self._t("compare_original"), self._t("compare_beautified")),
            params=params_text if visible else "",
        )

    def _sync_beauty_toggles(self) -> None:
        """Mirror beauty state into the page switch, dock pill and panel chip.

        The pill carries the state in its label ("美颜 · 开 / 关"): the dock is
        the only entrance in fullscreen and a checked tint alone is easy to
        miss on a 1280 px panel.  While windowed the pill is hidden, so the
        icon-only panel chip gets an amber dot for the same information.
        """
        if self._updating_toggles:
            return
        enabled = bool(self.config.beauty.enabled)
        self._updating_toggles = True
        try:
            if self._beauty_switch.get_active() != enabled:
                self._beauty_switch.set_active(enabled)
            if self._dock_beauty_button.get_active() != enabled:
                self._dock_beauty_button.set_active(enabled)
        finally:
            self._updating_toggles = False
        self._dock_beauty_button.set_label(
            self._t("beauty_pill_on" if enabled else "beauty_pill_off")
        )
        dot = getattr(self, "_panel_dot", None)
        if dot is not None:
            dot.set_visible(enabled)

    def _sync_compare_toggles(self) -> None:
        """Mirror compare state into the A|B switch and the dock pill.

        A|B needs beauty, so the fullscreen pill stays visible but disabled
        while the effect is off, labelled "对比 · 需美颜" (v4 mockup).
        """
        if self._updating_toggles:
            return
        state = beauty_page_state(
            enabled=self.config.beauty.enabled,
            compare_mode=self.config.beauty.compare_mode,
            show_faces=self._show_faces,
        )
        self._updating_toggles = True
        try:
            if self._compare_switch.get_active() != state.compare:
                self._compare_switch.set_active(state.compare)
            if self._dock_compare_button.get_active() != state.compare:
                self._dock_compare_button.set_active(state.compare)
        finally:
            self._updating_toggles = False
        self._dock_compare_button.set_sensitive(state.dock_compare_sensitive)
        self._dock_compare_button.set_label(self._t(state.dock_compare_label))

    def _sync_beauty_page_state(self) -> None:
        """Grey the beauty drawer body while its master switch is off (v4).

        The switch itself stays live; everything it owns (strengths, the
        settings card, the experimental expander) drops to 34 % and becomes
        insensitive, and the cascade notice replaces the footer note.
        """
        if not hasattr(self, "_beauty_body"):
            return
        state = beauty_page_state(
            enabled=self.config.beauty.enabled,
            compare_mode=self.config.beauty.compare_mode,
            show_faces=self._show_faces,
        )
        if state.page_off:
            self._beauty_page.add_css_class("is-off")
        else:
            self._beauty_page.remove_css_class("is-off")
        self._beauty_body.set_sensitive(state.body_sensitive)
        self._beauty_body.set_opacity(state.body_opacity)
        self._beauty_cascade.set_visible(state.cascade_visible)
        self._note_label.set_visible(state.note_visible)
        self._split_row.set_sensitive(state.split_sensitive)
        self._split_row.set_opacity(state.split_opacity)
        self._refresh_beauty_status()

    def _refresh_beauty_status(self) -> None:
        """Status line under the drawer title: GPU time plus the NPU face count."""
        beauty = self.config.beauty
        if not beauty.enabled:
            text = self._t("beauty_status_off")
        elif self.backend.beauty_available:
            text = self._t("beauty_status_on", ms=self.backend.beauty_ms)
        else:
            text = self._t("beauty_status_on_pending")
        self._beauty_status_label.set_text(text)
        if hasattr(self, "_drawer_face_label"):
            self._drawer_face_label.set_text(self._drawer_face_text())

    def _drawer_face_text(self) -> str:
        """What the NPU currently reports, in the drawer's status line."""
        if self._detection_error:
            return self._t("drawer_face_error")
        if not self._detection_needed():
            return self._t("drawer_face_off")
        if self._faces:
            return self._t("drawer_face_count", count=len(self._faces))
        return self._t("drawer_face_none")

    def _set_beauty_enabled(self, enabled: bool) -> None:
        """Apply beauty from either entrance and cascade its dependants.

        v4: turning the master switch off also turns off the A|B split, the
        face boxes and (already) the effect.  Re-enabling beauty deliberately
        does not restore the split or the boxes; they stay off until the user
        asks for them again.
        """
        cascade = bool(self.config.beauty.compare_mode or self._show_faces)
        # The helper is the single definition of the cascade: with beauty off
        # A|B and the boxes read as off, so they are written back that way.
        state = beauty_page_state(
            enabled=enabled,
            compare_mode=self.config.beauty.compare_mode,
            show_faces=self._show_faces,
        )
        self._updating_toggles = True
        try:
            self.config.beauty.enabled = state.enabled
            self.config.beauty.compare_mode = state.compare
            self._show_faces = state.faces
            if not state.enabled:
                self._compare_switch.set_active(False)
                self._dock_compare_button.set_active(False)
                self._faces_switch.set_active(False)
                self._face_overlay.set_faces((), 0.0, 0.0)
            self._beauty_switch.set_active(state.enabled)
            self._dock_beauty_button.set_active(state.enabled)
        finally:
            self._updating_toggles = False
        self._refresh_beauty()
        self._on_detection_dependency_toggled()
        self._sync_beauty_page_state()
        self._sync_beauty_toggles()
        self._sync_compare_toggles()
        log_action("beauty", enabled=enabled, cascade=cascade)

    def _set_compare_enabled(self, enabled: bool) -> None:
        """Apply A|B from either entrance; it is only available with beauty on.

        v3 silently turned beauty on when A|B was enabled.  In v4 the split
        lives inside the beauty page and the dock pill reads "需美颜", so the
        gate refuses instead of switching on an effect the user turned off.
        """
        if enabled and not self.config.beauty.enabled:
            self._updating_toggles = True
            try:
                self._compare_switch.set_active(False)
                self._dock_compare_button.set_active(False)
            finally:
                self._updating_toggles = False
            self._set_hint_key("compare_requires_beauty")
            self._sync_compare_toggles()
            return
        self._updating_toggles = True
        try:
            self.config.beauty.compare_mode = enabled
            self._compare_switch.set_active(enabled)
            self._dock_compare_button.set_active(enabled)
        finally:
            self._updating_toggles = False
        self._refresh_beauty()
        self._sync_beauty_page_state()
        self._sync_compare_toggles()
        log_action("compare", enabled=enabled)

    def _on_dock_compare_toggled(self, button: Gtk.ToggleButton) -> None:
        if self._updating_toggles:
            return
        self._set_compare_enabled(button.get_active())

    def _on_files_dropped(self, _target, value, _x: float, _y: float) -> bool:
        """Drop target on the picture: files and folders feed the playlist."""
        self._dropzone.remove_css_class("drag-active")
        paths: list[Path] = []
        try:
            files = value.get_files()
        except (AttributeError, TypeError):
            files = []
        for file in files:
            path = file.get_path()
            if path:
                paths.append(Path(path))
        self._add_dropped_paths(paths)
        return True

    def _on_drop_enter(self, *_args: object) -> int:
        self._dropzone.add_css_class("drag-active")
        return int(Gdk.DragAction.COPY)

    def _on_drop_leave(self, *_args: object) -> None:
        self._dropzone.remove_css_class("drag-active")

    def _add_dropped_paths(self, paths: list[Path]) -> None:
        if not paths:
            self._set_hint_key("drop_empty")
            return
        try:
            media = expand_media_args(paths)
        except ValueError:
            self._set_hint_key("drop_empty")
            return
        if not media:
            self._set_hint_key("drop_empty")
            return
        for path in media:
            self.playlist.add(path)
        self._sync_playlist()
        log_action("drop_media", count=len(media))
        self._play_path(media[0])

    def _on_open_folder_clicked(self, _button: Gtk.Button) -> None:
        log_action("open_folder_dialog")
        dialog = Gtk.FileDialog(title=self._t("open_folder"))
        dialog.select_folder(self, None, self._on_folder_chosen)

    def _on_folder_chosen(
        self, dialog: Gtk.FileDialog, result: Gio.AsyncResult
    ) -> None:
        try:
            gfile = dialog.select_folder_finish(result)
        except GLib.Error:
            log_action("open_folder_cancelled")
            return
        if gfile is None:
            return
        path_text = gfile.get_path()
        if not path_text:
            return
        try:
            media = expand_media_args([Path(path_text)])
        except ValueError:
            media = []
        if not media:
            self._set_hint_key("folder_empty")
            return
        for path in media:
            self.playlist.add(path)
        self._sync_playlist()
        log_action("open_folder_selected", count=len(media))
        self._play_path(media[0])

    def _on_status_primary_clicked(self, _button: Gtk.Button) -> None:
        if self._playback_status == "paused":
            self._on_play_clicked(None)
        elif self._playback_status in ("error", "ended"):
            self._retry_playback()

    def _on_status_secondary_clicked(self, _button: Gtk.Button) -> None:
        # "Back to the list" means the library in the v5 shell.
        self._show_page("browse")

    def _retry_playback(self) -> None:
        path = self._playback_path or self._current_path
        if path is not None:
            self._play_path(path)

    def _return_to_playlist(self) -> None:
        self._cancel_surface_tap()
        log_action("return_to_playlist")
        if hasattr(self, "_more_popover"):
            self._more_popover.popdown()
        self._set_beauty_drawer_open(False)
        if self._recorder is not None:
            self._stop_recording()
        # "Back to the playlist" is still leaving a clip mid-way, so the history
        # and the resume position must see it before the pipeline goes away.
        self._save_resume_position(remember=True)
        self.backend.close()
        self._current_path = None
        self._playback_path = None
        self._chapters = ()
        self._chapter_duration_ns = 0
        self._playback_started = False
        self._fps = 0.0
        self._duration_ns = 0
        self._faces = ()
        self._detection_error = None
        self._face_overlay.set_faces((), 0.0, 0.0)
        self._picture.set_paintable(None)
        self._updating_seek = True
        self._seek.set_value(0)
        self._updating_seek = False
        self._set_timecode("00:00 / 00:00")
        self._update_title()
        self._update_play_icon(False)
        self._reset_info_chips()
        self._set_playback_status(None)
        self._sync_playlist()

    def _on_language_clicked(self, _button: Gtk.Button) -> None:
        self._set_language("en" if self._t.language == "zh" else "zh")

    def _set_language(self, language: str) -> None:
        """Switch the UI language from the player's more menu or settings.

        The choice is persisted immediately; a read-only home directory must
        not break the switch, so a failed save is ignored on purpose.
        """
        if language not in LANGUAGES or language == self._t.language:
            return
        self._t.language = language
        try:
            save_language(language)
        except OSError:
            pass
        self._apply_language()
        log_action("language", lang=language)

    def _set_decode_mode(self, mode: str) -> None:
        """Set the decode strategy (transport segments or the settings page).

        On the player page the current clip is reopened so the new path takes
        effect; from the settings page there is nothing open, so the value is
        staged and applies to the next file.
        """
        if mode not in DECODE_MODES or mode == self.config.player.decode_mode:
            self._sync_decode_buttons()
            return
        self.config.player.decode_mode = mode
        log_action("decode_mode", mode=mode)
        if self._page == "player" and self.backend.has_media:
            self._apply_decode_mode(mode)
        self._sync_decode_buttons()
        self._browse.settings.sync_decode_mode()

    # -- pages (ui-v5 browse shell) -------------------------------------

    def _show_page(self, page: str, *, refresh: bool = True) -> None:
        """Switch between the browse shell and the player.

        Browsing means the clip is over, so returning to the shell persists the
        resume position and the history, closes the pipeline and clears the
        player chrome — the same work "back to playlist" always did.  The
        browse shell remembers which of its pages (home / library / recent /
        detail) the player was opened from, so "back" lands there.
        """
        if page not in PAGE_NAMES:
            raise ValueError(f"unknown page: {page!r}")
        if page == "browse":
            # Clears playback, the error card and the recording overlay in one
            # place; safe without media because every step is a no-op then.
            self._return_to_playlist()
        changed = page != self._page
        self._page = page
        self._stack.set_visible_child_name(page)
        if page == "browse":
            self._browse.activate()
        else:
            self._browse.mark_player_origin()
        self._sync_page_chrome()
        if page == "player":
            # Probing decodes video; the player page must own the VPU.
            self._browse.suspend()
        if changed:
            log_action("page", page=page)
        if page == "browse" and refresh:
            GLib.idle_add(self._refresh_browse)

    def _refresh_browse(self) -> bool:
        if not self._closed:
            self._browse.refresh()
        return GLib.SOURCE_REMOVE

    def _on_library_changed(self) -> None:
        """The browse shell re-scanned or changed pages: refresh the header."""
        if self._page == "browse":
            self._sync_page_chrome()

    def _on_library_dropped(self, paths: Sequence[Path]) -> None:
        """Video files dropped on a browse page become the playlist."""
        if not paths:
            return
        log_action("library_drop", count=len(paths))
        self._play_paths(paths, 0)

    def _on_back_clicked(self, _button: Gtk.Button) -> None:
        log_action("back_to_browse")
        self._show_page("browse")

    def _play_paths(
        self,
        paths: Sequence[Path],
        index: int = 0,
        start_position_ns: int | None = None,
    ) -> None:
        """Play *paths* from *index*; the list the user sees is the playlist.

        The home rows pass their own four cards, the detail page passes the
        folder's episodes and a dropped set of files passes itself, so
        previous/next and auto-advance always walk the list the viewer was
        looking at when the clip started.
        """
        files = [Path(path) for path in paths]
        if not files:
            return
        index = max(0, min(int(index), len(files) - 1))
        self.playlist = Playlist(files)
        try:
            self.playlist.select(index)
        except IndexError:
            pass
        log_action("browse_play", path=str(files[index]), count=len(files))
        self._show_page("player", refresh=False)
        if start_position_ns is None:
            self._play_path(files[index])
        else:
            # The detail page's "replay" must ignore the stored resume point.
            self._play_path(files[index], start_position_ns=start_position_ns)

    def _sync_page_chrome(self) -> None:
        """Header follows the page and the windowed/fullscreen distinction.

        Windowed playback now keeps the real GTK header bar (clip title and
        window buttons) while the picture top bar runs in *compact* mode;
        fullscreen hides the header bar and restores the titles.  ``--record``
        keeps the pre-existing no-header canvas so the captured layout does
        not change.  The docked episodes column follows the same gating.
        """
        browse = self._page == "browse"
        windowed_player = (
            not browse and not self._immersive and self._record_path is None
        )
        self._headerbar.set_visible(browse or windowed_player)
        self._back_button.set_visible(not browse)
        self._top_bar.set_visible(not browse and not self._immersive)
        self._top_bar.set_compact(windowed_player)
        if browse:
            title, text = self._browse.headline()
            self._title_label.set_text(title)
            self.set_title(self._t("app_title"))
            self._header_subtitle_label.set_text(text)
            self._header_subtitle_label.set_visible(True)
            self._header_subtitle_cache = text
        else:
            self._header_subtitle_cache = ""
            self._update_title()
        self._sync_episodes_layout()

    # -- playback -------------------------------------------------------

    def _apply_demo_mode(self) -> None:
        if not self.demo:
            return
        self._info_bar.set_visible(True)
        # Beauty is off by default (player first), but --demo is the explicit
        # beauty showcase.  With u_active=0 the shader returns the untouched
        # frame before drawing the F9 divider, so the compare view would be a
        # plain picture if we did not opt in here.
        self.config.beauty.enabled = True
        self._beauty_switch.set_active(True)
        # Beauty's oval mask is built from the NPU boxes, so the demo has to
        # opt into detection too.  This runs before open(), while the warm-up
        # is still safe (pre-PLAYING, on the main thread).
        self.config.detection.enabled = True
        # F9: the demo mode always shows the original/beautified split.
        self.config.beauty.compare_mode = True
        self._compare_switch.set_active(True)
        self._faces_switch.set_active(True)
        # On stage the operator drives the sliders live, so "More settings" is
        # open in demo mode even though it is collapsed by default.
        self._more_button.set_active(True)
        # P4: the fullscreen showcase keeps the picture clean; the drawer is
        # one tap away on the beauty pill (v4 also hid the sidebar in fullscreen).
        self._set_beauty_drawer_open(False)
        self.backend.refresh_beauty()
        self._refresh_beauty_status()
        self._sync_compare_overlay()
        self._sync_beauty_toggles()
        self._sync_compare_toggles()
        self._sync_beauty_page_state()

    def _start_index(self, index: int) -> bool:
        try:
            path = self.playlist.select(index)
        except IndexError:
            return False
        self._play_path(path)
        return False

    def _detection_needed(self) -> bool:
        """True when any NPU-backed feature is on (or explicitly requested)."""
        return bool(
            self._detection_opted_in
            or self.demo
            or self.config.beauty.enabled
            or self._show_faces
        )

    def _sync_detection_need(self) -> None:
        """Resolve whether this ``open()`` should prepare the NPU detector.

        Detection costs a ~1.7 s synchronous warm-up (model load + one large
        dummy inference) on the GTK main thread, paid before PLAYING.  A plain
        player must not pay it, so the shipped default is off; --demo and the
        beauty/face-box switches request it.  It is deliberately resolved here,
        right before ``backend.open()``, and never while a pipeline is live:
        warming the NPU with decoder/GL buffers alive aborts librknnrt on the
        RK3576 with ``std::bad_alloc``.
        """
        self.config.detection.enabled = self._detection_needed()

    def _on_detection_dependency_toggled(self) -> None:
        """Start the NPU when a dependent switch is turned on mid-playback.

        The warm-up must happen before the new pipeline goes to PLAYING (see
        :meth:`_sync_detection_need`), so a live clip is reopened from its
        current position.  Only warning about "the next clip" left the beauty
        switch lit with no face box for the shader to paint: on the board that
        read as "beauty is on but nothing happens".
        """
        needed = self._detection_needed()
        if not needed:
            self._cancel_detection_reopen()
            if self._hint_key == "detection_enabling":
                self._set_hint_key(None)
            return
        if self.config.detection.enabled:
            self._cancel_detection_reopen()
            return
        if not self.backend.has_media:
            # Nothing open yet: the next _play_path() resolves detection.
            return
        path = self._playback_path or self._current_path
        if path is None:
            # Media without a path should not happen, but a staged rebuild is
            # still better than pretending detection is already live.
            self._set_hint_key("detection_pending")
            return
        # Capture the position while the live pipeline is still alive, then
        # rebuild on idle: tearing down the pipeline (and its bus) inside the
        # switch's own notify::active emission is the failure documented in
        # PITFALLS.md.
        position_ns = max(0, int(self.backend.position_ns()))
        self._cancel_detection_reopen()
        self._detection_reopen_id = GLib.idle_add(
            self._reopen_for_detection, path, position_ns
        )
        self._set_hint_key("detection_enabling")

    def _cancel_detection_reopen(self) -> None:
        """Drop a queued detection rebuild (the switches changed again)."""
        if self._detection_reopen_id:
            GLib.source_remove(self._detection_reopen_id)
            self._detection_reopen_id = 0

    def _reopen_for_detection(self, path: Path, position_ns: int) -> bool:
        """Idle callback: rebuild once the toggle's signal has returned."""
        self._detection_reopen_id = 0
        if self._closed or not self._detection_needed():
            return GLib.SOURCE_REMOVE
        if (self._playback_path or self._current_path) != path:
            # Another clip was opened while this rebuild was queued; that open
            # already resolved detection from the same switches.
            return GLib.SOURCE_REMOVE
        if not self.backend.has_media:
            return GLib.SOURCE_REMOVE
        self._play_path(path, start_position_ns=position_ns, keep_hint=True)
        return GLib.SOURCE_REMOVE

    def _play_path(
        self,
        path: Path,
        *,
        start_position_ns: int | None = None,
        keep_hint: bool = False,
    ) -> None:
        """Open ``path``, optionally at an explicit position instead of resume.

        ``keep_hint`` leaves the current toast alone: the detection rebuild
        sets "正在启用人脸检测" just before this call and must not replace it
        with the resume confirmation.
        """
        self._cancel_surface_tap()
        log_action("play_path", path=path)
        self._cancel_detection_reopen()
        # Persist the outgoing clip first: ``position_ns()`` is only meaningful
        # while the old pipeline is still alive.
        self._save_resume_position(remember=True)
        resume_ns = self._resume_target_ns(path, start_position_ns)
        self._playback_path = path
        self._current_path = path
        self._chapters = load_chapters(path)
        self._chapter_duration_ns = 0
        if hasattr(self, "_seek"):
            self._seek.set_chapters(())
        self._update_title()
        self._apply_subtitle(find_sidecar(path))
        self._playback_started = False
        self._fps = 0.0
        self._duration_ns = 0
        self._detection_ms = 0.0
        self._detection_error = None
        self._faces = ()
        self._face_overlay.set_faces((), 0.0, 0.0)
        self._picture.set_paintable(None)
        self._reset_info_chips()
        self._set_timecode("00:00 / 00:00")
        self._updating_seek = True
        self._seek.set_value(0)
        self._updating_seek = False

        if not path.exists():
            self.backend.close()
            self._current_path = None
            self._update_title()
            self._show_error(self._t("media_file_not_found", path=path))
            return
        self._set_playback_status("loading")
        self._sync_detection_need()
        list_index = self.playlist.add(path)
        self.playlist.select(list_index)
        try:
            self.backend.open(path, autoplay=True, start_position_ns=resume_ns)
        except PlaybackError as exc:
            self._show_error(str(exc))
            return
        log_action("play_path_ok", path=path)
        paintable = self.backend.paintable
        if paintable is not None:
            self._picture.set_paintable(paintable)
        self._sync_playlist()
        # Opening a file is what makes it recent; the stored duration is
        # unknown at this point, touch() keeps whatever it had before.
        if self._recents is not None:
            self._remember_recent(path, resume_ns, 0)
        self._update_play_icon(True)
        self._current_path = path
        self.set_title(self._t("title_with_media", name=path.name))
        if not keep_hint:
            if resume_ns > 0:
                # Confirm the jump: a player that silently starts at 00:12 looks
                # broken on a demo table.
                self._set_hint_key("resumed_from", time=_format_time(resume_ns))
            else:
                self._set_hint_key(None)

    def _on_play_clicked(self, _button: Gtk.Button | None) -> None:
        if self._playback_status == "error":
            self._retry_playback()
            return
        if not self.backend.has_media:
            if self.playlist:
                self._start_index(0)
            else:
                self._on_open_clicked(self._open_button)
            return
        self.backend.toggle_play()
        playing = self.backend.is_playing
        self._update_play_icon(playing)
        # Pausing stays silent too: the card used to cover the very frame the
        # operator was looking at, and the transport icon already says it.
        self._set_playback_status(None)
        if self._hint_key == "hint_finished":
            self._set_hint_key(None)
        log_action("play_toggle", playing=playing)

    def _on_stop_clicked(self, _button: Gtk.Button) -> None:
        """Stop the current clip and rewind it to 00:00."""
        log_action("stop")
        if not self.backend.has_media:
            if self.playlist:
                self._start_index(0)
            return
        # Stopping is the opposite of resuming: drop the stored position so the
        # next launch starts at 00:00 instead of jumping back into the clip, and
        # reset the row in "recently played" to match.
        path = self._playback_path or self._current_path
        if path is not None:
            self._remember_recent(path, 0, self._duration_ns)
        self._clear_resume_position()
        self._playback_started = False
        self.backend.stop()
        self._update_play_icon(False)
        self._set_playback_status(None)
        self._faces = ()
        self._face_overlay.set_faces((), 0.0, 0.0)
        self._updating_seek = True
        self._seek.set_value(0)
        self._updating_seek = False
        self._set_timecode(f"00:00 / {_format_time(self._duration_ns)}")
        self._set_progress_fraction(0.0)
        self._set_hint_key("hint_stopped")

    def _on_decode_toggled(self, button: Gtk.ToggleButton, mode: str) -> None:
        if self._updating_decode:
            return
        if not button.get_active():
            # GTK emits "toggled" for the segment it deactivates as well.  Never
            # force it back on: re-activating it used to fight the radio group,
            # leave two segments active and retrigger the signal until the row
            # stopped responding after a single click.
            return
        if mode != self.config.player.decode_mode:
            self.config.player.decode_mode = mode
            log_action("decode_mode", mode=mode)
            self._apply_decode_mode(mode)
        # Re-assert the config as the single source of truth.  Programmatic
        # set_active() calls below are guarded, so they cannot loop back here.
        self._sync_decode_buttons()

    def _sync_decode_buttons(self) -> None:
        """Mirror ``config.player.decode_mode`` into the segments."""
        self._updating_decode = True
        try:
            wanted = self.config.player.decode_mode
            for mode, segment in self._decode_buttons.items():
                if segment.get_active() != (mode == wanted):
                    segment.set_active(mode == wanted)
        finally:
            self._updating_decode = False

    def _apply_decode_mode(self, mode: str) -> None:
        """Reopen the current clip so the new decode path takes effect."""
        mode_label = self._t(f"decode_{mode}")
        path = self._playback_path or self._current_path
        if path is None or not self.backend.has_media:
            # Nothing open yet: the new mode applies to the next file.
            self._set_hint_key("decode_staged", mode=mode_label)
            return
        # _play_path persists the position before rebuilding and resumes from
        # it, so the viewer keeps their place across the reopen.  Defer it to
        # the next main-loop turn: this runs inside a "toggled" emission and
        # tearing down the pipeline (and its bus) while GTK is still
        # dispatching that signal is the failure documented in PITFALLS.md.
        if self._decode_reopen_id:
            GLib.source_remove(self._decode_reopen_id)
            self._decode_reopen_id = 0
        self._decode_reopen_id = GLib.idle_add(
            self._reopen_for_decode_mode, mode, path
        )
        self._set_hint_key("decode_switched", mode=mode_label)

    def _reopen_for_decode_mode(self, mode: str, path: Path) -> bool:
        """Idle callback: rebuild once the click's signal has returned."""
        self._decode_reopen_id = 0
        if self._closed or mode != self.config.player.decode_mode:
            # Window closed, or a newer click superseded this rebuild.
            return GLib.SOURCE_REMOVE
        self._play_path(path)
        return GLib.SOURCE_REMOVE

    def _on_decode_fallback(self, message: str) -> None:
        """The backend dropped from the VPU to the CPU decoder on its own."""
        log_action("decode_fallback", message=message)
        # The pipeline is about to be rebuilt, so show the loading card again
        # instead of leaving the last frame frozen.
        self._set_playback_status("loading")
        self._set_hint_key("decode_fallback", message=message[:80])
        # A fallback is a warning, not a failure: amber, not red.
        self._hint_label.add_css_class("warn-hint")

    def _on_previous_clicked(self, _button: Gtk.Button) -> None:
        if not self.backend.has_media:
            self._start_index(0)
            return
        target = self.playlist.previous()
        if target is None:
            self._set_hint_key("hint_first")
            return
        log_action("previous")
        self._play_path(target)

    def _on_next_clicked(self, _button: Gtk.Button) -> None:
        if not self.backend.has_media:
            self._start_index(0)
            return
        target = self.playlist.next()
        if target is None:
            self._set_hint_key("hint_last")
            return
        log_action("next")
        self._play_path(target)

    def _on_seek_changed(self, scale: Gtk.Scale) -> None:
        if self._updating_seek or not self.backend.has_media:
            return
        fraction = max(0.0, min(1.0, scale.get_value() / 1000.0))
        if self._duration_ns > 0:
            target = int(self._duration_ns * fraction)
            self._set_timecode(
                f"{_format_time(target)} / {_format_time(self._duration_ns)}"
            )
        self.backend.seek_fraction(fraction)
        self._set_progress_fraction(fraction)

    def _on_volume_changed(self, scale: Gtk.Scale) -> None:
        volume = int(round(scale.get_value()))
        self.config.player.volume = volume
        self.backend.set_volume(volume)
        # The row has no room for a static "80%" label; the value follows the
        # pointer/focus in the tooltip instead (the icon still names the band).
        self._volume.set_tooltip_text(f"{self._t('volume')} {volume}%")
        icon = (
            "audio-volume-muted-symbolic"
            if volume == 0
            else "audio-volume-low-symbolic"
            if volume < 35
            else "audio-volume-high-symbolic"
        )
        self._volume_icon.set_from_icon_name(icon)

    def _on_speed_changed(self, dropdown: Gtk.DropDown, _param: object) -> None:
        index = dropdown.get_selected()
        if 0 <= index < len(SPEED_VALUES):
            self.config.player.speed = SPEED_VALUES[index]
            self.backend.set_rate(SPEED_VALUES[index])

    def _on_fullscreen_clicked(self, _button: Gtk.Button | None) -> None:
        if self._page != "player":
            return
        if self.is_fullscreen():
            self.unfullscreen()
            log_action("fullscreen", on=False)
        else:
            self.fullscreen()
            log_action("fullscreen", on=True)
        self._sync_immersive()

    # -- fullscreen HUD ---------------------------------------------------

    def _sync_immersive(self) -> None:
        """Enter or leave the fullscreen HUD, matching the window state.

        ``--record`` runs fullscreen too, but keeps the windowed layout: the
        capture canvas is composed from the live allocations of the very
        widgets this mode moves and hides.
        """
        want = self.is_fullscreen() and self._record_path is None
        if want != self._immersive:
            self._immersive = want
            if want:
                self._enter_immersive()
            else:
                self._leave_immersive()
            log_action("immersive", on=want)
        self._refresh_fullscreen_button()

    def _enter_immersive(self) -> None:
        """Fullscreen: picture only; top chrome leaves, the bottom dock fades.

        The dock revealer is the same widget the windowed layout always shows,
        so a video cannot look different in the two modes.  ``--record`` keeps
        the windowed layout (the recorder composes from these allocations).
        """
        self._immersive = True
        self._headerbar.set_visible(False)
        self._top_bar.set_visible(False)
        self._immersive_tools.set_visible(True)
        self._player_overlay.add_css_class("immersive")
        self._dock_revealer.set_visible(self.backend.has_media)
        self._dock_revealer.set_reveal_child(self.backend.has_media)
        # The always-on corner line appears only after the dock fades, so the
        # picture does not carry both telemetry rows at once.
        self._telemetry_box.set_visible(False)
        self._telemetry_box.set_opacity(1.0)
        self._fullscreen_progress.set_visible(self.backend.has_media)
        self._sync_compare_overlay()
        self._sync_subtitle_margin()
        self._sync_veils()
        self._dock_hover = False
        self._dock_deadline = time.monotonic() + DOCK_IDLE_SECONDS
        self._sync_episodes_layout()

    def _leave_immersive(self) -> None:
        self._immersive = False
        self._headerbar.set_visible(
            self._page == "player" and self._record_path is None
        )
        self._top_bar.set_visible(self._page == "player")
        self._telemetry_box.set_visible(False)
        self._telemetry_box.set_opacity(1.0)
        self._fullscreen_progress.set_visible(False)
        self._immersive_tools.set_visible(False)
        self._player_overlay.remove_css_class("immersive")
        self._dock_hover = False
        self._dock_deadline = 0.0
        self._sync_subtitle_margin()
        self._sync_veils()
        self._sync_chrome_visibility()
        self._sync_episodes_layout()

    def _show_dock(self) -> None:
        if not self._immersive:
            return
        self._dock_deadline = time.monotonic() + DOCK_IDLE_SECONDS
        self._telemetry_box.set_visible(False)
        if not self._dock_revealer.get_reveal_child():
            self._dock_revealer.set_reveal_child(True)
            self._sync_subtitle_margin()
            self._sync_veils()

    def _maybe_hide_dock(self) -> None:
        """Fade the dock out once the pointer has been still for a while.

        The telemetry line and the bottom progress hairline deliberately stay:
        the viewer keeps the clip identity and position after the controls go.
        A paused frame and an open beauty drawer both keep the dock up.
        """
        if not self._immersive or not self._dock_revealer.get_reveal_child():
            return
        if self._dock_hover or not self.backend.is_playing or self._drawer_open:
            self._dock_deadline = time.monotonic() + DOCK_IDLE_SECONDS
            return
        if time.monotonic() >= self._dock_deadline:
            self._dock_revealer.set_reveal_child(False)
            self._telemetry_box.set_visible(True)
            self._telemetry_box.set_opacity(TELEMETRY_IDLE_OPACITY)
            self._sync_subtitle_margin()
            self._sync_veils()

    def _sync_subtitle_margin(self) -> None:
        """Keep subtitles clear of the dock while it is on screen.

        In windowed mode the dock is always up; in fullscreen it is up until
        the idle timer hides it.
        """
        lifted = (not self._immersive) or self._dock_revealer.get_reveal_child()
        self._subtitle_label.set_margin_bottom(
            DOCK_SUBTITLE_CLEARANCE if lifted else 24
        )

    def _on_stage_pressed(
        self, gesture: Gtk.GestureClick, _n_press: int, x: float, y: float
    ) -> None:
        """Click outside the beauty drawer closes it and swallows the press.

        CAPTURE phase, because the press would otherwise also reach the video
        tap gesture (pause) or the transport keys underneath.  Claiming the
        sequence is what makes the outside click "only close the drawer"; the
        board touch panel behaves like the mouse (verified on GTK 4.14).
        """
        if not self._drawer_open:
            return
        panel = getattr(self, "_drawer_panel", None)
        stage = getattr(self, "_player_box", None)
        if panel is not None and stage is not None:
            ok, rect = panel.compute_bounds(stage)
            if ok and (
                rect.origin.x <= x <= rect.origin.x + rect.size.width
                and rect.origin.y <= y <= rect.origin.y + rect.size.height
            ):
                return
        self._set_beauty_drawer_open(False)
        self._ignore_surface_tap_until = time.monotonic() + 0.35
        gesture.set_state(Gtk.EventSequenceState.CLAIMED)

    def _on_surface_pressed(
        self, _gesture: Gtk.GestureClick, _n_press: int, x: float, y: float
    ) -> None:
        """Single tap pauses, double tap toggles fullscreen (300 ms window).

        The debounce exists because the second press of a double tap arrives
        while the single-tap action is still pending; double-tap cancels it so
        the viewer gets fullscreen without a visible play/pause flash.
        """
        if self._record_path is not None or not self.backend.has_media:
            return
        now = time.monotonic()
        if now < self._ignore_surface_tap_until:
            return
        action = self._tap_tracker.classify(now, x, y)
        self._cancel_surface_tap()
        if action == DOUBLE:
            self._on_fullscreen_clicked(None)
            log_action("surface_tap", kind="fullscreen")
            return
        self._tap_path = self._current_path
        self._tap_timeout_id = GLib.timeout_add(
            DOUBLE_TAP_MS, self._commit_surface_tap
        )

    def _commit_surface_tap(self) -> bool:
        self._tap_timeout_id = 0
        if (
            self._closed
            or self._page != "player"
            or self._record_path is not None
            or not self.backend.has_media
        ):
            return False
        if self._tap_path is not None and self._tap_path != self._current_path:
            return False
        if self._playback_status in ("error", "ended"):
            return False
        self._on_play_clicked(None)
        if self._immersive:
            self._show_dock()
        log_action("surface_tap", kind="toggle")
        return False

    def _cancel_surface_tap(self) -> None:
        if self._tap_timeout_id:
            GLib.source_remove(self._tap_timeout_id)
            self._tap_timeout_id = 0
        self._tap_path = None

    def _on_pointer_motion(
        self, _controller: Gtk.EventControllerMotion, x: float, y: float
    ) -> None:
        if not self._immersive:
            return
        # Without the hover check a dock parked under the pointer would fade
        # exactly as the viewer reaches for it.  The band is deliberately
        # generous; the dock itself is about 140 px tall.
        height = self.get_height()
        if height <= 0:
            # Motion can arrive before the first allocation; with height 0 the
            # comparison below is true for every Y and would latch the flag.
            return
        self._dock_hover = y >= height - DOCK_HOVER_BAND
        # The compositor keeps sending motion events for a resting pointer, and
        # each one used to push the fade deadline forward, so the dock never
        # hid.  Only a real move counts as intent to see the transport.
        last = self._dock_pointer
        self._dock_pointer = (x, y)
        if last is not None and abs(x - last[0]) < 3 and abs(y - last[1]) < 3:
            return
        self._show_dock()

    def _on_fullscreen_notified(self, *_args: object) -> None:
        self._sync_immersive()

    def _refresh_fullscreen_button(self) -> None:
        full = self.is_fullscreen()
        self._fullscreen_button.set_icon_name(
            "view-restore-symbolic" if full else "view-fullscreen-symbolic"
        )
        self._fullscreen_button.set_tooltip_text(
            self._t("exit_fullscreen" if full else "fullscreen")
        )

    def _on_row_activated(self, _listbox: Gtk.ListBox, row: Gtk.ListBoxRow) -> None:
        # Keep the column open: switching episodes is a browse action, not a
        # dismissal of the panel.
        log_action("playlist_row", index=row.get_index())
        self._start_index(row.get_index())

    # -- recently played -------------------------------------------------

    def _sync_recents(self) -> None:
        """Rebuild the history rows from the store.

        Rows carry their path out of band (``_recent_paths``) so activating one
        does not have to parse a label or trust a caption as a file name.
        Entries already present in the current playlist are skipped so the same
        file never appears twice in the sidebar.
        """
        while self._recents_list.get_row_at_index(0) is not None:
            row = self._recents_list.get_row_at_index(0)
            self._recents_list.remove(row)
        store = self._recents
        all_items = store.items(only_existing=True) if store is not None else ()
        items = (
            store.items(only_existing=True, exclude=self.playlist)
            if store is not None
            else ()
        )
        self._recent_paths = [item.path for item in items]
        for item in items:
            row = Gtk.ListBoxRow()
            row.add_css_class("recent-row")
            row.set_tooltip_text(str(item.path))
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
            name = Gtk.Label(label=item.name)
            name.set_xalign(0.0)
            name.set_ellipsize(Pango.EllipsizeMode.MIDDLE)
            name.add_css_class("recent-name")
            meta = Gtk.Label(label=self._recent_meta(item))
            meta.set_xalign(0.0)
            meta.add_css_class("recent-meta")
            box.append(name)
            box.append(meta)
            row.set_child(box)
            self._recents_list.append(row)
        # Three states: a normal history, a genuinely empty history (keep the
        # hint), or every entry already in the playlist (hide the whole
        # section rather than leaving a lone heading behind).
        show_section = bool(items) or not all_items
        self._recents_title.set_visible(show_section)
        self._recents_scroll.set_visible(bool(items))
        self._recents_empty.set_text(self._t("recents_empty"))
        self._recents_empty.set_visible(not all_items and show_section)

    def _recent_meta(self, item: RecentItem) -> str:
        if item.duration_ns <= 0:
            return self._t("recent_no_duration")
        return f"{_format_time(item.position_ns)} / {_format_time(item.duration_ns)}"

    def _on_recent_activated(self, _listbox: Gtk.ListBox, row: Gtk.ListBoxRow) -> None:
        index = row.get_index()
        if not 0 <= index < len(self._recent_paths):
            return
        path = self._recent_paths[index]
        log_action("recent_row", index=index, path=path)
        if not path.is_file():
            # The history outlives the file: a cleaned-up demo folder must not
            # leave a row that opens a black window.
            if self._recents is not None:
                self._recents.remove(path)
            self._sync_recents()
            self._set_hint_key("recent_missing", name=path.name)
            return
        self._play_path(path)

    def _remember_recent(self, path: Path, position_ns: int, duration_ns: int) -> None:
        if self._recents is None:
            return
        if self._recents.touch(
            path, duration_ns=duration_ns, position_ns=position_ns
        ):
            self._sync_recents()

    # -- beauty panel (M3: live shader uniforms) ------------------------

    def _on_smoothing_changed(self, scale: Gtk.Scale) -> None:
        self.config.beauty.smoothing = int(round(scale.get_value()))
        self._smoothing_value.set_text(slider_value_text(scale.get_value()))
        self._refresh_beauty()
        log_action("smoothing", value=self.config.beauty.smoothing)

    def _on_whitening_changed(self, scale: Gtk.Scale) -> None:
        self.config.beauty.whitening = int(round(scale.get_value()))
        self._whitening_value.set_text(slider_value_text(scale.get_value()))
        self._refresh_beauty()
        log_action("whitening", value=self.config.beauty.whitening)

    def _on_split_changed(self, scale: Gtk.Scale) -> None:
        self.config.beauty.split = round(scale.get_value(), 3)
        self._split_value.set_text(percent_value_text(scale.get_value()))
        self._refresh_beauty()
        log_action("split", value=self.config.beauty.split)

    def _on_face_slim_changed(self, scale: Gtk.Scale) -> None:
        self.config.beauty.face_slim = round(scale.get_value(), 1)
        self._face_slim_value.set_text(slider_value_text(scale.get_value()))
        self._refresh_beauty()
        log_action("face_slim", value=self.config.beauty.face_slim)

    def _on_eye_enlarge_changed(self, scale: Gtk.Scale) -> None:
        self.config.beauty.eye_enlarge = round(scale.get_value(), 1)
        self._eye_enlarge_value.set_text(slider_value_text(scale.get_value()))
        self._refresh_beauty()
        log_action("eye_enlarge", value=self.config.beauty.eye_enlarge)

    def _on_beauty_toggled(self, switch: Gtk.Switch, _param: object) -> None:
        if self._updating_toggles:
            return
        self._set_beauty_enabled(switch.get_active())

    def _on_compare_toggled(self, switch: Gtk.Switch, _param: object) -> None:
        if self._updating_toggles:
            return
        self._set_compare_enabled(switch.get_active())

    def _toggle_beauty(self) -> None:
        self._beauty_switch.set_active(not self._beauty_switch.get_active())

    def _toggle_compare(self) -> None:
        # The C key must respect the same gate as the switch and the dock pill:
        # with beauty off the split is unavailable instead of forcing beauty on.
        if not self.config.beauty.enabled:
            self._set_hint_key("compare_requires_beauty")
            return
        self._compare_switch.set_active(not self._compare_switch.get_active())

    def _toggle_debug_mask(self) -> None:
        self.config.beauty.debug_mask = not self.config.beauty.debug_mask
        self._refresh_beauty()
        log_action("debug_mask", enabled=self.config.beauty.debug_mask)

    def _refresh_beauty(self) -> None:
        """Apply the current beauty config to the running shader immediately."""
        self.backend.refresh_beauty()
        self._refresh_beauty_status()
        self._update_info_label()
        self._sync_compare_overlay()

    def _on_beauty_error(self, message: str) -> None:
        log_action("beauty_error", message=message)
        self._beauty_error = message
        self._beauty_switch.set_sensitive(False)
        # A failed GPU pass is the feature switching itself off: use the same
        # cascade so no split or face box outlives the effect.
        self._set_beauty_enabled(False)
        self._set_hint_key("beauty_error", message=message)
        self._hint_label.add_css_class("error-hint")
        self._refresh_beauty_status()
        self._update_info_label()

    # -- backend callbacks ----------------------------------------------

    def _on_state_changed(self, state: str) -> None:
        self._update_play_icon(state == "playing")
        if state == "playing":
            self._playback_started = True
            self._set_playback_status(None)
            # The demuxer only exposes its audio pads during preroll, so the
            # track list has to be rebuilt here rather than in _play_path.
            self._refresh_audio_tracks()
        log_action("playback_state", state=state)

    def _on_video_info(self, info: dict[str, object]) -> None:
        # The beauty element exists only once the video branch is built, so the
        # status line is refreshed here to replace the "preparing" placeholder.
        self._refresh_beauty_status()
        self._update_info_label()
        self._sync_compare_overlay()
        self._sync_chrome_visibility()

    def _on_fps(self, fps: float) -> None:
        self._fps = fps
        self._update_info_label()

    def _on_detection(self, result: DetectionResult) -> None:
        # The boxes feed two consumers with different switches: the overlay
        # draws them only when asked, while the info line (and the beauty
        # shader) always want the newest result.  Returning early on
        # ``_show_faces`` used to report ``NPU —`` while beauty was still
        # masking with those very boxes.
        faces = tuple(result.faces)
        changed = faces != self._faces
        self._faces = faces
        self._detection_ms = result.inference_ms
        if changed and self._show_faces:
            width = float(self.backend.video_info.get("width") or 0)
            height = float(self.backend.video_info.get("height") or 0)
            self._face_overlay.set_faces(self._faces, width, height)
        # Repainting the label on every detection (10 Hz) competes with the
        # video sink's frame clock; the NPU timing only needs ~1 Hz updates.
        now = time.monotonic()
        if changed or now - self._last_detection_label_at >= 1.0:
            self._last_detection_label_at = now
            self._update_info_label()

    def _on_detection_error(self, message: str) -> None:
        self._detection_error = message
        self._set_hint_key("face_detection_disabled", message=message)
        self._update_info_label()
        log_action("detection_error", message=message)

    def _on_show_faces_toggled(self, switch: Gtk.Switch, _param: object) -> None:
        # The cascade in _set_beauty_enabled clears the boxes through this
        # switch; honour the guard so it does not look like a user action.
        if self._updating_toggles:
            return
        self._show_faces = switch.get_active()
        log_action("show_faces", enabled=self._show_faces)
        if not self._show_faces:
            self._face_overlay.set_faces((), 0.0, 0.0)
        elif self._faces:
            width = float(self.backend.video_info.get("width") or 0)
            height = float(self.backend.video_info.get("height") or 0)
            self._face_overlay.set_faces(self._faces, width, height)
        self._on_detection_dependency_toggled()

    def _on_eos(self) -> None:
        log_action("eos")
        # Update the history first (a finished clip shows 100 % there), then
        # drop the resume entry: keeping it would make the next launch jump
        # into the last seconds and advance again.
        self._save_resume_position(remember=True)
        self._clear_resume_position()
        self._playback_started = False
        self._update_play_icon(False)
        if self.playlist.has_next():
            target = self.playlist.next()
            if target is not None:
                log_action("eos_advance")
                self._play_path(target)
                return
        # A finished clip is not an error, so no card: it used to cover the last
        # frame.  The transport icon returns to "play" (which now rewinds) and
        # the hint line reports the end.
        self._set_playback_status(None)
        self._set_hint_key("hint_finished")

    def _show_error(self, message: str) -> None:
        log_action("playback_error", message=message)
        self._playback_started = False
        self._set_playback_status("error", message)
        self._update_play_icon(False)

    # -- helpers --------------------------------------------------------

    def _set_hint_key(self, key: str | None, **values: object) -> None:
        self._hint_key = key
        self._hint_values = values
        self._hint_label.remove_css_class("error-hint")
        self._hint_label.remove_css_class("warn-hint")
        text = "" if key is None else self._t(key, **values)
        self._hint_label.set_text(text)
        self._hint_revealer.set_visible(bool(text))
        self._hint_revealer.set_reveal_child(bool(text))
        if self._hint_timeout:
            GLib.source_remove(self._hint_timeout)
            self._hint_timeout = 0
        if text:
            # Long enough to read a decode-fallback line, short enough that the
            # picture is clean again before anyone starts talking about it.
            self._hint_timeout = GLib.timeout_add(6000, self._hide_hint_toast)

    def _hide_hint_toast(self) -> bool:
        self._hint_timeout = 0
        self._hint_revealer.set_reveal_child(False)
        return False

    def _update_title(self) -> None:
        """Keep the browse header, the picture title and the window in step."""
        if getattr(self, "_page", "player") == "browse":
            return
        t = self._t
        if self._current_path is not None:
            name = self._current_path.name
            display = self._current_path.stem
            self.set_title(t("title_with_media", name=name))
        else:
            name = t("app_title")
            display = name
            self.set_title(t("app_title"))
        self._title_label.set_text(name)
        if hasattr(self, "_play_title_label"):
            self._play_title_label.set_text(display)
        self._refresh_subtitle()

    def _header_subtitle_text(self) -> str:
        """Header second line: brand when idle, playlist position when open.

        Resolution / codec / fps used to repeat here; they now live once in the
        labelled info row under the picture.
        """
        if self._current_path is None or not self.backend.has_media:
            return "Quectel Pi M2 · RK3576"
        if len(self.playlist):
            return self._t(
                "playlist_position",
                index=self.playlist.index + 1,
                count=len(self.playlist),
            )
        return ""

    def _refresh_subtitle(self) -> None:
        if getattr(self, "_page", "player") == "browse":
            return
        text = self._header_subtitle_text()
        if text != self._header_subtitle_cache:
            self._header_subtitle_cache = text
            self._header_subtitle_label.set_text(text)
            self._header_subtitle_label.set_visible(bool(text))
        if hasattr(self, "_top_bar"):
            self._top_bar.set_subtitle(text)

    def _update_play_icon(self, playing: bool) -> None:
        icon = "media-playback-pause-symbolic" if playing else "media-playback-start-symbolic"
        self._play_button.set_icon_name(icon)

    def _update_info_label(self) -> None:
        """Refresh the in-picture telemetry row and the fullscreen hairline copy."""
        t = self._t
        info = self.backend.video_info
        width = info.get("width", "—")
        height = info.get("height", "—")
        codec = normalise_codec(info.get("codec", "—"))
        decoder = info.get("decoder", "—")
        fps_value = self._fps or info.get("fps")
        if fps_value in ("—", None):
            fps_text = "—"
        else:
            try:
                fps_text = f"{float(fps_value):.1f}"
            except (TypeError, ValueError):
                fps_text = str(fps_value)
        # The face clause reports what is on screen, not what the NPU is still
        # running for the beauty mask.  With the boxes switch off (the cascade
        # clears it with the effect) the row reads "人脸 关", matching the mockup.
        if not self._show_faces:
            face_text = t("info_face_off")
        elif self._detection_error:
            face_text = t("info_face_unavailable")
        elif self._faces:
            face_text = t(
                "info_face_on",
                count=len(self._faces),
                ms=self._detection_ms,
            )
        elif not self.config.detection.enabled:
            face_text = t("info_face_off")
        else:
            face_text = t("info_face_none")
        if self._beauty_error:
            beauty_text = t("info_beauty_unavailable")
        elif not self.backend.beauty_available:
            beauty_text = t("info_beauty_off")
        elif not self.config.beauty.enabled:
            beauty_text = t("info_beauty_off")
        else:
            beauty_text = t("info_beauty_on", ms=self.backend.beauty_ms)
        format_text = f"{width}×{height}" if width != "—" else "—"
        decode_text = self._decode_chip_text(decoder)
        self._info_bar.set_values(
            decode=decode_text,
            codec=codec,
            resolution=format_text,
            fps=f"{fps_text} fps" if fps_text != "—" else "—",
            face=face_text,
            beauty=beauty_text,
            cpu=t("telemetry_cpu", percent=self._cpu_percent),
            hardware="mpp" in str(decoder),
        )
        # "mppvideodec" would not fit the row; keep the element name in the
        # tooltip so a decode fallback is still diagnosable.
        self._info_values["decode"].set_tooltip_text(str(decoder))

        # The fullscreen line is the same telemetry without labels on the
        # static fields; NPU/beauty keep their labels because the numbers move.
        self._telemetry_main.set_text(
            f"{format_text} · {codec} · {decode_text} · {fps_text} fps"
        )
        self._telemetry_live.set_text(f"{face_text} · {beauty_text}")
        if self.config.beauty.enabled:
            self._telemetry_dot.remove_css_class("is-off")
        else:
            self._telemetry_dot.add_css_class("is-off")
        self._refresh_beauty_status()
        self._refresh_subtitle()

    def _decode_chip_text(self, decoder: str) -> str:
        """Collapse the element name to 硬解 / 软解; the name lives in a tooltip."""
        name = str(decoder)
        if "mpp" in name:
            return self._t("decode_hw")
        if name and name != "—":
            return self._t("decode_sw")
        return "—"

    def _reset_info_chips(self) -> None:
        """Neutral state used before a file is open and after it is closed."""
        self._info_bar.set_values(
            decode="—",
            codec="—",
            resolution="—",
            fps="—",
            face="—",
            beauty="—",
            cpu="—",
            hardware=False,
        )
        self._info_values["decode"].set_tooltip_text(None)
        self._telemetry_main.set_text("—")
        self._telemetry_live.set_text("")
        if hasattr(self, "_timeline"):
            self._timeline.set_chapters(())
        self._set_timecode("00:00 / 00:00")
        self._set_progress_fraction(0.0)

    def _set_timecode(self, text: str) -> None:
        """Split ``00:03 / 00:10`` into the two time labels."""
        position, _, total = text.partition(" / ")
        self._elapsed_label.set_text(position or "00:00")
        self._total_label.set_text(total or "00:00")

    def _set_progress_fraction(self, fraction: float) -> None:
        self._fullscreen_progress.set_fraction(max(0.0, min(1.0, fraction)))

    def _sync_playlist(self) -> None:
        while self._playlist_list.get_row_at_index(0) is not None:
            row = self._playlist_list.get_row_at_index(0)
            self._playlist_list.remove(row)
        for name in (path.name for path in self.playlist):
            row = Gtk.ListBoxRow()
            label = Gtk.Label(label=name)
            label.set_xalign(0.0)
            label.set_ellipsize(Pango.EllipsizeMode.END)
            row.set_child(label)
            self._playlist_list.append(row)
        if self.playlist:
            row = self._playlist_list.get_row_at_index(self.playlist.index)
            if row is not None:
                self._playlist_list.select_row(row)
        # Empty playlist gets the dashed hint from the mockup instead of a
        # blank box; the list and the hint never show together.
        if hasattr(self, "_playlist_scroll"):
            self._playlist_scroll.set_visible(bool(self.playlist))
            self._playlist_empty.set_visible(not self.playlist)
        if hasattr(self, "_episodes_count_label"):
            # The heading above already says 播放列表; the meta label is only
            # the position, so no locale-specific prefix is duplicated.
            self._episodes_count_label.set_text(
                f"{self.playlist.index + 1}/{len(self.playlist)}"
                if self.playlist
                else ""
            )
        # The playlist changed: an entry shown as "recent" a moment ago may
        # now be duplicated, so rebuild the history section too.
        self._sync_recents()
        self._refresh_subtitle()

    def _tick(self) -> bool:
        self._maybe_hide_dock()
        self._sample_cpu()
        if self.backend.has_media:
            measured = self.backend.measured_fps()
            if measured and abs(measured - self._fps) >= 0.5:
                self._fps = measured
                self._update_info_label()
            duration = self.backend.duration_ns()
            position = self.backend.position_ns()
            if duration > 0:
                self._duration_ns = duration
                if duration != self._chapter_duration_ns:
                    self._chapter_duration_ns = duration
                    self._seek.set_chapters(
                        chapter_marks(self._chapters, duration)
                    )
                if not self._updating_seek:
                    self._updating_seek = True
                    self._seek.set_value(position / duration * 1000.0)
                    self._updating_seek = False
                self._set_timecode(
                    f"{_format_time(position)} / {_format_time(duration)}"
                )
                self._set_progress_fraction(position / duration)
                self._update_subtitle(position)
            self._update_info_label()
            if self.backend.beauty_available and self.config.beauty.enabled:
                # Keep the page's ms/frame in sync with the live GPU timing;
                # otherwise it stays at the value captured before playback.
                self._refresh_beauty_status()
            if self._resume is not None and self.backend.is_playing:
                now = time.monotonic()
                if now - self._resume_saved_at >= RESUME_SAVE_INTERVAL:
                    self._resume_saved_at = now
                    self._save_resume_position()
        # Log after the measured-fps correction above: logging first sampled
        # fpsdisplaysink's lifetime average, which reads far too low while the
        # stream warms up and looked like a fps dip on a perfectly smooth run.
        self._maybe_log_perf()
        if self._record_path is not None:
            self._maybe_start_recording()
        return True

    def _sample_cpu(self) -> None:
        """Measure process CPU (all threads) over a one-second window."""
        now = time.monotonic()
        elapsed = now - self._cpu_sampled_at
        if elapsed < 1.0:
            return
        usage = resource.getrusage(resource.RUSAGE_SELF)
        total = usage.ru_utime + usage.ru_stime
        self._cpu_percent = max(0.0, (total - self._cpu_usage) / elapsed * 100.0)
        self._cpu_usage = total
        self._cpu_sampled_at = now

    def _maybe_log_perf(self) -> None:
        """Print a one-line performance sample once per second (evidence runs).

        Enabled with ``VIDEO_PLAYER_PERF_LOG=1``; the line is plain text so it can
        be pasted into ``docs/evidence/`` and ``docs/BOARD.md``.
        """
        if not self._perf_log:
            return
        now = time.monotonic()
        if self._perf_last and now - self._perf_last < 1.0:
            return
        wall = now - self._perf_last if self._perf_last else 0.0
        cpu = 0.0
        if wall > 0:
            usage = resource.getrusage(resource.RUSAGE_SELF)
            total = usage.ru_utime + usage.ru_stime
            cpu = (total - self._perf_cpu) / wall * 100.0
            self._perf_cpu = total
        self._perf_last = now
        self._perf_seconds += 1
        print(
            f"PERF t={self._perf_seconds:3d}s fps={self._fps:5.1f} "
            f"beauty={self.backend.beauty_ms:5.2f}ms "
            f"faces={len(self._faces)} npu={self._detection_ms:5.1f}ms "
            f"cpu={cpu:5.0f}%",
            flush=True,
        )

    # -- resume ------------------------------------------------------------

    def _save_resume_position(self, *, remember: bool = False) -> None:
        """Persist the position of the clip that is playing.

        Called every couple of seconds and on close.  :class:`ResumeStore`
        decides whether the value is worth keeping (it drops the first ten
        seconds and anything inside the tail of the file), so this can be
        called unconditionally.

        ``remember=True`` also refreshes the "recently played" row.  That is
        deliberately kept off the two-second timer: the history only needs to
        know where a clip stopped when playback actually stops, and two small
        JSON writes per second is real flash wear on a board that runs all day.
        """
        path = self._playback_path or self._current_path
        if path is None:
            return
        position = self.backend.position_ns()
        if position <= 0:
            return
        duration = self.backend.duration_ns()
        if remember:
            self._remember_recent(path, position, duration)
        if self._resume is None:
            return
        self._resume.save(path, position, duration)

    def _resume_target_ns(
        self, path: Path, requested_ns: int | None = None
    ) -> int:
        """Start position for an open: explicit rebuild position, else resume.

        The detection rebuild must keep the viewer exactly where they were even
        when resume positions are disabled (``player.resume=false``) or the
        outgoing position was never stored (the first ten seconds).
        """
        if requested_ns is not None:
            return max(0, int(requested_ns))
        if self._resume is not None:
            return int(self._resume.load(path) or 0)
        return 0

    def _clear_resume_position(self) -> None:
        if self._resume is None:
            return
        path = self._playback_path or self._current_path
        if path is not None:
            self._resume.clear(path)

    # -- subtitles -------------------------------------------------------

    def _build_subtitle_popover(self) -> Gtk.Popover:
        popover = Gtk.Popover()
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        box.set_margin_top(12)
        box.set_margin_bottom(12)
        box.set_margin_start(12)
        box.set_margin_end(12)
        box.set_size_request(260, -1)

        self._subtitle_switch = Gtk.Switch()
        self._subtitle_switch.set_active(self._subtitles_enabled)
        self._subtitle_switch.connect("notify::active", self._on_subtitles_toggled)
        self._subtitles_menu_label = Gtk.Label(label=self._t("show_subtitles"))
        self._subtitles_menu_label.set_xalign(0.0)
        box.append(_labelled_row(self._subtitles_menu_label, self._subtitle_switch))

        self._open_subtitle_button = Gtk.Button(label=self._t("open_subtitle"))
        self._open_subtitle_button.connect("clicked", self._on_open_subtitle)
        box.append(self._open_subtitle_button)

        self._audio_label = Gtk.Label(label=self._t("audio_track"))
        self._audio_label.set_xalign(0.0)
        self._audio_dropdown = Gtk.DropDown.new_from_strings(
            [self._t("audio_track_none")]
        )
        self._audio_dropdown.set_sensitive(False)
        self._audio_dropdown.set_hexpand(True)
        self._audio_dropdown.connect("notify::selected", self._on_audio_track_selected)
        box.append(_labelled_row(self._audio_label, self._audio_dropdown))

        self._subtitle_status_label = Gtk.Label()
        self._subtitle_status_label.set_xalign(0.0)
        self._subtitle_status_label.set_wrap(True)
        self._subtitle_status_label.add_css_class("dim-label")
        self._subtitle_status_label.set_text(self._t("subtitle_none"))
        box.append(self._subtitle_status_label)

        popover.set_child(box)
        return popover

    def _apply_subtitle(self, path: Path | None) -> None:
        """Load (or clear) the subtitle track shown for the current clip."""
        self._subtitle_path = path
        self._subtitles = None
        if path is not None:
            cues = load_srt(path)
            if cues:
                self._subtitles = SubtitleTrack(cues)
        self._subtitle_text = None
        self._subtitle_label.set_text("")
        self._subtitle_label.set_visible(False)
        self._refresh_subtitle_status()

    def _refresh_subtitle_status(self) -> None:
        if self._subtitle_path is not None and self._subtitles is not None:
            self._subtitle_status_label.set_text(
                self._t(
                    "subtitle_loaded",
                    name=self._subtitle_path.name,
                    count=len(self._subtitles),
                )
            )
        elif self._subtitle_path is not None:
            self._subtitle_status_label.set_text(
                self._t("subtitle_empty", name=self._subtitle_path.name)
            )
        else:
            self._subtitle_status_label.set_text(self._t("subtitle_none"))

    def _update_subtitle(self, position_ns: int) -> None:
        text = None
        if self._subtitles_enabled and self._subtitles is not None:
            text = self._subtitles.text_at(position_ns)
        if text == self._subtitle_text:
            return
        self._subtitle_text = text
        self._subtitle_label.set_text(text or "")
        self._subtitle_label.set_visible(bool(text))

    def _on_subtitles_toggled(self, switch: Gtk.Switch, _param: object) -> None:
        self._subtitles_enabled = switch.get_active()
        log_action("subtitles_toggle", enabled=self._subtitles_enabled)
        if not self._subtitles_enabled:
            self._subtitle_text = None
            self._subtitle_label.set_text("")
            self._subtitle_label.set_visible(False)

    def _refresh_audio_tracks(self) -> None:
        """Rebuild the track dropdown from the container's audio pads."""
        count = self.backend.audio_track_count
        if count > 0:
            labels = [
                self._t("audio_track_n", index=index + 1) for index in range(count)
            ]
        else:
            labels = [self._t("audio_track_none")]
        self._updating_audio = True
        try:
            self._audio_dropdown.set_model(Gtk.StringList.new(labels))
            self._audio_dropdown.set_sensitive(count > 1)
            self._audio_dropdown.set_selected(
                min(self.backend.audio_track_index, max(0, count - 1))
            )
        finally:
            self._updating_audio = False

    def _on_audio_track_selected(
        self, dropdown: Gtk.DropDown, _param: object
    ) -> None:
        if self._updating_audio:
            return
        index = int(dropdown.get_selected())
        log_action("audio_track", index=index)
        if not self.backend.set_audio_track(index):
            # Fall back to the track that is actually playing.
            self._refresh_audio_tracks()

    def _on_open_subtitle(self, _button: Gtk.Button) -> None:
        log_action("subtitle_dialog")
        dialog = Gtk.FileDialog(title=self._t("open_subtitle"))
        file_filter = Gtk.FileFilter()
        file_filter.set_name(self._t("subtitle_files"))
        file_filter.add_suffix("srt")
        filters = Gio.ListStore.new(Gtk.FileFilter)
        filters.append(file_filter)
        dialog.set_filters(filters)
        dialog.set_default_filter(file_filter)
        dialog.open(self, None, self._on_subtitle_chosen)

    def _on_subtitle_chosen(
        self, dialog: Gtk.FileDialog, result: Gio.AsyncResult
    ) -> None:
        try:
            gfile = dialog.open_finish(result)
        except GLib.Error:
            return
        if gfile is None:
            return
        path_text = gfile.get_path()
        if not path_text:
            return
        self._apply_subtitle(Path(path_text))
        self._set_hint_key("subtitle_loaded", name=Path(path_text).name, count=len(self._subtitles) if self._subtitles else 0)

    def _on_open_clicked(self, _button: Gtk.Button) -> None:
        log_action("open_dialog")
        dialog = Gtk.FileDialog(title=self._t("open_video"))
        file_filter = Gtk.FileFilter()
        file_filter.set_name(self._t("video_files"))
        for suffix in VIDEO_SUFFIXES:
            file_filter.add_pattern(f"*{suffix}")
        filters = Gio.ListStore.new(Gtk.FileFilter)
        filters.append(file_filter)
        dialog.set_filters(filters)
        dialog.set_default_filter(file_filter)
        dialog.open(self, None, self._on_open_finished)

    def _on_open_finished(self, dialog: Gtk.FileDialog, result: Gio.AsyncResult) -> None:
        try:
            gfile = dialog.open_finish(result)
        except GLib.Error as exc:
            log_action("open_cancelled", error=str(exc))
            return
        if gfile is None:
            log_action("open_cancelled")
            return
        path_text = gfile.get_path()
        if path_text:
            log_action("open_selected", path=path_text)
            self._show_page("player", refresh=False)
            self._play_path(Path(path_text))

    def _on_key_pressed(
        self,
        _controller: Gtk.EventControllerKey,
        keyval: int,
        _keycode: int,
        _state: Gdk.ModifierType,
    ) -> bool:
        name = Gdk.keyval_name(keyval)
        # Reaching for the keyboard is the same intent as reaching for the
        # transport: bring it back before the command lands.
        self._show_dock()
        if name in {
            "space", "Left", "Right", "f", "F", "Escape", "n", "N",
            "p", "P", "b", "B", "c", "C", "m", "M", "r", "R", "o", "O",
            "s", "S",
        }:
            log_action("key", name=name)
        if self._page == "browse":
            # The browse pages are not the player: transport and feature
            # shortcuts must not fire here (space used to open the file dialog
            # on the library page).  Only Escape has a meaning: leave detail.
            if name == "Escape" and self._browse.is_detail():
                self._browse.back_from_detail()
                self._sync_page_chrome()
                return True
            if name == "Escape" and self._browse.page in ("settings", "recent"):
                self._browse.go_home()
                self._sync_page_chrome()
                return True
            return False
        if name == "space":
            self._on_play_clicked(None)
            return True
        if name == "Right" and self.backend.has_media:
            self.backend.seek_ns(self.backend.position_ns() + 10 * GST_SECOND)
            return True
        if name == "Left" and self.backend.has_media:
            self.backend.seek_ns(max(0, self.backend.position_ns() - 10 * GST_SECOND))
            return True
        if name in ("f", "F"):
            self._on_fullscreen_clicked(None)
            return True
        if name == "Escape" and self._drawer_open:
            self._set_beauty_drawer_open(False)
            return True
        if name == "Escape" and self._episodes_open and not self._immersive:
            self._set_episodes_open(False)
            return True
        if name == "Escape" and self.is_fullscreen():
            self.unfullscreen()
            self._sync_immersive()
            return True
        if name == "Escape" and self._page == "player":
            self._show_page("browse")
            return True


        if name in ("n", "N"):
            self._on_next_clicked(None)
            return True
        if name in ("p", "P"):
            self._on_previous_clicked(None)
            return True
        if name in ("s", "S"):
            self._on_stop_clicked(None)
            return True
        if name in ("b", "B"):
            self._toggle_beauty()
            return True
        if name in ("c", "C"):
            self._toggle_compare()
            return True
        if name in ("m", "M"):
            self._toggle_debug_mask()
            return True
        if name in ("r", "R"):
            self._toggle_recording()
            return True
        if name in ("o", "O"):
            self._on_open_clicked(self._open_button)
            return True
        return False

    # -- recording (demo video capture) ---------------------------------

    def _recording_layer_specs(self) -> list[tuple[str, Gtk.Widget]]:
        """Chrome painted into the recorder canvas, in paint order.

        ``--record`` keeps the P4 windowed canvas: header bar hidden, 选集
        column collapsed, so the chrome is the picture-top bar, the bottom dock
        and the beauty drawer.  All three are overlay children of the stage,
        and capturing them as separate layers keeps the 16:9 video hole free
        for the mixer.  The list is a method (not
        inline in :meth:`_maybe_start_recording`) so the order can be asserted
        without a display.
        """
        return [
            ("top", self._top_bar),
            ("dock", self._dock),
            ("drawer", self._beauty_page),
            ("hint", self._hint_label),
            ("playback-status", self._status_card),
        ]

    def _maybe_start_recording(self) -> None:
        """Start ``--record`` once the window is painted and a video plays.

        The recorder taps the GPU video branch and composites it with a UI
        canvas; both pieces only exist after the window mapped and the video
        branch was built, so this is retried from ``_tick`` until it succeeds.
        """
        if self._closed or self._record_done:
            return
        path = self._record_path
        if path is None or self._recorder is not None or self._record_error:
            return
        if not self.get_mapped() or not self.backend.has_media:
            return
        if not self.backend.is_playing or not self.backend.video_info:
            return
        if self._record_attempts >= 60:
            self._record_error = self._t("recording_not_ready")
            self._set_hint_key("recording_unavailable", message=self._record_error)
            return
        self._record_attempts += 1
        if os.environ.get("REC_DEBUG"):
            print(
                f"[rec] attempt={self._record_attempts} media={self.backend.has_media} "
                f"playing={self.backend.is_playing} info={bool(self.backend.video_info)}",
                flush=True,
            )
        # numpy-backed canvas code is imported lazily: the player itself must
        # stay importable on hosts without numpy.
        from .capture import CanvasBuilder

        builder = CanvasBuilder(self)
        for name, widget in self._recording_layer_specs():
            builder.add_layer(name, widget)
        builder.set_video_widget(self._player_overlay)
        builder.set_backdrop(self._theme_background())
        if not builder.refresh_background():
            if os.environ.get("REC_DEBUG"):
                print(
                    f"[rec] canvas not ready: size={builder.size} "
                    f"video_area={builder.video_area}",
                    flush=True,
                )
            return
        if os.environ.get("REC_DEBUG"):
            print(
                f"[rec] canvas {builder.size} video_area={builder.video_area}",
                flush=True,
            )
        from ..recording import fit_rect

        info = self.backend.video_info
        frame_width = int(info.get("width") or 0)
        frame_height = int(info.get("height") or 0)
        area = builder.video_area
        video_size = None
        if area is not None and frame_width > 0 and frame_height > 0:
            fitted = fit_rect(frame_width, frame_height, area)
            video_size = (fitted.width, fitted.height)
        try:
            recorder = self.backend.start_recording(path, builder.size, video_size)
        except PlaybackError as exc:
            self._record_failures += 1
            if os.environ.get("REC_DEBUG"):
                print(f"[rec] start failed ({self._record_failures}): {exc}", flush=True)
            if self._record_failures >= RECORD_START_RETRIES:
                self._record_error = str(exc)
                self._set_hint_key("recording_unavailable", message=exc)
            return
        self._record_failures = 0
        self._canvas_builder = builder
        self._recorder = recorder
        self._update_record_geometry()
        if self._record_duration:
            self._record_deadline = time.monotonic() + float(self._record_duration)
        self._record_timer_id = GLib.timeout_add(100, self._on_record_tick)
        if os.environ.get("REC_DEBUG"):
            print(f"[rec] started {path} canvas={builder.size}", flush=True)

    def _theme_background(self) -> tuple[int, int, int, int]:
        """The window base colour, shared with the custom recording canvas.

        Read from the ui-v5 ``bg`` token instead of repeating the triple: the
        recorder composites its own backdrop, so a literal here would drift
        from ``window.cinema-window`` and put a different frame around the
        video.
        """
        # The player page's visible backdrop is the stage's video black, not
        # the window's graphite; the recorder must use the same colour so its
        # letterbox bars match the live picture.
        red, green, blue, _alpha = rgba("video")
        return (
            round(red * 255.0),
            round(green * 255.0),
            round(blue * 255.0),
            255,
        )

    @staticmethod
    def _save_rgba_png(image, path: str) -> None:
        """Write an RGBA ``numpy`` canvas to a PNG file (debug aid)."""
        import numpy as np
        from gi.repository import GdkPixbuf, GLib

        array = np.ascontiguousarray(image, dtype=np.uint8)
        height, width = array.shape[0], array.shape[1]
        data = GLib.Bytes.new(array.tobytes())
        pixbuf = GdkPixbuf.Pixbuf.new_from_bytes(
            data, GdkPixbuf.Colorspace.RGB, True, 8, width, height, width * 4
        )
        pixbuf.savev(path, "png", [], [])

    def _update_record_geometry(self) -> None:
        """Tell the recorder where the video sits inside the window canvas."""
        builder = self._canvas_builder
        recorder = self._recorder
        if builder is None or recorder is None:
            return
        area = builder.video_area
        info = self.backend.video_info
        width = int(info.get("width") or 0)
        height = int(info.get("height") or 0)
        if area is None or width <= 0 or height <= 0:
            return
        from ..recording import fit_rect

        recorder.set_video_geometry(fit_rect(width, height, area), (width, height))

    def _on_record_tick(self) -> bool:
        """Refresh the UI canvas at 10 fps and hand it to the recorder."""
        self._record_ticks += 1
        builder = self._canvas_builder
        recorder = self._recorder
        if builder is None or recorder is None:
            self._record_timer_id = 0
            return False
        if self._record_deadline and time.monotonic() >= self._record_deadline:
            self._record_deadline = 0.0
            self._stop_recording()
            GLib.idle_add(self.close)
            return False
        debug = bool(os.environ.get("REC_DEBUG"))
        started_at = time.perf_counter()
        if builder.size != (self.get_width(), self.get_height()):
            builder.refresh_background()
            self._update_record_geometry()
        # Layer capture costs 2-10 ms per widget.  The top bar is mostly static
        # but the dock carries the timecode and the live telemetry, so it is
        # refreshed once a second; the drawer only exists while it is open.
        # The first two seconds are different: the window may still be painting
        # its first frames, and a widget snapshot of an unpainted widget comes
        # back blank, so every layer is refreshed until the tree settled.
        if self._record_ticks <= 20:
            builder.refresh()
        else:
            if self._record_ticks % 30 == 1 and self._drawer_open:
                builder.refresh(("drawer",))
            if self._record_ticks % 10 == 1:
                builder.refresh(
                    ("top", "dock", "hint", "playback-status")
                )
        info = self.backend.video_info
        width = int(info.get("width") or 0)
        height = int(info.get("height") or 0)
        faces = self._faces if self._show_faces else ()
        canvas = builder.compose(faces=faces, frame_size=(width, height))
        if canvas is not None:
            recorder.set_canvas(canvas)
        if debug:
            self._record_tick_ms += (time.perf_counter() - started_at) * 1000.0
            if self._record_ticks % 10 == 0:
                for line in builder.layer_report():
                    print(f"[rec] layer {line}", flush=True)
                dump = os.environ.get("REC_DUMP_CANVAS")
                if dump and canvas is not None:
                    try:
                        self._save_rgba_png(canvas, dump)
                    except Exception as exc:  # pragma: no cover - debug aid
                        print(f"[rec] canvas dump failed: {exc}", flush=True)
                value = recorder.stats
                encoded, pushes, buffers = value() if callable(value) else value
                print(
                    f"[rec] enc={encoded} ui={pushes} vid={buffers} "
                    f"t={self._record_ticks / 10:.0f}s "
                    f"tick={self._record_tick_ms / 10:.1f}ms",
                    flush=True,
                )
                self._record_tick_ms = 0.0
        return True

    def _stop_recording(self, *, finished: bool = True) -> None:
        if self._record_timer_id:
            GLib.source_remove(self._record_timer_id)
            self._record_timer_id = 0
        self._canvas_builder = None
        self._record_deadline = 0.0
        self._record_done = finished
        if self._recorder is not None:
            self._recorder = None
            # Finishes the MP4 (EOS + moov atom) while the pipeline lives.
            self.backend.stop_recording()

    def _toggle_recording(self) -> None:
        self._record_done = False
        if self._recorder is not None:
            path = self._recorder.path
            log_action("recording_stop", path=path)
            self._stop_recording()
            self._set_hint_key("recording_saved", path=path)
            return
        if self._record_path is None:
            stamp = time.strftime("%Y%m%d-%H%M%S")
            self._record_path = Path.cwd() / f"player-{stamp}.mp4"
        log_action("recording_start", path=self._record_path)
        self._record_error = None
        self._record_attempts = 0
        self._record_failures = 0
        self._record_ticks = 0
        self._maybe_start_recording()

    def _on_recording_error(self, message: str) -> None:
        self._record_error = message
        log_action("recording_error", message=message)
        self._set_hint_key("recording_stopped", message=message)
        GLib.idle_add(self._stop_recording)

    def capture_snapshot(self, path: Path | str) -> bool:
        """Render the whole window to a PNG file (used by ``--snapshot``).

        GTK has no public screenshot API for a single widget, but a
        ``Gtk.WidgetPaintable`` can snapshot any mapped widget; the resulting
        render node is then rasterised with the GSK renderer of the window.
        """
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        paintable = Gtk.WidgetPaintable.new(self)
        width = paintable.get_intrinsic_width()
        height = paintable.get_intrinsic_height()
        if width <= 0 or height <= 0:
            return False
        snapshot = Gtk.Snapshot()
        paintable.snapshot(snapshot, width, height)
        node = snapshot.to_node()
        native = self.get_native()
        renderer = native.get_renderer() if native is not None else None
        if node is None or renderer is None:
            return False
        texture = renderer.render_texture(node, None)
        if texture is None:
            return False
        return bool(texture.save_to_png(str(target)))

    def _on_close_request(self, _window: Gtk.Window) -> bool:
        log_action("window_close")
        if self._closed:
            return False
        self._closed = True
        self._cancel_surface_tap()
        if os.environ.get("REC_DEBUG"):
            print("[rec] window closing", flush=True)
        self._stop_recording()
        if self._timer_id:
            GLib.source_remove(self._timer_id)
            self._timer_id = 0
        if self._decode_reopen_id:
            GLib.source_remove(self._decode_reopen_id)
            self._decode_reopen_id = 0
        if self._detection_reopen_id:
            GLib.source_remove(self._detection_reopen_id)
            self._detection_reopen_id = 0
        self._save_resume_position(remember=True)
        self.backend.close()
        self._browse.shutdown()
        return False


class PlayerApplication(Gtk.Application):
    """GTK application object owning the main window."""

    def __init__(
        self,
        config: Config,
        media: Sequence[Path] = (),
        *,
        demo: bool = False,
        fullscreen: bool = False,
        snapshot: Path | None = None,
        snapshot_delay: float = 3.0,
        record: Path | None = None,
        record_duration: float | None = None,
        language: str | None = None,
    ) -> None:
        # Non-unique: the demo may be launched from SSH and the desktop at
        # once, and D-Bus name registration is unreliable in remote sessions.
        super().__init__(
            application_id=APP_ID, flags=Gio.ApplicationFlags.NON_UNIQUE
        )
        self._config = config
        self._media = list(media)
        self._demo = demo
        self._fullscreen = fullscreen
        self._snapshot = snapshot
        self._snapshot_delay = max(0.5, float(snapshot_delay))
        self._record = record
        self._record_duration = record_duration
        self._language = language
        self._snapshot_attempts = 0
        self._window: PlayerWindow | None = None

    def do_activate(self) -> None:  # pragma: no cover - needs a display
        if self._window is None:
            display = Gdk.Display.get_default()
            if display is not None:
                provider = Gtk.CssProvider()
                provider.load_from_data(CSS)
                Gtk.StyleContext.add_provider_for_display(
                    display, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
                )
            self._window = PlayerWindow(
                self,
                self._config,
                self._media,
                demo=self._demo,
                fullscreen=self._fullscreen,
                record=self._record,
                record_duration=self._record_duration,
                language=self._language,
            )
        self._window.present()
        if self._snapshot is not None:
            GLib.timeout_add(
                int(self._snapshot_delay * 1000), self._capture_and_quit
            )

    def _capture_and_quit(self) -> bool:
        window = self._window
        path = self._snapshot
        ready = (
            window is not None
            and window.backend.has_media
            and window.backend.position_ns() > GST_SECOND // 5
        )
        captured = bool(ready and path is not None and window.capture_snapshot(path))
        if not captured and self._snapshot_attempts < 8:
            # The window may still be mapping / waiting for the first frame.
            self._snapshot_attempts += 1
            GLib.timeout_add(500, self._capture_and_quit)
            return False
        if captured and path is not None:
            print(f"snapshot saved: {path}", file=sys.stderr)
        else:
            print("error: window snapshot failed", file=sys.stderr)
        if window is not None:
            # Close the window first so the GStreamer (GL) pipeline is torn
            # down while GTK is still alive; quitting first can crash on exit.
            window.close()
        self.quit()
        return False


def run_window(
    config: Config,
    media: Sequence[Path] = (),
    *,
    demo: bool = False,
    fullscreen: bool = False,
    snapshot: Path | None = None,
    snapshot_delay: float = 3.0,
    record: Path | None = None,
    record_duration: float | None = None,
    language: str | None = None,
) -> int:  # pragma: no cover - needs a display
    """Open the GTK window and block until it closes; returns the exit code."""
    install_crash_logging()
    log_action(
        "run_window",
        media=len(media),
        demo=demo,
        fullscreen=fullscreen,
        snapshot=str(snapshot) if snapshot is not None else "",
        record=str(record) if record is not None else "",
        language=language or "",
    )
    # The detection worker runs numpy/OpenCV decoding in a Python thread; a
    # short switch interval keeps the GTK main loop rendering at 30 fps instead
    # of waiting for the worker's GIL slices.
    sys.setswitchinterval(0.001)
    application = PlayerApplication(
        config,
        media,
        demo=demo,
        fullscreen=fullscreen,
        snapshot=snapshot,
        snapshot_delay=snapshot_delay,
        record=record,
        record_duration=record_duration,
        language=language,
    )
    code = application.run([])
    sys.stdout.flush()
    sys.stderr.flush()
    log_action("run_window_return", code=code)
    # PyGObject finalisation of the GStreamer/GTK objects segfaults on the
    # RK3576 (mpp/GL teardown order). GTK is already shut down at this point,
    # so exit the process directly instead of letting the interpreter run the
    # crashing finalisers.
    os._exit(code)
