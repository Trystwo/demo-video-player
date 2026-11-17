"""Beauty parameters, GLES shaders and face-mask helpers.

The beauty pass runs on the Mali GPU through the ``glshader`` element.  The
shader receives the decoded RGBA frame and a *procedural* face mask: the M2
detector publishes up to :data:`BEAUTY_MAX_FACES` bounding boxes, the backend
forwards them as shader uniforms, and the fragment shader builds a feathered
rounded-rectangle mask around each box.  Smoothing (edge-preserving bilateral
approximation) and whitening are blended only where the mask is non-zero, so the
background/hair stay untouched and the face border has no seam (acceptance A3).

Why a procedural mask instead of the rasterised mask texture on the CPU?
``glshader`` binds exactly one input texture through its ``tex`` sampler and has
no API to bind a second one from Python; a mask *texture* would need a second GL
pass plus a custom ``GstGLShader`` (C plugin).  The mask is therefore described
by a handful of floats (box + feather + expansion), which is both cheaper (a few
dozen bytes per frame instead of a full-size mask upload) and resolution
independent.  ``build_face_mask`` is kept for the CPU reference used by the
``scripts/mask_preview.py`` regression tool and by the unit tests.

This module is deliberately free of GTK/GStreamer imports so that it can be unit
tested on a development host.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Iterable, Sequence

from .config import BeautyConfig

if TYPE_CHECKING:  # pragma: no cover - typing only
    from .face import Face

#: Maximum number of faces the shader can handle (uniforms are unrolled).
BEAUTY_MAX_FACES = 3

#: Face-box components packed as shader uniforms, in normalised frame coords.
_FACE_COMPONENTS = ("x", "y", "w", "h")

#: Fragment shader for the ``glshader`` beauty element.
#:
#: Contract (verified on the RK3576 board, GStreamer 1.26.2):
#:
#: * the element supplies the vertex stage and the input sampler is named
#:   ``tex``; ``v_texcoord`` is the interpolated texture coordinate in [0, 1];
#: * a GLSL ES fragment shader must declare its own ``precision`` (the element
#:   does not inject one) and must not rely on ``#version``;
#: * only ``float`` uniforms reach the GLSL program: a ``float`` set as a
#:   ``gdouble`` from Python is silently dropped, so the backend wraps every
#:   value in a ``GObject.Value`` of ``G_TYPE_FLOAT``.
BEAUTY_FRAGMENT_SHADER = """
precision highp float;

varying vec2 v_texcoord;
uniform sampler2D tex;

uniform float u_texel_x;
uniform float u_texel_y;
uniform float u_full_w;
uniform float u_full_h;
uniform float u_smoothing;
uniform float u_whitening;
uniform float u_active;
uniform float u_compare;
uniform float u_split;
uniform float u_feather;
uniform float u_expand;
uniform float u_skin;
uniform float u_debug;
uniform float u_face0_x;
uniform float u_face0_y;
uniform float u_face0_w;
uniform float u_face0_h;
uniform float u_face1_x;
uniform float u_face1_y;
uniform float u_face1_w;
uniform float u_face1_h;
uniform float u_face2_x;
uniform float u_face2_y;
uniform float u_face2_w;
uniform float u_face2_h;

// Signed distance to a rounded box centred on the origin (negative inside).
float rounded_box(vec2 p, vec2 half_size, float radius) {
    vec2 q = abs(p) - max(half_size - vec2(radius), 0.0);
    return length(max(q, 0.0)) + min(max(q.x, q.y), 0.0) - radius;
}

// Feathered mask for one detected face, in texture pixel coordinates.
float face_mask(vec2 px, vec4 box) {
    if (box.z <= 0.0 || box.w <= 0.0) {
        return 0.0;
    }
    vec2 size = vec2(u_full_w, u_full_h);
    vec2 centre = (box.xy + box.zw * 0.5) * size;
    vec2 half_size = max(box.zw * 0.5 * size * (1.0 + u_expand), vec2(1.0));
    float radius = min(half_size.x, half_size.y) * 0.45;
    float dist = rounded_box(px - centre, half_size, radius);
    return 1.0 - smoothstep(0.0, max(u_feather, 1.0), dist);
}

