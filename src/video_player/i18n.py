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
    "app_title": ("视频播放器 · Quectel Pi M2", "Video Player · Quectel Pi M2"),
    "title_with_media": ("{name} · 视频播放器", "{name} · Video Player"),
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
    "placeholder": (
        "打开视频文件开始播放\n\n"
        "本地 MP4 / MKV H.264 & H.265 文件由 RK3576 VPU 硬解",
        "Open a video file to start playback\n\n"
        "Local MP4 / MKV H.264 & H.265 files are decoded on the RK3576 VPU",
    ),
    "info_placeholder": (
        "分辨率 —   编码 —   解码 —   帧率 —   NPU —",
        "resolution —   codec —   decode —   fps —   NPU —",
    ),
    "playlist": ("播放列表", "Playlist"),
    "beauty": ("美颜", "Beauty"),
    "auto_beauty": ("自动人脸美颜", "Automatic face beauty"),
    "smoothing": ("磨皮", "Smoothing"),
    "whitening": ("美白", "Whitening"),
    "original_beautified": ("原图 / 美颜", "Original / beautified"),
    "split_position": ("分割位置", "Split position"),
    "show_face_boxes": ("显示人脸框", "Show face boxes"),
    "note": (
        "磨皮和美白由 GPU 着色器完成，并限制在 NPU 检测器给出的羽化人脸"
        "遮罩内。快捷键：B 美颜、C 对比、M 遮罩预览。",
        "Smoothing and whitening run in the GPU shader, limited to the "
        "feathered face mask from the NPU detector. Keys: B beauty, C compare, "
        "M mask preview.",
    ),
    "experimental": ("实验性", "Experimental"),
    "face_slim": ("瘦脸", "Face slim"),
    "eye_enlarge": ("大眼", "Eye enlarge"),
    "experimental_note": (
        "五点关键点驱动的局部几何形变，只作用于主脸；瘦脸收窄下颌线，"
        "大眼放大眼睛。移动端 GPU 上属于实验特性。",
        "Local geometric warp driven by the five face landmarks; applies to "
        "the primary face only. Slim narrows the jawline, enlarge magnifies "
        "the eyes. Experimental on the mobile GPU.",
    ),
    "play_pause": ("播放 / 暂停（空格）", "Play / pause (space)"),
    "previous": ("上一项（P）", "Previous item (p)"),
    "next": ("下一项（N）", "Next item (n)"),
    "seek": ("播放进度", "Seek position"),
    "volume": ("音量", "Volume"),
    "speed": ("播放速度", "Playback speed"),
    "fullscreen": ("全屏（F）", "Fullscreen (f)"),
    "hint_open": (
        "打开视频开始播放。传入文件夹会作为播放列表。",
        "Open a video to start. Folder arguments become a playlist.",
    ),
    "hint_first": ("已经是播放列表第一项。", "This is the first item in the playlist."),
    "hint_last": ("已经是播放列表最后一项。", "This is the last item in the playlist."),
    "hint_finished": ("播放结束。", "Playback finished."),
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
    "beauty_params": (
        "美颜 {state}（磨皮 {smoothing:.2f}，美白 {whitening:.2f}{warp}）{extra}",
        "beauty {state} (smoothing {smoothing:.2f}, whitening {whitening:.2f}"
        "{warp}){extra}",
    ),
    "beauty_warp": ("，瘦脸 {slim:.0%}，大眼 {eyes:.0%}", ", slim {slim:.0%}, eyes {eyes:.0%}"),
    "beauty_split": ("，分割 {split:.0%}", ", split {split:.0%}"),
    "beauty_ms": ("，{ms:.1f} 毫秒/帧", ", {ms:.1f} ms/frame"),
    "beauty_gpu_unavailable": ("，GPU 通道不可用", ", GPU pass unavailable"),
    "beauty_error": ("美颜不可用：{message}", "Beauty unavailable: {message}"),
    "beauty_unavailable": ("不可用", "unavailable"),
    "beauty_state_on": ("开", "on"),
    "beauty_state_off": ("关", "off"),
    "beauty_on": ("开 {ms:.1f} 毫秒", "on {ms:.1f} ms"),
    "npu_off": ("关", "off"),
    "npu_face": ("{count} 张人脸 · {ms:.1f} 毫秒", "{count} face · {ms:.1f} ms"),
    "npu_faces": ("{count} 张人脸 · {ms:.1f} 毫秒", "{count} faces · {ms:.1f} ms"),
    "info_line": (
        "{width}×{height}   {codec}   解码：{decoder}   帧率：{fps}   "
        "NPU：{npu}   美颜：{beauty}",
        "{width}×{height}   {codec}   decode: {decoder}   fps: {fps}   "
        "NPU: {npu}   beauty: {beauty}",
    ),
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
