# 视频播放器架构设计（M1–M5：播放、检测、GPU 美颜、录制与实验性 warp）

> 状态：M1 硬解播放、M2 NPU 人脸检测、M3 GPU 美颜与对比模式、M4 录制与交付打磨、
> M5 检测保持与实验性瘦脸/大眼 warp 均已实现并在板端验证；ui-v5 P1–P4（浏览壳/首页/详情/
> 播放页）已于 2026-11-27 全部板端验收（§2.11）；同日的**最小设置页**（§2.12，语言 / 解码 /
> 扫描目录 / 关于）已于 2026-11-27 10:02 上板验收（§2.12）；1080p30 播放稳定 30 fps；
> 磨皮/美白在 Mali GPU 的单个 `glshader` 内完成（M3 单帧 0.8–1.6 ms，相对纯播放的
> CPU 增量约 +1–2 个百分点）；人脸遮罩由 NPU 检测框光栅化，分屏对比在同一帧、
> 同一次 pass 内完成。证据见 `docs/evidence/m3-*.png`，实测数据见
> [BOARD.md](BOARD.md) §5.2，范围变更同步到 [PRD.md](PRD.md)；M5 的检测保持与
> warp 实测见 §7。

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

### 2.1 美颜元素（M3 颜色美颜 + M5 几何 warp，`glshader` 单 pass）

- 美颜不新增自定义 GStreamer 元素，而是一个 `glshader`（元素名 `video-beauty`），
  插在 GL 转换链尾部、`fpsdisplaysink` 之前。顶点阶段使用元素内建的
  `a_position`/`a_texcoord`/`u_matrix`（已板端验证，无需自写顶点 shader）；
  片元阶段是 `beauty.BEAUTY_FRAGMENT_SHADER`。
- **不需要第二张蒙版纹理**：所有参数（最多 3 组人脸框、磨皮/美白强度、羽化像素、
  向外扩张比例、肤色门限、分屏位置、调试开关，以及 M5 的主脸中心/脸宽脸高/
  双眼中心/旋转角与 warp 强度）都以 scalar `G_TYPE_FLOAT` uniform 传入，遮罩在片元里由
  「圆角矩形 SDF + `smoothstep` 羽化」光栅化，再乘 YCbCr 肤色权重，避免把 CPU
  生成的 mask 位图上传成纹理（也规避了 `glshader` 只有一个采样器输入的约束）。
  uniform 清单：

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
  | `u_active` | 美颜关闭或参数为恒等时置 0，片元直接透传，不采样邻域 |
  | `u_slim`, `u_eyes` | M5 实验性瘦脸/大眼强度 0–1（0 = 关闭，与 M4 等价） |
  | `u_warp_ready`, `u_warp_cx/cy` | M5 主脸 warp 开关、主脸中心（显示归一化坐标 0–1） |
  | `u_warp_face_w/h` | M5 主脸脸宽/脸高（显示归一化），驱动瘦脸作用区 |
  | `u_warp_lx0/ly0/lx1/ly1` | M5 双眼中心（显示归一化，大眼向眼心采样） |
  | `u_warp_rot` | M5 纹理→显示旋转角（0/90/180/270），shader 内换回纹理坐标 |
  | `u_shape_ready` | M5.2 脸型椭圆可用（=5 点关键点齐全且脸未退化）；0 时整段回退 M5 行为 |
  | `u_shape_cx/cy/a/b/eye_y/d` | M5.2 由 5 点拟合的椭圆（显示像素）：中心、横/纵半轴、眼线 y、眼距 d |
  | `u_slim_peak`, `u_disp_w/h` | 当前瘦脸峰值位移（px，=0.07·a，钳在 4–30）与显示帧尺寸（px） |

  以上 scalar uniform 共 **47 个 `G_TYPE_FLOAT`**（M3 的 25 个 + M5 新增 12 个 +
  M5.2 新增 10 个；`tex` 是采样器，不计入）。M5.2 的几何量一律用**显示像素**：
  椭圆长短轴以 px 表达，位移以 px 表达，`warp_uv` 里再除以 `u_disp_w/h` 换回 UV，
  这样羽化宽度（px）与位移强度（px）在 720p/1080p 下观感一致。

- 磨皮是 8 邻域双边近似（9 tap）：空间权重随 `u_smoothing` 放大半径
  （1.5–8.5 px），值域权重抑制跨边缘混合，因此皱纹被平滑而睫毛/发丝等强边缘保留；
  美白向白点插值并回补饱和度（`1 - 0.18*amount`），避免提亮后发灰。
- **检测保持（M5，消除美颜闪烁）**：10 Hz 检测在快速运动或检测条件不佳的素材上
  会偶发 `raw=0` 的空档，M4 及更早版本每个空档都会清空人脸框与关键点，表现为美颜
  周期性闪断。M5 在 backend 里维护最近一次结果与时间戳，空档未超过
  `detection.hold_ms`（默认 600）时继续 dispatch 上一次的 faces 并标记
  `held=True`（UI 人脸框同步保持，不再闪断）；5 点关键点做 EMA
  （`alpha=0.35`，点数≠5 或中心跳变 >0.3×脸宽时重置），warp 控制点因此不抖。
  v2 成片中最明显的周期性美颜闪烁（01 近景素材可复现，空档 6 次）在 v3 中降为 0。
- **无脸/超时段落透传**：无脸超过 hold 窗口或检测关闭、模型不可用时，shader 的
  人脸遮罩为 0（`u_face*=0`），输出原像素，不产生采样与混合开销；美颜关闭或参数
  为恒等时 `u_active=0` 直接透传。播放不受影响。
- 坐标系：`mppvideodec` 输出未旋转帧，屏幕旋转由 sink 按 `image-orientation`
  tag 完成；检测线程先把 BGR 旋转到屏幕方向再送 YuNet，所以检测框在「显示
  坐标系」，写 uniform 前经 `beauty.face_rect_to_texture()` 反向旋转回纹理
  坐标（0/90/180/270 均有单测；板端用 `ffmpeg -display_rotation` 生成的
  90°/180°/270° 素材 + 标记图验证过方向，见 BOARD §5.2）。
- 对比模式（F9）在 shader 内完成：`u_compare=1` 时 `x < u_split` 输出原图，
  分割线附近画 2 px 白线，因此分屏是**同一帧、同一次 pass**，不存在两路管线
  的帧对齐问题。demo 模式（`--demo`）强制打开对比，并显式开启美颜：
  `beauty.enabled=false` 时 `u_active=0`，shader 在画分割线之前就返回原图。
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
  （标题栏 / 侧栏 / 信息行 / 控制条 / 快捷键提示 / 状态卡）+ 每帧自绘的人脸框与 5 点。
  标题栏自 2026-11-27 起加入 `_recording_layer_specs()` 的首层（此前漏掉，成片顶部
  留 59 px 空带）。背景与图层只在 widget 变化时重拍（侧栏 3 s、信息行/控制条 1 s、
  前 2 s 每 tick 一次——刚建窗时 widget 还没画出第一帧，快照会是空白），每帧只做
  `base.copy()` + 画框。
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

### 2.4 纯视频录制（演示素材采集，M4 收尾）

带 UI 的片内合成在板端只能跑到 ~21 fps（见上表），录出来的画面本身会出现冻结帧
（M3 诚片 42.6% near-dup）。为了让演示视频里"播放画面"绝对顺滑，素材改用**纯视频
录制**：`Recorder(..., video_only=True)` 跳过 `glvideomixer`，直接从渲染链的
`tee` 接出 `queue → capsfilter(GL RGBA) → gldownload → mpph264enc → h264parse →
mp4mux → filesink`，队列 `leaky=0` / `max-size-buffers=8`（宁可阻塞也不丢帧补
重复）。驱动脚本 `docs/demo-video/record_video.py`（headless：
`PlaybackBackend(config, display_bypass=True)`，支持 `--seconds/--start-at/
--beauty/--ramp-smoothing` 等）；UI、标签、字幕全部后期叠加。

实测（板端 `/tmp/43-m2`，`rec3/` 六段素材）：1920×1080@30 采集吞吐 ≈29 fps
（H.265 自拍与 H.264 近景相近），成片 1456 帧中相邻重复帧 **1.79%**（对比 M3
成片 42.6% near-dup）；成片播放无冻结帧。

旋转踩坑：源文件的 display matrix 会以 `image-orientation` tag 一路传到 `mp4mux`
并被写进录制文件（`ffprobe` 可见 `rotation=-180`），所以纯视频录制**不能再对像素
做翻转**——否则播放器会把矩阵和像素各转一次，录制文件看起来上下颠倒。

### 2.5 实验性美颜：瘦脸 / 大眼（几何 warp，M5）

M5 在 M3 颜色美颜（磨皮/美白）之外加了一组**几何形变**：瘦脸把下半脸整体收窄、
大眼温和放大眼睛。默认 `face_slim=0`、`eye_enlarge=0`，即默认与 M4 完全等价，只有
把滑杆调离 0 才启用。

- **显示空间几何**：所有 warp 都在显示（upright）归一化坐标里计算，
  `beauty.warp_uniforms()` 由主脸框 + 5 点关键点生成 12 个 uniform：主脸中心、脸宽/
  脸高、双眼中心与旋转角。shader 用 `tex_to_display()` / `display_to_tex()`
  与纹理坐标互换（0/90/180/270 四向互逆，单测覆盖）。5 点缺失、点数≠5 或框退化时
  `u_warp_ready=0` 且全部归零——不做半套 warp，避免关键点补齐时跳变。
- **瘦脸 = 下半脸整体收窄**：片元在脸框局部坐标
  `rel = (disp - centre) / (face_w/2, face_h/2)` 上，纵向
  `smoothstep(-0.05, 0.30, rel.y)`（眼线以上不动）、横向
  `smoothstep(0.15, 0.95, |rel.x|)`（中线不动、轮廓处最大），把采样点沿 x 向轮廓外
  拉 `(disp.x - cx) × 0.16 × u_slim`（常量 `SLIM_STRENGTH=0.16`）——下半脸左右一起
  内收，是整体收窄而不是某几个控制点的局部凹陷。
- **大眼 = 眼睛区域各向异性放大**：以双眼中点为心、椭圆半径
  `(0.16×脸宽, 0.16×脸宽×0.72)`，采样点向眼心收
  `-delta × 0.22 × u_eyes × (1 - smoothstep(0.20, 1.0, |delta/radii|))`
  （常量 `EYE_MAGNIFY=0.22`、`EYE_RADIUS=0.16`、`EYE_ASPECT=0.72`）→ 眼心放大率约
  1.28×，温和不凸眼；椭圆略高于宽，只覆盖眼周、不拽动整个眼眶。
