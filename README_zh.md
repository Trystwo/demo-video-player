[English](README.md) | 简体中文

# Quectel Pi M2 视频播放器

面向 [Quectel Pi M2](https://developer.quectel.com/doc/sbc/en/index.html)（RK3576）的本地视频播放器：视频由 VPU 硬解，内嵌 GTK4 窗口渲染，外层是浏览壳（首页 / 片库 / 最近 / 设置）和画面优先的播放页，带文件夹播放列表、断点续播、外挂字幕、音轨切换、倍速、全屏，以及从运行中管线读取的遥测。

NPU 人脸检测与 GPU 磨皮美白是可选能力，默认关闭；`--demo` 一次开启检测、美颜与 A|B 分屏，控件收在播放页的「美颜」抽屉里。

| | |
|---|---|
| 板卡 | Quectel Pi M2（RK3576），Debian 13，DSI 1280×800 |
| 播放 | 1080p30 H.264/H.265 硬解，稳定 30fps |
| 检测 | YuNet RKNN 跑在 NPU 上，10Hz |
| 美颜 | 单个 GPU shader pass，1080p 单帧约 0.9ms |
| 测试 | 49 个 pytest 文件（纯 Python、GTK 与板端用例） |
| 演示视频 | [飞书](https://quectel.feishu.cn/wiki/DarawV37OiDQBQkPHAgct74KnGc) · [52 秒 MP4](https://github.com/Trystwo/demo-video-player/releases/download/v1.1.1/video-player-m5-demo-v3.mp4) |

![首页](docs/evidence/ui-v5-home-1280x768.png)

*板端实拍（1280×768，2026-11-27）：首页——最近观看主视觉带真实「继续观看」CTA，下面是带进度细线的历史卡片与「最近添加」海报，数值全部来自扫描到的片库与观看历史。*

**状态。** 浏览壳、播放页、设置页与最近页已在板端用真实 VPU 硬解与 GPU 美颜链路部署验收。触摸点按、真实拖放、目录选择、排序弹层与 VP9/AV1/4K 素材仍未人工验证，见[「限制」](#限制)。

## 功能

### 播放

- **本地硬解** —— MP4 / MOV / MKV / WebM / TS，H.264 与 H.265 经 `mppvideodec` 走 VPU；音频支持 AAC / MP3 / Opus。VP9 / AV1 / MPEG-2 没有 VPU 通路，只在装了软件解码器的环境里能播。控制条可在自动 / 强制硬解 / 强制软解之间切换。
- **播放控制** —— 播放/暂停、拖动进度、音量、0.5×–2.0× 倍速、全屏、上一首/下一首、返回；任何模式下控制条都是单行 44 px。
- **播放页** —— 窗口态保留系统标题栏（片段名、「＋ 打开」、窗口按钮），16:9 画面居中；360 px 面板有「选集」「美颜」两个标签。单击画面播放/暂停，双击全屏，`Esc` 依次收起抽屉 → 面板 → 全屏 → 返回浏览页。
- **空态即拖放区** —— 无参数启动落在首页；没有片时播放区就是一整块拖放区，带「选择文件」/「选择文件夹」CTA，文件或文件夹可以直接拖进去。
- **字幕与音轨** —— 同名 `.srt`（含 `.zh.srt` / `.en.srt`，UTF-8 或 GB18030）以 GTK 图层画在画面上方；`CC` 弹层列出并切换容器音轨。
- **章节** —— 可选 `片名.mp4.chapters.json` 外挂在时间轴上画真实刻度；外挂损坏就当作没有章节。
- **遥测** —— 分辨率 / 编码 / 解码路径 / 实测 fps / 人脸与美颜状态 / 进程 CPU，全部从运行中的管线读取。

### 浏览壳

- **左导航** —— 首页 / 片库 / 最近 / 设置，底部显示硬解就绪状态与扫描时间。
- **片库** —— 扫描目录上的 4 列海报网格：后台探测与海报缓存、全部 / 已看完筛选、名称 / 时长 / 最近添加排序、重新扫描、选择目录与空态。拖入文件夹就并入扫描，拖入视频直接播放。
- **首页** —— 最近观看的片子做主视觉（真实「继续观看 mm:ss」CTA），下面是 4 张继续观看卡片和 4 张最近添加卡片。
- **详情页** —— 片子自己的画面做背景板，参数行、3 列选集、「继续观看 mm:ss」/「重新播放」和同目录列表；不编造评分或简介。
- **最近** —— 按「继续观看 / 已看完」分组的观看历史，点卡片直接续播，清除记录带确认，记录为空时有空态。
- **设置** —— 界面语言（中文 / English）、解码方式、扫描目录，以及关于卡（版本 / 平台 / 内核 / GTK / GStreamer / 解码后端 / 状态路径）。

### 片库与历史

- **文件夹播放列表** —— 打开一个文件即载入所在目录（按名称排序），`P` / `N` 或方向键前后切换。
- **断点续播** —— 按文件记录位置（XDG state，键为路径 + 大小 + mtime），preroll 之后恢复，开头 10 秒与结尾 15 秒不记录。`player.resume: false` 可关闭。
- **最近播放** —— 最多 20 条，与续播位置分开存储，播完的片子仍留在历史里。`player.recent_files: false` 可关闭。
- **片库扫描** —— 只扫一层，跨根目录去重，单次上限 500 个文件（`library.max_items`）；页内选择的目录写在 XDG state。
- **中英文界面** —— 运行中随时切换，`--lang {zh,en}` 只覆盖本次启动。默认中文。

### 板卡能力

- **NPU 人脸检测** —— 转换后的 YuNet RKNN 在工作线程里每 3 帧跑一次（10Hz），GTK 图层画四角 L 形框与置信度标签，关键点用于美颜 warp。默认关闭：首次要花约 1.7 s 预热 NPU。
- **检测保持** —— 某帧没检测到脸时沿用上一次的脸框 `detection.hold_ms`（默认 600ms），美颜不再跨检测空档闪烁（近景实测空档 6 → 0）。
- **GPU 美颜** —— 单个 `glshader` pass，在按关键点拟合、羽化并经肤色门限保护的椭圆内磨皮美白；CIELAB 肤色门限是「头发发白、镜框被抹平」的修复点。
- **一键对比** —— `C` 或 A|B 开关切到同帧分屏，两侧标原图 / 美颜；`B` 开关美颜，`--demo` 启动即分屏。
- **实验性 warp** —— 同一 shader 内的瘦脸 / 大眼滑杆，默认 0（视觉上与不加 warp 一致），拉满 100 每帧约增加 0.1ms。
- **应用内录制** —— 按 `R` 或 `--record FILE`：GPU 分支与 UI 覆盖层画布在 `glvideomixer` 内合成，由 `mpph264enc` 编码成整窗 30fps MP4，不需要采集卡。录制时屏幕上的视频区是黑的，文件本身正常。

## 怎么实现的

```
filesrc → qtdemux ─┬─ video ─ h26xparse → mppvideodec (VPU) → glupload → glcolorconvert
                   │          → glcolorscale (≤1280) → glshader (磨皮 + 美白 + 分屏 + warp)
                   │          → tee ─┬→ fpsdisplaysink → gtk4paintablesink
                   │                └→ glvideomixer → mpph264enc   (--record)
                   └─ audio ─ decodebin → audioconvert → volume → autoaudiosink

解码 src pad probe，每 3 帧取一帧 → 工作线程 → NV12→BGR → YuNet (NPU)
                                   → GLib.idle_add → shader uniforms
```

两个决定最要紧：脸型椭圆、羽化与肤色门限都在片元 shader 里用 uniform 光栅化，不额外绑蒙版纹理（`glshader` 只允许一个采样器）；`glcolorscale` 在 sink 前预缩放，否则 GSK 退回 CPU 缩放，CPU 会从 18% 升到 62–78%。同帧分屏则在片元里切左右，没有两路管线的帧对齐问题。

交互参考 [IINA](https://iina.io/)，解码 → 滤镜 → 渲染的分层参考 [ExoPlayer](https://github.com/google/ExoPlayer)。完整管线与线程模型见 [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)。

## 环境要求与安装

- Quectel Pi M2（RK3576），Debian 13，GNOME/Wayland，DSI 屏 1280×800（窗口按 1280×768 设计）
- Python 3.10+（板端 3.13），PyGObject + GTK 4 走 **apt**，GStreamer 1.x 与 Rockchip MPP 插件，`gst-plugin-gtk4`，NumPy 2.2，以及 RKNN/RGA 库
- 可选：人脸检测与美颜需要 `models/yunet_rk3576.rknn`（见 [models/README.md](models/README.md)）
- 不需要摄像头：检测与美颜跑在播放的视频帧上
- 不要在板端用 pip 安装 PyGObject 或 GStreamer 绑定——typelib 必须与系统 GStreamer 匹配

```bash
git clone https://github.com/Trystwo/demo-video-player.git
cd demo-video-player
chmod 755 install.sh && ./install.sh --require-board   # 启动器 + 桌面入口，不需要 sudo/apt/网络
video-player-demo --check                              # 以 ok= / warn= / fail= 结尾
```

`--require-board` 把板端专属检查变成硬失败，缺 RKNN 模型只会警告。`--venv DIR`、`--no-desktop`、`--bin-dir`、`--desktop-dir`、`--dry-run` 可调整安装。GNOME 会话下脚本还会装内置的 Desktop Icons NG 扩展，而 GNOME Shell 只在登录时扫描扩展目录——**首次安装后注销再登录一次**。

板端没有网络或 git 时，用单文件离线安装包：`bash board-install.sh [DEST]`。内嵌载荷是生成的，改过随包文件后必须运行 `python3 scripts/build_board_install.py`。

## 运行

```bash
video-player-demo                                               # 打开首页
video-player-demo /path/to/video.mp4                            # 窗口播放
video-player-demo --demo /path/to/folder                        # 全屏演示模式
video-player-demo --lang en video.mp4                           # 本次用英文界面
video-player-demo --record /tmp/demo.mp4 --record-duration 10   # 录制窗口
```

从源码树运行时 `cd src && python3 -m video_player ...` 效果相同。其他参数：`--fullscreen`、`--config FILE`、`--snapshot FILE`、`--snapshot-delay S`、`--check`、`--require-board`、`--version`。

| 按键 | 动作 | 按键 | 动作 |
|---|---|---|---|
| `Space` | 播放 / 暂停 | `S` | 停止并回到开头 |
| `←` `→` | 后退 / 前进 10 秒 | `B` | 开 / 关美颜 |
| `P` `N` | 上一首 / 下一首 | `C` | 分屏对比 |
| `F` | 全屏（`Esc` 退出） | `M` | 遮罩预览 |
| `O` | 打开文件 | `R` | 开始 / 停止录制 |

画面上轻点一下播放/暂停，双击切换全屏。

## 配置

所有可调项在一个严格校验的 JSON 文件里——`configs/player.example.json` 或 `--config` 指向的文件；未知键报错，坏值会带上键名。常用项：

| 键 | 默认值 | 作用 |
|---|---|---|
| `player.decode_mode` | `auto` | `auto` 优先 VPU，失败回退软解；`hw` / `sw` 强制一条路径 |
| `player.texture_max_dimension` | `1280` | GTK sink 前的预缩放上限 |
| `player.volume` / `player.speed` | `80` / `1.0` | 初始音量（0–100）与倍速（0.5–2.0） |
| `player.resume` / `player.recent_files` | `true` / `true` | 记住位置 / 保留观看历史 |
| `detection.enabled` | `false` | NPU 检测默认关，`--demo`、美颜或人脸框开关才会拉起 |
| `detection.hold_ms` | `600` | 没检测到脸时沿用上一次脸框的时长（0–5000） |
| `beauty.enabled` | `false` | 美颜默认关，`B` 或抽屉开关打开 |
| `beauty.smoothing` / `beauty.whitening` | `60` / `40` | 强度 0–100，拖动时实时生效 |
| `beauty.face_slim` / `beauty.eye_enlarge` | `0` / `0` | 实验性 warp 0–100 |
| `library.directories` | `[]` | 片库扫描目录（一层深） |
| `library.max_items` | `500` | 单次扫描上限 |

## 性能

板端 1080p30 实测：

| 场景 | fps | CPU |
|---|---|---|
| 仅播放 | 30 | ≈16% |
| + 美颜（检测关） | 30 | 30–32% |
| + 检测 + 美颜 | 30.0 | 117–131% |
| 完整演示模式（分屏、全开） | 30.8 | — |

检测开销主要来自 10Hz 的 YuNet 推理；1 小时长跑已完成（3,549 个样本，见 [docs/BOARD.md](docs/BOARD.md)）。

## 无硬件测试

```bash
python3 -m pytest tests -q
PYTHONPATH=src python3 -m video_player --check
```

需要 PyGObject、GStreamer、NumPy 或 RKNN 运行时的用例在库缺失时会跳过；板端时序、真实 VPU 硬解与截图证据仍需要 Pi M2。

## 文档

[产品需求](docs/PRD.md) · [架构](docs/ARCHITECTURE.md) · [板端记录](docs/BOARD.md) · [踩坑记录](docs/PITFALLS.md) · [演示指南](docs/DEMO_GUIDE.md) · [证据](docs/evidence/README.md)

## 限制

- 演示级实现，不是加固过的发行版：没有认证、沙箱或 DRM。只播本地文件，不支持网络流（HTTP/HLS/RTSP/RTMP）、内嵌字幕轨与 `.ass` 样式。
- 检测仍在流线程上抽帧（每 3 帧一次 `extract_dup()` 整帧 1080p NV12，17–33ms），这是完整演示模式跑满一个核以上的已知原因；计划改用 RGA 缩放。
- 美颜按近景人像、最多 3 张脸调过；4K 未测。
- **应用内录制产物标为 `yuvj420p` / `pc` / `gbr`。** 画面看起来正确，但严格按标记解释颜色空间的播放器或转码器可能偏色；两次改成 `yuv420p` / `tv` / `bt709` 的尝试都掉到 5.5–6.9 唯一帧/秒，已回退（见 [docs/PITFALLS.md](docs/PITFALLS.md) §3）。
- **板端尚未人工点选验证**：chip/卡片上的触摸点按、真实拖放、目录选择、排序弹层，以及浏览壳里的 H.265/VP9/AV1 文件。
- **离线安装包不带 RKNN 模型**，所以拷入 `models/*.rknn` 前 `--check` 会报一条警告；视频与图片素材也不随仓库分发。
- 性能数字是板端实测，不保证其他内容、编码或显示模式。

## 许可

Apache License 2.0，见 [LICENSE](LICENSE)。欢迎 Issue 与 Pull Request；技术咨询请走 [Quectel 官方论坛](https://forumschinese.quectel.com/c/quectel-pi/58)。更多板端演示见 [Quectel-Pi](https://github.com/Quectel-Pi)。
