"""The ui-v5 settings page: widget structure as source, plus toggle behaviour.

Like the other ui-v5 page tests this runs without a display: structure is read
from the module source and the two radio-style toggles are lifted out of
:class:`SettingsPage` and driven through stand-ins.  The pure rows live in
``tests/test_settings_model.py``.
"""

from __future__ import annotations

import ast
from pathlib import Path
import re
import textwrap
import types

from video_player.i18n import LANGUAGES, TEXT
from video_player.ui import style

SRC = Path(__file__).resolve().parents[1] / "src"
SETTINGS_PAGE = SRC / "video_player" / "ui" / "settings_page.py"
BROWSE = SRC / "video_player" / "ui" / "browse.py"
RAIL = SRC / "video_player" / "ui" / "rail.py"
WINDOW = SRC / "video_player" / "ui" / "window.py"

TOGGLE_METHODS = (
    "_on_language_toggled",
    "_sync_language",
    "_on_decode_toggled",
    "_sync_decode",
)


class FakeButton:
    def __init__(self, label: str = "", active: bool = False) -> None:
        self.label = label
        self.active = bool(active)
        self.enabled = True

    def get_active(self) -> bool:
        return self.active

    def set_active(self, active: bool) -> None:
        self.active = bool(active)

    def set_label(self, label: str) -> None:
        self.label = label

    def set_sensitive(self, sensitive: bool) -> None:
        self.enabled = bool(sensitive)


class FakeTranslator:
    def __init__(self, language: str = "zh") -> None:
        self.language = language

    def __call__(self, key: str, **values: object) -> str:
        chinese, english = TEXT[key]
        template = chinese if self.language == "zh" else english
        return template.format(**values)


class FakeConfig:
    def __init__(self, decode_mode: str = "auto") -> None:
        self.player = types.SimpleNamespace(decode_mode=decode_mode)


def _class_source(path: Path, name: str) -> tuple[str, ast.ClassDef]:
    text = path.read_text(encoding="utf-8")
    tree = ast.parse(text)
    node = next(
        item
        for item in tree.body
        if isinstance(item, ast.ClassDef) and item.name == name
    )
    return text, node


def _load_settings_methods() -> dict[str, str]:
    text, node = _class_source(SETTINGS_PAGE, "SettingsPage")
    found = {
        item.name: ast.get_source_segment(text, item)
        for item in node.body
        if isinstance(item, ast.FunctionDef) and item.name in TOGGLE_METHODS
    }
    assert set(found) == set(TOGGLE_METHODS), sorted(found)
    return found


def _make_page(decode_mode: str = "auto") -> tuple[types.SimpleNamespace, list, list]:
    language_calls: list[str] = []
    decode_calls: list[str] = []
    page = types.SimpleNamespace(
        _updating_language=False,
        _updating_decode=False,
        _t=FakeTranslator("zh"),
        _config=FakeConfig(decode_mode),
        _language_buttons={
            "zh": FakeButton("中文", active=True),
            "en": FakeButton("英文"),
        },
        _decode_buttons={
            "auto": FakeButton("自动", active=decode_mode == "auto"),
            "hw": FakeButton("硬解", active=decode_mode == "hw"),
            "sw": FakeButton("软解", active=decode_mode == "sw"),
        },
    )

    def on_language(language: str) -> None:
        language_calls.append(language)
        page._t.language = language

    def on_decode(mode: str) -> None:
        decode_calls.append(mode)
        page._config.player.decode_mode = mode

    page._on_language = on_language
    page._on_decode = on_decode
    namespace: dict[str, object] = {}
    code = "from __future__ import annotations\n\n" + "\n\n".join(
        textwrap.dedent(source) for source in _load_settings_methods().values()
    )
    exec(compile(code, "<settings-page>", "exec"), namespace)  # noqa: S102 - test only
    for name in TOGGLE_METHODS:
        setattr(page, name, types.MethodType(namespace[name], page))
    return page, language_calls, decode_calls


# -- structure -------------------------------------------------------------


def test_settings_page_class_keeps_the_public_api() -> None:
    _, node = _class_source(SETTINGS_PAGE, "SettingsPage")
    methods = {item.name for item in node.body if isinstance(item, ast.FunctionDef)}
    assert {"refresh", "apply_language", "sync_decode_mode"} <= methods


def test_language_toggle_switches_once_and_mirrors_the_state() -> None:
    page, language_calls, _ = _make_page()
    button = page._language_buttons["en"]
    # A radio click activates the button before "toggled" is emitted.
    button.set_active(True)

    page._on_language_toggled(button, "en")

    assert language_calls == ["en"]
    assert page._language_buttons["en"].get_active() is True
    # ``_sync_language`` mirrors the translator, not the clicked button.
    assert page._language_buttons["zh"].get_active() is False


def test_language_toggle_ignores_the_deactivated_half_of_the_group() -> None:
    page, language_calls, _ = _make_page()

    page._on_language_toggled(page._language_buttons["zh"], "zh")

    assert language_calls == []


