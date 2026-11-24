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

import math
from dataclasses import dataclass, field
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
// Experimental warp (M5): normalised 0-1 strengths and the primary face's
// geometry.  Every position below is normalised in *display* UV space (the
// upright frame); the shader rotates the texture UV into display space itself,
// so the maths is independent of the stream's display rotation.  The warp is
// resolution independent (the UI texture and the recorded texture have
// different pixel sizes but the same UVs).
uniform float u_slim;
uniform float u_eyes;
uniform float u_warp_ready;
uniform float u_warp_cx;
uniform float u_warp_cy;
uniform float u_warp_face_w;
uniform float u_warp_face_h;
uniform float u_warp_lx0;
uniform float u_warp_ly0;
uniform float u_warp_lx1;
uniform float u_warp_ly1;
uniform float u_warp_rot;
// M5.2 face shape (M5.2 review: the box mask smeared whatever sat inside the
// rectangle, and the box warp dragged the background along the box edge).
// All positions below are *display pixels*: the ellipse is fitted to the five
// landmarks, which the detector reports in the upright frame.
//   u_shape_ready  1 when the five points are available, else the shader falls
//                  back to the M5 box mask/gate (no landmark -> no jump);
//   u_shape_cx/cy  ellipse centre, u_shape_a/b its half width/height;
//   u_shape_eye_y  eye line (the slim warp only moves below it);
//   u_shape_d      interocular distance, the scale for every ratio;
//   u_slim_peak    peak slim displacement in display px at strength 1.0;
//   u_disp_w/h     displayed frame size, to turn display UV into pixels.
//                  The oval and the neck reuse u_feather as their soft edge.
uniform float u_shape_ready;
uniform float u_shape_cx;
uniform float u_shape_cy;
uniform float u_shape_a;
uniform float u_shape_b;
uniform float u_shape_eye_y;
uniform float u_shape_d;
uniform float u_slim_peak;
uniform float u_disp_w;
uniform float u_disp_h;
// Per-face ovals for the secondary faces (P0-b).  A secondary face has its own
// five landmarks from YuNet; when they pass the same geometry checks it gets an
// oval instead of the M5 box mask, so the same skin gate applies to every face.
// No eye/slim uniforms: the warp stays primary-only in this step.
//   u_shape1_ready 1 when face 1 has a plausible oval, else its box is used
//                  (and a degenerate face has already had that box zeroed by
//                  BeautyParams.uniforms, so it paints nothing);
//   u_shape2_*     the same for face 2.
uniform float u_shape1_ready;
uniform float u_shape1_cx;
uniform float u_shape1_cy;
uniform float u_shape1_a;
uniform float u_shape1_b;
uniform float u_shape2_ready;
uniform float u_shape2_cx;
uniform float u_shape2_cy;
uniform float u_shape2_a;
uniform float u_shape2_b;

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

// --- M5.2 face shape mask ------------------------------------------------
// The M5 mask was a rounded rectangle around the detector box, which contains
// the hairline, the glasses temples, the ear and parts of the background; a
// 10% floor then applied smoothing/whitening to all of it.  The shape mask
// replaces it with a face oval fitted to the five landmarks (top edge on the
// hairline, bottom on the chin) times a colour partition in CIELAB, so only
// skin is touched.  On this footage Lab separates skin (a* ~ 7..9, b* ~ 12..13)
// from hair (a* ~ 0, b* ~ 9), glasses frames (a* ~ 5, b* ~ 9), lips (a* ~ 13)
// and the grey-blue background (a* ~ 0); the old YCbCr gate scored dark hair at
// 0.64, i.e. "half skin".

// sRGB -> linear light (IEC 61966-2-1).  Two branches instead of pow(2.2):
// the exact curve keeps the Lab numbers identical to the CPU reference.
vec3 srgb_to_linear(vec3 c) {
    vec3 low = c / 12.92;
    vec3 high = pow((c + 0.055) / 1.055, vec3(2.4));
    return mix(low, high, step(vec3(0.04045), c));
}

// CIELAB f(): cube root above the knee, linear below it.
vec3 lab_f(vec3 t) {
    vec3 root = pow(max(t, vec3(0.0)), vec3(1.0 / 3.0));
    return mix(7.787 * t + 16.0 / 116.0, root, step(vec3(0.008856), t));
}

// RGB -> (L*/100, a*, b*) with the D65 white point, same as cv2's BGR2LAB.
vec3 rgb_to_lab(vec3 rgb) {
    vec3 lin = srgb_to_linear(clamp(rgb, 0.0, 1.0));
    float x = dot(lin, vec3(0.4124564, 0.3575761, 0.1804375)) / 0.95047;
    float y = dot(lin, vec3(0.2126729, 0.7151522, 0.0721750));
    float z = dot(lin, vec3(0.0193339, 0.1191920, 0.9503041)) / 1.08883;
    vec3 f = lab_f(vec3(x, y, z));
    return vec3((116.0 * f.y - 16.0) / 100.0, 500.0 * (f.x - f.y), 200.0 * (f.y - f.z));
}

// Skin probability from Lab: (loose, strict).
//   loose  drives smoothing: rejects hair, frames, background;
//   strict drives whitening: additionally rejects lips (a* > 11, whitening
//          them greys the mouth out) and speculars (L* > 0.86), so highlights
//          and dark hair are never lifted.
// Both are products of smoothsteps, i.e. wide and soft -- a hard threshold
// would flicker with detector/Lab noise from frame to frame.
vec2 skin_lab(vec3 rgb) {
    vec3 lab = rgb_to_lab(rgb);
    float l = lab.x;
    float a = lab.y;
    float b = lab.z;
    float loose = smoothstep(2.5, 5.0, a) * (1.0 - smoothstep(15.0, 18.0, a));
    loose *= smoothstep(7.0, 9.5, b) * (1.0 - smoothstep(20.0, 25.0, b));
    loose *= smoothstep(0.18, 0.28, l);
    float strict = smoothstep(4.5, 6.5, a) * (1.0 - smoothstep(11.0, 13.0, a));
    strict *= smoothstep(9.5, 11.5, b) * (1.0 - smoothstep(17.0, 21.0, b));
    strict *= smoothstep(0.25, 0.35, l) * (1.0 - smoothstep(0.86, 0.96, l));
    return vec2(loose, strict);
}

