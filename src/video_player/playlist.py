"""Local playlist handling (F4).

The player accepts zero or more positional arguments: files open as a playlist
in the given order, folders are expanded to their video files in name order.
The :class:`Playlist` cursor powers previous/next and automatic advance after
the end of a file.
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterable

from .pipeline import VIDEO_SUFFIXES

__all__ = ["VIDEO_SUFFIXES", "expand_media_args", "is_video", "Playlist"]


def is_video(path: str | Path) -> bool:
    """Return True when *path* has a supported video suffix."""
    return Path(path).suffix.lower() in VIDEO_SUFFIXES


def expand_media_args(items: Iterable[str | Path]) -> list[Path]:
    """Expand CLI arguments into an ordered, de-duplicated file list.

    Directories are scanned (non-recursively) for supported video files sorted
    by name; explicit file paths are kept in the order given, even when they do
    not exist yet (the window reports the missing file).

    Raises:
        ValueError: for an unsupported explicit file suffix (e.g. ``.ass``) so a
            typo fails loudly instead of silently opening nothing.
    """
    result: list[Path] = []
    seen: set[Path] = set()

    def add(path: Path) -> None:
        key = path.resolve() if path.exists() else path.absolute()
        if key in seen:
            return
        seen.add(key)
        result.append(path)

    for item in items:
        path = Path(item).expanduser()
        if path.is_dir():
            for child in sorted(path.iterdir(), key=lambda entry: entry.name.lower()):
                if child.is_file() and is_video(child):
                    add(child)
            continue
        if path.exists() and not is_video(path):
            raise ValueError(
                f"unsupported media file {path.name!r}; supported suffixes: "
                f"{', '.join(VIDEO_SUFFIXES)}"
            )
        add(path)
    return result


class Playlist:
    """Ordered list of local files with a moving cursor."""

    def __init__(self, items: Iterable[str | Path] = ()) -> None:
        self._items: list[Path] = [Path(item) for item in items]
        self._index = 0

    # -- sequence protocol -------------------------------------------------

    def __len__(self) -> int:
        return len(self._items)

    def __bool__(self) -> bool:
        return bool(self._items)

    def __iter__(self):
        return iter(self._items)

    # -- inspection --------------------------------------------------------

    @property
    def files(self) -> tuple[Path, ...]:
        return tuple(self._items)

    @property
    def index(self) -> int:
        return self._index

    @property
    def current(self) -> Path | None:
        if not self._items:
            return None
        return self._items[self._index]

    def has_next(self) -> bool:
        return self._index + 1 < len(self._items)

    def has_previous(self) -> bool:
        return self._index > 0

    def label(self, index: int) -> str:
        return self._items[index].name

    # -- mutation ----------------------------------------------------------

    def add(self, item: str | Path) -> int:
        """Append *item* unless present; return its index."""
        path = Path(item)
        for index, existing in enumerate(self._items):
            if existing == path:
                return index
        self._items.append(path)
        return len(self._items) - 1

    def select(self, index: int) -> Path:
        """Move the cursor to *index* and return the selected file.

        Raises:
            IndexError: when *index* is out of range.
        """
        if not 0 <= index < len(self._items):
            raise IndexError(f"playlist index {index} out of range 0..{len(self._items)}")
        self._index = index
        return self._items[index]

    def next(self) -> Path | None:
        """Advance the cursor; returns None at the end of the playlist."""
        if not self.has_next():
            return None
        return self.select(self._index + 1)

    def previous(self) -> Path | None:
        """Move the cursor back; returns None at the start of the playlist."""
        if not self.has_previous():
            return None
        return self.select(self._index - 1)
