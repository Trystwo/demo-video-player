# 模型（不入库）

美颜链路运行时需要一个 RKNN 人脸检测模型。权重因体积与许可不提交到仓库。

| 文件 | 模型 | 输入 | 许可 | 说明 |
|------|------|------|------|------|
| `yunet.rknn` | YuNet（OpenCV Zoo） | 320×320 或 640×640 | Apache-2.0（待转换时复核） | **首选**：体积小、许可宽松、适合公开仓库 |
| `retinaface.rknn` | RetinaFace (mobile) | 640×640 | 依上游权重许可（部分仅限研究） | 配置默认路径，若许可不允许公开再分发则替换 |
| `scrfd_500m.rknn` | SCRFD 500M | 640×640 | 依上游权重许可 | 更快，许可同上需复核 |

## 转换与运行方式（2026-11-14 板端结论）

- 板端 Python 3.13 **没有 `rknn-toolkit-lite2` 的 wheel**（`pip index versions` 无匹配），
  所以运行时用 **ctypes 直接调用 `/usr/lib/librknnrt.so`** 的 C API（M2 实现）。
- `.onnx → .rknn` 转换在 x86 主机用 `rknn-toolkit2` 完成（目标平台 `rk3576`），
  转换脚本与参数在 M2 落地后补充到 [../docs/BOARD.md](../docs/BOARD.md)。
- 模型来源、版本与许可必须记录在本文件；只选允许公开再分发的权重。

转换后放到本目录（路径与 `configs/player.example.json` 的
`detection.model_path` 对应）。模型缺失时播放器只报板级警告，硬解播放不受影响，
自动美颜保持关闭。

不要提交模型权重、转换后的 RKNN 文件或下载的测试视频；测试素材放仓库外，
或放 `assets/videos/`（已被 git 忽略）。
