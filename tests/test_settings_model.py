"""The settings page model (folders and about facts) without GTK.

The page itself is built with GTK; the rules that decide *what* it shows live
in ``video_player.settings`` so they run under the plain system interpreter and
under ``.venv`` (no ``gi`` import), matching how ``browse.py`` keeps its data
model separate from ``ui/browse.py``.
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from video_player.i18n import LANGUAGES
from video_player.settings import (
    LANGUAGE_OPTIONS,
    AboutRow,
    FolderRow,
    about_rows,
    folder_rows,
)


def test_language_options_match_the_translator() -> None:
    assert LANGUAGE_OPTIONS == LANGUAGES == ("zh", "en")


def test_folder_rows_put_config_first_and_keep_the_flags(tmp_path: Path) -> None:
    configured = tmp_path / "configured"
    picked = tmp_path / "picked"
    configured.mkdir()
    picked.mkdir()
    missing = tmp_path / "gone"

    rows = folder_rows(
        [str(configured), str(missing)],
        [str(picked)],
    )

    assert [row.path for row in rows] == [
        configured.resolve(),
        missing.resolve(),
        picked.resolve(),
    ]
    assert [row.from_config for row in rows] == [True, True, False]
    assert [row.exists for row in rows] == [True, False, True]
    assert [row.removable for row in rows] == [False, False, True]


def test_folder_rows_dedupe_a_folder_that_is_both_configured_and_picked(
    tmp_path: Path,
) -> None:
    folder = tmp_path / "shared"
    folder.mkdir()

    other = tmp_path / "store-only"
    other.mkdir()
    rows = folder_rows([str(folder)], [str(folder), str(other)])

    assert len(rows) == 2  # the shared folder once, plus the store-only one
    assert rows[0].path == folder.resolve()
    assert rows[0].from_config is True
    assert rows[0].removable is False
    assert rows[1].path == other.resolve()
    assert rows[1].from_config is False
    assert rows[1].removable is True


def test_folder_rows_drop_unusable_entries() -> None:
    rows = folder_rows(["", "   ", "~no-such-user-please"], [])
    assert rows == []


def test_about_rows_keep_a_stable_order_and_values() -> None:
    rows = about_rows(
        version="9.9.9",
        platform_name="Linux",
        machine="aarch64",
        kernel="6.1.118-rt36",
        gtk="4.18.6",
        gstreamer="GStreamer 1.26.2",
        decode_text="VPU 硬解可用",
        state_path="/home/tris/.local/state/x.json",
        cache_path="/home/tris/.cache/x",
    )

    assert isinstance(rows[0], AboutRow)
    assert [row.key for row in rows] == [
        "settings_about_version",
        "settings_about_platform",
        "settings_about_kernel",
        "settings_about_gtk",
        "settings_about_gstreamer",
        "settings_about_decode",
        "settings_about_state",
        "settings_about_cache",
    ]
    assert rows[0].value == "9.9.9"
    assert rows[1].value == "Linux aarch64"
    assert rows[5].value == "VPU 硬解可用"
    assert rows[6].value == "/home/tris/.local/state/x.json"


def test_about_rows_omit_a_blank_platform_machine() -> None:
    rows = about_rows(
        version="1",
        platform_name="Linux",
        machine="",
        kernel="k",
        gtk="g",
        gstreamer="s",
        decode_text="d",
        state_path="s",
        cache_path="c",
    )
    assert rows[1].value == "Linux"


def test_folder_row_is_hashable_and_frozen(tmp_path: Path) -> None:
    row = FolderRow(tmp_path, from_config=True, exists=True, removable=False)
    assert isinstance(row, FolderRow)
    assert {row, row} == {row}
    with pytest.raises(FrozenInstanceError):
        row.exists = False  # type: ignore[misc]


def test_module_has_no_gtk_import() -> None:
    """The model must stay importable where ``gi`` is missing (``.venv``)."""
    source = (
        Path(__file__).resolve().parents[1] / "src" / "video_player" / "settings.py"
    ).read_text(encoding="utf-8")
    assert "import gi" not in source
    assert "gi.require_version" not in source
    assert "Gtk" not in source


