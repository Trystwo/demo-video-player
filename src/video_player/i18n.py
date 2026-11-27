"""Chinese/English strings for the player UI.

The demo is shown on a Chinese board but is also used for English evidence
captures, so the language is a runtime choice instead of a build-time one.
Only the language preference is persisted (XDG config); playback settings stay
in the project JSON so a demo machine is never modified behind the user's back.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

LANGUAGES = ("zh", "en")
DEFAULT_LANGUAGE = "zh"

#: ``key -> (Chinese, English)``.  Dynamic entries are ``str.format`` templates.
TEXT: dict[str, tuple[str, str]] = {
    "app_title": ("视频播放器", "Video Player"),
    "title_with_media": ("{name} · 视频播放器", "{name} · Video Player"),
    "playlist_position": ("播放列表 {index}/{count}", "Playlist {index}/{count}"),
    "open": ("打开", "Open"),
    "open_tooltip": ("打开本地视频文件", "Open a local video file"),
    "compare": ("对比", "Compare"),
    "compare_tooltip": (
        "分屏对比：左侧原图，右侧美颜（C）",
        "Split view: original on the left, beautified on the right (C)",
    ),
    "switch_language": ("English", "中文"),
    "switch_language_tooltip": (
        "切换界面语言为英文",
        "Switch interface language to Chinese",
    ),
    "empty_title": ("把视频放进来", "Drop a video here"),
    "empty_subtitle": (
        "MP4 · MKV · H.264 / H.265，由 RK3576 VPU 硬解",
        "MP4 · MKV · H.264 / H.265, hardware-decoded on the RK3576 VPU",
    ),
    "empty_choose_file": ("选择文件", "Choose file"),
    "empty_choose_folder": ("选择文件夹", "Choose folder"),
    "empty_drop_hint": (
        "也可以把文件或文件夹直接拖进来",
        "You can also drag files or folders here",
    ),
    "empty_chip_vpu": ("VPU 硬解 · 4K", "VPU decode · 4K"),
    "empty_chip_npu": ("NPU 人脸检测", "NPU face detection"),
    "empty_chip_beauty": ("美颜 · A|B 分屏", "Beauty · A|B split"),
    "open_folder": ("选择文件夹", "Choose folder"),
    "folder_empty": (
        "这个文件夹里没有支持的视频",
        "No supported videos in that folder",
    ),
    "drop_empty": (
        "拖入的内容里没有支持的视频",
        "Nothing playable was dropped",
    ),
    "playlist_empty": (
        "拖入视频或文件夹\n加入播放列表",
        "Drop videos or folders\nto build a playlist",
    ),
    "playlist_add": ("+ 添加文件", "+ Add file"),
    # -- ui-v5 browse shell (library page) --------------------------------
    "rail_ready": ("硬解就绪", "HW ready"),
    "rail_sw_only": ("仅软解", "SW only"),
    "nav_library": ("片库", "Library"),
    "nav_recent": ("最近", "Recent"),
    "nav_settings": ("设置", "Settings"),
    "settings_tooltip": (
        "设置：语言 / 解码 / 扫描目录 / 关于",
        "Settings: language / decode / folders / about",
    ),
    "settings_headline": (
        "界面 · 播放 · 片库 · 关于",
        "Interface · Playback · Library · About",
    ),
    "settings_language_title": ("界面语言", "Interface language"),
    "settings_language_hint": (
        "切换后立刻生效，并记住选择。",
        "Applies immediately and is remembered.",
    ),
    "settings_lang_zh": ("中文", "Chinese"),
    "settings_lang_en": ("英文", "English"),
    "settings_decode_hint": (
        "影响下一次打开的视频；播放中切档会按新方式重新打开当前视频。",
        "Applies to the next video; while playing, the clip is reopened.",
    ),
    "settings_folders_title": ("扫描目录", "Scan folders"),
    "settings_folders_hint": (
        "片库只扫这些目录的一层；拖入文件夹会自动加入。",
        "One level of each folder is scanned; dropped folders join automatically.",
    ),
    "settings_folder_config": ("配置", "Config"),
    "settings_folder_missing": ("缺失", "Missing"),
    "settings_about_title": ("关于", "About"),
    "settings_about_hint": (
        "只读信息，用于演示与排障。",
        "Read-only facts for demos and support.",
    ),
    "settings_about_version": ("版本", "Version"),
    "settings_about_platform": ("平台", "Platform"),
    "settings_about_kernel": ("内核", "Kernel"),
    "settings_about_gtk": ("GTK 版本", "GTK version"),
    "settings_about_gstreamer": ("GStreamer 版本", "GStreamer version"),
    "settings_about_decode": ("解码后端", "Decode backend"),
    "settings_about_decode_hw": ("VPU 硬解可用", "VPU ready"),
    "settings_about_decode_sw": ("仅软解", "Software only"),
    "settings_about_state": ("状态文件", "State file"),
    "settings_about_cache": ("缓存目录", "Cache folder"),
    "library_title": ("片库", "Library"),
    "library_stat": ("{count} 项", "{count} items"),
    "library_stat_one": ("{count} 项", "{count} item"),
    "library_sub": ("{folders} 个目录", "{folders} folders"),
    "library_sub_one": ("{folders} 个目录", "{folders} folder"),
    "library_sub_empty": ("尚未添加目录", "No folders yet"),
    "library_filter_all": ("全部", "All"),
    "library_filter_finished": ("已看完", "Finished"),
    "library_sort": ("排序", "Sort"),
    "library_sort_name": ("名称", "Name"),
    "library_sort_duration": ("时长", "Duration"),
    "library_sort_added": ("最近添加", "Recently added"),
    "library_rescan": ("重新扫描", "Rescan"),
    "library_scan_summary": (
        "上次扫描 {clock} · {seconds:.1f} s",
        "Scanned {clock} · {seconds:.1f}s",
    ),
    "library_scan_never": ("尚未扫描", "Never scanned"),
    "library_empty_title": ("片库还是空的", "Your library is empty"),
    "library_empty_body": (
        "扫描目录里没有找到可播放的视频。把一个目录加进来，或直接打开一个文件。",
        "No playable videos were found in the scanned folders. "
        "Add a folder, or open a file directly.",
    ),
    "library_add_dir": ("选择目录", "Add folder"),
    "library_open_file": ("打开文件", "Open file"),
    "library_scan_scope": ("扫描范围", "Scan scope"),
    "library_no_dirs": ("还没有扫描目录", "No folders yet"),
    "library_remove_dir": ("移除目录", "Remove folder"),
    "library_supported": ("支持格式", "Supported formats"),
    "library_formats": (
        "容器 MP4 / MKV / MOV / TS / WEBM",
        "Containers MP4 / MKV / MOV / TS / WEBM",
    ),
    "library_codecs": (
        "编码 H.264 / H.265 / VP9 / AV1",
        "Codecs H.264 / H.265 / VP9 / AV1",
    ),
    "library_back": ("返回片库", "Back to library"),
    "library_dialog_title": ("选择片库目录", "Choose a library folder"),
    "library_finished_badge": ("已看完", "Finished"),
    "library_no_meta": ("信息读取中…", "Reading metadata…"),
    "library_missing_file": ("文件已不存在", "File no longer exists"),
    "library_no_matches": ("这个筛选下没有视频", "No videos match this filter"),
    "library_empty_hint": (
        "把视频放进扫描目录后点「重新扫描」",
        "Put videos in a scanned folder, then press Rescan",
    ),
    # -- ui-v5 home page ---------------------------------------------------
    "nav_home": ("首页", "Home"),
    "home_continue": ("继续观看", "Continue watching"),
    "home_added": ("最近添加", "Recently added"),
    "home_all": ("全部", "All"),
    "home_row_count": ("{count} 部", "{count} items"),
    "home_finished": ("已看完", "Finished"),
    "home_remaining": ("剩 {time}", "{time} left"),
    "home_resume": ("继续观看 {time}", "Continue {time}"),
    "home_play": ("开始播放", "Play"),
    "home_details": ("详情", "Details"),
    "home_kicker": ("{folder} · 共 {count} 个", "{folder} · {count} items"),
    "home_device": (
        "本机播放 · {picture} · {codec} · {decode} · {fps} fps",
        "Local · {picture} · {codec} · {decode} · {fps} fps",
    ),
    "home_empty_title": ("片库还是空的", "Your library is empty"),
    "home_empty_body": (
        "扫描目录里没有找到可播放的视频。先添加一个目录，或直接打开一个文件。",
        "No playable videos were found in the scanned folders. "
        "Add a folder, or open a file directly.",
    ),
    "home_open_library": ("前往片库", "Open library"),
    # -- ui-v5 recent page -------------------------------------------------
    "recent_title": ("最近观看", "Recently watched"),
    "recent_headline": (
        "继续观看 {continue_count} · 已看完 {finished_count}",
        "Continue {continue_count} · Finished {finished_count}",
    ),
    "recent_group_continue": ("继续观看", "Continue watching"),
    "recent_group_finished": ("已看完", "Finished"),
    "recent_clear": ("清除记录", "Clear history"),
    "recent_clear_title": ("清除观看记录？", "Clear watch history?"),
    "recent_clear_body": (
        "会清空「继续观看」与「最近观看」的记录列表，片库里的文件不受影响。",
        "Clears the continue-watching and recently-watched lists; "
        "files in the library are untouched.",
    ),
    "recent_clear_confirm": ("清除", "Clear"),
    "recent_cancel": ("取消", "Cancel"),
    "recent_empty_title": ("还没有播放记录", "No watch history yet"),
    "recent_empty_body": (
        "播放过的视频会按观看时间出现在这里，续播位置也会保留。",
        "Videos you play show up here by watch time; resume positions are kept.",
    ),
    "recent_open_library": ("去片库", "Open library"),
    "recent_time_just": ("刚刚观看", "Just now"),
    "recent_time_today": ("今天 {time}", "Today {time}"),
    "recent_time_yesterday": ("昨天 {time}", "Yesterday {time}"),
    "recent_time_date": ("{month} 月 {day} 日", "{month}/{day}"),
    "recent_time_date_year": (
        "{year} 年 {month} 月 {day} 日",
        "{year}/{month}/{day}",
    ),
    # -- ui-v5 detail page -------------------------------------------------
    "detail_back": ("返回", "Back"),
    "detail_resume": ("从 {time} 继续", "Continue at {time}"),
    "detail_play": ("开始播放", "Play"),
    "detail_replay": ("重新播放", "Replay"),
    "detail_episodes": ("选集", "Episodes"),
    "detail_file_count": ("{count} 个文件", "{count} files"),
    "detail_now_playing": ("正在播放", "Now playing"),
    "detail_auto_next": (
        "播完自动进入下一集",
        "Advances to the next file when done",
    ),
    "detail_foot": (
        "{count} 个文件 · {size} · 全部在本机，无网络请求",
        "{count} files · {size} · all local, no network",
    ),
    "detail_folder": ("同目录", "Same folder"),
    "detail_no_siblings": ("同目录没有其他文件", "No other files in this folder"),
    "detail_spec_title": ("文件参数", "File details"),
    "spec_codec": ("编码", "Codec"),
    "spec_picture": ("画面", "Picture"),
    "spec_fps": ("帧率", "Frame rate"),
    "spec_duration": ("时长", "Duration"),
    "spec_size": ("大小", "Size"),
    "spec_bitrate": ("码率", "Bitrate"),
    "spec_decode": ("解码", "Decode"),
    "spec_decode_hw": ("VPU 硬解", "VPU decode"),
    "spec_decode_sw": ("软件解码", "Software decode"),
    "sidebar_shortcuts": (
        "空格 播放 · F 全屏 · B 美颜",
        "Space play · F fullscreen · B beauty",
    ),
    "info_resolution": ("分辨率", "Resolution"),
    "info_codec": ("编码", "Codec"),
    "info_decode": ("解码", "Decode"),
    "info_fps": ("帧率", "Frame rate"),
    "info_face_off": ("人脸 关", "Face off"),
    "info_face_none": ("人脸 —", "Face —"),
    "info_face_unavailable": ("人脸 不可用", "Face unavailable"),
    "info_face_on": ("人脸 {count} · {ms:.0f} ms", "Face {count} · {ms:.0f} ms"),
    "info_beauty_off": ("美颜 关", "Beauty off"),
    "info_beauty_unavailable": ("美颜 不可用", "Beauty unavailable"),
    "info_beauty_on": ("美颜 开 · {ms:.1f} ms", "Beauty on · {ms:.1f} ms"),
    "recents": ("最近播放", "Recently played"),
    "recents_empty": ("还没有播放记录", "Nothing played yet"),
    "recent_no_duration": ("时长未知", "duration unknown"),
    "recent_missing": ("文件已不存在：{name}", "File no longer exists: {name}"),
    "playlist": ("播放列表", "Playlist"),
    "beauty": ("美颜", "Beauty"),
    "beauty_more": ("更多设置", "More settings"),
    "beauty_more_hint": ("实验性 · 瘦脸 / 大眼", "Experimental · slim / eyes"),
    "beauty_settings": ("设置", "Settings"),
    "beauty_status_on": ("已开启 · GPU {ms:.1f} ms/帧", "On · GPU {ms:.1f} ms/frame"),
    "beauty_status_on_pending": (
        "已开启 · GPU 通道准备中",
        "On · GPU pass preparing",
    ),
    "beauty_status_off": (
        "已关闭 · 分屏与人脸框已关",
        "Off · split and face boxes off",
    ),
    "beauty_cascade": (
        "已同步关闭：A|B 分屏、人脸框、美颜效果。\n"
        "重新打开美颜不会自动恢复这两项。",
        "Also turned off: A|B split, face boxes, beauty effect.\n"
        "Re-enabling beauty does not restore them.",
    ),
    "beauty_shortcuts": (
        "空格 播放 · C 对比 · B 美颜",
        "Space play · C compare · B beauty",
    ),
    "beauty_pill_on": ("美颜 · 开", "Beauty · on"),
    "beauty_pill_off": ("美颜 · 关", "Beauty · off"),
    "compare_needs_beauty": ("对比 · 需美颜", "Compare · needs beauty"),
    "compare_requires_beauty": (
        "先打开美颜，才能使用 A|B 分屏对比。",
        "Turn beauty on to use the A|B split.",
    ),
    "compare_split_hint": (
        "左原图 · 右美颜 · C",
        "Original left · beautified right · C",
    ),
    "show_face_boxes_hint": (
        "四角 L 框 · 置信度",
        "Corner brackets · confidence",
    ),
    "toggle_sidebar": ("收起 / 展开侧栏", "Toggle sidebar"),
    "whitening": ("美白", "Whitening"),
    "compare_original": ("原图", "Original"),
    "compare_split": ("A|B 分屏对比", "A|B split compare"),
    "compare_beautified": ("美颜", "Beautified"),
    "compare_params": (
        "磨皮 {smoothing:.2f} · 美白 {whitening:.2f}",
        "Smoothing {smoothing:.2f} · whitening {whitening:.2f}",
    ),
    "split_position": ("分割位置", "Split position"),
    "show_face_boxes": ("显示人脸框", "Show face boxes"),
    "face_prefix": ("人脸", "Face"),
    "note": (
        "关闭美颜会同步关闭 A|B 分屏与人脸框；重新打开不会自动恢复。",
        "Turning beauty off also closes the A|B split and face boxes; "
        "re-enabling does not restore them.",
    ),
    "experimental": ("实验性", "Experimental"),
    "face_slim": ("瘦脸", "Face slim"),
    "eye_enlarge": ("大眼", "Eye enlarge"),
    "experimental_note": (
        "几何形变只作用于主脸：瘦脸把下半脸沿轮廓整体收窄，"
        "大眼以双眼关键点为心做椭圆放大。移动端 GPU 上属于实验特性。",
        "Geometric warp on the primary face only: slim narrows the whole lower "
        "face along its contour, enlarge magnifies an elliptical region around "
        "the eye centres. Experimental on the mobile GPU.",
    ),
    "play_pause": ("播放 / 暂停（空格）", "Play / pause (space)"),
    "play_subtitles": ("字幕", "Subtitles"),
    "panel_tooltip": ("选集 / 美颜（播放面板）", "Episodes / Beauty (playback panel)"),
    "play_back": ("返回", "Back"),
    "play_more": ("更多", "More"),
    "play_back15": ("后退 15 秒", "Back 15 s"),
    "play_forward15": ("前进 15 秒", "Forward 15 s"),
    "recording_toggle": ("录制 / 停止录制（R）", "Record / stop (R)"),
    "close": ("关闭", "Close"),
    "close_window": ("关闭窗口", "Close window"),
    "player_shortcuts": (
        "空格 播放 · F 全屏 · B 美颜 · C 对比 · Esc 返回",
        "Space play · F fullscreen · B beauty · C compare · Esc back",
    ),
    "telemetry_cpu": ("处理器 {percent:.0f}%", "CPU {percent:.0f}%"),
    "drawer_face_error": ("人脸检测不可用", "Face detection unavailable"),
    "drawer_face_off": ("人脸检测关", "Face detection off"),
    "drawer_face_none": ("人脸检测中…", "Detecting faces…"),
    "drawer_face_count": (
        "NPU 已识别 {count} 张人脸",
        "NPU: {count} face(s)",
    ),
    "stop_playback": ("停止并回到开头（S）", "Stop and rewind (s)"),
    "hint_stopped": ("已停止，点播放从头开始。", "Stopped. Press play to start over."),
    "decode_mode": ("解码方式", "Decode"),
    "decode_auto": ("自动", "Auto"),
    "decode_hw": ("硬解", "HW"),
    "decode_sw": ("软解", "SW"),
    "decode_note": (
        "自动优先 VPU 硬解，失败退回软解；强制档不切换。",
        "Auto prefers the VPU and falls back to software; forced modes never switch.",
    ),
    "decode_staged": (
        "已设为{mode}，打开视频时生效。",
        "Set to {mode}; applies to the next video.",
    ),
    "decode_switched": (
        "已切换为{mode}，当前视频已重新打开。",
        "Switched to {mode}; the clip was reopened.",
    ),
    "decode_fallback": (
        "硬解失败（{message}），已自动改用软解。",
        "Hardware decode failed ({message}); switched to software.",
    ),
    "previous": ("上一项（P）", "Previous item (p)"),
    "next": ("下一项（N）", "Next item (n)"),
    "seek": ("播放进度", "Seek position"),
    "volume": ("音量", "Volume"),
    "speed": ("播放速度", "Playback speed"),
    "fullscreen": ("全屏（F）", "Fullscreen (f)"),
    "exit_fullscreen": ("退出全屏（F）", "Exit fullscreen (f)"),
    "hint_first": ("已经是播放列表第一项。", "This is the first item in the playlist."),
    "hint_last": ("已经是播放列表最后一项。", "This is the last item in the playlist."),
    "hint_finished": ("播放结束。", "Playback finished."),
    "detection_enabling": (
        "正在启用人脸检测（约2秒）",
        "Enabling face detection (~2 s)",
    ),
    "detection_pending": (
        "人脸检测将在切换视频后启用（首次需预热 NPU 约 2 秒）。",
        "Face detection starts on the next clip (one-off NPU warm-up, ~2 s).",
    ),
    "status_loading_title": ("正在读取视频", "Loading video"),
    "status_loading_message": (
        "正在准备本地播放，画面很快就会显示。",
        "Preparing local playback. The picture will appear shortly.",
    ),
    "status_paused_title": ("已暂停", "Paused"),
    "status_paused_message": (
        "点击继续播放，或按空格键。",
        "Click Resume or press Space to continue.",
    ),
    "status_error_title": ("无法播放此视频", "Can't play this video"),
    "status_error_message": (
        "请检查文件是否完整，或尝试其他本地视频。",
        "Check the file or try another local video.",
    ),
    "status_ended_title": ("播放完成", "Playback finished"),
    "status_ended_message": (
        "可以从头重播，或选择列表中的其他视频。",
        "Replay from the beginning or choose another video.",
    ),
    "resume_playback": ("继续播放", "Resume"),
    "resumed_from": ("已从 {time} 继续播放", "Resumed at {time}"),
    "show_subtitles": ("显示字幕", "Show subtitles"),
    "open_subtitle": ("打开字幕文件…", "Open subtitle file…"),
    "subtitle_files": ("字幕文件", "Subtitle files"),
    "subtitle_loaded": (
        "字幕：{name}（{count} 条）",
        "Subtitles: {name} ({count} cues)",
    ),
    "subtitle_empty": (
        "{name} 中没有可用的字幕条目",
        "{name} contains no usable cues",
    ),
    "subtitle_none": (
        "未检测到同名 .srt 字幕",
        "No sidecar .srt found",
    ),
    "audio_track": ("音轨", "Audio track"),
    "audio_track_n": ("音轨 {index}", "Track {index}"),
    "audio_track_none": ("无音轨", "No audio"),
    "retry_playback": ("重新打开", "Try again"),
    "replay_video": ("重新播放", "Replay"),
    "return_to_playlist": ("返回列表", "Back to playlist"),
    "face_detection_disabled": (
        "人脸检测已停用：{message}",
        "Face detection disabled: {message}",
    ),
    "playback_error": ("播放错误：{message}", "Playback error: {message}"),
    "recording_unavailable": (
        "录制不可用：{message}",
        "Recording unavailable: {message}",
    ),
    "recording_saved": ("录制已保存到 {path}", "Recording saved to {path}"),
    "recording_stopped": ("录制已停止：{message}", "Recording stopped: {message}"),
    "recording_not_ready": (
        "窗口未能在录制开始前就绪",
        "the window never became ready for recording",
    ),
    "media_file_not_found": ("找不到媒体文件：{path}", "media file not found: {path}"),
    "open_video": ("打开视频", "Open video"),
    "video_files": ("视频文件", "Video files"),
    "beauty_error": ("美颜不可用：{message}", "Beauty unavailable: {message}"),
    "npu_face": ("{count} 张脸 {ms:.1f} ms", "{count} face {ms:.1f} ms"),
    "npu_faces": ("{count} 张脸 {ms:.1f} ms", "{count} faces {ms:.1f} ms"),
    "smoothing": ("磨皮", "Smoothing"),
    "subtitles_tooltip": ("字幕开关与字幕文件", "Subtitle options"),
    # Only the decoder still needs a human-readable form; the element name
    # itself lives in a tooltip.
    "decode_hw": ("硬解", "HW"),
    "decode_sw": ("软解", "SW"),
}


def settings_path() -> Path:
    """Return the per-user UI settings file (XDG config, never the repo)."""
    base = os.environ.get("XDG_CONFIG_HOME", "")
    root = Path(base) if os.path.isabs(base) else Path.home() / ".config"
    return root / "video-player-demo" / "ui.json"


def load_language() -> str | None:
    """Read the saved UI language; missing or malformed settings are ignored."""
    try:
        raw = settings_path().read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    try:
        data = json.loads(raw)
    except ValueError:
        return None
    language = data.get("language") if isinstance(data, dict) else None
    return language if language in LANGUAGES else None


def save_language(language: str) -> None:
    """Atomically persist *language*; callers may ignore ``OSError``."""
    if language not in LANGUAGES:
        raise ValueError(f"unsupported language: {language!r}")
    path = settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    try:
        temporary.write_text(
            json.dumps({"language": language}) + "\n", encoding="utf-8"
        )
        os.replace(temporary, path)
    except OSError:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
        raise


class Translator:
    """Small runtime translator; ``t("key", **values)`` returns one language."""

    def __init__(self, language: str | None = None) -> None:
        if language is not None:
            if language not in LANGUAGES:
                raise ValueError(f"unsupported language: {language!r}")
            self.language = language
        else:
            self.language = load_language() or DEFAULT_LANGUAGE

    def __call__(self, key: str, **values: object) -> str:
        chinese, english = TEXT[key]
        template = chinese if self.language == "zh" else english
        return template.format(**values)

    def toggle(self) -> str:
        self.language = "en" if self.language == "zh" else "zh"
        return self.language
