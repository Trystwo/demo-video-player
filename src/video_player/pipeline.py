"""Playback element selection for the M1 local-file pipeline.

M1 builds an explicit GStreamer pipeline so the RK3576 hardware decoder and the
GTK 4 paintable sink are used unconditionally::

    filesrc location=... ! qtdemux name=demux
    demux.video_0 ! queue ! h264parse ! mppvideodec ! videoconvert
                  ! fpsdisplaysink video-sink=gtk4paintablesink
    demux.audio_0 ! queue ! decodebin ! audioconvert ! audioresample
                  ! volume ! autoaudiosink

The container demuxer is chosen from the file suffix, the parser and decoder
from the caps of the demuxer video pad.  Keeping those decisions here (rather
than inside the GStreamer backend) keeps them pure and testable on a
development host without a VPU.

Network streams (F2) and subtitles (F5) are out of scope for this release: the
player only accepts local files.
"""

from __future__ import annotations

from pathlib import Path
from urllib.parse import urlparse

#: Demuxers per container suffix.  MP4/MKV/TS cover the demo media.
DEMUXERS: dict[str, str] = {
    ".mp4": "qtdemux",
    ".m4v": "qtdemux",
    ".mov": "qtdemux",
    ".qt": "qtdemux",
    ".mkv": "matroskademux",
    ".webm": "matroskademux",
    ".ts": "tsdemux",
    ".m2ts": "tsdemux",
    ".mts": "tsdemux",
}

#: Parser element per video caps name produced by the demuxer.
PARSERS: dict[str, str] = {
    "video/x-h264": "h264parse",
    "video/x-h265": "h265parse",
    "video/x-vp9": "vp9parse",
    "video/x-av1": "av1parse",
    "video/x-mpeg2": "mpegvideoparse",
    "video/x-h263": "h263parse",
    "video/x-vp8": "vp8parse",
}

#: RK3576 hardware decoder(s) per caps name (VPU via mppvideodec).
HW_DECODERS: dict[str, str] = {
    "video/x-h264": "mppvideodec",
    "video/x-h265": "mppvideodec",
}

#: Software decoder fallback per caps name (off-board hosts).
SW_DECODERS: dict[str, str] = {
    "video/x-h264": "avdec_h264",
    "video/x-h265": "avdec_h265",
    "video/x-vp9": "vp9dec",
    "video/x-av1": "dav1ddec",
    "video/x-mpeg2": "mpeg2dec",
}

#: Decode strategies exposed by ``player.decode_mode``.  ``auto`` asks for the
#: VPU decoder but accepts the software one when the VPU path is unavailable or
#: fails mid-stream; ``hw`` and ``sw`` force one path and never fall back.
DECODE_MODES = ("auto", "hw", "sw")

#: Local media suffixes accepted by the playlist and the window drop handler.
VIDEO_SUFFIXES = tuple(sorted(DEMUXERS))

#: Container signatures used by :func:`sniff_container` as
#: ``(byte offset, magic, container key)``.  Only unambiguous magics belong
#: here: a wrong guess would pick a demuxer that cannot parse a single byte.
_CONTAINER_MAGIC: tuple[tuple[int, bytes, str], ...] = (
    (4, b"ftyp", "mp4"),  # ISO base media file format (MP4 / MOV / M4V)
    (0, b"\x1a\x45\xdf\xa3", "matroska"),  # EBML header (MKV / WebM)
)

#: Demuxer per container key reported by :func:`sniff_container`.
_SNIFFED_DEMUXERS: dict[str, str] = {
    "mp4": "qtdemux",
    "matroska": "matroskademux",
}


