# 板端环境与实测清单

> 目标板：Quectel Pi M2（RK3576）。本文件记录**可复现的板端事实**与待实测项；
> 环境自检可执行版本是 `src/video_player/envcheck.py`，由 `install.sh`
> 和 `scripts/probe_board.py` 调用。

## 1. 目标板基线

来自 [../../../docs/README.md](../../../docs/README.md) 的板卡实测记录
（2026-10-16，板端 SSH，IP 略）：

| 项目 | 实测值 |
|------|--------|
| SoC | RK3576，4×Cortex-A72 + 4×Cortex-A53 @2.0GHz |
| 内存 / 存储 | 3.8GiB RAM（可用约 1.7GiB）/ 根分区 42G（剩余 14G） |
| 系统 | Debian 13 (trixie)，kernel 6.1.118-rt36（PREEMPT_RT），aarch64 |
| 桌面 | GNOME 48，Wayland 会话；SSH 登录属 tty 会话，运行 GUI 需 `WAYLAND_DISPLAY=wayland-0 XDG_RUNTIME_DIR=/run/user/1000` |
| GStreamer | 1.26.2（280 个插件）；`mppvideodec`、`mpph264enc`、`waylandsink`、`gtk4paintablesink`、`glshader`、`glupload`、`fpsdisplaysink` 均可用 |
| GTK / PyGObject | GTK 4.18.6，python3-gi 可用 |
| GPU | Mali-G52（`rockchip_dri` 驱动，mesa 25.0.7）；GTK 使用 `GskGLRenderer`（硬件 GL）；GL 元素：`glupload`/`glcolorconvert`/`glcolorscale`/`glshader`/`gltransformation`（opengl 插件 1.26.2） |
| 显示 | DSI 1280×800 已连接；HDMI 未连接 |
| NPU | librknnrt 2.3.2，驱动 0.9.8；MobileNetV1 推理 2.15ms（约 465fps）；运行时 `/usr/lib/librknnrt.so`，Python `rknnlite` 未安装（M2 需 pip 装 rknn-toolkit-lite2 或 ctypes 调用） |
| 视频编码 | GStreamer `mpph264enc`/`mpph265enc` 1080p30 ≥47fps |
| 视频解码 | `mppvideodec` 硬件解码可用；4K60 H.264/H.265 裸解 186–187 fps（≈3.1× 实时）；8K（7680×4320）裸解 **44.7 fps（8K30 H.265）/ 47.8 fps（8K60 H.264，不足实时）**（2026-11-24） |
| 视频编码（8K） | 实测 `mpph265enc` **可硬编 8K30 H.265**（7680×4320@30，日志 `h265e_proc_prep_cfg w:h [7680:4320]`），超出 datasheet 的 4K60 编码上限；但该片播放路径不稳定（见 §5.2） |
| 2D 加速 | `librga 2.2.0` 已安装；GStreamer 侧无 `rga`/`rgaconvert` 元素（M2 走 librga C API 或自定义元素） |
| 软件编码参考 | x264 1080p 仅约 16fps |
| 媒体工具 | ffmpeg 7.1（无 rkmpp，媒体链路以 GStreamer 为准） |
| 音频 | ES8389 codec，PipeWire 1.4 |
| 网络 | 仅 WiFi，无蜂窝模组 |
| 其他 | 当前无 USB 摄像头；E4 摄像头输入按 P2 结论本期不做 |
| 已装软件 | Python 3.13、numpy 2.2、OpenCV 4.10、sox、GTK4 gi、ssh |

## 2. 待实测清单（M1/M2/M3 逐项勾选）

| # | 待验证项 | 命令/方法 | 期望 | 实测（日期/结论） |
|---|----------|----------|------|------------------|
| 1 | `mppvideodec` 可硬解 1080p30 H.264 | `scripts/bench_board.sh` / `scripts/perf_probe.py` | 稳定 30fps，单核 CPU 占用低 | ✅ 2026-11-14：12s/360 帧全解 0.80s（≈15× 实时）；GPU 转换路径播放窗口 30fps、进程 CPU 16–17%（A1 达标；证据 `docs/evidence/m1-ui-gl.png`） |
| 2 | `mppvideodec` 可硬解 1080p30 H.265 | 同上，换 `h265parse` + H.265 素材 | 稳定 30fps | **已实测（2026-11-18）**：`user-selfie-1080p30.mp4`（HEVC 1080p30、rotate-180）demo 全开 30.0fps，快照 `docs/evidence/m3-h265-selfie-demo.png` |
| 3 | 4K30/4K60 H.264/H.265 解码能力 | 4K 素材 + `fpsdisplaysink` / `scripts/bench_4k60.sh` | 连续播放不卡顿（A1 可选加分项） | ✅ 2026-11-24 实测（超出预期）：**4K60 H.264/H.265 均稳定 60.0 fps**（3840×2160@60 素材，默认 `texture_max_dimension=1280`；H.264 CPU 39–50%/beauty 2.0–2.7 ms，H.265 CPU 29–38%/beauty 1.5–1.8 ms）；VPU 裸解吞吐 **186–187 fps（≈3.1× 实时）**。瓶颈只有 NPU 检测（开启后 20.7–60 抖动）。证据 `docs/evidence/4k60/`，细节见 §5.2 |
| 4 | `waylandsink` 可用 | `gst-inspect-1.0 waylandsink` + 最小播放管线 | 窗口正常出画 | 2026-11-14 元素存在；M1 用 `gtk4paintablesink` 内嵌，`waylandsink` 留作降级未出画 |
| 5 | `gtk4paintablesink` 是否可用 | `gst-inspect-1.0 gtk4paintablesink` | 决定 F9 内嵌方案 | ✅ 2026-11-14 可用且已验证：`gst-plugin-gtk4 0.13.5`；`Gtk.Picture` + `paintable` 播放窗口 30fps |
| 6 | Mali GPU 上 `glshader` 可用 | `gst-inspect-1.0 glshader` + 简单 fragment shader 管线 | 可在播放链路中插入 | ✅ 2026-11-17：headless（`GST_GL_PLATFORM=egl GST_GL_WINDOW=surfaceless`）下 shader 编译/渲染可用；约束：片元 shader 必须自带 `precision highp float;`、uniform 必须 `G_TYPE_FLOAT`（见 §5.2）；已用于美颜 |
| 7 | `librknnrt.so` 与 RKNN 版本 | `python3 scripts/probe_board.py --markdown`；`ldconfig -p \| grep rknn` | 2.3.2 可用 | 2026-11-14：`/usr/lib/librknnrt.so` 存在（7.7MB）；Python 3.13 无 `rknn-toolkit-lite2` wheel，M2 走 ctypes 调 C API |
| 8 | RGA（`librga.so`）缩放路径 | 用 RGA 将 1080p NV12 缩放到 640×640 并计时 | ≤2ms，可 Python/C 调用 | 2026-11-14：`librga 2.2.0` 已装；GStreamer 无 rga 元素；API 验证待 M2 |
| 9 | RetinaFace/SCRFD RKNN 转换 | rknn-toolkit2 转 ONNX → RKNN，板端加载推理 | 640×640 单次 ≤15ms | 待测 |
| 10 | 检测+后处理总耗时 | `DetectionScheduler` 记录 `inference_ms` | N=3 时摊薄到 ≤5ms/帧 | 待测 |
| 11 | GLES 美颜单帧耗时 | shader 基线测试 / 信息面板 | 全屏 ≤4ms（1280×800） | ✅ 2026-11-17：`perf_probe.py BEAUTY=1` 0.76–1.38 ms/帧（预缩放 1280×720）；应用内信息行 `beauty: on 0.8–1.6 ms`；CPU 增量约 +1–2 个百分点（见 §5.2） |
| 12 | VPU→GL 零拷贝（dmabuf/EGLImage） | 按 [ARCHITECTURE.md](ARCHITECTURE.md) 第 5 节验证 | 无 CPU 帧拷贝，或记录降级 | 2026-11-14：`mppvideodec ! gtk4paintablesink` 直连报 `not-negotiated`；当前走 `glupload+glcolorconvert`（GPU 内转换）→ `GLMemory` RGBA；M3 追加 `glcolorscale`+`glshader` 仍在 GPU 内，dmabuf 直通未启用（当前链路已达标） |
| 13 | 显示与触屏 | 本地播放 + 触控操作 | 1280×800 全屏正常，触屏可点 | 2026-11-14：DSI-1 connected；GUI 在 Xwayland/`GDK_BACKEND=x11` 下启动成功并截图；Wayland 原生出画与触屏待 M2 前补测 |
| 14 | 音频输出 | 播放带音轨素材 | PipeWire 正常出声，音量/静音可控 | 2026-11-14：`pulsesink` 可用、`audiotestsrc ! autoaudiosink` rc=0；真实音轨待补测（M1 样片无音轨） |
| 15 | GTK4 整窗逐帧捕获（录制候选方案 A） | `Gtk.WidgetPaintable` → `render_texture` → `Gdk.pixbuf_get_from_texture` | 未知 | ❌ 2026-11-18：render 15.6 ms + 读回 22.5 ms = **38.7 ms/帧**（≈26 fps 上限），真实 app 内 snapshot 35–70 ms 且 `to_node()` 时有时无 → 不适合 30 fps 录制 |
| 16 | `glvideomixer` 合成录制（采用方案） | 视频 GL 分支（tee）+ UI RGBA appsrc → mixer → `gldownload` → `mpph264enc` | 1080p 级 30 fps | ✅ 2026-11-18：1280×800@30 录制稳定 30 fps（`enc 30–32/s`），gst-launch 基准 CPU ≈0.8–1.0 核；细节与踩坑见 ARCHITECTURE §2.3 |
| 17 | `mpph264enc` 编码器输入/参数 | `gst-inspect-1.0 mpph264enc` + RGBA/NV12 路径基准 | 码率参数可用、可吃 RGBA | ✅ 2026-11-18：**没有 `bitrate` 属性**，码率写 `bps`；sink caps 接受 RGBA/NV12 等；`GL RGBA→gldownload→编码` 90 fps vs `GL NV12→gldownload→编码` 20 fps（NV12 转换慢 4 倍） |
| 18 | 用户级部署契约（install.sh） | `./install.sh --require-board` 部署启动器 + 桌面入口 | 输出 `Deployment complete.`，`desktop-file-validate` 通过 | ✅ 2026-11-18：板端实测 16 ok / 1 warn / 0 fail；`~/.local/bin/video-player-demo` + `~/.local/share/applications/video-player-demo.desktop`；启动器 `--version` 输出 `video_player 0.1.0` |
| 19 | 网络流 | HLS 样本 + 局域网 RTSP 摄像头 | 可播放（A6） | 移出本期（2026-11-14），不验收 |
| 20 | 8K（7680×4320）解码与播放 | 8K 素材 + `docs/evidence/8k/README.md` 复现命令 | 能出画面、可评估 | ⚠️ 2026-11-24：**能解能播但渲染不稳，不作交付项**。8K30 H.264 稳 30.0 fps（CPU 21%）、8K60 H.264 标称 59.9 fps（裸解仅 47.8 fps，实际丢帧）；8K30 H.265 出图后 SIGSEGV；`texture_max_dimension=0` SIGSEGV；根因 GPU BO `PANFROST_CREATE_BO err=12`。详见 §5.2 与 `docs/evidence/8k/` |

## 3. 模型与素材清单

| 类别 | 文件 | 来源/许可 | 状态 |
|------|------|----------|------|
| 人脸检测模型 | `models/yunet_rk3576.rknn`（已用） | YuNet（OpenCV Zoo，MIT）经 `scripts/convert_yunet.py` 转换 | ✅ 2026-11-16（1,084,515B，30ms/次） |
| 人脸检测模型 | `models/scrfd_500m.rknn`（备选） | 同上，速度优先 | 未使用 |
| 测试视频（正脸） | 1080p30 H.264 人像，≥1 人 | 用户自备（公司自有版权） | 待提供 |
| 测试视频（多人/侧脸） | 1080p30，≥3 人，含 ≤45° 侧脸 | 用户自备（公司自有版权） | 待提供 |
| 测试视频（4K） | 4K60 H.264/H.265，可选 | 板端合成 + 1080p30 人像上采样（`/tmp/4k60/`，不入库） | ✅ 2026-11-24 已用于 4K60 实测（见 §5.2 与 `docs/evidence/4k60/`） |
| 测试视频（8K） | 8K H.264/H.265，能力探测用 | 公开免费样本（thetestdata / ai2done）+ 板端硬解→硬编转码（`/home/tris/demo-8k/`，不入库） | ✅ 2026-11-24 已用于 8K 探测（见 §5.2 与 `docs/evidence/8k/`） |
| 测试流 | 局域网 RTSP + 公网 HLS 样本 | 公开样本/公司搭建 | 本期不做（随 F2 移出） |

版权要求：演示与验收素材必须使用公司自有或已授权素材，禁止把下载的影视片段
放进仓库或公开演示材料；模型权重与测试视频都已被 `.gitignore` 排除。

## 4. 探测命令

在板端项目目录执行：

```bash
# 一键环境检查（板端要求所有板级项通过）
./install.sh --require-board

# 自检报告（含 GTK4/GStreamer/插件/库/模型/显示）
python3 -m video_player --check

# 导出 Markdown 表格，回填本文件“实测记录”
python3 scripts/probe_board.py --markdown --require-board

# 单个插件详情
gst-inspect-1.0 mppvideodec
gst-inspect-1.0 waylandsink

# M1 性能探针（pad probe 计帧 + GTK snapshot 计数 + CPU；可直接在板上跑）
python3 scripts/perf_probe.py

# 色彩转换路径 wall-time 基准矩阵
bash scripts/bench_board.sh
```

