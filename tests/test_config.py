"""Tests for the configuration contract (loading, defaults, clamping, errors)."""

from __future__ import annotations

import json

import pytest

from video_player.config import (
    BeautyConfig,
    Config,
    ConfigError,
    PlayerConfig,
    default_config_path,
)


def test_defaults_are_valid_and_match_example_file() -> None:
    config = Config()
    config.validate()
    assert config.player.render_sink == "gtk4paintablesink"
    assert config.player.video_convert == "auto"
    assert config.player.volume == 80
    assert config.beauty.enabled is True
    assert config.detection.interval_frames == 3

    # The shipped example file is the documented defaults.
    example = Config.load()
    assert example == config
    assert default_config_path().exists()


def test_partial_section_keeps_other_defaults() -> None:
    config = Config.from_dict({"beauty": {"smoothing": 30}})
    assert config.beauty.smoothing == 30
    assert config.beauty.whitening == BeautyConfig().whitening
    assert config.player == PlayerConfig()


def test_values_are_clamped_to_safe_ranges() -> None:
    config = Config.from_dict(
        {
            "player": {"volume": 250, "speed": 3.5},
            "beauty": {"smoothing": -20, "whitening": 180, "feather_px": 500},
            "detection": {"interval_frames": 0, "max_faces": 99, "min_score": 1.5},
            "debug": {"fps_window": 10000},
        }
    )
    assert config.player.volume == 100
    assert config.player.speed == 2.0
    assert config.beauty.smoothing == 0
    assert config.beauty.whitening == 100
    assert config.beauty.feather_px == 64
    assert config.detection.interval_frames == 1
    assert config.detection.max_faces == 8
    assert config.detection.min_score == 1.0
    assert config.debug.fps_window == 600


def test_video_convert_rejects_unknown_backend() -> None:
    with pytest.raises(ConfigError, match="video_convert"):
        Config.from_dict({"player": {"video_convert": "cuda"}})


def test_unknown_keys_are_rejected() -> None:
    with pytest.raises(ConfigError, match="unknown key"):
        Config.from_dict({"player": {"volumne": 10}})
    with pytest.raises(ConfigError, match="unknown key"):
        Config.from_dict({"mystery": {}})


def test_invalid_sink_is_rejected() -> None:
    with pytest.raises(ConfigError, match="render_sink"):
        Config.from_dict({"player": {"render_sink": "not-a-sink"}})


def test_unsupported_version_is_rejected() -> None:
    with pytest.raises(ConfigError, match="version"):
        Config.from_dict({"version": 99})


def test_save_and_load_roundtrip(tmp_path) -> None:
    config = Config.load()
    config.beauty.whitening = 77
    path = tmp_path / "player.json"
    saved = config.save(path)
    assert saved.exists()
    assert Config.load(path) == config


def test_bad_json_reports_config_error(tmp_path) -> None:
    path = tmp_path / "broken.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(ConfigError, match="not valid JSON"):
        Config.load(path)


def test_missing_explicit_file_reports_config_error(tmp_path) -> None:
    with pytest.raises(ConfigError, match="not found"):
        Config.load(tmp_path / "missing.json")


def test_to_dict_is_json_serialisable() -> None:
    text = json.dumps(Config.load().to_dict())
    assert '"smoothing": 60' in text
