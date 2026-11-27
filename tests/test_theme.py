"""ui-v6 theme contract: scheme C palette, action/data split, no literal colours.

Scheme C in ``mockups/ui-v6/skin-c.css`` separates the two jobs ui-v5 had put
on amber: **bone** is the action colour (CTA, checked switch, current nav item,
play key) and **amber** is the data colour (progress, counters, live telemetry,
filled sliders).  The stylesheet moved out of ``window.py`` into
:mod:`video_player.ui.style` so these checks are pure Python; one test still
asks GTK to parse the result *and* collects the parser's warnings.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from video_player.ui import style

ROOT = Path(__file__).resolve().parents[1]
WINDOW = ROOT / "src" / "video_player" / "ui" / "window.py"
RAIL = ROOT / "src" / "video_player" / "ui" / "rail.py"
PAINT = ROOT / "src" / "video_player" / "ui" / "paint.py"
PLAYER_CHROME = ROOT / "src" / "video_player" / "ui" / "player_chrome.py"
I18N = ROOT / "src" / "video_player" / "i18n.py"

#: The semantic values frozen by ``mockups/ui-v6/skin-c.css``.  ``play`` is the
#: scheme C bone action colour; the token kept its ui-v5 name because the Cairo
#: overlays and the play key already read it.
SEMANTIC_TOKENS = {
    "bg": "#0B0D0F",
    "panel": "#111417",
    "line": "#20252A",
    "fg": "#ECEAE5",
    "muted": "#98A0A7",
    "dim": "#747C84",
    "accent": "#D9A85C",
    "annotation": "#7EA8C4",
    "play": "#F1EDE4",
}

#: Controls scheme C fills with bone.  Selector -> declaration that must prove
#: the action colour reached that control.
BONE_CONTROLS = (
    ("button.btn-primary", "background-color: @play;"),
    ("button.drop-primary", "background-color: @play;"),
    ("button.accent-button", "background-color: @play;"),
    ("button.play-primary", "background-color: @play;"),
    ("button.transport-primary", "background-color: @play;"),
    ("button.play-top-chip:checked", "background-color: @play;"),
    (".sidebar-panel switch:checked", "background-color: @play;"),
    ("button.rail-item:checked", "color: @fg;"),
)

#: Amber survives only as the data colour.  Selector -> declaration that must
#: prove the accent token reached that readout.
AMBER_DATA_CONTROLS = (
    ("scale highlight", "background-color: @accent;"),
    ("label.beauty-status-dot", "color: @accent;"),
    ("label.fchip-count", "color: @accent;"),
    ("label.fullscreen-telemetry-live", "color: @accent;"),
    ("label.info-live", "color: @accent;"),
    ("label.spec-value.is-accent", "color: @accent;"),
    ("label.telemetry-accent", "color: @accent;"),
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
def test_semantic_token_has_the_ui_v6_value(name: str, value: str) -> None:
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


@pytest.mark.parametrize(("selector", "expected"), BONE_CONTROLS)
def test_bone_reaches_every_action_control(selector: str, expected: str) -> None:
    declarations = _declarations(style.RULES)
    assert selector in declarations, f"missing selector: {selector}"
    assert expected in declarations[selector], f"{selector}: {declarations[selector]!r}"


@pytest.mark.parametrize(("selector", "expected"), AMBER_DATA_CONTROLS)
def test_amber_only_reaches_data_readouts(selector: str, expected: str) -> None:
    declarations = _declarations(style.RULES)
    assert selector in declarations, f"missing selector: {selector}"
    assert expected in declarations[selector], f"{selector}: {declarations[selector]!r}"


@pytest.mark.parametrize("selector", FOCUS_SELECTORS)
def test_focus_ring_uses_bone(selector: str) -> None:
    declarations = _declarations(style.RULES)
    assert "outline: 2px solid @play;" in declarations[selector]


def test_checked_switch_knob_is_ink() -> None:
    declarations = _declarations(style.RULES)
    assert (
        declarations[".sidebar-panel switch:checked slider"]
        == "background-color: @on_play;"
    )


@pytest.mark.parametrize(
    "selector", ("button.btn-primary", "button.drop-primary", "button.accent-button")
)
def test_primary_button_text_is_ink(selector: str) -> None:
    declarations = _declarations(style.RULES)
    assert "color: @on_play;" in declarations[selector]


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


def test_recording_backdrop_reads_the_video_token() -> None:
    source = WINDOW.read_text(encoding="utf-8")
    function = source[
        source.index("def _theme_background") : source.index("def _save_rgba_png")
    ]
    assert 'rgba("video")' in function
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
        (0xD9 / 255.0, 0xA8 / 255.0, 0x5C / 255.0, 0.5)
    )
    assert style.rgba("annotation") != style.rgba("accent")
    with pytest.raises(KeyError):
        style.rgba("not_a_token")


# -- ui-v6 signature moves --------------------------------------------------


def test_rail_mark_is_deleted_everywhere() -> None:
    """2026-11-27 decision: no 42x42 "面" plate at the top of the rail."""
    assert "rail-mark" not in style.RULES
    rail = RAIL.read_text(encoding="utf-8")
    assert "mark_box" not in rail
    assert "_mark" not in rail
    assert "rail_mark" not in rail
    assert "rail_mark" not in I18N.read_text(encoding="utf-8")
    # The nav keeps its paddings, so deleting the mark moves it up naturally.
    assert "padding: 14px 0 12px 0;" in style.RULES
    assert "nav.set_margin_top(24)" in rail


def test_rail_current_entry_is_a_bone_cursor_not_a_plate() -> None:
    rail = RAIL.read_text(encoding="utf-8")
    assert "class _ActiveBar" in rail
    assert 'rgba("play", 1.0)' in rail
    declarations = _declarations(style.RULES)
    assert "background-color: transparent;" in declarations["button.rail-item:checked"]


def test_poster_uses_scheme_c_radii_duration_and_progress() -> None:
    paint = PAINT.read_text(encoding="utf-8")
    assert "RADIUS = 8.0" in paint
    assert "BACKDROP_RADIUS = 10.0" in paint
    assert '"DejaVu Sans Mono 8"' in paint
    assert "bar_h = 2.0" in paint
    assert 'rgba("accent", 0.95)' in paint
    declarations = _declarations(style.RULES)
    assert "font-size: 13px;" in declarations["label.lib-title"]
    assert "font-size: 11px;" in declarations["label.lib-meta"]
    assert "font-family: monospace;" in declarations["label.lib-meta"]


def test_timeline_has_a_vertical_bar_knob() -> None:
    source = PLAYER_CHROME.read_text(encoding="utf-8")
    assert "_KNOB_WIDTH = 10.0" in source
    assert "_KNOB_HEIGHT = 15.0" in source
    assert "_RAIL_HEIGHT = 3.0" in source
    assert "rounded_path(" in source


def test_beauty_drawer_head_keeps_the_40px_underline() -> None:
    window = WINDOW.read_text(encoding="utf-8")
    chrome = PLAYER_CHROME.read_text(encoding="utf-8")
    assert "class DrawerTabRule" in chrome
    assert "DRAWER_RULE_UNDERLINE = 40" in chrome
    # The beauty page still uses the scheme C head rule.  The episodes head is
    # the 选集/美颜 tab switcher now, so it no longer needs its own marker.
    assert window.count("= DrawerTabRule()") == 1
    assert "self._beauty_tab_rule" in window
    assert "self._episodes_tab_rule" not in window
    assert "head.add_css_class(\"drawer-head\")" in window
    assert "self._panel_tab_episodes" in window
    assert "self._panel_tab_beauty" in window


def test_gtk_parses_the_stylesheet_without_warnings() -> None:
    gi = pytest.importorskip("gi")
    try:
        gi.require_version("Gtk", "4.0")
        from gi.repository import Gtk
    except (ImportError, ValueError) as exc:  # pragma: no cover - CI without GTK
        pytest.skip(f"GTK 4 typelib not available: {exc}")

    provider = Gtk.CssProvider()
    errors: list[str] = []
    if hasattr(provider, "connect"):
        provider.connect(
            "parsing-error",
            lambda _provider, _section, error: errors.append(str(error)),
        )
    try:
        provider.load_from_data(style.CSS)
    except Exception as exc:  # pragma: no cover - only on a broken stylesheet
        pytest.fail(f"GTK rejected the stylesheet: {exc}")
    assert not errors, "GTK CSS parsing errors: " + "; ".join(errors)