离线安装（板端无网络/无 git）用单文件安装器；它内嵌的源码包是生成物，发布前必须与工作区一致：

```bash
# 本机：重新生成内嵌源码包（改了任何随包发布的文件都要跑）
python3 scripts/build_board_install.py
python3 scripts/build_board_install.py --check    # 过期会非零退出并列出差异文件
```

`install.sh --require-board` 会把板级项（`mppvideodec`、`mpph264enc`、`waylandsink`、
`gtk4paintablesink`、`librknnrt`、`librga`）的缺失视为失败；`models/*.rknn` 缺失只警告
（人脸检测是可选功能，缺模型时美颜自动透传）。普通 `--check` 在开发主机上只把这些项
标成 `warn`。M4 起 `install.sh` 还会为当前用户部署启动器 `~/.local/bin/video-player-demo`
与桌面入口 `~/.local/share/applications/video-player-demo.desktop`，最后输出
`Deployment complete.`；`desktop-file-validate` 校验通过（板端实测）。

## 5. 实测记录

### 5.1 参考：x86 开发主机（2026-11-14，非板端）

用于确认自检逻辑本身可用：

- `python3 -m video_player --check` → **9 ok / 7 warn / 0 fail**
- 通过项：Python 3.10、GTK4 4.6.9、GStreamer 1.20.3、`uridecodebin`、
  `videoconvert`、`glupload`、`glshader`、`pulsesink`、显示会话
- 预期缺失（板端才有）：`mppvideodec`、`mpph264enc`、`waylandsink`、
  `gtk4paintablesink`、`librknnrt.so`、`librga.so`、RKNN 模型

### 5.2 目标板实测（M1–M5 已回填）

| 日期 | 项目 | 结果 | 备注 |
|------|------|------|------|
| 2026-11-14 | 板端能力审计（SSH 只读） | GStreamer 1.26.2；GTK 4.18.6；`gtk4paintablesink` 可用；`mppvideodec`/`mpph264enc`/`waylandsink`/`glshader` 可用；`/usr/lib/librknnrt.so` 存在；`librga 2.2.0`；`rknnlite` 未安装；无 `rga` GStreamer 元素；DSI-1 connected；磁盘剩余 14G | 明细见 §1，M1 起逐项复测 |
| 2026-11-14 | M1 CPU `videoconvert` 瓶颈 | 1080p NV12→RGBA 仅约 15fps（12s 素材 19.9–23.3s，单核打满） | 改用 GPU 色彩转换 |
| 2026-11-14 | M1 GPU 色彩转换 | `glupload+glcolorconvert`→`GLMemory` RGBA：12s 素材 3.27s（≈110fps 吞吐） | 已用于播放链路（`video_convert=gl`） |
| 2026-11-14 | M1 播放窗口实测 | 1080p30 稳定 30fps，进程 CPU 16–17%，信息面板 fps 28.7–28.9；快照 `docs/evidence/m1-ui-gl.png` | A1 达标 |
| 2026-11-14 | M1 软件降级 | `video_convert=sw`：15–18 buffers/s、窗口约 3.7fps；快照 `docs/evidence/m1-ui-sw.png` | 仅作兜底 |
| 2026-11-14 | M1 退出崩溃修复 | 快照退出频繁 SIGSEGV（Gst/GL 终结顺序）；改为先销毁窗口/管线，GTK run 结束后 `os._exit`；5/5 稳定 rc=0 | 见 ARCHITECTURE 第 8 节 |
| 2026-11-16 | M2 模型转换 | YuNet ONNX 232,589B → RKNN 1,084,515B（fp16，target rk3576，mean 0/std 1） | x86 rknn-toolkit2 2.5.0 |
| 2026-11-16 | M2 RKNN 推理 | 首次 35.4 ms，10 次中位 30.1 ms（640×640 输入，单核 mask 0/1/2 同速） | `scripts/check_yunet_board.py` |
| 2026-11-16 | M2 检测精度 | 与板端 cv2 YuNet 对比 IoU 0.74–0.89（OpenCV letterbox 与拉伸预处理差异） | obama / two_people |
| 2026-11-16 | M2 播放叠加检测 | 720p 稳定 30 fps、1080p 双人脸 28.8 fps；信息行 `NPU: N face · 37.8 ms`；开检测进程 CPU ≈82%，关 ≈30% | `docs/evidence/m2-ui-*.png` |
| 2026-11-16 | M2 检测吞吐 | headless `measure_tap.py`：30.0 fps、10 次检测/s、平均 35.6 ms | 单脸每帧 |
| 2026-11-17 | M2 显示矩阵（rotate-180） | 手机竖录 MP4 的 Display Matrix 为 180°，`mppvideodec` 输出未旋转帧、GTK sink 按 tag 旋转显示，导致 pad probe 送检的是倒置帧、人脸框只框住上半脸；按 `image-orientation` tag 在检测线程旋转 BGR 后修复，整脸框与 5 点对齐，NPU 48.6 ms，28–30 fps | `qtdemux` tag `rotate-180`；用户素材实测 |
| 2026-11-17 | M3 `glshader` 通路验证 | headless GL 下自定义 fragment shader + uniform 输出纯色，PNG 像素 `(0.1,0.6,0.9)` 精确命中；写 double/int 全黑 → 必须 `G_TYPE_FLOAT` | 探针 `.cache/m3probe/probe_glshader.py`；约束回填 ARCHITECTURE §9 |
| 2026-11-17 | M3 美颜 GPU 成本（探针） | `perf_probe.py CONV=gl SIZE=1080`：`BEAUTY=0` 30.0fps/CPU 14–15%；`BEAUTY=1`（完整美颜）30.0fps/16–17%、0.93–1.0 ms/帧；`BEAUTY=2`（u_active=0 透传）与 `BEAUTY=3`（copy shader）同量级 | 美颜在 1080p30 上几乎免费 |
| 2026-11-17 | M3 GTK4 缩放慢路径 | 1080p 纹理、视频区宽 900/940/950/955 px 时 CPU 70–73%；960 px 起 15%，1280 px 17%；`GSK_RENDERER=cairo` 126% | 断点=缩放比 0.5（1920/960），即 A2 CPU 异常根因 |
| 2026-11-17 | M3 `glcolorscale` 预缩修复 | 940×700 窗口：无缩放 CPU 62–64%、GTK 只画 26.2 帧/s → `SCALE=1280x720` 后 CPU 18–20%、30.0 帧/s、beauty 0.76–1.38 ms | 新增 `player.texture_max_dimension=1280` |
| 2026-11-17 | M3 应用 A2（美颜开 + 检测关） | 1080p30 近景、1280×768 窗口：30.0–30.4fps、CPU 30–33%、`beauty: on 0.68–1.36 ms`；同场景美颜关 30.1–30.2fps/30–32% | 修复前同一配置 78–80% CPU |
| 2026-11-17 | M3 应用（美颜开 + 检测开，真实人脸） | 1080p30 近景：稳定 30.0fps、CPU 110–119%、beauty 1.06–1.56 ms、NPU 37.5–44.0 ms、faces 1 | 检测是 CPU 主要来源；美颜仍 <1.6ms |
| 2026-11-17 | M3 demo 全开（美颜+检测+强制分屏） | 全屏 1280×800：30.0fps、CPU 117–131%、NPU 37.8–41.2 ms、beauty 1.1–1.5 ms；关美颜 112–121% | 增量主要来自 M2 检测（约 85pp） |
| 2026-11-18 | M1 A1 H.265 1080p30 硬解（补测） | `user-selfie-1080p30.mp4`（HEVC 1080p30、158MB/92.7s、rotate-180）经 `h265parse`+`mppvideodec` 硬解；demo 全开（美颜+检测+分屏）30.0 fps、`drop=0.000%`、NPU 43.0 ms、beauty 1.1 ms、画面正立且人脸框/蒙版对齐 | A1 的 H.264/H.265 均已达标；快照 `docs/evidence/m3-h265-selfie-demo.png` |
| 2026-11-18 | M3 fps 读数修正（旧「掉速」证伪） | 界面/PERF 的「周期性 20–25 fps 掉速」不存在：同步采集瞬时 fps、累计平均值与墙钟帧率，全程 `sig_fps 28.9–31`、`drop=0.000%`、墙钟 30.0 帧/s；21–25 是 `fpsdisplaysink` 的 `average_fps`（累计平均，起播丢帧后要几十秒才爬回）+ PERF 采样早于 UI 校正 | 改用 `measured_fps()`（1.5 s 滑窗到达率）；`_maybe_log_perf()` 移到 `_tick()` 末尾；原「检测路径阻塞 sink」归因证伪 |
| 2026-11-18 | M3 预热位置回归（std::bad_alloc） | 模型 `yunet_rk3576.rknn` 就位后，1080p 自拍 + 检测启动在 `start()`（pad-added / streaming thread）执行 1080p 零帧预热，`rknn_inputs_set` 抛 C++ `std::bad_alloc`，进程 SIGABRT；同素材关检测正常 | 预热移到 `prepare()`：pipeline PLAYING 前、主线程完成；板端复测 NPU 1 脸 46.4ms、beauty 1.5ms、快照成功 |
| 2026-11-18 | M3 起播丢帧根因（检测开） | 首个真实帧 RKNN 推理 **2.38–2.43 s**（之后 38–41 ms/帧），worker 忙这一秒多导致起播丢约 50% 帧；640×640 零图预热仅 30.9 ms 且**不覆盖大尺寸路径**（首真实帧仍 2.4 s）；另测得 `extract_dup` 1080p NV12 稳定 17–33 ms/次 | 修复：`FaceDetectionTap.start()` 用真实解码尺寸零帧预热（`warmup_frame_sizes()`，90/270 额外预热交换尺寸 ≈35 ms）；复测 cfg_det_only / cfg_faceon / demo 三组 `drop=0.000%`、fps 30.0 全程 |
| 2026-11-17 | M3 检测线程 CPU 归因 | `/proc/PID/task` 采样（总 CPU 115.2%）：主线程 27.3%、demux:sink 27.6%、video-decoder 15.0%、gstglcontext 11.3%；headless `measure_tap.py` 30.0fps、10 次/s、37.5 ms | M2 遗留成本，M3 未改动 |
| 2026-11-17 | M3 旋转（90/270/180） | `ffmpeg -display_rotation` 素材 + 四色标记图：tag `rotate-270/rotate-90/rotate-180` 与显示方向自洽；180° 自拍素材人脸框与遮罩对齐 | `face_rect_to_texture()` 反向映射正确（单测覆盖 0/90/180/270） |
| 2026-11-17 | M3 一键开关（F9 路径） | 运行时 `refresh_beauty()`：`beauty_active False→True`，beauty_ms 稳定 0.9–1.1 ms，无异常 | UI 开关走同一路径 |
| 2026-11-17 | M1 遗留 seek 缺陷 | PyGObject 绑定顺序为 `seek(rate, format, flags, ...)`；修复后 seek/倍速恢复，`tests/test_seek.py` 4 passed | 回归测试补齐 |
| 2026-11-18 | 录制链路原型（gst-launch） | 先验 `glvideomixer` 合成：video GL 940×528@30 + UI 1280×800@10fps + mixer 后 capsfilter 1280×800@30 → NV12 → `mpph264enc bps=8M`；输出 `h264 1280×800 30/1`、10.2s/306 帧，CPU ≈0.82–1.02 核 | 仅此一路；混流器要求每个 pad 都推进，UI pad 不能"复用旧帧" |
| 2026-11-18 | `--record` 应用内录制（成品） | `--demo --record /tmp/x.mp4 --record-duration N`：画布 = 背景 + 视频区（alpha 洞）+ sidebar/info/控制条快照 + 人脸框；`display_bypass=1` 让显示分支让出 GPU | ✅ 稳定 30.0 fps、`enc 30–32/s`、CPU 184–205%、compose 3–6 ms/tick；产物 `h264 1280×800 30/1`、10.8s/325 帧/5.2MB；录制时屏幕视频区为黑（旁路显示） | 抽帧 `.cache/m3shots/rec3_frame7.png`；架构与踩坑见 ARCHITECTURE §2.3 |
| 2026-11-18 | 录制模式拍摄演示素材 | `docs/demo-video/shoot.sh`（7 段：分屏/强度/关美颜/双人/遮罩/无人脸/自拍 H.265），每段 `--record-duration` 定时自动收尾 | 每段可播放、`nb_frames≈30×时长` | 7/7 成功（13.07/8.47/9.73/8.00/12.33/8.27/10.83s）；首轮 4 段缺 moov 用重试兜住；自拍 180° 录制需在录制链加 `glvideoflip`（已实现） |
| 2026-11-18 | M4 A5 1 h 长跑 | 板端 pid 42428：`--config /tmp/cfg_faceon.json /home/tris/soak_1h.mp4`（1080p30 循环播放 60 min，检测+美颜开），PERF 每秒 + 每分钟 RSS/线程/fd 采样；脚本 `.cache/a5/analyze.py` | ✅ 3549 个样本：t≥300 s 共 3250 个，均值 **30.06 fps**（min 24.5 / max 30.7），仅 7 个 <29.5、2 个 <25，且全部落在 t=1165–1170 s —— 当时我在板端并行部署/构建成片（NPU 同步飙到 117 ms）的外部负载窗口；最后 600 s 全部 ≥30.0 fps（均值 30.07）。RSS 330,664→335,548 kB（+4.8 MB/h）、threads 29–32、fds 74–79 无增长，播放结束自动退出（`app exited at t=3600s`） | A5 通过：无帧率衰减、无内存/句柄泄漏 |
| 2026-11-18 | M4 A4 正脸/侧脸/多人检出 | 固定素材逐条：单人 `docs/evidence/m2-ui-detect.png`、双人 `m2-ui-two-faces.png`、三人素材（Pexels 帧合成的 1080p30 8 s 片段：青年正脸 + 银发女士近景 + 访谈男子微侧）`/home/tris/demo-footage/06_three_faces.mp4`，demo 运行 `faces=3`、NPU 39.8–41.5 ms、fps 30.0 | ✅ A4 通过（1/2/3 脸 + 微侧脸），证据 `docs/evidence/a4-three-faces.png`、`a4-three-masks.png`（三脸遮罩预览） |
| 2026-11-18 | M5 检测保持（`detection.hold_ms`） | 01 近景 1080p H.264（复现 v2 闪烁）：`hold_ms=600` 时 129 次检测中 raw=0 共 12 次、美颜空档 **0** 次；`hold_ms=0` 时同样 raw=0 12 次 → 空档 **6** 次。07 微信自拍（v3 唯一源）`hold=600`：167 次检测、raw=0 **0**、空档 **0**。病态素材 03 空档 15→6 | 空档=相邻两次检测结果之间 faces 为空的次数；v2 闪烁根因是空档帧 `u_active=0` 整段透传；>600 ms 无脸的极差素材仍会残留空档（已知限制） |
| 2026-11-18 | M5 实验性 warp 性能（瘦脸/大眼） | 美颜+检测：30.0 fps / beauty 0.86–0.87 ms；美颜无检测：30.0 fps / 0.64–1.20 ms；美颜关：30.0 fps / 0.87–1.77 ms；美颜+检测+瘦脸/大眼=100：30.0 fps / 0.86–0.96 ms（warp 增量 ≤0.1 ms） | 单个 `glshader` pass 内完成反向 warp（新增 12 个 scalar uniform，共 37 个 float；2026-11-19 几何重做后 uniform 从 16 减到 12）；「美颜关」行的 0.87–1.77 ms 是透传元素开销 |
| 2026-11-18 | M5 纯视频录制吞吐 | 07 微信自拍 HEVC 1080p30 源：`Recorder(video_only=True)` + `mpph264enc` RGBA 直编稳态 ≈30 fps（wall 与内容时长一致），9 段主录制 `r_frame_rate` 均 `30/1` | `docs/demo-video/record_video.py`；另 1 段 `10_split_warp` 为 QC/成片补拍 |
| 2026-11-19 | M5 实验面板滚动回归 | 1280x800 板屏下展开「实验性」后旧代码把窗口撑到 1280x913，底部大眼滑杆点不到 | 侧栏套外层 `Gtk.ScrolledWindow`、playlist 固定 160 px、展开后 150 ms 自动滚底；复测窗口 1280x768，瘦脸/大眼均可见；`GLib.idle_add` 版本 exit 139，timeout 版本 exit 0 |
| 2026-11-19 | M5 成片 v3 自检（warp 重做后重剪） | `video-player-m5-demo-v3.mp4`：52.10 s / 1920×1080@30 / H.264 yuv420p / CRF21 / 35,433,497 B（5.44 Mbps）/ 无音轨 / 无 rotation side data / faststart（moov 在 mdat 前）/ sha256 `ca6b23b1…`；`framemd5` 1563 帧相邻重复帧 0（要求 <2%）；闪烁/纹理回弹检测 0 段（480×270 灰度中央人脸区 Laplacian 方差：中位数 1455.9、阈值 2620.6、最大 2156.8） | 抽帧目视（t=3/7.5/9.5/15/25/29/31.5/33.5/35.8/37.8/40.5/42/44.5/48.5 s）画面正立、无闪烁、字幕不遮脸、瘦脸为整体收窄且自然、大眼温和可辨；构建脚本 `docs/demo-video/build_video_v3.sh`（07/08/09/10 段来自 rec6） |
| 2026-11-19 | M5 warp 几何重做（用户反馈驱动） | 旧实现（下颌控制点局部位移 0.065×脸宽 + 圆形径向放大 0.40）用户反馈「瘦脸像脸凹进去一块、大眼不自然」；改为显示空间下半脸整体收窄（`SLIM_STRENGTH=0.16`）+ 眼周椭圆各向异性放大（`EYE_MAGNIFY=0.22`、半径 0.16×脸宽、纵横比 0.72），`warp_gate` 去掉肤色门限；CPU `cv2.remap` 参数网格 + 板端 A/B（slim0/slim100/eyes100/all100）+ v3 抽帧目视通过，warp 开销仍 ≤0.1 ms | 旧参数只调大小方向无法消除「局部凹陷/凸眼」，属于几何模型问题而非强度问题；A/B 需注意两次录制内容不同步（见 `docs/PITFALLS.md` 录制节） |
| 2026-11-24 | **M6 4K60 硬解与播放（本轮）** | 板端 `/tmp/4k60/` 三段 3840×2160@60/8 s/480 帧素材（H.264 测试卡、H.265 测试卡、1080p30 人像 bicubic 上采样 + 60fps）；`--demo` 全屏 + `VIDEO_PLAYER_PERF_LOG=1` + `--snapshot --snapshot-delay 12` | **H.264 稳态 60.0 fps、CPU 39–50%、beauty 2.0–2.7 ms；H.265 稳态 60.0 fps、CPU 29–38%、beauty 1.5–1.8 ms**（默认 `texture_max_dimension=1280`）；headless VPU 裸解 **186–187 fps**（480 帧 2.57–2.59 s，≈3.1× 实时），全部 `rc=0` | 证据 `docs/evidence/4k60/`（4 张快照 + 全套 PERF）；信息栏读数 `3840×2160 / X-H264 / mppvideodec / fps 60.0` |
| 2026-11-24 | 4K 纹理预缩 vs 全帧（`texture_max_dimension` 1280→0） | 同素材同配置，仅改 `texture_max_dimension` | 1280 预缩：稳态 60.0 fps；**0（全 4K 进 GTK）：稳态 58.0 fps，起播 1 s 掉到 8.0 fps，beauty 8–10 ms（约 4 倍）**，CPU 反而更低（8–15%，缩放转由 GPU 承担） | `perf_B_full4k_nodetect.log`；证明 `texture_max_dimension=1280` 是 4K60 稳 60 的关键设置 |
| 2026-11-24 | 4K60 + NPU 人脸检测 | 同素材，`detection.enabled=true`、`interval_frames=3` | **fps 在 20.7–60.0 间抖动（均值≈35）、单帧 NPU 54–90 ms、CPU 182–289%**；检测是唯一把 4K60 拉离 60 fps 的因素 | `perf_C_1280_detect.log`、`shot_C_1280_detect.png`（人脸框正常）；1080p30 下 NPU 37–44 ms 本可摊薄，帧预算从 33 ms 收紧到 16.7 ms 后暴露 |
| 2026-11-24 | **8K 解码与播放（本轮）** | 板端 `/home/tris/demo-8k/videos/`：8K60 H.264 实拍（7680×4320 59.94fps / 16.1 Mbps / 24.9 s）、8K30 H.264 实拍（2.6 Mbps / 27 s）、8K30 H.265 硬解→硬编转码（8.4 Mbps / 6.1 s，真实风景）；`--demo` + `demo_playback.json` + `VIDEO_PLAYER_PERF_LOG=1` + `--snapshot-delay 3` | **能解能播**：8K30 H.264 **30.0 fps 稳定**（CPU 21%）；8K60 H.264 **59.9 fps**（CPU 40–56%，片尾掉 17.5）；headless VPU 裸解 8K30 H.265 **44.7 fps**、8K60 H.264 **47.8 fps**（均 rc=0） | 信息栏读数 `7680×4320 / X-H264 / mppvideodec / 帧率 30.0`；证据 `docs/evidence/8k/`（4 张快照 + 4 轮执行输出 + README） |
| 2026-11-24 | 8K 渲染稳定性（关键边界） | 同 8K 素材，改 `texture_max_dimension` 与编码 | ❌ **8K30 H.265 出图后 SIGSEGV（rc=-11）**；**`texture_max_dimension=0`（全 8K 进 GTK）直接 SIGSEGV**；8K H.265 硬编彩条 GPU BO `PANFROST_CREATE_BO err=12`（ENOMEM）、18.7 fps / 60 s 超时；首轮 8K 播放把 GNOME shell 拖崩（`GDK_IS_DISPLAY` 断言），gdm 自动重启后恢复 | 结论：**8K 可探测、不可交付**；4K60 仍是可靠上限（预缩 1280 是 4K 保命设置，8K 下也救不了） |
| 2026-11-24 | 8K 硬编与软编对照 | `mpph265enc` 8K30 彩条 vs `libx265` 8K30 2 s | ✅ 硬编 rc=0（68.8 KB / 60 帧，`w:h [7680:4320]`）；❌ 软编 `libx265` 被 OOM killer 杀（rc=-9，3.8 GB RAM 不足） | 硬编 8K 超出 datasheet 的 4K60 上限，属实测超水平；软编路线在板端不可行 |

