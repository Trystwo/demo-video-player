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
from gi.repository import Gdk, Gio, GLib, Gtk, Pango  # noqa: E402

from ..backend import PlaybackBackend, PlaybackError  # noqa: E402
from ..beauty import BeautyParams  # noqa: E402
from ..config import Config  # noqa: E402
from ..diagnostics import install_crash_logging, log_action  # noqa: E402
from ..face import DetectionResult, Face  # noqa: E402
from ..i18n import Translator, save_language  # noqa: E402
from ..pipeline import VIDEO_SUFFIXES  # noqa: E402
from ..playlist import Playlist  # noqa: E402

APP_ID = "io.quectel.pi.VideoPlayer"
GST_SECOND = 1_000_000_000

SPEED_CHOICES = ("0.5x", "0.75x", "1.0x", "1.25x", "1.5x", "2.0x")
SPEED_VALUES = (0.5, 0.75, 1.0, 1.25, 1.5, 2.0)

#: Attempts to attach the recording branch; the first ones can fail while the
#: pipeline is still bringing up its GL context.
RECORD_START_RETRIES = 10

CSS = b"""
.video-surface { background-color: #000000; }
.video-placeholder { color: rgba(255, 255, 255, 0.65); font-size: 13pt; }
.error-hint { color: #e01b24; }
.info-line { font-family: monospace; }
"""


class _FaceOverlay(Gtk.DrawingArea):
    """Transparent canvas that draws detected faces on top of the video.

    ``Gtk.Picture`` uses :attr:`Gtk.ContentFit.CONTAIN`, so the video is scaled
    down and centred inside the widget allocation; the same transform is
    applied here to map frame coordinates to widget coordinates.
    """

    def __init__(self) -> None:
        super().__init__()
        self.set_can_target(False)
        self.set_draw_func(self._draw)
        self._faces: tuple[Face, ...] = ()
        self._frame_width = 0.0
        self._frame_height = 0.0

    def set_faces(
        self, faces: tuple[Face, ...], frame_width: float, frame_height: float
    ) -> None:
        self._faces = faces
        self._frame_width = frame_width
        self._frame_height = frame_height
        self.queue_draw()

    def _draw(
        self, _area: Gtk.DrawingArea, cr: object, width: int, height: int
    ) -> None:
        if not self._faces or self._frame_width <= 0 or self._frame_height <= 0:
            return
        scale = min(width / self._frame_width, height / self._frame_height)
        offset_x = (width - self._frame_width * scale) / 2.0
        offset_y = (height - self._frame_height * scale) / 2.0
        cr.set_line_width(2.0)  # type: ignore[attr-defined]
        cr.set_source_rgba(0.18, 0.86, 0.42, 0.95)  # type: ignore[attr-defined]
        for face in self._faces:
            cr.rectangle(  # type: ignore[attr-defined]
                offset_x + face.x * scale,
                offset_y + face.y * scale,
                face.width * scale,
                face.height * scale,
            )
            cr.stroke()  # type: ignore[attr-defined]
            for landmark_x, landmark_y in face.landmarks:
                cr.arc(  # type: ignore[attr-defined]
                    offset_x + landmark_x * scale,
                    offset_y + landmark_y * scale,
                    2.0,
                    0.0,
                    6.283185307179586,
                )
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


