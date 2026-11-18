"""Environment self-checks for the video player demo.

Used by ``python3 -m video_player --check`` and by ``scripts/probe_board.py``.
Checks are split into *core* checks (anything a GTK4 + GStreamer host needs) and
*board* checks (RK3576 VPU / NPU / GPU pieces).  A missing board item is only a
warning on a development host; ``--require-board`` turns those warnings into a
failure, which is what ``install.sh`` does on the board.

The complete board baseline recorded by the doc team lives in ``docs/BOARD.md``;
this module is the executable version of that checklist.
"""

from __future__ import annotations

import glob
import os
import platform
import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

#: Minimum Python version supported by the demo (board image ships 3.13).
MIN_PYTHON = (3, 10)

#: Plugins every GTK4 + GStreamer host needs for UI, media decode and audio.
CORE_GST_PLUGINS = (
    "uridecodebin",
    "videoconvert",
    "glupload",
    "glcolorconvert",
    "glshader",
    "pulsesink",
)

#: Plugins/features that only exist on the RK3576 board image.
BOARD_GST_PLUGINS = ("mppvideodec", "mpph264enc", "waylandsink", "gtk4paintablesink")

#: Shared libraries shipped by the Quectel board image (NPU runtime and RGA).
BOARD_LIBRARIES = ("librknnrt.so", "librga.so")

#: Valid status values for a single check.
STATUSES = ("ok", "warn", "fail", "skip")


@dataclass
class Check:
    """Result of one environment probe.

    ``optional`` marks a check whose warning/failure never blocks a board
    deployment: missing RKNN models only disable the optional face-detection
    feature, so a fresh clone must still be deployable with ``--require-board``.
    """

    name: str
    status: str
    detail: str = ""
    board_related: bool = False
    optional: bool = False

    def __post_init__(self) -> None:
        if self.status not in STATUSES:
            raise ValueError(f"invalid check status {self.status!r}; expected one of {STATUSES}")


@dataclass
class Report:
    """All probes for one host, plus the host description."""

    platform: dict[str, str]
    checks: list[Check] = field(default_factory=list)

    def counts(self) -> dict[str, int]:
        counts = {status: 0 for status in STATUSES}
        for check in self.checks:
            counts[check.status] += 1
        return counts

    def ok(self, require_board: bool = False) -> bool:
        """True when no relevant check failed.

        Core failures always fail the report.  Board warnings only fail it when
        the caller runs on the target hardware (``require_board``).
        """
        for check in self.checks:
            if check.status == "fail" and (require_board or not check.board_related):
                return False
            if require_board and check.board_related and not check.optional and check.status in ("warn", "fail"):
                return False
        return True

    def to_dict(self) -> dict[str, object]:
        return {
            "platform": dict(self.platform),
            "checks": [asdict(check) for check in self.checks],
            "summary": self.counts(),
        }


def _run(cmd: list[str], timeout: float = 10.0) -> tuple[int, str]:
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 127, str(exc)
    output = (proc.stdout + proc.stderr).strip()
    return proc.returncode, output or f"exit code {proc.returncode}"


def _first_line(text: str) -> str:
    lines = text.splitlines()
    return lines[0].strip() if lines else ""


def check_python() -> Check:
    current = sys.version_info[:2]
    needed = f"{MIN_PYTHON[0]}.{MIN_PYTHON[1]}"
    detail = f"Python {platform.python_version()} (minimum {needed})"
    return Check("python", "ok" if current >= MIN_PYTHON else "fail", detail)


def check_gtk() -> Check:
    try:
        import gi

        gi.require_version("Gtk", "4.0")
        from gi.repository import Gtk

        gtk_version = (
            f"{Gtk.get_major_version()}.{Gtk.get_minor_version()}.{Gtk.get_micro_version()}"
        )
    except (ImportError, ValueError) as exc:
        return Check("gtk4", "fail", f"PyGObject with Gtk 4.0 is not importable: {exc}")
    return Check("gtk4", "ok", f"PyGObject {gi.__version__}, Gtk {gtk_version}")


def check_gstreamer() -> Check:
    tool = shutil.which("gst-inspect-1.0")
    if tool is None:
        return Check(
            "gstreamer",
            "fail",
            "gst-inspect-1.0 not found (install gstreamer1.0-tools)",
        )
    code, output = _run([tool, "--version"])
    if code != 0:
        return Check("gstreamer", "fail", f"gst-inspect-1.0 --version failed: {output}")
    return Check("gstreamer", "ok", _first_line(output))


def check_gst_plugin(name: str, board_related: bool = False) -> Check:
    """Check that a GStreamer plugin element is available."""
    tool = shutil.which("gst-inspect-1.0")
    if tool is None:
        return Check(f"gst:{name}", "fail", "gst-inspect-1.0 not found", board_related)
    code, output = _run([tool, name])
    if code == 0:
        return Check(f"gst:{name}", "ok", "available", board_related)
    status = "warn" if board_related else "fail"
    detail = "missing from this host"
    if board_related:
        detail += " (board-only plugin; expected off-board)"
    return Check(f"gst:{name}", status, detail, board_related)