## 6. 已知环境限制与复现命令

- **8K 会击穿渲染管线**：8K 进 GTK 后触发 `MESA: DRM_IOCTL_PANFROST_CREATE_BO failed (err=12)`
  （GPU BO/显存分配失败，ENOMEM），随后 `mppvideodec` 分支正常但 UI 进程 SIGSEGV；
  严重时连 GNOME shell 一起崩，`/run/user/1000/wayland-0` 短时不可用、GUI 报
  `gtk_icon_theme_get_for_display: assertion 'GDK_IS_DISPLAY (display)' failed`。
  gdm 约 1 分钟内自动重启 shell（`pgrep -a gnome-shell` 可确认），恢复后可继续；
  **跑 8K 前先确认没有演示在用板子**。8K 素材与复现命令见 `docs/evidence/8k/README.md`。
- 板端图形会话依赖物理登录：GNOME/gdm 未配置自动登录
  （`/etc/gdm3/daemon.conf` → `AutomaticLoginEnable=false`）。2026-11-17 上午
  连续多轮 1080p 全屏播放/截图期间触发 OOM（journal 记录
  `app-gnome-org.gnome.SettingsDaemon.DiskUtilityNotify` 被 OOM killer 终止），
  随后 tris 的 Wayland 会话退出、回到 gdm 登录界面；SSH 侧只能看到 greeter
  会话，`/run/user/1000/wayland-0` 消失，GUI 断言会报
  `RuntimeError: Gtk couldn't be initialized`。恢复方式：在板端物理登录后
  重新执行下文命令（无需改代码）。
- SSH 运行 GUI 前必须显式设置（板端 `/tmp/43-m2` 为工作副本）：
  ```bash
  export XDG_RUNTIME_DIR=/run/user/1000 WAYLAND_DISPLAY=wayland-0 GST_GL_PLATFORM=egl
  ```
  截图只能用应用内快照（`--snapshot`，`Gtk.WidgetPaintable` + `render_texture`），
  `x11grab` 抓不到 GTK/GL 画面。
- M3 复现命令（板端项目目录；`cfg_B` = 美颜开+检测关，`cfg_C` = 都关，
  `demo` = 默认配置强制分屏）：
  ```bash
  export VIDEO_PLAYER_PERF_LOG=1
  python3 scripts/launch_player.py --config /tmp/cfg_B.json /tmp/face_1080p30.mp4
  python3 scripts/launch_player.py --demo /tmp/face_1080p30.mp4 \
    --snapshot /tmp/m3shots/demo_default.png --snapshot-delay 12
  # 探针：美颜成本与预缩放对照
  SAMPLE=/tmp/m1_sample.mp4 CONV=gl SIZE=1080 BEAUTY=1 SCALE=1280x720 \
    python3 scripts/perf_probe.py
  ```
  `PERF` 行格式：`PERF t=15s fps=30.1 beauty=1.20ms faces=1 npu=38.9ms cpu=31%`。
- M4 录制模式（`--record`）复现命令：
  ```bash
  REC_DEBUG=1 python3 scripts/launch_player.py --demo \
    --record /tmp/demo.mp4 --record-duration 10 --config /tmp/cfg_faceon.json \
    /tmp/face_1080p30.mp4
  ffprobe -v error -show_entries stream=width,height,r_frame_rate,nb_frames \
    -of default=nw=1 /tmp/demo.mp4
  # 演示素材批量拍摄（板端 docs/demo-video/ 已部署配置与脚本）
  bash /home/tris/demo-video/shoot.sh            # 7 段全部
  bash /home/tris/demo-video/shoot.sh 05 07      # 只拍指定段
  bash /home/tris/demo-video/build_video.sh      # 剪接 + 字幕 → out/*.mp4
  ```
  备注：录制时 DSI 屏上视频区是黑的（`display_bypass`，GPU 让给编码），录制文件
  本身正常；`--snapshot` 在 windowed 模式会失败，录制/截图都在 `--demo` 全屏下做。
