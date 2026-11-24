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
    assert translator("npu_face", count=1, ms=1.25) == "1 face · 1.2 ms"
    assert translator("npu_faces", count=2, ms=1.25) == "2 faces · 1.2 ms"
    assert translator("beauty_split", split=0.5) == ", split 50%"
    assert (
        translator(
            "info_line",
            width=1280,
            height=720,
            codec="h264",
            decoder="mppvideodec",
            fps="30.0",
            npu="off",
            beauty="off",
        )
        == "1280×720   h264   decode: mppvideodec   fps: 30.0   NPU: off   beauty: off"
    )


def test_playback_status_recovery_actions_are_localized() -> None:
    chinese = Translator("zh")
    english = Translator("en")
    assert chinese("status_loading_title") == "正在读取视频"
    assert english("status_loading_title") == "Loading video"
    assert chinese("return_to_playlist") == "返回列表"
    assert english("return_to_playlist") == "Back to playlist"
    assert chinese("replay_video") == "重新播放"
    assert english("replay_video") == "Replay"