- **作用域**：位移乘 `warp_gate` = 未外扩主脸框的圆角矩形 SDF（羽化
  `1-smoothstep(-2, 6, dist)` px），**不再乘肤色门限**（旧实现逐像素肤色权重会让
  位移随皮肤纹理斑驳、边界出现断裂/凹陷）；框外背景、头发、衣服不动。最终
  `clamp(uv, 0, 1)` 防越界采样。
- **执行顺序**：先用 warp 后的 UV 采样 base（瘦脸与两只眼睛的位移相加后乘 gate），
  遮罩/磨皮仍用未 warp 的像素坐标计算，磨皮在 warp 后的采样上做；分屏对比（F9）
  左半依然输出未 warp 的原图。
- **UI / 配置 / 演示约束**：侧栏「实验性 / Experimental」折叠面板（默认折叠）内是
  两个 0–100 滑杆，实时生效；配置项 `beauty.face_slim`、`beauty.eye_enlarge`
  （float，默认 0）。侧栏整体放在 `Gtk.ScrolledWindow` 中，展开「实验性」后自动滚到
  底部，避免 1280x800 板屏下窗口被撑出屏幕。演示配置必须 `detection.max_faces=1`：
  素材背景里还有第二张脸，多脸时背景人脸也会被磨皮遮罩覆盖。warp 只作用主脸——
  backend 用 `face.primary_face()` 按面积取最大者并排到 `u_face0`，不依赖检测器
  返回顺序。
- **演进（v1.1.1，2026-11-19）**：M5 初版用「左右下颌控制点 + 局部水平位移 +
  肤色门限」，用户反馈瘦脸像「脸凹进去一块」、大眼不自然（径向放大 1.67×）；
  因此重做为上述整体收窄 + 椭圆温和放大，并去掉 warp 里的肤色门限。改后 CPU 复算
  最大瘦脸位移约 0.08×脸宽（526 px 脸约 42 px，沿轮廓平滑过渡），大眼中心放大
  1.28×；板端 A/B（同源常量片段）与 v3 成片抽帧目视均自然。
- **性能**：warp 只改采样坐标、不增加采样次数；美颜+检测时 `beauty_ms`
  0.86–0.87 → 0.86–0.96（瘦脸/大眼=100），增量 ≤0.1 ms，fps 仍 30.0（见 §7）。

### 2.6 美颜自然化（M5.2：脸型椭圆蒙版 + Lab 肤色分割 + 脸内瘦脸）

M5.1 之后用户仍反馈三点：「美颜痕迹明显」「瘦脸时旁边环境跟着变」「美白把头发也提亮」。
根因都在 shader 的两个「矩形」上：蒙版是检测框的圆角矩形（框里有头发、镜框、耳机线、
椅背），瘦脸位移按框内归一化坐标算、一直保持到框边才归零（框内背景被一起拖走，框边
42 px 突变）。M5.2 用**同一个 fragment pass**把这两件事都换掉，不新增 pass、不新增纹理。

- **脸型椭圆（替代蒙版矩形）**：`beauty.fit_face_shape()` 只用 5 点关键点（无需额外模型）：
  `d`=眼距，横半轴 `a=0.90d`，上缘 `eye_y-0.80d`（发际线），下缘 `mouth_y+0.42d`（下巴），
  中心 `cx`=眼中点与嘴角中点的均值、`cy`=上下缘中点 —— 因此偏头时椭圆跟着走而不是
  被检测框的左右留白拉偏。本帧实测 `cx=906.6, cy=554.1, a=234.5, b=292.3`。
  椭圆外再并一个圆角矩形「颈部补丁」（半宽 `0.45a`、半高 `0.29b`、中心 `chin+0.13b`），
  管住下巴到脖子的过渡，不留未处理的台阶。羽化仍用 `u_feather`（px），
  所以椭圆边界是 `1-smoothstep(0, feather, (r-1)·a)` —— 位移量以像素为单位。
- **Lab 肤色分割（替代 YCbCr + 10 % 保底）**：M5 的 `mask *= mix(1.0, 0.10+0.90*skin_gate, u_skin)`
  有 10 % 保底，等于框内**所有**像素至少拿 10 % 磨皮/美白 —— 头发发灰、镜框被抹平就是
  它造成的；而 YCbCr 又会把头发判成 0.64 的「肤色」。M5.2 改为 sRGB→线性→CIELAB
  （D65），用 `a*`/`b*` 两个通道做**两级**门限：`loose`（磨皮，`a*∈[2.5,18]`、
  `b*∈[7,25]`、`L>0.18`）与 `strict`（美白，`a*∈[4.5,13]`、`b*∈[9.5,21]`、
  `L∈[0.25,0.35]` 升、`0.86–0.96` 高光降）。本帧实测：皮肤 `a*≈7–9, b*≈12–13`、
  头发 `a*≈-1–1, b*≈8–10`、椅背 `a*≈0.3`、虹膜 `a*≈-10.8`、唇 `a*≈13` ——
  头发/背景被判 0，**没有保底**，唇色与镜框不再被美白。磨皮与美白从此**各用各的蒙版**
  （M5 是同一个 `mask`），高光与深色头发不再被提亮。
- **瘦脸 = 下颌两侧内收 + 下巴上提，横纵分离且无逐行剪切**：位移场为
  `vec2(±u_slim_peak·u_slim·side, 0.85·u_slim_peak·u_slim·lift) × slim_support_gate`，其中
  `rel_x=(x-cx)/max(a,1)`、`rel_y=(y-cy)/max(b,1)`，
  `below_eyes=smoothstep(0.05,0.55,(y-eye_y)/d)`（眼线以上不动），
  `side=smoothstep(0.15,0.95,|rel_x|)·below_eyes`（两颊向外采样 → 可见下颌内收），
  `lift=smoothstep(0.55,0.98,rel_y)·(1-smoothstep(0.55,1.00,|rel_x|))·below_eyes`（向下采样 → 近水平的下巴底缘上提）。
  **水平量只依赖 `rel_x`，不按每行 `a_y` 归一化**：旧场用 `a_y` 使位移在尖端逐行骤降（实测
  `max |∂dx/∂y|≈5.5 px/px`），这正是用户看到的「断层」；新场 `∂dx/∂y≈0`，下巴交给 `lift`，尖点也能收。
  峰值仍是 `0.07a`（钳 4–30 px，a=234.5 时约 16 px/侧，随脸大小自适应）；`lift` 为 0.85 峰值。
  轮廓外由 `slim_support_gate`（`max(3×实际峰值, 24 px)` 的径向带）平滑衰减并**直接乘进位移**，
  因此带边连续、带外背景不动；相邻像素跳变实测 <1.2 px（`tests/test_beauty_shape.py`）。
  CPU 复算（1080p 参考帧）：`min det(J)=0.509`、`max|∂offset|/∂p=0.491`，无折叠、无陡坡；网格映射单射测试锁住。
  大眼仍独立使用原来的椭圆 `warp_gate`（在轮廓处归零），不继承瘦脸的外侧过渡；SLIM 不再乘 eye gate。
- **回退**：`u_shape_ready=0`（无 5 点、点数≠5、脸退化）时**整段走 M5 原逻辑**
  （框蒙版 + YCbCr + 10 % 保底 + 框形 warp gate），与 M5.2 之前的 shader 输出
  **逐字节相同**（GLSL 实测 mean=0.000 / max=0，见 `.cache/qc/m51/README.md` §7）。
  检测器刚启动、夜间丢点等场景因此不会跳变。
- **成本**：Lab 只在 `mask > 0.004` 且椭圆内（约 8–13 % 像素）算，`pow` ×3/px 摊到全帧
  可忽略；纹理实际上限 `texture_max_dimension=1280`（≈0.92 Mpx）。板端待实测
  `beauty_ms`（桌面 llvmpipe 无参考价值）。
- **验证方式**：桌面没有板端帧源，因此在 `.cache/qc/m51/` 用「同一张板端真帧 + numpy
  逐行移植（`beauty_cpu.py`）」跑三档对照，再用 `glsl_check.py` 把**真实 shader**
  通过 `glshader`（Mesa llvmpipe / EGL）渲染同一帧，与 numpy 原型逐像素比：
  整帧 mean 0.071/255、p99=2、脸外 0.0106/255 —— 移植无偏差（见 README §7）。

### 2.7 播放器能力（M6：续播 / 外挂字幕 / 音轨）

美颜之外，M6 把「播放器该有的东西」补齐了。三者都刻意避开视频链路，因此对 30 fps 预算零影响：

- **断点续播**：`resume.ResumeStore` 以「路径 + 文件大小 + mtime」为键存 XDG state
  （`~/.local/state/video-player-demo/resume.json`，原子写、坏文件静默降级）。位置在
  `PlaybackBackend.open(start_position_ns=…)` 里登记，等 `STATE_CHANGED → PLAYING`
  （preroll 完成）再 seek——preroll 之前的 seek 会被 GStreamer 丢掉，表现为「每次
  都从头播」。开头 10 s、结尾 15 s 的位置不写盘，避免「打开两秒就关」和「看完又跳回
  片尾」。UI 侧每 2 s 落一次盘，切文件/关窗前各落一次，EOS 时清除。
- **外挂字幕**：`subtitle.py` 解析 SRT（容 BOM、CRLF、点号毫秒、缺序号、HTML 标签），
  `SubtitleTrack.text_at()` 用二分查找。渲染不走 `subtitleoverlay`/`textoverlay`：
  它们要把文字烧进视频帧，而本项目的 sink 是 GTK 内嵌的 `gtk4paintablesink`，没有
  可协商的窗口句柄；改成画面之上的 `Gtk.Label` 图层后，视频链路一个字节都不用改，
  样式也回到 CSS。代价是 `--record` 录不到字幕（演示片的字幕本来就在后期叠加）。
- **音轨切换**：修掉一个真 bug——旧实现给每个 audio pad 建一条完整解码分支，双音轨
  素材会把两种语言**混在一起播**。现在只建一条分支、只 link 选中的那条 pad；切换时
  先设 `qtdemux current-audio`，再重连 pad 并 seek 回当前位置（切轨会重置音频解码器，
  不 seek 的话新音轨从 00:00 开始）。容器不支持 `current-audio` 时 UI 置灰。

### 2.8 UI v3（2026-11-26，按 `mockups/ui-v3` 全量改造）

只动 UI 层与文案，机芯（backend/beauty/recording/pipeline）和录制编排不变。设计稿
的五张板端对照图在 `docs/evidence/ui-v3-0*.png`。

- **空态去控件化**：无媒体时整条传输控制栏与信息行隐藏，视频区变成虚线拖放区
  （主 CTA「选择文件」、次 CTA「选择文件夹」、拖放提示、能力 chip）。文件/文件夹
  也可直接拖到画面上，`Gtk.DropTarget(Gdk.FileList)` → `expand_media_args()`。