- 本机（x86 开发机，**非板端**）后期工具链限制：无系统 `ffmpeg`/`ffprobe`、无 sudo，
  统一用 `.cache/bin/ffmpeg`、`.cache/bin/ffprobe`（ffmpeg 7.0.2-static，含
  `ass`/`subtitles`/`xfade`/`fade`/`drawtext`）；本机也没有 WenQuanYi 字体，字幕
  烧录前从板端拷 `/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc` 到 `.cache/fonts/`，
  滤镜加 `fontsdir=.cache/fonts`。板端相反：系统自带 ffmpeg 与 WenQuanYi Zen Hei，
  可直接用系统命令；v3 成片即由 `docs/demo-video/build_video_v3.sh` 在此组合下构建。
- 测试素材在板端 /tmp（非仓库）：`/tmp/face_1080p30.mp4`（近景，
  由 `test-media/pexels/5534415…` 转码）、`/tmp/face_rot180.mp4`、
  `/tmp/face_selfie.mp4`、`/tmp/m1_sample.mp4`（1080p30 双人）、
  `/tmp/marker*.mp4`（旋转方向验证）。

> 回填方式：`python3 scripts/probe_board.py --markdown` 的输出可直接贴入本文件
> 对应小节，并保留命令与日期，保证可复现。

## 2026-11-19 侧栏滚轮、操作/崩溃诊断（板端验证）

- 板端屏 `1280x800`，播放器窗口 1280x768。侧栏外层滚动区（`_sidebar_scroll`）建立后，
  鼠标滚轮在磨皮/美白/分割/瘦脸/大眼 5 个 `Gtk.Scale` 上默认只改滑杆数值，不能滚动页面。
- 修复：每个 scale 装 `Gtk.EventControllerScroll`（VERTICAL，CAPTURE 阶段），回调把
  `dy * 48` 加到 `_sidebar_scroll` 的 vadjustment；页内容不超高时返回 False 放行。
- 板端 handler 级验证：展开「实验性」后 `upper=726 page=721`；5 个 scale 各 2 个 scroll
  controller；`_on_page_wheel_scroll(None,0,3)` 返回 True，value 0→5（clamp 到
  `upper-page`），`dy=-100` 回 0，`dy=0` 返回 False。
- 新增 `src/video_player/diagnostics.py`：启动时把 `faulthandler` 输出写到
  `$XDG_CACHE_HOME/video-player-demo/crash.log`（默认 `~/.cache/video-player-demo/`），
  关键 UI 操作写到同目录 `actions.log`。板端用 `ctypes.string_at(0)` 验证 crash.log
  捕获 SIGSEGV 栈；用安装后的 `/home/tris/.local/bin/video-player-demo --snapshot`
  验证 actions.log 记录 `window_open`/`play_path`/`playback_state`/`window_close`。
- 用户报告的“视频放着闪退”无法用脚本复现：正常关窗 exit 0；warp=100 30s、30 轮
  展开/收起+滑杆+对比+语言、fullscreen 20 次均 exit 0。桌面启动实例日志仍无栈；
  已部署诊断日志等待下一次复现。桌面 at-spi 注册失败是**全系统**现象
  （gnome-shell/DING 都报 `AT-SPI: Could not obtain desktop path or name`），不单独归因于播放器。
- 部署：`window.py` md5 `6d95fe5f7fa959203ea4000c34a86f9b`，`diagnostics.py`
  `96ac58d1fd05ed2d72ec3478eeee43ff`；`--check` 17 ok / 0 warn / 0 fail。

## 2026-11-24 UI 重排 + 全屏沉浸 HUD（板端验证）

- 控制条改两行：`.seek-row`（进度 + 时间码）在上，`.button-row` 在下；顺序
  `上一 / 播放 / 下一 ┊ 停止（ghost）┊ 倍速 ┊ 解码三段 / 弹性空白 / 音量 / CC / 全屏`。
  解码三段（自动 / 硬解 / 软解）从侧栏搬到控制条，全屏时也能切。
- 配色改中性石墨（底 `#0D0F11`、面板 `#15181B`、文字 `#E7E4DF`、钢蓝强调 `#7EA8C4`），
  避免装饰色污染肤色参照；语义色只留给解路径与故障（琥珀 = 硬解降级，红 = 失败）。
- 全屏沉浸：信息行 / 控制条 / 提示重挂进 `Gtk.Overlay` 上的浮动 dock
  （2.5 s 静止淡出、底部 160 px 内视为悬停、暂停常显、字幕 `margin_bottom` 抬到 150 px）。
  `--record` 时不进沉浸，避免录制画布缺块。
- 板端实测（1280x768 窗口 / 1280x800 全屏，`03_selfie` 与 `01_closeup`）：
  窗口态 30.0 fps、NPU 39–41 ms、美颜 1.1–2.7 ms；全屏 HUD 显示 → 8 s 时已淡出，
  只剩画面 + 人脸框。证据 `docs/evidence/m7-ui-window.png`、`m7-ui-fullscreen.png`、
  `m7-demo-hud.png`、`m7-demo-immersive.png`。
- 修掉三个只有板端才暴露的样式/计时问题（详见 `docs/PITFALLS.md` §2）：
  `Gtk.MenuButton` 的 CSS 节点是 `menubutton`（CC 一直白底）；`Gtk.Box` 默认
  `valign=FILL` 把播放键拉成椭圆；Wayland 对静止指针持续投递 `motion`，把 dock
  淡出截止时间一直推后。
- 部署：24 个文件（`src/` 22 + `configs/` 2）经 paramiko SFTP 明文写入
  `/home/tris/video-player-demo`，逐个 md5 校验一致；备份
  `/home/tris/video-player-demo.bak-ui20261124-164847`；`--check --require-board`
  17 ok / 0 warn / 0 fail。M7 的 `window.py` md5 `f39ec6a1dd8b97465de5cf06dd4e818e`。

## 2026-11-24 M7.1 版面对齐效果图（板端验证）

用户反馈「左下角小字不要了、界面填满到底、和效果图不一致」。对照 `mockups/g-1.png` 改了四处：

- **删掉控制条下方的常驻提示行**：它占 18 px，窗口看起来像没到底。提示改成画面上方的
  `Gtk.Revealer` toast（CROSSFADE 180 ms，6 s 后自动淡出），解码降级/续播/到头等提示都还在，
  但不再占布局高度。全屏 dock 里因此只搬信息行与控制条。
- **顶栏改两行标题**：`Gtk.HeaderBar.set_title_widget()` 放文件名 + `时长 · 分辨率 · 编码 · fps`
  副标题；「打开」改成图标 + 文字（`document-open-symbolic`）；右侧顺序改为 对比 → 语言 → ☰。
- **信息行精简**：`1920×1080 · H.264 · mppvideodec · 30.0 fps · NPU 1 张脸 41 ms · 美颜 1.6 毫秒`，
  去掉「解码：/帧率：/NPU：/美颜：」标签，改成中点分隔，字号 9pt、固定 30 px 高。
- **按钮回到 44 px**（播放键仍 48 px 圆形），与效果图的 `tb 44 / play 48` 一致；顺带修掉
  `scale:focus` 的白色焦点框、可选中 Label 的 caret 竖线。

板端实测（1280×768 窗口 / 1280×800 全屏）：控制条贴底、无空白行；30–31 fps、NPU 40 ms 量级、
美颜 1.6 ms。证据 `docs/evidence/m7-ui-window.png`、`m7-ui-fullscreen.png`。
本轮同样是全量部署：`deploy.py --apply` 后 `--dry-run` 显示 `local files: 24  remote known: 24  to upload: 0`，即板端与本地零差异；板端 `--check --require-board` 17 ok / 0 warn / 0 fail，安装入口 `~/.local/bin/video-player-demo --version` 正常。
`window.py` md5（Python 明文）`c74effcd8cf125ed2ff700c95d0e1e0c`（上一节 `f39ec6a1…` 为 M7 版本）。

## 2026-11-24 M7.2 全屏 dock 紧凑化（板端验证）

用户反馈「全屏之后，显示的进度条等信息，占的屏幕面积太大了」。板端实测旧 dock
**190 px / 800 = 23.8%**，改后 **120 px = 15.0%**（省 37%）。

- 改动全部限定在 `.transport-dock` 作用域，窗口态仍是 44 px 触控尺寸：
  dock `padding 4 10 6 10`、`radius 12`、`margin 12`、内部 `spacing 2`；
  信息行 `7.5 pt / 18 px`；seek 行高 18、槽 4 px、滑块 12 px、时间码 8.5 pt；
  按钮 38 px（播放 44 圆形）、倍速 38、解码段 30/46、分隔线 `margin 4 2`。
- 常量随实测修正：`DOCK_HOVER_BAND 160 → 140`、`DOCK_SUBTITLE_CLEARANCE 150 → 140`
  （120 dock + 12 底距 + 8 余量）。按原先的 110 会把字幕压在 dock 下面。
- `tests/test_fullscreen_dock.py` 改为读真实常量（`CLEARANCE`），不再硬编码 150。
- 板端：24 文件零差异，`--check --require-board` 17 ok / 0 warn / 0 fail；
  备份 `/home/tris/video-player-demo.bak-ui20261124-190800`。
  证据 `docs/evidence/m7-ui-fullscreen-compact.png`，
  `window.py` md5 `5de37acc7b8bf5a92d826fadbd713158`。
- 比例沟通用设计稿 `mockups/g-fullscreen-compact.html` → `mockups/g-3.png`（同尺寸，98 px）。

## 2026-11-24 M7.3 美颜默认关闭（板端验证）

用户要求「把美颜效果设为默认关闭」。先定位真源：`Config.load()` 在没有 `--config`
时读 `configs/player.example.json`，所以真正的默认值是那个 JSON，不是 dataclass
默认值——两处必须同时改，否则缺文件与有文件两种启动会不一致。

- `configs/player.example.json`：`beauty.enabled` `true → false`（只改这一行，保持最小 diff）。
- `src/video_player/config.py`：`BeautyConfig.enabled: bool = False`，
  维持 `Config() == Config.load()` 不变式；`tests/test_config.py` 断言同步，
  并补一条 `example.beauty.enabled is False`。
- `_apply_demo_mode()` 显式开启（`enabled = True` + switch `set_active(True)`）：
  shader 在 `u_active <= 0.5` 时直接返回原图，F9 对比分割线也在 active 分支里，
  不在 demo 里 opt-in 的话 `--demo` 会退化成一张普通全屏画面。
- 桌面入口 `Exec=$LAUNCHER %f` 不带 `--demo`，所以从菜单/启动器打开即为关闭状态；
  `B` 键或侧栏「美颜（次要功能）」随时可开。
- 板端：4 文件更新（config JSON + app.py + config.py + window.py），md5 4/4 校验通过；
  备份 `/home/tris/video-player-demo.bak-ui20261124-191922`；
  `--check --require-board` 17 ok / 0 warn / 0 fail。
- 证据：`docs/evidence/m7-beauty-default-off.png`（信息行 `美颜 关`、无分割线）、
  `docs/evidence/m7-demo-beauty-on.png`（`--demo` 仍分屏，`美颜 2.9 毫秒`）。
  md5：`window.py 6460e58f24a625d6913db118c2fbe84e`、
  `config.py 89d819e6c4639de57bcb2681eddc6951`、`app.py a9cc8776a54bf619dfa3405cc7141c54`、
  `configs/player.example.json 21c64c87dd3972851600291987f355d9`。

## 2026-11-24 M7.4 人脸检测默认关闭（板端验证）

用户反馈「点击视频后不立即播放，要等一会」。诊断结论：`FaceDetectionTap.prepare()`
在 `backend.open()` 里**同步**预热 NPU（模型 66 ms + 1080p 全零帧 1761 ms），占住
GTK 主线程；`close()` 每次释放 RKNN context，所以每切一片都重来。用户选方案 1：
默认关检测。

- 默认值：`configs/player.example.json` `detection.enabled=false`、
  `DetectionConfig.enabled=False`、`tests/test_config.py` 断言同步。
- `window.py`：`_show_faces` 默认 False；新增 `_detection_opted_in`（自定义 `--config`
  显式 true 时始终开）；`_sync_detection_need()` 只在 `backend.open()` 前解析
  `demo || beauty.enabled || _show_faces || opt-in`；**UI v4（2026-11-26）起**播放中拨
  开美颜/人脸框不再只提示「切换视频后生效」，而是记住当前播放位置、在信号返回后
  `GLib.idle_add` 自动重开当前视频并 toast「正在启用人脸检测（约2秒）」
  （`_on_detection_dependency_toggled` → `_reopen_for_detection`）；`--demo` 显式开启
  （美颜 oval mask 依赖 NPU 框，无框时 `face_mask` 返回 0，效果会整片消失）。
- 顺手修：`_on_detection()` 不再因 `_show_faces=False` 提前 return —— 关掉人脸框后
  信息行原本恒显示 `NPU —`，即使美颜还在用这些框。
- 板端实测 `play_path → play_path_ok`：

| 启动方式 | 耗时 | 说明 |
|---|---|---|
| 默认配置 | **36–38 ms** | 原 3132 ms，点击即播 |
| `--demo` | 3132 ms | 检测 + 美颜 + 分屏，一次预热 |
| 临时配置 `beauty.enabled=true`（detection 留 false） | 3073 ms | 联动拉起检测，信息行 `NPU 1 张脸 41.0 毫秒` |

