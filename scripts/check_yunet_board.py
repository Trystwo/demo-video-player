#!/usr/bin/env python3
"""Board-side check: run the RKNN YuNet model through librknnrt via ctypes.

Runs on the RK3576 board (``python3`` with ``python3-opencv``), where the RKNN
runtime is ``/usr/lib/librknnrt.so``:

    PYTHONPATH=/tmp/43-m2/src python3 scripts/check_yunet_board.py \
        --model /tmp/43-m2/models/yunet_rk3576.rknn \
        --onnx /tmp/43-m2/models/face_detection_yunet_2023mar.onnx \
        /tmp/43-m2/images/*.jpg

With ``--reference`` the same image is also run through OpenCV's
``FaceDetectorYN`` (CPU) so both detections can be compared side by side.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

import cv2  # noqa: E402
import numpy as np  # noqa: E402

from video_player.face_yunet import (  # noqa: E402
    INPUT_SIZE,
    decode_yunet_outputs,
    map_faces_to_frame,
    preprocess_frame,
)
from video_player.rknn_runtime import RknnRuntime  # noqa: E402


def iou(a: tuple[float, ...], b: tuple[float, ...]) -> float:
    ax1, ay1, ax2, ay2 = a[0], a[1], a[0] + a[2], a[1] + a[3]
    bx1, by1, bx2, by2 = b[0], b[1], b[0] + b[2], b[1] + b[3]
    inter = max(0.0, min(ax2, bx2) - max(ax1, bx1)) * max(0.0, min(ay2, by2) - max(ay1, by1))
    union = a[2] * a[3] + b[2] * b[3] - inter
    return inter / union if union > 0 else 0.0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("images", nargs="+")
    parser.add_argument("--model", required=True)
    parser.add_argument("--onnx", default="", help="ONNX model for the OpenCV reference")
    parser.add_argument("--threshold", type=float, default=0.5)
    parser.add_argument("--runs", type=int, default=10, help="timed inference runs")
    args = parser.parse_args(argv)

    runtime = RknnRuntime(args.model)
    print(f"model: {args.model}")
    print(f"inputs: {runtime.input_names} -> {[a['dims'] for a in runtime.input_attrs]}")
    print(f"outputs: {runtime.output_names} -> {[a['dims'] for a in runtime.output_attrs]}")

    reference = None
    if args.onnx:
        reference = cv2.FaceDetectorYN.create(args.onnx, "", (INPUT_SIZE, INPUT_SIZE))

    for image_path in args.images:
        image = cv2.imread(image_path)
        if image is None:
            print(f"{image_path}: cannot read", file=sys.stderr)
            continue
        height, width = image.shape[:2]
        model_input = preprocess_frame(image)

        started = time.perf_counter()
        raw = runtime.infer(model_input)
        infer_ms = (time.perf_counter() - started) * 1000.0

        faces = map_faces_to_frame(
            decode_yunet_outputs(raw, score_threshold=args.threshold), width, height
        )
        print(f"\n{image_path}: {width}x{height}, {len(faces)} face(s), {infer_ms:.2f} ms")
        for face in faces:
            print(
                f"  rknn box=({face.x:7.1f},{face.y:7.1f},{face.width:7.1f},{face.height:7.1f})"
                f" score={face.score:.3f}"
            )

        timings = []
        for _ in range(max(1, args.runs)):
            t0 = time.perf_counter()
            runtime.infer(model_input)
            timings.append((time.perf_counter() - t0) * 1000.0)
        timings.sort()
        print(
            f"  timing over {len(timings)} runs: median={timings[len(timings) // 2]:.2f} ms,"
            f" min={timings[0]:.2f} ms, max={timings[-1]:.2f} ms"
        )

        if reference is not None:
            reference.setInputSize((width, height))
            _, detected = reference.detect(image)
            ref_faces = [] if detected is None else detected
            print(f"  opencv reference: {len(ref_faces)} face(s)")
            for row in ref_faces:
                box = tuple(float(v) for v in row[:4])
                print(f"    cv box=({box[0]:7.1f},{box[1]:7.1f},{box[2]:7.1f},{box[3]:7.1f}) score={row[-1]:.3f}")
            for face in faces:
                best = max((iou(face.as_rect(), tuple(float(v) for v in row[:4])) for row in ref_faces), default=0.0)
                print(f"    IoU vs best cv face: {best:.3f}")
    runtime.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
