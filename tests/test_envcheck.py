"""Tests for the environment report model and formatters (no host probing)."""

from __future__ import annotations

import pytest

from video_player.envcheck import (
    Check,
    Report,
    format_markdown,
    format_report,
)


def _report() -> Report:
    return Report(
        platform={
            "system": "Linux",
            "release": "6.1.0",
            "machine": "aarch64",
            "python": "3.13.0",
            "session": "wayland",
        },
        checks=[
            Check("python", "ok", "Python 3.13.0"),
            Check("gst:mppvideodec", "warn", "missing from this host", board_related=True),
            Check("models", "fail", "no .rknn model", board_related=True),
        ],
    )


def test_status_validation() -> None:
    with pytest.raises(ValueError, match="invalid check status"):
        Check("weird", "great")


def test_counts() -> None:
    assert _report().counts() == {"ok": 1, "warn": 1, "fail": 1, "skip": 0}


def test_board_failure_is_ignored_on_dev_hosts() -> None:
    assert _report().ok() is True


def test_optional_board_warning_does_not_fail_require_board() -> None:
    report = Report(
        platform={"system": "Linux"},
        checks=[
            Check("python", "ok", "Python 3.13.0"),
            Check("gst:mppvideodec", "ok", "available", board_related=True),
            Check(
                "models",
                "warn",
                "no .rknn model in models/ (automatic face beauty stays disabled)",
                board_related=True,
                optional=True,
            ),
        ],
    )
    assert report.ok(require_board=True) is True


def test_board_failure_fails_the_report_with_require_board() -> None:
    assert _report().ok(require_board=True) is False


def test_board_warning_fails_the_report_with_require_board() -> None:
    report = Report(platform={}, checks=[Check("lib:librknnrt.so", "warn", "missing", True)])
    assert report.ok() is True
    assert report.ok(require_board=True) is False


def test_core_failure_always_fails_the_report() -> None:
    report = Report(platform={}, checks=[Check("gtk4", "fail", "not importable")])
    assert report.ok() is False


def test_formatters_include_every_check() -> None:
    report = _report()
    text = format_report(report)
    assert "environment report" in text.lower()
    for check in report.checks:
        assert check.name in text
    assert "1 ok, 1 warn, 1 fail" in text

    markdown = format_markdown(report)
    assert "| Check | Status | Detail |" in markdown
    assert "`gst:mppvideodec (board)`" in markdown
    assert "| `python` | ok |" in markdown


def test_to_dict_summary() -> None:
    payload = _report().to_dict()
    assert payload["summary"] == {"ok": 1, "warn": 1, "fail": 1, "skip": 0}
    assert isinstance(payload["checks"], list)
    assert payload["checks"][1]["board_related"] is True
