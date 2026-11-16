# 实测证据（M1，2026-11-14）

这些图片由板端真实应用抓取，不是开发主机截图。抓屏方式：应用内
`Gtk.WidgetPaintable` + `Gsk.Renderer.render_texture()` 快照
（`python3 scripts/launch_player.py --demo <video> --snapshot out.png --snapshot-delay N`）。

| 文件 | 内容 | 关键读数 |
|------|------|----------|
| `m1-frame.png` | 抽出的一帧 1080p 原始画面（1920×1080） | 烧录时间码 00:00:04.200、帧计数 126，证明 `mppvideodec` 硬解 + paintable 渲染链路正常 |
| `m1-ui-gl.png` | 板端 DSI 屏上的完整播放窗口（1280×800），GPU 色彩转换路径 | 1920×1080 / X-H264 / `mppvideodec` / fps 28.7，进度实时 |
| `m1-ui-sw.png` | 同一窗口，`video_convert=sw`（CPU `videoconvert` 降级） | fps 3.7，画面明显滞后（对照用，非交付路径） |

复现命令（板端，项目目录）：

```bash
export XDG_RUNTIME_DIR=/run/user/1000 WAYLAND_DISPLAY=wayland-0
python3 scripts/launch_player.py --demo /path/to/1080p30.mp4 \
  --snapshot docs/evidence/m1-ui-gl.png --snapshot-delay 5
```

性能数据与结论见 [../BOARD.md](../BOARD.md) §5.2 与
[../ARCHITECTURE.md](../ARCHITECTURE.md) 第 2、7 节。