**运行中开美颜自动重开实测（2026-11-26，UI v4 修复）**：普通启动
`python3 -m video_player` + 30 s 1080p30 H.264 素材，真实 `Gtk.Switch` 信号路径，
探针 `.cache/qc/detection-restart/verify_detection_restart.py`（板端
`/home/tris/detection-restart-accept/`，原始 JSON 同名 `.json`）：

| 场景 | 开关后 | 结果 |
|---|---|---|
| 播放到 3.05 s，开美颜 | 立即：hint「正在启用人脸检测（约2秒）」、`reopen_id` 已排队、`detection.enabled=False`、shader `u_active=1.0` 但无 `u_face0`（= 画面无变化，即用户报的「开了没效果」） | **3.48–3.58 s** 后人脸进入着色器：`faces 1–2`、`u_face0_w≈0.279`、NPU 42–75 ms、位置 3.19 s（未回到 0） |
| 同上，稳定 1.5 s 后 | 位置 4.69 s、`faces 3`、`NPU 42.4 ms`、`beauty 2.06 ms`、`u_face0_w 0.245` | 美颜持续生效 |
| 播放到 3.06 s，先开人脸框 | 立即 hint + 重开队列（192 ms 内无重复重开） | **3.51 s** 后 `faces 1`、overlay 画 1 个 L 框、`NPU 38.7 ms`；随后再开美颜 `reopen_id=0`、`u_active=1`、`u_face0_w 0.246` |
| 开关重复拨动 | 第二次拨动取消旧 idle、只保留一个重开；关掉最后一个依赖项则取消重开并撤掉 toast | 单元测试覆盖（`tests/test_detection_restart.py` 12 项） |

板端 `--check --require-board`：**17 ok / 0 warn / 0 fail**；部署哈希
`window.py 15636bb8…`、`i18n.py 07633c9a…`（与本地逐字节一致）。

- 部署：首次 5 文件 + 读数修复 1 文件，md5 全部校验；`--check --require-board`
  17 ok / 0 warn / 0 fail；备份 `video-player-demo.bak-ui20261125-091526`、
  `...091909`。
- 证据：`m7-detection-default-off.png`、`m7-demo-detection-on.png`、
  `m7-beauty-requests-detection.png`。md5：`window.py
  f5760dd01129c5560b3d55ce4dd06d6d`、`config.py a99da031cf515351547e72efa7ede8b5`、
  `app.py 4c8714c866bc1b713bc7bc8b00cb4c48`、`i18n.py 7696056e243ca8b4093b7dd6d9d22a5c`、
  `player.example.json 02e739081506c23ececbb8d2547a4f9f`。

## 2026-11-25 M7.5 解码三段按钮切换修复（板端验证）

用户反馈控制条的「自动 / 硬解 / 软解」点一次后就不再生效。根因是 `_on_decode_toggled()`
对 GTK 互斥组里**被取消**的按钮执行 `set_active(True)` 强制回写：GTK 切换时先给旧按钮发
`toggled(False)`，回调把它重新点亮，又按组规则压回新按钮，两个按钮互相触发信号，按钮状态
与 `config.player.decode_mode` 脱节，第一次之后就被 `mode == config` 的提前 return 吞掉。

- `window.py`：
  - 收到取消通知直接 `return`，不再回写；互斥组本身保证恰好一个 active。
  - 新增 `_sync_decode_buttons()`：用 `_updating_decode` 包住程序化 `set_active()`，每次用户
    切换后把按钮状态重新对齐 config；初始化也走这条路径。
  - `_apply_decode_mode()` 改为 `GLib.idle_add(self._reopen_for_decode_mode, ...)`，把管线
    重建推迟到 `toggled` 信号返回之后；快速连点时先 `source_remove` 旧 idle，只保留最新一次；
    `close-request` 里清理 pending idle。
- 回归测试：`tests/test_decode_segments.py`（9 个用例，AST 抽方法 + 复刻 GTK「先取消旧按钮、
  再激活新按钮」的派发顺序），覆盖连续三次切换、取消通知不回写、防重入同步、idle 防抖、
  关窗后不重建、无媒体只提示。
- 板端实测：
  - `python3 -m py_compile` 通过；`--check --require-board` 17 ok / 0 warn / 0 fail。
  - 9 个逻辑用例全过；再用**真实 `Gtk.ToggleButton` 互斥组**连续切换
    `hw → sw → auto → hw` 四次，四次都触发重建，且每次组内只有一个 active。
  - 启动实拍 `docs/evidence/m75-decode-switch.png`：三段按钮只有「自动」高亮，信息行
    `mppvieodec · 30.0 fps · 美颜 关`，无报错。
- 部署：备份 `video-player-demo.bak-ui20261125-094447`，上传 1 文件、md5 校验 1/1。
  md5：`window.py 73f2fd1203145d335c268406c92342dc`、`test_decode_segments.py
  dc13c74f5c563c8cb4d3498cdf90c208`。


## 2026-11-26 UI v3 全量改造（板端验收）

按 `mockups/ui-v3` 改造播放器 UI，范围：空态拖放区、带标签信息行与 `H.264/H.265`
归一、44 px 两行控制条、侧栏顶部美颜面板、不透明全屏 dock + 美颜/对比 pill、
淡出后常驻遥测与 2 px 进度线、分屏「原图/美颜」标签、四角 L 形人脸框。
只改 UI/文案层，`backend.py`、`beauty.py`、`recording.py`、`pipeline.py`、
`config.py`、`ui/capture.py` 均未改动。

**部署与自检**

- 板端项目 `/home/tris/video-player-demo`，完整同步 `src/` 23 个 `.py`（含新增
  `ui/chrome.py`）；旧源码备份
  `/home/tris/.cache/video-player-demo/deploy-backups/20261126-115030-ui-v3-src`。
- 部署后哈希：`window.py dd16d0cd…`、`ui/chrome.py ad3fc983…`、`i18n.py 58257216…`；
  本机与板端逐一相同。
- `PYTHONPATH=src python3 -m video_player --check --require-board`：
  **17 ok / 0 warn / 0 fail / 0 skip**。
- 验收素材：`/home/tris/demo-footage/ui-v3-accept-1080p30-h264.mp4`
  （H.264 1920×1080@30，8.0 s，5,102,988 B，sha256 `f32297fa…9cb9`），
  从本地自拍素材截取；只用于本次 UI 验收，不进入发布物料。

**实测读数（板端快照 + 临时采集脚本）**

| 项目 | 实测 |
|---|---|
| 窗口内容尺寸 | 1280×768（GNOME 顶栏占 32 px）；全屏 1280×800 |
| 控制条（窗口态） | `prev/play = 44×44`，`seek = 30 px`，整条 `108 px` 高；两行布局 |
| 控制条（全屏 dock） | `prev/play = 44×44`，`seek = 22 px`，dock `98 px` 高；不透明面板 |
| 侧栏 | 视口 709 px；快捷键提示 `y=650 / h=15`，底部完整可见（最近播放固定 128 px） |
| 信息行 | `分辨率 1920×1080 · 编码 H.264 · 解码 硬解 · 帧率 30.0`；`● 人脸 2 · 41 ms · 美颜 开 · 1.4 ms` |
| 全屏遥测 | `1920×1080 · H.264 · 硬解 · 30.0 fps ● 人脸 1 · 43 ms · 美颜 开 · 1.5 ms` |
| 淡出后 | dock 隐藏，遥测降到 62% 仍可见，底边 2 px 进度线常驻 |
| 人脸框 | 四角 L 形 + 置信度 `人脸 0.91` / `人脸 0.54`；无整框、无 5 点圆点绘制 |
| 语言 | 采集脚本显式 `language="zh"`；英文字符串也已在本地/此前板端截图中走通 |

**证据**（`docs/evidence/`）

| 文件 | 场景 |
|---|---|
| `ui-v3-01-empty.png` | 无片空态：传输栏与信息行隐藏，视频区为拖放区 + 主/次 CTA + 能力 chip |
| `ui-v3-02-window.png` | 窗口态播放：带标签信息行、44 px 控制条、顶部美颜面板、两脸 L 框 |
| `ui-v3-03-fullscreen-controls.png` | 全屏唤出：不透明 dock、美颜/对比 pill、左上单行遥测 |
| `ui-v3-04-fullscreen-idle.png` | 全屏淡出：dock 隐藏，62% 遥测 + 底部 2 px 进度线保留 |
| `ui-v3-05-fullscreen-compare.png` | 全屏分屏：原图/美颜标签 + 参数行，dock「对比」激活 |

**未验证 / 已知限制**

- 真实触摸拖放、`Gtk.FileDialog` 选文件夹未做人工点击验收；本次只验证程序化截图与
  空态/播放态切换。拖放代码路径为 `Gtk.DropTarget(Gdk.FileList)` +
  `expand_media_args()`，需要后续触摸屏实测。
- `--record` 未重录/未做质量门；本次未改录制编排，录制画布仍按窗口态控件合成。
  新增的分屏标签与全屏遥测不进入录制画布；如果后续要重录演示片，需要另开任务决定
  是否把这两层纳入 capture 画布。
- `--snapshot` 在本轮多次采集中偶发一次 `ok=False`（重跑成功），属既有
  `Gtk.WidgetPaintable.to_node()` 首帧/时序问题，不是 UI v3 引入。

## 2026-11-26 ui-v5 P1/P2 本机实现（当晚已部署到板端）

P1（琥珀 token 化）与 P2（左导航 + 片库 / 空态）先在本机验证，随后于 20:33 整组部署到
`/home/tris/video-player-demo`（9 文件、哈希对账、原子替换；旧模块缺失时只覆盖
`window.py` 会 ImportError，所以 4 个新模块必须一起上），21:28 热修排序菜单后重部署。
板端实测：`--check --require-board` **17 ok / 0 fail**、GTK 4.18 CSS **0 errors / 23,346 B /
25 token**、片库 / 空态 / 播放三张 1280×768 截图验收；实例 PID 561659。部署明细与哈希在
内部 `STATE.md` §43/§44。

本机验证（部署前）：

本机实测（GTK 4.14.5 / GStreamer 1.24.2，X11 `:10.0`，无 NPU、无 H.264 软解）：

- 真实 `PlayerWindow` 启动落点：无参数 → browse；`--demo` → player；`--record` → player。
- 内容区 1280×709（窗口 1280×768，headerbar 58）：rail 87、页面 1140×687、
  卡片 273×173、12 张卡三行、`vadjustment.upper == page_size`，页脚完整可见。
- 片库素材为 12 条本地 VP9 片段（本机可软解）；元数据探测与海报取帧均走真实
  `probe.py` 路径，缩略图缓存命中后不再解码。
- 交互探针 58 项全过（含筛选为空时保留网格与提示）：chip / rail 筛选、三种排序、目录增删、语言切换、卡片进播放页、
  返回箭头与 `Esc`、播放后最近观看计数刷新。
- 截图与探针留在内部 `.cache/qc/ui-v5-p2/`（不发布）。

板端仍待人工验证：H.265/VP9/AV1 文件的探测耗时、`mppvideodec` 海报取帧与播放互斥、
触摸点按 chip / 卡片 / 目录选择、真实触摸拖放、排序 popover 的实际点按（§44 只在本机截过）。

## 2026-11-26 ui-v5 P3 首页 + 详情：本机实现（当晚已部署到板端）

P3 新增首页（hero + 继续观看 + 最近添加）与详情页（背景板 + 选集 + 文件参数 + 同目录），
并把左导航抽出成四项（首页 / 片库 / 最近 / 设置）。本机验证后已于当晚 22:16 整组部署到
`/home/tris/video-player-demo`（10 文件；旧板端是 P1+P2，6 个新模块缺失，只覆盖 `window.py`
会 ImportError）。板端实测见本节末尾。

本机实测（GTK 4.14.5 / GStreamer 1.24.2，X11 `:10.0`，无 NPU、无 H.264 软解、
VP9 软解；真实 `PlayerWindow`，探针 `.cache/qc/ui-v5-p3/probe.py`）：

- **窗口尺寸**：1280×768 固定不被内容撑大（内容 709 px）。home 内容 697 px
  （`6 + hero 225 + 10 + 228 + 10 + 224 + 6`）；hero 是弹性带（min 170），
  详情页 `268 + 12 + 414` 正好一屏。
- **首页**：hero = 最近观看（60 s 长片，`继续观看 00:20`），两行各 4 张真卡片；
  hero 大图 1140 px（`BackdropCache` 单 worker 串行解码，缓存按 size+mtime+width）；
  空片库 → 整页首启空态（取景框 + 选择目录/打开文件/前往片库）。
- **详情页**：4 chips、7 行参数（VP9 / 1280×720 / 25.000 fps / 01:00 / 9.6 MB / 1.3 Mbps /
  软件解码）、12 条选集（当前条琥珀描边 + `正在播放` + 自动滚入）、同目录 3 行；
  参数卡高度压到 408 px 后才落进 414 px 内容区。
- **真实续播**：seed `recent.json` + `resume.json`（60 s 长片、位置 00:20）→
  详情 CTA「从 00:20 继续」，播放后 `position_ns ≥ 19 s`；「重新播放」后 `< 1.5 s`。
- **交互**：93 项断言全过（80 full + 13 empty）——启动落点、卡片→详情、详情→播放→返回、
  首页卡片直接播放、两处「全部」、中英切换、同目录切集（header 跟随）、拖放规则、
  空态 CTA、`Esc` 回上一级、browse 页快捷键失效。