- **信息行带标签**：`分辨率 / 编码 / 解码 / 帧率` 静态值在左，`● 人脸 / 美颜` 实时
  值在右；编码经 `ui/chrome.py::normalise_codec()` 把 caps 的 `X-H264/X-H265`
  归一成 `H.264/H.265`。header 副标题只显示品牌或 `播放列表 n/N`，不再重复编码。
- **44 px 两行控制条**：进度行（已播 / 滑杆 / 总时长）+ 按钮行，图标键 44×44、
  播放键 44 白圆；白色只留给播放键，其他主按钮改钢蓝。
- **侧栏一级美颜面板**：顶部常驻「美颜 -> 磨皮/美白 -> A|B 分屏对比 -> 更多设置」，
  不再藏到底部 Expander；最近播放高度压到 128 px，保证 1280×768 的窗口内容区放得下
  播放列表、添加文件行和快捷键提示。
- **全屏 dock**：不透明 `rgba(16,19,22,0.96)` + 顶部亮边 + 投影；只装控制条，新增
  「美颜 / 对比」pill 与侧栏开关双向同步。四角 HUD 卡片被移除。
- **全屏常驻遥测**：左上角单行 `1920×1080 · H.264 · 硬解 · 30.0 fps ● 人脸 1 · 39 ms
  · 美颜 开 · 1.5 ms`；dock 淡出后降到 62% 不透明度继续显示，屏幕底边 2 px 进度
  发丝线常驻。2 px 线用 `DrawingArea` 自绘，避开 `Gtk.ProgressBar` 在 GTK 4.14/4.18
  上的主题最小宽度告警。
- **分屏可读性**：`ui/chrome.py::contain_rect()` 把分屏标签放到真实视频矩形内；
  PangoCairo 画「原图 / 美颜」胶囊与参数行，不碰 shader 分割线。
- **人脸框轻量化**：整框 + 5 点改成四角 L 形（臂长 22、2 px、钢蓝）+ 置信度标签；
  关键点数据仍保留给美颜 warp。

### 2.9 ui-v5 浏览壳（2026-11-26，P2：左导航 + 片库 / 空态）

P1 把内嵌样式表拆到 `ui/style.py` 并换成琥珀 token 之后，P2 把单页播放器改成
「浏览页 + 播放页」两层。播放页的控件树、录制编排、全屏 dock 与 v4 完全一致；
新增的是外壳与片库数据。

- **页面栈**：`PlayerWindow` 根节点从 `Gtk.Paned` 换成 `Gtk.Stack`，`browse` /
  `player` 两页一次建好、只切可见性（`PAGE_NAMES`、`_show_page()`、
  `_sync_page_chrome()`、`_on_library_changed()`）。进入 browse 一律走
  `_return_to_playlist()`：停止录制、写续播与历史、关管线、清错误卡，所以
  「返回片库」不会留下半截状态。
- **启动落点**：无媒体 → browse；带媒体参数 / `--demo` / `--record` → player
  （录制画布按播放页控件分配合成）。`--check` 不建窗口。
- **左导航**：88 px，首页 / 片库 / 最近 / 设置 + 底部状态点与时钟（设置曾在
  P1–P3 置灰，2026-11-27 起是真页面，见 §2.12）。
  图标是 21 px `DrawingArea` 手绘线稿，绕开板端精简 icon theme 会画空按钮的问题。
  「最近」在 P2/P3 里只是片库的 `recent` 筛选；2026-11-27 起改成独立页（见 §2.13）。
  顶部的「面」品牌块（42×42）于 2026-11-27 按用户决定删除，导航随之上移（§2.14）。
- **片库数据层**（`library.py`，无 GTK / GStreamer 依赖）：
  `config.library.directories` 与 XDG state 里用户「选择目录」的结果取并集 →
  单层扫描（与 `expand_media_args` 一致）→ `LibraryItem`。元数据缓存以
  「解析后路径 + size + mtime」为键，文件变了自动失效；筛选 `all / finished`
  （`recent` 于 2026-11-27 移入独立页）、排序 `name / duration / added`、chip 计数都是纯函数。
- **元数据与缩略图**（`probe.py`）：`GstPbutils.Discoverer` 的同步接口在**工作
  线程**里串行跑（`discover_uri_async()` 在本栈 PyGObject 上不派发回调，见
  PITFALLS），`playbin ! appsink` 在 PAUSED 后 seek 到 10% 拉一帧，按
  `GstVideo.VideoInfo.stride` 拷 RGBA 存 PNG。缩略图缓存在
  `$XDG_CACHE_HOME/video-player-demo/thumbs/`，键同元数据；探测失败的文件记入
  会话内集合，避免每次进页重复付 8 s 超时。两件事都只在 browse 页可见时运行，
  进 player 页立即 `suspend()`，VPU 归播放。
- **片库页**（`ui/library_page.py`）：页头（片库 / 项数 / 目录数 / 排序菜单 /
  重扫）、两个筛选 chip（全部 / 已看完；「最近观看」2026-11-27 起是独立页）、4 列 × 273 px 固定宽卡片
  网格（273×4 + 16×3 = 1140，与内容宽精确对齐）、页脚扫描摘要。卡片是 Cairo
  自绘：圆角裁切、封面居中裁剪、时长胶囊、已看完徽标、进度发丝线；GTK4 不能把
  子控件内容裁到 CSS 圆角，所以不用 `Gtk.Picture`。无海报时画同尺寸占位板，
  探测完成后原地替换。
- **空态**：取景框 + 标题/说明 + 三个动作（选择目录 / 打开文件 / 重新扫描）+
  两条状态栏（扫描范围含目录 chip 与移除、支持格式）；无目录时扫描范围栏给提示。
- **拖放**：整页 `Gtk.DropTarget(Gdk.FileList)`；文件夹并入片库目录并重扫，
  视频文件按落点顺序成为播放列表并直接进播放页（`accept_drop()` 是公开规则，
  单测与探针可直接调用）。
- **交互**：`Escape`（非全屏）回片库；header 返回箭头回片库；状态卡「返回列表」
  现在回片库；全屏 / 录制 / 字幕 / 音轨快捷键不变。
- **录制**：画布图层 = header（2026-11-27 起）/ sidebar / info / controls / hint /
  playback-status + 视频，不包含 rail；`--record` 仍从 player 页开始。成片色彩标签
  保持 `yuvj420p / pc / gbr`（见 `docs/PITFALLS.md` §3）。
- **布局实测**（本机 GTK 4.14.5，窗口 1280×768 → 内容 1280×709）：rail 87、
  页面 1140×687、卡片 273×173、12 张卡三行、`vadjustment.upper == page_size`
  （无需滚动），页脚与全部卡片完整可见。板端 GTK 4.18 实测 headerbar **58 px**、
  内容 1280×709，与 4.14 一致。

### 2.10 ui-v5 首页 + 详情（2026-11-26，P3）

P3 在 P2 的浏览壳里加两个页面，并把「点卡片」的落点从播放页改成详情页；播放页
（`Gtk.Paned`、侧栏两页、全屏 dock、录制编排）完全没动。

- **浏览壳结构**：`ui/browse.py::BrowseShell` = `Rail(88 px)` + `Gtk.Stack(home / library /
  recent / detail / settings)`。左导航从 `library_page.py` 抽到 `ui/rail.py`（首页 / 片库 /
  最近 / 设置）；「最近」在 P3 时仍是片库的筛选，2026-11-27 改成独立页（§2.13）。
  外层 `PlayerWindow` 仍是 `Gtk.Stack(browse / player)`。
- **导航与返回**：启动无媒体落 home；卡片→详情→播放，播放页返回箭头 / `Esc` / 状态卡
  「返回列表」都回**打开播放页时所在的那一页**（`BrowseShell.mark_player_origin()` /
  `activate()`）；详情页自己的返回圆钮与 `Esc` 回来源页（home / library / recent）。
  在 browse 页上除 `Esc` 外的播放/功能快捷键不再响应（P2 时空格会弹打开文件对话框）。
- **播放列表语义**：`PlayerWindow._play_paths(paths, index, start_position_ns=None)` 用
  「用户眼前那份列表」装 playlist——详情页=目录选集顺序、首页继续观看=4 卡顺序、
  拖入文件=文件顺序。上一项/下一项/EOS 自动下一集都在这份列表内。
- **首页**（`ui/home_page.py`）：hero（1140 px 大图 + 左/底/顶三层 scrim + kicker/标题/
  时长·分辨率·编码·体积/路径/CTA）+「继续观看」4 卡 +「最近添加」4 卡；hero 是**弹性带**
  （`vexpand`，最小 170 px），把行高/字体的变化吃掉，不把 1280×768 窗口撑大。
  主 CTA 位置 ≥1 s 且未看完才写「继续观看 mm:ss」（`browse.RESUME_MIN_NS`）；空片库时整页
  换成取景框空态（选择目录 / 打开文件 / 前往片库），不是「空 hero」。「继续观看 › 全部」
  落到最近页（§2.13），「最近添加 › 全部」仍进片库。
- **详情页**（`ui/detail_page.py`）：268 px 背景板（返回圆钮 + chips + 标题/副题 + 路径 +
  续播/播放/重播 CTA）+ 左栏 3 列选集（当前集琥珀描边 + `正在播放` + 进度线 + 自动滚到当前行）
  + 右栏「文件参数」（编码/画面/帧率/时长/大小/码率/解码，7 行真值）与「同目录」3 行。
  「选集」= 同一目录下的文件（单层扫描下目录是唯一剧集单位），不造集号/季/评分/演员表；
  视觉稿的 synopsis 位置放真实文件路径。
- **纯数据层**（`browse.py`，无 GTK）：`home_view()`（继续观看只认片库内文件、hero 回退到
  最新文件）、`detail_view()`（选集/参数/续播）、`episode_items()`、`bitrate_text()`
  （体积÷时长）、`remaining_text()`（向上取整，避免「剩 00:00」+ 满进度条）、
  `hardware_decodable()`（VPU 只认 h264/h265 系列，VP9 诚实显示软解）。
- **Cairo 共用件**（`ui/paint.py`）：`Poster`（圆角裁切/封面裁剪/时长胶囊/徽标/进度线/
  当前集描边）、`GhostFrame`（空态取景框）、`BackdropArt(kind)`（hero 与详情共用封面裁剪 +
  各自 scrim 配方，16 px 圆角）。GTK4 不裁子控件内容到 CSS 圆角，所以海报仍是 `DrawingArea`。
- **大图缓存**（`ui/browse.py::BackdropCache`）：卡片海报 546 px，hero/详情背景板要 1140 px，
  不能让 546 放大顶上。按 `size+mtime+width` 单独缓存 1140 px 帧，`ThumbnailWorker` 在
  **工作线程**解码、同一时刻只排一个（VPU 一次一个解码）；进入播放页 `suspend()` 时取消。
