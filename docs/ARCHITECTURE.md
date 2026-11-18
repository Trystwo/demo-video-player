# 视频播放器架构设计（M1 播放 + M2 检测 + M3 GPU 美颜实现版）

> 状态：M1 硬解播放、M2 NPU 人脸检测、M3 GPU 美颜与对比模式均已实现并在板端验证：
> 1080p30 播放稳定 30 fps；磨皮/美白在 Mali GPU 的单个 `glshader` 内完成
> （单帧 0.8–1.6 ms，相对纯播放的 CPU 增量约 +1–2 个百分点）；人脸遮罩由 NPU
> 检测框光栅化，分屏对比在同一帧、同一次 pass 内完成。证据见
> `docs/evidence/m3-*.png`，实测数据见 [BOARD.md](BOARD.md) §5.2，
> 范围变更同步到 [PRD.md](PRD.md)。

## 1. 目标与约束

- 单进程 GTK4 应用（PyGObject），在 Wayland 会话中运行，DSI 屏 1280×800。
- 视频解码走 VPU（`mppvideodec`），美颜走 Mali GPU（GLES shader），
  人脸检测走 NPU（RKNN），三者与 CPU 的主要工作分离。
- CPU 只做控制、UI、帧调度与检测后处理；A1 要求 1080p30 播放时 CPU < 30%，
  A2 要求开美颜 1080p30 ≥ 25 fps（实测见第 7 节）。
- 配置集中在 `configs/player.example.json`，代码只通过 `video_player.config` 读取。

## 2. 总体管线