- pytest **531 passed / 15 skipped / 0 failed**（P2 基线 474/15；新增 `test_board_install.py` 10 项、`test_recording_layers.py` 2 项）；
  CSS 27,954 B / 25 token、GTK 4.14.5 解析 0 errors。
- 截图与探针留在内部 `.cache/qc/ui-v5-p3/`（不发布）。

### 板端部署与验收（2026-11-26 22:16–22:19，明细见内部 `STATE.md` §46）

- 10 文件整组替换（6 新增 + library_page/window/style/i18n），备份哈希、暂存哈希、部署后哈希
  三方一致；`--check --require-board` **17 ok / 0 fail**（重启前后各一次）。
- GTK 4.18.6：`style.CSS` **27,954 B / 25 token / 0 errors**；headerbar 实际 **58 px**，
  内容区 1280×709，窗口 1280×768。
- 首页 / 详情 / 片库 / 空态四张 1280×768 截图（单进程串行、各一次快照）与 54 项断言全过：
  首页 hero 设备行 `硬解`、详情参数行 `VPU 硬解`（琥珀 `is-accent`）、选集 6 / 同目录 3、
  片库 6 卡 4 列不滚动、空态 CTA 与扫描/格式栏完整；海报/背景板取帧日志有 MPP `h264d_api`。
- 实例 PID 628050（22:18:59，PPID=1，语言 en、无扫描目录 → 首页空态）。
- 仍未实机验证：触摸点按、真实拖放 / `Gtk.FileDialog`、排序 popover 点按、播放页实际播放 /
  续播 seek / 自动下一集、`--record`、H.265/VP9/AV1；首页/详情历史为隔离 state 预置。

### 离线安装包板端验证（2026-11-26 23:43–23:52，明细见内部 `STATE.md` §48）

- `bash board-install.sh /home/tris/ui-v5-offline-test/app --no-desktop`：解包 **68 文件**，
  `--require-board` **16 ok / 1 warn / 0 fail**（warn = 无 `models/*.rknn`），退出码 0；
  `--no-desktop` 不动用户既有启动器/桌面入口。
- 板端逐文件 `sha256sum -c` **68/68 一致**；脚本 sha256 与本地相同（`022d6384…`）。
- 从解包目录启动一次（PID 747601）：`actions.log` 记 `window_open` + `library_scan`，
  RSS ≈ 161 MiB，日志只有既有的 ZINK / GDBus 非致命两行；验证后退出，常驻实例 628050 未受影响。
- 真窗口探针从解包目录导入：**15/15 断言**，CSS 27,954 B、窗口 1280×768、ui-v5 P3 首页完整
  （hero 可续播、两行各 4 卡，海报/背景板取帧日志有 MPP `h264d_api`），截图 sha256 `e28fa798…`。
- 未覆盖：启动器/桌面入口/DING 安装、播放页 / 续播 / 录制、触摸、模型检测。

### 播放页板端回归（2026-11-26 23:55–2026-11-27 00:03，明细见内部 `STATE.md` §49）

覆盖 ui-v5 P3 播放页：详情续播 seek、EOS 自动下一集、`--record` 30 s。探针为一次性进程 + 一次快照，
每次用隔离 XDG state；测试期间停掉常驻实例，结束后恢复。

- **续播 seek（17/17）**：详情 CTA `从 00:20 继续` → 点击后起播 **20.050 s**；1.2 s 后 21.257 s；
  UI 时间码 `00:21`、toast `已从 00:20 继续播放`；`mppvideodec`、1920×1080、24 fps、`解码 硬解`。
- **EOS 自动下一集（12/12）**：8 s 短片播到尾部 → playlist **4 → 5**，下一集
  `ui-v3-demo-30s-h264.mp4` 在 **0.277 s** 起播，无错误卡，header/侧栏同步。
- **`--record` 30 s**：退出码 0，产物 `record-30s.mp4` 26,928,659 B / **927 帧 / 30.9 s /
  1280×768@30 / H.264 / 6.97 Mbps（无音频）**；视频 ROI 连续重复 54/897（**6.0%**）≈
  **28.2 unique fps**（旧 UI 同链短测为 34.29% / 19.86 fps，本次未改录制代码，差异来自 P3 布局）。
  抽帧确认视频 + 侧栏 + 控制条都入片。
- **两条观察（未改代码）**：① 录制画布不含 headerbar 但保留其高度，成片顶部有约 59 px 空带；
  ② 成片色彩标签为 `yuvj420p` / `pc` / `gbr` / sRGB，与 4:2:0 H.264 的常规 bt709/tv 不一致，
  严格按标签转换的链路可能偏色，抽帧目视未见异常。
- 常驻实例恢复后 PID 765845（语言 en、无扫描目录），uptime 连续未重启。

### 录制画布顶栏修复（2026-11-27 00:29–01:15，明细见内部 `STATE.md` §50）

- **问题**：成片顶部约 59 px 空带——画布只合成 sidebar/info/controls/hint/status + 视频，
  不含 headerbar，但画布仍取整窗尺寸。
- **修复**：`window.py` 记住 `_headerbar`，`_recording_layer_specs()` 第一项是
  `("header", _headerbar)`，每秒刷新组加入 `header`；新增 `tests/test_recording_layers.py`（2 项）。
- **板端 30 s 重录**（`header-fix-30s.mp4`，同素材/同窗口/隔离 state）：26.99 MB / 30.90 s /
  927 帧；视频 ROI 重复 **4.9%**、唯一帧 **28.6 fps**（旧成片 6.0% / 28.1 fps，未变慢）；
  顶部 58 px 从 std 3.4 / 3 色变成 **std 19.5 / 1915 色**，抽帧可见标题与按钮。
- **色彩标签（2026-11-27 决定：保持现状，不修）**：两版实现都能把成片标签改成
  `yuv420p / tv / bt709`，但内容质量不达标，均已回退：

  | 实现 | 标签 | moov | ROI 连续重复（跳前 30 帧） | 唯一帧 |
  |---|---|---|---|---|
  | 现状：RGBA → `mpph264enc` | `yuvj420p / pc / gbr` | ✅ | 6.0% | 28.1 fps |
  | RGBA + 仅加 `capsfilter`（对照） | 同现状 | ✅ | 6.3% | 28.0 fps |
  | CPU `videoconvert` → I420 bt709/tv | `yuv420p / tv / bt709` | ❌ 5/5 失败 | 81% | 5.5 fps |
  | GL `glcolorconvert` → I420 bt709/tv | `yuv420p / tv / bt709` | ✅（配 pad EOS） | 77% | 6.9 fps |
  | CPU `videoconvert` → NV12 bt709/tv | — | ❌ | — | — |

  原因：静态 `gst-launch` 能跑 217 fps（I420 编码）/ 102 fps（RGBA→I420），问题只在
  「动态挂载录制分支 + 并发硬解 + `glvideomixer`」组合下出现——YUV 路径产生背压，视频 queue
  丢帧、mixer 用重复帧补满 30 fps；YUV 输入还会让 EOS 卡在 mixer 视频入口，muxer 收不到 EOS
  就没有 moov。**保留现状的 `yuvj420p / pc / gbr` 标签**：严格按标签转换的播放器/转码链可能
  偏色，`docs/PITFALLS.md` §3 已记录，v1.2.0 发布说明标注为已知风险。

## 2026-11-27 ui-v5 P4 播放页：本机实现

> 2026-11-27 09:19 已按用户当次授权上板并验收，结果见下一节
> 「2026-11-27 ui-v5 P4 板端部署与验收」；本节保留本机阶段的读数作对照。

P4 的本机阶段只做 UI + 本机截图/探针，当时不部署、不 commit、不发布。
下面记录本机 GTK 4.14.5 / GStreamer 1.24.2 的真实窗口读数，供板端对照；**不要当成
板端 1280×768 的验收结果**。

- **探针**：`.cache/qc/ui-v5-p4/probe.py`，Xvfb `:13` 1280×800，真实 `PlayerWindow` +
  真实 VP9 软解后端（`display_bypass` 的 `fakesink sync=true`，无外窗）；
  106 项交互断言全过（32.2 s）。本机无 `gtk4paintablesink`/GL/NPU，截图帧是探针注入的
  同一素材真实帧，Beauty on/GPU ms 只在截图阶段按板端读数展示；详见 `RESULTS.md`。
- **布局**：窗口 1280×768；16:9 画面 **1280×720 @ (0,24)**；顶栏 47 px；底部 dock 96 px；
  播放键 52 px；控制键 44 px；时间轴命中带 20 px；美颜抽屉内容盒 318×546（含 padding
  约 350×578），内部滚动 upper 499 / page 411。
- **章节刻度**：真实 sidecar（15/30/45 s / 60 s）→ 25/50/75%；GStreamer 的 `qtdemux`
  在当前栈不实现 `TocSetter`，所以没有 sidecar 就没有刻度，不按百分比编造。
- **全屏**：窗口态和全屏态复用同一个 dock；全屏 idle 后 dock 淡出、左上遥测 62%、
  底边 2 px 进度线常驻、两个 scrim 随 HUD 隐现；暂停/抽屉打开时 dock 不隐藏。
- **录制画布**：图层 `top` 1236×47@(22,20)、`dock` 1232×96@(24,658)，视频 hole
  `Rect(0,24,1280,720)`，底色 `@video` 纯黑；`CanvasBuilder` 合成图
  `.cache/qc/ui-v5-p4/recording-composed.png` 可读。已知限制：半透明 scrim 不参与
  录制画布的 alpha（PITFALLS §3），实际 30 s 录制 MP4 未在本机跑（无 GL）。
- **未验证**：板端 GTK 4.18 CSS/字体/触摸、真实 GPU 美颜、真实 30 s 录制、实际 WM 下
  1280×768 内容高度、`--demo` 全屏联动、真实 compositor 下的 popover 摆位。
- **随包**：`src/` 改动后 `board-install.sh` 已重建为 **70 文件 / 359,320 B / base64
  357,532 B**，`--check` 通过；本机阶段未部署到板端。2026-11-27 09:19 上板后，
  payload 里 P4 的 5 个文件与板端部署哈希一致（其余文件本轮未逐项复核），
  但仍**没有在板端重跑离线安装**（见下一节）。

## 2026-11-27 ui-v5 P4 板端部署与验收（已完成）

用户当次授权后的上板结果。板端 GTK 4.18.6 / PyGObject 3.50.0 / Python 3.13.5 / GStreamer 1.26.2，
GNOME Wayland，DSI 1280×800；部署目录 `/home/tris/video-player-demo`。

### 部署

- **5 个文件**：`i18n.py`、`ui/style.py`、`ui/window.py` 替换，`ui/chapters.py`、`ui/player_chrome.py` 新增；
  暂存 `/home/tris/ui-v5-p4-stage-20261127-0919/`，备份
  `/home/tris/.cache/video-player-demo/deploy-backups/20261127-0919-ui-v5-p4/`。
- 上传后 `sha256sum -c` 5/5、`py_compile` 通过；目标目录内 `.<file>.p4-tmp` 拷贝后 `mv` 原子替换，
  替换后哈希与暂存一致、无 tmp 残留。部署前停 P3 实例，期间无第二个 GL 应用。
- 新哈希：`i18n 0866415…`、`style bc583a5…`、`window 387cfad…`、`chapters 3400b32…`、
  `player_chrome 8cf260a…`（完整值见 `.cache/qc/ui-v5-p4-board/RESULTS.md`）。

### 自检

- `PYTHONPATH=src python3 -m video_player --check --require-board` → **17 ok / 0 warn / 0 fail / 0 skip**。
- `style.CSS` **34,598 B**、GTK 4.18.6 `load_from_data` **0 parsing errors**、Wayland 上 provider 注册成功。
- 已知非致命日志仍是 `MESA ZINK vkCreateInstance failed` 与 `Gtk-CRITICAL … GDBus NoReply`。

### 验收（真实 VPU 硬解 + 真实 GPU 美颜）

探针 `.cache/qc/ui-v5-p4-board/board_accept_p4.py`：真实 `PlayerWindow` + `playbin`
（`mppvideodec` → `gtk4paintablesink`），**一个 mode 一个进程、每进程最多一次整窗快照**
（STATE §36.5 的快照限制），8 个 mode 串行，**116 项断言全过、0 failed**：

| mode | 断言 | 关键读数 |
|---|---|---|
| play | 33/33 | 窗口 1280×768；视频 `(0,24,1280,720)`；顶栏 46；dock 96；播放键 52；控制键 44；时间轴 20；`硬解 / H.264 / 1920×1080 / 30.0 fps`；fps 30.0；时长 30.00 s；章节 25/50/75；录制图层/底色/16:9 洞 |
| episodes | 9/9 | 6 行、`播放列表 1/6`、切集生效；内容测量 498 px |
| more | 9/9 | 5 个菜单项 + 快捷键提示；内容测量 438 px |
| language | 7/7 | `Episodes` / `Playlist 1/6` / `Beauty · off` 往返 |
| drawer | 15/15 | 面板 318×546、滚动 484/427、A|B 门禁、Escape 关闭 |
| fullscreen | 27/27 | 真全屏 **1280×800**、视频 `(0,40,1280,720)`；idle 后 dock 淡出、遥测 62%、进度线常驻；退出回 768 |
| browse | 4/4 | 返回 browse、headerbar 恢复 |
| beauty | 12/12 | 抽屉开美颜：GPU 2.2–3.0 ms/帧、NPU 2 张脸、fps 30.8、关掉后恢复 |

