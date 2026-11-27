"""Contracts for the ui-v5 browse shell and its home / detail pages.

The pages themselves need a display (the GTK snapshot probe covers them); these
tests lock the parts that a refactor can silently break:

* the shell's page list and the rail's entries,
* that the window shows the browse shell and the shell exposes the hooks the
  window calls (activate / mark_player_origin / suspend / headline / ...),
* that every translation key the new modules use exists in both languages,
* that the stylesheet carries the widget classes the new pages add, and that
  the new UI modules keep colours in the token table instead of literals.
"""

from __future__ import annotations

import ast
from pathlib import Path
import re

from video_player.i18n import TEXT

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src" / "video_player"
WINDOW_PY = SRC / "ui" / "window.py"
NEW_UI_MODULES = (
    SRC / "ui" / "browse.py",
    SRC / "ui" / "home_page.py",
    SRC / "ui" / "detail_page.py",
    SRC / "ui" / "rail.py",
    SRC / "ui" / "paint.py",
    SRC / "ui" / "recent_page.py",
    SRC / "ui" / "settings_page.py",
)


def _class(name: str, path: Path) -> ast.ClassDef:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == name:
            return node
    raise AssertionError(f"{name} not found in {path.name}")


def _methods(node: ast.ClassDef) -> set[str]:
    return {
        child.name for child in node.body if isinstance(child, ast.FunctionDef)
    }


def _module_constant(path: Path, name: str) -> object:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in tree.body:
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        target = node.targets[0]
        if isinstance(target, ast.Name) and target.id == name:
            return ast.literal_eval(node.value)
    raise AssertionError(f"{name} not found in {path.name}")


# -- shell contracts --------------------------------------------------------


def test_browse_pages_and_rail_entries() -> None:
    assert _module_constant(SRC / "ui" / "browse.py", "BROWSE_PAGES") == (
        "home",
        "library",
        "recent",
        "detail",
        "settings",
    )
    assert _module_constant(SRC / "ui" / "rail.py", "RAIL_ITEMS") == (
        "home",
        "library",
        "recent",
        "settings",
    )
    # The backdrop is generated at the page width, not upscaled from a card.
    assert _module_constant(SRC / "ui" / "browse.py", "BACKDROP_WIDTH") == 1140


def test_browse_shell_exposes_the_window_hooks() -> None:
    shell = _class("BrowseShell", SRC / "ui" / "browse.py")
    required = {
        "activate",
        "mark_player_origin",
        "show",
        "show_detail",
        "back_from_detail",
        "refresh",
        "headline",
        "is_detail",
        "suspend",
        "shutdown",
        "apply_language",
    }
    assert required <= _methods(shell)


def test_recent_page_surface() -> None:
    recent = _class("RecentPage", SRC / "ui" / "recent_page.py")
    assert {"refresh", "apply_language", "on_poster", "on_metadata", "summary"} <= _methods(
        recent
    )


def test_home_page_and_detail_page_surface() -> None:
    home = _class("HomePage", SRC / "ui" / "home_page.py")
    assert {"refresh", "apply_language", "on_poster", "on_metadata", "hero_item"} <= _methods(
        home
    )
    detail = _class("DetailPage", SRC / "ui" / "detail_page.py")
    assert {"show", "refresh", "apply_language", "on_poster", "on_metadata", "view"} <= _methods(
        detail
    )


def test_backdrop_cache_is_off_the_main_thread() -> None:
    # The big hero frame must not be decoded on the GTK thread; the cache wraps
    # the same ThumbnailWorker the library grid uses.
    text = (SRC / "ui" / "browse.py").read_text(encoding="utf-8")
    assert "ThumbnailWorker(" in text
    assert "ThumbnailWorker(self._on_ready, width=BACKDROP_WIDTH" in text
    assert "BACKDROP_WIDTH = 1140" in text
    # One decode at a time: a queued request only starts when the worker idles.
    assert "if self._worker.running:" in text


