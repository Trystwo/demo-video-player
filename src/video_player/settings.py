"""Settings page model: scan folders and about facts, without GTK.

The ui-v5 mockups never drew a settings page (``mockups/ui-v5/README.md`` chose
to grey out the rail entry instead), so the first version is deliberately small:
interface language, decode mode, scan folders and a read-only about card.
Everything that can be decided without a display lives here, which keeps
``ui/settings_page.py`` a widget builder and the rules unit-testable under the
``.venv`` interpreter too (no ``gi`` import in this module).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

__all__ = [
    "AboutRow",
    "FolderRow",
    "LANGUAGE_OPTIONS",
    "about_rows",
    "folder_rows",
]

#: Interface languages the settings page offers, in display order.
LANGUAGE_OPTIONS = ("zh", "en")


@dataclass(frozen=True)
class FolderRow:
    """One scanned folder as the settings page needs it.

    Attributes:
        path: Resolved folder.
        from_config: True when the folder comes from ``configs/*.json``; those
            cannot be removed from the page (they belong to the deployment).
        exists: False when the folder disappeared since the config was written.
        removable: True only for folders the user picked (stored in XDG state).
    """

    path: Path
    from_config: bool
    exists: bool
    removable: bool


@dataclass(frozen=True)
class AboutRow:
    """One ``label: value`` line of the about card."""

    key: str
    value: str


def _resolved(raw: str | Path) -> Path | None:
    """Best-effort absolute path; unusable entries are dropped, not raised."""
    text = str(raw).strip()
    if not text:
        # ``Path("")`` would silently mean the current directory.
        return None
    try:
        return Path(text).expanduser().resolve()
    except (OSError, RuntimeError):
        return None


def folder_rows(
    config_dirs: Sequence[str | Path],
    store_dirs: Sequence[str | Path],
) -> list[FolderRow]:
    """Merge configured and user-picked folders for the settings list.

    Config folders come first (they are the deployment's choice), then the
    folders picked in the UI.  Duplicates are dropped by resolved path so a
    folder that is both configured and picked appears once, as config.
    """
    rows: list[FolderRow] = []
    seen: set[str] = set()
    for raw in config_dirs:
        path = _resolved(raw)
        if path is None or str(path) in seen:
            continue
        seen.add(str(path))
        rows.append(FolderRow(path, from_config=True, exists=path.is_dir(), removable=False))
    for raw in store_dirs:
        path = _resolved(raw)
        if path is None or str(path) in seen:
            continue
        seen.add(str(path))
        rows.append(FolderRow(path, from_config=False, exists=path.is_dir(), removable=True))
    return rows


def about_rows(
    *,
    version: str,
    platform_name: str,
    machine: str,
    kernel: str,
    gtk: str,
    gstreamer: str,
    decode_text: str,
    state_path: str | Path,
    cache_path: str | Path,
) -> list[AboutRow]:
    """Facts shown in the about card, in a stable order.

    ``decode_text`` is already translated by the page: this module does not own
    language decisions, only the label keys.
    """
    platform_text = " ".join(part for part in (platform_name, machine) if part)
    return [
        AboutRow("settings_about_version", version),
        AboutRow("settings_about_platform", platform_text),
        AboutRow("settings_about_kernel", kernel),
        AboutRow("settings_about_gtk", gtk),
        AboutRow("settings_about_gstreamer", gstreamer),
        AboutRow("settings_about_decode", decode_text),
        AboutRow("settings_about_state", str(state_path)),
        AboutRow("settings_about_cache", str(cache_path)),
    ]