- **布局实测**（本机 GTK 4.14.5，窗口 1280×768，内容 709 px）：home 697 px 内容
  （`6 + hero 225 + 10 + 228 + 10 + 224 + 6`）、窗口**没有**被撑大；详情 268+12+414 一屏；
  `--record` 画布不含 browse 壳（录制只在播放页开始）。
  板端 GTK 4.18 实测 headerbar 58 px、内容 1280×709，首页 / 详情 / 片库 / 空态四张
  1280×768 截图与 54 项断言全过；详情参数行的「VPU 硬解」与 hero 续播均为真值。

### 2.11 ui-v5 播放页重做（2026-11-27，P4）

P4 把播放页从「GTK header + 侧栏 + 信息行 + 控制条」改成视觉稿的**画面优先**结构。
浏览壳、播放后端、美颜管线、录制分支都没有改协议，只换了播放页的控件树和展示。

- **页面结构**（`window.py::_build_player_area`）：播放页不再显示 `Gtk.HeaderBar`，
  窗口根是 `Gtk.Overlay`（`play-stage`，底色 `@video` 纯黑），主 child 是
  `Gtk.AspectFrame(16:9, obey_child=False)` 包住的视频 overlay。1280×768 时视频区
  实测 **1280×720 @ y=24**；上下黑边也属于 stage，所以换窗口尺寸仍居中。
  顶部/底部 scrim（`play-veil-top/bottom`）是 `can_target=False` 的渐变层；顶栏、
  底部 dock、右侧抽屉、toast 都是 stage 的 overlay 子节点。
- **顶栏**（`ui/player_chrome.py::PlayerTopBar`）：44 px 返回圆钮 + 标题（`stem`，
  不带 `.mp4`）+ 副题（`播放列表 i/n`）+「选集」chip + 44 px 更多圆钮。
  「选集」是 `Gtk.MenuButton`，popover 里是当前 playlist、最近播放（已排除 playlist
  重复项）和「+ 添加文件」；行激活后关闭 popover 并切集。「更多」是打开文件 /
  打开文件夹 / 切换语言 / 录制·停止 / 关闭窗口 + 快捷键说明。
- **遥测行**（`PlayerChrome.TelemetryBar`）：`● 硬解/软解 | 编码 | 分辨率 | fps |
  人脸 | 美颜 | CPU`。CPU 是 `resource.getrusage()` 的**进程 CPU（全部线程）**，
  每秒采样；硬解状态点用 `ok`，软解点降为 `line_3`。窗口态和全屏态共用同一行，
  全屏 dock 淡出后由左上角的 `fullscreen-telemetry` 常驻行接管。
- **时间轴**（`PlayerChrome.ChapterTimeline`）：自绘 20 px 命中带、4 px 轨道、
  12 px 琥珀/暖白旋钮；实现 `Gtk.Scale` 的 `set_value/get_value` 0–1000 子集和
  `value-changed` 信号，所以 v4 的 seek 代码不用改。点击/拖动轨道会 seek。
  章节刻度来自**真实数据**：GStreamer 在当前栈上的 `qtdemux` 不实现 `TocSetter`，
  演示素材也没有章节，所以时间轴读取视频旁的
  `<clip>.chapters.json`（`{"chapters":[{"start":15,"title":"…"}]}`，也接受裸列表），
  没有 sidecar 就不画刻度，绝不按百分比编造刻度；`chapter_marks()` 只投影
  0 < fraction < 1 的起点。P4 本机 QC 素材带 3 个章节，截图刻度为 25/50/75%。
- **44 px 控制条**：上一项 / −15 s / 52 px 播放 / +15 s / 下一项 / 停止 /
  倍速（0.5–2.0×）/ 解码三段 / 音量 / 字幕 / 美颜 pill / 全屏。全屏时额外出现
  禁用态「对比 · 需美颜」pill（v4 的全屏 dock 入口保留）。美颜 pill 现在**打开
  右侧抽屉**，其 checked/label 仍镜像 `beauty.enabled`；B 键仍直接开关美颜。
- **右侧美颜抽屉**（`Gtk.Revealer(SLIDE_LEFT)` + 352 px panel）：固定标题/总开关/
  NPU 人脸计数，下面 `Gtk.ScrolledWindow` 承载磨皮/美白、A|B、分屏位置、人脸框、
  更多设置（瘦脸/大眼）和说明。抽屉滚轮仍由 `_install_page_wheel_scroll()` 捕获，
  避免滚轮改数值（PITFALLS §5）。关闭美颜的级联与 v4 完全相同：A|B、人脸框、
  效果一起关，重开不自动恢复；抽屉只换容器，不改状态机。
- **全屏 dock**：窗口态底部 dock 与全屏态是同一个控件，不再在两种布局间搬运；
  `_enter_immersive()` 只隐藏顶栏、显示 compare pill、显示常驻遥测和底部 2 px
  进度线；顶/底 scrim 随 HUD 出现/隐藏；2.5 s 静止后 `_dock_revealer` 淡出，
  左上遥测保留 62%，鼠标/键盘/点按唤回。暂停、抽屉打开时 dock 不淡出。
  `--record` 仍停留在窗口态（画布按控件分配合成）。
- **录制编排**：`_recording_layer_specs()` 从 v4 的 header/sidebar/info/controls 改为
  **top / dock / drawer / hint / playback-status** 五层，视频 hole 仍取
  16:9 视频 overlay；画布底色改为 `@video`（纯黑）以匹配播放页的上下黑边。
  `_on_record_tick()` 每秒刷新 top/dock，抽屉打开时额外每 3 s 刷一次；录制中
  `CanvasBuilder` 的 `video_area` 实测 `Rect(0,24,1280,720)`。
- **兼容与返回**：browse 壳不变；播放页顶栏返回/`Esc` 回打开时的 browse 页面，
  浏览页重新显示 headerbar。启动无媒体时仍落 browse home。

**本机验证**（2026-11-27，GTK 4.14.5 / GStreamer 1.24.2 / Xvfb :13 1280×800）：
真窗口 1280×768（无 WM 下用 1290×778 默认尺寸抵消 CSD 阴影）、106 项交互断言全过；
截图见 `.cache/qc/ui-v5-p4/out/`，录制画布合成见 `.cache/qc/ui-v5-p4/recording-composed.png`。
本机没有 `gtk4paintablesink`/GL/NPU，探针里的视频帧是从同一素材抽出的真实帧注入
`Gtk.Picture`，美颜可用性与 GPU ms 只在截图阶段按板端数据展示，**不作能力验收**。

**板端验证**（2026-11-27 09:19，用户当次授权；GTK 4.18.6 / GStreamer 1.26.2 / GNOME Wayland）：
5 文件原子替换、`--check --require-board` 17 ok、CSS 34,598 B / 0 parsing errors；
真实 `mppvideodec` + `gtk4paintablesink` 下 8 个 mode / **116 项断言全过**——窗口 1280×768、
视频 `(0,24,1280,720)`、全屏 1280×800 `(0,40,1280,720)`、顶栏 46、dock 96、播放键 52、
控制键 44、时间轴 20、遥测 `硬解/H.264/1920×1080/30.0 fps`、章节 25/50/75、抽屉 318×546、
抽屉开美颜 GPU 2.2–3.0 ms/帧（NPU 2 张脸、fps 30.8）；30 s 录制成片 1280×768@30 /
902 帧 / 30.07 s。全过程 uptime 连续、无重启。
**仍未验证**：触摸点按；popover 在 Wayland 是独立 subsurface，整窗快照与无焦点探针都拿不到
（surface 1×1），只有内容/行为断言、没有 popover 截图；H.265/VP9/AV1、4K、网络流未在 P4 复测。
证据：`.cache/qc/ui-v5-p4-board/`（内部）。

**P5 修订（2026-11-27，本机完成并已板端验收）**——真实使用推翻了 P4 的两条取舍：

- **窗口态不再隐藏 headerbar**：`_sync_page_chrome()` 只在「播放页 + 非全屏 + 非
  `--record`」时显示 `Gtk.HeaderBar`（片段名 + 窗口按钮），画面顶栏进入 *compact*：
  隐藏标题/副题两个 label、保留 titles box 作占位，所以「选集/更多」仍在右侧。
  全屏隐藏 headerbar 并恢复完整标题；`--record` 的画布与 P4 逐项一致。
  1280×768 时：47 px 标题栏 + 721 px 画面（16:9 → 1280×720）。
- **「选集」从 popover 改为占布局的右侧栏**：`Gtk.Revealer(SLIDE_LEFT)` 包
  360 px `sidebar-panel`（标题/计数/✕、播放列表、+ 添加文件、最近播放；`Esc`、
  再点选集、✕ 均可关闭）。窗口态默认打开，视频缩到 **920×517**；打开时控制条
  切两行（第一行 = 上一项/−15/播放/+15/下一项/停止/倍速/解码/音量；第二行 =
  字幕/美颜/全屏），关闭后恢复单行 1280 布局。全屏与 `--record` 收起栏目但保留
  用户选择；与美颜抽屉互斥（打开美颜先收起栏目，反之亦然）。
- **headerbar 左上常驻「＋ 打开」菜单**（打开视频 / 选择文件夹，复用
  `_on_open_clicked` / `_on_open_folder_clicked`）；片库/首页空态 CTA 保留，片库
  非空后仍有全局入口。播放页「更多」里的同名入口不重复删除。
- **画面手势**：单击 = 播放/暂停（300 ms 防抖）、双击 = 全屏/退出；分组在
  `ui/taps.py`（纯 Python，`TapTracker`），不依赖 `Gtk.GestureClick` 的 `n_press`
  ——触摸序列的 `n_press` 由 GTK/服务器的双击时间决定，不是产品契约。
- **点外部关闭美颜抽屉**：stage 上 CAPTURE 阶段 `Gtk.GestureClick`，命中抽屉 panel
  之外就关闭并 `set_state(CLAIMED)`，这一次点击不再穿透到暂停或控制条。

本机验证（GTK 4.14.5 / X11 / 1920×1080 屏）：pytest **592 passed / 15 skipped**；
`.cache/qc/fix58/shoot.py` **21/0**（4 张 1280×768 + 全屏 1920×1080 截图）；
`.cache/qc/fix58/interactions.py` 五个场景 **39/0**（真实 xdotool 单击/双击/点外部）。
**板端验收（2026-11-27 15:04，GTK 4.18.6 / Wayland / DSI 1280×800）**：4 文件原子替换、
`--check --require-board` 17 ok、CSS 38,332 B / 0 errors；真实 `mppvideodec` 下
layout **15/0**、fullscreen **8/0**、interact **9/0**——窗口 1280×768、视频 **920×517**、
控制条 row1 830 + row2 177 ≤ dock 872；真全屏 **1280×800**、视频 **1280×720**；
截图 1280×768 / 1280×800。**仍未验证**：真实手指触摸双击/点外部、30 s `--record` 成片、
H.265/VP9/AV1/4K。证据 `.cache/qc/fix58-board/`。

