"""Runtime diagnostics: persistent crash stacks and UI action breadcrumbs.

The board launches the player from the desktop, so stderr is not visible to
the person using it.  When GTK, GStreamer or the RKNN runtime crashes in
native code, the Python traceback is lost and the window just disappears.
This module keeps two small append-only files under the user's cache
directory:

``crash.log``
    Enabled with :func:`faulthandler`, so SIGSEGV/SIGBUS/SIGABRT and Python
    tracebacks are written to disk even when the process is started from the
    desktop.

``actions.log``
    One line per user action (open, feature toggle, language switch, ...).
    The last line before a crash identifies what the user was doing.

Both files are best-effort: a read-only or full home directory must never
prevent the player from opening.
"""

from __future__ import annotations

import faulthandler
import os
import sys
import time
import traceback
from pathlib import Path

_CRASH_FILE = None


def cache_dir() -> Path:
    """Directory for diagnostics, honouring ``XDG_CACHE_HOME``."""
    base = os.environ.get("XDG_CACHE_HOME")
    root = Path(base) if base else Path.home() / ".cache"
    return root / "video-player-demo"


def crash_log_path() -> Path:
    return cache_dir() / "crash.log"


def action_log_path() -> Path:
    return cache_dir() / "actions.log"


def _timestamp() -> str:
    now = time.time()
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now)) + (
        f".{int(now * 1000) % 1000:03d}"
    )


def install_crash_logging() -> None:
    """Enable :mod:`faulthandler` and an uncaught-exception hook.

    Idempotent: repeated calls reuse the open file so a crash log is not
    truncated when the GTK application is constructed after the CLI.
    """
    global _CRASH_FILE
    if _CRASH_FILE is not None:
        return
    try:
        cache_dir().mkdir(parents=True, exist_ok=True)
        _CRASH_FILE = open(  # noqa: SIM115 - lifetime is process lifetime
            crash_log_path(), "a", encoding="utf-8", buffering=1
        )
    except OSError:
        _CRASH_FILE = None
        return

    _CRASH_FILE.write(
        f"\n=== video_player {os.getpid()} {_timestamp()} "
        f"argv={sys.argv[1:]} ===\n"
    )
    _CRASH_FILE.flush()
    faulthandler.enable(file=_CRASH_FILE, all_threads=True)

    def _exception_hook(
        exc_type: type[BaseException],
        exc_value: BaseException,
        exc_traceback: object,
    ) -> None:
        try:
            if _CRASH_FILE is not None:
                _CRASH_FILE.write(f"\n=== uncaught {_timestamp()} ===\n")
                traceback.print_exception(
                    exc_type, exc_value, exc_traceback, file=_CRASH_FILE
                )
                _CRASH_FILE.flush()
        finally:
            sys.__excepthook__(exc_type, exc_value, exc_traceback)

    sys.excepthook = _exception_hook


def log_action(action: str, **details: object) -> None:
    """Append one user-action line; never raises on I/O errors."""
    parts = [_timestamp(), f"pid={os.getpid()}", action]
    for key, value in details.items():
        text = str(value).replace("\n", "\\n")
        if len(text) > 160:
            text = text[:157] + "..."
        parts.append(f"{key}={text}")
    line = " ".join(parts) + "\n"
    try:
        cache_dir().mkdir(parents=True, exist_ok=True)
        with open(action_log_path(), "a", encoding="utf-8") as handle:
            handle.write(line)
    except OSError:
        pass
