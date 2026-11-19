# 已知坑（Pitfalls）

> 本文件汇总本项目开发/板端联调中踩过且会反复踩的坑。**开工前先读**，遇到异常先对照本表。
> 每条都注明现象、原因、规避方式；实测数字来自 RK3576 板卡（见 `docs/BOARD.md`）。

## 1. GStreamer / 板端

| 现象 | 原因 | 规避 |
|---|---|---|
| `Cannot link ...` 或元素静默不生效 | 板端 1.26.2 部分 GL 元素在**运行时用 factory 创建 + `sync_state_with_parent()` 组装进 tee 分支**时行为异常（如 `glvideoflip` 属性正确、caps 协商正常，但输出不旋转）；同样的链用 gst-launch 解析却正常 | 旋转/缩放等关键处理优先放在 CPU 侧或改用 gst-launch 验证过的组合；不要假设「属性设了就等于生效」，必须抽帧验证输出 |
| `glcolorconvert` 输出 NV12 很慢 | GL NV12 路径在板端只有 **20–28 fps**（RGBA 同链路 90 fps） | 编码前保持 **RGBA** 交给 `mpph264enc`（它直接吃 RGBA），避免 GL→NV12 |
| `mpph264enc` 设 `bitrate` 报「找不到属性」 | 该元素码率属性名是 `bps`（以及 `bps-max/bps-min/gop/rc-mode`） | 用 `bps`；`profile/level` 等按需 |
| 管线结束文件无法播放（`moov atom not found`） | 收到 EOS 立刻 `set_state(NULL)`，与 `mp4mux` 写 moov 竞争 | EOS 探针触发后先等文件大小稳定（`_drain_output()` 轮询 size），再 teardown |
| `GstAppSrc` 没有 `push_buffer` | PyGObject 绑定是信号式 API | `emit("push-buffer", buf)`、`emit("end-of-stream")` |
| `Gst.Bus.add_watch` 参数错误 | PyGObject 签名是 `add_watch(self, priority:int, func:Gst.BusFunc, user_data=None)`，与 C API 不同 | 复用 backend 已装的 `add_signal_watch()`，改用 `bus.connect("message::eos"/"message::error", ...)` |
| 录制分支压慢整条链（显示掉帧） | tee 的回收分支 queue 满会反压解码/显示 | 录制队列用 `leaky=downstream`（**但会丢帧，仅用于可接受丢帧的场景**）；要高质量录制就换更轻的路径（见 §3） |
| 显示掉的帧计数正常但「看起来卡」 | `fpsdisplaysink` 的 `average_fps` 是终身平均，起播丢帧后会长期偏低；用它做指标会误判 | 用 `measured_fps()`（1.5 s 滑窗）或读 `fps`（瞬时）作为显示/日志指标 |

## 2. GPU / GTK4 显示

| 现象 | 原因 | 规避 |
|---|---|---|
| 1080p 视频窗口化播放 CPU 突然 70–80%（全屏只要 16%） | **GTK4/GSK 在纹理缩放比 < 0.5 时走每帧全帧 CPU 路径**（1080p 纹理显示在 <960 px 宽的控件里就触发） | 在 GL 管线里用 `glcolorscale` 预缩到接近显示尺寸（默认 `player.texture_max_dimension = 1280`），让 GTK 侧缩放比 ≥ 0.5；实测 CPU 从 62–64% 降到 18–20% |
| 创建 `Gsk.CairoRenderer` 后进程卡死、板子重启 | 播放中再 realize 一个 GL 后端会触发 `MESA: DRM_IOCTL_PANFROST_CREATE_BO failed (err=12)` 并耗尽 GPU buffer | **不要**在运行中的应用里创建额外 renderer；不要逐帧整窗截图（35–70 ms/帧，且 `to_node()` 首帧前返回 None） |
| `Gtk.WidgetPaintable.to_node()` 返回 None | 窗口尚未真正绘制出首帧 | 按 500 ms 轮询重试（实测启动后约 2.5–5 s 才就绪）；录制/截图等 `to_node()` 可用再开始 |
| `--snapshot` 无输出/失败 | 非全屏（windowed）模式下窗口快照路径不可用 | 用 `--demo`/全屏模式；或直接走 `--record` |
| 关窗时崩溃/资源泄漏 | GTK4 **不发射** `GtkWidget::destroy` 信号 | 清理逻辑挂 `close-request`，`window.close()`；退出顺序仍是「先关窗、再 quit、末尾 `os._exit`」 |
| 新增 shader uniform 后画面无变化，也没有报错 | `glshader` 的 `uniforms` 只接受 `G_TYPE_FLOAT` 标量（gdouble/int 被静默忽略，甚至全黑）；Python 侧返回键集必须与 shader 的 `uniform float` 声明**完全一致**，缺键/多键都不报错 | 改 shader 声明时同步改 `beauty.uniforms()`/`WARP_UNIFORM_KEYS`；`tests/test_beauty_warp.py::test_warp_uniform_keys_match_the_shader_declaration` 校验键集一致（shader 共 41 个 `uniform float`）；板端再用 `debug_mask=1`/颜色探针/抽帧确认，不能只看「没报错」 |