```
filesrc → qtdemux/matroskademux/tsdemux
  ├─ video ── queue ── h264parse/h265parse ── mppvideodec            (NV12 DMABuf)
  │            ── glupload ── glcolorconvert ── capsfilter(GLMemory RGBA)
  │            ── glcolorscale ── capsfilter(长边 ≤ texture_max_dimension)   ── M3 预缩放
  │            ── glshader `video-beauty`（磨皮+美白+分屏，单 pass）          ── M3
  │            ── tee（仅 --record 时创建；录制分支见 §2.3）
  │            ── fpsdisplaysink ── gtk4paintablesink ── Gtk.Picture
  │                    ├─ M2：decoder src pad probe 每 N 帧抽 NV12 → 后台线程
  │                    │     NV12→BGR、按 tag 旋转、YuNet RKNN → GLib.idle_add
  │                    │     → 人脸框 + 5 点 overlay（GTK 图层）
  │                    └─ M3：同一批人脸框 → 归一化 + 反向旋转成纹理坐标
  │                          → glshader `uniforms`（与检测同频刷新，10 Hz）
  │                    └─ M3 证据/调试：`debug_mask=1` 时 shader 直接输出遮罩灰度
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

### 2.1 M3 美颜元素（已实现，`glshader` 单 pass）

- 美颜不新增自定义 GStreamer 元素，而是一个 `glshader`（元素名 `video-beauty`），
  插在 GL 转换链尾部、`fpsdisplaysink` 之前。顶点阶段使用元素内建的
  `a_position`/`a_texcoord`/`u_matrix`（已板端验证，无需自写顶点 shader）；
  片元阶段是 `beauty.BEAUTY_FRAGMENT_SHADER`。
- **不需要第二张蒙版纹理**：所有参数（最多 3 组人脸框、磨皮/美白强度、羽化像素、
  向外扩张比例、肤色门限、分屏位置、调试开关）都以 scalar `G_TYPE_FLOAT`
  uniform 传入，遮罩在片元里由「圆角矩形 SDF + `smoothstep` 羽化」光栅化，
  再乘 YCbCr 肤色权重，避免把 CPU 生成的 mask 位图上传成纹理（也规避了
  `glshader` 只有一个采样器输入的约束）。uniform 清单：

  | uniform | 含义 |
  |---------|------|
  | `tex`, `v_texcoord` | 输入采样器与纹理坐标（glshader 约定） |
  | `u_texel_x/y`, `u_full_w/h` | 纹理 texel 尺寸、整帧像素尺寸（用于半径与分屏定位） |
  | `u_face0_x/y/w/h` … `u_face2_*` | 最多 3 张脸的归一化纹理坐标框（反向旋转后） |
  | `u_smoothing`, `u_whitening` | 磨皮/美白强度 0–1 |
  | `u_feather`, `u_expand` | 羽化宽度（px）、人脸框外扩比例 |
  | `u_skin` | 肤色门限权重（保护眼睛/眉毛/发际线） |
  | `u_compare`, `u_split` | 分屏开关与分割位置（0–1，画面宽度比例） |
  | `u_debug` | 输出遮罩灰度预览 |
  | `u_active` | 无脸 / 关闭 / 参数为恒等时置 0，片元直接透传，不采样邻域 |

- 磨皮是 8 邻域双边近似（9 tap）：空间权重随 `u_smoothing` 放大半径
  （1.5–8.5 px），值域权重抑制跨边缘混合，因此皱纹被平滑而睫毛/发丝等强边缘保留；
  美白向白点插值并回补饱和度（`1 - 0.18*amount`），避免提亮后发灰。
- **无脸时段整条链路透传**（`u_active=0`），不产生采样与混合开销；检测关闭或
  模型不可用时同样透传，播放不受影响。
- 坐标系：`mppvideodec` 输出未旋转帧，屏幕旋转由 sink 按 `image-orientation`
  tag 完成；检测线程先把 BGR 旋转到屏幕方向再送 YuNet，所以检测框在「显示
  坐标系」，写 uniform 前经 `beauty.face_rect_to_texture()` 反向旋转回纹理
  坐标（0/90/180/270 均有单测；板端用 `ffmpeg -display_rotation` 生成的
  90°/180°/270° 素材 + 标记图验证过方向，见 BOARD §5.2）。
- 对比模式（F9）在 shader 内完成：`u_compare=1` 时 `x < u_split` 输出原图，
  分割线附近画 2 px 白线，因此分屏是**同一帧、同一次 pass**，不存在两路管线
  的帧对齐问题。demo 模式（`--demo`）强制打开对比。
- 板端踩坑（已记录在 BOARD §5.2）：片元 shader 必须自带 `precision highp float;`
  （`glshader` 不注入版本/精度）；`uniforms` 只认 `G_TYPE_FLOAT`，Python `float`
  会被写成 gdouble 并被元素静默忽略（表现为画面全黑）。

### 2.2 M3 纹理预缩放（`glcolorscale`）

- 板端实测出一个与美颜无关但影响 A2 CPU 目标的 GTK4 行为：当 `Gtk.Picture`
  把纹理**缩小到不足一半**时，GSK 每帧走 CPU 缩放路径，1080p 纹理在约 940 px
  宽的视频区里进程 CPU 从 16% 涨到 62–78%（fps 仍然 30）。窗口/视频区宽度
  在 955→960 px 之间跳变（对应 1920/960 = 0.5）即为分界。
- 因此 GL 分支在 `glcolorconvert` 之后插入 `glcolorscale` + capsfilter，把纹理
  长边预缩到 `player.texture_max_dimension`（默认 1280，0 表示不缩放），让 GTK
  侧的绘制比例 ≥ 0.5。板端实测同一窗口 CPU 62–64% → 18–20%，美颜采样成本也
  一并下降（1.9–2.8 → 0.8–1.4 ms/帧）。
- 该元素缺失时自动跳过（`_build_gl_scaler()` 返回空列表），退化为全尺寸纹理：
  效果不受影响，只是 1080p 在窄窗口下 CPU 偏高（降级而不是失败）。

### 2.3 应用内录制模式（`--record`，M3 收尾新增）

板端没有采集卡，GNOME/PipeWire 录屏在这块板子上不可用（`org.gnome.Shell.Screencast`
只落出一个 48 字节的空文件），x11grab 兜底只有 9–10 fps，所以演示视频改为由应用
自己录。**整窗快照不可行**：`Gtk.WidgetPaintable` + `render_texture` + pixbuf 读回
实测 35–70 ms/帧且 `to_node()` 时有时无，GTK 主循环被拖到 20 tick/s。因此录制不是
"拍屏幕"，而是把已经存在于 GPU 里的视频分支和一个 UI 覆盖层在 `glvideomixer` 里
合成：

```
beauty ─┬─ fpsdisplaysink ── gtk4paintablesink   （--record 时旁路为 fakesink）
        └─ tee ── queue ── [glvideoflip] ── glcolorscale ── capsfilter(GL RGBA)
                                                     └──> glvideomixer.sink_1
