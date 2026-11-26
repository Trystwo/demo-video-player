"""Configuration contract for the video player.

``configs/player.example.json`` is the single source of truth for player,
beauty and detection settings. Values are validated and clamped on load so that
a hand-edited file can never push the pipeline into an invalid state.

The JSON layout is mirrored one-to-one by the dataclasses below; unknown keys
are rejected on purpose so that typos in a config file fail loudly instead of
being silently ignored.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, fields
import json
from pathlib import Path
from typing import Any

from .pipeline import DECODE_MODES

CONFIG_VERSION = 1

#: Sinks that can be selected for video output.  ``fakesink`` is useful for
#: headless pipeline measurements; ``autovideosink`` is the fallback when no
#: Wayland sink is available.
RENDER_SINKS = ("waylandsink", "gtk4paintablesink", "autovideosink", "fakesink")

#: Video colour-conversion backends for the decoded frames.  ``gl`` uploads
#: the decoder DMABuf into GL memory and converts on the GPU (needed on RK3576:
#: CPU ``videoconvert`` tops out around 15 fps at 1080p); ``sw`` forces the CPU
#: converter.  ``auto`` uses GL when the elements exist and falls back to CPU.
VIDEO_CONVERTS = ("auto", "gl", "sw")

#: Log levels accepted by ``debug.log_level``.
LOG_LEVELS = ("debug", "info", "warning", "error")


class ConfigError(ValueError):
    """Raised when a configuration file is present but cannot be used."""


def default_config_path() -> Path:
    """Return the example configuration shipped with the demo."""
    # src/video_player/config.py -> src -> <demo root>/configs/player.example.json
    return Path(__file__).resolve().parents[2] / "configs" / "player.example.json"


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _as_bool(value: Any, *, name: str) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str) and value.lower() in ("true", "false"):
        return value.lower() == "true"
    raise ConfigError(f"{name} must be a boolean, got {value!r}")


def _as_int(value: Any, *, name: str) -> int:
    if isinstance(value, bool):
        raise ConfigError(f"{name} must be an integer, got {value!r}")
    try:
        return int(value)
    except (TypeError, ValueError):
        raise ConfigError(f"{name} must be an integer, got {value!r}") from None


def _as_float(value: Any, *, name: str) -> float:
    if isinstance(value, bool):
        raise ConfigError(f"{name} must be a number, got {value!r}")
    try:
        return float(value)
    except (TypeError, ValueError):
        raise ConfigError(f"{name} must be a number, got {value!r}") from None


def _as_str(value: Any, *, name: str) -> str:
    if not isinstance(value, str) or not value:
        raise ConfigError(f"{name} must be a non-empty string, got {value!r}")
    return value


@dataclass
class PlayerConfig:
    """Playback settings (decode, output, audio, resume)."""

    #: Decode strategy: ``auto`` prefers the RK3576 VPU and falls back to the
    #: CPU decoder when the VPU path is missing or fails; ``hw`` / ``sw`` pin
    #: the path and fail loudly instead of switching.  Older config files that
    #: still carry ``hw_decode`` are migrated on load.
    decode_mode: str = "auto"
    render_sink: str = "gtk4paintablesink"
    video_convert: str = "auto"
    #: Upper bound for the long side of the GL texture handed to GTK.  GTK4/GSK
    #: paints a texture through a CPU fallback when it is displayed at less
    #: than half of its pixel size; pre-scaling on the GPU keeps the paint
    #: scale >= 0.5 and keeps the on-screen cost proportional to the panel.
    #: 0 disables the pre-scale (full source resolution in GL memory).
    texture_max_dimension: int = 1280
    volume: int = 80
    speed: float = 1.0
    resume: bool = True
    #: Remember recently opened files for the sidebar history.  Kept
    #: separate from ``resume``: the history is useful even when the
    #: auto-seek is turned off, and vice versa.
    recent_files: bool = True

    def validate(self) -> None:
        self.decode_mode = _as_str(self.decode_mode, name="player.decode_mode").lower()
        if self.decode_mode not in DECODE_MODES:
            raise ConfigError(
                f"player.decode_mode must be one of {', '.join(DECODE_MODES)}, "
                f"got {self.decode_mode!r}"
            )
        self.render_sink = _as_str(self.render_sink, name="player.render_sink")
        self.video_convert = _as_str(self.video_convert, name="player.video_convert")
        if self.video_convert not in VIDEO_CONVERTS:
            raise ConfigError(
                f"player.video_convert must be one of {', '.join(VIDEO_CONVERTS)}, "
                f"got {self.video_convert!r}"
            )
        if self.render_sink not in RENDER_SINKS:
            raise ConfigError(
                f"player.render_sink must be one of {', '.join(RENDER_SINKS)}, "
                f"got {self.render_sink!r}"
            )
        self.volume = int(_clamp(_as_int(self.volume, name="player.volume"), 0, 100))
        self.texture_max_dimension = int(
            _clamp(
                _as_int(self.texture_max_dimension, name="player.texture_max_dimension"),
                0,
                8192,
            )
        )
        self.speed = round(_clamp(_as_float(self.speed, name="player.speed"), 0.5, 2.0), 2)
        self.resume = _as_bool(self.resume, name="player.resume")
        self.recent_files = _as_bool(
            self.recent_files, name="player.recent_files"
        )


@dataclass
class BeautyConfig:
    """Beauty settings; strengths use a 0-100 user-facing scale.

    Off by default: this is a video player first, beauty is an opt-in
    ability (B / sidebar switch / --demo).  Keep in sync with
    ``configs/player.example.json``.
    """

    enabled: bool = False
    smoothing: int = 60
    whitening: int = 40
    #: Experimental geometric effects (M5).  Both use the same 0-100 scale as the
    #: colour effects but deform the face instead of filtering it; 0 means off.
    face_slim: float = 0.0
    eye_enlarge: float = 0.0
    compare_mode: bool = False
    feather_px: int = 24
    expand: float = 0.12
    skin_guard: bool = True
    split: float = 0.5
    debug_mask: bool = False

    def validate(self) -> None:
        self.enabled = _as_bool(self.enabled, name="beauty.enabled")
        self.smoothing = int(_clamp(_as_int(self.smoothing, name="beauty.smoothing"), 0, 100))
        self.whitening = int(_clamp(_as_int(self.whitening, name="beauty.whitening"), 0, 100))
        self.face_slim = round(
            _clamp(_as_float(self.face_slim, name="beauty.face_slim"), 0.0, 100.0), 1
        )
        self.eye_enlarge = round(
            _clamp(_as_float(self.eye_enlarge, name="beauty.eye_enlarge"), 0.0, 100.0), 1
        )
        self.compare_mode = _as_bool(self.compare_mode, name="beauty.compare_mode")
        self.feather_px = int(_clamp(_as_int(self.feather_px, name="beauty.feather_px"), 0, 64))
        self.expand = round(
            _clamp(_as_float(self.expand, name="beauty.expand"), 0.0, 1.0), 3
        )
        self.skin_guard = _as_bool(self.skin_guard, name="beauty.skin_guard")
        self.split = round(_clamp(_as_float(self.split, name="beauty.split"), 0.05, 0.95), 3)
        self.debug_mask = _as_bool(self.debug_mask, name="beauty.debug_mask")


@dataclass
class DetectionConfig:
    """NPU face-detection settings.

    Off by default: the one-off ~1.7 s NPU warm-up (model load + a large
    dummy inference) used to run synchronously in ``PlaybackBackend.open()``
    on the GTK main thread, so every click on a video froze the UI for ~3 s.
    Plain playback needs no detector; ``--demo`` and the beauty / face-box
    switches opt in.  Keep in sync with ``configs/player.example.json``.
    """

    enabled: bool = False
    model_path: str = "models/yunet_rk3576.rknn"
    interval_frames: int = 3
    max_faces: int = 3
    min_score: float = 0.5
    track_fill: bool = True
    core_mask: int = 0
    #: How long the last face/mask is kept when one detection comes back empty.
    #: The NPU runs every ``interval_frames`` (about 10 Hz at 1080p30); a single
    #: empty result used to drop the mask for a frame, and the shader snapping
    #: back to a pass-through for one frame reads as a visible flicker.  Holding
    #: the previous faces for a few hundred ms bridges those results; 0 disables
    #: the hold and restores the old strict behaviour.
    hold_ms: int = 600

    def validate(self) -> None:
        self.enabled = _as_bool(self.enabled, name="detection.enabled")
        self.model_path = _as_str(self.model_path, name="detection.model_path")
        self.interval_frames = int(
            _clamp(_as_int(self.interval_frames, name="detection.interval_frames"), 1, 10)
        )
        self.max_faces = int(
            _clamp(_as_int(self.max_faces, name="detection.max_faces"), 1, 8)
        )
        self.min_score = round(
            _clamp(_as_float(self.min_score, name="detection.min_score"), 0.0, 1.0), 2
        )
        self.track_fill = _as_bool(self.track_fill, name="detection.track_fill")
        self.core_mask = int(
            _clamp(_as_int(self.core_mask, name="detection.core_mask"), 0, 3)
        )
        self.hold_ms = int(
            _clamp(_as_int(self.hold_ms, name="detection.hold_ms"), 0, 5000)
        )


@dataclass
class DebugConfig:
    """Diagnostics used while tuning latency and frame rate."""

    show_info_panel: bool = True
    fps_window: int = 60
    log_level: str = "info"

    def validate(self) -> None:
        self.show_info_panel = _as_bool(self.show_info_panel, name="debug.show_info_panel")
        self.fps_window = int(_clamp(_as_int(self.fps_window, name="debug.fps_window"), 1, 600))
        self.log_level = _as_str(self.log_level, name="debug.log_level").lower()
        if self.log_level not in LOG_LEVELS:
            raise ConfigError(
                f"debug.log_level must be one of {', '.join(LOG_LEVELS)}, "
                f"got {self.log_level!r}"
            )


@dataclass
class LibraryConfig:
    """Folders the ui-v5 browse shell scans for the library page.

    Empty by default: a fresh install opens on the empty state, and the
    operator adds folders from the page itself (persisted in the XDG state
    directory, not here).  ``directories`` exists for demo machines that should
    come up with a populated library without anyone touching the UI, which is
    how the 43 demo is usually driven.
    """

    #: Absolute or ``~``-relative folder paths.  Scanned one level deep.
    directories: tuple[str, ...] = ()
    #: Safety valve: a runaway folder must not build a thousand-card grid.
    max_items: int = 500

    def validate(self) -> None:
        raw = self.directories
        if isinstance(raw, (str, bytes)) or not isinstance(raw, (list, tuple)):
            raise ConfigError(
                f"library.directories must be a list of folder paths, got {type(raw).__name__}"
            )
        cleaned: list[str] = []
        for entry in raw:
            if not isinstance(entry, str) or not entry.strip():
                raise ConfigError(
                    f"library.directories must contain non-empty strings, got {entry!r}"
                )
            cleaned.append(entry.strip())
        self.directories = tuple(cleaned)
        self.max_items = int(
            _clamp(_as_int(self.max_items, name="library.max_items"), 1, 5000)
        )


def _migrate_player_section(raw: Any) -> Any:
    """Fold the two-state ``hw_decode`` switch of older builds into ``decode_mode``.

    Older builds ran the VPU unconditionally, so
    ``hw_decode: true`` maps to ``"hw"`` (not ``"auto"``) to keep the old
    behaviour exactly; ``false`` maps to ``"sw"``.
    """
    if not isinstance(raw, dict) or "hw_decode" not in raw:
        return raw
    migrated = dict(raw)
    legacy = _as_bool(migrated.pop("hw_decode"), name="player.hw_decode")
    migrated.setdefault("decode_mode", "hw" if legacy else "sw")
    return migrated


def _build_section(section_cls: type, raw: Any, label: str) -> Any:
    """Instantiate one config section, rejecting unknown keys."""
    if raw is None:
        return section_cls()
    if not isinstance(raw, dict):
        raise ConfigError(f"{label} must be a JSON object, got {type(raw).__name__}")
    known = {f.name for f in fields(section_cls)}
    unknown = sorted(set(raw) - known)
    if unknown:
        raise ConfigError(
            f"{label} contains unknown key(s): {', '.join(unknown)}; "
            f"valid keys are {', '.join(sorted(known))}"
        )
    return section_cls(**{key: raw[key] for key in raw})


@dataclass
class Config:
    """Root configuration object: one section per concern."""

    player: PlayerConfig = field(default_factory=PlayerConfig)
    beauty: BeautyConfig = field(default_factory=BeautyConfig)
    detection: DetectionConfig = field(default_factory=DetectionConfig)
    debug: DebugConfig = field(default_factory=DebugConfig)
    library: LibraryConfig = field(default_factory=LibraryConfig)
    version: int = CONFIG_VERSION

    def validate(self) -> None:
        if not isinstance(self.version, int) or self.version != CONFIG_VERSION:
            raise ConfigError(
                f"unsupported config version {self.version!r}; "
                f"this build expects version {CONFIG_VERSION}"
            )
        self.player.validate()
        self.beauty.validate()
        self.detection.validate()
        self.debug.validate()
        self.library.validate()

    @classmethod
    def from_dict(cls, data: Any) -> "Config":
        """Build a config from parsed JSON and validate it."""
        if not isinstance(data, dict):
            raise ConfigError(
                f"configuration root must be a JSON object, got {type(data).__name__}"
            )
        known = {"version", "player", "beauty", "detection", "debug", "library"}
        unknown = sorted(set(data) - known)
        if unknown:
            raise ConfigError(f"configuration contains unknown key(s): {', '.join(unknown)}")

        config = cls(
            player=_build_section(
                PlayerConfig, _migrate_player_section(data.get("player")), "player"
            ),
            beauty=_build_section(BeautyConfig, data.get("beauty"), "beauty"),
            detection=_build_section(DetectionConfig, data.get("detection"), "detection"),
            debug=_build_section(DebugConfig, data.get("debug"), "debug"),
            library=_build_section(LibraryConfig, data.get("library"), "library"),
            version=_as_int(data.get("version", CONFIG_VERSION), name="version"),
        )
        config.validate()
        return config

    @classmethod
    def load(cls, path: Path | str | None = None) -> "Config":
        """Load a config file; fall back to defaults when it is absent."""
        resolved = Path(path) if path is not None else default_config_path()
        if not resolved.exists():
            if path is not None:
                raise ConfigError(f"configuration file not found: {resolved}")
            return cls()
        try:
            data = json.loads(resolved.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ConfigError(f"{resolved} is not valid JSON: {exc}") from exc
        return cls.from_dict(data)

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serialisable representation of the config."""
        return asdict(self)

    def save(self, path: Path | str) -> Path:
        """Write the config to *path* (the settings UI will use this in M3)."""
        resolved = Path(path)
        resolved.parent.mkdir(parents=True, exist_ok=True)
        resolved.write_text(
            json.dumps(self.to_dict(), indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        return resolved