// Loose YCbCr skin gate: keeps hair, clothing and background inside the box
// sharp instead of smearing them along with the face.
float skin_gate(vec3 rgb) {
    float y = dot(rgb, vec3(0.299, 0.587, 0.114));
    float cb = 0.5 - 0.168736 * rgb.r - 0.331264 * rgb.g + 0.5 * rgb.b;
    float cr = 0.5 + 0.5 * rgb.r - 0.418688 * rgb.g - 0.081312 * rgb.b;
    float gate = smoothstep(0.26, 0.33, cb) * (1.0 - smoothstep(0.45, 0.52, cb));
    gate *= smoothstep(0.48, 0.55, cr) * (1.0 - smoothstep(0.66, 0.73, cr));
    gate *= smoothstep(0.10, 0.22, y);
    return gate;
}

vec3 bilateral_tap(vec2 uv, vec3 centre, vec2 offset, float spatial, inout float weight_sum) {
    vec3 sample_rgb = texture2D(tex, uv + offset).rgb;
    vec3 delta = sample_rgb - centre;
    float weight = spatial / (1.0 + 40.0 * dot(delta, delta));
    weight_sum += weight;
    return sample_rgb * weight;
}

// 8-neighbour bilateral approximation: gaussian-ish spatial weights, rational
// range weights so that strong edges are preserved (cheap stand-in for exp()).
vec3 smooth_face(vec2 uv, vec3 centre) {
    float radius = 1.5 + 7.0 * u_smoothing;
    vec2 stepv = vec2(u_texel_x, u_texel_y) * radius;
    vec3 sum = centre * 0.30;
    float weight_sum = 0.30;
    float d = 0.13;
    float e = 0.07;
    sum += bilateral_tap(uv, centre, vec2(stepv.x, 0.0), d, weight_sum);
    sum += bilateral_tap(uv, centre, vec2(-stepv.x, 0.0), d, weight_sum);
    sum += bilateral_tap(uv, centre, vec2(0.0, stepv.y), d, weight_sum);
    sum += bilateral_tap(uv, centre, vec2(0.0, -stepv.y), d, weight_sum);
    sum += bilateral_tap(uv, centre, vec2(stepv.x, stepv.y), e, weight_sum);
    sum += bilateral_tap(uv, centre, vec2(-stepv.x, stepv.y), e, weight_sum);
    sum += bilateral_tap(uv, centre, vec2(stepv.x, -stepv.y), e, weight_sum);
    sum += bilateral_tap(uv, centre, vec2(-stepv.x, -stepv.y), e, weight_sum);
    return sum / max(weight_sum, 0.0001);
}

// Lift toward white without blowing out the highlights, then restore a little
// saturation so the skin does not turn grey.
vec3 whiten(vec3 rgb, float amount) {
    float luma = dot(rgb, vec3(0.299, 0.587, 0.114));
    vec3 lifted = rgb + (1.0 - rgb) * (0.22 * amount * (1.0 - 0.45 * luma));
    float lifted_luma = dot(lifted, vec3(0.299, 0.587, 0.114));
    return mix(vec3(lifted_luma), lifted, 1.0 - 0.18 * amount);
}