### 2.12 ui-v5 设置页（2026-11-27，已上板验收）

视觉稿的第四项「设置」当时没有出稿，P1–P3 里一直置灰（`rail.py` 里 `set_sensitive(False)`、
不接信号），悬停提示「设置页未出稿」。2026-11-27 产品决定补一个**最小设置页**，范围固定为四块：

1. **界面语言**：中文 / 英文两段，切换走 `PlayerWindow._set_language()`（写 XDG config、
   `_apply_language()` 全量重译）。2026-11-27 起设置页是**浏览页唯一入口**：header 的语言按钮已删除
   （板端 18/0：titlebar 只剩标题与窗口按钮，无 `English` / `中文` 标签）；播放页「更多」里的语言项
   保留（播放时 header 隐藏，否则只能退出播放才能改语言）。
2. **解码方式**：自动 / 硬解 / 软解，读 `config.player.decode_mode`。在播放页切换时沿用
   `_apply_decode_mode()`（重建管线、保住位置）；在浏览页切换只改配置（`_set_decode_mode()`
   判断不在播放页就不碰管线），下次打开生效。**该值不落盘**，启动值仍来自配置文件。
3. **扫描目录**：`config.library.directories` + `LibraryStore` 里用户选的目录，合并去重
   （`settings.folder_rows()`）；配置里的目录只读（标「配置」），用户目录可移除；
   选择/移除/重扫都委托 `LibraryPage`，页面不另存一份状态。
4. **关于**：只读事实——版本、平台、内核、GTK、GStreamer、解码后端（`mppvideodec` 是否存在）、
   状态文件与缓存目录（`settings.about_rows()` 给行，UI 只排版）。

**结构**：`ui/settings_page.py::SettingsPage` = `Gtk.ScrolledWindow` + 四张 `.card-box` 卡片；
页面不持有业务状态，语言/解码通过回调回到 `PlayerWindow`，目录通过 `LibraryPage` 的公共方法
（`choose_directory()` / `rescan()` / `remove_directory()`）。纯数据在 `video_player/settings.py`
（不 import `gi`），所以规则能在 `.venv` 下跑单测。

**导航**：`BROWSE_PAGES` 增加 `settings`；rail 第四项恢复可点，`_sync_rail()` 负责高亮，
`headline()` 返回（`设置`，`界面 · 播放 · 片库 · 关于`），`Esc` 回首页（这一页没有返回箭头）。
从播放页返回时 `mark_player_origin()` 仍能记住 settings。

**本机验证**（2026-11-27，GTK 4.14.5 / Xvfb :11 / 真实 `PlayerWindow`，空片库）：
真窗口 1280×768，探针 **33/0** —— rail 可点、页面/标题/四卡、语言中英往返并落盘、
解码段与播放页控制条双向同步、目录增删（临时目录）、关于 8 行、`Esc` 回首页；
截图 `.cache/qc/ui-v5-settings/out/ui-v5-settings-01-zh.png` / `-02-en.png`。
pytest **561 passed / 15 skipped**（新增 `test_settings_model.py` 8 项、`test_settings_page.py` 13 项）。

**板端验证**（2026-11-27 10:02，用户当次授权；GTK 4.18.6 / GStreamer 1.26.2 / Wayland）：
8 文件原子替换、`--check --require-board` 17 ok、CSS 35,892 B / 0 parsing errors；真实窗口
**35 项断言全过**——窗口 1280×768、内容区 1192×711、语言分段容器 120×36、语言中英往返并落盘、
设置页与播放页控制条的解码段双向同步、目录增删、`Esc` 回首页；关于卡读到真实板端值
（`Linux aarch64` / `6.1.118-rt36` / `4.18.6` / `GStreamer 1.26.2` / `VPU 硬解可用`）。
部署后回归 P4 播放页 `play` mode **33/0**；常驻实例 PID 1564280。证据：`.cache/qc/ui-v5-settings-board/`（内部）。

**已知限制**：触摸点按、目录对话框的真实点选未验证；解码方式不跨启动持久化（改配置文件才跨启动）；
设置页没有「恢复默认」或机型字段——演示不需要，不编数据。

### 2.13 ui-v5 最近观看页（2026-11-27，方向 C：独立续播页）

**背景**：视觉稿（`mockups/ui-v5`，README §4）把「最近」定成片库的 `recent` 筛选，并让左导航
跟着筛选高亮。实现照做后暴露出三处不一致：点「最近」进去 H1 仍是「片库」、页头计数仍是全库
12 项、排序仍是「最近添加」（按文件 mtime），而 rail 会被 chip 反控。2026-11-27 决定把它做成
独立页（备选的方向 A「删入口」/ B「续播模式」未采用，效果图见 `mockups/ui-v5-directions/`）。

**结构**：`ui/recent_page.py::RecentPage` = 页头（`最近观看` / 项数 / `继续观看 N · 已看完 M` /
`清除记录`）+ `Gtk.ScrolledWindow` 里的两组卡片网格 + 空态。组头是
`标题 + 计数 + 1 px 分隔线`（`.recent-group-*`、`.recent-rule`），卡片直接复用
`library_page.LibraryCard`（273 px，4 列，与片库同一套海报/进度/徽标）。
页面**不启 worker**：海报走 `LibraryPage.poster_for()`，元数据走 `item_for()`，两张表都来自
片库页已经跑完的探测；缩略图后到时由 `BrowseShell._on_poster()` 转进 `on_poster()` 原地换图。

**数据**：`browse.recent_view(items, recents)`（无 GTK，单测覆盖）把 `RecentsStore` 与扫描结果
做**交集**——和首页「继续观看」同一条规则，只列还扫得到的文件（文件夹外的单文件没有海报与
同目录选集，详情页无从渲染，仍留在播放页的「最近播放」列表里）。组内按
`RecentItem.updated_at` 倒序，`progress ≥ FINISHED_THRESHOLD`（95%）进「已看完」，其余进
「继续观看」。`HomeCard` 因此多了 `updated_at` 字段，首页与最近页共用同一个卡片模型。

**交互**：
- 「继续观看」卡片点按**直接续播**（playlist = 该组卡片顺序，`_play_paths` 走已有续播路径），
  与首页「继续观看」一致；「已看完」卡片进详情页，用详情页的「重新播放」。
- 页头「清除记录」走 `Gtk.AlertDialog` 确认（默认按钮是「取消」），确认后
  `RecentsStore.clear()`，再经 `BrowseShell._on_history_cleared()` 调
  `LibraryPage.refresh_history()` 把片库卡片的进度条与「已看完」计数一起归零。
  文案只说清空记录列表，不承诺清 `ResumeStore`（那是 `player.resume` 的另一份状态）。
- 空态 = 取景框 + 「还没有播放记录」+ 一句说明 + 「去片库」，清除按钮置灰。
- 卡片 meta 用 `chrome.format_watch_time()`：`刚刚观看` / `今天 HH:MM` / `昨天 HH:MM` /
  `M 月 D 日` / `YYYY 年 M 月 D 日`（英文 `Just now` / `Today HH:MM` / `M/D` / `Y/M/D`），
  可续播时追加 `剩 mm:ss`。

**导航**：`BROWSE_PAGES` 增加 `recent`；`_on_nav("recent")` 直接切页并刷新，不再经过
`library.select_filter()`；`_sync_rail()` **只跟页面**（详情页跟 `_detail_origin`），
chip 切换不再移动 rail 高亮；首页「继续观看 › 全部」落到最近页，`Esc` 回首页（同设置页）。

**片库同步**：`FILTERS` 从 `all / recent / finished` 收成 `all / finished`，`filter_items()` /
`library_counts()` 去掉 `recent_paths`；`LibraryPage` 去掉 `on_filter_change` 回调（原先只为
同步 rail 而存在），新增 `item_for()` 与 `refresh_history()`。

**文案**：新增 `recent_*` 17 条（标题/副题/组名/清除确认/空态/时间桶），删除已无入口的
`library_filter_recent`；rail 的「最近」提示改用 `recent_title`。

**验证**（2026-11-27，本机）：
- `pytest` **573 passed / 15 skipped**（新增 `tests/test_recent.py` 10 项；`test_library` 的
  筛选契约同步收窄）。
- 真窗口探针 `.cache/qc/ui-v5-recent/shoot_recent.py`（GTK 4.14.5 / Xvfb :10.0 / 真实
  `PlayerWindow` / 12 个 VP9 文件 + seed 的 `recent.json`）：**0 失败**，断言
  page/rail/两组计数/页头副题/卡片控件数/空态/清除后进度归零/`Esc` 回首页；
  截图 `out/recent.png`、`out/recent-en.png`、`out/recent-empty.png`（1280×768）。
- **板端验收**（2026-11-27 11:00，用户当次授权「部署」；GTK 4.18.6 / GStreamer 1.26.2 /
  Wayland / 真实 `mppvideodec`）：11 文件原子替换、`sha256sum -c` 11/11、`--check --require-board`
  **17 ok / 0 warn / 0 fail**、CSS **36,593 B / 0 parse errors**；真窗口 **33/0**——rail 信号切页/
  高亮、两组与计数、273×170 卡、英文标签、片库两 chip、空态、点卡经 `mppvideodec` 起播
  （playlist = 继续观看组）、`Esc` 回最近页；2 张 1280×768 截图。部署后 P4 播放页回归 **33/0**。
  证据 `.cache/qc/ui-v5-recent-board/`。
- **未验证**：触摸点按、清除确认对话框的人工点选。

**已知限制**：只列扫描目录里的文件（与首页一致，见上）；不造「今天/昨天」时间分组标题
（组维度是观看状态，时间是卡片 meta）；清除记录不清 `ResumeStore`（文案已如实说明）。

### 2.14 ui-v6 方案 C「编辑台」落地（2026-11-27；本机完成，17:55 已板端部署）

**决定**：`mockups/ui-v6` 三方向中用户选定 **C · 编辑台**（冷中性 + 骨白操作色 + 琥珀只做
数据），并要求删除左导航顶部 42×42 的「面」标志（连底板）。设计源是
`mockups/ui-v6/{README.md,base.css,skin-c.css,app.html,c-*.png}`；落地只改视觉层，页面结构、
控件位置与点击区域、播放列 920 + 抽屉 360、视频 920×517 均不动（§1 冻结几何）。

**配色拆分（`ui/style.py`）**：ui-v5 把强调色全给琥珀；C 拆成两个语义：
- **骨白 = 操作**（`play` #F1EDE4 / `play_hi` / `play_active`）：主 CTA、开关 ON、
  当前导航项/页签、播放键、滑块手柄、焦点环。