canvas(10 fps 重建) ── appsrc ── capsfilter(SystemMemory RGBA) ──> glvideomixer.sink_2
glvideomixer ── capsfilter(GL RGBA) ── gldownload ── mpph264enc(bps) ── h264parse
             ── mp4mux ── filesink（1280×800@30 或窗口尺寸）
```

- **视频 pad**：从 `tee` 的新 request pad 接出，先按显示旋转加 `glvideoflip`
  （`--record` 旁路了显示 sink，而 display matrix 平时是 sink 侧应用的；不加会把
  rotate-180 的自拍片录成倒的），再 `glcolorscale` 到"屏幕上视频区的实际大小"
  （避免 mixer 每帧缩放），`capsfilter` 固定为 `GLMemory RGBA`。
- **UI pad**：`appsrc` 推整窗 RGBA 画布（1280×800），内容 = 主题背景色 +
  黑色视频区 + 拟合矩形内 alpha=0 的洞（视频透出）+ chrome 图层快照
  （侧栏/信息行/控制条）+ 每帧自绘的人脸框与 5 点。背景与图层只在 widget
  变化时重拍（侧栏 3 s、信息行/控制条 1 s、前 2 s 每 tick 一次——刚建窗时
  widget 还没画出第一帧，快照会是空白），每帧只做 `base.copy()` + 画框。
- **帧同步**：UI 画布在视频流线程的 pad probe 里跟随视频 buffer 的 PTS 逐帧推送
  （内容 10 fps 重建、PTS 30 fps 推进）；`videoaggregator` 只要有 pad 不推进就会
  卡住整条分支并反压 tee，所以两个 pad 必须同速。
- **旁路显示**：`gtk4paintablesink` 与 `gldownload` 会争抢 GL/带宽（实测录制时显示
  掉到 27–28 fps），`--record` 时 `PlaybackBackend(display_bypass=True)` 把显示
  sink 换成 `fakesink`，fps 仍由 `fpsdisplaysink` 从 tee 上的 buffer 统计；录制
  文件因此能稳在 30 fps（编码侧 30–32 帧/s）。
- **收尾**：整条播放管线永远不会 post EOS（显示分支一直在播），所以 recorder 在
  `h264parse` 的 src pad 上装 EVENT 探针，看到 EOS 就把 mp4mux 收尾后再拆分支；
  `stop()` 超时 1.5 s 兜底。
- **命令行**：`--record FILE`（录制到文件，窗口内 `R` 键提前停止）、
  `--record-duration SECONDS`（到点自动停止并关窗，适合脚本化拍摄）。
  调试：`REC_DEBUG=1`（打印 attempt/canvas/enc/ui/vid 统计）、
  `REC_DUMP_CANVAS=/path.png`（导出最近一帧画布）。

录制链路的实测参数与坑（板端 1.26.2，1280×800）：

| 项目 | 实测 | 说明 |
|------|------|------|
| 整窗快照 | 35–70 ms/帧 | 不可用于 30 fps；`to_node()` 在首帧前返回 None |
| `glcolorconvert`→NV12 + 读回 | 33 fps（仅读回） / 20 fps（含编码） | 慢 4 倍 |
| GL RGBA 直读 + `mpph264enc` | 90 fps | 编码器直接吃 RGBA，省 videoconvert |
| `glvideomixer` 两 pad + 读回 + 编码 | 36 fps（RGBA） / 27.6 fps（NV12） | 选 RGBA 输出 |
| 画布合成（缓存 base 后） | compose 3–6 ms/tick | 未缓存时 50–60 ms/tick |
| 录制中整体 | 30.0 fps、CPU 184–205%、enc 30–32/s | 1280×800@30 H.264，10 s ≈ 5 MB |

其它板端踩坑：PyGObject 里 `GstAppSrc` 没有 `push_buffer()` 方法，要
`emit("push-buffer", buf)` / `emit("end-of-stream")`；`Gst.Bus.add_watch` 的绑定
签名是 `(priority, func, user_data)`，这里复用 backend 已装的 signal watch；
`glupload` 不能放进录制分支（GL context 不共享），交给 `glvideomixer` 自己上传；
GTK4 **不发射** `GtkWidget::destroy`，窗口清理必须挂 `close-request`（M3 顺带
修正了主窗口退出路径）。

## 3. 模块划分

| 文件 | 职责 | 状态 |
|------|------|------|
| `src/video_player/app.py` | 命令行入口、`--check`、启动 GTK | 可用 |
| `src/video_player/config.py` | 配置数据类、严格校验与钳位、读写 JSON | 可用（单测覆盖，M3 新增 texture_max_dimension / split / skin_guard 等） |
| `src/video_player/envcheck.py` | 板端/开发主机环境自检、报告格式化 | 可用（单测覆盖） |
| `src/video_player/pipeline.py` | 容器/caps → demux/parser/decoder 选择、URI 工具 | M1 已实现（单测覆盖） |
| `src/video_player/backend.py` | GStreamer 播放后端：构建管线（含 GL 预缩放与美颜）、状态/seek/音量/fps/快照/uniforms 推送 | M1–M3 可用（板端 30fps） |
| `src/video_player/playlist.py` | 媒体参数展开、播放列表模型 | 可用（单测覆盖） |
| `src/video_player/face.py` | `Face`、`DetectionResult`、`RknnFaceDetector`（ctypes 调 librknnrt） | M2 已实现（10 Hz 采样） |
| `src/video_player/face_yunet.py` | YuNet 后处理：按 stride 解码、NMS、映射回帧坐标 | M2 已实现（单测覆盖） |
| `src/video_player/rknn_runtime.py` | `librknnrt.so` 的最小 ctypes 封装（init/query/inputs/run/outputs/release） | M2 已实现 |
| `src/video_player/detection.py` | NV12→BGR、pad probe 采样、单深度队列与后台检测线程、NPU 起播预热 | M2 已实现（M3 加真实尺寸预热） |
| `src/video_player/beauty.py` | 美颜参数与 fragment shader、uniform 打包、纹理坐标反向旋转、CPU 参考蒙版 | M3 已实现（单测覆盖） |
| `src/video_player/ui/window.py` | GTK4 主窗口、播放区、控制条、播放列表、美颜/对比面板、`--record` 编排（画布定时器/录制时长/R 键）、快照、PERF 日志 | M1–M3 已接线（板端验证） |
| `src/video_player/ui/capture.py` | 逐 widget 快照（`Gtk.WidgetPaintable`→`render_texture`→pixbuf）与 `CanvasBuilder` 画布合成（缓存 base、分层刷新、人脸框） | M3 收尾新增（录制用） |
| `src/video_player/recording.py` | 录制分支：`glvideomixer` 合成视频 + UI 画布，`mpph264enc`/`mp4mux` 输出，EOS 收尾与统计 | M3 收尾新增（板端 30 fps 验证） |
| `scripts/launch_player.py` | 桌面入口包装脚本（原样透传参数） | 可用（M3 修复 `--snapshot` 被吞） |
| `scripts/perf_probe.py` | 板端帧率探针：pad probe 计帧 + GTK snapshot 计数 + CPU | 可用（M1–M3 实测工具，含 `BEAUTY`/`SCALE` 旋钮） |
| `scripts/bench_board.sh` | 色彩转换路径 wall-time 基准矩阵 | 可用（M1 实测工具） |
| `scripts/convert_yunet.py` | x86 侧 ONNX→RKNN 转换（rk3576，fp16，mean 0/std 1） | 可用（1,084,515B 模型） |
| `scripts/measure_tap.py` | 板端 headless 检测吞吐测量（`fakesink sync=true`） | 可用（M2 实测工具） |
| `scripts/probe_board.py` | 环境报告导出（文本/JSON/Markdown） | 可用 |
| `tests/` | config/beauty/face/pipeline/playlist/app/envcheck/seek/launcher/fps/预热/录制 单测 | 107 passed, 12 skipped（无 numpy/GL/板卡项跳过；`test_recording.py` 在带 numpy 的环境单独跑 40 passed） |

## 4. 线程模型

| 线程 | 职责 | 注意 |
|------|------|------|
| GStreamer streaming thread | 解码、GL 上传/转换/预缩放、美颜 shader、sink 提交 | 不做阻塞调用；检测只投递请求；shader 只读 uniform |
| NPU 工作线程 | RKNN 推理 + 锚点解码 + NMS | 队列深度 1，忙时丢帧不排队，避免延迟累积 |
| GLib 主循环 | GTK UI、进度/音量同步、信息面板刷新、uniforms 推送 | UI 只读共享状态，不直接阻塞等 NPU |

检测结果通过最近一次结果 + 跟踪补帧（`DetectionScheduler` 的 `reused` 语义）
交给美颜参数；M2 引入跟踪后，`reused=True` 的帧使用跟踪位置而不是旧框。
美颜参数在 GLib 主线程由检测结果换算成 uniform 字典后写入 `glshader` 的
`uniforms` 属性（元素内部在流线程读取），与流线程无共享可变状态。

起播顺序（M3 收尾修正）：`FaceDetectionTap.start()` 在挂 probe 之前先做一次
**真实解码尺寸**的 NPU 预热（`warmup_frame_sizes()`；90/270 旋转素材额外预热
交换尺寸），把 RKNN 首个大帧推理的 1.7–2.4 s 一次性成本挪到播放启动之前，
否则 worker 忙这一秒多会让 sink 丢掉约一半的起播帧（详见 §7）。

## 5. 零拷贝与格式路径

M2 现状：检测在 decoder 的 src pad 用 `buffer.extract_dup()` 复制 NV12
（实测 `extract_dup` 1080p NV12 17–33 ms/次——旧注释估的 ~3 ms 偏低 5–10 倍；NV12→BGR ≈3.1 ms/次），尚未使用 RGA。
M3 未增加 CPU 拷贝：预缩放与美颜都在 GL 纹理上完成，蒙版也不上传纹理
（shader 内光栅化）；每帧新增的 CPU 工作只有 uniform 结构写入（10 Hz）。
RGA 直通仍留作后续优化，用于替代检测输入的 `extract_dup`。

显示方向：手机竖录或微信导出的 MP4 常带 180° Display Matrix。`qtdemux`
用 `image-orientation` tag 暴露它，GTK sink 会按 tag 旋转显示，但解码缓冲
仍是未旋转帧；检测若直接使用原始缓冲，人脸框会整体错位（表现为只框住半张
脸）。检测线程在 NV12→BGR 后按 tag 旋转（90°/270° 时同步交换
`video_info` 宽高），保证送检帧与屏幕显示一致（2026-11-17 用 1080p 手机
素材复现并修复，提交 725a681）；美颜把检测框反向旋转回纹理坐标（M3）。

优先级（M3 已在板上逐层验证）：

1. VPU 输出 dmabuf → EGLImage → GL 纹理，美颜在 GPU 内完成，全程不经过 CPU。
2. 若 dmabuf 导入不可用：RGA 做 NV12→RGBA 转换/缩放，再 `glupload` 上传。
3. 若 GL 整条链路不可用：退化为 720p 处理或仅对脸部 ROI 处理（见第 7 节）。

M1 实测：`mppvideodec` 的 DMABuf 输出直接连 `gtk4paintablesink` 协商失败
（`not-negotiated`），当前采用路径 2 的简化版——`glupload ! glcolorconvert`
在 GPU 内完成 NV12→RGBA，再以 `GLMemory` 提交给 sink；该路径已满足 30fps。
M3 在同一 GL 链路上追加 `glcolorscale` 与 `glshader`，均为 GPU 内处理，
dmabuf/EGLImage 直通（路径 1）仍未启用（当前链路已满足性能目标，收益有限）。

CPU 侧只保留：检测输入的 640×640 RGB（由 pad probe 抽帧）、UI 与 uniform 编码。
`build_face_mask()` 的 NumPy 光栅化保留为 CPU 参考实现（单测/离线预览用），
运行时不参与。

## 6. 配置契约

`configs/player.example.json` 与 `video_player/config.py` 一一对应：

- `player`：硬解开关、渲染 sink、色彩转换路径（`video_convert`：
  `auto`/`gl`/`sw`）、音量、倍速、断点续播、**`texture_max_dimension`**
  （GL 纹理长边上限，默认 1280，0 = 不预缩放）；
- `beauty`：开关、磨皮/美白强度（0–100 → 0–1）、对比模式、羽化像素、
  **`expand`**（人脸框外扩 0–1，默认 0.12）、**`skin_guard`**（肤色门限，
  默认 true）、**`split`**（分屏位置 0.05–0.95，默认 0.5）、
  **`debug_mask`**（遮罩预览，默认 false）；
- `detection`：模型路径、检测间隔帧数、最大人脸数、分数阈值、跟踪补帧；
- `debug`：信息面板、fps 统计窗口、日志级别。

加载时做类型校验和范围钳位（如音量 0–100、倍速 0.5–2.0、间隔 1–10、
`split` 0.05–0.95、`texture_max_dimension` 0–8192），未知字段直接报错
（见 `ConfigError`），避免手改配置静默失效。

## 7. 性能预算（1080p30，33.3 ms/帧）

M1 实测（板端 2026-11-14；12s/360 帧样片，`sync=false`，`scripts/bench_board.sh`）：

| 阶段 | 预算 | 实测 |
|------|------|------|
| VPU 解码 | ≤ 8 ms/帧 | 12s 素材全解 0.80 s（约 15× 实时，≈2.2 ms/帧） |
| CPU `videoconvert` NV12→RGBA | — | 19.9–23.3 s（≈15 fps，**不可用于 1080p30 实时**） |
| GPU `glupload+glcolorconvert`→RGBA | ≤ 4 ms/帧 | 同素材 3.27 s（≈110 fps 吞吐） |
| GTK4 提交 + 播放窗口 | ≤ 5 ms/帧 | 稳定 30 fps，进程 CPU 约 16–17% |
| 软件降级 `video_convert=sw` | — | 15–18 buffers/s，窗口约 3.7 fps（只作兜底） |

M2 实测：1080p30 播放 + 10 Hz 检测时窗口约 29–30 fps（1080p 双人脸 28.8）；
单次 RKNN 推理约 30–38 ms（640×640），检测线程与解码/渲染并行。开检测时
进程 CPU 约 82%、关检测约 30%，增量主要来自 NPU 线程与 NV12 拷贝。

M3 实测（板端 2026-11-17，pexels 1080p30 H.264 近景素材，1280×800 显示）：

| 场景 | 工具 | fps | CPU | 美颜耗时 |
|------|------|-----|-----|---------|
| 纯管线 + 预缩放 + 美颜 shader（全屏 1280×800） | `perf_probe.py CONV=gl BEAUTY=1 SCALE=1280x720` | 30.0 | 18–20% | 0.76–1.38 ms |
| 同一探针但不预缩放（940×700 窗口） | `perf_probe.py` 无 SCALE | 30.0（GTK 只画 26.2 帧/s） | 62–64% | 1.9–2.8 ms |
| 应用，美颜关 + 检测关 | `launch_player.py --config cfg_C.json` | 30.1–30.2 | 30–32% | —（透传仍有约 0.8 ms/帧的元素开销） |
| 应用，美颜开 + 检测关 | `launch_player.py --config cfg_B.json` | 30.0–30.4 | 30–33% | 0.68–1.36 ms |
| 应用，美颜开 + 检测开（真实人脸，无分屏） | `launch_player.py --config cfg_faceon.json` | 30.0 | 110–119% | 1.06–1.56 ms |
| 应用，demo（美颜 + 检测 + 强制分屏，全屏） | `launch_player.py --demo` | 30.0 | 117–131% | 1.1–1.5 ms |

结论：**美颜本身几乎免费**（同场景 CPU 增量约 +1–2 个百分点、单帧 <1.6 ms，
A2 的 fps 与 CPU 目标按「美颜+显示链路」计为达标）；demo 全开时 CPU 偏高是
M2 的 10 Hz YuNet + RKNN 线程池成本（headless `measure_tap.py`：30 fps、
10 次检测/s、37.5 ms/次；线程采样显示 NPU 侧约占 85 个百分点），列入后续优化。

测量方法：表中数字均取自 `VIDEO_PLAYER_PERF_LOG=1` 的 `PERF` 行稳定段。
fps 取 `PlaybackBackend.measured_fps()`——最近 1.5 s 内实际到达的帧数（pad probe
记录 pts），**不再使用 `fpsdisplaysink` 的 `average_fps`**：后者是从起播开始累计的
平均值，起播期一旦丢帧就要几十秒才爬回 30，读起来像「周期性掉速」（M3 收尾修正，
见下）。检测开启时起播会丢约一半帧，根因是 **RKNN 首个大尺寸推理实测 2.38–2.43 s**
（之后每帧 38–41 ms），worker 忙这一秒多 sink 只能丢帧；640×640 零图预热只要
30.9 ms，**不覆盖大尺寸输入路径**，所以此前无效。现在 `start()` 用真实解码尺寸
预热后，连续复测 `fpsdisplaysink` 的 `droprate` 全程 `0.000%`、PERF fps 从第一个
采样点起就是 30.0（2026-11-18 复测：cfg_det_only / cfg_faceon / `--demo` 三组一致）。

**已证伪的旧结论**：`c71d42e` 记载的「开检测时周期性 20–25 fps 掉速窗口」并不
存在。同步采集 `fpsdisplaysink` 的瞬时值、累计平均值与墙钟帧率后可见：全过程中
`sig_fps≈29–31`、`drop=0.000`、墙钟 30.0 帧/s 稳定；界面/日志里的 21–25 只是
`average_fps` 的累计值，加上 PERF 采样发生在 `_tick()` 用 measured_fps 校正
`_fps` 之前（现已把 `_maybe_log_perf()` 移到 `_tick()` 末尾）。

M3 排查中确认的 GTK4 慢路径：`Gtk.Picture` 显示纹理时若缩放比 < 0.5 会走
CPU 每帧缩放（1080p 纹理、视频区 940 px 宽时进程 CPU 62–78%；把视频区放到
960 px 以上则回落到 15–20%）。这是 A2 CPU 目标的关键，已由 `glcolorscale`
预缩放规避（见 2.2）。

## 8. 失败与降级策略

| 失败点 | 表现 | 降级 |
|--------|------|------|
| NPU/模型不可用 | 检测器初始化失败（如模型文件缺失） | 已实现：检测关闭，信息行显示 `NPU: off`，硬解播放与美颜透传不受影响 |
| `glshader` 缺失或 shader 编译失败 | 建元素/设 `fragment` 失败 | 已实现：美颜禁用、开关置灰，信息行显示 `beauty: unavailable`，播放继续（M3） |
| `glcolorscale` 缺失 | 无法预缩放纹理 | 已实现：跳过该级，全尺寸纹理提交；效果不变，窄窗口下 CPU 偏高（M3） |
| GL/dmabuf 不可用 | 上传/纹理链路报错 | RGA 转换 + CPU 上传，必要时降到 720p |
| `gtk4paintablesink` 缺失 | 无法嵌入 GTK 窗口 | `waylandsink` 独立全屏窗口（F9 对比仍可用） |
| `glupload`/`glcolorconvert` 缺失 | `video_convert=gl` 报错 | `auto` 回落 `videoconvert`（1080p 约 15 fps，信息面板暴露真实值）；`sw` 路径下美颜自动禁用 |
| 配置 `video_convert=sw` 但美颜开启 | 无 GL 纹理可处理 | 已实现：报 `GPU beauty needs player.video_convert=gl`，美颜禁用 |
| Wayland sink 不可用 | 无显示输出 | `autovideosink`/`fakesink` 用于无头验证 |
| 性能不足 | fps 低于 25 | 按第 7 节顺序降级，并在信息面板暴露实际值 |

M1 的退出崩溃修复（先销毁窗口/管线，`run_window` 末尾 `os._exit`）在 M3
保持不变：反复启动 + 快照 20 余次均为 rc=0。

## 9. 未决技术点

1. ~~GTK4 内嵌视频用 `gtk4paintablesink` 还是 `waylandsink`~~ → **已定**：
   `gtk4paintablesink` 可用且已在 M1 验证 30fps。
2. ~~`glshader` 的顶点属性/矩阵约定与板端版本是否匹配~~ → **已定（M3）**：
   板端 1.26.2 的 `glshader` 提供 `a_position`/`a_texcoord`/`u_matrix` 与
   `v_texcoord`，片元 shader **不带 `#version`**、但必须写 `precision highp float;`；
   uniform 必须用 `G_TYPE_FLOAT` 写入。无需自写 GL 元素。
