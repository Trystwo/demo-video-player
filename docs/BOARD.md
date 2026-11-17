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
| 视频解码 | `mppvideodec` 硬件解码可用 |
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
| 2 | `mppvideodec` 可硬解 1080p30 H.265 | 同上，换 `h265parse` + H.265 素材 | 稳定 30fps | 待测：板端暂无 H.265 样片，M2 前补测 |
| 3 | 4K30 H.265 解码能力 | 4K H.265 素材 + `fpsdisplaysink` | 连续播放不卡顿（A1 可选加分项） | 可选；4K 不作硬指标（P4），待素材 |
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
| 15 | 网络流 | HLS 样本 + 局域网 RTSP 摄像头 | 可播放（A6） | 移出本期（2026-11-14），不验收 |

## 3. 模型与素材清单

| 类别 | 文件 | 来源/许可 | 状态 |
|------|------|----------|------|
| 人脸检测模型 | `models/yunet_rk3576.rknn`（已用） | YuNet（OpenCV Zoo，MIT）经 `scripts/convert_yunet.py` 转换 | ✅ 2026-11-16（1,084,515B，30ms/次） |
| 人脸检测模型 | `models/scrfd_500m.rknn`（备选） | 同上，速度优先 | 未使用 |
| 测试视频（正脸） | 1080p30 H.264 人像，≥1 人 | 用户自备（公司自有版权） | 待提供 |
| 测试视频（多人/侧脸） | 1080p30，≥3 人，含 ≤45° 侧脸 | 用户自备（公司自有版权） | 待提供 |
| 测试视频（4K） | 4K30 H.265，可选 | 用户自备（公司自有版权） | 选配；4K 不作硬指标 |
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

`install.sh --require-board` 会把板级项（`mppvideodec`、`waylandsink`、
`gtk4paintablesink`、`librknnrt`、`librga`、RKNN 模型）的缺失视为失败；
普通 `--check` 在开发主机上只把它们标成 `warn`。

## 5. 实测记录

### 5.1 参考：x86 开发主机（2026-11-14，非板端）

用于确认自检逻辑本身可用：

- `python3 -m video_player --check` → **9 ok / 7 warn / 0 fail**
- 通过项：Python 3.10、GTK4 4.6.9、GStreamer 1.20.3、`uridecodebin`、
  `videoconvert`、`glupload`、`glshader`、`pulsesink`、显示会话
- 预期缺失（板端才有）：`mppvideodec`、`mpph264enc`、`waylandsink`、
  `gtk4paintablesink`、`librknnrt.so`、`librga.so`、RKNN 模型

### 5.2 目标板实测（M1–M2 已回填）

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
| 2026-11-17 | M3 掉速窗口归因（检测路径） | 开检测时周期性掉速：15s 素材 t≈9–14s、60s 长片 t≈11–16s/27–33s，fps 21–25 后自行恢复 30.0；同期 beauty 1.0–1.5 ms、NPU 37–44 ms、CPU 112–125% 全平稳；低负载（load 1.32）复现；检测降到 1Hz（CPU 59–62%）仍复现；不带检测的 `perf_probe` 15s 内 buffers/s 稳定 30.0 | 诱因在「检测开启」而非美颜/推理频率；怀疑 `extract_dup` NV12 DMABuf 映射 + `GLib.idle_add` 回主线程周期性阻塞 sink，M2 遗留，交 M4 |
| 2026-11-17 | M3 检测线程 CPU 归因 | `/proc/PID/task` 采样（总 CPU 115.2%）：主线程 27.3%、demux:sink 27.6%、video-decoder 15.0%、gstglcontext 11.3%；headless `measure_tap.py` 30.0fps、10 次/s、37.5 ms | M2 遗留成本，M3 未改动 |
| 2026-11-17 | M3 旋转（90/270/180） | `ffmpeg -display_rotation` 素材 + 四色标记图：tag `rotate-270/rotate-90/rotate-180` 与显示方向自洽；180° 自拍素材人脸框与遮罩对齐 | `face_rect_to_texture()` 反向映射正确（单测覆盖 0/90/180/270） |
| 2026-11-17 | M3 一键开关（F9 路径） | 运行时 `refresh_beauty()`：`beauty_active False→True`，beauty_ms 稳定 0.9–1.1 ms，无异常 | UI 开关走同一路径 |
| 2026-11-17 | M1 遗留 seek 缺陷 | PyGObject 绑定顺序为 `seek(rate, format, flags, ...)`；修复后 seek/倍速恢复，`tests/test_seek.py` 4 passed | 回归测试补齐 |
| 待测 | M3 长跑 / 1h 稳定性（A5） | 连续播放 + `free`/句柄检查 | 本轮未做（板端图形会话意外中断，见 §6） |

## 6. 已知环境限制与复现命令

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
- 测试素材在板端 /tmp（非仓库）：`/tmp/face_1080p30.mp4`（近景，
  由 `test-media/pexels/5534415…` 转码）、`/tmp/face_rot180.mp4`、
  `/tmp/face_selfie.mp4`、`/tmp/m1_sample.mp4`（1080p30 双人）、
  `/tmp/marker*.mp4`（旋转方向验证）。

> 回填方式：`python3 scripts/probe_board.py --markdown` 的输出可直接贴入本文件
> 对应小节，并保留命令与日期，保证可复现。