- **琥珀 = 数据**（`accent` #D9A85C / `accent_hi`）：进度、计数、实时遥测、滑杆填充、
  时间轴播放段、卡片进度线。
- 冷中性表面：页底 `#0B0D0F`、导航底 `#090B0D`、面板 `#111417`、抬起面 `#171B1F`、
  发丝线 `#20252A`、播放页底 `#050607`、抽屉 `#0E1114`、设置卡 `#0C0F11`；卡名
  13 px / 元信息等宽 11 px `#828B93`、时长淡底等宽、圆角 10（Hero·抽屉）/ 8（海报·
  导航项）/ 9（按钮·Chip）。`annotation` 仍只给画面标注，widget 规则不引用
  （`tests/test_theme.py` 守住）。
- `tests/test_theme.py` 从 ui-v5 契约改成 ui-v6：冻结 9 个语义 token（含骨白），
  分别断言骨白到 8 个操作控件、琥珀到 7 个数据读数，并要求 GTK `parsing-error`
  信号 **0 条**（旧版只捕获 `load_from_data` 异常，未知属性不会失败）。

**删除「面」标志**：`ui/rail.py` 删 `mark_box` / `_mark`、`apply_language` 不再取
`rail_mark`；`i18n.py` 删该 key；`style.py` 删 `label.rail-mark`。rail 保持
`padding: 14px 0 12px 0`、nav 保持 `margin-top: 24px`，导航第一项落在 rail 顶下
**38 px**；相对旧构建实测上移 **44 px**（旧 mark 盒是 42 px 高 + 2 px margin，
设计稿的「42 px」只算底板）。当前项不再用整块琥珀底：`_ActiveBar` 是 2×40 px 骨白
子控件（GTK CSS 无 `::before`），图标与文字转骨白；录制画布不含 rail，无需另删。

**GTK 落地要点**：
- 40 px 短 Tab 下划线：`player_chrome.DrawerTabRule` 是 2 px `DrawingArea`，同层画
  1 px 发丝线 + 40 px 骨白下划线，挂在大纲「选集」列与美颜抽屉的标题行下；
  产品没有 mockup 的「选集 / 美颜」双 Tab（两者是独立面板），所以标记落在当前
  面板标题下，而不是新增一排 Tab。
- 时间轴：3 px 轨道 + `10×15` 竖条骨白滑块（`ChapterTimeline`），章节刻度 1×10。
- 海报：`paint.py` 圆角 8、50 px 单段渐变 scrim、淡底等宽时长、2 px 琥珀进度；
  Hero/详情背景板圆角 10，scrim 换成 C 的左右渐隐 + 底部压暗配方。
- 开关 44×24 / 5 px 圆角 / 骨白 ON + 墨色 knob；播放键 52 px 骨白 + 1 px 高光边；
  解码段选中为 12% 骨白 + 1 px inset 环；Chip 9 px 圆角；焦点环骨白。
- 不新增字体；卡名用 400 字重（板端 Noto Sans CJK 无 450/500，会回退 400）；
  不使用 `backdrop-filter`（效果稿也未用，玻璃感 = alpha 填充 + 1 px 边）。

**验证（本机 GTK 4.14.5 / X11 :10.0 / 真窗口 1280×768）**：
- `PYTHONPATH="$HOME/.local/lib/python3.10/site-packages" python3 -m pytest tests -q`
  → **598 passed / 15 skipped / 0 failed**（基线 592 + 15；新增 `test_theme.py` 6 项；
  `board-install.sh` payload 已按流程重建为 **74 文件 / 383,854 B**）。
- CSS **40,237 B**、`Gtk.CssProvider` `parsing-error` **0**、运行时无 `GtkGizmo`
  尺寸告警（上一版 `scale slider` 的 10 px 覆盖会报 `min width -4`，已删）。
- `.cache/qc/ui-v6/probe.py`（真实 `PlayerWindow` + 注入帧 + `display_bypass`
  实时 fakesink）：**37/0** —— 无 rail mark、导航 38 px、当前项 2×40 骨白条、卡片
  273×132 圆角 8、库三行不滚动、视频 920×517、选集列 360、Tab 规则 2 px、后端
  定位到 15 s；截图 `out/ui-v6-01-home…05-play-beauty-on`（1280×768）。
- `.cache/qc/ui-v6/pages.py`：详情 / 最近 / 设置三页 **12/0**（窗口高 768、页面不溢出），
  截图 `ui-v6-06…08`。对比图 `out/compare-*.png`、`out/compare-sheet.png`。
- **板端部署**（2026-11-27 17:55）：8 文件替换、GTK 4.18.6 CSS 0 errors、`--check --require-board` 17 ok、
  `home-1280x768.png` 探针 10/0；常驻实例 PID 2219404（tris）；详见 `.cache/handoff/STATE.md` §65。
  `docs/PITFALLS.md` §2 的板端字体/尺寸经验仍适用（底部预算以板端实测为准）。

**与效果稿的已知差异（均为保留的既有结构 / mockup 与 GTK 的度量差）**：
headerbar 50 px（GTK 实测）vs 稿 59；rail 项 72 px（min-height 60 + padding 12，v5 既有）
vs 稿 60；Hero 弹性 234 px vs 稿 220（v5 的行距 10/9 未改）；播放页视频垂直居中 top 147
vs 稿 95；控制条在选集列打开时拆两行、产品无「选集/美颜」双 Tab —— **已由 P6 解决（§2.15）**；
美颜抽屉 352×529（内容 318×495）浮层（margins 76/112/24）vs 稿 360 全高列，故「更多设置」需滚动；片库只有 2 个 chip
（「最近观看」自 §2.13 起是独立页）。骨架色板、骨白/琥珀纪律、圆角与字阶按 C 落地。

### 2.15 ui-v5 P6 窗口态单行控制条 + 选集/美颜面板（2026-11-27，已上板验收）

P5/v6 的窗口态在选集栏打开时把「字幕 / 美颜 / 全屏」拆到第二行（920 px 画面 + 872 px dock），
视觉上像控制条断了。P6 把底部固定成一行，并把次级入口放回各自的容器：

- **右侧 360 px 面板 = 选集 / 美颜两个标签**：`_build_episodes_sidebar()` 的头部换成
  `.sidebar-tabs` 分段（两个 `Gtk.ToggleButton` 用 `set_group` 成单选），下面是 `Gtk.Stack`；
  选集页保留播放列表/计数/+ 添加文件/最近播放，美颜页就是原来的 `_build_beauty_page()` 整页。
  面板顶部标签 + ✕；✕ / chip / `Esc` 关闭，`_panel_tab` 记住上次标签。
- **美颜页只构建一次，按模式换宿主**：窗口态放进面板标签（去掉 `.beauty-drawer` 卡片类、
  margin 归零、隐藏页头标题/第二个 ✕/40 px 下划线）；全屏和 `--record` 放回右侧 overlay
  抽屉（恢复 margin 76/112/24、352 px、卡片类、页头/✕/下划线）。搬页面前若抽屉开着先关，
  且 `Gtk.Stack` 的旧页名要显式 `remove` 后再 `add_named`（见 PITFALLS §2）。
- **字幕跟着顶栏走**：窗口态把 `_subtitle_button`（沿用原 popover）插到画面顶栏 titles box
  之后、面板 chip 之前；全屏/`--record` 面板不可用时插回底部行 spacer 之后（`--record` 保持
  P4 画布：字幕仍在底部行）。
- **底部固定一行**：`_build_controls()` 只返回一行（transport + 倍速 + 解码 + 音量 + 全屏；
  美颜 pill 和对比 pill 只在面板不可用的模式显示）。删除 `_controls_row2` /
  `_set_transport_compact` 与 `.play-controls-stack`；音量百分比 label 删除（值进 tooltip）。
  窗口态 872 px dock 下 row 自然宽 **838**（中英相同，余 34 px）；全屏 1232 px dock 下
  **1080 中 / 1194 英**（含对比 pill）；`--record` **972**。
- **chip 图标化**：顶栏面板按钮隐藏「选集 · 美颜」文字（只留 ☰ + tooltip「选集 / 美颜」），
  美颜开启时加琥珀点（选中态骨白底时点变墨色）。
- **状态机**：`_sync_episodes_layout()` 只做页面/全屏/录制门控，再调
  `_sync_secondary_placement(available)`；`_set_beauty_drawer_open()` 在页面被面板持有时
  拒绝打开（pill 同时隐藏）；面板 chip 在面板不可用的模式（`--record`）隐藏，避免死控件。

**本机验证**（GTK 4.14.5 / Xvfb 1280×768 与 1280×820、真实 `PlayerWindow`、fakesink 注入
画面）：pytest **606 passed / 15 skipped**（新增 `tests/test_player_panel.py` 7 项、
`test_player_drawer.py` +1、`test_player_episodes.py` 改写）；`.cache/qc/fix59/check.py`
windowed **24/0**、fullscreen **11/0**、record **11/0**；截图 5 张在 `mockups/fix59/`
（本机，gitignored）。

**板端验收**（2026-11-27 18:36，用户当次授权；GTK 4.18.6 / Wayland / DSI 1280×800 /
真实 `mppvideodec`）：3 文件原子替换（`window.py 401ad1f7…`、`style.py 5fce8be6…`、
`i18n.py 814a9c1a…`），`--check --require-board` **17 ok**、CSS 0 errors；探针
`/home/tris/ui-v6-p6-accept/board_accept_p6.py` windowed **21/0**、beauty **23/0**、
fullscreen **12/0**、record **11/0**——窗口 1280×768、视频 **920×517**、dock **872**、
row **838 ≤ 872**；真全屏 1280×800、视频 **1280×720**、dock **1232**、row
**1066 ≤ 1232**；record 1280×768、row **967 ≤ 1232**。截图
`docs/evidence/ui-v5-player-1280x768.png` / `...fullscreen-1280x800.png`；常驻实例
**PID 2278277**（tris，语言 zh，13 项 / 2 目录）。**仍未验证**：真实手指触摸标签/✕/chip、
30 s `--record` 成片、H.265/VP9/AV1/4K。

## 3. 模块划分

