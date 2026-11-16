"""Beauty parameters, GLES shaders and face-mask generation.

The beauty pass runs on the Mali GPU through a ``glshader``-based element.  The
shader receives the decoded RGBA frame plus a per-pixel face mask; smoothing and
whitening are blended only where the mask is non-zero, and the mask edges are
feathered so that the face/background border stays invisible (acceptance A3).

This module is deliberately free of GTK/GStreamer imports so that it can be unit
tested on a development host.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Iterable, Sequence

from .config import BeautyConfig

if TYPE_CHECKING:  # pragma: no cover - typing only
    from .face import Face

#: Number of texels the smoothing kernel reaches sideways at full strength.
_MAX_KERNEL_TEXELS = 3.0

#: Vertex shader for the ``glshader`` beauty element.  ``gst-launch`` supplies
#: the position/texcoord attributes; the texture matrix is applied by the
#: element itself (verify against the board's glshader version in M3).
BEAUTY_VERTEX_SHADER = """#version 100
attribute vec4 a_position;
attribute vec2 a_texcoord;
uniform mat4 u_matrix;
varying vec2 v_texcoord;
void main() {
    gl_Position = u_matrix * a_position;
    v_texcoord = a_texcoord;
}
"""

#: Fragment shader with 9-tap smoothing and highlight-preserving whitening.
#: ``u_tex`` is the input frame, ``u_mask`` the single-channel face mask.
BEAUTY_FRAGMENT_SHADER = """#version 100
precision highp float;
varying vec2 v_texcoord;
uniform sampler2D u_tex;
uniform sampler2D u_mask;
uniform vec2 u_texel_size;
uniform float u_smoothing;
uniform float u_whitening;

vec3 sample_tex(vec2 uv) {
    return texture2D(u_tex, uv).rgb;
}

void main() {
    vec4 base = texture2D(u_tex, v_texcoord);
    float mask = clamp(texture2D(u_mask, v_texcoord).r, 0.0, 1.0);
    if (mask <= 0.001) {
        gl_FragColor = base;
        return;
    }

    float radius = 1.0 + 2.0 * u_smoothing;
    vec2 stepv = u_texel_size * radius;
    vec3 blurred = sample_tex(v_texcoord) * 0.24;
    blurred += sample_tex(v_texcoord + vec2(stepv.x, 0.0)) * 0.16;
    blurred += sample_tex(v_texcoord - vec2(stepv.x, 0.0)) * 0.16;
    blurred += sample_tex(v_texcoord + vec2(0.0, stepv.y)) * 0.16;
    blurred += sample_tex(v_texcoord - vec2(0.0, stepv.y)) * 0.16;
    blurred += sample_tex(v_texcoord + stepv) * 0.03;
    blurred += sample_tex(v_texcoord - stepv) * 0.03;
    blurred += sample_tex(v_texcoord + vec2(stepv.x, -stepv.y)) * 0.03;
    blurred += sample_tex(v_texcoord + vec2(-stepv.x, stepv.y)) * 0.03;

    vec3 rgb = mix(base.rgb, blurred, 0.85 * u_smoothing * mask);

    // Whitening: lift toward white, then restore a little saturation so the
    // result does not look grey (keeps the "obvious but natural" A3 balance).
    vec3 lifted = mix(rgb, vec3(1.0), 0.18 * u_whitening * mask);
    float luma = dot(lifted, vec3(0.299, 0.587, 0.114));
    lifted = mix(vec3(luma), lifted, 1.0 - 0.15 * u_whitening * mask);
    gl_FragColor = vec4(lifted, base.a);
}
"""


@dataclass(frozen=True)
class BeautyParams:
    """Normalised beauty strengths in the 0.0-1.0 range."""

    smoothing: float = 0.6
    whitening: float = 0.4

    @classmethod
    def from_config(cls, config: BeautyConfig) -> "BeautyParams":
        return cls(
            smoothing=_clamp01(config.smoothing / 100.0),
            whitening=_clamp01(config.whitening / 100.0),
        )

    def is_identity(self) -> bool:
        """True when the shader would leave the frame untouched."""
        return self.smoothing <= 0.0 and self.whitening <= 0.0

    def uniforms(self) -> dict[str, float]:
        """Uniform values pushed to the shader for this parameter set."""
        return {"u_smoothing": self.smoothing, "u_whitening": self.whitening}


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def _rect_of(face: "Face | Sequence[float]") -> tuple[float, float, float, float]:
    """Accept either a ``Face`` or an ``(x, y, width, height)`` tuple."""
    if hasattr(face, "as_rect"):
        return face.as_rect()  # type: ignore[union-attr]
    values = tuple(float(v) for v in face)
    if len(values) != 4:
        raise ValueError(f"expected (x, y, width, height), got {values!r}")
    return values  # type: ignore[return-value]


def build_face_mask(
    faces: Iterable["Face | Sequence[float]"],
    width: int,
    height: int,
    feather_px: int = 24,
) -> "object":
    """Rasterise feathered face rectangles into a float32 ``(height, width)`` mask.

    The mask is 1.0 inside a face rectangle and falls off with a smoothstep over
    ``feather_px`` pixels at the border, so the GPU pass blends the beautified
    face into the untouched frame without a visible seam.

    NumPy is a board runtime dependency; it is imported lazily so that the rest
    of the package (and the test suite) works on hosts without NumPy.
    """
    try:
        import numpy as np
    except ImportError as exc:  # pragma: no cover - depends on the host
        raise RuntimeError(
            "NumPy is required to build face masks; install requirements.txt"
        ) from exc

    if width <= 0 or height <= 0:
        raise ValueError(f"frame size must be positive, got {width}x{height}")

    mask = np.zeros((height, width), dtype=np.float32)
    feather = max(1, int(feather_px))

    for face in faces:
        x, y, w, h = _rect_of(face)
        x0 = max(0, min(width, int(round(x))))
        y0 = max(0, min(height, int(round(y))))
        x1 = max(0, min(width, int(round(x + w))))
        y1 = max(0, min(height, int(round(y + h))))
        if x1 <= x0 or y1 <= y0:
            continue
        patch = mask[y0:y1, x0:x1]
        rows, cols = patch.shape
        # Distance in pixels to the nearest rectangle border, per axis.
        ys = np.minimum(np.arange(rows), np.arange(rows)[::-1]).astype(np.float32)
        xs = np.minimum(np.arange(cols), np.arange(cols)[::-1]).astype(np.float32)
        dist = np.minimum(ys[:, None], xs[None, :])
        soft = np.clip(dist / float(min(feather, max(1, min(rows, cols) // 2))), 0.0, 1.0)
        soft = soft * soft * (3.0 - 2.0 * soft)  # smoothstep
        np.maximum(patch, soft, out=patch)

    return mask