3. VPU 输出到 GL 的 dmabuf 零拷贝路径在 RK3576 上的可行性（`kmssink`/
   `waylandsink` 已支持，自定义元素需验证）——M3 未启用，当前链路已达标。
4. ~~RetinaFace/SCRFD 的 RKNN 转换与后处理耗时~~ → **已定**：YuNet（MIT）转
   RKNN，640×640 输入、10 Hz 采样、单次约 30 ms，1080p 播放 28.8–30 fps。
5. ~~是否需要跟踪器来稳定每 N 帧检测之间的框~~ → **已定**：沿用 M2 的
   「最近一次结果 + 跟踪补帧」，美颜 uniform 与检测同频刷新（10 Hz），
   实测无可见跳变；若后续出现快速运动下的遮罩滞后，再引入独立的平滑跟踪。
6. **新增**：GTK4 纹理缩放的 0.5 阈值行为（`Gtk.Picture` 缩小到不足一半时
   走 CPU 路径）已用预缩放规避，但窗口尺寸/侧栏宽度变化仍可能把绘制比例
   推到 0.5 以下；当前默认布局（1280 px 窗口、330 px 侧栏、1280 纹理）留有
   余量，后续做小窗模式时需要重新核对。
7. **新增（M4）**：录制画布的 overlay 与视频之间有 ≤100 ms 的相位差
   （画布 10 Hz 重建 + 检测结果延迟），静态/慢动作镜头无感，快速运动镜头
   人脸框会轻微滞后；若以后要录高速素材，可把画布重建提到 15–20 Hz 或给
   检测结果做位置外推。录制时屏幕上的视频区是黑的（显示被旁路），属预期。
8. **新增**：检测线程 CPU 占比（约占 85 个百分点）是 M2 遗留成本，M3 未改动；
   其中 `extract_dup` 抽一帧 1080p NV12 实测 17–33 ms（10 Hz 即约 0.18 核），
   远超旧注释的 ~3 ms。若 A2 的 CPU 目标需要严格覆盖 demo 全开场景，优先从
   RGA 直通 / NV12 预缩放 / 降低检测频率入手。

## 10. 目录结构

```
demos/43-video-player/
├── configs/player.example.json   # 配置契约
├── docs/                         # PRD / 架构 / 板端清单 / 演示指南 / evidence/（实测证据）
├── models/                       # RKNN 模型（不入库）
├── scripts/                      # 启动、环境探测、性能探针脚本
├── src/video_player/             # app/config/beauty/face/pipeline/backend/playlist/envcheck/ui
│                                 #   + recording.py/capture.py（--record 应用内录制）
├── tests/                        # pytest 单测
├── install.sh                    # 环境检查 / 部署脚本（M4 完善）
├── requirements*.txt             # 运行/开发依赖
├── README.md / README_zh.md      # 英文 / 中文门面
└── .gitignore
```
