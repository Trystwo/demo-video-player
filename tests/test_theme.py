"""ui-v5 theme contract: palette tokens, no literal colours, no colour mixing.

The stylesheet moved out of ``window.py`` into :mod:`video_player.ui.style` so
these checks are pure Python; one test still asks GTK to parse the result.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from video_player.ui import style

WINDOW = (
    Path(__file__).resolve().parents[1]
    / "src"
    / "video_player"
    / "ui"
    / "window.py"
)

#: The eight values frozen by ``mockups/ui-v5/theme.css`` (README §3).
SEMANTIC_TOKENS = {
    "bg": "#0E1216",
    "panel": "#151A20",
    "line": "#232B33",
    "fg": "#E9E5DE",
    "muted": "#98A0A8",
    "dim": "#6C757E",
    "accent": "#E9B756",
    "annotation": "#7EA8C4",
}

#: Controls the ui-v5 brief forces onto amber.  Selector -> declaration that
#: must prove the accent token reached that control.
ACCENT_CONTROLS = (
    ("scale highlight", "background-color: @accent;"),
    ("scale slider", "border: 2px solid @accent;"),
    ("list.cinema-playlist row:selected", "border-left: 3px solid @accent;"),
    (
        ".sidebar-panel .sidebar-tabs button:checked",
        "background-color: alpha(@accent, 0.16);",
    ),
    (".decode-segments button:checked", "background-color: alpha(@accent, 0.18);"),
    (".sidebar-panel switch:checked", "background-color: @accent;"),
    ("label.beauty-status-dot", "color: @accent;"),
    ("label.info-dot", "color: @accent;"),
    ("button.drop-primary", "background-color: @accent;"),
    ("button.accent-button", "background-color: @accent;"),
    ("button.header-action:checked", "background-color: alpha(@accent, 0.18);"),
    ("button.transport-button:checked", "background-color: alpha(@accent, 0.18);"),
    ("button.dock-pill:checked", "background-color: alpha(@accent, 0.30);"),
    ("image.status-symbol", "color: @accent;"),
    ("spinner.status-spinner", "color: @accent;"),
)

FOCUS_SELECTORS = (
    "button:focus-visible",
    "scale:focus-visible",
    "dropdown:focus-visible",
)

NAMED_COLOURS = re.compile(
    r"\b(?:black|white|red|green|blue|gray|grey|silver|maroon|navy|teal|olive|"
    r"purple|fuchsia|aqua|lime|yellow)\b",
    re.IGNORECASE,
)


def _without_comments(css: str) -> str:
    return re.sub(r"/\*.*?\*/", "", css, flags=re.DOTALL)


def _declarations(css: str) -> dict[str, str]:
    """Flatten ``selector { declarations }`` blocks into a lookup table.

    A selector can appear more than once (base rule plus cascade overrides);
    later declarations win, exactly like the GTK cascade, so the table reflects
    the effective value instead of the first block that happens to match.
    """
    merged: dict[str, dict[str, str]] = {}
    for selector, block in re.findall(r"([^{}]+)\{([^{}]*)\}", _without_comments(css)):
        for part in selector.split(","):
            name = " ".join(part.split())
            if not name:
                continue
            properties = merged.setdefault(name, {})
            for declaration in block.split(";"):
                property_name, separator, value = declaration.partition(":")
                if not separator:
                    continue
                properties[property_name.strip()] = " ".join(value.split())
    return {
        selector: " ".join(f"{name}: {value};" for name, value in properties.items())
        for selector, properties in merged.items()
    }


def _source_slice(start: str, end: str) -> str:
    source = WINDOW.read_text(encoding="utf-8")
    begin = source.index(start)
    return source[begin : source.index(end, begin)]


@pytest.mark.parametrize(("name", "value"), SEMANTIC_TOKENS.items())
def test_semantic_token_has_the_ui_v5_value(name: str, value: str) -> None:
    assert style.TOKENS[name] == value
    assert f"@define-color {name} {value};" in style.TOKENS_CSS


def test_every_token_is_a_plain_hex_colour() -> None:
    for name, value in style.TOKENS.items():
        assert re.fullmatch(r"#[0-9A-F]{6}", value), f"{name} is not #RRGGBB: {value!r}"


def test_rules_contain_no_literal_colours() -> None:
    rules = _without_comments(style.RULES)
    assert not re.search(r"#[0-9A-Fa-f]{3,8}\b", rules), "literal hex colour in the rules"
    assert "rgb(" not in rules and "rgba(" not in rules, "literal rgb() colour in the rules"
    assert not NAMED_COLOURS.search(rules), "named CSS colour in the rules"


def test_every_token_reference_is_defined() -> None:
    references = set(
        re.findall(r"@([A-Za-z_][A-Za-z0-9_]*)", _without_comments(style.RULES))
    )
    assert references <= set(style.TOKENS), (
        "undefined token(s): " + ", ".join(sorted(references - set(style.TOKENS)))
    )


def test_annotation_is_not_used_by_widget_rules() -> None:
    references = set(
        re.findall(r"@([A-Za-z_][A-Za-z0-9_]*)", _without_comments(style.RULES))
    )
    assert "annotation" not in references, (
        "the steel-blue annotation colour belongs to the picture, not the widgets"
    )


@pytest.mark.parametrize(("selector", "expected"), ACCENT_CONTROLS)
def test_accent_reaches_every_listed_control(selector: str, expected: str) -> None:
    declarations = _declarations(style.RULES)
    assert selector in declarations, f"missing selector: {selector}"
    assert expected in declarations[selector], f"{selector}: {declarations[selector]!r}"


@pytest.mark.parametrize("selector", FOCUS_SELECTORS)
def test_focus_ring_uses_accent(selector: str) -> None:
    declarations = _declarations(style.RULES)
    assert "outline: 2px solid @accent;" in declarations[selector]


def test_checked_switch_knob_is_ink() -> None:
    declarations = _declarations(style.RULES)
    assert (
        declarations[".sidebar-panel switch:checked slider"]
        == "background-color: @accent_ink;"
    )


@pytest.mark.parametrize("selector", ("button.drop-primary", "button.accent-button"))
def test_primary_button_text_is_accent_ink(selector: str) -> None:
    declarations = _declarations(style.RULES)
    assert "color: @accent_ink;" in declarations[selector]


def test_face_overlay_uses_annotation_only() -> None:
    face = _source_slice("class _FaceOverlay", "class _CompareOverlay")
    assert 'rgba("annotation"' in face
    assert 'rgba("accent"' not in face


def test_split_captions_use_annotation_only() -> None:
    compare = _source_slice("class _CompareOverlay", "class _ProgressLine")
    assert 'rgba("annotation"' in compare
    assert 'rgba("accent"' not in compare


def test_progress_hairline_uses_accent_not_annotation() -> None:
    progress = _source_slice("class _ProgressLine", "def _format_time")
    assert 'rgba("accent"' in progress
    assert 'rgba("annotation"' not in progress


def test_recording_backdrop_reads_the_bg_token() -> None:
    source = WINDOW.read_text(encoding="utf-8")
    function = source[
        source.index("def _theme_background") : source.index("def _save_rgba_png")
    ]
    assert 'rgba("bg")' in function
    assert "0x0D" not in function
    assert "0x0E" not in function


def test_window_has_no_literal_colours_left() -> None:
    source = WINDOW.read_text(encoding="utf-8")
    assert not re.search(r"#[0-9A-Fa-f]{6}\b", source)
    # The old float triple for the annotation colour must not creep back in.
    assert "0.494, 0.659, 0.769" not in source


def test_cairo_helper_matches_the_token_table() -> None:
    red, green, blue, alpha = style.rgba("accent", 0.5)
    assert (red, green, blue, alpha) == pytest.approx(
        (0xE9 / 255.0, 0xB7 / 255.0, 0x56 / 255.0, 0.5)
    )
    assert style.rgba("annotation") != style.rgba("accent")
    with pytest.raises(KeyError):
        style.rgba("not_a_token")


def test_gtk_parses_the_stylesheet() -> None:
    gi = pytest.importorskip("gi")
    try:
        gi.require_version("Gtk", "4.0")
        from gi.repository import GLib, Gtk
    except (ImportError, ValueError) as exc:  # pragma: no cover - CI without GTK
        pytest.skip(f"GTK 4 typelib not available: {exc}")

    provider = Gtk.CssProvider()
    try:
        provider.load_from_data(style.CSS)
    except GLib.Error as exc:  # pragma: no cover - only on a broken stylesheet
        pytest.fail(f"GTK rejected the stylesheet: {exc}")
