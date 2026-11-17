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
from ..face import DetectionResult, Face  # noqa: E402
from ..pipeline import VIDEO_SUFFIXES  # noqa: E402
from ..playlist import Playlist  # noqa: E402

APP_ID = "io.quectel.pi.VideoPlayer"
GST_SECOND = 1_000_000_000

SPEED_CHOICES = ("0.5x", "0.75x", "1.0x", "1.25x", "1.5x", "2.0x")
SPEED_VALUES = (0.5, 0.75, 1.0, 1.25, 1.5, 2.0)

INFO_PLACEHOLDER = "resolution —   codec —   decode —   fps —   NPU —"

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


def _labelled_row(label: str, widget: Gtk.Widget) -> Gtk.Widget:
    row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
    text = Gtk.Label(label=label)
    text.set_xalign(0.0)
    text.set_hexpand(True)
    row.append(text)
    row.append(widget)
    return row


def _labelled_scale(label: str, scale: Gtk.Scale) -> Gtk.Widget:
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
    text = Gtk.Label(label=label)
    text.set_xalign(0.0)
    box.append(text)
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
    ) -> None:
        super().__init__(application=application)
        self.config = config
        self.demo = demo
        self.playlist = Playlist(media)
        self.backend = PlaybackBackend(config)
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

        self.set_title("Video Player · Quectel Pi M2")
        self.set_default_size(1280, 800)

        header = Gtk.HeaderBar()
        self.set_titlebar(header)
        self._open_button = Gtk.Button(label="Open")
        self._open_button.set_tooltip_text("Open a local video file")
        self._open_button.connect("clicked", self._on_open_clicked)
        header.pack_start(self._open_button)

        self._compare_button = Gtk.ToggleButton(label="Compare")
        self._compare_button.set_tooltip_text(
            "Split view: original on the left, beautified on the right (C)"
        )
        self._compare_button.set_active(config.beauty.compare_mode)
        self._compare_button.connect("toggled", self._on_compare_toggled)
        header.pack_end(self._compare_button)

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
        self.connect("destroy", self._on_destroy)

        if fullscreen or demo:
            self.fullscreen()
        if self.playlist:
            GLib.idle_add(self._start_index, 0)
        else:
            self._hint("Open a video to start. Folder arguments become a playlist.")

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

        self._placeholder = Gtk.Label(
            label="Open a video file to start playback\n\n"
            "Local MP4 / MKV H.264 & H.265 files are decoded on the RK3576 VPU"
        )
        self._placeholder.set_justify(Gtk.Justification.CENTER)
        self._placeholder.add_css_class("video-placeholder")
        overlay.add_overlay(self._placeholder)
        box.append(overlay)

        self._info_label = Gtk.Label(label=INFO_PLACEHOLDER)
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
        self._play_button.set_tooltip_text("Play / pause (space)")
        self._play_button.connect("clicked", self._on_play_clicked)
        bar.append(self._play_button)

        self._prev_button = Gtk.Button.new_from_icon_name("media-skip-backward-symbolic")
        self._prev_button.set_tooltip_text("Previous item (p)")
        self._prev_button.connect("clicked", self._on_previous_clicked)
        bar.append(self._prev_button)

        self._next_button = Gtk.Button.new_from_icon_name("media-skip-forward-symbolic")
        self._next_button.set_tooltip_text("Next item (n)")
        self._next_button.connect("clicked", self._on_next_clicked)
        bar.append(self._next_button)

        self._seek = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0, 1000, 1)
        self._seek.set_hexpand(True)
        self._seek.set_draw_value(False)
        self._seek.set_tooltip_text("Seek position")
        self._seek.connect("value-changed", self._on_seek_changed)
        bar.append(self._seek)

        self._time_label = Gtk.Label(label="00:00 / 00:00")
        bar.append(self._time_label)

        self._volume = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0, 100, 1)
        self._volume.set_size_request(110, -1)
        self._volume.set_value(self.config.player.volume)
        self._volume.set_draw_value(False)
        self._volume.set_tooltip_text("Volume")
        self._volume.connect("value-changed", self._on_volume_changed)
        bar.append(self._volume)

        self._speed = Gtk.DropDown.new_from_strings(list(SPEED_CHOICES))
        self._speed.set_selected(_closest_speed_index(self.config.player.speed))
        self._speed.set_tooltip_text("Playback speed")
        self._speed.connect("notify::selected", self._on_speed_changed)
        bar.append(self._speed)

        fullscreen = Gtk.Button.new_from_icon_name("view-fullscreen-symbolic")
        fullscreen.set_tooltip_text("Fullscreen (f)")
        fullscreen.connect("clicked", self._on_fullscreen_clicked)
        bar.append(fullscreen)
        return bar

    def _build_sidebar(self) -> Gtk.Widget:
        panel = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        panel.set_size_request(340, -1)
        panel.set_margin_top(8)
        panel.set_margin_bottom(8)
        panel.set_margin_start(4)
        panel.set_margin_end(8)

        playlist_title = Gtk.Label(label="Playlist")
        playlist_title.set_xalign(0.0)
        panel.append(playlist_title)

        self._playlist_list = Gtk.ListBox()
        self._playlist_list.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self._playlist_list.set_activate_on_single_click(True)
        self._playlist_list.connect("row-activated", self._on_row_activated)
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_vexpand(True)
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scrolled.set_child(self._playlist_list)
        panel.append(scrolled)
        self._sync_playlist()

        panel.append(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL))

        beauty_title = Gtk.Label(label="Beauty")
        beauty_title.set_xalign(0.0)
        panel.append(beauty_title)

        self._beauty_switch = Gtk.Switch()
        self._beauty_switch.set_active(self.config.beauty.enabled)
        self._beauty_switch.connect("notify::active", self._on_beauty_toggled)
        panel.append(_labelled_row("Automatic face beauty", self._beauty_switch))

        self._smoothing = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0, 100, 1)
        self._smoothing.set_value(self.config.beauty.smoothing)
        self._smoothing.set_draw_value(True)
        self._smoothing.set_hexpand(True)
        self._smoothing.connect("value-changed", self._on_smoothing_changed)
        panel.append(_labelled_scale("Smoothing", self._smoothing))

        self._whitening = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0, 100, 1)
        self._whitening.set_value(self.config.beauty.whitening)
        self._whitening.set_draw_value(True)
        self._whitening.set_hexpand(True)
        self._whitening.connect("value-changed", self._on_whitening_changed)
        panel.append(_labelled_scale("Whitening", self._whitening))

        self._compare_switch = Gtk.Switch()
        self._compare_switch.set_active(self.config.beauty.compare_mode)
        self._compare_switch.connect("notify::active", self._on_compare_switch_toggled)
        panel.append(_labelled_row("Original / beautified", self._compare_switch))

        self._split = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0.1, 0.9, 0.05)
        self._split.set_value(self.config.beauty.split)
        self._split.set_draw_value(True)
        self._split.set_digits(2)
        self._split.set_hexpand(True)
        self._split.connect("value-changed", self._on_split_changed)
        panel.append(_labelled_scale("Split position", self._split))

        self._faces_switch = Gtk.Switch()
        self._faces_switch.set_active(self._show_faces)
        self._faces_switch.connect("notify::active", self._on_show_faces_toggled)
        panel.append(_labelled_row("Show face boxes", self._faces_switch))

        self._params_label = Gtk.Label()
        self._params_label.set_xalign(0.0)
        self._params_label.set_wrap(True)
        self._params_label.add_css_class("dim-label")
        panel.append(self._params_label)
        self._refresh_params_label()

        note = Gtk.Label(
            label="Smoothing and whitening run in the GPU shader, limited to the "
            "feathered face mask from the NPU detector. Keys: B beauty, C compare, "
            "M mask preview."
        )
        note.set_xalign(0.0)
        note.set_wrap(True)
        note.add_css_class("dim-label")
        panel.append(note)
        return panel

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
        if not path.exists():
            self._show_error(f"media file not found: {path}")
            return
        list_index = self.playlist.add(path)
        self.playlist.select(list_index)
        try:
            self.backend.open(path, autoplay=True)
        except PlaybackError as exc:
            self._show_error(str(exc))
            return
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
        self.set_title(f"{path.name} · Video Player")
        self._hint("")

    def _on_play_clicked(self, _button: Gtk.Button | None) -> None:
        if not self.backend.has_media:
            if self.playlist:
                self._start_index(0)
            else:
                self._on_open_clicked(self._open_button)
            return
        self.backend.toggle_play()
        self._update_play_icon(self.backend.is_playing)

    def _on_previous_clicked(self, _button: Gtk.Button) -> None:
        if not self.backend.has_media:
            self._start_index(0)
            return
        target = self.playlist.previous()
        if target is None:
            self._hint("This is the first item in the playlist.")
            return
        self._play_path(target)

    def _on_next_clicked(self, _button: Gtk.Button) -> None:
        if not self.backend.has_media:
            self._start_index(0)
            return
        target = self.playlist.next()
        if target is None:
            self._hint("This is the last item in the playlist.")
            return
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
        else:
            self.fullscreen()

    def _on_row_activated(self, _listbox: Gtk.ListBox, row: Gtk.ListBoxRow) -> None:
        self._start_index(row.get_index())

    # -- beauty panel (M3: live shader uniforms) ------------------------

    def _on_smoothing_changed(self, scale: Gtk.Scale) -> None:
        self.config.beauty.smoothing = int(round(scale.get_value()))
        self._refresh_beauty()

    def _on_whitening_changed(self, scale: Gtk.Scale) -> None:
        self.config.beauty.whitening = int(round(scale.get_value()))
        self._refresh_beauty()

    def _on_split_changed(self, scale: Gtk.Scale) -> None:
        self.config.beauty.split = round(scale.get_value(), 3)
        self._refresh_beauty()

    def _on_beauty_toggled(self, switch: Gtk.Switch, _param: object) -> None:
        self.config.beauty.enabled = switch.get_active()
        self._refresh_beauty()

    def _on_compare_toggled(self, button: Gtk.ToggleButton) -> None:
        self.config.beauty.compare_mode = button.get_active()
        self._compare_switch.set_active(button.get_active())
        self._refresh_beauty()

    def _on_compare_switch_toggled(self, switch: Gtk.Switch, _param: object) -> None:
        self.config.beauty.compare_mode = switch.get_active()
        self._compare_button.set_active(switch.get_active())
        self._refresh_beauty()

    def _toggle_beauty(self) -> None:
        self._beauty_switch.set_active(not self._beauty_switch.get_active())

    def _toggle_compare(self) -> None:
        self._compare_switch.set_active(not self._compare_switch.get_active())

    def _toggle_debug_mask(self) -> None:
        self.config.beauty.debug_mask = not self.config.beauty.debug_mask
        self._refresh_beauty()

    def _refresh_beauty(self) -> None:
        """Apply the current beauty config to the running shader immediately."""
        self.backend.refresh_beauty()
        self._refresh_params_label()
        self._update_info_label()

    def _refresh_params_label(self) -> None:
        beauty = self.config.beauty
        params = BeautyParams.from_config(beauty)
        state = "on" if beauty.enabled else "off"
        if beauty.compare_mode:
            state += f", split {beauty.split:.0%}"
        extra = ""
        if self.backend.beauty_available:
            extra = f", {self.backend.beauty_ms:.1f} ms/frame"
        elif not beauty.enabled:
            extra = ""
        else:
            extra = ", GPU pass unavailable"
        self._params_label.set_text(
            f"beauty {state} (smoothing {params.smoothing:.2f}, "
            f"whitening {params.whitening:.2f}){extra}"
        )

    def _on_beauty_error(self, message: str) -> None:
        self._beauty_error = message
        self._beauty_switch.set_sensitive(False)
        self._beauty_switch.set_active(False)
        self.config.beauty.enabled = False
        self._hint(message)
        self._refresh_params_label()
        self._update_info_label()

    # -- backend callbacks ----------------------------------------------

    def _on_state_changed(self, state: str) -> None:
        self._update_play_icon(state == "playing")

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
        self._hint(f"Face detection disabled: {message}")
        self._update_info_label()

    def _on_show_faces_toggled(self, switch: Gtk.Switch, _param: object) -> None:
        self._show_faces = switch.get_active()
        if not self._show_faces:
            self._face_overlay.set_faces((), 0.0, 0.0)
        elif self._faces:
            width = float(self.backend.video_info.get("width") or 0)
            height = float(self.backend.video_info.get("height") or 0)
            self._face_overlay.set_faces(self._faces, width, height)

    def _on_eos(self) -> None:
        self._update_play_icon(False)
        if self.playlist.has_next():
            target = self.playlist.next()
            if target is not None:
                self._play_path(target)
                return
        self._hint("Playback finished.")

    def _show_error(self, message: str) -> None:
        self._hint_label.set_text(f"Playback error: {message}")
        self._hint_label.add_css_class("error-hint")
        self._update_play_icon(False)

    # -- helpers --------------------------------------------------------

    def _hint(self, text: str) -> None:
        self._hint_label.remove_css_class("error-hint")
        self._hint_label.set_text(text)

    def _update_play_icon(self, playing: bool) -> None:
        icon = "media-playback-pause-symbolic" if playing else "media-playback-start-symbolic"
        self._play_button.set_icon_name(icon)

    def _update_info_label(self) -> None:
        info = self.backend.video_info
        width = info.get("width", "—")
        height = info.get("height", "—")
        codec = info.get("codec", "—")
        decoder = info.get("decoder", "—")
        fps = self._fps or info.get("fps") or "—"
        if self._detection_error:
            npu = "off"
        elif self._faces:
            npu = f"{len(self._faces)} face · {self._detection_ms:.1f} ms"
        else:
            npu = "—"
        if self._beauty_error or not self.backend.beauty_available:
            beauty = "unavailable" if self._beauty_error else "off"
            if not self._beauty_error and self.config.beauty.enabled:
                beauty = "off"
        elif not self.config.beauty.enabled:
            beauty = "off"
        else:
            beauty = f"on {self.backend.beauty_ms:.1f} ms"
        self._info_label.set_text(
            f"{width}×{height}   {codec}   decode: {decoder}   "
            f"fps: {fps}   NPU: {npu}   beauty: {beauty}"
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
        self._maybe_log_perf()
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
        dialog = Gtk.FileDialog(title="Open video")
        file_filter = Gtk.FileFilter()
        file_filter.set_name("Video files")
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
        except GLib.Error:
            return
        if gfile is None:
            return
        path_text = gfile.get_path()
        if path_text:
            self._play_path(Path(path_text))

    def _on_key_pressed(
        self,
        _controller: Gtk.EventControllerKey,
        keyval: int,
        _keycode: int,
        _state: Gdk.ModifierType,
    ) -> bool:
        name = Gdk.keyval_name(keyval)
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
        if name in ("o", "O"):
            self._on_open_clicked(self._open_button)
            return True
        return False

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

    def _on_destroy(self, _window: Gtk.Window) -> None:
        if self._timer_id:
            GLib.source_remove(self._timer_id)
            self._timer_id = 0
        self.backend.close()


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
            # Destroy the window first so the GStreamer (GL) pipeline is torn
            # down while GTK is still alive; quitting first can crash on exit.
            window.destroy()
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
) -> int:  # pragma: no cover - needs a display
    """Open the GTK window and block until it closes; returns the exit code."""
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
    )
    code = application.run([])
    sys.stdout.flush()
    sys.stderr.flush()
    # PyGObject finalisation of the GStreamer/GTK objects segfaults on the
    # RK3576 (mpp/GL teardown order). GTK is already shut down at this point,
    # so exit the process directly instead of letting the interpreter run the
    # crashing finalisers.
    os._exit(code)
