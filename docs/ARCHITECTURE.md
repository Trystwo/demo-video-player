# 视频播放器架构设计（M1 播放 + M2 人脸检测实现版）

> 状态：M1 播放内核与 M2 NPU 人脸检测均已实现并在板端验证（硬解 + GTK4 内嵌 +
> GPU 色彩转换，1080p30 实时 30fps；YuNet RKNN 每次约 30 ms、10 Hz 采样，人脸框
> 在 GTK 图层叠加；证据见 `docs/evidence/`）。M3 美颜尚未开始，实测数据回填
> [BOARD.md](BOARD.md)，范围变更同步到 [PRD.md](PRD.md)。

## 1. 目标与约束

- 单进程 GTK4 应用（PyGObject），在 Wayland 会话中运行，DSI 屏 1280×800。
- 视频解码走 VPU（`mppvideodec`），美颜走 Mali GPU（GLES shader），
  人脸检测走 NPU（RKNN），三者与 CPU 的主要工作分离。
- CPU 只做控制、UI、帧调度与检测后处理；A1 要求 1080p30 播放时 CPU < 30%。
- 配置集中在 `configs/player.example.json`，代码只通过 `video_player.config` 读取。

## 2. 总体管线

```
filesrc → qtdemux/matroskademux/tsdemux
  ├─ video ── queue ── h264parse/h265parse ── mppvideodec
  │            ── glupload ── glcolorconvert ── capsfilter(video/x-raw(memory:GLMemory),format=RGBA)
  │            ── fpsdisplaysink ── gtk4paintablesink ── Gtk.Picture
  │                    │
  │                    └─ M2（已实现）：decoder src pad probe 每 N 帧抽 NV12 → 后台线程
  │                       NV12→BGR、拉伸 640×640 → RKNN YuNet → 解码/NMS → GLib.idle_add
  │                    └─ M3：人脸蒙版 → glshader 美颜 → 同一 sink
  └─ audio ── queue ── decodebin ── audioconvert ── audioresample ── volume ── autoaudiosink
```

- M1 使用**显式管线**而不是 `uridecodebin`：后缀决定解封装元素
  （`pipeline.demux_for`），caps 事件决定 parser 与解码器（`parser_for` /
  `decoder_for`），保证 H.264/H.265 一定走 `mppvideodec` 硬解。网络流（F2）
  按 2026-11-14 范围决策移出本期。
- 视频分支必须做 **GPU 色彩转换**：`mppvideodec` 输出 NV12（DMABuf），若用
  `videoconvert` 转 RGBA，1080p 实测约 15 fps（55–66 ms/帧，单核打满）；
  改为 `glupload ! glcolorconvert ! GLMemory RGBA` 后实测约 110 fps 吞吐，
  播放窗口稳定 30 fps。配置项 `player.video_convert` 控制该路径
  （`auto`/`gl`/`sw`），`sw` 只作无 GL 环境的降级。
- 选择 `gtk4paintablesink`（`gst-plugin-gtk4 0.13.5`）做 GTK4 内嵌，
  通过只读属性 `paintable` 交给 `Gtk.Picture`；DMABuf 直连
  `mppvideodec ! gtk4paintablesink` 在板端协商失败（not-negotiated），
  所以先经 GL 上传，再以 `GLMemory` RGBA 提交。
- `beautyfilter` 是计划中的 GPU 元素，接收 RGBA 帧与一张单通道人脸蒙版，
  输出同尺寸 RGBA 帧；M3 决定用现成 `glshader` 组合还是自写 GL 元素。
- 无脸时段 beautyfilter 直接透传（`BeautyParams.is_identity()` / mask 全 0）。

## 3. 模块划分