// Normalised radius of one face oval: < 1 inside, 1 on the outline.
float oval_radius_at(vec2 disp_px, float cx, float cy, float a, float b) {
    vec2 rel = (disp_px - vec2(cx, cy)) / max(vec2(a, b), vec2(1.0));
    return length(rel);
}

// Primary-face wrapper, kept for the warp gate / slim code below.
float oval_radius(vec2 disp_px) {
    return oval_radius_at(disp_px, u_shape_cx, u_shape_cy, u_shape_a, u_shape_b);
}

// Neck continuation of one oval.  The oval ends on the chin, so without this
// the jaw/neck boundary keeps its original tone and the frame shows a visible
// step.  A feather-matched rectangle below the chin fixes that; the Lab gate
// still keeps collars and background out of it.
float neck_mask_at(vec2 disp_px, float cx, float cy, float a, float b) {
    vec2 chin = vec2(cx, cy + b);
    vec2 centre = chin + vec2(0.0, 0.13 * b);
    vec2 half_size = max(vec2(0.45 * a, 0.29 * b), vec2(1.0));
    float d = rounded_box(
        disp_px - centre, half_size, min(half_size.x, half_size.y) * 0.45
    );
    return 1.0 - smoothstep(0.0, max(u_feather, 1.0), d);
}

float neck_mask(vec2 disp_px) {
    return neck_mask_at(disp_px, u_shape_cx, u_shape_cy, u_shape_a, u_shape_b);
}

// Feathered face oval + neck for one face, in display pixels.  The feather is
// measured in pixels along the *horizontal* semi-axis, matching u_feather's M5
// meaning.
float face_shape_mask_at(vec2 disp_px, float cx, float cy, float a, float b) {
    float edge = (oval_radius_at(disp_px, cx, cy, a, b) - 1.0) * max(a, 1.0);
    float oval = 1.0 - smoothstep(0.0, max(u_feather, 1.0), edge);
    return max(oval, neck_mask_at(disp_px, cx, cy, a, b));
}

