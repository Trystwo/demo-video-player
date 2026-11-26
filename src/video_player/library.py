"""Local video library: folders, scanning, metadata cache, filters.

The v4 player only knows what the command line passed in; the ui-v5 browse
shell needs a small on-disk library.  This module owns the data side:

* which folders are scanned (``configs/player.example.json`` declares
  ``library.directories``; folders picked in the UI are persisted separately in
  the XDG state directory so a demo machine never has to edit its config),
* the scan itself (one level deep, like :func:`video_player.playlist.expand_media_args`),
* a size+mtime keyed metadata cache so durations, resolutions and codecs are
  probed once per file version and survive restarts,
* the filter/sort arithmetic behind the library page chips.

No GTK and no GStreamer import here: probing lives in :mod:`video_player.probe`,
and everything below stays unit-testable on a host without a display.  Every
filesystem or JSON failure degrades to an empty library instead of raising into
the UI thread.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
import json
import os
from pathlib import Path
from typing import Any, Iterable

from .pipeline import VIDEO_SUFFIXES

__all__ = [
    "FILTERS",
    "FINISHED_THRESHOLD",
    "MAX_ITEMS",
    "SORTS",
    "LibraryItem",
    "LibraryStore",
    "VideoMetadata",
    "default_library_state_path",
    "filter_items",
    "library_counts",
    "scan_library",
    "sort_items",
]

#: A clip counts as watched once this fraction of it was played.  ``recents``
#: keeps finished clips in the history (unlike ``resume``, which drops them),
#: so "已看完" is a filter over the recents progress rather than a second store.
FINISHED_THRESHOLD = 0.95

#: Filter chips: everything, what is in the recents history, what was watched
#: through.  "剧集" is deliberately absent: there is no episode data source yet.
FILTERS = ("all", "recent", "finished")

#: Sort orders exposed by the page-head menu.  ``added`` is the file mtime.
SORTS = ("name", "duration", "added")

#: Upper bound for one scan; a runaway folder must not build a 10 000-card grid.
MAX_ITEMS = 500

_STATE_DIR_NAME = "video-player-demo"
_STATE_FILE_NAME = "library.json"
_FORMAT_VERSION = 1


def default_library_state_path() -> Path:
    """Return the XDG state file holding user-picked folders and metadata."""
    base = os.environ.get("XDG_STATE_HOME")
    root = Path(base) if base else Path.home() / ".local" / "state"
    return root / _STATE_DIR_NAME / _STATE_FILE_NAME


@dataclass(frozen=True)
class VideoMetadata:
    """What one probe learned about a file; all fields may be unknown.

    ``codec`` keeps the container-level short name (``h264``, ``h265``, ``vp9``)
    rather than a display string: the UI normalises it with
    :func:`video_player.ui.chrome.normalise_codec`, the same helper the info row
    uses for the live caps.
    """

    duration_ns: int = 0
    width: int = 0
    height: int = 0
    fps: float = 0.0
    codec: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "duration_ns": int(self.duration_ns),
            "width": int(self.width),
            "height": int(self.height),
            "fps": round(float(self.fps), 3),
            "codec": self.codec,
        }

    @classmethod
    def from_dict(cls, raw: Any) -> "VideoMetadata | None":
        """Parse one cache entry; anything malformed yields None."""
        if not isinstance(raw, dict):
            return None
        try:
            duration = int(raw.get("duration_ns", 0))
            width = int(raw.get("width", 0))
            height = int(raw.get("height", 0))
            fps = float(raw.get("fps", 0.0))
        except (TypeError, ValueError):
            return None
        codec = raw.get("codec", "")
        if not isinstance(codec, str):
            codec = ""
        if duration < 0 or width < 0 or height < 0 or fps < 0:
            return None
        if duration == 0 and width == 0 and height == 0:
            # A probe that learned nothing is not worth caching: the next scan
            # should try again (the file may have been copied mid-write).
            return None
        return cls(
            duration_ns=duration, width=width, height=height, fps=fps, codec=codec
        )


@dataclass(frozen=True)
class LibraryItem:
    """One scanned file plus whatever metadata the cache already had."""

    path: Path
    size: int = 0
    mtime: float = 0.0
    metadata: VideoMetadata | None = None

    @property
    def name(self) -> str:
        return self.path.name

    @property
    def title(self) -> str:
        return self.path.stem

    @property
    def duration_ns(self) -> int:
        return self.metadata.duration_ns if self.metadata is not None else 0

    @property
    def has_metadata(self) -> bool:
        return self.metadata is not None

    def with_metadata(self, metadata: VideoMetadata | None) -> "LibraryItem":
        return replace(self, metadata=metadata)


class LibraryStore:
    """User-picked folders and the metadata cache, persisted as JSON.

    Args:
        path: state file to use; defaults to :func:`default_library_state_path`.
    """

    def __init__(self, path: Path | str | None = None) -> None:
        self._path = Path(path) if path is not None else default_library_state_path()
        self._directories: list[Path] = []
        #: resolved path -> ``{"size", "mtime", <metadata dict>}``
        self._metadata: dict[str, dict[str, Any]] = {}
        self._read()

    # -- inspection --------------------------------------------------------

    @property
    def path(self) -> Path:
        return self._path

    @property
    def directories(self) -> tuple[Path, ...]:
        return tuple(self._directories)

    def metadata_for(
        self, path: Path | str, size: int, mtime: float
    ) -> VideoMetadata | None:
        """Return cached metadata when it still describes this file version."""
        entry = self._metadata.get(self._key(path))
        if entry is None:
            return None
        if int(entry.get("size", -1)) != int(size):
            return None
        if abs(float(entry.get("mtime", -1.0)) - float(mtime)) > 1e-6:
            return None
        return VideoMetadata.from_dict(entry)

    def is_known(self, path: Path | str, size: int, mtime: float) -> bool:
        return self.metadata_for(path, size, mtime) is not None

    # -- mutation ----------------------------------------------------------

    def add_directory(self, directory: Path | str) -> bool:
        """Remember *directory* for future scans; False when it is not a folder."""
        resolved = self._resolve(directory)
        if resolved is None or resolved in self._directories:
            return False
        self._directories.append(resolved)
        return True

    def remove_directory(self, directory: Path | str) -> bool:
        resolved = self._resolve(directory)
        if resolved is None or resolved not in self._directories:
            return False
        self._directories.remove(resolved)
        return True

    def remember_metadata(
        self,
        path: Path | str,
        size: int,
        mtime: float,
        metadata: VideoMetadata,
    ) -> bool:
        """Cache one probe result; False when it was already identical."""
        key = self._key(path)
        payload = metadata.to_dict()
        existing = self._metadata.get(key)
        if (
            existing
            and int(existing.get("size", -1)) == int(size)
            and abs(float(existing.get("mtime", -1.0)) - float(mtime)) <= 1e-6
            and isinstance(existing.get("metadata"), dict)
            and existing["metadata"] == payload
        ):
            return False
        self._metadata[key] = {"size": int(size), "mtime": float(mtime), **payload}
        return True

    # -- persistence -------------------------------------------------------

    def save(self) -> bool:
        """Write the state file atomically; failures are reported, not raised."""
        payload = {
            "version": _FORMAT_VERSION,
            "directories": [str(path) for path in self._directories],
            "metadata": self._metadata,
        }
        tmp = self._path.with_suffix(self._path.suffix + ".tmp")
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            tmp.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            os.replace(tmp, self._path)
        except OSError:
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass
            return False
        return True

    # -- internals ---------------------------------------------------------

    def _key(self, path: Path | str) -> str:
        resolved = self._resolve(path, require_directory=False)
        if resolved is None:
            return str(Path(path).expanduser().absolute())
        return str(resolved)

    def _resolve(
        self, path: Path | str, *, require_directory: bool = True
    ) -> Path | None:
        try:
            resolved = Path(path).expanduser().resolve()
        except (OSError, RuntimeError):
            return None
        if require_directory and not resolved.is_dir():
            return None
        return resolved

    def _read(self) -> None:
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        if not isinstance(raw, dict):
            return
        directories: Iterable[Any] = raw.get("directories") or ()
        if isinstance(directories, list):
            seen: set[Path] = set()
            for entry in directories:
                if not isinstance(entry, str) or not entry.strip():
                    continue
                try:
                    resolved = Path(entry).expanduser().resolve()
                except (OSError, RuntimeError):
                    continue
                if resolved in seen:
                    continue
                seen.add(resolved)
                self._directories.append(resolved)
        metadata = raw.get("metadata")
        if isinstance(metadata, dict):
            for key, entry in metadata.items():
                if not isinstance(key, str) or not isinstance(entry, dict):
                    continue
                try:
                    size = int(entry.get("size", 0))
                    mtime = float(entry.get("mtime", 0.0))
                except (TypeError, ValueError):
                    continue
                if VideoMetadata.from_dict(entry) is None:
                    continue
                self._metadata[key] = {"size": size, "mtime": mtime, **entry}


def scan_library(
    directories: Iterable[Path | str],
    store: LibraryStore | None = None,
    *,
    limit: int = MAX_ITEMS,
) -> list[LibraryItem]:
    """List supported videos directly inside *directories*.

    Directories are scanned one level deep (sub-folders are not walked), files
    are de-duplicated across roots and sorted by lower-case name so the base
    order is stable.  Cached metadata is attached when it still matches the
    file's size and mtime; the caller probes the rest asynchronously.
    """
    items: list[LibraryItem] = []
    seen: set[Path] = set()
    for raw_root in directories:
        try:
            root = Path(raw_root).expanduser().resolve()
        except (OSError, RuntimeError):
            continue
        if not root.is_dir():
            continue
        try:
            entries = sorted(root.iterdir(), key=lambda entry: entry.name.lower())
        except OSError:
            continue
        for entry in entries:
            if len(items) >= limit:
                return items
            if entry.suffix.lower() not in VIDEO_SUFFIXES:
                continue
            try:
                if not entry.is_file():
                    continue
                resolved = entry.resolve()
                stat = resolved.stat()
            except (OSError, RuntimeError):
                continue
            if resolved in seen:
                continue
            seen.add(resolved)
            metadata = (
                store.metadata_for(resolved, stat.st_size, stat.st_mtime)
                if store is not None
                else None
            )
            items.append(
                LibraryItem(
                    path=resolved,
                    size=stat.st_size,
                    mtime=stat.st_mtime,
                    metadata=metadata,
                )
            )
    return items


def filter_items(
    items: Iterable[LibraryItem],
    filter_key: str,
    *,
    recent_paths: Iterable[Path] = (),
    finished_paths: Iterable[Path] = (),
) -> list[LibraryItem]:
    """Keep the items a filter chip selects.

    ``recent_paths`` are the files in the recents history (resolved paths);
    ``finished_paths`` the subset watched past :data:`FINISHED_THRESHOLD`.
    Unknown filter keys raise instead of silently showing everything.
    """
    if filter_key not in FILTERS:
        raise ValueError(f"unknown library filter: {filter_key!r}")
    ordered = list(items)
    if filter_key == "all":
        return ordered
    if filter_key == "recent":
        allowed = set(recent_paths)
        return [item for item in ordered if item.path in allowed]
    allowed = set(finished_paths)
    return [item for item in ordered if item.path in allowed]


def sort_items(items: Iterable[LibraryItem], sort_key: str) -> list[LibraryItem]:
    """Sort a copy for the page-head menu.

    ``name`` is case-insensitive, ``added`` is newest first, ``duration`` is
    longest first; unprobed durations sort last in both directions so a pending
    metadata probe never shuffles the grid under the user's finger.
    """
    if sort_key not in SORTS:
        raise ValueError(f"unknown library sort: {sort_key!r}")
    ordered = list(items)
    if sort_key == "name":
        return sorted(ordered, key=lambda item: (item.path.name.lower(), item.path.name))
    if sort_key == "added":
        return sorted(ordered, key=lambda item: (-item.mtime, item.path.name.lower()))
    return sorted(
        ordered,
        key=lambda item: (
            not item.has_metadata,
            -item.duration_ns,
            item.path.name.lower(),
        ),
    )


def library_counts(
    items: Iterable[LibraryItem],
    *,
    recent_paths: Iterable[Path] = (),
    finished_paths: Iterable[Path] = (),
) -> dict[str, int]:
    """Chip counts for :data:`FILTERS`, in that order."""
    ordered = list(items)
    recent = set(recent_paths)
    finished = set(finished_paths)
    return {
        "all": len(ordered),
        "recent": sum(1 for item in ordered if item.path in recent),
        "finished": sum(1 for item in ordered if item.path in finished),
    }
