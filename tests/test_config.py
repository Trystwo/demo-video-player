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
    assert config.player.texture_max_dimension == 1280
    assert config.player.volume == 80
    assert config.beauty.enabled is True
    assert config.detection.enabled is True
    assert config.detection.core_mask == 0
    assert config.detection.model_path == "models/yunet_rk3576.rknn"
    assert config.detection.interval_frames == 3
    assert config.detection.hold_ms == 600
    assert config.beauty.face_slim == 0.0
    assert config.beauty.eye_enlarge == 0.0

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
            "player": {"volume": 250, "speed": 3.5, "texture_max_dimension": 99999},
            "beauty": {
                "smoothing": -20,
                "whitening": 180,
                "feather_px": 500,
                "face_slim": 150,
                "eye_enlarge": -5,
            },
            "detection": {
                "interval_frames": 0,
                "max_faces": 99,
                "min_score": 1.5,
                "hold_ms": 99999,
            },
            "debug": {"fps_window": 10000},
        }
    )
    assert config.player.volume == 100
    assert config.player.speed == 2.0
    assert config.player.texture_max_dimension == 8192
    assert config.beauty.smoothing == 0
    assert config.beauty.whitening == 100
    assert config.beauty.feather_px == 64
    assert config.detection.interval_frames == 1
    assert config.detection.max_faces == 8
    assert config.detection.min_score == 1.0
    assert config.detection.hold_ms == 5000
    assert config.beauty.face_slim == 100.0
    assert config.beauty.eye_enlarge == 0.0
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


def test_experimental_beauty_rounds_to_one_decimal() -> None:
    config = Config.from_dict({"beauty": {"face_slim": 33.33, "eye_enlarge": 66.66}})
    assert config.beauty.face_slim == 33.3
    assert config.beauty.eye_enlarge == 66.7


def test_hold_ms_must_be_a_number() -> None:
    with pytest.raises(ConfigError, match="hold_ms"):
        Config.from_dict({"detection": {"hold_ms": "soon"}})