| 文件 | 职责 | 状态 |
|------|------|------|
| `src/video_player/app.py` | 命令行入口、`--check`、启动 GTK | 可用 |
| `src/video_player/config.py` | 配置数据类、严格校验与钳位、读写 JSON | 可用（单测覆盖，M3 新增 texture_max_dimension / split / skin_guard 等，M5 新增 face_slim / eye_enlarge / hold_ms） |
| `src/video_player/envcheck.py` | 板端/开发主机环境自检、报告格式化 | 可用（单测覆盖） |
| `src/video_player/pipeline.py` | 容器/caps → demux/parser/decoder 选择、URI 工具 | M1 已实现（单测覆盖） |
| `src/video_player/backend.py` | GStreamer 播放后端：构建管线（含 GL 预缩放与美颜）、状态/seek/音量/fps/快照/uniforms 推送 | M1–M3 可用（板端 30fps）；M5 加检测保持（`hold_ms` + 关键点 EMA）与主脸排序 |
| `src/video_player/playlist.py` | 媒体参数展开、播放列表模型 | 可用（单测覆盖） |
| `src/video_player/library.py` | 片库数据层：配置目录 + 用户目录、单层扫描、按 size+mtime 失效的元数据缓存、筛选/排序/计数纯函数 | ui-v5 P2 新增（`tests/test_library.py` 23 项） |
| `src/video_player/browse.py` | 首页/详情纯数据层（无 GTK）：hero 与两行的选择、同目录选集、7 行文件参数、体积÷时长码率、续播门槛（1 s）、剩余时间向上取整、VPU 可解判断 | ui-v5 P3 新增（`tests/test_browse.py` 28 项） |
| `src/video_player/probe.py` | 元数据（同步 `GstPbutils.Discoverer` 跑在工作线程）与海报帧（`playbin ! appsink`，按 `VideoInfo.stride` 取 RGBA）、缩略图缓存键、硬件解码就绪探测 | ui-v5 P2 新增（`tests/test_probe.py` 5 项；解码路径本机只覆盖 VP9） |
| `src/video_player/resume.py` | 断点续播存储：文件身份（路径 + size + mtime）→ 位置，XDG state，原子写，坏文件静默降级 | M6 新增（单测 17 项） |
| `src/video_player/subtitle.py` | 外挂 SRT 解析、编码探测（UTF-8/GB18030）、二分查找当前字幕、同名文件发现 | M6 新增（单测 16 项） |
| `src/video_player/face.py` | `Face`、`DetectionResult`、`RknnFaceDetector`（ctypes 调 librknnrt） | M2 已实现（10 Hz 采样）；M5 加 `primary_face` / `smooth_landmarks` / `held` 标记 |
| `src/video_player/face_yunet.py` | YuNet 后处理：按 stride 解码、NMS、映射回帧坐标 | M2 已实现（单测覆盖） |
| `src/video_player/rknn_runtime.py` | `librknnrt.so` 的最小 ctypes 封装（init/query/inputs/run/outputs/release） | M2 已实现 |
| `src/video_player/detection.py` | NV12→BGR、pad probe 采样、单深度队列与后台检测线程、NPU 起播预热（在 pipeline PLAYING 前、主线程执行） | M2 已实现；M3 加真实尺寸预热；2026-11-18 从 `start()` 移到 `prepare()`，避免 streaming thread 上 RKNN `std::bad_alloc` |
| `src/video_player/beauty.py` | 美颜参数与 fragment shader、uniform 打包、纹理坐标反向旋转、CPU 参考蒙版、脸型拟合 | M3 已实现（单测覆盖）；M5 加 12 个 warp uniform 与实验性瘦脸/大眼；M5.2 加 10 个 shape uniform、`FaceShape`/`fit_face_shape`、Lab 肤色分割与脸内瘦脸（单测覆盖） |
| `src/video_player/ui/window.py` | GTK4 主窗口、播放区、空态拖放区、信息行、单行 44 px 控制条、选集/美颜双标签面板（P6）、全屏 dock/遥测/进度线、分屏标签、L 形人脸框、中英文切换、`--record` 编排（画布定时器/录制时长/R 键）、快照、PERF 日志、滚轮事件路由与操作日志；ui-v5 P2 起根节点为 `Gtk.Stack`（browse / player）、header 返回箭头、启动落点与 Escape 回片库；ui-v5 P3 起 browse 子节点为 `BrowseShell`（home/library/detail 三页），`_play_paths()` 决定 playlist，browse 页上播放快捷键全部失效（Escape 回上一级）；ui-v5 P4（2026-11-27）播放页重做：隐藏 headerbar、16:9 居中画面、顶栏/选集/更多 popover、遥测行、章节时间轴、44 px 控制条、右侧美颜抽屉、全屏 dock 复用同一控件；录制图层改为 top/dock/drawer/hint/status | M1–M3 已接线（板端验证）；中英文切换已板端点击验证；M5 加实验性 warp 滑杆；2026-11-19 侧栏滚轮改为滚动整页；2026-11-26 **UI v3 全量改造**：空态拖放区、带标签信息行、44 px 两行控制条、顶部美颜面板、不透明全屏 dock + 美颜/对比 pill、淡出后常驻遥测与 2 px 进度线、分屏原始/美颜标签、四角 L 形人脸框；板端 5 态快照见 `docs/evidence/ui-v3-0*.png`，`--check --require-board` 17 ok/0 warn/0 fail；2026-11-26 **UI v4**：侧栏播放列表/美颜两页、美颜页独立、关闭美颜联动；**同日落码**普通启动下运行中开美颜或人脸框时，若 `detection.enabled` 仍为 false 且已有媒体，按当前播放位置 `GLib.idle_add` 重开当前视频（保持美颜状态）并提示「正在启用人脸检测（约2秒）」，见 BOARD.md「运行中开美颜自动重开实测」 |
| `src/video_player/ui/chrome.py` | UI 纯函数：编码归一（`X-H264` → `H.264`）、`CONTAIN` 视频矩形、四角 L 臂坐标、空态/控制条可见性、参数/时长/体积/分辨率格式化、观看时间（刚刚/今天/昨天/月日） | UI v3 新增；无 GTK 依赖，`tests/test_ui_chrome.py` 覆盖；2026-11-27 加 `format_watch_time()` |
| `src/video_player/ui/player_chrome.py` | P4 播放页无状态件：`ChapterTimeline`（自绘轨道/竖条旋钮/章节刻度 + `Gtk.Scale` 0–1000 子集）、`PlayerTopBar`（返回/标题/选集/更多）、`TelemetryBar`（硬解/编码/分辨率/fps/人脸/美颜/CPU）、`DrawerTabRule`（抽屉标题下 1 px 发丝线 + 40 px 骨白下划线） | ui-v5 P4 新增；ui-v6 §2.14 把旋钮改 10×15 竖条、4 px 轨道收到 3 px，并加 `DrawerTabRule`；常量 `CONTROL_HEIGHT=44`、`PLAY_BUTTON_SIZE=52`、`TIMELINE_HEIGHT=20`、`DRAWER_RULE_UNDERLINE=40` |
| `src/video_player/ui/chapters.py` | 可选章节 sidecar 读取与投影：`<clip>.chapters.json`、裸列表兼容、坏文件静默为空、章节起点→0–1 fraction | ui-v5 P4 新增（`tests/test_chapters.py` 10 项） |
| `src/video_player/ui/style.py` | 调色板单一来源：34 个 `@define-color` token + 内嵌样式表（ui-v6 约 40.0 KB）；Cairo 图层用 `rgba()` 读同一份 token | ui-v5 P1 新增；P2/P3/P4 持续追加规则；2026-11-27 换成 ui-v6 方案 C（骨白操作色 + 琥珀数据色，见 §2.14），`tests/test_theme.py` 改为 v6 契约 |
| `src/video_player/ui/library_page.py` | 片库页：页头/筛选/排序/卡片网格/页脚、空态与扫描范围栏、片库探测 worker 编排；卡片回调与 `on_poster`/`on_metadata` 钩子 | ui-v5 P2 新增；P3 把左导航与拖放 receptor 移到 `ui/rail.py` / `ui/browse.py`，卡片改为「打开详情」；2026-11-27 去掉 `recent` chip 与筛选联动，加 `item_for()` / `refresh_history()` 供最近页复用 |
| `src/video_player/ui/rail.py` | 88 px 左导航：首页/片库/最近/设置、手绘图标、硬解状态点、扫描时钟；当前项用 2×40 px 骨白竖条（`_ActiveBar` 子控件） | ui-v5 P3 从 `library_page.py` 抽出；2026-11-27 设置项从置灰改为可点，「最近」项改为真实页面；同日 ui-v6 删「面」标志、导航上移、当前项改骨白竖条（§2.14） |
| `src/video_player/settings.py` | 设置页纯数据（无 GTK）：扫描目录合并去重与「配置 / 用户 / 缺失」标志、关于行的稳定顺序与字段 | 设置页新增（`tests/test_settings_model.py` 8 项） |
| `src/video_player/ui/settings_page.py` | 设置页：语言 / 解码 / 扫描目录 / 关于四张卡；语言与解码走回调回窗口，目录委托 `LibraryPage`（`choose_directory`/`rescan`/`remove_directory`） | 设置页新增（`tests/test_settings_page.py` 13 项；本机探针 33/0） |
| `src/video_player/ui/paint.py` | Cairo 共用件：`Poster`（圆角海报/淡底等宽时长/徽标/2 px 进度线/当前集描边）、`GhostFrame`、`BackdropArt(hero/detail)`（封面裁剪 + 多层 scrim）、`paint_cover`/`draw_pill`/`rounded_path` | ui-v5 P3 新增（`tests/test_browse_shell.py` 守样式类名）；ui-v6 §2.14 圆角 8/10、50 px 渐变 scrim、时长改淡底等宽 |
| `src/video_player/ui/browse.py` | `BrowseShell`：rail + home/library/recent/detail/settings 五页栈、导航/返回/拖放、`BackdropCache`（1140 px 大图，单 worker 串行 + size/mtime/width 缓存） | ui-v5 P3 新增；本机真窗口 93 项交互断言；板端 2026-11-26 部署，四张 1280×768 截图 + 54 项断言；2026-11-27 加 settings 路由；同日晚加 recent 页（`_sync_rail()` 只跟页面，不再跟筛选） |
| `src/video_player/ui/home_page.py` | 首页：hero（弹性高度、续播 CTA、设备行）+ 继续观看 4 卡 + 最近添加 4 卡 + 空态整页 | ui-v5 P3 新增；2026-11-27「继续观看 › 全部」改落最近页 |
| `src/video_player/ui/detail_page.py` | 详情页：268 px 背景板（返回/chips/标题/CTA）+ 3 列选集（当前集自动滚入）+ 文件参数卡 + 同目录 3 行 | ui-v5 P3 新增 |
| `src/video_player/ui/recent_page.py` | 最近观看页：页头（项数 / 继续观看 N · 已看完 M / 清除记录）+ 两组卡片网格（组内按观看时间倒序）+ 空态；海报与元数据复用片库缓存，不启 worker | 2026-11-27 新增（方向 C，见 §2.13） |
| `src/video_player/i18n.py` | 中英文文案表、`--lang`/按钮切换、XDG 语言偏好持久化（默认中文，按钮显示目标语言） | 新增；单测覆盖，板端已验证 |
| `src/video_player/diagnostics.py` | 崩溃与操作诊断：`faulthandler` 写 `crash.log`、关键 UI 操作追加 `actions.log`（XDG cache，失败静默） | 新增；单测覆盖，板端验证 `crash.log` 可捕获 SIGSEGV |
| `src/video_player/ui/capture.py` | 逐 widget 快照（`Gtk.WidgetPaintable`→`render_texture`→pixbuf）与 `CanvasBuilder` 画布合成（缓存 base、分层刷新、人脸框） | M3 收尾新增（录制用） |
| `src/video_player/recording.py` | 录制分支：`glvideomixer` 合成视频 + UI 画布（`--record`），以及演示素材用的纯视频模式（`video_only=True`，无合成器）；`mpph264enc`/`mp4mux` 输出，EOS 收尾与统计 | M3 收尾新增，M4 收尾加纯视频模式（板端 30 fps 验证） |
| `scripts/launch_player.py` | 桌面入口包装脚本（原样透传参数） | 可用（M3 修复 `--snapshot` 被吞） |
| `scripts/perf_probe.py` | 板端帧率探针：pad probe 计帧 + GTK snapshot 计数 + CPU | 可用（M1–M3 实测工具，含 `BEAUTY`/`SCALE` 旋钮） |
| `scripts/bench_board.sh` | 色彩转换路径 wall-time 基准矩阵 | 可用（M1 实测工具） |
| `scripts/convert_yunet.py` | x86 侧 ONNX→RKNN 转换（rk3576，fp16，mean 0/std 1） | 可用（1,084,515B 模型） |
| `scripts/measure_tap.py` | 板端 headless 检测吞吐测量（`fakesink sync=true`） | 可用（M2 实测工具） |
| `scripts/probe_board.py` | 环境报告导出（文本/JSON/Markdown） | 可用 |
| `scripts/build_board_install.py` | 重新生成 / 校验 `board-install.sh` 的内嵌源码包（确定性 tar.gz + base64） | 可用（发布前 `--check`） |
| `tests/` | config/beauty/face/pipeline/playlist/app/envcheck/seek/launcher/fps/预热/录制/中英文/检测保持/warp/诊断/UI chrome/全屏 dock/主题/片库/页面栈 单测 | 本机 `PYTHONPATH=~/.local/lib/python3.10/site-packages python3 -m pytest tests -q` → **561 passed / 15 skipped / 0 failed**（15 skip 为无 numpy 的既有能力差异）。ui-v5 P2 新增 `test_library.py`（23）、`test_probe.py`（5）、`test_page_shell.py`，并扩了 `test_ui_chrome.py` / `test_config.py` / `test_i18n.py`；ui-v5 P3 新增 `test_browse.py`（28）、`test_browse_shell.py`（11），`test_page_shell.py` 重写为 18 项（browse 壳/播放页往返、playlist、详情 Escape）；2026-11-26 新增 `test_board_install.py`（10），校验离线安装包内嵌源码与工作区逐字节一致；2026-11-27 新增 `test_recording_layers.py`（2），锁定录制画布包含顶栏；ui-v5 P4 新增 `test_chapters.py`（10）、`test_player_drawer.py`（4），重写 `test_fullscreen_dock.py`（10）与 `test_page_shell.py`（18），`test_recording_layers.py` 改为 top/dock/drawer/hint/status；设置页新增 `test_settings_model.py`（8，纯数据）与 `test_settings_page.py`（13，结构 + 单选行为） |