def _find_shared_library(name: str) -> str | None:
    """Locate a shared library through ldconfig or the usual install paths."""
    candidates: list[str] = []
    for tool in ("ldconfig", "/sbin/ldconfig", "/usr/sbin/ldconfig"):
        path = tool if os.path.isabs(tool) else shutil.which(tool)
        if path is None:
            continue
        candidates.append(path)
        break
    for ldconfig in candidates:
        code, output = _run([ldconfig, "-p"])
        if code != 0:
            continue
        for line in output.splitlines():
            if name in line and "=>" in line:
                return line.split("=>", 1)[1].strip()
    for pattern in (
        f"/usr/lib/*/{name}",
        f"/usr/local/lib/*/{name}",
        f"/usr/lib/{name}",
        f"/usr/local/lib/{name}",
        f"/opt/**/{name}",
    ):
        matches = sorted(glob.glob(pattern, recursive=True))
        if matches:
            return matches[0]
    return None


def check_board_library(name: str) -> Check:
    """Check a board-only shared library (librknnrt.so / librga.so)."""
    found = _find_shared_library(name)
    if found:
        return Check(f"lib:{name}", "ok", found, board_related=True)
    return Check(
        f"lib:{name}",
        "warn",
        "not found (board-only; required for NPU detection / RGA scaling)",
        board_related=True,
    )


def check_display() -> Check:
    """Report whether a graphical session is available for the GTK window."""
    parts: list[str] = []
    if os.environ.get("WAYLAND_DISPLAY"):
        parts.append(f"WAYLAND_DISPLAY={os.environ['WAYLAND_DISPLAY']}")
    if os.environ.get("DISPLAY"):
        parts.append(f"DISPLAY={os.environ['DISPLAY']}")
    if Path("/dev/dri").exists():
        parts.append("/dev/dri present")
    if not parts:
        return Check(
            "display",
            "warn",
            "no Wayland/X11 session detected; --check works headless, the window needs a display",
        )
    return Check("display", "ok", ", ".join(parts))


def check_models(root: Path) -> Check:
    """Check that at least one converted RKNN face model is present."""
    model_dir = root / "models"
    models = sorted(model_dir.glob("*.rknn")) if model_dir.is_dir() else []
    if models:
        return Check(
            "models",
            "ok",
            ", ".join(model.name for model in models),
            board_related=True,
        )
    return Check(
        "models",
        "warn",
        "no .rknn model in models/ (automatic face beauty stays disabled); see models/README.md",
        board_related=True,
        optional=True,
    )


def collect(root: Path | None = None) -> Report:
    """Run all probes and return the complete report."""
    demo_root = Path(root) if root is not None else Path(__file__).resolve().parents[2]
    checks: list[Check] = [check_python(), check_gtk(), check_gstreamer()]
    checks.extend(check_gst_plugin(name) for name in CORE_GST_PLUGINS)
    checks.extend(check_gst_plugin(name, board_related=True) for name in BOARD_GST_PLUGINS)
    checks.extend(check_board_library(name) for name in BOARD_LIBRARIES)
    checks.append(check_display())
    checks.append(check_models(demo_root))

    platform_info = {
        "system": platform.system(),
        "release": platform.release(),
        "machine": platform.machine(),
        "python": platform.python_version(),
        "session": os.environ.get("XDG_SESSION_TYPE", "unknown"),
    }
    return Report(platform=platform_info, checks=checks)


def format_report(report: Report) -> str:
    """Plain-text report for the CLI."""
    counts = report.counts()
    host = " ".join(
        report.platform.get(key, "") for key in ("system", "release", "machine")
    ).strip()
    lines = [
        "Video Player environment report",
        f"  host: {host or 'unknown'} (Python {report.platform.get('python', '?')}, "
        f"session {report.platform.get('session', '?')})",
        "",
    ]
    marks = {"ok": "  ok  ", "warn": " warn ", "fail": " FAIL ", "skip": " skip "}
    for check in report.checks:
        scope = " (board)" if check.board_related else ""
        lines.append(f"[{marks[check.status].strip():>4}] {check.name}{scope}: {check.detail}")
    lines.append("")
    lines.append(
        f"summary: {counts['ok']} ok, {counts['warn']} warn, "
        f"{counts['fail']} fail, {counts['skip']} skip"
    )
    return "\n".join(lines)


def format_markdown(report: Report) -> str:
    """Markdown table for docs/BOARD.md or PR notes."""
    lines = ["| Check | Status | Detail |", "|-------|--------|--------|"]
    for check in report.checks:
        name = check.name + (" (board)" if check.board_related else "")
        detail = check.detail.replace("|", "\\|")
        lines.append(f"| `{name}` | {check.status} | {detail} |")
    return "\n".join(lines)


if __name__ == "__main__":  # pragma: no cover - convenience entry point
    report = collect()
    print(format_report(report))
    sys.exit(0 if report.ok() else 1)
