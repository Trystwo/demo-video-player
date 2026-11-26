#!/usr/bin/env python3
"""Regenerate the embedded source payload of ``board-install.sh``.

``board-install.sh`` is a single-file offline installer: the sources are
base64-encoded inside the script.  Hand-editing that blob is how it went stale
in the first place, so this script rebuilds it from the working tree and can
verify that the committed blob still matches the tree.

The payload manifest is intentionally explicit (not ``git ls-files``) because
some shipped modules are still untracked in the working tree during UI work:

    assets/desktop-icons/**          bundled Desktop Icons NG extension
    configs/*.json                   example player config
    install.sh                       the installer the script delegates to
    requirements.txt                 runtime dependencies
    src/video_player/**/*.py         the application

Never included: ``models/*.rknn`` / ``*.onnx`` (licence + size; generated on
the dev machine), test media, tests, docs, caches and virtualenvs.

The output is byte-for-byte reproducible: entries sorted, timestamps pinned to
the epoch, fixed ownership, fixed modes and a gzip header without a timestamp.
That lets ``tests/test_board_install.py`` compare the embedded payload with a
freshly built one.

Usage::

    python3 scripts/build_board_install.py            # rewrite board-install.sh
    python3 scripts/build_board_install.py --check    # fail if the blob is stale
    python3 scripts/build_board_install.py --out FILE # write somewhere else
"""

from __future__ import annotations

import argparse
import base64
import gzip
import hashlib
import io
import os
import re
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "board-install.sh"

# The first marker line is ``base64 -d <<'__BOARD_PAYLOAD__' | tar -xzf - -C "$DEST"``,
# the closing one is the bare heredoc terminator.
PAYLOAD_START = b"base64 -d <<'__BOARD_PAYLOAD__'"
PAYLOAD_END = b"__BOARD_PAYLOAD__\n"

# ``printf '[board-install] unpacking %d source files -> %s\n' 57 "$DEST"``
COUNT_RE = re.compile(rb"(source files -> %s\\n' )(\d+)( \"\$DEST\")")

# Directories whose whole contents ship (recursively).
MANIFEST_DIRS = ("assets/desktop-icons",)
# Single files that ship as-is.
MANIFEST_FILES = ("install.sh", "requirements.txt")
# Globs (relative to the project root) that ship.
MANIFEST_GLOBS = ("configs/*.json", "src/video_player/**/*.py")

EXCLUDED_DIR_NAMES = {"__pycache__", ".pytest_cache", ".venv", "venv"}
EXCLUDED_SUFFIXES = {".pyc", ".pyo", ".log", ".tmp"}


def collect_files(root: Path = ROOT) -> list[str]:
    """Return the sorted manifest of files that belong in the payload."""

    found: set[str] = set()
    for directory in MANIFEST_DIRS:
        base = root / directory
        if not base.is_dir():
            continue
        for path in base.rglob("*"):
            if path.is_file():
                found.add(path.relative_to(root).as_posix())
    for name in MANIFEST_FILES:
        path = root / name
        if path.is_file():
            found.add(path.relative_to(root).as_posix())
    for pattern in MANIFEST_GLOBS:
        for path in root.glob(pattern):
            if path.is_file():
                found.add(path.relative_to(root).as_posix())

    cleaned = []
    for name in found:
        parts = name.split("/")
        if any(part in EXCLUDED_DIR_NAMES for part in parts):
            continue
        if os.path.splitext(name)[1] in EXCLUDED_SUFFIXES:
            continue
        cleaned.append(name)
    return sorted(cleaned)


def _file_mode(name: str) -> int:
    return 0o755 if name.endswith(".sh") else 0o644


def build_tarball(root: Path, files: list[str]) -> bytes:
    """Pack ``files`` into a reproducible ``.tar.gz`` (bytes)."""

    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w", format=tarfile.GNU_FORMAT) as tar:
        for name in files:
            data = (root / name).read_bytes()
            info = tarfile.TarInfo(name=name)
            info.size = len(data)
            info.mtime = 0
            info.uid = info.gid = 0
            info.uname = info.gname = ""
            info.mode = _file_mode(name)
            tar.addfile(info, io.BytesIO(data))

    packed = io.BytesIO()
    # filename='' + mtime=0 keeps the gzip header free of a timestamp.
    with gzip.GzipFile(
        filename="", mode="wb", compresslevel=9, fileobj=packed, mtime=0
    ) as gz:
        gz.write(raw.getvalue())
    return packed.getvalue()


def encode_payload(blob: bytes) -> bytes:
    """base64 with the classic 76-column wrapping ``base64 -d`` accepts."""

    return base64.encodebytes(blob)


def split_script(script: bytes) -> tuple[bytes, bytes, bytes]:
    """Return ``(head, payload, tail)`` around the embedded blob."""

    start = script.find(PAYLOAD_START)
    if start < 0:
        raise SystemExit("board-install.sh: payload start marker not found")
    newline = script.find(b"\n", start)
    if newline < 0:
        raise SystemExit("board-install.sh: payload start line is not terminated")
    head_end = newline + 1
    end = script.find(PAYLOAD_END, head_end)
    if end < 0:
        raise SystemExit("board-install.sh: payload end marker not found")
    return script[:head_end], script[head_end:end], script[end:]