def sniff_container(media: str | Path) -> str | None:
    """Return the container key detected from the first bytes of *media*.

    Media collected from stock-footage sites is routinely renamed, so a
    ``.mkv`` file may hold an MP4 stream and vice versa.  The suffix alone
    then selects a demuxer that fails on the very first byte
    (``ebml-read: Invalid EBML ID size tag``), which looks like a decoder
    problem to the viewer.  Reading a handful of bytes is cheap and catches
    it before the pipeline is built.

    Returns:
        ``"mp4"`` / ``"matroska"`` when the magic is recognised, otherwise
        ``None``.  ``None`` deliberately means "keep using the suffix", so
        unreadable and not-yet-downloaded paths behave exactly as before.
    """
    try:
        with open(media, "rb") as handle:
            head = handle.read(16)
    except OSError:
        return None
    for offset, magic, key in _CONTAINER_MAGIC:
        if head[offset : offset + len(magic)] == magic:
            return key
    return None


def demux_for(media: str | Path) -> str:
    """Return the demuxer factory for *media*.

    The container signature wins when it disagrees with the suffix, because
    a mismatched demuxer cannot open the file at all.  When the magic is not
    recognised (or the file is unreadable) the suffix decides, so this stays
    a pure suffix lookup for callers that pass a path they have not
    downloaded yet.

    Raises:
        ValueError: when the container suffix is not supported.
    """
    suffix = Path(media).suffix.lower()
    try:
        from_suffix = DEMUXERS[suffix]
    except KeyError:
        supported = ", ".join(sorted(DEMUXERS))
        raise ValueError(
            f"unsupported container {suffix or '<none>'!r}; supported: {supported}"
        ) from None
    sniffed = sniff_container(media)
    if sniffed is not None:
        return _SNIFFED_DEMUXERS[sniffed]
    return from_suffix


def parser_for(caps_name: str) -> str:
    """Return the parser factory for a demuxer video caps name.

    Raises:
        ValueError: for video codecs the player cannot parse.
    """
    try:
        return PARSERS[caps_name]
    except KeyError:
        supported = ", ".join(sorted(PARSERS))
        raise ValueError(
            f"unsupported video codec {caps_name!r}; supported: {supported}"
        ) from None


def decoder_candidates(caps_name: str, mode: str = "auto") -> tuple[str, ...]:
    """Return the decoder factories to try for *caps_name*, best first.

    ``hw`` / ``sw`` return exactly one factory, so a missing element is a hard
    failure instead of a silent switch to the other path.  ``auto`` returns the
    VPU decoder first and the software decoder second; the caller walks the
    tuple and keeps the first factory that is actually installed.

    Raises:
        ValueError: for an unknown *mode* or a codec without any decoder.
    """
    if mode not in DECODE_MODES:
        raise ValueError(
            f"unknown decode mode {mode!r}; expected one of {', '.join(DECODE_MODES)}"
        )
    if mode != "auto":
        table = HW_DECODERS if mode == "hw" else SW_DECODERS
        try:
            return (table[caps_name],)
        except KeyError:
            supported = ", ".join(sorted(table))
            raise ValueError(
                f"unsupported video codec {caps_name!r} in {mode} mode; "
                f"expected one of {supported}"
            ) from None
    hardware = HW_DECODERS.get(caps_name)
    software = SW_DECODERS.get(caps_name)
    candidates = tuple(f for f in (hardware, software) if f is not None)
    if not candidates:
        supported = ", ".join(sorted(set(HW_DECODERS) | set(SW_DECODERS)))
        raise ValueError(
            f"unsupported video codec {caps_name!r}; expected one of {supported}"
        )
    return candidates


def decoder_for(caps_name: str, hw_decode: bool = True) -> str:
    """Return the single decoder factory for *caps_name* (no availability check).

    Kept for callers that only need the forced choice; ``hw_decode=False`` is
    the software path used on a development host.

    Raises:
        ValueError: for codecs without a decoder.
    """
    return decoder_candidates(caps_name, "hw" if hw_decode else "sw")[0]


def to_uri(media: str | Path) -> str:
    """Convert a local path to a ``file://`` URI; pass URLs through unchanged."""
    text = str(media)
    if "://" in text:
        return text
    return Path(text).expanduser().resolve().as_uri()


def is_stream_uri(media: str | Path) -> bool:
    """Return True for non-file URIs (kept for the future network-stream mode)."""
    scheme = urlparse(str(media)).scheme.lower()
    return bool(scheme) and scheme != "file"
