# 实测证据（M1–M3）

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

| 文件 | 内容 | 关键读数 |
|------|------|----------|
| `m3-beauty-on.png` | 美颜开、检测关（cfg_B），窗口 1280×768 | `fps: 30.1`、`beauty: on 1.0 ms`、`NPU: –`；面部平滑提亮、五官保留 |
| `m3-beauty-off.png` | 同素材美颜关（cfg_C），同窗口 | `fps: 30.2`、`beauty: off`；与上图对比可见磨皮/美白效果 |
| `m3-split-demo.png` | demo 模式（`--demo`）：强制分屏 + 检测开，全屏 1280×800 | 左半原图 / 右半美颜、白色分割线在 0.50；`fps: 30.0`、`NPU: 1 face · 38.9 ms`、`beauty: on 1.2 ms`（A3 主证据，同一帧对比） |
| `m3-split-closeup.png` | 上一图裁切放大（1.5×，带 ORIGINAL/BEAUTY 标注） | 左：额头/眼角皱纹与色斑清晰；右：明显平滑、肤色提亮不发灰；眼睛眉毛保持锐利、遮罩外头发背景不变（A3 近景） |
| `m3-mask.png` | `beauty.debug_mask=true` 遮罩预览（检测开） | 白色=羽化圆角矩形人脸处理区；眼睛/眉毛被肤色门限压暗；框外全黑（背景/头发不处理）；绿框与遮罩对齐 |
| `m3-rot180-beauty.png` | rotate-180 自拍片源 + 美颜（检测开） | 画面正立、人脸框与磨皮区域均落在脸部（方向处理正确） |
| `m3-rot180-mask.png` | 同上素材的遮罩预览 | 遮罩随检测框反向旋转回纹理坐标，无镜像/错位 |

复现命令（板端，项目目录；`/tmp/cfg_B.json` = 美颜开+检测关，`/tmp/cfg_C.json` = 都关）：

```bash
export XDG_RUNTIME_DIR=/run/user/1000 WAYLAND_DISPLAY=wayland-0 GST_GL_PLATFORM=egl
export VIDEO_PLAYER_PERF_LOG=1

# A2：美颜开 / 关（信息行 + PERF 行给出 fps、beauty ms、CPU）
python3 scripts/launch_player.py --config /tmp/cfg_B.json /tmp/face_1080p30.mp4
python3 scripts/launch_player.py --config /tmp/cfg_C.json /tmp/face_1080p30.mp4

# A3：demo 分屏（强制）与遮罩预览
python3 scripts/launch_player.py --demo /tmp/face_1080p30.mp4 \
  --snapshot /tmp/m3shots/demo_default.png --snapshot-delay 12
python3 scripts/launch_player.py --config /tmp/cfg_mask.json /tmp/face_1080p30.mp4 \
  --snapshot /tmp/m3shots/demo_mask.png --snapshot-delay 12
```

性能数据与结论见 [../BOARD.md](../BOARD.md) §5.2 与
[../ARCHITECTURE.md](../ARCHITECTURE.md) 第 2、7 节。
