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

    hw_decode: bool = True
    render_sink: str = "gtk4paintablesink"
    video_convert: str = "auto"
    volume: int = 80
    speed: float = 1.0
    resume: bool = True

    def validate(self) -> None:
        self.hw_decode = _as_bool(self.hw_decode, name="player.hw_decode")
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
        self.speed = round(_clamp(_as_float(self.speed, name="player.speed"), 0.5, 2.0), 2)
        self.resume = _as_bool(self.resume, name="player.resume")


@dataclass
class BeautyConfig:
    """Beauty settings; strengths use a 0-100 user-facing scale."""

    enabled: bool = True
    smoothing: int = 60
    whitening: int = 40
    compare_mode: bool = False
    feather_px: int = 24

    def validate(self) -> None:
        self.enabled = _as_bool(self.enabled, name="beauty.enabled")
        self.smoothing = int(_clamp(_as_int(self.smoothing, name="beauty.smoothing"), 0, 100))
        self.whitening = int(_clamp(_as_int(self.whitening, name="beauty.whitening"), 0, 100))
        self.compare_mode = _as_bool(self.compare_mode, name="beauty.compare_mode")
        self.feather_px = int(_clamp(_as_int(self.feather_px, name="beauty.feather_px"), 0, 64))


@dataclass
class DetectionConfig:
    """NPU face-detection settings."""

    model_path: str = "models/retinaface.rknn"
    interval_frames: int = 3
    max_faces: int = 3
    min_score: float = 0.5
    track_fill: bool = True

    def validate(self) -> None:
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

    @classmethod
    def from_dict(cls, data: Any) -> "Config":
        """Build a config from parsed JSON and validate it."""
        if not isinstance(data, dict):
            raise ConfigError(
                f"configuration root must be a JSON object, got {type(data).__name__}"
            )
        known = {"version", "player", "beauty", "detection", "debug"}
        unknown = sorted(set(data) - known)
        if unknown:
            raise ConfigError(f"configuration contains unknown key(s): {', '.join(unknown)}")

        config = cls(
            player=_build_section(PlayerConfig, data.get("player"), "player"),
            beauty=_build_section(BeautyConfig, data.get("beauty"), "beauty"),
            detection=_build_section(DetectionConfig, data.get("detection"), "detection"),
            debug=_build_section(DebugConfig, data.get("debug"), "debug"),
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