## 4. 线程模型

| 线程 | 职责 | 注意 |
|------|------|------|
| GStreamer streaming thread | 解码、GL 上传/转换/预缩放、美颜 shader、sink 提交 | 不做阻塞调用；检测只投递请求；shader 只读 uniform；**不要在这里做 RKNN 预热**（见 §9 第 9 条） |
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
**NV12 布局不能只看 buffer 大小**：RK3576 的 mppvideodec 按 64 B 对齐行宽，
1280×720 输出 1,589,760 B（真实布局 `1472×720`），而 `1440×736` 也能整除此长度；
旧实现按「stride 余量最小」选择，720p 上选错布局 → 图像横向剪切 → 一张脸都检不到
（2026-11-23 板端验收复现并修复）。`nv12_to_bgr()` 现在优先选 stride 为 64 倍数的候选，
没有才退回 16 B 对齐；逐行相关性扫描是判断真实布局的可靠手段（`GstVideo.VideoInfo`
对 DMABuf 缓冲报告的值可能不符）。
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
- `beauty`：开关、磨皮/美白强度（0–100 → 0–1）、**`face_slim`** / **`eye_enlarge`**
  （M5 实验性瘦脸/大眼 warp 强度，float 0–100，默认 0 = 与 M4 完全等价；按 0.1
  取整）、对比模式、羽化像素、**`expand`**（人脸框外扩 0–1，默认 0.12）、
  **`skin_guard`**（肤色门限，默认 true）、**`split`**（分屏位置 0.05–0.95，
  默认 0.5）、**`debug_mask`**（遮罩预览，默认 false）；
- `detection`：**`enabled`（默认 false，见 §M7.4；`--demo` / 美颜 / 人脸框按需开启）**、模型路径、检测间隔帧数、最大人脸数、分数阈值、跟踪补帧、
  **`hold_ms`**（检测空档保持窗口 0–5000 ms，默认 600；0 = 关闭保持、无脸立即
  清空，即 M4 行为）；
- `debug`：信息面板、fps 统计窗口、日志级别。

加载时做类型校验和范围钳位（如音量 0–100、倍速 0.5–2.0、间隔 1–10、
`split` 0.05–0.95、`texture_max_dimension` 0–8192、`face_slim`/`eye_enlarge`
0–100、`hold_ms` 0–5000），未知字段直接报错
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

M4 实测（板端 2026-11-18）：

| 场景 | 工具 | fps | CPU | 说明 |
|------|------|-----|-----|------|
| 应用内录制（`--record`，demo 分屏，1280×800 输出） | `launch_player.py --demo --record --record-duration` | 30.0（编码 30–32 帧/s） | 184–205% | `display_bypass` 让显示分支走 fakesink；画布 compose 3–6 ms/tick，每 3 s/1 s 分层刷新 |
| 1 h 长跑（检测+美颜开，1080p30 循环素材） | `launch_player.py --config cfg_faceon.json` | t≥300 s 均值 **30.06**，最后 600 s 全部 ≥30.0 | 90–133% | 3549 个 PERF 样本；RSS +4.8 MB/h、threads 29–32、fds 74–79 无增长（A5） |

录制分路径基准（gst-launch，300 帧 1280×800）：系统内存 `videoconvert`+`mpph264enc` 166 fps；
`GL NV12 → gldownload → 编码` 20 fps；`GL RGBA → gldownload → 编码` 90 fps；
`glvideomixer`(2 pad) → `GL RGBA` → 读回 → 编码 36 fps。因此录制链故意保留 RGBA 到
`gldownload`，由 `mpph264enc` 直接吃 RGBA（NV12 的 GL 转换是主要慢点）。

M5 实测（板端 2026-11-18，同一台板卡；素材 `selfie-demo-1080p30-h265.mp4`，HEVC
1920×1080@30、37.65 s；`perfdbg.py` patch `start_recording` 后采样
`measured_fps()` / `beauty_ms`）：

| 场景 | fps | `beauty_ms` | 说明 |
|------|-----|-------------|------|
| 美颜 + 检测 | 30.0 | 0.86–0.87 | 基线（检测保持开启） |
| 美颜、检测关 | 30.0 | 0.64–1.20 | 无 NPU 线程与保持逻辑 |
| 美颜关 | 30.0 | 0.87–1.77 | 透传仍有元素开销 |
| 美颜 + 检测 + 瘦脸/大眼 = 100 | 30.0 | 0.86–0.96 | warp 增量 ≤0.1 ms |

结论：warp 只改采样坐标、不增加采样次数；检测保持与关键点 EMA 只在 10 Hz 检测
回调里维护状态（GLib 主线程），不进入流线程。M5 的 fps 与 M4 持平（30.0），A2 的
余量不变。

## 8. 失败与降级策略

| 失败点 | 表现 | 降级 |
|--------|------|------|
| NPU/模型不可用 | 检测器初始化失败（如模型文件缺失） | 已实现：检测关闭，信息行显示 `NPU: off`，硬解播放与美颜透传不受影响 |
| `glshader` 缺失或 shader 编译失败 | 建元素/设 `fragment` 失败 | 已实现：美颜禁用、开关置灰，信息行显示 `beauty: unavailable`，播放继续（M3） |
| `glcolorscale` 缺失 | 无法预缩放纹理 | 已实现：跳过该级，全尺寸纹理提交；效果不变，窄窗口下 CPU 偏高（M3） |
| 关键点缺失或退化（无脸、点数≠5、框过小） | 实验性瘦脸/大眼无法定位控制点 | 已实现：`u_warp_ready=0` 时 warp 原样透传，磨皮/美白与播放不受影响（M5） |
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
9. **已定（2026-11-18）**：NPU 起播预热必须在 pipeline PLAYING 前、主线程的
   `FaceDetectionTap.prepare()` 里完成。把它放在 `start()`（由 pad-added 在
   GStreamer streaming thread 调用）时，1080p 零帧推理会让 librknnrt 在
   `rknn_inputs_set` 抛 C++ `std::bad_alloc`，进程直接 SIGABRT；Python 的
   `except Exception` 兜不住 C++ 异常。
10. **新增（M5）**：检测保持只桥接 `< detection.hold_ms`（默认 600 ms）的检测空档；
   01 近景素材的空档从 6 次降到 0，但检测极差的 03 病态素材仍会从 15 次残留 6 次
   （>600 ms 无脸的时段仍会闪断），彻底解决需要跟踪器或提高检测频率。实验性 warp
   依赖 YuNet 5 点关键点质量，远距离小脸/大角度侧脸时控制点会漂移；功能默认关闭，
   不影响 P0 验收。

## 10. 目录结构

```
demos/43-video-player/
├── configs/player.example.json   # 配置契约
├── docs/                         # PRD / 架构 / 板端清单 / 演示指南 / evidence/（实测证据）
├── models/                       # RKNN 模型（不入库）
├── scripts/                      # 启动、环境探测、性能探针脚本
├── src/video_player/             # app/config/beauty/face/pipeline/backend/playlist/resume/
│                                 #   recents/subtitle/envcheck/library/probe/
│                                 #   ui（window.py/chrome.py/style.py/library_page.py/capture.py）
│                                 #   + recording.py（--record 应用内录制）
├── tests/                        # pytest 单测
├── install.sh                    # 环境检查 / 部署脚本（M4 完善）
├── requirements*.txt             # 运行/开发依赖
├── README.md / README_zh.md      # 英文 / 中文门面
└── .gitignore
```
