"""Pure presentation helpers for the player chrome.

The GTK window is hard to unit-test, so the small pieces of presentation
arithmetic that have caused regressions before live here instead: codec
labels, the letterboxed video rectangle, the four-corner face brackets and
which chrome rows are visible for a given playback state.  Nothing in this
module imports GTK, so the tests run on a host without PyGObject.
"""

from __future__ import annotations

from dataclasses import dataclass
import time

#: Arm length of the four-corner face bracket, in widget pixels.
FACE_CORNER_ARM = 22.0

#: Codec names that arrive as ``x-h264`` from the GStreamer caps and must read
#: as ``H.264`` in the UI.  Keys are normalised (upper case, no punctuation).
_CODEC_ALIASES = {
    "XH264": "H.264",
    "H264": "H.264",
    "AVC": "H.264",
    "AVC1": "H.264",
    "XH265": "H.265",
    "H265": "H.265",
    "HEVC": "H.265",
    "HVC1": "H.265",
    "HEV1": "H.265",
    "XVP8": "VP8",
    "VP8": "VP8",
    "XVP9": "VP9",
    "VP9": "VP9",
    "AV1": "AV1",
    "XAV1": "AV1",
    "MPEG4": "MPEG-4",
    "XMPEG4": "MPEG-4",
}


@dataclass(frozen=True)
class ChromeState:
    """Visibility of the major chrome regions for one playback state."""

    empty: bool
    transport: bool
    info: bool


def normalise_codec(value: object) -> str:
    """Return the display form of a codec name (``X-H264`` -> ``H.264``)."""
    text = str(value or "").strip()
    if not text or text in {"—", "-"}:
        return "—"
    key = (
        text.upper()
        .replace(".", "")
        .replace("-", "")
        .replace("_", "")
        .replace(" ", "")
    )
    return _CODEC_ALIASES.get(key, text)


def chrome_state(has_media: bool, status: str | None) -> ChromeState:
    """Resolve empty-state / transport / info visibility.

    The drop zone is only the empty state: a loading or error card should not
    sit on top of a "drop a video here" panel.  The transport belongs to an
    open clip, so an empty window never shows eleven dead buttons.
    """
    empty = not has_media and status is None
    return ChromeState(empty=empty, transport=has_media, info=has_media)


def contain_rect(
    frame_width: float,
    frame_height: float,
    area_width: float,
    area_height: float,
) -> tuple[float, float, float, float]:
    """Letterbox *frame* inside *area* like ``Gtk.ContentFit.CONTAIN``.

    Returns ``(x, y, width, height)``.  Degenerate inputs fall back to the
    whole area so overlay drawing never divides by zero.
    """
    if frame_width <= 0 or frame_height <= 0 or area_width <= 0 or area_height <= 0:
        return 0.0, 0.0, max(0.0, area_width), max(0.0, area_height)
    scale = min(area_width / frame_width, area_height / frame_height)
    width = frame_width * scale
    height = frame_height * scale
    return (area_width - width) / 2.0, (area_height - height) / 2.0, width, height


def corner_arms(
    x: float,
    y: float,
    width: float,
    height: float,
    arm: float = FACE_CORNER_ARM,
) -> tuple[tuple[float, float, float, float], ...]:
    """Eight line segments drawing an L at each corner of a rectangle.

    The arm is clamped to half of the smaller side, so a tiny face box stays a
    bracket instead of collapsing into a full rectangle.
    """
    if width <= 0 or height <= 0:
        return ()
    arm = max(1.0, min(float(arm), width / 2.0, height / 2.0))
    right = x + width
    bottom = y + height
    return (
        (x, y, x + arm, y),
        (x, y, x, y + arm),
        (right - arm, y, right, y),
        (right, y, right, y + arm),
        (right - arm, bottom, right, bottom),
        (right, bottom - arm, right, bottom),
        (x, bottom, x + arm, bottom),
        (x, bottom - arm, x, bottom),
    )


def format_parameter(value: float, digits: int = 2) -> str:
    """Format a normalised beauty parameter without a trailing ``.00``."""
    text = f"{float(value):.{digits}f}"
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def slider_value_text(value: float) -> str:
    """Beauty strengths are 0-100; the page shows them as integers."""
    return f"{int(round(float(value)))}"


def percent_value_text(value: float) -> str:
    """Split position lives as a 0-1 fraction; the page shows a percentage."""
    return f"{float(value) * 100:.0f}%"


#: v4 mockup opacities: the beauty page body while the effect is off, and the
#: split-position row while A|B itself is off (the page may still be on).
BEAUTY_PAGE_OFF_OPACITY = 0.34
BEAUTY_SPLIT_OFF_OPACITY = 0.40


@dataclass(frozen=True)
class BeautyPageState:
    """What the v4 beauty page and its entrances show for one config state."""

    enabled: bool
    compare: bool
    faces: bool
    page_off: bool
    body_sensitive: bool
    body_opacity: float
    cascade_visible: bool
    note_visible: bool
    split_sensitive: bool
    split_opacity: float
    dock_compare_sensitive: bool
    dock_compare_label: str