void main() {
    vec4 base = texture2D(tex, v_texcoord);
    vec2 px = v_texcoord * vec2(u_full_w, u_full_h);

    float mask = face_mask(px, vec4(u_face0_x, u_face0_y, u_face0_w, u_face0_h));
    mask = max(mask, face_mask(px, vec4(u_face1_x, u_face1_y, u_face1_w, u_face1_h)));
    mask = max(mask, face_mask(px, vec4(u_face2_x, u_face2_y, u_face2_w, u_face2_h)));
    mask *= mix(1.0, 0.10 + 0.90 * skin_gate(base.rgb), u_skin);

    if (u_debug > 0.5) {
        gl_FragColor = vec4(vec3(mask), 1.0);
        return;
    }
    if (u_active <= 0.5) {
        gl_FragColor = base;
        return;
    }

    vec3 rgb = base.rgb;
    if (mask > 0.002) {
        float smoothing = 0.90 * u_smoothing * mask;
        if (smoothing > 0.002) {
            rgb = mix(rgb, smooth_face(v_texcoord, base.rgb), smoothing);
        }
        float whitening = u_whitening * mask;
        if (whitening > 0.002) {
            rgb = whiten(rgb, whitening);
        }
    }

    // Compare mode: left half stays original, right half is beautified, with a
    // thin separator so the split is obvious in screenshots (F9).
    if (u_compare > 0.5) {
        if (v_texcoord.x < u_split) {
            rgb = base.rgb;
        }
        if (abs(px.x - u_split * u_full_w) < 1.5) {
            rgb = vec3(1.0);
        }
    }
    gl_FragColor = vec4(rgb, base.a);
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

    def uniforms(
        self,
        *,
        frame_size: tuple[int, int] = (0, 0),
        texture_size: tuple[int, int] | None = None,
        faces: Iterable["Face | Sequence[float]"] = (),
        rotation: int = 0,
        compare_mode: bool = False,
        split: float = 0.5,
        feather_px: float = 24.0,
        expand: float = 0.12,
        skin_guard: bool = True,
        active: bool = True,
        debug: bool = False,
    ) -> dict[str, float]:
        """Uniform values for one frame.

        Args:
            frame_size: size of the *displayed* frame, i.e. the coordinate space
                of the detection boxes (already rotated upright).
            texture_size: size of the frame the shader samples.  Defaults to
                ``frame_size``; 90/270 degree display matrices swap the axes.
            faces: detected faces in display coordinates.
            rotation: clockwise display rotation applied to the decoded frame
                before it reaches the screen (the detector rotates by the same
                amount, so boxes must be mapped back for the shader).
        """
        frame_w, frame_h = (max(0, int(v)) for v in frame_size)
        texture_w, texture_h = (
            max(0, int(v))
            for v in (texture_size or frame_size)
        )
        values: dict[str, float] = {
            "u_texel_x": 1.0 / texture_w if texture_w else 0.0,
            "u_texel_y": 1.0 / texture_h if texture_h else 0.0,
            "u_full_w": float(texture_w),
            "u_full_h": float(texture_h),
            "u_smoothing": self.smoothing,
            "u_whitening": self.whitening,
            "u_active": 1.0 if active and not self.is_identity() else 0.0,
            "u_compare": 1.0 if compare_mode else 0.0,
            "u_split": _clamp01(split),
            "u_feather": max(0.0, float(feather_px)),
            "u_expand": max(0.0, float(expand)),
            "u_skin": 1.0 if skin_guard else 0.0,
            "u_debug": 1.0 if debug else 0.0,
        }
        boxes = normalise_face_boxes(
            faces, frame_w, frame_h, rotation=rotation, limit=BEAUTY_MAX_FACES
        )
        for index in range(BEAUTY_MAX_FACES):
            box = boxes[index] if index < len(boxes) else (0.0, 0.0, 0.0, 0.0)
            for component, value in zip(_FACE_COMPONENTS, box):
                values[f"u_face{index}_{component}"] = float(value)
        return values


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


def face_rect_to_texture(
    rect: tuple[float, float, float, float], rotation: int
) -> tuple[float, float, float, float]:
    """Map a normalised face rectangle from display space to texture space.

    The detector rotates every decoded frame clockwise by the stream's display
    matrix before running YuNet, so its boxes live in the *upright* frame while
    the shader samples the *decoded* (unrotated) texture.  This applies the
    inverse rotation to a normalised ``(x, y, w, h)`` rectangle.
    """
    x, y, width, height = rect
    rotation %= 360
    if rotation == 90:
        return (y, 1.0 - x - width, height, width)
    if rotation == 180:
        return (1.0 - x - width, 1.0 - y - height, width, height)
    if rotation == 270:
        return (1.0 - y - height, x, height, width)
    return (x, y, width, height)


def normalise_face_boxes(
    faces: Iterable["Face | Sequence[float]"],
    frame_width: int,
    frame_height: int,
    *,
    rotation: int = 0,
    limit: int = BEAUTY_MAX_FACES,
) -> list[tuple[float, float, float, float]]:
    """Convert pixel face boxes into texture-space normalised rectangles."""
    if frame_width <= 0 or frame_height <= 0:
        return []
    boxes: list[tuple[float, float, float, float]] = []
    for face in faces:
        x, y, width, height = _rect_of(face)
        if width <= 0 or height <= 0:
            continue
        rect = (
            x / frame_width,
            y / frame_height,
            width / frame_width,
            height / frame_height,
        )
        boxes.append(face_rect_to_texture(rect, rotation))
        if len(boxes) >= limit:
            break
    return boxes


def build_face_mask(
    faces: Iterable["Face | Sequence[float]"],
    width: int,
    height: int,
    feather_px: int = 24,
) -> "object":
    """Rasterise feathered face rectangles into a float32 ``(height, width)`` mask.

    CPU reference implementation of the shader's mask: 1.0 inside a face
    rectangle, falling off with a smoothstep over ``feather_px`` pixels at the
    border.  Used by ``scripts/mask_preview.py`` to sanity-check the shader
    output and by the unit tests.

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