def test_window_shell_wiring() -> None:
    text = WINDOW_PY.read_text(encoding="utf-8")
    assert "from .browse import BrowseShell" in text
    for call in (
        "self._browse.activate()",
        "self._browse.mark_player_origin()",
        "self._browse.suspend()",
        "self._browse.shutdown()",
        "self._browse.headline()",
        "self._browse.apply_language(self._t)",
        "self._browse.back_from_detail()",
    ):
        assert call in text, call


# -- translations -----------------------------------------------------------

#: Regex for the translator calls the page modules use: ``self._t("key")``,
#: ``t("key")`` and ``translator("key")``.
_TRANSLATOR_CALL = re.compile(
    r"""\b(?:_t|t|translator)\(\s*"([a-z][a-z0-9_]*)\""""
)


def test_every_page_translation_key_exists() -> None:
    used: set[str] = set()
    for path in NEW_UI_MODULES:
        used |= set(_TRANSLATOR_CALL.findall(path.read_text(encoding="utf-8")))
    assert used, "no translation keys found"
    missing = sorted(key for key in used if key not in TEXT)
    assert not missing, f"missing translations: {missing}"


def test_rail_nav_keys_exist_for_every_entry() -> None:
    for kind in ("home", "library", "recent", "settings"):
        assert f"nav_{kind}" in TEXT


def test_new_home_and_detail_copy_is_present_in_both_languages() -> None:
    keys = (
        "home_continue",
        "home_added",
        "home_resume",
        "home_remaining",
        "home_empty_title",
        "detail_back",
        "detail_resume",
        "detail_play",
        "detail_episodes",
        "detail_auto_next",
        "detail_folder",
        "detail_spec_title",
        "spec_codec",
        "spec_picture",
        "spec_fps",
        "spec_duration",
        "spec_size",
        "spec_bitrate",
        "spec_decode",
        "spec_decode_hw",
        "spec_decode_sw",
    )
    for key in (
        "recent_title",
        "recent_headline",
        "recent_group_continue",
        "recent_group_finished",
        "recent_clear",
        "recent_clear_title",
        "recent_clear_body",
        "recent_clear_confirm",
        "recent_cancel",
        "recent_empty_title",
        "recent_empty_body",
        "recent_open_library",
        "recent_time_just",
        "recent_time_today",
        "recent_time_yesterday",
        "recent_time_date",
        "recent_time_date_year",
    ):
        chinese, english = TEXT[key]
        assert chinese and english, key
        assert chinese != english, key
    for key in keys:
        chinese, english = TEXT[key]
        assert chinese and english, key
        assert chinese != english, key


# -- stylesheet -------------------------------------------------------------


def test_stylesheet_covers_the_new_pages() -> None:
    from video_player.ui import style

    rules = style.RULES
    for selector in (
        ".home-page",
        "label.hero-title",
        "label.hero-kicker",
        "button.btn-primary",
        "button.btn-ghost",
        "button.row-more",
        ".detail-page",
        "button.detail-back",
        "label.bd-title",
        "label.bd-chip",
        "button.ep-card",
        "label.spec-key",
        "label.spec-value",
        "button.rel-row",
        "label.detail-foot",
        ".recent-page",
        "label.recent-group-title",
        "box.recent-rule",
        "button.recent-clear:hover",
    ):
        assert selector + " {" in rules, selector


def test_new_ui_modules_keep_colours_in_tokens() -> None:
    hex_colour = re.compile(r"#[0-9A-Fa-f]{3,8}\b")
    for path in NEW_UI_MODULES:
        source = path.read_text(encoding="utf-8")
        assert not hex_colour.search(source), f"literal colour in {path.name}"


def test_browse_shell_marker_class_is_styled() -> None:
    from video_player.ui import style

    # The drop-target highlight lives on the shell, not the library page.
    assert ".browse-shell.drag-active" in style.RULES