- **稳定性**：8 个进程 + 1 次 30 s 录制的全过程 uptime 18:43 → 18:58 连续，**没有重启/失联**。
- **截图**（1280×768；全屏 1280×800）：play / play-en / drawer / fullscreen-dock /
  fullscreen-idle / browse / beauty 共 7 张，哈希见 RESULTS.md §4。
- **30 s 录制**：`p4-record-30s.mp4`，33,409,173 B，H.264 1280×768@30、902 帧 / 30.07 s，
  色彩标签仍是 `yuvj420p / pc / gbr`（已知风险，不属本轮待办）；顶栏/遥测/带刻度时间轴/控制条合成正常。
- **实例**：验收后恢复为普通启动，PID 1525425（PPID 1）、RSS ≈ 175 MiB、语言沿用已保存的 en。

### P4 上板后才暴露的限制

1. **popover 不进整窗快照**：`选集` / `更多` 在 Wayland 上是独立 subsurface（`GdkWaylandPopup`），
   窗口快照不含它；探针在无 seat 焦点的 ssh 会话里拿到的 popup surface 恒为 **1×1**。
   本轮只断言内容与行为（6 行、计数、切集、菜单项、内容测量 498/438 px），**没有 popover 截图**，
   真实点击（有焦点）下的摆位/尺寸**未验证**。
2. 触摸点按仍未实机验证；H.265/VP9/AV1、4K、网络流未在 P4 播放页复测。
3. `board-install.sh` 已重建为 **70 文件**（含 P4 5 文件，`--check` 通过），**本轮未在板端重跑离线安装**。
4. 回滚步骤与部署备份路径见 `.cache/qc/ui-v5-p4-board/RESULTS.md` §7。

## 2026-11-27 ui-v5 设置页：本机实现

> 2026-11-27 10:02 已按用户当次授权部署到板端并验收，结果见下一节；本节保留本机阶段读数。

左导航第四项「设置」自 P1–P3 起一直是置灰占位（`rail.py` `set_sensitive(False)`、不接信号，
悬停提示「设置页未出稿」）。2026-11-27 产品决定补一个**最小设置页**，范围：界面语言、
解码方式、扫描目录、关于。**本轮只在本机完成，板端未部署、未改动。**

- **代码**：新增 `src/video_player/settings.py`（纯数据，无 GTK）与
  `src/video_player/ui/settings_page.py`（四张 `.card-box` 卡片）；改 `ui/rail.py`（设置恢复可点）、
  `ui/browse.py`（`BROWSE_PAGES` 加 `settings`、路由/高亮/标题/刷新/重译）、
  `ui/library_page.py`（公开 `store_directories` 与 `remove_directory()`）、
  `ui/window.py`（`_set_language()` / `_set_decode_mode()` 回调，`Esc` 从设置回首页）、
  `i18n.py`（23 个键）、`ui/style.py`（设置页 CSS）。
- **数据流**：语言与解码走回调回 `PlayerWindow`（语言落 XDG config；解码只改
  `player.decode_mode`，不跨启动持久化）；目录列表由 `config.library.directories` +
  `LibraryStore.directories` 合并去重；关于卡只读（版本/平台/内核/GTK/GStreamer/解码后端/
  状态文件/缓存目录）。
- **本机验证**（GTK 4.14.5 / Xvfb :11 / 真实 `PlayerWindow`、空片库）：真窗口 **1280×768**，
  探针 **33/0** —— rail 可点且高亮、header 标题/副题、四张卡、语言中英往返并落盘、
  解码段与播放页控制条双向同步、目录增删（临时目录 + × 按钮）、关于 8 行、`Esc` 回首页。
  截图 `.cache/qc/ui-v5-settings/out/ui-v5-settings-01-zh.png` / `-02-en.png`（1280×768）。
- **测试**：`pytest` **561 passed / 15 skipped**（`test_settings_model.py` 8 项纯数据、
  `test_settings_page.py` 13 项结构与单选行为）；`compileall` 通过；`style.CSS`
  **35,892 B / 0 parsing errors**（GTK 4.14.5）；`board-install.sh` 重建为 **72 文件**
  （新增两个模块），`--check` 通过。
- **未验证 / 风险**：板端 GTK 4.18 的字体度量与触摸点按；真实 VPU 下「关于」的解码后端读数；
  目录对话框的真实选择（自动化不点系统弹窗）；解码方式不持久化是设计取舍，不是缺陷。
- **回滚**：本轮没有动板端，也**没有 commit**；发布树未更新。

## 2026-11-27 ui-v5 设置页：板端部署与验收（已完成）

用户当次授权「板端部署」后的结果。板端 GTK 4.18.6 / GStreamer 1.26.2 / GNOME Wayland，
部署目录 `/home/tris/video-player-demo`。

### 部署

- **8 个文件**（2 新增 + 6 修改）：`src/video_player/settings.py`、
  `src/video_player/ui/settings_page.py`、`ui/rail.py`、`ui/browse.py`、`ui/library_page.py`、
  `ui/window.py`、`i18n.py`、`ui/style.py`。
- 暂存 `/home/tris/ui-v5-settings-stage-20261127-1002/`；备份
  `/home/tris/.cache/video-player-demo/deploy-backups/20261127-1002-ui-v5-settings/`
  （`old.sha256` 6 项 + 2 项 `MISSING`、`deployed.sha256` 8 项 + 旧文件）。
- 上传后 `sha256sum -c` 8/8、`py_compile` 通过；`.<file>.settings-tmp` 拷贝后 `mv` 原子替换，
  无 tmp 残留。旧板端 rail 仍是 P3 版本（`93e2b7f…`），本轮一并更新。
- 新哈希：`settings 6ce9858…`、`settings_page 4278a4b…`、`rail dc041c4…`、`browse 0cda3ed…`、
  `library_page 10c1f08…`、`window bd0f4fd…`、`i18n 1c6748b…`、`style e9c823b…`。

### 自检

- `PYTHONPATH=src python3 -m video_player --check --require-board` → **17 ok / 0 warn / 0 fail**。
- `style.CSS` **35,892 B**、GTK 4.18.6 `load_from_data` **0 parsing errors**、Wayland provider 成功。
- `board-install.sh` 已重建为 **72 文件 / 365,426 B base64**（含设置页两个模块，`--check` 通过），
  本轮部署走 tar over ssh，未用离线包重装。

### 验收（真实窗口，浏览壳，无 VPU 路径）

探针 `/home/tris/ui-v5-settings-accept/board_accept_settings.py`（内部）：真实 `PlayerWindow`、
空片库、一个进程一次串行；**35 项断言全过、0 failed**：

- 导航：rail「设置」可点且高亮、页面切换、header `设置 / 界面 · 播放 · 片库 · 关于`、四张卡可见。
- 布局：窗口 **1280×768**、内容区 **1192×711**（88 px rail + 58 px headerbar）；卡片高
  语言 64 / 解码 90 / 目录 118 / 关于 235；语言分段容器 **120×36**、每个按钮自然宽 **60**。
- 语言：切英文后 header=`Settings`、分段与关于标签全部变英文、写入 XDG config；再切回中文。
- 解码：设置页切「软解」→ `config.player.decode_mode=sw` 且播放页控制条三段同步；切回 auto。
- 目录：初始 0 行；加入临时目录后出现 1 行且带 × 按钮；移除后回到 0 行。
- 关于（真实板端读数）：`0.1.0` / `Linux aarch64` / `6.1.118-rt36` / `4.18.6` /
  `GStreamer 1.26.2` / **`VPU 硬解可用`** / 状态文件 / 缓存目录。
- `Esc` 回到首页。
- 截图：`settings-01-zh-1280x768.png`（sha256 `8d3dd8d7…c84ab891`）、
  `settings-02-en-1280x768.png`（`47fa317b…6ae6e4ec7`），均为 1280×768。
- **回归**：部署后重跑 P4 播放页 `play` mode → **33/0**（视频 `(0,24,1280,720)`、`硬解/H.264/
  1920×1080/30.0 fps`、章节 25/50/75）；uptime 19:23 → 19:27 连续、无重启。
- **实例**：验收后恢复常驻，PID **1564280**（PPID 1，RSS ≈177 MiB，语言沿用 en，
  `library_scan folders=1 items=4`）。

### 未验证 / 风险

- 触摸点按（rail/分段/按钮）仍是真实控件信号 + 截图，没有手指点按验收。
- 目录对话框（`Gtk.FileDialog`）未在板端点选；「选择文件夹」只验证了控件存在与委托路径。
- 解码方式不跨启动持久化（设计取舍，见 ARCHITECTURE §2.12）。
- 未 commit / 未发布；publish 树仍是 v1.2.0 + P4 未提交。

## 2026-11-27 ui-v5 最近观看页：板端部署与验收（已完成）

用户问「library 和 recently 分为两个标签是不是多余了」→ 三方向（删入口 / 续播模式 / 独立页）
效果图（`mockups/ui-v5-directions/`）→ 用户选「独立续播页（方向 C）」并当次授权「部署」。
板端 GTK 4.18.6 / GStreamer 1.26.2 / GNOME Wayland，目录 `/home/tris/video-player-demo`。

### 部署

- **11 个文件**（10 修改 + 1 新增）：`browse.py`、`library.py`、`i18n.py`、`ui/browse.py`、
  `ui/chrome.py`、`ui/home_page.py`、`ui/library_page.py`、`ui/rail.py`、`ui/recent_page.py`（新）、
  `ui/style.py`、`ui/window.py`。
- 暂存 `/home/tris/ui-v5-recent-stage-20261127-1055/`；整树备份
  `/home/tris/video-player-demo.bak-recent-20261127-1055`（3.4 MB）；旧/新哈希
  `/home/tris/.cache/video-player-demo/deploy-backups/20261127-1055-recent/`
  （`old.sha256` 10 项 + 1 项 MISSING、`deployed.sha256` 11 项）。
- `sha256sum -c` **11/11**、`py_compile` 通过、`.<file>.recent-tmp` + `mv` 原子替换、0 tmp 残留；
  部署后 `src/video_player` 38 个文件与工作区 **0 差异**。

### 自检

- `PYTHONPATH=src python3 -m video_player --check --require-board` → **17 ok / 0 warn / 0 fail / 0 skip**。
- `style.CSS` **36,593 B**、GTK 4.18.6 `load_from_data` **0 parsing errors**。
- `board-install.sh` 已在本地重建为 **73 文件 / 373,098 B base64**（`--check` 通过）；
  本轮部署仍走 tar over ssh，未用离线包重装。

### 验收（真实窗口，浏览壳 + 一次真硬解起播）

探针 `/home/tris/ui-v5-recent-accept/board_accept_recent.py`（内部）：真实 `PlayerWindow`、
`/home/tris/demo-movies` 4 部真实影片、临时 XDG state 里 seed 3 条历史（2 继续 / 1 已看完），
**33 项断言全过、0 failed**：

- 导航：起始 home；**rail「最近」按钮的真实 `toggled` 信号**切到最近页且高亮；rail 回首页。
- 布局：窗口 **1280×768**、页面 **1140×689**、卡片 **273×170**（本地 GTK 4.14 为 177 高）、
  3 张卡两组、无需滚动。
- 内容：`继续观看 2 · 已看完 1`、`3 项`、组标题「继续观看 / 已看完」；英文下
  `Continue 2 · Finished 1` / `Continue watching` / `Finished`。
- 片库：chip 只剩「全部 / 已看完」。
- 清除：记录清空后进空态、`清除记录` 置灰、片库「已看完」计数归零。
- 起播：点「继续观看」卡片 → 播放页，playlist 恰好是该组顺序，**真实 `mppvideodec`** 起播
  （`mpp_info` / `h264d_api` 日志，`backend.has_media=True`）；`Esc` 回**最近页**。
- 截图：`recent-01-zh-1280x768.png`（sha256 `fbbe73a6…`）、`recent-02-empty-1280x768.png`
  （`86d078ff…`），均 1280×768。
- **回归**：`/home/tris/ui-v5-p4-accept/board_accept_p4.py --mode play` → **33/0 / 7.3 s**。
- **实例**：验收前停旧 PID 1564280；验收后按原 env 重启，**PID 1633296**（PPID 1、RSS ≈178 MB），
  日志仍只有既有的 MESA ZINK / GDBus NoReply 两行。证据：`.cache/qc/ui-v5-recent-board/`（内部）。

### 未验证 / 风险

- 触摸点按（rail / 卡片 / 清除）仍是真实信号 + 截图，没有手指点按验收。
- 「清除记录」确认对话框（`Gtk.AlertDialog`）未人工点选；探针直接清空 store 后再刷新。
- 最近页只列扫描目录里的文件（与首页一致）；库外单文件仍在播放页「最近播放」。
- 未 commit / 未发布；publish 树仍是 v1.2.0 + P4 未提交。

## 2026-11-27 删除 headerbar 语言按钮：板端部署与验收（已完成）

用户：「上面的中文/英文按钮切换删除了吧，放到设置里面就可以了」，随后授权「部署一下」。
gtk 4.18.6 / GStreamer 1.26.2 / GNOME Wayland。

### 部署与自检

- 单文件：`src/video_player/ui/window.py`（删除 `_header_language_button` 的创建/`pack_end`/
  `_apply_language` 文案与 tooltip），`sha256 892a5367…`。