def _labelled_scale(label: Gtk.Label, scale: Gtk.Scale) -> Gtk.Widget:
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
    label.set_xalign(0.0)
    box.append(label)
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
        self.config = config
        self.demo = demo
        self._t = Translator(language)
        self.playlist = Playlist(media)
        # ``--record`` bypasses the on-screen video sink so the recorder
        # owns the GPU and the recorded file stays a smooth 30 fps.
        self.backend = PlaybackBackend(config, display_bypass=record is not None)
        self._fps = 0.0
        self._duration_ns = 0
        self._updating_seek = False
        self._timer_id = 0
        self._faces: tuple[Face, ...] = ()
        self._detection_ms = 0.0
        self._detection_error: str | None = None
        self._beauty_error: str | None = None
        self._show_faces = True
        self._last_detection_label_at = 0.0
        self._current_path: Path | None = None
        self._hint_key: str | None = None
        self._hint_values: dict[str, object] = {}
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
        self.backend.on_recording_error = self._on_recording_error

        self.set_default_size(1280, 800)

        header = Gtk.HeaderBar()
        self.set_titlebar(header)
        self._open_button = Gtk.Button()
        self._open_button.connect("clicked", self._on_open_clicked)
        header.pack_start(self._open_button)

        self._compare_button = Gtk.ToggleButton()
        self._compare_button.set_active(config.beauty.compare_mode)
        self._compare_button.connect("toggled", self._on_compare_toggled)

        self._language_button = Gtk.Button()
        self._language_button.connect("clicked", self._on_language_clicked)
        end_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        end_box.append(self._compare_button)
        end_box.append(self._language_button)
        header.pack_end(end_box)

        paned = Gtk.Paned.new(Gtk.Orientation.HORIZONTAL)
        paned.set_position(940)
        paned.set_wide_handle(True)
        paned.set_start_child(self._build_player_area())
        paned.set_end_child(self._build_sidebar())
        self.set_child(paned)

        self._apply_demo_mode()

        controller = Gtk.EventControllerKey()
        controller.connect("key-pressed", self._on_key_pressed)
        self.add_controller(controller)

        self._timer_id = GLib.timeout_add(200, self._tick)
        self._closed = False
        # GTK4 no longer emits GtkWidget::destroy, so the teardown hook is
        # close-request; it runs synchronously from Window.close()/destroy().
        self.connect("close-request", self._on_close_request)

        if fullscreen or demo:
            self.fullscreen()
        if self.playlist:
            GLib.idle_add(self._start_index, 0)
        else:
            self._set_hint_key("hint_open")
        self._apply_language()
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
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.set_margin_top(8)
        box.set_margin_bottom(8)
        box.set_margin_start(8)
        box.set_margin_end(8)

        overlay = Gtk.Overlay()
        overlay.set_hexpand(True)
        overlay.set_vexpand(True)
        overlay.add_css_class("video-surface")

        self._picture = Gtk.Picture()
        self._picture.set_hexpand(True)
        self._picture.set_vexpand(True)
        self._picture.set_can_shrink(True)
        self._picture.set_content_fit(Gtk.ContentFit.CONTAIN)
        overlay.set_child(self._picture)

        self._face_overlay = _FaceOverlay()
        self._face_overlay.set_hexpand(True)
        self._face_overlay.set_vexpand(True)
        overlay.add_overlay(self._face_overlay)

        self._placeholder = Gtk.Label()
        self._placeholder.set_justify(Gtk.Justification.CENTER)
        self._placeholder.add_css_class("video-placeholder")
        overlay.add_overlay(self._placeholder)
        box.append(overlay)
        self._player_overlay = overlay

        self._info_label = Gtk.Label()
        self._info_label.set_xalign(0.0)
        self._info_label.set_selectable(True)
        self._info_label.add_css_class("info-line")
        self._info_label.set_visible(self.config.debug.show_info_panel)
        box.append(self._info_label)

        box.append(self._build_controls())

        self._hint_label = Gtk.Label(label="")
        self._hint_label.set_xalign(0.0)
        self._hint_label.set_wrap(True)
        self._hint_label.add_css_class("dim-label")
        box.append(self._hint_label)
        return box

    def _build_controls(self) -> Gtk.Widget:
        bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)

        self._play_button = Gtk.Button.new_from_icon_name("media-playback-start-symbolic")
        self._play_button.connect("clicked", self._on_play_clicked)
        bar.append(self._play_button)

        self._prev_button = Gtk.Button.new_from_icon_name("media-skip-backward-symbolic")
        self._prev_button.connect("clicked", self._on_previous_clicked)
        bar.append(self._prev_button)

        self._next_button = Gtk.Button.new_from_icon_name("media-skip-forward-symbolic")
        self._next_button.connect("clicked", self._on_next_clicked)
        bar.append(self._next_button)

        self._seek = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0, 1000, 1)
        self._seek.set_hexpand(True)
        self._seek.set_draw_value(False)
        self._seek.connect("value-changed", self._on_seek_changed)
        bar.append(self._seek)

        self._time_label = Gtk.Label(label="00:00 / 00:00")
        bar.append(self._time_label)

        self._volume = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0, 100, 1)
        self._volume.set_size_request(110, -1)
        self._volume.set_value(self.config.player.volume)
        self._volume.set_draw_value(False)
        self._volume.connect("value-changed", self._on_volume_changed)
        bar.append(self._volume)

        self._speed = Gtk.DropDown.new_from_strings(list(SPEED_CHOICES))
        self._speed.set_selected(_closest_speed_index(self.config.player.speed))
        self._speed.connect("notify::selected", self._on_speed_changed)
        bar.append(self._speed)

        self._fullscreen_button = Gtk.Button.new_from_icon_name("view-fullscreen-symbolic")
        self._fullscreen_button.connect("clicked", self._on_fullscreen_clicked)
        bar.append(self._fullscreen_button)
        self._controls_bar = bar
        return bar

    def _build_sidebar(self) -> Gtk.Widget:
        panel = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        panel.set_size_request(340, -1)
        panel.set_margin_top(8)
        panel.set_margin_bottom(8)
        panel.set_margin_start(4)
        panel.set_margin_end(8)

        self._playlist_title = Gtk.Label()
        self._playlist_title.set_xalign(0.0)
        panel.append(self._playlist_title)

        self._playlist_list = Gtk.ListBox()
        self._playlist_list.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self._playlist_list.set_activate_on_single_click(True)
        self._playlist_list.connect("row-activated", self._on_row_activated)
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_vexpand(False)
        scrolled.set_size_request(-1, 160)
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scrolled.set_child(self._playlist_list)
        panel.append(scrolled)
        self._sync_playlist()

        panel.append(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL))

        self._beauty_title = Gtk.Label()
        self._beauty_title.set_xalign(0.0)
        panel.append(self._beauty_title)

        self._beauty_switch = Gtk.Switch()
        self._beauty_switch.set_active(self.config.beauty.enabled)
        self._beauty_switch.connect("notify::active", self._on_beauty_toggled)
        self._auto_beauty_label = Gtk.Label()
        panel.append(_labelled_row(self._auto_beauty_label, self._beauty_switch))

        self._smoothing = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0, 100, 1)
        self._smoothing.set_value(self.config.beauty.smoothing)
        self._smoothing.set_draw_value(True)
        self._smoothing.set_hexpand(True)
        self._smoothing.connect("value-changed", self._on_smoothing_changed)
        self._smoothing_label = Gtk.Label()
        panel.append(_labelled_scale(self._smoothing_label, self._smoothing))

        self._whitening = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0, 100, 1)
        self._whitening.set_value(self.config.beauty.whitening)
        self._whitening.set_draw_value(True)
        self._whitening.set_hexpand(True)
        self._whitening.connect("value-changed", self._on_whitening_changed)
        self._whitening_label = Gtk.Label()
        panel.append(_labelled_scale(self._whitening_label, self._whitening))

        self._compare_switch = Gtk.Switch()
        self._compare_switch.set_active(self.config.beauty.compare_mode)
        self._compare_switch.connect("notify::active", self._on_compare_switch_toggled)
        self._compare_label = Gtk.Label()
        panel.append(_labelled_row(self._compare_label, self._compare_switch))

        self._split = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0.1, 0.9, 0.05)
        self._split.set_value(self.config.beauty.split)
        self._split.set_draw_value(True)
        self._split.set_digits(2)
        self._split.set_hexpand(True)
        self._split.connect("value-changed", self._on_split_changed)
        self._split_label = Gtk.Label()
        panel.append(_labelled_scale(self._split_label, self._split))

        self._faces_switch = Gtk.Switch()
        self._faces_switch.set_active(self._show_faces)
        self._faces_switch.connect("notify::active", self._on_show_faces_toggled)
        self._faces_label = Gtk.Label()
        panel.append(_labelled_row(self._faces_label, self._faces_switch))

        self._params_label = Gtk.Label()
        self._params_label.set_xalign(0.0)
        self._params_label.set_wrap(True)
        self._params_label.add_css_class("dim-label")
        panel.append(self._params_label)
        self._refresh_params_label()

        self._note_label = Gtk.Label()
        self._note_label.set_xalign(0.0)
        self._note_label.set_wrap(True)
        self._note_label.add_css_class("dim-label")
        panel.append(self._note_label)

        # M5 experimental warp controls live in a collapsed expander so the
        # stable M3 beauty controls stay the default path.
        self._experimental_expander = Gtk.Expander()
        self._experimental_expander.set_expanded(False)
        self._experimental_expander.connect(
            "notify::expanded", self._on_experimental_expanded
        )
        warp_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        warp_box.set_margin_top(6)
        warp_box.set_margin_start(8)

        self._face_slim = Gtk.Scale.new_with_range(
            Gtk.Orientation.HORIZONTAL, 0, 100, 1
        )
        self._face_slim.set_value(self.config.beauty.face_slim)
        self._face_slim.set_draw_value(True)
        self._face_slim.set_hexpand(True)
        self._face_slim.connect("value-changed", self._on_face_slim_changed)
        self._face_slim_label = Gtk.Label()
        warp_box.append(_labelled_scale(self._face_slim_label, self._face_slim))

        self._eye_enlarge = Gtk.Scale.new_with_range(
            Gtk.Orientation.HORIZONTAL, 0, 100, 1
        )
        self._eye_enlarge.set_value(self.config.beauty.eye_enlarge)
        self._eye_enlarge.set_draw_value(True)
        self._eye_enlarge.set_hexpand(True)
        self._eye_enlarge.connect("value-changed", self._on_eye_enlarge_changed)
        self._eye_enlarge_label = Gtk.Label()
        warp_box.append(_labelled_scale(self._eye_enlarge_label, self._eye_enlarge))

        self._experimental_note_label = Gtk.Label()
        self._experimental_note_label.set_xalign(0.0)
        self._experimental_note_label.set_wrap(True)
        self._experimental_note_label.add_css_class("dim-label")
        warp_box.append(self._experimental_note_label)
        self._experimental_expander.set_child(warp_box)
        panel.append(self._experimental_expander)

        # Wheel events over a Gtk.Scale adjust that scale by default. On the
        # board panel users expect the wheel to scroll the whole sidebar, so
        # intercept vertical wheel events on every sidebar scale and move the
        # sidebar adjustment instead. The playlist keeps its own scroll.
        for scale in (
            self._smoothing,
            self._whitening,
            self._split,
            self._face_slim,
            self._eye_enlarge,
        ):
            self._install_page_wheel_scroll(scale)

        # Keep the controls reachable on the 1280x800 board panel: the sidebar
        # itself scrolls, so expanding "Experimental" never grows the window
        # beyond the screen.
        sidebar_scroll = Gtk.ScrolledWindow()
        sidebar_scroll.set_vexpand(True)
        sidebar_scroll.set_hexpand(False)
        sidebar_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        sidebar_scroll.set_child(panel)
        self._sidebar_scroll = sidebar_scroll
        return sidebar_scroll

    def _on_experimental_expanded(
        self, expander: Gtk.Expander, _pspec: object
    ) -> None:
        log_action("experimental", expanded=expander.get_expanded())
        if not expander.get_expanded():
            return
        # The expander is at the bottom of the sidebar; once it opens, scroll
        # to the new bottom so both sliders are immediately reachable.
        # Defer one layout tick: changing the adjustment from this notify
        # handler (or from an idle callback) can crash GTK 4.18 on the board.
        def scroll_to_bottom() -> bool:
            if self._closed:
                return GLib.SOURCE_REMOVE
            adjustment = self._sidebar_scroll.get_vadjustment()
            if adjustment is not None:
                adjustment.set_value(
                    max(
                        adjustment.get_lower(),
                        adjustment.get_upper() - adjustment.get_page_size(),
                    )
                )
            return GLib.SOURCE_REMOVE

        GLib.timeout_add(150, scroll_to_bottom)

    def _install_page_wheel_scroll(self, scale: Gtk.Scale) -> None:
        """Make the wheel over a sidebar scale scroll the sidebar page."""
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
        sidebar_scroll = getattr(self, "_sidebar_scroll", None)
        if sidebar_scroll is None:
            return False
        adjustment = sidebar_scroll.get_vadjustment()
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
        if self._current_path is not None:
            self.set_title(t("title_with_media", name=self._current_path.name))
        else:
            self.set_title(t("app_title"))

        self._open_button.set_label(t("open"))
        self._open_button.set_tooltip_text(t("open_tooltip"))
        self._compare_button.set_label(t("compare"))
        self._compare_button.set_tooltip_text(t("compare_tooltip"))
        self._language_button.set_label(t("switch_language"))
        self._language_button.set_tooltip_text(t("switch_language_tooltip"))

        self._placeholder.set_text(t("placeholder"))
        self._playlist_title.set_text(t("playlist"))
        self._beauty_title.set_text(t("beauty"))
        self._auto_beauty_label.set_text(t("auto_beauty"))
        self._smoothing_label.set_text(t("smoothing"))
        self._whitening_label.set_text(t("whitening"))
        self._compare_label.set_text(t("original_beautified"))
        self._split_label.set_text(t("split_position"))
        self._faces_label.set_text(t("show_face_boxes"))
        self._note_label.set_text(t("note"))
        self._experimental_expander.set_label(t("experimental"))
        self._face_slim_label.set_text(t("face_slim"))
        self._eye_enlarge_label.set_text(t("eye_enlarge"))
        self._experimental_note_label.set_text(t("experimental_note"))

        self._play_button.set_tooltip_text(t("play_pause"))
        self._prev_button.set_tooltip_text(t("previous"))
        self._next_button.set_tooltip_text(t("next"))
        self._seek.set_tooltip_text(t("seek"))
        self._volume.set_tooltip_text(t("volume"))
        self._speed.set_tooltip_text(t("speed"))
        self._fullscreen_button.set_tooltip_text(t("fullscreen"))

        self._refresh_params_label()
        if self.backend.has_media:
            self._update_info_label()
        else:
            self._info_label.set_text(t("info_placeholder"))
        if self._hint_key is not None:
            self._hint_label.set_text(t(self._hint_key, **self._hint_values))

    def _on_language_clicked(self, _button: Gtk.Button) -> None:
        self._t.toggle()
        try:
            save_language(self._t.language)
        except OSError:
            # A read-only home directory must not break the switch.
            pass
        self._apply_language()
        log_action("language", lang=self._t.language)

    # -- playback -------------------------------------------------------

    def _apply_demo_mode(self) -> None:
        if not self.demo:
            return
        self._info_label.set_visible(True)
        # F9: the demo mode always shows the original/beautified split.
        self.config.beauty.compare_mode = True
        self._compare_switch.set_active(True)
        self._compare_button.set_active(True)
        self._faces_switch.set_active(True)
        self.backend.refresh_beauty()
        self._refresh_params_label()

    def _start_index(self, index: int) -> bool:
        try:
            path = self.playlist.select(index)
        except IndexError:
            return False
        self._play_path(path)
        return False

    def _play_path(self, path: Path) -> None:
        log_action("play_path", path=path)
        if not path.exists():
            self._show_error(self._t("media_file_not_found", path=path))
            return
        list_index = self.playlist.add(path)
        self.playlist.select(list_index)
        try:
            self.backend.open(path, autoplay=True)
        except PlaybackError as exc:
            self._show_error(str(exc))
            return
        log_action("play_path_ok", path=path)
        self._fps = 0.0
        self._faces = ()
        self._detection_ms = 0.0
        self._detection_error = None
        self._face_overlay.set_faces((), 0.0, 0.0)
        paintable = self.backend.paintable
        if paintable is not None:
            self._picture.set_paintable(paintable)
            self._placeholder.set_visible(False)
        self._sync_playlist()
        self._update_play_icon(True)
        self._current_path = path
        self.set_title(self._t("title_with_media", name=path.name))
        self._set_hint_key(None)

    def _on_play_clicked(self, _button: Gtk.Button | None) -> None:
        if not self.backend.has_media:
            if self.playlist:
                self._start_index(0)
            else:
                self._on_open_clicked(self._open_button)
            return
        self.backend.toggle_play()
        self._update_play_icon(self.backend.is_playing)
        log_action("play_toggle", playing=self.backend.is_playing)

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
        self.backend.seek_fraction(scale.get_value() / 1000.0)

    def _on_volume_changed(self, scale: Gtk.Scale) -> None:
        self.config.player.volume = int(round(scale.get_value()))
        self.backend.set_volume(self.config.player.volume)

    def _on_speed_changed(self, dropdown: Gtk.DropDown, _param: object) -> None:
        index = dropdown.get_selected()
        if 0 <= index < len(SPEED_VALUES):
            self.config.player.speed = SPEED_VALUES[index]
            self.backend.set_rate(SPEED_VALUES[index])

    def _on_fullscreen_clicked(self, _button: Gtk.Button) -> None:
        if self.is_fullscreen():
            self.unfullscreen()
            log_action("fullscreen", on=False)
        else:
            self.fullscreen()
            log_action("fullscreen", on=True)

    def _on_row_activated(self, _listbox: Gtk.ListBox, row: Gtk.ListBoxRow) -> None:
        log_action("playlist_row", index=row.get_index())
        self._start_index(row.get_index())

    # -- beauty panel (M3: live shader uniforms) ------------------------

    def _on_smoothing_changed(self, scale: Gtk.Scale) -> None:
        self.config.beauty.smoothing = int(round(scale.get_value()))
        self._refresh_beauty()
        log_action("smoothing", value=self.config.beauty.smoothing)

    def _on_whitening_changed(self, scale: Gtk.Scale) -> None:
        self.config.beauty.whitening = int(round(scale.get_value()))
        self._refresh_beauty()
        log_action("whitening", value=self.config.beauty.whitening)

    def _on_split_changed(self, scale: Gtk.Scale) -> None:
        self.config.beauty.split = round(scale.get_value(), 3)
        self._refresh_beauty()
        log_action("split", value=self.config.beauty.split)

    def _on_face_slim_changed(self, scale: Gtk.Scale) -> None:
        self.config.beauty.face_slim = round(scale.get_value(), 1)
        self._refresh_beauty()
        log_action("face_slim", value=self.config.beauty.face_slim)

    def _on_eye_enlarge_changed(self, scale: Gtk.Scale) -> None:
        self.config.beauty.eye_enlarge = round(scale.get_value(), 1)
        self._refresh_beauty()
        log_action("eye_enlarge", value=self.config.beauty.eye_enlarge)

    def _on_beauty_toggled(self, switch: Gtk.Switch, _param: object) -> None:
        self.config.beauty.enabled = switch.get_active()
        self._refresh_beauty()
        log_action("beauty", enabled=self.config.beauty.enabled)

    def _on_compare_toggled(self, button: Gtk.ToggleButton) -> None:
        self.config.beauty.compare_mode = button.get_active()
        self._compare_switch.set_active(button.get_active())
        self._refresh_beauty()

    def _on_compare_switch_toggled(self, switch: Gtk.Switch, _param: object) -> None:
        self.config.beauty.compare_mode = switch.get_active()
        self._compare_button.set_active(switch.get_active())
        self._refresh_beauty()
        log_action("compare", enabled=self.config.beauty.compare_mode)

    def _toggle_beauty(self) -> None:
        self._beauty_switch.set_active(not self._beauty_switch.get_active())

    def _toggle_compare(self) -> None:
        self._compare_switch.set_active(not self._compare_switch.get_active())

    def _toggle_debug_mask(self) -> None:
        self.config.beauty.debug_mask = not self.config.beauty.debug_mask
        self._refresh_beauty()
        log_action("debug_mask", enabled=self.config.beauty.debug_mask)

    def _refresh_beauty(self) -> None:
        """Apply the current beauty config to the running shader immediately."""
        self.backend.refresh_beauty()
        self._refresh_params_label()
        self._update_info_label()

    def _refresh_params_label(self) -> None:
        beauty = self.config.beauty
        params = BeautyParams.from_config(beauty)
        t = self._t
        state = t("beauty_state_on" if beauty.enabled else "beauty_state_off")
        if beauty.compare_mode:
            state += t("beauty_split", split=beauty.split)
        warp = ""
        if params.face_slim > 0.0 or params.eye_enlarge > 0.0:
            warp = t(
                "beauty_warp",
                slim=params.face_slim,
                eyes=params.eye_enlarge,
            )
        extra = ""
        if self.backend.beauty_available:
            extra = t("beauty_ms", ms=self.backend.beauty_ms)
        elif not beauty.enabled:
            extra = ""
        else:
            extra = t("beauty_gpu_unavailable")
        self._params_label.set_text(
            t(
                "beauty_params",
                state=state,
                smoothing=params.smoothing,
                whitening=params.whitening,
                warp=warp,
                extra=extra,
            )
        )

    def _on_beauty_error(self, message: str) -> None:
        log_action("beauty_error", message=message)
        self._beauty_error = message
        self._beauty_switch.set_sensitive(False)
        self._beauty_switch.set_active(False)
        self.config.beauty.enabled = False
        self._set_hint_key("beauty_error", message=message)
        self._hint_label.add_css_class("error-hint")
        self._refresh_params_label()
        self._update_info_label()

    # -- backend callbacks ----------------------------------------------

    def _on_state_changed(self, state: str) -> None:
        self._update_play_icon(state == "playing")
        log_action("playback_state", state=state)

    def _on_video_info(self, info: dict[str, object]) -> None:
        # The beauty element exists only once the video branch is built, so the
        # panel is refreshed here to replace the "unavailable" placeholder.
        self._refresh_params_label()
        self._update_info_label()

    def _on_fps(self, fps: float) -> None:
        self._fps = fps
        self._update_info_label()

    def _on_detection(self, result: DetectionResult) -> None:
        if not self._show_faces:
            return
        faces = tuple(result.faces)
        changed = faces != self._faces
        self._faces = faces
        self._detection_ms = result.inference_ms
        if changed:
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
        self._show_faces = switch.get_active()
        log_action("show_faces", enabled=self._show_faces)
        if not self._show_faces:
            self._face_overlay.set_faces((), 0.0, 0.0)
        elif self._faces:
            width = float(self.backend.video_info.get("width") or 0)
            height = float(self.backend.video_info.get("height") or 0)
            self._face_overlay.set_faces(self._faces, width, height)

    def _on_eos(self) -> None:
        log_action("eos")
        self._update_play_icon(False)
        if self.playlist.has_next():
            target = self.playlist.next()
            if target is not None:
                log_action("eos_advance")
                self._play_path(target)
                return
        self._set_hint_key("hint_finished")

    def _show_error(self, message: str) -> None:
        log_action("playback_error", message=message)
        self._set_hint_key("playback_error", message=message)
        self._hint_label.add_css_class("error-hint")
        self._update_play_icon(False)

    # -- helpers --------------------------------------------------------

    def _set_hint_key(self, key: str | None, **values: object) -> None:
        self._hint_key = key
        self._hint_values = values
        self._hint_label.remove_css_class("error-hint")
        self._hint_label.set_text("" if key is None else self._t(key, **values))

    def _update_play_icon(self, playing: bool) -> None:
        icon = "media-playback-pause-symbolic" if playing else "media-playback-start-symbolic"
        self._play_button.set_icon_name(icon)

    def _update_info_label(self) -> None:
        t = self._t
        info = self.backend.video_info
        width = info.get("width", "—")
        height = info.get("height", "—")
        codec = info.get("codec", "—")
        decoder = info.get("decoder", "—")
        fps = self._fps or info.get("fps") or "—"
        if self._detection_error:
            npu = t("npu_off")
        elif self._faces:
            count = len(self._faces)
            npu = t("npu_face" if count == 1 else "npu_faces", count=count, ms=self._detection_ms)
        else:
            npu = "—"
        if self._beauty_error or not self.backend.beauty_available:
            beauty = (
                t("beauty_unavailable")
                if self._beauty_error
                else t("beauty_state_off")
            )
        elif not self.config.beauty.enabled:
            beauty = t("beauty_state_off")
        else:
            beauty = t("beauty_on", ms=self.backend.beauty_ms)
        self._info_label.set_text(
            t(
                "info_line",
                width=width,
                height=height,
                codec=codec,
                decoder=decoder,
                fps=fps,
                npu=npu,
                beauty=beauty,
            )
        )

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

    def _tick(self) -> bool:
        if self.backend.has_media:
            measured = self.backend.measured_fps()
            if measured and abs(measured - self._fps) >= 0.5:
                self._fps = measured
                self._update_info_label()
            duration = self.backend.duration_ns()
            position = self.backend.position_ns()
            if duration > 0:
                self._duration_ns = duration
                if not self._updating_seek:
                    self._updating_seek = True
                    self._seek.set_value(position / duration * 1000.0)
                    self._updating_seek = False
                self._time_label.set_text(
                    f"{_format_time(position)} / {_format_time(duration)}"
                )
            self._update_info_label()
            if self.backend.beauty_available and self.config.beauty.enabled:
                # Keep the panel's ms/frame in sync with the live GPU timing;
                # otherwise it stays at the value captured before playback.
                self._refresh_params_label()
        # Log after the measured-fps correction above: logging first sampled
        # fpsdisplaysink's lifetime average, which reads far too low while the
        # stream warms up and looked like a fps dip on a perfectly smooth run.
        self._maybe_log_perf()
        if self._record_path is not None:
            self._maybe_start_recording()
        return True

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
            self._play_path(Path(path_text))

    def _on_key_pressed(
        self,
        _controller: Gtk.EventControllerKey,
        keyval: int,
        _keycode: int,
        _state: Gdk.ModifierType,
    ) -> bool:
        name = Gdk.keyval_name(keyval)
        if name in {
            "space", "Left", "Right", "f", "F", "Escape", "n", "N",
            "p", "P", "b", "B", "c", "C", "m", "M", "r", "R", "o", "O",
        }:
            log_action("key", name=name)
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
        if name == "Escape" and self.is_fullscreen():
            self.unfullscreen()
            return True
        if name in ("n", "N"):
            self._on_next_clicked(None)
            return True
        if name in ("p", "P"):
            self._on_previous_clicked(None)
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
        builder.add_layer("sidebar", self._sidebar_scroll)
        builder.add_layer("info", self._info_label)
        builder.add_layer("controls", self._controls_bar)
        builder.add_layer("hint", self._hint_label)
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
        """Window background colour, used to fill the recording canvas."""
        try:
            found, colour = self.get_style_context().lookup_color("window_bg_color")
            if found:
                return (
                    int(round(colour.red * 255)),
                    int(round(colour.green * 255)),
                    int(round(colour.blue * 255)),
                    255,
                )
        except (AttributeError, TypeError, ValueError):  # pragma: no cover
            pass
        return (0xFA, 0xFA, 0xFA, 255)

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
        # Layer capture costs 2-10 ms per widget: the sidebar is big and rarely
        # changes (3 s), the info line and controls only tick once a second.
        # The first two seconds are different: the window may still be painting
        # its first frames, and a widget snapshot of an unpainted widget comes
        # back blank, so every layer is refreshed until the tree settled.
        if self._record_ticks <= 20:
            builder.refresh()
        else:
            if self._record_ticks % 30 == 1:
                builder.refresh(("sidebar",))
            if self._record_ticks % 10 == 1:
                builder.refresh(("info", "controls", "hint"))
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
        if os.environ.get("REC_DEBUG"):
            print("[rec] window closing", flush=True)
        self._stop_recording()
        if self._timer_id:
            GLib.source_remove(self._timer_id)
            self._timer_id = 0
        self.backend.close()
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
