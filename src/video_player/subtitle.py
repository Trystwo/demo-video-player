"""External subtitles for local playback (F5, minimal scope).

Why not ``subtitleoverlay``/``textoverlay``?  Both render into the video
frames, which means the text goes through the same GL chain as the picture:
on this board the sink is ``gtk4paintablesink`` inside a GTK widget, and a
textoverlay element would have to negotiate a window handle it does not have.
Drawing the cue in a ``Gtk.Label`` above the picture keeps the video path
untouched (zero risk to the 30 fps budget) and makes the styling a CSS concern
instead of a GStreamer caps concern.

The trade-off is that the subtitles are *not* baked into ``--record`` output.
For the demo that is acceptable: the recorded segments get their captions in
post-production (see ``docs/demo-video/``), and the on-screen overlay is what
the audience reads during a live demo.

Scope: sidecar ``.srt`` files only (UTF-8/GB18030/BOM tolerated).  Embedded
subtitle tracks and ``.ass`` styling are out of scope; the parser ignores
anything it does not understand instead of failing playback.
"""

from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass
from pathlib import Path
import re

__all__ = ["Cue", "SubtitleTrack", "find_sidecar", "load_srt", "parse_srt"]

#: ``00:00:01,250 --> 00:00:04,000`` (also tolerates a dot as the ms separator
#: and the ``HH:MM:SS`` short form some editors emit).
_TIME = r"(?:\d{1,2}:)?\d{1,2}:\d{1,2}[.,]\d{1,3}"
_TIMING_RE = re.compile(rf"^\s*({_TIME})\s*-->\s*({_TIME})\s*$")
_TAG_RE = re.compile(r"<[^>]+>")

#: Suffixes tried next to the video, in priority order.  Chinese releases often
#: use ``.chs``/``.chi``; a plain ``.srt`` is the most common case.
_SIDECAR_SUFFIXES = (
    ".srt",
    ".zh.srt",
    ".chs.srt",
    ".chi.srt",
    ".en.srt",
    ".eng.srt",
)


@dataclass(frozen=True)
class Cue:
    """One subtitle entry, with nanosecond times on the media timeline."""

    start_ns: int
    end_ns: int
    text: str

    def contains(self, position_ns: int) -> bool:
        return self.start_ns <= position_ns < self.end_ns


def _parse_time(value: str) -> int:
    """Convert ``HH:MM:SS,mmm`` to nanoseconds (hours optional)."""
    clock, _, fraction = value.replace(".", ",").partition(",")
    parts = [int(part) for part in clock.split(":")]
    while len(parts) < 3:
        parts.insert(0, 0)
    hours, minutes, seconds = parts
    millis = int((fraction + "000")[:3]) if fraction else 0
    return ((hours * 3600 + minutes * 60 + seconds) * 1000 + millis) * 1_000_000


def parse_srt(text: str) -> list[Cue]:
    """Parse SRT content, skipping blocks that do not look like a cue.

    Tolerant by design: a single malformed block (missing index, stray blank
    lines, HTML tags) must not drop the whole file.
    """
    text = text.replace("\ufeff", "").replace("\r\n", "\n").replace("\r", "\n")
    cues: list[Cue] = []
    for block in re.split(r"\n\s*\n", text):
        lines = [line for line in block.split("\n")]
        if not lines:
            continue
        # The first line may be the sequence number; the timing line follows
        # it.  Some files omit the number entirely.
        timing_index = 0
        if lines and not _TIMING_RE.match(lines[0]):
            timing_index = 1
        if timing_index >= len(lines):
            continue
        match = _TIMING_RE.match(lines[timing_index])
        if match is None:
            continue
        body_lines = [
            _TAG_RE.sub("", line).strip()
            for line in lines[timing_index + 1 :]
            if line.strip()
        ]
        if not body_lines:
            continue
        try:
            start_ns = _parse_time(match.group(1))
            end_ns = _parse_time(match.group(2))
        except ValueError:
            continue
        if end_ns <= start_ns:
            continue
        cues.append(Cue(start_ns=start_ns, end_ns=end_ns, text="\n".join(body_lines)))
    cues.sort(key=lambda cue: cue.start_ns)
    return cues


def _decode(raw: bytes) -> str:
    """Decode a subtitle file, preferring UTF-8 and falling back to GB18030.

    Chinese subtitles downloaded from domestic sites are frequently GBK; a
    latin-1 fallback guarantees we never raise on an unknown code page.
    """
    for encoding in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("latin-1", errors="replace")


def load_srt(path: Path | str) -> list[Cue]:
    """Read and parse one ``.srt`` file; returns an empty list on failure."""
    try:
        raw = Path(path).read_bytes()
    except OSError:
        return []
    return parse_srt(_decode(raw))


def find_sidecar(video: Path | str) -> Path | None:
    """Return the subtitle file that sits next to *video*, if any."""
    path = Path(video)
    for suffix in _SIDECAR_SUFFIXES:
        candidate = path.with_name(path.stem + suffix)
        if candidate.is_file():
            return candidate
    return None


class SubtitleTrack:
    """Time-indexed cue list with O(log n) lookup for the UI tick.

    ``offset_ns`` shifts every cue, which covers the common case of subtitles
    authored for a different intro length.
    """

    def __init__(self, cues: list[Cue] | None = None, *, offset_ns: int = 0) -> None:
        self._cues: list[Cue] = list(cues or [])
        self._starts: list[int] = [cue.start_ns for cue in self._cues]
        self._offset_ns = int(offset_ns)

    def __len__(self) -> int:
        return len(self._cues)

    def __bool__(self) -> bool:
        return bool(self._cues)

    @property
    def cues(self) -> tuple[Cue, ...]:
        return tuple(self._cues)

    @property
    def offset_ns(self) -> int:
        return self._offset_ns

    def text_at(self, position_ns: int) -> str | None:
        """Return the cue covering *position_ns*, or None between cues."""
        if not self._cues:
            return None
        position = int(position_ns) - self._offset_ns
        # ``bisect_right`` gives the first cue starting after ``position``; the
        # candidate is therefore the previous one.
        index = bisect_right(self._starts, position) - 1
        if index < 0:
            return None
        cue = self._cues[index]
        if position < cue.end_ns:
            return cue.text
        return None

    def shifted(self, offset_ns: int) -> "SubtitleTrack":
        return SubtitleTrack(self._cues, offset_ns=offset_ns)