| 文件 | 职责 | 状态 |
|------|------|------|
| `src/video_player/app.py` | 命令行入口、`--check`、启动 GTK | 骨架可用 |
| `src/video_player/config.py` | 配置数据类、严格校验与钳位、读写 JSON | 可用（单测覆盖） |
| `src/video_player/envcheck.py` | 板端/开发主机环境自检、报告格式化 | 可用（单测覆盖） |
| `src/video_player/pipeline.py` | 容器/caps → demux/parser/decoder 选择、URI 工具 | M1 已实现（单测覆盖） |
| `src/video_player/backend.py` | GStreamer 播放后端：构建管线、状态/seek/音量/fps/快照回调 | M1 可用（板端 30fps） |
| `src/video_player/playlist.py` | 媒体参数展开、播放列表模型 | 可用（单测覆盖） |
| `src/video_player/face.py` | `Face`、`DetectionResult`、`RknnFaceDetector`（ctypes 调 librknnrt） | M2 已实现（10 Hz 采样） |
| `src/video_player/face_yunet.py` | YuNet 后处理：按 stride 解码、NMS、映射回帧坐标 | M2 已实现（单测覆盖） |
| `src/video_player/rknn_runtime.py` | `librknnrt.so` 的最小 ctypes 封装（init/query/inputs/run/outputs/release） | M2 已实现 |
| `src/video_player/detection.py` | NV12→BGR、pad probe 采样、单深度队列与后台检测线程 | M2 已实现 |
| `src/video_player/beauty.py` | 美颜参数、GLES shader、羽化人脸蒙版 | 参数/shader/蒙版可用，元素在 M3 |
| `src/video_player/ui/window.py` | GTK4 主窗口、播放区、控制条、播放列表、美颜面板、快照 | M1 已接线（板端验证） |
| `scripts/launch_player.py` | 桌面入口包装脚本（透传未知参数） | 可用 |
| `scripts/perf_probe.py` | 板端帧率探针：pad probe 计帧 + GTK snapshot 计数 + CPU | 可用（M1 实测工具） |
| `scripts/bench_board.sh` | 色彩转换路径 wall-time 基准矩阵 | 可用（M1 实测工具） |
| `scripts/convert_yunet.py` | x86 侧 ONNX→RKNN 转换（rk3576，fp16，mean 0/std 1） | 可用（1,084,515B 模型） |
| `scripts/measure_tap.py` | 板端 headless 检测吞吐测量（`fakesink sync=true`） | 可用（M2 实测工具） |
| `scripts/probe_board.py` | 环境报告导出（文本/JSON/Markdown） | 可用 |
| `tests/` | config/beauty/face/pipeline/playlist/app/envcheck 单测 | 72 passed, 4 skipped（缺 NumPy） |

## 4. 线程模型

| 线程 | 职责 | 注意 |
|------|------|------|
| GStreamer streaming thread | 解码、RGA 缩放、GL 美颜、sink 提交 | 不做阻塞调用；检测只投递请求 |
| NPU 工作线程 | RKNN 推理 + 锚点解码 + NMS | 队列深度 1，忙时丢帧不排队，避免延迟累积 |
| GLib 主循环 | GTK UI、进度/音量同步、信息面板刷新 | UI 只读共享状态，不直接阻塞等 NPU |

检测结果通过最近一次结果 + 跟踪器补帧（`DetectionScheduler` 的 `reused` 语义）
交给美颜元素；M2 引入跟踪后，`reused=True` 的帧使用跟踪位置而不是旧框。

## 5. 零拷贝与格式路径

M2 现状：检测在 decoder 的 src pad 用 `buffer.extract_dup()` 复制 NV12
（`extract_dup` ≈2.8 ms/次、NV12→BGR ≈3.1 ms/次），尚未使用 RGA；
M3 再评估 RGA 直通，以减少 CPU 拷贝。

优先级（M3 前在板上逐层验证，失败则降级）：

1. VPU 输出 dmabuf → EGLImage → GL 纹理，美颜在 GPU 内完成，全程不经过 CPU。
2. 若 dmabuf 导入不可用：RGA 做 NV12→RGBA 转换/缩放，再 `glupload` 上传。
3. 若 GL 整条链路不可用：退化为 720p 处理或仅对脸部 ROI 处理（见第 7 节）。

M1 实测：`mppvideodec` 的 DMABuf 输出直接连 `gtk4paintablesink` 协商失败
（`not-negotiated`），当前采用路径 2 的简化版——`glupload ! glcolorconvert`
在 GPU 内完成 NV12→RGBA，再以 `GLMemory` 提交给 sink；该路径已满足 30fps。
dmabuf/EGLImage 直通（路径 1）留待 M3 美颜元素接入时再验证。

CPU 侧只保留：检测输入的 640×640 RGB（由 RGA 生成）、蒙版位图（M3 可用
GPU 光栅化替代 NumPy 生成）。

## 6. 配置契约

`configs/player.example.json` 与 `video_player/config.py` 一一对应：

- `player`：硬解开关、渲染 sink、色彩转换路径（`video_convert`：
  `auto`/`gl`/`sw`）、音量、倍速、断点续播；
- `beauty`：开关、磨皮/美白强度（0–100）、对比模式、羽化像素；
- `detection`：模型路径、检测间隔帧数、最大人脸数、分数阈值、跟踪补帧；
- `debug`：信息面板、fps 统计窗口、日志级别。

