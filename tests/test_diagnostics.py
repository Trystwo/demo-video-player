"""Tests for the best-effort diagnostics helpers."""

from __future__ import annotations

from video_player import diagnostics


def test_action_log_appends(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    diagnostics.log_action("wheel", scale="smoothing", value=42)
    diagnostics.log_action("language", lang="en")
    log = tmp_path / "video-player-demo" / "actions.log"
    text = log.read_text(encoding="utf-8")
    assert "wheel" in text
    assert "scale=smoothing" in text
    assert "value=42" in text
    assert "language" in text
    assert "lang=en" in text


def test_action_log_truncates_and_never_raises(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    diagnostics.log_action("long", text="x" * 500, multiline="a\nb")
    log = tmp_path / "video-player-demo" / "actions.log"
    text = log.read_text(encoding="utf-8")
    assert "x" * 157 + "..." in text
    assert "multiline=a\\nb" in text


def test_cache_path_uses_home_without_xdg(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("XDG_CACHE_HOME", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    assert diagnostics.cache_dir() == tmp_path / ".cache" / "video-player-demo"
