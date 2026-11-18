# 实测证据（M1–M4）

这些图片由板端真实应用抓取，不是开发主机截图。抓屏方式：应用内
`Gtk.WidgetPaintable` + `Gsk.Renderer.render_texture()` 快照
（`python3 scripts/launch_player.py --demo <video> --snapshot out.png --snapshot-delay N`）。

## M1 播放

| 文件 | 内容 | 关键读数 |
|------|------|----------|
| `m1-frame.png` | 抽出的一帧 1080p 原始画面（1920×1080） | 烧录时间码 00:00:04.200、帧计数 126，证明 `mppvideodec` 硬解 + paintable 渲染链路正常 |
| `m1-ui-gl.png` | 板端 DSI 屏上的完整播放窗口（1280×800），GPU 色彩转换路径 | 1920×1080 / X-H264 / `mppvideodec` / fps 28.7，进度实时 |
| `m1-ui-sw.png` | 同一窗口，`video_convert=sw`（CPU `videoconvert` 降级） | fps 3.7，画面明显滞后（对照用，非交付路径） |

## M2 NPU 人脸检测

| 文件 | 内容 | 关键读数 |
|------|------|----------|
| `m2-ui-detect.png` | 单人 720p 素材 + NPU 检测 | 人脸框与 5 点关键点、信息行 `NPU: 1 face · 37.8 ms`、`fps: 30.0` |
| `m2-ui-two-faces.png` | 双人 720p 素材 + NPU 检测 | 2 个人脸框、`NPU: 2 face · 36.8 ms`、`fps: 30.0` |

## M3 GPU 美颜 + 对比

抓取环境：1080p30 H.264 近景素材（`test-media/pexels/5534415…` 转码到板端
`/tmp/face_1080p30.mp4`），DSI 1280×800；`VIDEO_PLAYER_PERF_LOG=1` 时应用每秒
打印 `PERF t=..s fps=.. beauty=..ms faces=.. npu=..ms cpu=..%`。

> 注意：美颜只在**检测到人脸**时生效（无脸 → `u_active=0` 整链透传）。因此下面
> 的开/关 A/B 与分屏证据都在**检测开启**下抓取；检测关闭时 `beauty: on` 只是
> 开关状态，画面不会有可见变化。

| 文件 | 内容 | 关键读数 |
|------|------|----------|
| `m3-beauty-on.png` | 检测开 + 美颜开（cfg_faceon），窗口 1280×768 | `fps: 30.0`、`NPU: 1 face · 38.5 ms`、`beauty: on 1.1 ms`；面部平滑提亮、五官保留 |
| `m3-beauty-off.png` | 同素材、同帧附近，检测开 + 美颜关（cfg_A） | `fps: 30.0`、`NPU: 1 face · 40.9 ms`、`beauty: off`；与上图构成同一场景 A/B |
| `m3-beauty-ab.png` | 上图各裁人脸区并排（A 原始 / B 美颜） | A3 主证据：皱纹、毛孔与泛红明显平滑，肤色提亮不发灰；虹膜/睫毛仍锐利，遮罩外头发与背景不变 |
| `m3-split-demo.png` | demo 模式（`--demo`，1280×800 全屏）：强制分屏 + 检测开 | 左半原图 / 右半美颜、白色分割线 0.50；`fps: 30.0`、`NPU: 1 face · 40.9 ms`、`beauty: on 1.3 ms`、侧栏参数行与信息行一致（A3 同帧对比） |
| `m3-split-closeup.png` | 上一图裁切放大 1.5×（带 ORIGINAL/BEAUTY 标注） | 分割线两侧皮肤纹理差异清晰；羽化过渡无硬接缝 |
| `m3-mask.png` | `beauty.debug_mask=true` 遮罩预览（检测开） | 白色=羽化圆角矩形人脸处理区；眼睛/眉毛被肤色门限压暗；框外全黑（背景/头发不处理）；绿框与遮罩对齐 |
| `m3-rot180-beauty.png` | rotate-180 自拍片源 + 美颜（检测开） | 画面正立、人脸框与磨皮区域均落在脸部（方向处理正确） |
| `m3-h265-selfie-demo.png` | demo 分屏 + H.265（HEVC 1080p30，rotate-180 自拍素材 `user-selfie-1080p30.mp4`） | `1920×1080`、`X-H265`、`decode: mppvideodec`、`fps: 30.0`、`NPU: 1 face · 43.0 ms`、`beauty: on 1.1 ms`；画面正立、人脸框与分屏线对齐（A1 的 H.265 补测证据） |
| `m3-rot180-mask.png` | 同上素材的遮罩预览 | 遮罩随检测框反向旋转回纹理坐标，无镜像/错位 |

性能读数（同一批抓取的 `PERF` 行，取稳定段；详见 [../BOARD.md](../BOARD.md) §5.2）：

| 场景 | fps | CPU | beauty | NPU |
|------|-----|-----|--------|-----|
| 检测关 + 美颜关（cfg_C） | 30.1–30.2 | 30–32% | 透传 ≈0.8 ms | — |
| 检测关 + 美颜开（cfg_B） | 30.0–30.4 | 30–33% | 0.68–1.36 ms | — |
| 检测开 + 美颜开（cfg_faceon） | 30.0 | 110–119% | 1.06–1.56 ms | 37.5–44.0 ms |
| demo 分屏全开（`--demo`） | 30.0 | 117–131% | 1.1–1.5 ms | 37.8–41.2 ms |