## 3. 录制（`--record` 与演示素材采集）

| 现象 | 原因 | 规避 |
|---|---|---|
| 录出的视频里有大量冻结/重复帧（实测 12–41%） | 应用内合成路径 `glvideomixer` 在板端只有 **~21 fps**，而录制队列是 leaky，合成器来不及就用上一帧补满 30 fps | 演示素材用 **纯视频录制** `Recorder(video_only=True)`（tee → queue → capsfilter(GL RGBA) → gldownload → `mpph264enc` → `mp4mux`，`leaky=0`），实测 ≈29 fps、重复帧 1.79%；UI/字幕后期叠 |
| 纯视频录制把手机竖拍素材录成上下颠倒 | 源文件的 **Display Matrix 会以 `image-orientation` tag 传给 `mp4mux` 写进输出**；若代码再翻转一次像素，播放器按矩阵又转一次 | 纯视频录制**不要对像素做旋转**，让矩阵随文件沿用；用 `ffprobe -show_entries stream_side_data=rotation` 验证 |
| 录制文件时长短于 wall 时间 | 采集吞吐 ≈29 fps（H.265 1080p），略低于实时 | 按内容时长校准（`ffprobe` 实测），`--seconds` 留 20–30% 余量 |
| `--start-at` 指定位置后录到 0 帧 | H.265 seek 后偶发不推帧 | 重跑该段，或从片头开始录再剪 |
| mp4 录制完很慢才退出 | 等的 EOS 永远不来（显示分支还在播，pipeline 不会整体 EOS） | 在解析器 src pad 装 EVENT_DOWNSTREAM 探针检测 EOS，再等文件落地 |
| `Gst.Bus` 上收不到录制分支 EOS | 录制元素名以 `rec-` 开头，被 backend 的 bus 处理误当播放消息 | backend 的 `_on_bus_message` 对 `rec-` 前缀直接 return，录制分支自己收尾 |
| 后期或播放器忽略录制文件的方向，成片倒置/转两次 | `mp4mux` 会把源 Display Matrix（本次 07 微信素材 `rotation=-180`）原样写进录制文件；ffmpeg 默认按矩阵 autorotate，播放器也按 tag 旋转 | 后期用 ffmpeg 默认 autorotate 解码成 1080p 正立中间层再合成；最终输出加 `-map_metadata -1` 不带 rotation side data；源/成片都用 `ffprobe -show_entries stream_side_data=rotation` 复核 |
| 本机后期报 `ffmpeg: command not found`，或字幕中文变方框 | 本机（开发机）无系统 ffmpeg/ffprobe、无 sudo，也没有 WenQuanYi 字体；板端相反，系统自带 ffmpeg 与字体 | 用静态构建 `.cache/bin/ffmpeg`、`.cache/bin/ffprobe`（ffmpeg 7.0.2-static，含 `ass`/`subtitles`/`xfade`/`fade`/`drawtext`）；从板端拷 `/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc` 到 `.cache/fonts/`，`ass`/`subtitles` 滤镜加 `fontsdir=.cache/fonts` |
| 调小瘦脸/大眼常量后成片「看不出效果」，但 A/B 帧差仍非零 | 反向 warp 位移量小，差异集中在眼与下颌边缘：`EYE_MAGNIFY=0.35`/`SLIM_PUSH=0.05` 时同源 A/B 帧差 YAVG=1.59（PSNR 35.3 dB）仍肉眼不明显；且成片素材录的是录制当时的常量 | 常量固定为 `0.40`/`0.065`（单测 NumPy 参考实现同步）；调整常量后必须重录 07/08/09/10 等相关片段并抽帧目视（ramp/分屏）确认，不能只信帧差指标 |

## 4. 人脸检测 / 美颜

