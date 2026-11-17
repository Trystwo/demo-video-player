# 演示指南（草稿）

> 状态：M1（2026-11-14，1080p30 实时）、M2（2026-11-16，NPU 检测 + 人脸框）、M3（2026-11-17，GPU 美颜 + 对比分屏）均已实现并在板端验证；本文件按 M3 实测修订，M4 补充演示视频与最终截图。演示范围（2026-11-14 确认）：本地硬解播放 + 自动美颜 + 一键/分屏对比；网络流与字幕不在本期。
> 环境与性能前置条件见 [BOARD.md](BOARD.md)，范围见 [PRD.md](PRD.md)，交付与拍摄计划见 [DELIVERY.md](DELIVERY.md)。

## 1. 演示目标

用一段人像视频，在一次演示中同时说明三件事：

1. 视频由 **VPU 硬解**（画面流畅、CPU 占用低）；
2. 人脸被 **NPU 自动检测**，磨皮/美白只作用在脸部区域（背景、头发保持原样）；
3. 一键切换 **原图/美颜对比**，效果肉眼可辨，并能看到实时的 fps 与 NPU 耗时。

## 2. 演示准备

| 项目 | 要求 | 状态 |
|------|------|------|
| 硬件 | M2 开发板 + DSI 屏（1280×800）+ 电源、键鼠或触屏 | 就绪 |
| 系统 | Debian 13 出厂镜像，GNOME/Wayland 会话 | 就绪 |
| 板端依赖 | `./install.sh --require-board` 全部通过 | `mppvideodec`/`gtk4paintablesink`/`glshader`/`glcolorscale`/`librknnrt` 均已验证 |
| 模型 | `models/yunet_rk3576.rknn` 已放置（`scripts/convert_yunet.py` 生成） | 已就绪（M2） |
| 素材 | 正脸 1080p30、多人侧脸素材；本机 `test-media/pexels/` 已有 5 个候选（见 [TEST_MEDIA.md](TEST_MEDIA.md)），公司自有素材可替换；4K 为可选加分项 | 已就绪（M3 用 5534415 近景与 5252440 双人验证） |
| 音频 | 板载扬声器或耳机 | 就绪 |
| 环境 | 现场灯光均匀，人物面向屏幕，与镜头 ≤45° 侧脸也可验证 | — |

启动前自查：

```bash
cd demo-video-player
./install.sh --require-board
python3 -m video_player --check
```

## 3. 演示步骤（M3 实测版）

1. **启动**：执行
   `python3 scripts/launch_player.py --demo <媒体路径>`，窗口全屏出现在 DSI 屏上，
   自动打开对比分屏与检测（demo 模式强制，桌面启动器在 M4 提供）。
2. **硬解播放**：播放 1080p30 H.264 人像视频；指出信息面板的
   `decode: mppvideodec`、分辨率 `1920×1080`、`fps: 30.0`，说明色彩转换与预缩放
   都在 GPU 上完成。
3. **NPU 检测**：信息行显示 `NPU: 1 face · 38.5–40.9 ms`，画面叠加绿色人脸框与 5 点关键点；
   侧栏 `Show face boxes` 可关。说明检测以 10Hz 采样、单次约 30–40ms，播放仍为 30fps。
4. **自动美颜**：默认开启（`Automatic face beauty`），画面右侧人物脸部平滑/提亮；
   遮住脸部或移到无人脸片段，指出“无人脸时不处理”，背景与头发保持原样（A3）。
5. **强度调节**：拖动 `Smoothing` / `Whitening`（0–100）滑杆，效果实时生效；
   说明只处理人脸区域、羽化蒙版过渡无接缝（可把 Smoothing 拉到 100 展示上限）。
6. **一键对比**：`Compare` 按钮或 `C` 键切换分屏/整帧；`Split position` 拖动分割线；
   `B` 键直接开关美颜。三者都在同一帧内完成（F9）。
7. **信息面板**：读数 `fps 30.0`、`NPU 38–41 ms`、`beauty: on 1.1–1.5 ms`，
   给出 A2（≥25fps）与美颜开销（<2ms/帧）结论。
8. **遮罩演示（可选）**：`M` 键或 `beauty.debug_mask=true` 显示白/黑遮罩预览，
   说明“白色=处理、黑色=不处理”，眼睛眉毛因肤色门限被压暗。
9. **多人/侧脸**：播放多人素材，说明最多 3 张脸、≤45° 侧脸仍可检出（A4）。
10. **收尾（可选）**：连续播放 1h 稳定性（A5，M4 补测）。

## 4. 讲解要点

- 不是“软件滤镜”：解码、检测、美颜分别落在 VPU、NPU、GPU，CPU 只做调度，
  这是 RK3576 相对纯 CPU 方案的核心价值。
- 参考项目定位：IINA 是桌面播放器交互的标杆，ExoPlayer 是“解码 + 渲染可插拔”
  的分层参考；M3 版本用 GStreamer 还原同样的分层（source → decode → filter →
  sink），美颜就是一个可插拔的 `glshader` filter（预缩放也在同一段 GL 链路里）。
- 自动美颜的边界：先检测再处理，检测按帧间隔降低开销，帧间用“上次结果/跟踪”
  补足；无人脸时整条美颜链路透传。
- 效果与性能的折中：磨皮/美白强度、检测间隔、处理分辨率都是可调参数，
  演示时可以指出当前档位与实测 fps。

## 5. 降级预案

| 情况 | 预案 |
|------|------|
| NPU 模型/运行时异常 | 关闭美颜，演示硬解播放与信息面板；展示 M3 对比图（`docs/images/beauty-compare.png`），并说明检测不可用时美颜自动透传 |
| 美颜帧率不足 | 先关检测（信息行 `NPU: off`）确认 30fps；再降 `texture_max_dimension` 或改 720p 素材（信息面板显示实际值） |
| `gtk4paintablesink` 异常（已验证 0.13.5 可用，留作预案） | 用独立全屏 `waylandsink` 窗口，对比模式改为前后切换而非分屏 |
| 4K 素材缺失或播放不流畅 | 4K 不作硬指标（P4），改用 1080p H.265 展示硬解余量 |
| 摄像头输入 | 本期不做（P2 结论），无需准备 |
| 素材版权问题 | 只用公司自有素材；不下载影视片段做演示 |

## 6. 拍摄/证据清单（M4 补齐）

- [x] 主窗口截图 → `docs/images/main-window.png`（2026-11-14，取自 `docs/evidence/m1-ui-gl.png`）
- [x] 美颜前后对比截图 → `docs/images/beauty-compare.png`（A/B 近景对比，取自 `docs/evidence/m3-beauty-ab.png`）；主窗口分屏 → `docs/images/beauty-split.png`（`m3-split-demo.png`）；同帧近景 → `docs/evidence/m3-split-closeup.png`
- [ ] 板端环境自检输出（`--markdown`）→ 回填 [BOARD.md](BOARD.md)（部分已回填）
- [ ] 演示视频（约 90 秒、1080p30、中英双语硬字幕）→ 按 [DELIVERY.md](DELIVERY.md) 拍摄方案执行，成片链接记录到本文件
- [ ] 验收 A1–A6 的逐条证据（fps/CPU/检出率/1h 稳定性）

归档约定：图片放 `docs/images/`，过程证据与历史评审放 `docs/history/`
（M1 起创建），文件名使用英文短横线命名，避免空格与中文文件名。
