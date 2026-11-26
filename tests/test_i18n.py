"""Tests for the runtime Chinese/English UI strings."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from video_player.i18n import (
    DEFAULT_LANGUAGE,
    LANGUAGES,
    TEXT,
    Translator,
    load_language,
    save_language,
    settings_path,
)


@pytest.fixture(autouse=True)
def isolated_config(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    return tmp_path / "video-player-demo" / "ui.json"


def test_language_catalog_has_both_columns_and_no_empty_strings() -> None:
    assert LANGUAGES == ("zh", "en")
    assert DEFAULT_LANGUAGE == "zh"
    for key, (chinese, english) in TEXT.items():
        assert chinese, key
        assert english, key
        assert chinese != english, key


def test_translator_defaults_to_chinese() -> None:
    translator = Translator()
    assert translator.language == "zh"
    assert translator("open") == "打开"


def test_translator_switches_and_persists(isolated_config: Path) -> None:
    translator = Translator("en")
    assert translator("open") == "Open"
    assert translator("switch_language") == "中文"
    translator.toggle()
    save_language(translator.language)
    assert translator("open") == "打开"
    assert translator("switch_language") == "English"
    assert json.loads(isolated_config.read_text(encoding="utf-8")) == {"language": "zh"}
    assert settings_path() == isolated_config


def test_save_language_round_trip(isolated_config: Path) -> None:
    save_language("en")
    assert load_language() == "en"
    assert json.loads(isolated_config.read_text(encoding="utf-8")) == {"language": "en"}
    save_language("zh")
    assert load_language() == "zh"


def test_invalid_language_is_rejected() -> None:
    with pytest.raises(ValueError):
        Translator("fr")
    with pytest.raises(ValueError):
        save_language("fr")


def test_corrupt_or_unknown_preference_is_ignored(isolated_config: Path) -> None:
    isolated_config.parent.mkdir(parents=True)
    isolated_config.write_text("{not json", encoding="utf-8")
    assert load_language() is None
    isolated_config.write_text(json.dumps({"language": "fr"}), encoding="utf-8")
    assert load_language() is None
    isolated_config.write_text(json.dumps(["zh"]), encoding="utf-8")
    assert load_language() is None


def test_dynamic_strings_are_formatted() -> None:
    translator = Translator("en")
    assert translator("npu_face", count=1, ms=1.25) == "1 face 1.2 ms"
    assert translator("npu_faces", count=2, ms=1.25) == "2 faces 1.2 ms"
    assert translator("beauty_status_on", ms=1.5) == "On · GPU 1.5 ms/frame"
    assert translator("compare_needs_beauty") == "Compare · needs beauty"
    assert translator("info_face_on", count=1, ms=22.4) == "Face 1 · 22 ms"
    assert (
        translator("compare_params", smoothing=0.35, whitening=0.2)
        == "Smoothing 0.35 · whitening 0.20"
    )


def test_beauty_page_cascade_strings_exist_in_both_languages() -> None:
    chinese = Translator("zh")
    english = Translator("en")
    # The page-off notice and the footer note both explain the same cascade;
    # keep the wording explicit so a translation edit cannot drop a dependant.
    for translator in (chinese, english):
        cascade = translator("beauty_cascade")
        assert "A|B" in cascade
        assert "人脸框" in cascade or "face boxes" in cascade
        note = translator("note")
        assert "A|B" in note
        assert "人脸框" in note or "face boxes" in note
    assert chinese("beauty_status_off") == "已关闭 · 分屏与人脸框已关"
    assert english("beauty_pill_off") == "Beauty · off"


def test_detection_enabling_hint_is_localized() -> None:
    # The live-clip restart shows this while the NPU warms up; both languages
    # matter because the board captures English evidence too.
    assert (
        Translator("zh")("detection_enabling") == "正在启用人脸检测（约2秒）"
    )
    english = Translator("en")("detection_enabling")
    assert "face detection" in english.lower()
    assert "~2 s" in english


def test_playback_status_recovery_actions_are_localized() -> None:
    chinese = Translator("zh")
    english = Translator("en")
    assert chinese("status_loading_title") == "正在读取视频"
    assert english("status_loading_title") == "Loading video"
    assert chinese("return_to_playlist") == "返回列表"
    assert english("return_to_playlist") == "Back to playlist"
    assert chinese("replay_video") == "重新播放"
    assert english("replay_video") == "Replay"


def test_browse_shell_strings_are_localized() -> None:
    chinese = Translator("zh")
    english = Translator("en")
    assert chinese("nav_library") == "片库"
    assert english("nav_library") == "Library"
    assert chinese("nav_recent") == "最近"
    assert english("nav_recent") == "Recent"
    assert chinese("library_filter_finished") == "已看完"
    assert english("library_filter_finished") == "Finished"
    assert chinese("library_sort_added") == "最近添加"
    assert english("library_sort_added") == "Recently added"
    # Singular/plural forms: the header must not say "1 folders".
    assert chinese("library_stat_one", count=1) == "1 项"
    assert english("library_stat_one", count=1) == "1 item"
    assert english("library_stat", count=12) == "12 items"
    assert english("library_sub_one", folders=1) == "1 folder"
    assert english("library_sub", folders=2) == "2 folders"
    # The rail status distinguishes hardware decode from the software fallback.
    assert chinese("rail_ready") == "硬解就绪"
    assert english("rail_ready") == "HW ready"
    assert chinese("rail_sw_only") == "仅软解"
    assert english("rail_sw_only") == "SW only"


def test_browse_shell_empty_and_rescan_copy_is_present_in_both_languages() -> None:
    for translator in (Translator("zh"), Translator("en")):
        assert translator("library_empty_title")
        assert translator("library_empty_body")
        assert translator("library_add_dir")
        assert translator("library_open_file")
        assert translator("library_rescan")
        assert translator("library_scan_scope")
        assert translator("library_supported")
        assert translator("library_dialog_title")