def test_language_toggle_does_nothing_when_the_language_is_unchanged() -> None:
    page, language_calls, _ = _make_page()
    page._t.language = "en"

    page._on_language_toggled(page._language_buttons["en"], "en")

    assert language_calls == []


def test_decode_toggle_calls_the_window_and_resyncs() -> None:
    page, _, decode_calls = _make_page("auto")
    button = page._decode_buttons["hw"]
    button.set_active(True)

    page._on_decode_toggled(button, "hw")

    assert decode_calls == ["hw"]
    assert page._decode_buttons["hw"].get_active() is True
    assert page._decode_buttons["auto"].get_active() is False


def test_decode_toggle_ignores_the_same_mode_and_updates_it() -> None:
    page, _, decode_calls = _make_page("auto")

    page._on_decode_toggled(page._decode_buttons["auto"], "auto")
    button = page._decode_buttons["sw"]
    button.set_active(True)
    page._on_decode_toggled(button, "sw")

    assert decode_calls == ["sw"]


def test_sync_decode_mirrors_the_config_without_emitting() -> None:
    page, _, decode_calls = _make_page("auto")
    page._config.player.decode_mode = "sw"

    page._sync_decode()

    assert page._decode_buttons["sw"].get_active() is True
    assert decode_calls == []


# -- wiring ----------------------------------------------------------------


def test_browse_shell_routes_the_rail_settings_entry() -> None:
    source = BROWSE.read_text(encoding="utf-8")
    assert 'BROWSE_PAGES = ("home", "library", "recent", "detail", "settings")' in source
    assert 'self._stack.add_named(self.settings, "settings")' in source
    assert 'elif kind == "settings":' in source
    assert 'self.rail.set_active(self._page)' in source
    assert 'return self._t("nav_settings"), self._t("settings_headline")' in source
    assert "self.settings.apply_language(translator)" in source
    assert "self.settings.refresh()" in source


def test_rail_no_longer_greys_out_settings() -> None:
    source = RAIL.read_text(encoding="utf-8")
    assert "set_sensitive(False)" not in source
    assert 'button.connect("toggled", self._on_toggled, kind)' in source
    assert 'self._buttons["settings"].set_tooltip_text(t("settings_tooltip"))' in source


def test_interface_language_lives_only_in_settings() -> None:
    source = WINDOW.read_text(encoding="utf-8")
    # 2026-11-27: the headerbar switch was removed; the settings page owns the
    # choice and the player keeps its own entry in the "more" menu (the header
    # is hidden while playing).
    assert "_header_language_button" not in source
    assert "header.pack_end(" not in source
    assert 'self._t("switch_language")' in source


def test_window_owns_language_and_decode_updates() -> None:
    source = WINDOW.read_text(encoding="utf-8")
    assert "on_language=self._set_language" in source
    assert "on_decode=self._set_decode_mode" in source
    assert "def _set_language(self, language: str) -> None:" in source
    assert "def _set_decode_mode(self, mode: str) -> None:" in source
    assert "save_language(language)" in source
    assert "if mode not in DECODE_MODES" in source
    # Escape leaves the settings page the same way the rail's home entry does.
    assert "self._browse.go_home()" in source


def test_settings_page_keys_exist_in_both_languages() -> None:
    used = {
        key
        for key in (
            "settings_tooltip",
            "settings_headline",
            "settings_language_title",
            "settings_language_hint",
            "settings_lang_zh",
            "settings_lang_en",
            "settings_decode_hint",
            "settings_folders_title",
            "settings_folders_hint",
            "settings_folder_config",
            "settings_folder_missing",
            "settings_about_title",
            "settings_about_hint",
            "settings_about_version",
            "settings_about_platform",
            "settings_about_kernel",
            "settings_about_gtk",
            "settings_about_gstreamer",
            "settings_about_decode",
            "settings_about_decode_hw",
            "settings_about_decode_sw",
            "settings_about_state",
            "settings_about_cache",
        )
    }
    missing = sorted(key for key in used if key not in TEXT)
    assert not missing, f"missing translations: {missing}"
    for key in used:
        chinese, english = TEXT[key]
        assert chinese and english and chinese != english, key
    assert LANGUAGES == ("zh", "en")


def test_stylesheet_covers_the_settings_page() -> None:
    css = style.CSS if isinstance(style.CSS, str) else style.CSS.decode("utf-8")
    for selector in (
        ".settings-page",
        "button.settings-seg-button",
        "button.settings-seg-button:checked",
        "label.settings-dir-path",
        "label.settings-badge",
    ):
        assert selector in css, selector


def test_settings_page_module_uses_only_known_translator_keys() -> None:
    text = SETTINGS_PAGE.read_text(encoding="utf-8")
    used = set(
        re.findall(r'\b(?:_t|t)\(\s*(?:f?)"([a-z][a-z0-9_]*)"', text)
    )
    # ``f"settings_lang_{lang}"`` style keys are built at runtime and covered
    # by the explicit list above; the static ones must all exist.
    missing = sorted(
        key
        for key in used
        if key not in TEXT and not key.startswith("settings_lang")
        and not key.startswith("decode_")
    )
    assert not missing, missing
