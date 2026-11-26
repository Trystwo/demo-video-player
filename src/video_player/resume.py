"""Resume positions for local playback (F4).

``configs/player.example.json`` has advertised ``player.resume`` since M1, but
nothing implemented it: the flag was read, validated and then ignored, so every
launch restarted at 00:00.  This module is the missing half — a tiny persistent
store keyed by *file identity* rather than by file name.

Keying by identity matters: a demo folder is reused constantly, and a clip that
was re-encoded overnight usually keeps its name but changes size and mtime.
Resuming into a different cut of the same file looks like a corrupted seek, so
the entry stores size and mtime and is ignored when either has moved.

Design constraints:

* **No GTK / GStreamer import.**  The store must stay unit-testable on a
  development host without PyGObject, exactly like ``config`` and ``playlist``.
* **Never break playback.**  Every filesystem failure (read-only home, corrupt
  JSON, permissions) degrades to "no resume position" and never raises into the
  UI thread.
* **Never resurrect finished files.**  Positions in the first few seconds and
  positions inside the tail of the file are dropped instead of stored, so a
  finished clip does not reopen at 99 %.

Storage lives under ``$XDG_STATE_HOME/video-player-demo/resume.json``
(``~/.local/state`` when the variable is unset), matching the XDG split used by
``i18n`` (config) and ``diagnostics`` (cache).
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import time
from typing import Any, Callable

__all__ = [
    "DEFAULT_MIN_POSITION_NS",
    "DEFAULT_TAIL_GUARD_NS",
    "ResumeEntry",
    "ResumeStore",
    "default_state_path",
]

#: Do not remember a position before this point: opening a clip and closing it
#: after two seconds should not make the next launch jump into the intro.
DEFAULT_MIN_POSITION_NS = 10_000_000_000  # 10 s

#: Treat the last seconds as "watched": resuming 5 s before the end is worse
#: than starting over, and the playlist advance would fire immediately.
DEFAULT_TAIL_GUARD_NS = 15_000_000_000  # 15 s

#: Upper bound on stored entries.  Old entries are pruned by ``updated_at`` so
#: the file cannot grow without bound on a demo board that plays hundreds of
#: clips over its lifetime.
DEFAULT_MAX_ENTRIES = 200

_STATE_DIR_NAME = "video-player-demo"
_STATE_FILE_NAME = "resume.json"
_FORMAT_VERSION = 1


def default_state_path() -> Path:
    """Return the XDG state file used when no explicit path is given."""
    base = os.environ.get("XDG_STATE_HOME")
    root = Path(base) if base else Path.home() / ".local" / "state"
    return root / _STATE_DIR_NAME / _STATE_FILE_NAME


@dataclass(frozen=True)
class ResumeEntry:
    """One stored playback position."""

    position_ns: int
    duration_ns: int
    updated_at: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "position_ns": int(self.position_ns),
            "duration_ns": int(self.duration_ns),
            "updated_at": round(float(self.updated_at), 3),
        }

    @classmethod
    def from_dict(cls, raw: Any) -> "ResumeEntry | None":
        """Parse one entry, returning None for anything malformed.

        A hand-edited or half-written file must not crash the player, so every
        field is checked instead of trusted.
        """
        if not isinstance(raw, dict):
            return None
        try:
            position = int(raw["position_ns"])
            duration = int(raw.get("duration_ns", 0))
            updated = float(raw.get("updated_at", 0.0))
        except (KeyError, TypeError, ValueError):
            return None
        if position < 0 or duration < 0 or updated < 0:
            return None
        return cls(position_ns=position, duration_ns=duration, updated_at=updated)


class ResumeStore:
    """Persistent map of ``file identity -> last position``.

    Args:
        path: state file to use; defaults to :func:`default_state_path`.
        min_position_ns: positions shorter than this are never stored.
        tail_guard_ns: positions this close to the end are treated as finished.
        max_entries: keep at most this many entries (oldest pruned first).
        clock: injectable time source, for deterministic tests.
    """

    def __init__(
        self,
        path: Path | str | None = None,
        *,
        min_position_ns: int = DEFAULT_MIN_POSITION_NS,
        tail_guard_ns: int = DEFAULT_TAIL_GUARD_NS,
        max_entries: int = DEFAULT_MAX_ENTRIES,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self._path = Path(path) if path is not None else default_state_path()
        self._min_position_ns = max(0, int(min_position_ns))
        self._tail_guard_ns = max(0, int(tail_guard_ns))
        self._max_entries = max(1, int(max_entries))
        self._clock = clock or time.time
        # Loaded eagerly: the window reads the position right after opening a
        # file, and one small JSON read at startup is cheaper than a lazy check
        # on every play.  Failures leave an empty map.
        self._entries, self._dirty = self._read()

    # -- public API --------------------------------------------------------

    @property
    def path(self) -> Path:
        return self._path

    def load(self, media: Path | str) -> int | None:
        """Return the stored position for *media*, or None when there is none.

        The size/mtime comparison is what makes a re-encoded clip start over
        instead of seeking into a position that no longer means anything.
        """
        key = self._identify(media)
        if key is None:
            return None
        raw = self._entries.get(key["path"])
        if not isinstance(raw, dict):
            return None
        if raw.get("size") != key["size"] or raw.get("mtime_ns") != key["mtime_ns"]:
            return None
        entry = ResumeEntry.from_dict(raw)
        if entry is None:
            return None
        if not self._worth_resuming(entry.position_ns, entry.duration_ns):
            return None
        return entry.position_ns

    def save(self, media: Path | str, position_ns: int, duration_ns: int = 0) -> bool:
        """Record *position_ns* for *media*.

        Returns True when an entry was written (or cleared), False when the
        position was ignored or the file could not be written.
        """
        key = self._identify(media)
        if key is None:
            return False
        position = max(0, int(position_ns))
        duration = max(0, int(duration_ns))
        if not self._worth_resuming(position, duration):
            return self.clear(media)
        self._entries[key["path"]] = {
            "size": key["size"],
            "mtime_ns": key["mtime_ns"],
            "position_ns": position,
            "duration_ns": duration,
            "updated_at": round(float(self._clock()), 3),
        }
        self._prune()
        self._dirty = True
        return self._write()

    def clear(self, media: Path | str) -> bool:
        """Drop the entry for *media* (played to the end, or file replaced)."""
        key = self._identify(media)
        if key is None:
            return False
        existing = self._entries.get(key["path"])
        if existing is None:
            return False
        # Keep the entry when the file identity changed: that is a new clip
        # reusing the old name, and clearing is the caller's explicit intent
        # only for the file they are playing now.
        if (
            not isinstance(existing, dict)
            or existing.get("size") != key["size"]
            or existing.get("mtime_ns") != key["mtime_ns"]
        ):
            return False
        del self._entries[key["path"]]
        self._dirty = True
        return self._write()

    def forget(self, media: Path | str) -> None:
        """Drop the entry regardless of file identity, in memory and on disk."""
        key = self._identify(media)
        if key is None:
            return
        if self._entries.pop(key["path"], None) is not None:
            self._dirty = True
            self._write()

    def entries(self) -> dict[str, ResumeEntry]:
        """Return a copy of the valid entries (diagnostics and tests)."""
        result: dict[str, ResumeEntry] = {}
        for name, raw in self._entries.items():
            entry = ResumeEntry.from_dict(raw)
            if entry is not None:
                result[name] = entry
        return result

    # -- internals ---------------------------------------------------------

    def _worth_resuming(self, position_ns: int, duration_ns: int) -> bool:
        if position_ns < self._min_position_ns:
            return False
        if duration_ns > 0 and duration_ns - position_ns < self._tail_guard_ns:
            return False
        return True

    @staticmethod
    def _identify(media: Path | str) -> dict[str, Any] | None:
        """Return the stable key fields for *media*, or None when unusable."""
        try:
            path = Path(media).expanduser()
            stat = path.stat()
        except (OSError, RuntimeError):
            # Missing file (removed from the playlist), permission error, or a
            # path too long for the platform: simply no resume.
            return None
        return {
            "path": str(path.resolve()),
            "size": int(stat.st_size),
            "mtime_ns": int(stat.st_mtime_ns),
        }

    def _read(self) -> tuple[dict[str, Any], bool]:
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}, False
        if not isinstance(raw, dict):
            return {}, False
        files = raw.get("files")
        if not isinstance(files, dict):
            return {}, False
        # Keep only entries that still parse; a corrupt row is dropped silently
        # rather than failing the whole file.
        cleaned: dict[str, Any] = {}
        for name, entry in files.items():
            if not isinstance(name, str):
                continue
            if ResumeEntry.from_dict(entry) is None:
                continue
            cleaned[name] = entry
        return cleaned, False

    def _prune(self) -> None:
        if len(self._entries) <= self._max_entries:
            return
        ordered = sorted(
            self._entries.items(),
            key=lambda item: float(
                item[1].get("updated_at", 0.0) if isinstance(item[1], dict) else 0.0
            ),
            reverse=True,
        )
        self._entries = dict(ordered[: self._max_entries])

    def _write(self) -> bool:
        """Atomically persist the map; any failure is silent by design."""
        payload = {
            "version": _FORMAT_VERSION,
            "files": self._entries,
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
        self._dirty = False
        return True