| 现象 | 原因 | 规避 |
|---|---|---|
| 美颜/人脸框周期性闪断（v2 成片闪烁根因） | 检测结果里 `faces` 为空的帧把 `u_active` 置 0 并清空人脸 uniform，整段透传 → 美颜与框消失 100–600 ms，下一帧又恢复 | 已修（M5）：`detection.hold_ms`（默认 600 ms，钳制 0–5000）——窗口内沿用上次人脸/5 点关键点并标 `held=True`（UI 人脸框同样保持），关键点 EMA（`alpha=0.35`，脸中心跳变 >0.3 脸宽视为换人重置），超时才清空（架构见 `docs/ARCHITECTURE.md` §2.1）。验证：板端 monkeypatch `_on_detection_result` 统计 raw=0 与空档次数，01 近景素材空档 6→0（`docs/BOARD.md` §5.2）；素材检测极差（>600 ms 无脸）时仍可能残留空档 |
| 蒙版边界「呼吸/抖动」 | 检测 10 Hz 更新且无时间平滑/插值；框每次更新跳 10–20 px，与 24 源像素羽化同量级 | 时间滤波（EMA/one-euro）+ 帧间插值；或降低检测间隔（代价是 CPU/NPU） |
| 美颜「没覆盖全脸」 | 蒙版是「检测框外扩 12% 的圆角矩形」+ 肤色门限；YuNet 框是内脸框，不含额头/发际/耳/下颌缘；镜框/眉毛/眼白会被肤色门限压低 | 用 5 点关键点拟合椭圆蒙版、加大 `expand`/`feather`；演示镜头避免极端角度 |
| 实时播放有周期性顿挫（平均 fps 30 但体感卡） | 检测在**解码线程**里同步 `extract_dup()` 整帧 NV12（1920×1080 ≈ 3.2 MB，17–33 ms），每 3 帧堵一次，随后积压帧连发 | 已规划：取样移到独立线程 / 用 RGA 先缩放到 640×640；临时可加大 `detection.interval_frames` |
| 背景里路人也被磨皮 / 蒙版在背景闪 | 检测返回多张脸（含远处小脸），`max_faces` 默认 3 | 演示/单人场景把 `detection.max_faces` 设为 1（只保留分数最高的人脸） |
| 瘦脸/大眼 warp 作用到背景路人，或人脸框与关键点对不上 | warp 的 5 点关键点必须来自**主脸**（框面积最大，`face.primary_face()` 把它排到 `faces[0]`，shader 的 `warp_gate` 只认 `u_face0`）；背景有第二张脸时，若 `max_faces>1`，主脸排序/关键点可能对应到错误人脸 | 演示素材背景有第二张脸时必须 `detection.max_faces=1`（`docs/demo-video/configs/video_*.json` 已统一）；多脸场景开实验 warp 前先确认 `faces[0]` 是最大脸；`tests/test_detection_hold.py` 覆盖主脸排第一与无 landmarks 时不启用 warp |
| 自拍/手机素材检测分数贴阈值（0.5–0.6）易丢 | 特写人脸过大（占画面 1600×950 px），YuNet 在 640×640 输入上偏弱 | 演示素材选中等景别；必要时降低 `min_score` 或依赖 `detection.hold_ms` 保持 |

## 5. 测试 / 本机环境

| 现象 | 原因 | 规避 |
|---|---|---|
| `pytest` 报 `ModuleNotFoundError: gi` / numpy 缺失 | 系统 `python3` 有 `gi` 无 numpy；`.venv/bin/python` 有 numpy 无 gi（用 `PYTHONPATH=/usr/lib/python3/dist-packages` 可补） | 常规单测用系统 `python3 -m pytest tests -q`（M5 收尾：149 passed / 15 skipped）；需要 numpy 的测试用 `.venv/bin/python` 并补 `PYTHONPATH` |
| `.venv` 下 3 个测试失败 | numpy 1.26 与测试断言的历史差异（beauty mask 羽化、NV12 跨步转换、YuNet NMS 数量），M3 前代码同样失败（M5 收尾复测 211 passed / 3 failed，仍为同样 3 个） | 非回归；以系统 python（无 numpy）结果为准 |
| 板端 `pytest` 不存在 | 板端未装 pytest | 板端用脚本/应用实测验证；单测在本机跑 |

## 6. 文档 / 发布（内部）

| 现象 | 原因 | 规避 |
|---|---|---|
| 误把内部文件推到公开仓库 | 仓库里同时有公开与内部资料 | 发布走 `.cache/export_publish.py`（已排除 `AGENTS.md`、`docs/DELIVERY.md`、`docs/TEST_MEDIA.md`、板端连接文档、`docs/demo-video/`、`test-media/`、`*.mp4`、`models/*.rknn`、`.cache/`）；提交前 `git status` 复核 |
| 飞书 `<ol seq>` 编号不生效 | 该 API 不支持 `seq` 属性（`degrade_code=5002`） | 需要编号用普通段落手写 `1.` `2.` `3.`；或 `docs +update --command block_replace` 局部替换 |
| `docs +create` 后标题重复 | 工具会自动在正文前插入 `<title>` | 正文 XML 不要自带 title |
| 全局替换字幕/视频后旧 token 失效 | 飞书文档里的图片/视频是 token 引用 | 替换媒体后确认 block 内 token 已更新；删除旧 block 前先 fetch 核对 |
| 成片体积偏大，飞书/Release 上传慢 | 同一 v3 时间轴 CRF18 编码为 57.3 MB；演示清晰度对更高码率收益有限 | 最终编码用 CRF21（35.5 MB，仍满足清晰度）并保留 `+faststart`；发布前用 `ffprobe` 复核体积/分辨率/时长 |

## 7. 复现入口

- 板端命令、性能实测表、长跑与降级记录：`docs/BOARD.md` §5.2、§6。
- 线程模型、uniforms 契约、录制架构、检测保持与实验 warp：`docs/ARCHITECTURE.md` §2.1–§2.5、§4。
- 演示视频制作（纯视频录制、剪接、字幕）：`docs/demo-video/README.md`。
- 演示流程与话术：`docs/DEMO_GUIDE.md`。
