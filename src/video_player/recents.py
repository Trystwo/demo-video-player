"""Recently played files (F4).

A playlist only knows what was passed on the command line, so on a demo board
every restart starts from an empty sidebar.  This module keeps a short,
most-recent-first history of what was actually opened, which the sidebar shows
above the current playlist.

Deliberately separate from :mod:`video_player.resume`:

* **resume** answers "where was I in this file" and *drops* entries once a clip
  is watched to the end — that is exactly right for seeking, and exactly wrong
  for a history list.
* **recents** answers "what did I open lately" and keeps finished clips, because
  the operator who just watched a demo through usually wants it again.

Design constraints match the rest of the player: no GTK/GStreamer import so it
stays unit-testable on a host without PyGObject, and every filesystem failure
degrades to an empty history instead of raising into the UI thread.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import time
from typing import Any, Callable, Iterable

__all__ = ["DEFAULT_MAX_RECENTS", "RecentItem", "RecentsStore", "default_state_path"]

#: The sidebar shows this many rows before scrolling; keeping the file small
#: also keeps launch parsing cheap on the board.
DEFAULT_MAX_RECENTS = 20

_STATE_DIR_NAME = "video-player-demo"
_STATE_FILE_NAME = "recent.json"
_FORMAT_VERSION = 1


def default_state_path() -> Path:
    """Return the XDG state file used when no explicit path is given."""
    base = os.environ.get("XDG_STATE_HOME")
    root = Path(base) if base else Path.home() / ".local" / "state"
    return root / _STATE_DIR_NAME / _STATE_FILE_NAME


@dataclass(frozen=True)
class RecentItem:
    """One history entry: which file, how long it is, and how far it got."""

    path: Path
    duration_ns: int = 0
    position_ns: int = 0
    updated_at: float = 0.0

    @property
    def name(self) -> str:
        return self.path.name

    @property
    def progress(self) -> float:
        """Watched fraction in ``0.0..1.0``; 0 when the duration is unknown."""
        if self.duration_ns <= 0:
            return 0.0
        return min(1.0, max(0.0, self.position_ns / self.duration_ns))

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": str(self.path),
            "duration_ns": int(self.duration_ns),
            "position_ns": int(self.position_ns),
            "updated_at": round(float(self.updated_at), 3),
        }

    @classmethod
    def from_dict(cls, raw: Any) -> "RecentItem | None":
        """Parse one entry; anything malformed yields None instead of raising."""
        if not isinstance(raw, dict):
            return None
        path = raw.get("path")
        if not isinstance(path, str) or not path.strip():
            return None
        try:
            duration = int(raw.get("duration_ns", 0))
            position = int(raw.get("position_ns", 0))
            updated = float(raw.get("updated_at", 0.0))
        except (TypeError, ValueError):
            return None
        if duration < 0 or position < 0 or updated < 0:
            return None
        return cls(
            path=Path(path),
            duration_ns=duration,
            position_ns=position,
            updated_at=updated,
        )


class RecentsStore:
    """Most-recent-first history of opened files, capped and persisted.

    Args:
        path: state file to use; defaults to :func:`default_state_path`.
        max_entries: keep at most this many entries (oldest dropped first).
        clock: injectable time source, for deterministic tests.
    """

    def __init__(
        self,
        path: Path | str | None = None,
        *,
        max_entries: int = DEFAULT_MAX_RECENTS,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self._path = Path(path) if path is not None else default_state_path()
        self._max_entries = max(1, int(max_entries))
        self._clock = clock or time.time
        self._items: list[RecentItem] = self._read()

    # -- public API --------------------------------------------------------

    @property
    def path(self) -> Path:
        return self._path

    def touch(
        self,
        media: Path | str,
        *,
        duration_ns: int = 0,
        position_ns: int = 0,
    ) -> bool:
        """Record *media* as the most recent file and persist the list.

        Called when a file starts playing and again when its position is saved,
        so the row shows how far the viewer got.  A duration of zero keeps the
        previously stored value: the caller may not know it yet on the first
        call right after opening.
        """
        try:
            path = Path(media).expanduser().resolve()
        except (OSError, RuntimeError):
            return False
        if not path.is_file():
            return False
        duration = max(0, int(duration_ns))
        position = max(0, int(position_ns))
        previous = next((item for item in self._items if item.path == path), None)
        if previous is not None:
            self._items.remove(previous)
            if duration == 0:
                duration = previous.duration_ns
            if position == 0:
                position = previous.position_ns
        self._items.insert(
            0,
            RecentItem(
                path=path,
                duration_ns=duration,
                position_ns=position,
                updated_at=float(self._clock()),
            ),
        )
        del self._items[self._max_entries :]
        return self._write()

    def items(self, *, only_existing: bool = False) -> tuple[RecentItem, ...]:
        """Return the history, newest first.

        ``only_existing=True`` hides entries whose file has vanished (unplugged
        drive, cleaned-up demo folder) for display, without discarding them —
        the file may come back, and silently rewriting the history on launch
        would be surprising.
        """
        if not only_existing:
            return tuple(self._items)
        return tuple(item for item in self._items if item.path.is_file())

    def remove(self, media: Path | str) -> bool:
        """Drop the entry for *media* and persist."""
        try:
            path = Path(media).expanduser().resolve()
        except (OSError, RuntimeError):
            return False
        kept = [item for item in self._items if item.path != path]
        if len(kept) == len(self._items):
            return False
        self._items = kept
        return self._write()

    def clear(self) -> bool:
        """Forget the whole history and persist."""
        if not self._items:
            return False
        self._items = []
        return self._write()

    # -- internals ---------------------------------------------------------

    def _read(self) -> list[RecentItem]:
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []
        files: Iterable[Any]
        if isinstance(raw, dict):
            files = raw.get("files") or ()
        elif isinstance(raw, list):  # tolerated hand-written file
            files = raw
        else:
            return []
        items: list[RecentItem] = []
        seen: set[Path] = set()
        for entry in files:
            item = RecentItem.from_dict(entry)
            if item is None or item.path in seen:
                continue
            seen.add(item.path)
            items.append(item)
        return items[: self._max_entries]

    def _write(self) -> bool:
        payload = {
            "version": _FORMAT_VERSION,
            "files": [item.to_dict() for item in self._items],
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