def beauty_page_state(
    *,
    enabled: bool,
    compare_mode: bool,
    show_faces: bool,
    page_off_opacity: float = BEAUTY_PAGE_OFF_OPACITY,
    split_off_opacity: float = BEAUTY_SPLIT_OFF_OPACITY,
) -> BeautyPageState:
    """Resolve the v4 cascade: A|B and face boxes only exist while beauty is on.

    The beauty page owns the whole feature, so a ``compare_mode`` left in the
    config from an earlier session must not light a control while the effect is
    off.  ``compare_mode`` and ``show_faces`` are therefore reported (and, by
    the caller, written back) as off whenever beauty is off: turning beauty
    back on starts with a clean page, exactly as the page notice promises.
    """
    enabled = bool(enabled)
    compare = enabled and bool(compare_mode)
    faces = enabled and bool(show_faces)
    return BeautyPageState(
        enabled=enabled,
        compare=compare,
        faces=faces,
        page_off=not enabled,
        body_sensitive=enabled,
        body_opacity=1.0 if enabled else page_off_opacity,
        cascade_visible=not enabled,
        note_visible=enabled,
        split_sensitive=compare,
        split_opacity=1.0 if compare else split_off_opacity,
        dock_compare_sensitive=enabled,
        dock_compare_label="compare" if enabled else "compare_needs_beauty",
    )



def format_duration_ns(nanoseconds: int) -> str:
    """Format a duration as ``mm:ss`` (``h:mm:ss`` past an hour)."""
    seconds = max(0, int(nanoseconds) // 1_000_000_000)
    hours, remainder = divmod(seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours:
        return f"{hours:d}:{minutes:02d}:{seconds:02d}"
    return f"{minutes:02d}:{seconds:02d}"


_SIZES = ("B", "KB", "MB", "GB", "TB")


def format_size(size: int) -> str:
    """Human file size (``66.7 MB``); zero or negative reads as a dash."""
    if size <= 0:
        return "—"
    value = float(size)
    unit = 0
    while value >= 1024.0 and unit < len(_SIZES) - 1:
        value /= 1024.0
        unit += 1
    if unit == 0:
        return f"{int(value)} B"
    return f"{value:.1f} {_SIZES[unit]}"


def format_clock(epoch: float) -> str:
    """``HH:MM`` for a scan timestamp; ``—`` when the value is unknown."""
    if epoch <= 0:
        return "—"
    return time.strftime("%H:%M", time.localtime(epoch))


def format_watch_time(
    updated_at: float,
    *,
    t: Callable[..., str],
    now: float | None = None,
) -> str:
    """Relative watch time for a history entry (``今天 19:04``, ``昨天 21:40``).

    The recent page sorts by watch time, so the card meta has to say *when*
    without a second timestamp column.  Same-day entries read as a clock time,
    yesterday as a word, older entries as a date; a timestamp at or below zero
    (a history entry written before ``updated_at`` existed) reads as unknown.
    """
    if updated_at <= 0:
        return ""
    reference = time.time() if now is None else float(now)
    moment = time.localtime(updated_at)
    clock = time.strftime("%H:%M", moment)
    if abs(reference - updated_at) < 60.0:
        return t("recent_time_just")
    today = time.localtime(reference)
    if time.strftime("%Y-%m-%d", moment) == time.strftime("%Y-%m-%d", today):
        return t("recent_time_today", time=clock)
    yesterday = time.localtime(reference - 86400.0)
    if time.strftime("%Y-%m-%d", moment) == time.strftime("%Y-%m-%d", yesterday):
        return t("recent_time_yesterday", time=clock)
    if moment.tm_year == today.tm_year:
        return t("recent_time_date", month=moment.tm_mon, day=moment.tm_mday)
    return t(
        "recent_time_date_year",
        year=moment.tm_year,
        month=moment.tm_mon,
        day=moment.tm_mday,
    )


def format_resolution(width: int, height: int, fps: float = 0.0) -> str:
    """Card shorthand: ``1080p30`` for standard heights, ``1280×720`` otherwise."""
    if width <= 0 or height <= 0:
        return "—"
    text = f"{height}p" if height in (576, 720, 1080, 1440, 2160) else f"{width}×{height}"
    if fps >= 1.0:
        text += f"{int(round(fps)):d}"
    return text


def video_meta_text(
    *,
    width: int = 0,
    height: int = 0,
    fps: float = 0.0,
    codec: str = "",
    size: int = 0,
) -> str:
    """One-line card meta: ``1080p30 · H.265 · 66.7 MB`` (unknown parts drop out)."""
    parts: list[str] = []
    if width > 0 and height > 0:
        parts.append(format_resolution(width, height, fps))
    if codec:
        parts.append(normalise_codec(codec))
    if size > 0:
        parts.append(format_size(size))
    return " · ".join(parts) if parts else "—"
