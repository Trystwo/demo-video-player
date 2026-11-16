#!/usr/bin/env python3
"""Dev helper: run YuNet ONNX through onnxruntime and decode with our code.

Used on the x86 host to validate :mod:`video_player.face_yunet` before deploying
the RKNN model.  Usage:

    .venv/bin/python scripts/check_yunet_onnx.py .cache/obama.jpg
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

import cv2  # noqa: E402
import numpy as np  # noqa: E402

from video_player.face_yunet import (  # noqa: E402
    INPUT_SIZE,
    OUTPUT_NAMES,
    decode_yunet_outputs,
    map_faces_to_frame,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("image", nargs="+", help="image file(s) to inspect")
    parser.add_argument("--onnx", default=str(PROJECT_ROOT / "models" / "face_detection_yunet_2023mar.onnx"))
    parser.add_argument("--threshold", type=float, default=0.5)
    args = parser.parse_args(argv)

    import onnxruntime as ort

    session = ort.InferenceSession(args.onnx, providers=["CPUExecutionProvider"])
    input_name = session.get_inputs()[0].name

    for image_path in args.image:
        image = cv2.imread(image_path)
        if image is None:
            print(f"{image_path}: cannot read", file=sys.stderr)
            continue
        resized = cv2.resize(image, (INPUT_SIZE, INPUT_SIZE), interpolation=cv2.INTER_LINEAR)
        blob = resized.astype(np.float32).transpose(2, 0, 1)[None]  # BGR, NCHW
        raw = session.run(list(OUTPUT_NAMES), {input_name: blob})
        outputs = dict(zip(OUTPUT_NAMES, raw))
        faces = map_faces_to_frame(
            decode_yunet_outputs(outputs, score_threshold=args.threshold),
            image.shape[1],
            image.shape[0],
        )
        print(f"{image_path}: {image.shape[1]}x{image.shape[0]}, {len(faces)} face(s)")
        for face in faces:
            print(
                f"  box=({face.x:7.1f},{face.y:7.1f},{face.width:7.1f},{face.height:7.1f})"
                f" score={face.score:.3f}"
            )
        annotated = image.copy()
        for face in faces:
            x1, y1 = int(face.x), int(face.y)
            x2, y2 = int(face.x + face.width), int(face.y + face.height)
            cv2.rectangle(annotated, (x1, y1), (x2, y2), (0, 255, 0), 2)
            for px, py in face.landmarks:
                cv2.circle(annotated, (int(px), int(py)), 3, (0, 0, 255), -1)
        out_path = PROJECT_ROOT / ".cache" / f"check_{Path(image_path).stem}.jpg"
        cv2.imwrite(str(out_path), annotated)
        print(f"  annotated -> {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
