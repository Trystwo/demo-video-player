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

#: Local media suffixes accepted by the playlist and the window drop handler.
VIDEO_SUFFIXES = tuple(sorted(DEMUXERS))


def demux_for(media: str | Path) -> str:
    """Return the demuxer factory for *media* based on its suffix.

    Raises:
        ValueError: when the container suffix is not supported.
    """
    suffix = Path(media).suffix.lower()
    try:
        return DEMUXERS[suffix]
    except KeyError:
        supported = ", ".join(sorted(DEMUXERS))
        raise ValueError(
            f"unsupported container {suffix or '<none>'!r}; supported: {supported}"
        ) from None


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


def decoder_for(caps_name: str, hw_decode: bool = True) -> str:
    """Return the decoder factory for *caps_name*.

    Hardware decode uses ``mppvideodec`` on the VPU; setting ``hw_decode`` to
    ``False`` selects the software fallback used on a development host.

    Raises:
        ValueError: for codecs without a decoder.
    """
    table = HW_DECODERS if hw_decode else SW_DECODERS
    try:
        return table[caps_name]
    except KeyError:
        supported = ", ".join(sorted(table))
        raise ValueError(
            f"unsupported video codec {caps_name!r}; "
            f"expected one of {supported}"
        ) from None


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
