#!/usr/bin/env python3
"""Convert the YuNet ONNX detector to an RKNN model for the RK3576 NPU.

Run on an x86 Linux host (``rknn-toolkit2`` has no aarch64 wheels):

    .venv/bin/python scripts/convert_yunet.py

The conversion is deterministic: fp16 weights (no quantization dataset needed),
640x640 NHWC uint8 input with ``mean=0, std=1`` so the frame is fed as raw BGR
bytes (the same convention as OpenCV's ``FaceDetectorYN``).

Outputs are the 12 raw YuNet tensors in graph order:
``cls_{8,16,32}, obj_{8,16,32}, bbox_{8,16,32}, kps_{8,16,32}``.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ONNX = PROJECT_ROOT / "models" / "face_detection_yunet_2023mar.onnx"
DEFAULT_RKNN = PROJECT_ROOT / "models" / "yunet_rk3576.rknn"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--onnx", default=str(DEFAULT_ONNX), help="input ONNX model")
    parser.add_argument("--out", default=str(DEFAULT_RKNN), help="output .rknn model")
    parser.add_argument("--target", default="rk3576", help="Rockchip target platform")
    parser.add_argument(
        "--quantize",
        action="store_true",
        help="int8 quantization (needs --dataset; off = fp16 weights)",
    )
    parser.add_argument("--dataset", default="", help="quantization dataset list file")
    args = parser.parse_args(argv)

    onnx_path = Path(args.onnx)
    out_path = Path(args.out)
    if not onnx_path.is_file():
        print(f"error: ONNX model not found: {onnx_path}", file=sys.stderr)
        return 2

    from rknn.api import RKNN  # imported late so --help works without the SDK

    rknn = RKNN(verbose=True)
    print(f"config: target={args.target} mean=0 std=1 fp16")
    status = rknn.config(
        mean_values=[[0, 0, 0]],
        std_values=[[1, 1, 1]],
        target_platform=args.target,
    )
    _check(status, "rknn.config")

    status = rknn.load_onnx(model=str(onnx_path))
    _check(status, "rknn.load_onnx")

    status = rknn.build(
        do_quantization=args.quantize,
        dataset=args.dataset or None,
    )
    _check(status, "rknn.build")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    status = rknn.export_rknn(str(out_path))
    _check(status, "rknn.export_rknn")
    rknn.release()

    print(f"wrote {out_path} ({out_path.stat().st_size:,} bytes)")
    return 0


def _check(status: int, operation: str) -> None:
    if status != 0:
        raise SystemExit(f"{operation} failed with status {status}")


if __name__ == "__main__":
    raise SystemExit(main())