def payload_of(script: bytes) -> bytes:
    return split_script(script)[1]


def render_script(script: bytes, files: list[str], payload: bytes) -> bytes:
    """Splice ``payload`` into ``script`` and update the printed file count."""

    head, _old, tail = split_script(script)
    head = COUNT_RE.sub(
        lambda m: m.group(1) + str(len(files)).encode() + m.group(3), head
    )
    return head + payload + tail


def decode_payload(payload: bytes) -> bytes:
    """Decode the heredoc body (whitespace-wrapped base64) to gzip bytes."""

    compact = b"".join(payload.split())
    return base64.b64decode(compact, validate=True)


def payload_files(payload: bytes) -> dict[str, bytes]:
    """Decode the payload and return ``{name: content}`` (in memory)."""

    files: dict[str, bytes] = {}
    blob = decode_payload(payload)
    with tarfile.open(fileobj=io.BytesIO(blob), mode="r:gz") as tar:
        for member in tar.getmembers():
            if not member.isfile():
                raise SystemExit(f"payload member is not a regular file: {member.name}")
            handle = tar.extractfile(member)
            assert handle is not None
            files[member.name] = handle.read()
    return files


def verify(root: Path, payload: bytes) -> list[str]:
    """Compare the payload with the working tree; return human-readable problems."""

    problems: list[str] = []
    expected = collect_files(root)
    actual = payload_files(payload)

    for name in actual:
        if name.startswith("/") or ".." in name.split("/"):
            problems.append(f"unsafe payload path: {name}")

    missing = [name for name in expected if name not in actual]
    extra = [name for name in actual if name not in expected]
    if missing:
        problems.append("missing from payload: " + ", ".join(missing))
    if extra:
        problems.append("obsolete in payload: " + ", ".join(extra))

    for name in expected:
        if name not in actual:
            continue
        want = hashlib.sha256((root / name).read_bytes()).hexdigest()
        got = hashlib.sha256(actual[name]).hexdigest()
        if want != got:
            problems.append(f"stale content: {name}")

    if len(actual) != len(expected):
        problems.append(
            f"file count {len(actual)} in payload, {len(expected)} in manifest"
        )
    return problems


def compile_check(payload: bytes) -> str | None:
    """Extract the payload and byte-compile its sources; return an error or None."""

    with tempfile.TemporaryDirectory(prefix="board-install-verify-") as tmp:
        target = Path(tmp)
        blob = decode_payload(payload)
        with tarfile.open(fileobj=io.BytesIO(blob), mode="r:gz") as tar:
            try:
                tar.extractall(target, filter="data")  # Python >= 3.11.4
            except TypeError:  # pragma: no cover - older interpreters
                tar.extractall(target)
        src = target / "src"
        if not src.is_dir():
            return "payload has no src/ directory"
        proc = subprocess.run(
            [sys.executable, "-m", "compileall", "-q", str(src)],
            capture_output=True,
            text=True,
        )
        if proc.returncode != 0:
            return (proc.stderr or proc.stdout).strip() or "compileall failed"
    return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--check",
        action="store_true",
        help="verify the committed payload instead of rewriting it",
    )
    parser.add_argument(
        "--out",
        type=Path,
        help="write the regenerated script here instead of board-install.sh",
    )
    parser.add_argument(
        "--no-compile",
        action="store_true",
        help="skip the extracted-source byte-compile check",
    )
    args = parser.parse_args(argv)

    files = collect_files(ROOT)
    if not files:
        print("error: manifest is empty", file=sys.stderr)
        return 2
    payload = encode_payload(build_tarball(ROOT, files))

    if args.check:
        current = payload_of(TARGET.read_bytes())
        if current == payload:
            print(
                f"board-install.sh payload is up to date: {len(files)} files, "
                f"{len(current)} bytes of base64"
            )
            return 0
        problems = verify(ROOT, current)
        print("board-install.sh payload is STALE", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        if not problems:  # pragma: no cover - payload differs without content drift
            print("  - embedded blob differs from a fresh build", file=sys.stderr)
        print("\nrun: python3 scripts/build_board_install.py", file=sys.stderr)
        return 1

    # The committed script is always the template (header + trailer); --out only
    # changes where the regenerated copy is written.
    target = TARGET if args.out is None else args.out
    script = render_script(TARGET.read_bytes(), files, payload)
    problems = verify(ROOT, payload_of(script))
    if problems:
        for problem in problems:
            print(f"error: {problem}", file=sys.stderr)
        return 2
    if not args.no_compile:
        error = compile_check(payload_of(script))
        if error:
            print(f"error: payload does not compile: {error}", file=sys.stderr)
            return 2

    tmp = target.with_name(target.name + ".tmp")
    tmp.write_bytes(script)
    os.replace(tmp, target)
    try:
        shown = target.relative_to(ROOT)
    except ValueError:
        shown = target
    print(
        f"wrote {shown}: {len(files)} files, {len(script)} bytes total, "
        f"{len(payload)} bytes of base64"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
