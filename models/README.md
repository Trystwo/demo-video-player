# 人脸检测模型（YuNet / RKNN）

本目录只放说明，模型文件不入库（`.gitignore` 已排除 `models/*.rknn`）。

## 已选模型

**YuNet**（OpenCV Zoo，MIT License，© 2020 Shiqi Yu）：

- 上游 ONNX：`face_detection_yunet_2023mar.onnx`，232,589 字节；
- 输入：`[1, 3, 640, 640]` float32，**BGR、0–255、拉伸到 640×640**（无均值/方差归一化）；
- 输出 12 个张量：`cls_8/16/32`、`obj_8/16/32`（各 `[1, 6400/1600/400, 1]`）、
  `bbox_*`（`[.., 4]`）、`kps_*`（`[.., 10]`），stride 为 8/16/32。

## 转换（x86 主机）

```bash
# 需要 python3 + rknn-toolkit2 2.5.0（x86 only）+ torch CPU 版
python3 scripts/convert_yunet.py   --onnx models/face_detection_yunet_2023mar.onnx   --rknn models/yunet_rk3576.rknn
```

转换参数：`target_platform='rk3576'`、`mean_values=[[0,0,0]]`、`std_values=[[1,1,1]]`、
`do_quantization=False`（fp16）。产物约 **1,084,515 字节**。

## 板端运行

把 `models/yunet_rk3576.rknn` 放到板端项目同路径即可；`configs/player.example.json`
的 `detection.model_path` 默认就是它。检测器通过 ctypes 直接调用
`/usr/lib/librknnrt.so`（板端 Python 3.13 没有 `rknn-toolkit-lite2` wheel）。

实测（RK3576）：单次推理首次 35.4ms、10 次中位 **30.1ms**（640×640，单核）；
1080p30 播放 + 10Hz 检测仍稳定 **30fps**。数据见 [../docs/BOARD.md](../docs/BOARD.md)。

## 许可

YuNet 为 MIT；如替换为 RetinaFace / SCRFD，请确认权重许可（InsightFace 预训练
权重有非商业限制，公开 Demo 不建议使用），并在本文档记录来源与许可。