注（2026-11-18）：表中的 fps 是 `fpsdisplaysink` 的累计平均值，早期曾被误读为
「周期性 20–25 fps 掉速窗口」——实际播放全程 30.0 帧/s、`drop=0.000%`（见
[../BOARD.md](../BOARD.md) §5.2 的 fps 读数修正与起播丢帧两行）。现在界面与
PERF 行的 fps 改用 `PlaybackBackend.measured_fps()`（1.5 s 滑窗到达率），检测开启
时起播丢帧也已用真实尺寸 NPU 预热消除。

## M4 应用内录制（`--record`，M3 收尾实现）

没有采集卡时，演示视频由应用自己录制：视频分支经 `tee` 进 `glvideomixer`，与
10 fps 重建的 UI 覆盖层（背景 + 窗口 chrome 快照 + 人脸框）在 GPU 内合成后
`gldownload` → `mpph264enc` → `mp4mux`。录制时显示分支旁路（`fakesink`），
所以板端屏幕上视频区是黑的，录制文件本身正常。架构与踩坑见
[../ARCHITECTURE.md](../ARCHITECTURE.md) §2.3。

| 文件 | 内容 | 关键读数 |
|------|------|----------|
| `m4-record-split.png` | 录制文件抽帧：demo 模式分屏（左原图/右美颜），UI 全部合成进画面 | `fps: 30.0`、`NPU: 1 face · 38.1 ms`、`beauty: on 1.6 ms`；侧栏/信息行/控制条/人脸框均与视频对齐 |
| `m4-record-selfie-rot180.png` | 录制文件抽帧：rotate-180 自拍 H.265 素材 | 画面正立（录制链的 `glvideoflip` 补了 sink 侧的显示旋转）、分屏线与脸对齐 |

录制产物规格（`--demo --record out.mp4 --record-duration N`）：1280×800@30
H.264、约 5 MB / 10 s、`enc 30–32 帧/s`、CPU 184–205%。7 段演示素材由
`docs/demo-video/shoot.sh` 拍摄、`docs/demo-video/build_video.sh` 剪接并烧
双语字幕，成片 1920×1080@30、约 62 s（内部文件，不入公开仓库）。

复现命令（板端，项目目录；`/tmp/cfg_A.json` = 检测开+美颜关，`/tmp/cfg_B.json`
= 检测关+美颜开，`/tmp/cfg_C.json` = 都关，`/tmp/cfg_faceon.json` = 检测开+美颜开）：

```bash
export XDG_RUNTIME_DIR=/run/user/1000 WAYLAND_DISPLAY=wayland-0 GST_GL_PLATFORM=egl
export VIDEO_PLAYER_PERF_LOG=1

# A2：美颜开/关与真实人脸场景（信息行 + PERF 行给出 fps、beauty ms、CPU）
python3 scripts/launch_player.py --config /tmp/cfg_B.json /tmp/face_1080p30.mp4
python3 scripts/launch_player.py --config /tmp/cfg_faceon.json /tmp/face_1080p30.mp4

# A3：demo 分屏（强制）与遮罩预览
python3 scripts/launch_player.py --demo /tmp/face_1080p30.mp4 \
  --snapshot /tmp/m3shots/final_demo.png --snapshot-delay 8
python3 scripts/launch_player.py --config /tmp/cfg_mask.json /tmp/face_1080p30.mp4 \
  --snapshot /tmp/m3shots/final_mask.png --snapshot-delay 8
```

性能数据与结论见 [../BOARD.md](../BOARD.md) §5.2 与
[../ARCHITECTURE.md](../ARCHITECTURE.md) 第 2、7 节。

## A4 / A5 验收证据（2026-11-18）

| 文件 | 内容 | 关键读数 |
|------|------|----------|
| `a4-three-faces.png` | 固定三人素材（Pexels 帧合成的 1080p30 8 s 片段：青年正脸 + 银发女士近景 + 访谈男子微侧）× demo 检测 + 分屏 | 3 个人脸框 + 5 点全部命中、`NPU: 3 face · 39.8 ms`、`fps: 30.0`、`beauty: on 2.5 ms`；A4「≥3 张脸、含微侧脸」 |
| `a4-three-masks.png` | 同一素材的 `beauty.debug_mask=true` 遮罩预览 | 三块羽化遮罩各自落在对应人脸上、眼睛/眉毛被肤色门限压暗、框外全黑；证明三脸遮罩与检测框对齐 |

A4 的 1 脸 / 2 脸证据沿用 `m2-ui-detect.png`、`m2-ui-two-faces.png`；`06_three_faces.mp4` 是内部测试素材，不入公开仓库。

A5（1 h 长跑，检测+美颜开）指标：`/home/tris/soak2.log`（PERF 每秒）与
`/home/tris/soak2_rss.log`（每分钟 RSS/线程/fd，内部日志）。3549 个样本：t≥300 s
均值 30.06 fps、最后 600 s 全部 ≥30.0 fps；RSS +4.8 MB/h、threads 29–32、fds 74–79
无增长，播放结束自动退出。唯一 <29.5 fps 的 7 个样本来自并行部署/构建的外部负载窗口。

