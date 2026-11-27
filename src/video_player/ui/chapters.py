"""Optional chapter marks for the playback timeline.

GStreamer's ``qtdemux`` on this stack does not expose an MP4 chapter TOC
(``Gst.TocSetter`` is not implemented for it), and the demo footage carries no
chapters at all.  The timeline therefore reads an optional sidecar next to the
clip instead of inventing ticks from the duration::

    <clip name>.chapters.json
    {"chapters": [{"start": 8.0, "title": "开场"}, ...]}

A bare list is accepted too.  ``start`` is in seconds; ``start_ns`` is accepted
for exactness.  A broken or missing sidecar simply yields no marks: chapter
decoration must never keep a clip from playing.  The data is user-authored,
so the UI never displays a tick that the file did not provide.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import math
from pathlib import Path
from typing import Any

__all__ = [
    "CHAPTER_SUFFIX",
    "MAX_CHAPTERS",
    "Chapter",
    "ChapterMark",
    "chapter_marks",
    "load_chapters",
    "parse_chapters",
    "sidecar_path",
]

#: Sidecar name is appended to the full clip name (``clip.mp4.chapters.json``)
#: so two clips that differ only by suffix never share a chapter file.
CHAPTER_SUFFIX = ".chapters.json"

#: A runaway sidecar must not turn the timeline into a solid bar.
MAX_CHAPTERS = 64

#: Labels are drawn nowhere yet, but keep the payload bounded.
_MAX_TITLE_CHARS = 96


@dataclass(frozen=True)
class Chapter:
    """One chapter start in the timeline's own time base (nanoseconds)."""

    start_ns: int
    title: str = ""


@dataclass(frozen=True)
class ChapterMark:
    """A chapter position as a 0–1 fraction of the clip."""

    fraction: float
    title: str = ""


def sidecar_path(clip: Path | str) -> Path:
    """Return the chapter sidecar path for *clip*."""
    path = Path(clip)
    return path.with_name(path.name + CHAPTER_SUFFIX)


def _title(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    text = " ".join(value.split())
    return text[:_MAX_TITLE_CHARS]


def _entry(entry: Any) -> Chapter | None:
    if not isinstance(entry, dict):
        return None
    raw_ns = entry.get("start_ns")
    raw_s = entry.get("start")
    start_ns = 0
    try:
        if raw_ns is not None:
            start_ns = int(round(float(raw_ns)))
        elif raw_s is not None:
            start_ns = int(round(float(raw_s) * 1_000_000_000))
        else:
            return None
    except (TypeError, ValueError, OverflowError):
        return None
    if start_ns < 0:
        return None
    return Chapter(start_ns=start_ns, title=_title(entry.get("title", "")))


def parse_chapters(raw: Any) -> tuple[Chapter, ...]:
    """Validate one decoded sidecar payload.

    Accepts ``{"chapters": [...]}`` and a bare list; entries are sorted by
    start and deduplicated.  Every malformed entry is dropped individually so
    one bad line cannot hide the valid marks around it.
    """
    entries: Any
    if isinstance(raw, dict):
        entries = raw.get("chapters")
    else:
        entries = raw
    if not isinstance(entries, (list, tuple)):
        return ()
    chapters: list[Chapter] = []
    seen: set[int] = set()
    for entry in entries[:MAX_CHAPTERS]:
        chapter = _entry(entry)
        if chapter is None or chapter.start_ns in seen:
            continue
        seen.add(chapter.start_ns)
        chapters.append(chapter)
    chapters.sort(key=lambda chapter: chapter.start_ns)
    return tuple(chapters)


def load_chapters(clip: Path | str) -> tuple[Chapter, ...]:
    """Read the optional sidecar for *clip*; any failure means no chapters."""
    path = sidecar_path(clip)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError):
        return ()
    return parse_chapters(raw)


def chapter_marks(
    chapters: tuple[Chapter, ...] | list[Chapter] | tuple[ChapterMark, ...],
    duration_ns: int,
    *,
    edge_fraction: float = 0.004,
) -> tuple[ChapterMark, ...]:
    """Project chapter starts onto a 0–1 timeline.

    Marks at the very beginning or end are dropped: a tick under the knob at
    00:00 and one against the duration label carry no information.  The
    projection is linear because the playback CDR is; no chapters means an
    empty tuple, never a regular grid.
    """
    if duration_ns <= 0:
        return ()
    marks: list[ChapterMark] = []
    for chapter in chapters:
        if isinstance(chapter, ChapterMark):
            fraction = float(chapter.fraction)
            title = chapter.title
        else:
            fraction = float(chapter.start_ns) / float(duration_ns)
            title = chapter.title
        if not math.isfinite(fraction):
            continue
        if fraction <= edge_fraction or fraction >= 1.0 - edge_fraction:
            continue
        marks.append(ChapterMark(fraction=fraction, title=title))
    marks.sort(key=lambda mark: mark.fraction)
    return tuple(marks)