float face_shape_mask(vec2 disp_px) {
    return face_shape_mask_at(disp_px, u_shape_cx, u_shape_cy, u_shape_a, u_shape_b);
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

// --- M5 experimental warp -------------------------------------------------
// Both effects are backwards warps evaluated in *display* space: texture UV ->
// display UV, move the sample point, then map back.  Display space keeps the
// geometry independent of the stream's rotation metadata (the stored texture
// may be upside down or sideways) and lets the centroid/landmark maths use the
// upright coordinates the detector produced.
//
// Face slim compresses the outer part of the lower face towards the vertical
// centre line, so the whole jawline moves inwards smoothly.  (A local push
// around a jaw control point only dents the cheek: M5 review feedback.)
// Eye enlarge is a mild anisotropic magnification centred on each eye with a
// wide falloff, so the eyelid/brow boundary blends into untouched pixels.
vec2 tex_to_display(vec2 uv) {
    float r = u_warp_rot;
    if (r > 45.0 && r < 135.0) {
        return vec2(1.0 - uv.y, uv.x);
    }
    if (r > 135.0 && r < 225.0) {
        return vec2(1.0 - uv.x, 1.0 - uv.y);
    }
    if (r > 225.0 && r < 315.0) {
        return vec2(uv.y, 1.0 - uv.x);
    }
    return uv;
}

vec2 display_to_tex(vec2 uv) {
    float r = u_warp_rot;
    if (r > 45.0 && r < 135.0) {
        return vec2(uv.y, 1.0 - uv.x);
    }
    if (r > 135.0 && r < 225.0) {
        return vec2(1.0 - uv.x, 1.0 - uv.y);
    }
    if (r > 225.0 && r < 315.0) {
        return vec2(1.0 - uv.y, uv.x);
    }
    return uv;
}

// Gate the eye warp at the face outline.  Slimming has a separate support gate:
// it must stay active on the outline and fade out in a narrow exterior band so
// the visible jaw can move inward without a hard seam.
float warp_gate(vec2 px, vec2 disp_px, vec4 box, float face_radius) {
    if (u_shape_ready > 0.5) {
        return 1.0 - smoothstep(0.94, 1.00, face_radius);
    }
    if (box.z <= 0.0 || box.w <= 0.0) {
        return 0.0;
    }
    vec2 size = vec2(u_full_w, u_full_h);
    vec2 centre = (box.xy + box.zw * 0.5) * size;
    vec2 half_size = max(box.zw * 0.5 * size, vec2(1.0));
    float radius = min(half_size.x, half_size.y) * 0.45;
    float dist = rounded_box(px - centre, half_size, radius);
    return 1.0 - smoothstep(-2.0, 6.0, dist);
}

// The slim field reaches the face edge and fades out just outside it.  This
// gate *is* that exterior falloff: it is 1 on the contour, so the visible jaw
// can move, and drops to 0 over a smooth band.  It is multiplied into the
// displacement, so the field is continuous where the gate ends; a gate that
// reached 0 at the contour (the old M5.2 one) pinned the outline instead.
float slim_support_gate(float face_radius) {
    float outside_band_px = max(3.0 * u_slim_peak * u_slim, 24.0);
    float outer_radius = 1.0 + outside_band_px / max(u_shape_a, 1.0);
    return 1.0 - smoothstep(1.0, outer_radius, face_radius);
}

// Display-space slim of the lower face: a horizontal pull on the jaw sides plus
// an upward lift under the chin.  This is an inverse sample map, so sampling
// further out on the sides narrows the visible jaw, and sampling lower under the
// chin lifts the almost horizontal chin outline.  A horizontal-only field cannot
// move that edge at all, and its row-to-row collapse (several pixels of
// displacement lost per row) read as a crease across the chin.
vec2 face_slim_offset(vec2 disp_px) {
    float a = max(u_shape_a, 1.0);
    float b = max(u_shape_b, 1.0);
    float rel_x = (disp_px.x - u_shape_cx) / a;
    float rel_y = (disp_px.y - u_shape_cy) / b;
    // Nothing above the eye line moves.
    float below_eyes = smoothstep(0.05, 0.55, (disp_px.y - u_shape_eye_y) / max(u_shape_d, 1.0));
    float side = smoothstep(0.15, 0.95, abs(rel_x)) * below_eyes;
    // The lift is confined to the chin zone and fades out towards the jaw
    // corners, so the outline turns instead of stepping.
    float lift = smoothstep(0.55, 0.98, rel_y)
        * (1.0 - smoothstep(0.55, 1.00, abs(rel_x))) * below_eyes;
    float amount = u_slim_peak * u_slim;
    return vec2((rel_x < 0.0 ? -1.0 : 1.0) * amount * side, amount * 0.85 * lift);
}

// Mild anisotropic magnification around one eye centre.  The falloff reaches 0
// at the ellipse edge (0.72 aspect: wider than tall) so the eyelid and brow
// pixels blend into the surrounding skin without shearing.
vec2 eye_enlarge_offset(vec2 disp, vec2 eye) {
    vec2 delta = disp - eye;
    vec2 radii = max(
        vec2(0.16 * u_warp_face_w, 0.16 * u_warp_face_w * 0.72),
        vec2(0.0001)
    );
    float radius = length(delta / radii);
    float falloff = 1.0 - smoothstep(0.20, 1.0, radius);
    return -delta * (0.22 * u_eyes * falloff);
}

vec2 warp_uv(vec2 texcoord) {
    if (u_warp_ready < 0.5 || (u_slim <= 0.001 && u_eyes <= 0.001)) {
        return texcoord;
    }
    vec2 px = texcoord * vec2(u_full_w, u_full_h);
    vec2 disp = tex_to_display(texcoord);
    vec2 disp_size = max(vec2(u_disp_w, u_disp_h), vec2(1.0));
    vec2 disp_px = disp * disp_size;
    float face_radius = u_shape_ready > 0.5 ? oval_radius(disp_px) : 0.0;
    float eye_gate = warp_gate(px, disp_px, vec4(u_face0_x, u_face0_y, u_face0_w, u_face0_h), face_radius);
    float slim_gate = u_slim > 0.001 && u_shape_ready > 0.5
        ? slim_support_gate(face_radius)
        : 0.0;
    if ((u_slim <= 0.001 || slim_gate <= 0.001) &&
        (u_eyes <= 0.001 || eye_gate <= 0.001)) {
        return texcoord;
    }
    vec2 offset = vec2(0.0);
    if (u_slim > 0.001 && slim_gate > 0.001) {
        // face_slim_offset works in display pixels; the warp accumulates in UV.
        // The support gate is the exterior falloff and is applied here so the
        // displacement reaches zero exactly where the gate does.
        offset += face_slim_offset(disp_px) / disp_size * slim_gate;
    }
    if (u_eyes > 0.001 && eye_gate > 0.001) {
        // Keep the eye field on its original face-oval gate; slimming's outer
        // transition must not widen the eye warp.
        offset += eye_enlarge_offset(disp, vec2(u_warp_lx0, u_warp_ly0)) * eye_gate;
        offset += eye_enlarge_offset(disp, vec2(u_warp_lx1, u_warp_ly1)) * eye_gate;
    }
    vec2 warped = clamp(disp + offset, vec2(0.0), vec2(1.0));
    return clamp(display_to_tex(warped), vec2(0.0), vec2(1.0));
}

void main() {
    // The mask/gate always use the *unwarped* position so the region does not
    // travel with the warp; only the colour sample is warped.
    vec2 px = v_texcoord * vec2(u_full_w, u_full_h);
    vec2 uv = u_active > 0.5 ? warp_uv(v_texcoord) : v_texcoord;
    vec4 base = texture2D(tex, uv);

    // Region mask.  Every face uses its own landmark oval when it has one
    // (P0-b); a face without an oval keeps its own M5 box mask, and a face whose
    // landmarks are present but implausible has already had its box zeroed by
    // BeautyParams.uniforms, so it paints nothing.  The branch is selected by
    // *any* face having an oval (P1): a primary face that loses its landmarks
    // must not disable the secondary faces' ovals.  A frame with no ovals at all
    // keeps the old soft box behaviour end to end.
    float mask;
    float white;
    bool any_shape = u_shape_ready > 0.5 || u_shape1_ready > 0.5 || u_shape2_ready > 0.5;
    if (any_shape) {
        vec2 disp_px = tex_to_display(v_texcoord) * max(vec2(u_disp_w, u_disp_h), vec2(1.0));
        float shape0;
        if (u_shape_ready > 0.5) {
            shape0 = face_shape_mask(disp_px);
        } else {
            shape0 = face_mask(px, vec4(u_face0_x, u_face0_y, u_face0_w, u_face0_h));
        }
        float shape1;
        if (u_shape1_ready > 0.5) {
            shape1 = face_shape_mask_at(
                disp_px, u_shape1_cx, u_shape1_cy, u_shape1_a, u_shape1_b
            );
        } else {
            shape1 = face_mask(px, vec4(u_face1_x, u_face1_y, u_face1_w, u_face1_h));
        }
        float shape2;
        if (u_shape2_ready > 0.5) {
            shape2 = face_shape_mask_at(
                disp_px, u_shape2_cx, u_shape2_cy, u_shape2_a, u_shape2_b
            );
        } else {
            shape2 = face_mask(px, vec4(u_face2_x, u_face2_y, u_face2_w, u_face2_h));
        }
        mask = max(shape0, max(shape1, shape2));
        white = mask;
        if (mask > 0.004 && u_skin > 0.5) {
            // Only skin is smoothed, and only *clean* skin is whitened (lips
            // and speculars stay).  No floor for any face: hair/frames/
            // background get 0, whether the face is primary or secondary.
            vec2 gates = skin_lab(base.rgb);
            mask = max(shape0 * gates.x, max(shape1 * gates.x, shape2 * gates.x));
            white = max(shape0 * gates.y, max(shape1 * gates.y, shape2 * gates.y));
        }
    } else {
        mask = face_mask(px, vec4(u_face0_x, u_face0_y, u_face0_w, u_face0_h));
        mask = max(mask, face_mask(px, vec4(u_face1_x, u_face1_y, u_face1_w, u_face1_h)));
        mask = max(mask, face_mask(px, vec4(u_face2_x, u_face2_y, u_face2_w, u_face2_h)));
        mask *= mix(1.0, 0.10 + 0.90 * skin_gate(base.rgb), u_skin);
        white = mask;
    }

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
            rgb = mix(rgb, smooth_face(uv, base.rgb), smoothing);
        }
    }
    if (white > 0.002) {
        float whitening = u_whitening * white;
        if (whitening > 0.002) {
            rgb = whiten(rgb, whitening);
        }
    }

    // Compare mode: keep the divider narrow and low-contrast so it marks the
    // split without cutting through the subject like a bright white rule.
    if (u_compare > 0.5) {
        if (v_texcoord.x < u_split) {
            rgb = texture2D(tex, v_texcoord).rgb;
        }
        float split_distance = abs(px.x - u_split * u_full_w);
        float split_coverage = 1.0 - smoothstep(0.45, 1.25, split_distance);
        vec3 split_color = vec3(0.48, 0.53, 0.58);
        rgb = mix(rgb, split_color, split_coverage * 0.62);
    }
    gl_FragColor = vec4(rgb, base.a);
}
"""


@dataclass(frozen=True)
class BeautyParams:
    """Normalised beauty strengths in the 0.0-1.0 range."""

    smoothing: float = 0.6
    whitening: float = 0.4
    #: Experimental M5 warp strengths (0.0-1.0; the UI exposes 0-100).
    face_slim: float = 0.0
    eye_enlarge: float = 0.0

    @classmethod
    def from_config(cls, config: BeautyConfig) -> "BeautyParams":
        return cls(
            smoothing=_clamp01(config.smoothing / 100.0),
            whitening=_clamp01(config.whitening / 100.0),
            face_slim=_clamp01(config.face_slim / 100.0),
            eye_enlarge=_clamp01(config.eye_enlarge / 100.0),
        )

    def is_identity(self) -> bool:
        """True when the shader would leave the frame untouched."""
        return (
            self.smoothing <= 0.0
            and self.whitening <= 0.0
            and self.face_slim <= 0.0
            and self.eye_enlarge <= 0.0
        )

    def uniforms(
        self,
        *,
        frame_size: tuple[int, int] = (0, 0),
        texture_size: tuple[int, int] | None = None,
        faces: Iterable["Face | Sequence[float]"] = (),
        landmarks: Sequence[Sequence[float]] | None = None,
        face_box: "Face | Sequence[float] | None" = None,
        rotation: int = 0,
        compare_mode: bool = False,
        split: float = 0.5,
        feather_px: float = 24.0,
        expand: float = 0.12,
        skin_guard: bool = True,
        active: bool = True,
        debug: bool = False,
        shape_hold: ShapeHold | None = None,
    ) -> dict[str, float]:
        """Uniform values for one frame.

        Args:
            frame_size: size of the *displayed* frame, i.e. the coordinate space
                of the detection boxes (already rotated upright).
            texture_size: size of the frame the shader samples.  Defaults to
                ``frame_size``; 90/270 degree display matrices swap the axes.
            faces: detected faces in display coordinates.  The **first** entry is
                the primary face: the warp mask uses ``u_face0`` and the warp
                landmarks belong to it (``normalise_face_boxes`` keeps the order).
            landmarks: five YuNet points of the primary face, in display
                coordinates.  Defaults to ``faces[0].landmarks`` when omitted.
            face_box: primary face box; defaults to ``faces[0]``.  Its centre and
                size drive the slim warp region.
            rotation: clockwise display rotation applied to the decoded frame
                before it reaches the screen (the detector rotates by the same
                amount, so boxes must be mapped back for the shader).
            shape_hold: optional per-face temporal hold.  When the current fit is
                rejected but the same face (matched by box position and width)
                had a plausible oval within the last :data:`SHAPE_HOLD_FRAMES`
                frames, the held oval is reused.  ``None`` disables the hold,
                which is the deterministic behaviour used by the pure uniform
                tests.

        A primary face whose five points are finite but fail the geometry checks
        in :func:`fit_face_shape` is dropped: its ``u_face0`` box is zeroed and
        the warp is disabled, so that face paints no mask at all.  Missing or
        non-finite landmarks keep the M5 box fallback.  With a ``shape_hold`` the
        drop is delayed by a few frames; the warp is still disabled as soon as
        the current landmarks are not a valid fit.
        """
        frame_w, frame_h = (max(0, int(v)) for v in frame_size)
        texture_w, texture_h = (
            max(0, int(v))
            for v in (texture_size or frame_size)
        )
        face_list = list(faces)
        primary = face_list[0] if face_list else None
        if landmarks is None and primary is not None:
            landmarks = getattr(primary, "landmarks", ())
        if face_box is None and primary is not None:
            face_box = primary
        values: dict[str, float] = {
            "u_texel_x": 1.0 / texture_w if texture_w else 0.0,
            "u_texel_y": 1.0 / texture_h if texture_h else 0.0,
            "u_full_w": float(texture_w),
            "u_full_h": float(texture_h),
            "u_smoothing": self.smoothing,
            "u_whitening": self.whitening,
            "u_slim": self.face_slim,
            "u_eyes": self.eye_enlarge,
            "u_active": 1.0 if active and not self.is_identity() else 0.0,
            "u_compare": 1.0 if compare_mode else 0.0,
            "u_split": _clamp01(split),
            "u_feather": max(0.0, float(feather_px)),
            "u_expand": max(0.0, float(expand)),
            "u_skin": 1.0 if skin_guard else 0.0,
            "u_debug": 1.0 if debug else 0.0,
        }
        boxes = normalise_face_boxes(
            face_list, frame_w, frame_h, rotation=rotation, limit=BEAUTY_MAX_FACES
        )
        for index in range(BEAUTY_MAX_FACES):
            box = boxes[index] if index < len(boxes) else (0.0, 0.0, 0.0, 0.0)
            for component, value in zip(_FACE_COMPONENTS, box):
                values[f"u_face{index}_{component}"] = float(value)
        primary_rect: tuple[float, float, float, float] | None = None
        if face_box is not None:
            try:
                rect = _rect_of(face_box)
                if (
                    all(math.isfinite(float(v)) for v in rect)
                    and rect[2] > 0.0
                    and rect[3] > 0.0
                ):
                    primary_rect = rect
            except (TypeError, ValueError):
                primary_rect = None
        box_width = primary_rect[2] if primary_rect is not None else 0.0
        fitted_primary = fit_face_shape(landmarks, box_width=box_width)
        shape = fitted_primary
        if shape_hold is not None:
            shape = shape_hold.resolve(0, fitted_primary, primary_rect)
        # Five finite points that fail the geometry checks mean the detector saw
        # a face but the landmarks do not describe it (a profile collapse, or a
        # box/landmark mismatch).  Zero this face's box as well: the M5 fallback
        # would otherwise paint hair and background inside that box.  Missing or
        # malformed landmarks keep the box fallback on purpose -- a lost
        # detection should degrade, not flicker off.
        skip_primary = (
            shape is None
            and box_width > 0.0
            and _finite_landmarks(landmarks) is not None
        )
        if skip_primary:
            for component in _FACE_COMPONENTS:
                values[f"u_face0_{component}"] = 0.0
        # The warp needs the *current* landmarks.  A held oval keeps the mask
        # alive for a few frames, but it must not drive the slim warp from the
        # bad points that invalidated the fit.
        values.update(
            warp_uniforms(
                landmarks if fitted_primary is not None else None,
                face_box if fitted_primary is not None else None,
                (frame_w, frame_h),
                rotation=rotation,
            )
        )
        values.update(_shape_uniform_values(shape, frame_w, frame_h))
        # P0-b: fit every secondary face from its own five points.  ``boxes``
        # only holds faces with a non-empty rectangle, so pair the shapes with
        # that same filtered order; otherwise a zero-width face would shift
        # u_shape1 onto u_face2.
        valid_faces: list[tuple[object, tuple[float, float, float, float]]] = []
        for face in face_list:
            try:
                rect = _rect_of(face)
            except (TypeError, ValueError):
                continue
            if rect[2] > 0.0 and rect[3] > 0.0:
                valid_faces.append((face, rect))
        secondary_shapes: list[FaceShape | None] = []
        for index in range(1, BEAUTY_MAX_FACES):
            if index >= len(valid_faces):
                if shape_hold is not None:
                    shape_hold.resolve(index, None, None)
                secondary_shapes.append(None)
                continue
            face, rect = valid_faces[index]
            points = getattr(face, "landmarks", ())
            fitted_secondary = fit_face_shape(points, box_width=rect[2])
            secondary_shape = fitted_secondary
            if shape_hold is not None:
                secondary_shape = shape_hold.resolve(
                    index, fitted_secondary, rect
                )
            if (
                secondary_shape is None
                and _finite_landmarks(points) is not None
            ):
                for component in _FACE_COMPONENTS:
                    values[f"u_face{index}_{component}"] = 0.0
            secondary_shapes.append(secondary_shape)
        values.update(_secondary_shape_values(secondary_shapes))
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


#: YuNet five-point landmark order, as returned by the detector in display
#: coordinates.  The same order the face overlay draws.
LANDMARK_RIGHT_EYE = 0
LANDMARK_LEFT_EYE = 1
LANDMARK_NOSE = 2
LANDMARK_MOUTH_RIGHT = 3
LANDMARK_MOUTH_LEFT = 4

#: Gentle slim peak as a fraction of the oval's half width.  The oval scales with
#: the face, so a fixed pixel peak would over-crush a small (distant) face and
#: barely touch a close one.  Reduced from M5.2's 0.085 after the contour-moving
#: pass looked too strong: 0.07 * a is ~16 px on the 526 px reference box.
SLIM_PEAK_RATIO = 0.07
SLIM_PEAK_MIN_PX = 4.0
SLIM_PEAK_MAX_PX = 30.0

#: M5.2a geometry sanity bounds for the oval fit.  The shader draws the oval with
#: half width ``a = 0.90 d``, so requiring ``d / box_w >= 0.28`` guarantees the
#: oval is at least half the detector box wide (``1.8 * 0.28 = 0.504``).  A
#: profile face collapses the five YuNet points (``d`` -> a few pixels) while the
#: box keeps the full head width, which is exactly the thin-strip artifact M5.2
#: shipped on 90-degree profiles.
SHAPE_MIN_D_TO_BOX = 0.28
#: The oval's half height ``b`` comes from the eye-to-mouth distance, which is
#: stable for a real face (0.99..1.38 measured across the M5.2 review clips).
#: Outside this band the landmarks do not describe the box: a collapsed profile
#: gives ``b/d`` > 2, a box/landmark mismatch gives an arbitrary value.
SHAPE_MIN_B_TO_D = 0.80
SHAPE_MAX_B_TO_D = 1.75


@dataclass(frozen=True)
class FaceShape:
    """Face oval in display pixels, fitted to the five YuNet landmarks.

    The M5 mask/warp used the detector box: a rectangle that contains hairstyle,
    glasses temples, ears and background, and whose edge is not a face
    boundary.  This oval is: its top edge sits on the hairline, its bottom on
    the chin, and ``a`` is measured from the skin width seen in the recordings.
    """

    cx: float
    cy: float
    a: float  #: half width, display px
    b: float  #: half height, display px
    eye_y: float  #: eye line, the vertical fade for the slim warp
    d: float  #: interocular distance, the scale for every ratio

    def radius(self, x: float, y: float) -> float:
        """Normalised oval radius: < 1 inside the face, 1 on the outline."""
        if self.a <= 0.0 or self.b <= 0.0:
            return float("inf")
        return math.hypot((x - self.cx) / self.a, (y - self.cy) / self.b)

    @property
    def slim_peak_px(self) -> float:
        """Peak slim displacement in pixels, clamped for tiny/huge faces."""
        return max(SLIM_PEAK_MIN_PX, min(SLIM_PEAK_MAX_PX, SLIM_PEAK_RATIO * self.a))


#: How many consecutive frames a face may keep its previous oval when the
#: current fit is rejected.  Three frames is ~100 ms at 30 fps: long enough to
#: bridge a single bad detection, short enough that a real profile collapse
#: stops masking almost immediately.
SHAPE_HOLD_FRAMES = 3


@dataclass
class ShapeHold:
    """Per-face temporal hold for :class:`FaceShape` fits (P1).

    ``BeautyParams.uniforms`` is built from an immutable config every frame, so
    it cannot remember the previous frame itself.  The backend owns one
    :class:`ShapeHold` and passes it to :meth:`BeautyParams.uniforms`; this keeps
    the state explicit and unit-testable.

    The slot is keyed by face index and guarded by a box match (centre shift and
    width ratio).  A face that moves to a different place, or whose box changes
    size abruptly, is treated as a new face: its previous oval is not reused.
    When the fit fails, the held oval follows the box centre but keeps its size;
    after ``frames`` rejected frames the slot expires.
    """

    frames: int = SHAPE_HOLD_FRAMES
    _slots: dict[
        int, tuple[FaceShape, tuple[float, float, float, float], int]
    ] = field(default_factory=dict, init=False, repr=False)

    def clear(self) -> None:
        """Forget every held oval (call when playback/detection resets)."""
        self._slots.clear()

    def resolve(
        self,
        index: int,
        shape: FaceShape | None,
        rect: tuple[float, float, float, float] | None,
    ) -> FaceShape | None:
        """Return ``shape`` or the previous held oval for the same face."""
        if rect is None:
            self._slots.pop(index, None)
            return None
        try:
            x, y, width, height = (float(v) for v in rect)
        except (TypeError, ValueError):
            self._slots.pop(index, None)
            return None
        if not all(math.isfinite(v) for v in (x, y, width, height)):
            self._slots.pop(index, None)
            return None
        if width <= 0.0 or height <= 0.0:
            self._slots.pop(index, None)
            return None
        current = (x, y, width, height)
        if shape is not None:
            self._slots[index] = (shape, current, max(0, int(self.frames)))
            return shape
        slot = self._slots.get(index)
        if slot is None:
            return None
        previous, previous_rect, remaining = slot
        if remaining <= 0 or not self._same_face(previous_rect, current):
            self._slots.pop(index, None)
            return None
        dx = (x + width * 0.5) - (previous_rect[0] + previous_rect[2] * 0.5)
        dy = (y + height * 0.5) - (previous_rect[1] + previous_rect[3] * 0.5)
        held = FaceShape(
            cx=previous.cx + dx,
            cy=previous.cy + dy,
            a=previous.a,
            b=previous.b,
            eye_y=previous.eye_y + dy,
            d=previous.d,
        )
        self._slots[index] = (held, current, remaining - 1)
        return held

    @staticmethod
    def _same_face(
        previous: tuple[float, float, float, float],
        current: tuple[float, float, float, float],
    ) -> bool:
        """Loose identity check: same slot, nearby centre, similar width."""
        previous_width = max(previous[2], 1.0)
        current_width = max(current[2], 1.0)
        ratio = current_width / previous_width
        if ratio < 0.5 or ratio > 2.0:
            return False
        limit = 0.75 * max(previous_width, current_width)
        dx = (current[0] + current[2] * 0.5) - (previous[0] + previous[2] * 0.5)
        dy = (current[1] + current[3] * 0.5) - (previous[1] + previous[3] * 0.5)
        return math.hypot(dx, dy) <= limit


def _finite_landmarks(
    landmarks: Sequence[Sequence[float]] | None,
) -> list[tuple[float, float]] | None:
    """The five points as floats, or ``None`` unless they are all finite."""
    if landmarks is None or len(landmarks) != 5:
        return None
    try:
        points = [(float(x), float(y)) for x, y in landmarks]
    except (TypeError, ValueError):
        return None
    if any(not math.isfinite(v) for point in points for v in point):
        return None
    return points


def fit_face_shape(
    landmarks: Sequence[Sequence[float]] | None,
    *,
    box_width: float | None = None,
) -> FaceShape | None:
    """Fit :class:`FaceShape` to the five YuNet points (display coordinates).

    Unknowns are all expressed in interocular distances ``d``, which is the most
    stable measure the detector gives (it does not change with mouth opening or
    a small yaw):

    * half width ``a = 0.90 d`` (the skin measures ~1.8 d wide on the board
      recordings);
    * top edge ``eye_y - 0.80 d`` (hairline), bottom ``mouth_y + 0.42 d``
      (chin);
    * centre x is the mean of the eye midpoint and the mouth midpoint, so a
      yawed face does not shift the oval sideways.

    ``box_width`` is the primary face's detector box width in the same display
    pixels.  When given, two sanity checks reject geometry that does not
    describe a face:

    * ``d / box_width >= SHAPE_MIN_D_TO_BOX`` -- a 90-degree profile collapses
      the five points, and the old oval became a 12 px strip inside a 144 px box;
    * ``SHAPE_MIN_B_TO_D <= b / d <= SHAPE_MAX_B_TO_D`` -- the landmark-derived
      face height must be consistent with the interocular distance.

    Returns ``None`` when the five points are missing, degenerate or fail the
    sanity checks; the caller then decides whether to keep the M5 box mask (no
    landmarks) or to drop this face entirely (plausible points, implausible
    geometry).
    """
    points = _finite_landmarks(landmarks)
    if points is None:
        return None
    (rx, ry), (lx, ly) = points[LANDMARK_RIGHT_EYE], points[LANDMARK_LEFT_EYE]
    mouth_right, mouth_left = points[LANDMARK_MOUTH_RIGHT], points[LANDMARK_MOUTH_LEFT]
    d = math.hypot(lx - rx, ly - ry)
    if d < 2.0:
        return None
    if box_width is not None:
        try:
            box_w = float(box_width)
        except (TypeError, ValueError):
            box_w = 0.0
        if math.isfinite(box_w) and box_w > 0.0 and d / box_w < SHAPE_MIN_D_TO_BOX:
            return None
    eye_y = 0.5 * (ry + ly)
    eye_x = 0.5 * (rx + lx)
    mouth_x = 0.5 * (mouth_right[0] + mouth_left[0])
    mouth_y = 0.5 * (mouth_right[1] + mouth_left[1])
    top_y = eye_y - 0.80 * d
    chin_y = mouth_y + 0.42 * d
    b = 0.5 * (chin_y - top_y)
    b_to_d = b / d
    if b_to_d < SHAPE_MIN_B_TO_D or b_to_d > SHAPE_MAX_B_TO_D:
        return None
    return FaceShape(
        cx=0.5 * (eye_x + mouth_x),
        cy=0.5 * (top_y + chin_y),
        a=0.90 * d,
        b=b,
        eye_y=eye_y,
        d=d,
    )


#: Uniforms for the primary face's oval.  ``u_shape_eye_y/d/slim_peak`` only
#: drive the primary warp; the secondary ovals need the five geometry values.
PRIMARY_SHAPE_UNIFORM_KEYS = (
    "u_shape_ready",
    "u_shape_cx",
    "u_shape_cy",
    "u_shape_a",
    "u_shape_b",
    "u_shape_eye_y",
    "u_shape_d",
    "u_slim_peak",
    "u_disp_w",
    "u_disp_h",
)

#: Per-face oval uniforms for faces 1 and 2 (P0-b).  Always present (zeroed when
#: that face has no plausible oval) so the shader's uniform set never changes.
SECONDARY_SHAPE_UNIFORM_KEYS = (
    "u_shape1_ready",
    "u_shape1_cx",
    "u_shape1_cy",
    "u_shape1_a",
    "u_shape1_b",
    "u_shape2_ready",
    "u_shape2_cx",
    "u_shape2_cy",
    "u_shape2_a",
    "u_shape2_b",
)

#: Uniforms written by :func:`shape_uniforms` (always present, so the shader's
#: uniform set never changes at runtime).  The secondary entries are zeroed by
#: that function; :meth:`BeautyParams.uniforms` fills them per face.
SHAPE_UNIFORM_KEYS = PRIMARY_SHAPE_UNIFORM_KEYS + SECONDARY_SHAPE_UNIFORM_KEYS


def _shape_uniform_values(
    shape: FaceShape | None, frame_w: int, frame_h: int
) -> dict[str, float]:
    values = {key: 0.0 for key in SHAPE_UNIFORM_KEYS}
    values["u_disp_w"] = float(frame_w)
    values["u_disp_h"] = float(frame_h)
    if shape is None or frame_w <= 0 or frame_h <= 0:
        return values
    values.update(
        {
            "u_shape_ready": 1.0,
            "u_shape_cx": shape.cx,
            "u_shape_cy": shape.cy,
            "u_shape_a": shape.a,
            "u_shape_b": shape.b,
            "u_shape_eye_y": shape.eye_y,
            "u_shape_d": shape.d,
            "u_slim_peak": shape.slim_peak_px,
        }
    )
    return values


def _secondary_shape_values(
    shapes: Sequence[FaceShape | None],
) -> dict[str, float]:
    """Uniform values for faces 1/2; ``shapes`` is indexed from face 1."""
    values = {key: 0.0 for key in SECONDARY_SHAPE_UNIFORM_KEYS}
    for index, shape in enumerate(shapes[: BEAUTY_MAX_FACES - 1], start=1):
        if shape is None:
            continue
        values.update(
            {
                f"u_shape{index}_ready": 1.0,
                f"u_shape{index}_cx": shape.cx,
                f"u_shape{index}_cy": shape.cy,
                f"u_shape{index}_a": shape.a,
                f"u_shape{index}_b": shape.b,
            }
        )
    return values


def shape_uniforms(
    landmarks: Sequence[Sequence[float]] | None,
    frame_size: tuple[int, int],
    *,
    box_width: float | None = None,
) -> dict[str, float]:
    """Uniform values for the M5.2 face oval, in display pixels.

    ``u_shape_ready`` is 0.0 -- and every other entry 0.0 -- without the five
    landmarks, or when they fail the geometry checks in
    :func:`fit_face_shape`.  ``u_disp_w/h`` carry the *displayed* frame size so
    the shader can turn its display UV into the pixel space the oval lives in.
    The secondary ``u_shape1/2_*`` entries are always zero here; only
    :meth:`BeautyParams.uniforms` has the other faces and fills them.
    """
    frame_w, frame_h = (max(0, int(v)) for v in frame_size)
    shape = fit_face_shape(landmarks, box_width=box_width)
    return _shape_uniform_values(shape, frame_w, frame_h)


#: Warp uniforms written by :func:`warp_uniforms` (every key always present so
#: the shader's uniform set never changes at runtime).  All values are in
#: display space: the geometry is upright, whatever the stream rotation is.
WARP_UNIFORM_KEYS = (
    "u_warp_ready",
    "u_warp_cx",
    "u_warp_cy",
    "u_warp_face_w",
    "u_warp_face_h",
    "u_warp_lx0",
    "u_warp_ly0",
    "u_warp_lx1",
    "u_warp_ly1",
    "u_warp_rot",
)


def point_to_texture(
    x: float,
    y: float,
    frame_width: float,
    frame_height: float,
    rotation: int = 0,
) -> tuple[float, float]:
    """Map one display-space point to shader texture UV coordinates.

    Inverse of the display rotation, matching :func:`face_rect_to_texture`:
    0 -> ``(x, y)``, 90 -> ``(y, 1-x)``, 180 -> ``(1-x, 1-y)``,
    270 -> ``(1-y, x)`` (all normalised to [0, 1]).
    """
    if frame_width <= 0 or frame_height <= 0:
        return (0.0, 0.0)
    u = float(x) / float(frame_width)
    v = float(y) / float(frame_height)
    rotation %= 360
    if rotation == 90:
        return (v, 1.0 - u)
    if rotation == 180:
        return (1.0 - u, 1.0 - v)
    if rotation == 270:
        return (1.0 - v, u)
    return (u, v)


def warp_uniforms(
    landmarks: Sequence[Sequence[float]] | None,
    face_box: "Face | Sequence[float] | None",
    frame_size: tuple[int, int],
    rotation: int = 0,
) -> dict[str, float]:
    """Geometry for the M5 face warp, normalised in display UV space.

    Args:
        landmarks: the primary face's five YuNet points in display coordinates
            (right eye, left eye, nose, right mouth corner, left mouth corner).
        face_box: primary face box in display coordinates; its centre and size
            define the region the slim warp compresses (the jawline follows the
            box edge, the interior stays put).
        frame_size: *displayed* frame size (the landmarks' coordinate space).
        rotation: clockwise display rotation applied to the decoded texture.
            The shader rotates texture UVs back into display space, so every
            value returned here stays upright for any stream rotation.

    Returns:
        A dict with every :data:`WARP_UNIFORM_KEYS` entry.  ``u_warp_ready`` is
        0.0 (and all other entries 0.0) unless the full five-point geometry is
        available: a partial warp would jump as soon as the detector supplies
        the missing points.  Values are clamped to [0, 1] because the shader
        clamps its UVs anyway.
    """
    values = {key: 0.0 for key in WARP_UNIFORM_KEYS}
    frame_w, frame_h = (max(0, int(v)) for v in frame_size)
    if frame_w <= 0 or frame_h <= 0:
        return values
    if landmarks is None or len(landmarks) != 5:
        return values
    try:
        points = [(float(x), float(y)) for x, y in landmarks]
    except (TypeError, ValueError):
        return values
    if face_box is None:
        return values
    try:
        box_x, box_y, box_w, box_h = _rect_of(face_box)
    except (TypeError, ValueError):
        return values
    if box_w <= 0.0 or box_h <= 0.0:
        return values

    def uv(point: tuple[float, float]) -> tuple[float, float]:
        return (_clamp01(point[0] / frame_w), _clamp01(point[1] / frame_h))

    centre = uv((box_x + box_w * 0.5, box_y + box_h * 0.5))
    right_eye = uv(points[LANDMARK_RIGHT_EYE])
    left_eye = uv(points[LANDMARK_LEFT_EYE])

    values.update(
        {
            "u_warp_ready": 1.0,
            "u_warp_cx": centre[0],
            "u_warp_cy": centre[1],
            "u_warp_face_w": _clamp01(box_w / float(frame_w)),
            "u_warp_face_h": _clamp01(box_h / float(frame_h)),
            "u_warp_lx0": right_eye[0],
            "u_warp_ly0": right_eye[1],
            "u_warp_lx1": left_eye[0],
            "u_warp_ly1": left_eye[1],
            "u_warp_rot": float(rotation % 360),
        }
    )
    return values


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