加载时做类型校验和范围钳位（如音量 0–100、倍速 0.5–2.0、间隔 1–10），
未知字段直接报错（见 `ConfigError`），避免手改配置静默失效。
UI 修改只写内存中的 `Config`；持久化由 M3 的设置面板调用 `Config.save()`。

## 7. 性能预算（1080p30，33.3 ms/帧）

M1 实测（板端 2026-11-14；12s/360 帧样片，`sync=false`，`scripts/bench_board.sh`）：

| 阶段 | 预算 | 实测 |
|------|------|------|
| VPU 解码 | ≤ 8 ms/帧 | 12s 素材全解 0.80 s（约 15× 实时，≈2.2 ms/帧） |
| CPU `videoconvert` NV12→RGBA | — | 19.9–23.3 s（≈15 fps，**不可用于 1080p30 实时**） |
| GPU `glupload+glcolorconvert`→RGBA | ≤ 4 ms/帧 | 同素材 3.27 s（≈110 fps 吞吐） |
| GTK4 提交 + 播放窗口 | ≤ 5 ms/帧 | 稳定 30 fps，进程 CPU 约 16–17% |
| 软件降级 `video_convert=sw` | — | 15–18 buffers/s，窗口约 3.7 fps（只作兜底） |

M2 实测：1080p30 播放 + 10 Hz 检测时窗口约 29–30 fps（1080p 双人脸 28.8）；单次 RKNN 推理约
30–38 ms（640×640），检测线程与解码/渲染并行。开检测时进程 CPU 约 82%、
关检测约 30%，增量主要来自 NPU 线程与 NV12 拷贝。M1 已满足 A1，M3 美颜
接入后若超标，按以下顺序降级：
降低检测频率 → 只处理人脸 ROI → 美颜处理分辨率降到 720p → 关闭美白保留磨皮。

## 8. 失败与降级策略

| 失败点 | 表现 | 降级 |
|--------|------|------|
| NPU/模型不可用 | 检测器初始化失败（如模型文件缺失） | 已实现：检测关闭，信息行显示 `NPU: off`，硬解播放不受影响 |
| GL/dmabuf 不可用 | 上传/纹理链路报错 | RGA 转换 + CPU 上传，必要时降到 720p |
| `gtk4paintablesink` 缺失 | 无法嵌入 GTK 窗口 | `waylandsink` 独立全屏窗口（F9 对比仍可用） |
| `glupload`/`glcolorconvert` 缺失 | `video_convert=gl` 报错 | `auto` 回落 `videoconvert`（1080p 约 15 fps，信息面板暴露真实值） |
| Wayland sink 不可用 | 无显示输出 | `autovideosink`/`fakesink` 用于无头验证 |
| 性能不足 | fps 低于 25 | 按第 7 节顺序降级，并在信息面板暴露实际值 |

## 9. 未决技术点（M1/M2 前需验证）

1. ~~GTK4 内嵌视频用 `gtk4paintablesink` 还是 `waylandsink`~~ → **已定**：
   `gtk4paintablesink` 可用且已在 M1 验证 30fps。
2. `glshader` 的顶点属性/矩阵约定与板端 GStreamer 版本是否匹配，是否需要
   自写 `GstGLFilter` 子类（C 或 Python 插件）。
3. VPU 输出到 GL 的 dmabuf 零拷贝路径在 RK3576 上的可行性（`kmssink`/
   `waylandsink` 已支持，自定义元素需验证）。
4. ~~RetinaFace/SCRFD 的 RKNN 转换与后处理耗时~~ → **已定**：YuNet（MIT）转
   RKNN，640×640 输入、10 Hz 采样、单次约 30 ms，1080p 播放 28.8–30 fps。
5. 是否需要跟踪器来稳定每 N 帧检测之间的框（M3 美颜接入前评估；当前人脸框
   直接复用最近一次检测结果）。

## 10. 目录结构

```
demos/43-video-player/
├── configs/player.example.json   # 配置契约
├── docs/                         # PRD / 架构 / 板端清单 / 演示指南 / evidence/（实测证据）
├── models/                       # RKNN 模型（不入库）
├── scripts/                      # 启动、环境探测、性能探针脚本
├── src/video_player/             # app/config/beauty/face/pipeline/backend/playlist/envcheck/ui
├── tests/                        # pytest 单测
├── install.sh                    # 环境检查 / 部署脚本（M4 完善）
├── requirements*.txt             # 运行/开发依赖
├── README.md / README_zh.md      # 英文 / 中文门面
└── .gitignore
```