- 备份 `/home/tris/video-player-demo.bak-header-20261127-1119`；旧/新哈希
  `/home/tris/.cache/video-player-demo/deploy-backups/20261127-1119-header/`；
  `sha256sum -c` 1/1、`py_compile` 通过、无 tmp 残留。
- `--check --require-board` **17 ok / 0 warn / 0 fail**；CSS 36,593 B / **0 parse errors**。

### 验收（真实窗口）

探针 `/home/tris/ui-v5-header-accept/board_accept_header.py`（内部）：真实 `PlayerWindow`、
`/home/tris/demo-movies` 4 部影片、**18/0**：

- titlebar 只剩标题 + 副题（`视频播放器 / 4 项 · 1 个目录`）与窗口按钮：
  无 `English` / `中文` 标签，`header-action` 按钮数 **0**。
- 窗口 1280×768、titlebar 高 46。
- 设置页：rail 进入、语言卡与两段可见；切英文 → `window._t.language=en`、header 标题 `Settings`；
  切回中文 → header `设置`；`Esc` 回首页。
- 截图 `header-01-zh-1280x768.png`（1280×768）。
- **回归**：P4 播放页 `--mode play` **33/0 / 7.0 s**。
- **实例**：验收后重启常驻，PID **1663326**（PPID 1、RSS ≈178 MB，`window_open` + `library_scan items=4`）。
- 证据本机 `.cache/qc/ui-v5-header-board/`（脚本 + 截图 + metrics.json）。

### 未验证 / 风险

- 触摸点按仍是真实信号 + 截图，没有手指点按验收。
- 播放页「更多」菜单里的语言项**保留**（播放时 header 隐藏）；若要求「语言只在设置里」可再删。
- 未 commit / 未发布。

## 2026-11-27 P5 板端部署与验收：窗口化标题栏 + 挤压式选集栏 + 画面手势

用户当次授权「部署到板端」后完成；**未 commit / 未发布**。部署 4 文件（3 改 + 1 新增），
验收后恢复常驻实例。完整证据在 `.cache/qc/fix58-board/RESULTS.md`（内部）。

### 部署

- 目标 `tris@10.66.24.110:/home/tris/video-player-demo`；暂存
  `/home/tris/ui-v5-p5-stage-20261127-1444/`；备份
  `/home/tris/.cache/video-player-demo/deploy-backups/20261127-1444-p5/`。
- `ui/window.py` `320ba329…`、`ui/player_chrome.py` `48be37a4…`、`ui/style.py` `3661f0b2…`、
  `ui/taps.py` `6c9748be…`（新增）；`i18n.py` 未改。原子替换、无 tmp 残留、`py_compile` 4/4。
- 部署前停掉旧实例（PID 1883288），部署期间无第二个 GL 应用。

### 自检

- `PYTHONPATH=src python3 -m video_player --check --require-board` → **17 ok / 0 warn /
  0 fail / 0 skip**。
- GTK 4.18.6：`style.CSS` **38,332 B**、`load_from_data` **0 parsing errors**，Wayland provider
  注册成功；已知非致命日志仍是 ZINK vkCreateInstance / GDBus NoReply 两行。

### 验收（真实 VPU 硬解 + 真实 GTK sink）

探针 `/home/tris/ui-v5-p5-accept/board_accept_p5.py`，媒体
`/home/tris/ui-v5-p4-accept/library/*.mp4`（1080p30 H.264，30 s），一 mode 一进程、
每进程最多一次整窗快照、mode 间 sleep 4 s：

| mode | 结果 | 关键读数 |
|---|---|---|
| layout | **15/0** | 窗口 1280；headerbar 可见；顶栏 compact；选集 360；视频 **920×517**（16:9）；控制条两行；row1 **830** / row2 **177** ≤ dock **872**；选集 ✕；播放列表有行；header「＋ 打开」；**硬解 `mppvideodec`**；截图 1280×768 |
| fullscreen | **8/0** | 真全屏 **1280×800**；headerbar 隐藏；选集收起；控制条单行；视频 **1280×720**；退出恢复；截图 1280×800 |
| interact | **9/0** | 单击暂停；双击全屏；美颜开 → 选集关；点抽屉外关闭并 claim；`Esc` 先关选集；chip 重开；browse 保留「＋ 打开」 |

- 截图：`docs/evidence/ui-v5-player-1280x768.png`（P5 窗口态）、
  `docs/evidence/ui-v5-player-fullscreen-1280x800.png`（P5 全屏）。
- 常驻实例：普通启动，**PID 2002290（PPID 1，RSS ≈ 192 MB）**，语言沿用 en，
  无媒体落 browse home；`library_scan folders=3 items=14`。

### 上板过程暴露的坑（已写入 PITFALLS §5）

GUI/GL 探针反复启停后板端 SSH 会短暂 `banner exchange` 超时（等 1–3 分钟恢复）；
探针直连 `PlayerWindow` 必须手动注册 `style.CSS`；GTK 4.18/PyGObject 3.50 用
`GdkPixbuf.Pixbuf.new_from_file`；探针结束用 `os._exit(0)` 避免 mpp/GL 析构卡住；
不要并发两个 GL runner。

### 仍未验证

真实手指触摸双击/点外部、30 s `--record` 成片、H.265/VP9/AV1/4K；离线安装包未在板端重跑。

## 2026-11-27 P6 窗口态单行控制条 + 选集/美颜面板：板端部署与验收

用户 17:57 选定方案 A，18:33 当次授权「部署到板子上」。P5/v6 在选集栏打开时把
「字幕 / 美颜 / 全屏」拆到第二行（row1 830 / row2 177 ≤ dock 872），P6 改成固定单行，
并把次级入口放回各自容器。**未 commit / 未发布 / 未重跑离线安装**。

### 改动

- 右侧 360 px 面板头部换成「选集 / 美颜」两标签（`.sidebar-tabs` + `Gtk.Stack`，单选、
  记住上次标签）；选集页 = 播放列表 / 计数 / + 添加文件 / 最近播放，美颜页 = 原
  `_build_beauty_page()` 整页。
- 美颜页只构建一次，按模式换宿主：窗口态进面板标签（去卡片类、margin 归零、隐藏页头标题/
  第二个 ✕/下划线）；全屏与 `--record` 回 overlay 抽屉（352 px、margins 76/112/24）。
- 窗口态字幕按钮移到画面顶栏（沿用原 popover），全屏/`--record` 回到底部行；
  `--record` 保持 P4 画布（字幕在底部行）。
- 底部固定一行：删 `_controls_row2` / `_set_transport_compact` / `.play-controls-stack`；
  音量百分比 label 删除（值进 tooltip）；面板 chip 图标化（☰ + tooltip + 美颜琥珀点），
  面板不可用时隐藏。

### 本机验证（GTK 4.14.5 / X11 / fakesink 注入画面）

命令与证据（内部缓存）：

```
DISPLAY=:12 PYTHONPATH=src python3 .cache/qc/fix59/check.py windowed zh
DISPLAY=:13 PYTHONPATH=src python3 .cache/qc/fix59/check.py fullscreen zh
DISPLAY=:12 PYTHONPATH=src python3 .cache/qc/fix59/check.py record zh
PYTHONPATH="$HOME/.local/lib/python3.10/site-packages" python3 -m pytest tests -q
```

| 场景 | 结果 | 关键读数 |
|---|---|---|
| windowed | **24/0** | stage 920；dock 872；row **838** ≤ 872（中英同宽）；字幕在画面顶栏；美颜页在面板 stack；面板 chip 仅图标 + tooltip；两标签切换；关面板不重排（838 vs 838） |
| fullscreen | **11/0** | dock 1232；row **1080 中 / 1194 英**（含「对比 · 需美颜」）≤ 1232；字幕/美颜 pill 回到底部行；美颜页回抽屉；退出全屏后回面板 |
| record | **11/0** | stage 1280；dock 1232；row **972**；headerbar 隐藏、顶栏完整；面板 chip 隐藏；字幕在底部行、美颜页在抽屉 |
| pytest | **606 passed / 15 skipped** | 新增 `test_player_panel.py` 7 项、`test_player_drawer.py` +1；`test_player_episodes.py` 改为面板/归位断言；`test_theme.py` 改为只要求美颜头的 40 px 下划线 |
| 离线安装包 | 74 文件 / **386,104 B**（base64 384,316 B） | `scripts/build_board_install.py` 重建，payload 与工作树一致 |

截图（本机，内部）：`mockups/fix59/01-windowed-episodes.png`、`02-windowed-beauty.png`、
`03-windowed-closed.png`、`04-fullscreen.png`、`05-record.png`（1280×768 / 1280×800）。

### 板端部署（2026-11-27 18:33–18:38）

- 目标 `tris@10.66.24.110:/home/tris/video-player-demo`（linaro-alip / aarch64 /
  Python 3.13.5 / GTK 4.18.6 / GStreamer 1.26.2）。暂存
  `/home/tris/ui-v6-p6-stage-20261127-1832/`；备份
  `/home/tris/.cache/video-player-demo/deploy-backups/20261127-1832-p6/`
  （`old.sha256` + 3 个旧文件）。
- 停旧实例 **PID 2219404**（v6），3 文件 `install -m 644` + `mv` 原子替换，无 `.p6-tmp`
  残留，`py_compile` **3/3**。

| 文件 | 旧 sha256 | 新 sha256 |
|---|---|---|
| `ui/window.py` | `e3a342ffa73b8ccb91eb0d37fa7a800cc277318ae56d822f25483bdf936bae57` | `401ad1f79981e21384d6a92b0989785cb4b60bf31b99b6a4cb8b64cb25b08da9` |
| `ui/style.py` | `9e205910de33d5e691f99a56c49c5fae67307b3f4cfaa148fb853136bced4b28` | `5fce8be6660829cfc1d3038e4cd87dbae1a370c2ea398c6d012110aff2f76d2c` |
| `i18n.py` | `1087b1133b19f9c894b81902d3d9eb312a6ee3a2b8ca16aff747aa5946a0aad7` | `814a9c1aa67cbbcf0e27d3142739586228cf3d31136bb380a9718c2d3b4f7a40` |

- 自检：`--check --require-board` **17 ok / 0 warn / 0 fail / 0 skip**；GTK 4.18.6 CSS
  0 parsing errors。已知非致命日志不变（ZINK / GDBus NoReply），另有启动时的
  `mpp: unable to create enc vp8 for soc rk3576 unsupported`（后端探测编码器，非本轮引入）。

### 板端验收（真实 VPU 硬解 + 真实 GTK sink）

探针 `/home/tris/ui-v6-p6-accept/board_accept_p6.py`，媒体
`/home/tris/ui-v5-p4-accept/library/*.mp4`（1080p30 H.264，50 s）；一 mode 一进程、
每进程最多一次整窗快照、mode 间 sleep 4 s；日志 `out/{layout,beauty,fullscreen,record}.log`。

| mode | 结果 | 关键读数 |
|---|---|---|
| layout | **21/0** | 窗口 1280×768；headerbar 可见；顶栏 compact；面板 360；视频 **920×517**（16:9）；dock **872**；row **838 ≤ 872**；顶栏 **314 ≤ 876**；字幕在画面顶栏；美颜页在面板 stack；面板 chip 仅图标；**硬解 `mppvideodec`**；截图 1280×768 |
| beauty | **23/0** | 面板 stack 切到 `beauty`；美颜页仍 docked（无 drawer 类）；其余窗口态断言同 layout；截图 1280×768 |
| fullscreen | **12/0** | 真全屏 **1280×800**；headerbar/顶栏隐藏；面板收起；视频 **1280×720**；dock **1232**；row **1066 ≤ 1232**；字幕/美颜 pill 回到底部行；美颜页回抽屉；截图 1280×800 |
| record | **11/0** | stage 1280；dock 1232；row **967 ≤ 1232**；headerbar 隐藏、顶栏完整；面板 chip 隐藏；字幕在底部行、美颜页在抽屉；截图 1280×768 |

- 截图（发布用）：`docs/evidence/ui-v5-player-1280x768.png`
  （sha256 `e6134752…b92d3`）、`docs/evidence/ui-v5-player-fullscreen-1280x800.png`
  （sha256 `f5ca9c9d…30df`），都是 P6 板端实拍。其余两张（beauty / record）在内部
  `.cache/qc/fix59-board/out/`。
- 常驻实例：`PYTHONPATH=src` + Wayland/GL 环境，`setsid -f nohup python3 -m video_player`；
  **PID 2278277**（PPID 1，RSS ≈ 194 MB，tris），`actions.log`：
  `run_window media=0` → `window_open language=zh` → `library_scan folders=2 items=13`。
  启动日志只有 ZINK / GDBus 两行已知非致命信息。
  **坑**：漏 `PYTHONPATH=src` 会直接 `No module named video_player`（本轮第一次重启踩到）。

### 回滚

```sh
cd /home/tris/video-player-demo
cp -a /home/tris/.cache/video-player-demo/deploy-backups/20261127-1832-p6/{window.py,style.py} src/video_player/ui/
cp -a /home/tris/.cache/video-player-demo/deploy-backups/20261127-1832-p6/i18n.py src/video_player/
# 重启实例
```

### 仍未验证 / 风险

- 真实手指触摸标签 / ✕ / chip、30 s `--record` 成片、H.265/VP9/AV1/4K 未复测。
- 窗口态 `--record` 运行时切换（非启动 `--record`）仍沿用旧的门控调用点，未在本轮扩展；
  P5 的触摸与 popover 限制不变。
- 离线安装器未在板端重跑（本机 payload 已重建）。
