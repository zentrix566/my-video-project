"""
增强版剪映草稿合成器 — 独立使用，不修改原 jianying_draft_composer.py。

相比原版的改进：
1. 进度回调 — 可以接进度条、日志等任意上报
2. 预检模式 — 不创建草稿，只验证所有输入
3. 智能断句 — 同时处理中英文标点
4. 片段转场 — 支持 fade_in / fade_out 效果
5. 跨平台兼容 — Windows 下安全跳过 chmod
6. 结构化为类 — 可继承、可扩展

使用方式（完全兼容原接口）：
    from improvements.composer_v2 import JianyingDraftBuilder

    builder = JianyingDraftBuilder(
        draft_name="我的视频",
        draft_folder_path="D:/JianyingPro Drafts",
        on_progress=lambda msg, pct: print(f"[{pct}%] {msg}"),
    )
    result = builder.build(
        subtitle_texts=["第一句", "第二句"],
        audio_paths=["a1.mp3", "a2.mp3"],
        media_paths=["img1.png", "img2.png"],
    )
"""

from __future__ import annotations

import inspect
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Literal

import pyJianYingDraft as draft
from pyJianYingDraft import (
    TrackType, TextStyle, ClipSettings, TextBackground,
    KeyframeProperty, trange, TransitionType,
)
from PIL import Image

from pipeline.subtitles import (
    SubtitlePreset, resolve_subtitle_preset,
)


ProgressFn = Callable[[str, int], None]


_smart_separators = re.compile(r'([，。！,!?；：:、\n])')


def smart_split(text: str) -> list[str]:
    """已迁移至 pipeline.helpers.smart_split；此处保留代理以兼容老调用。"""
    from pipeline.helpers import smart_split as _shared
    return _shared(text)


def is_image_file(path: str) -> bool:
    return Path(path).suffix.lower() in {'.png', '.jpg', '.jpeg', '.bmp', '.webp'}


def is_windows() -> bool:
    return sys.platform == "win32"


def cover_scale_for_canvas(canvas_width: int, canvas_height: int, media_width: int, media_height: int) -> float:
    """按剪映“先完整适配进画布”的行为，计算再次放大到铺满画布所需的倍率。"""
    if canvas_width <= 0 or canvas_height <= 0 or media_width <= 0 or media_height <= 0:
        return 1.0
    canvas_ratio = canvas_width / canvas_height
    media_ratio = media_width / media_height
    if media_ratio < canvas_ratio:
        return canvas_ratio / media_ratio
    return media_ratio / canvas_ratio


class CameraEffect:
    """诗词流水线专用运镜效果，任何时刻都保证图片铺满画布。"""

    # 缩放运镜安全边距：cover_scale 已按剪映“适配后再放大”语义计算，
    # 这里只保留少量冗余，既铺满画布，又不会把全局画面裁得太狠。
    _ZOOM_MIN = 1.05
    _ZOOM_MAX = 1.25

    # 横向移动安全缩放：古诗更重“看完整画面”，所以只做轻微横移。
    # 过大的平移会迫使图片大幅放大，导致只看到局部。
    _PAN_SCALE = 1.18
    _PAN_OFFSET = 0.08

    @staticmethod
    def zoom_out_full(seg, duration: int, base_scale: float = 1.0) -> None:
        """全局逐渐缩小：从近景慢慢拉远到全局，但末帧仍铺满画布。"""
        seg.add_keyframe(KeyframeProperty.uniform_scale, 0, base_scale * CameraEffect._ZOOM_MAX)
        seg.add_keyframe(KeyframeProperty.uniform_scale, duration, base_scale * CameraEffect._ZOOM_MIN)
        for prop in (KeyframeProperty.position_x, KeyframeProperty.position_y):
            seg.add_keyframe(prop, 0, 0)
            seg.add_keyframe(prop, duration, 0)

    @staticmethod
    def zoom_in_full(seg, duration: int, base_scale: float = 1.0) -> None:
        """缩小逐渐放大：从全局慢慢推近到中心细节，起帧也铺满画布。"""
        seg.add_keyframe(KeyframeProperty.uniform_scale, 0, base_scale * CameraEffect._ZOOM_MIN)
        seg.add_keyframe(KeyframeProperty.uniform_scale, duration, base_scale * CameraEffect._ZOOM_MAX)
        for prop in (KeyframeProperty.position_x, KeyframeProperty.position_y):
            seg.add_keyframe(prop, 0, 0)
            seg.add_keyframe(prop, duration, 0)

    @staticmethod
    def pan_left_to_right(seg, duration: int, base_scale: float = 1.0) -> None:
        """镜头从左到右：画面从左侧开始，慢慢向右展示。"""
        scale = base_scale * CameraEffect._PAN_SCALE
        seg.add_keyframe(KeyframeProperty.uniform_scale, 0, scale)
        seg.add_keyframe(KeyframeProperty.uniform_scale, duration, scale)
        seg.add_keyframe(KeyframeProperty.position_x, 0, -CameraEffect._PAN_OFFSET)
        seg.add_keyframe(KeyframeProperty.position_x, duration, CameraEffect._PAN_OFFSET)
        seg.add_keyframe(KeyframeProperty.position_y, 0, 0)
        seg.add_keyframe(KeyframeProperty.position_y, duration, 0)

    @staticmethod
    def pan_right_to_left(seg, duration: int, base_scale: float = 1.0) -> None:
        """镜头从右到左：画面从右侧开始，慢慢向左展示。"""
        scale = base_scale * CameraEffect._PAN_SCALE
        seg.add_keyframe(KeyframeProperty.uniform_scale, 0, scale)
        seg.add_keyframe(KeyframeProperty.uniform_scale, duration, scale)
        seg.add_keyframe(KeyframeProperty.position_x, 0, CameraEffect._PAN_OFFSET)
        seg.add_keyframe(KeyframeProperty.position_x, duration, -CameraEffect._PAN_OFFSET)
        seg.add_keyframe(KeyframeProperty.position_y, 0, 0)
        seg.add_keyframe(KeyframeProperty.position_y, duration, 0)

    # 兼容旧代码/旧配置里的名字
    zoom_out = zoom_out_full
    zoom_in = zoom_in_full
    pan_left = pan_left_to_right
    pan_right = pan_right_to_left

    ALL = [zoom_out_full, zoom_in_full, pan_left_to_right, pan_right_to_left]


_ci = CameraEffect
POEM_CAMERA_PRESETS: dict[str, list] = {
    "all": _ci.ALL,
    "zoom": [_ci.zoom_out_full, _ci.zoom_in_full],
    "horizontal": [_ci.pan_left_to_right, _ci.pan_right_to_left],
    "mixed": _ci.ALL,
    # 兼容旧配置：pan 在诗词流水线里只保留左右横向移动
    "pan": [_ci.pan_left_to_right, _ci.pan_right_to_left],
}
del _ci


@dataclass
class SentenceInfo:
    """一段 narration 内部的一个子句（对应一个独立 TTS mp3）。

    提供 SentenceInfo 列表给 SegmentInfo.sentences 后，draft_composer 会用**每个子句
    真实的 audio_duration** 精确对齐字幕，而不是靠字符比例估算。
    """
    text: str        # 已去除尾部标点的干净字幕文本
    audio_path: str  # 该子句独立合成出来的 mp3 路径
    target_duration_us: int | None = None  # 可选：LLM 给的目标时长（微秒），用于按比例分配


@dataclass
class SegmentInfo:
    subtitle: str
    audio_path: str
    media_path: str
    sentences: list[SentenceInfo] | None = None   # 有值时启用逐句同步


@dataclass
class PreflightReport:
    """预检报告"""
    passed: bool = False
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    total_segments: int = 0
    estimated_canvas: tuple[int, int] | None = None
    is_landscape: bool | None = None
    missing_files: list[str] = field(default_factory=list)
    draft_exists: bool = False


class JianyingDraftBuilder:
    """增强版剪映草稿构建器

    所有参数都在构造器里设一次，之后多次 build() 复用。
    """

    def __init__(
        self,
        draft_name: str,
        draft_folder_path: str,
        *,
        on_progress: ProgressFn | None = None,
        add_image_movement: bool = True,
        add_video_movement: bool = True,
        split_subtitles: bool = True,
        fade_transition: bool = True,
        mute_original_video: bool = False,
        crop_video: bool = False,
        preserve_video_duration: bool = False,
        tts_overshoot: Literal["slow_video", "speed_audio"] = "slow_video",
        tts_overshoot_max_speed: float = 1.8,
        background_image: str | None = None,
        background_music: str | None = None,
        camera_effects: list | None = None,
        canvas_width: int | None = None,
        canvas_height: int | None = None,
        subtitle_preset: str | dict | SubtitlePreset | None = None,
        transition_duration: int | str | None = "0.5s",
    ):
        self.draft_name = draft_name
        self.draft_folder_path = draft_folder_path
        self.on_progress = on_progress
        self.add_image_movement = add_image_movement
        self.add_video_movement = add_video_movement
        self.split_subtitles = split_subtitles
        self.fade_transition = fade_transition
        self.mute_original_video = mute_original_video
        self.crop_video = crop_video
        self.preserve_video_duration = preserve_video_duration
        self.tts_overshoot = tts_overshoot
        self.tts_overshoot_max_speed = tts_overshoot_max_speed
        self.background_image = background_image
        self.background_music = background_music
        self.camera_effects = camera_effects if camera_effects is not None else CameraEffect.ALL
        self.canvas_width = canvas_width
        self.canvas_height = canvas_height
        self.transition_duration = transition_duration
        # 字幕预设：传 None 时用 default，行为和接入前完全一致
        self.subtitle_preset: SubtitlePreset = resolve_subtitle_preset(subtitle_preset)

    def _progress(self, msg: str, pct: int) -> None:
        if self.on_progress:
            self.on_progress(msg, pct)

    @staticmethod
    def _validate_lengths(segments: list[SegmentInfo]) -> None:
        """校验片段数量（预检已做详细检查，此处仅做最后防御）"""
        if not segments:
            raise ValueError("segments 不能为空")

    # ---- 预检 ----

    def preflight(self, segments: list[SegmentInfo]) -> PreflightReport:
        """预检模式：验证所有输入，不创建草稿"""
        report = PreflightReport(total_segments=len(segments))

        if not self.draft_name.strip():
            report.errors.append("草稿名为空")

        folder = Path(self.draft_folder_path)
        if not folder.exists():
            report.errors.append(f"草稿文件夹不存在: {self.draft_folder_path}")

        draft_folder = draft.DraftFolder(self.draft_folder_path)
        if draft_folder.has_draft(self.draft_name):
            report.draft_exists = True
            report.warnings.append(f"草稿 '{self.draft_name}' 已存在，将被覆盖")

        # 检查素材文件
        for i, seg in enumerate(segments, start=1):
            # 视频/图片素材永远要检查
            if not os.path.exists(seg.media_path):
                report.missing_files.append(f"segments[{i}].媒体: {seg.media_path}")

            # 音频：若有逐句列表，检查每一句；否则检查单条 audio_path
            if seg.sentences:
                for j, s in enumerate(seg.sentences):
                    if not os.path.exists(s.audio_path):
                        report.missing_files.append(
                            f"segments[{i}].sentences[{j}]: {s.audio_path}"
                        )
            else:
                if not os.path.exists(seg.audio_path):
                    report.missing_files.append(f"segments[{i}].音频: {seg.audio_path}")

        if report.missing_files:
            report.errors.append(f"缺少 {len(report.missing_files)} 个文件")

        # 预估画布
        if segments:
            first_media = segments[0].media_path
            if is_image_file(first_media):
                try:
                    with Image.open(first_media) as img:
                        w, h = img.size
                        report.estimated_canvas = (w, h)
                        report.is_landscape = w > h
                except Exception as exc:
                    report.warnings.append(f"无法读取第一张图片尺寸: {exc}")
            else:
                try:
                    mat = draft.VideoMaterial(first_media)
                    report.estimated_canvas = (mat.width, mat.height)
                    report.is_landscape = mat.width > mat.height
                except Exception as exc:
                    report.warnings.append(f"无法读取第一个视频尺寸: {exc}")

        if self.background_image and not os.path.exists(self.background_image):
            report.errors.append(f"背景图片不存在: {self.background_image}")
        if self.background_music and not os.path.exists(self.background_music):
            report.errors.append(f"背景音乐不存在: {self.background_music}")

        report.passed = len(report.errors) == 0
        return report

    # ---- 构建 ----

    def build(
        self,
        subtitle_texts: list[str],
        audio_paths: list[str],
        media_paths: list[str],
    ) -> str:
        segments = [
            SegmentInfo(sub, aud, med)
            for sub, aud, med in zip(subtitle_texts, audio_paths, media_paths)
        ]
        return self._build(segments)

    def build_from_segments(self, segments: list[SegmentInfo]) -> str:
        return self._build(segments)

    def _build(self, segments: list[SegmentInfo]) -> str:
        self._progress("开始构建草稿", 0)
        self._validate_lengths(segments)
        self._progress("参数校验通过", 5)

        # 画布尺寸：优先使用配置值，否则从素材推断
        all_images = all(is_image_file(s.media_path) for s in segments)
        if self.canvas_width and self.canvas_height:
            width, height = self.canvas_width, self.canvas_height
        elif all_images:
            with Image.open(segments[0].media_path) as img:
                width, height = img.size
        else:
            first_video = next(s.media_path for s in segments if not is_image_file(s.media_path))
            vm = draft.VideoMaterial(first_video)
            width, height = vm.width, vm.height

        is_landscape = width > height
        font_size = 6.0 if is_landscape else 13.0
        subtitle_y = -0.8 if is_landscape else -0.3
        subtitle_kwargs = self.subtitle_preset.to_text_segment_kwargs(is_landscape=is_landscape)
        self._progress(f"画布: {width}x{height}", 10)

        # 创建草稿（允许覆盖已存在的同名草稿）
        df = draft.DraftFolder(self.draft_folder_path)
        if df.has_draft(self.draft_name):
            self._progress("覆盖已有草稿", 9)

        script = df.create_draft(self.draft_name, width, height, allow_replace=True)

        # 轨道
        tb = script.add_track(TrackType.video, "媒体轨道") \
                  .add_track(TrackType.audio, "音频轨道") \
                  .add_track(TrackType.text, "字幕轨道")
        if self.background_image:
            tb.add_track(TrackType.video, "背景图片轨道", relative_index=3)
        if self.background_music:
            tb.add_track(TrackType.audio, "背景音乐轨道")
        self._progress("轨道创建完毕", 15)

        total_segments = len(segments)
        current_time = 0
        total_duration = 0
        previous_media_seg = None

        for i, seg in enumerate(segments):
            base_pct = 15 + int(75 * i / total_segments)
            self._progress(f"处理片段 {i + 1}/{total_segments}", base_pct)

            # ---- 音频总时长 ----
            # 若 seg.sentences 有值：一段 narration 被拆成多个子句、每个子句一个独立 mp3。
            #   总音频时长 = Σ 子句时长；后续会**逐句**加进音频轨与字幕轨，字幕严格对齐 TTS 真实节奏。
            # 否则走老路径：整段 narration 一个 mp3、字幕靠字符比例估算。
            if seg.sentences:
                sentence_materials = [
                    draft.AudioMaterial(s.audio_path) for s in seg.sentences
                ]
                sentence_durations = [am.duration for am in sentence_materials]
                audio_duration = sum(sentence_durations)
            else:
                sentence_materials = None
                sentence_durations = None
                audio_material = draft.AudioMaterial(seg.audio_path)
                audio_duration = audio_material.duration

            # ---- 决定这一段在时间线上的总长 ----
            # preserve_video_duration 模式：视频原速（默认）；TTS 比视频段长时根据
            # tts_overshoot 策略决定是"慢放视频"还是"加速音频卡进视频段"。
            is_img = is_image_file(seg.media_path)
            audio_speed: float = 1.0  # 音频加速倍率（仅 speed_audio 策略下 >1）
            if self.preserve_video_duration and not is_img:
                vm = draft.VideoMaterial(seg.media_path)
                video_duration = vm.duration

                if audio_duration <= video_duration:
                    # TTS ≤ 视频段：视频原速原长，音频讲完静音
                    scene_duration = video_duration
                    speed = 1.0
                else:
                    # TTS 讲得比视频段长：根据策略处理
                    if self.tts_overshoot == "speed_audio":
                        raw_audio_speed = audio_duration / video_duration  # > 1
                        if raw_audio_speed <= self.tts_overshoot_max_speed:
                            # 新行为：视频原速原长，音频统一加速 raw_audio_speed 倍（不变调）卡进段内
                            scene_duration = video_duration
                            speed = 1.0
                            audio_speed = raw_audio_speed
                            speed_pct = round((audio_speed - 1) * 100, 1)
                            self._progress(
                                f"⚡ 片段 {i + 1}: TTS {audio_duration/1_000_000:.2f}s > 视频段 "
                                f"{video_duration/1_000_000:.2f}s，音频加速 {audio_speed:.2f}x（+{speed_pct}%）不变调卡进视频段",
                                base_pct,
                            )
                        else:
                            # 加速倍数过大（>1.8x 会太突兀），回退慢放视频
                            scene_duration = audio_duration
                            speed = video_duration / audio_duration
                            slowdown_pct = round((1 - speed) * 100, 1)
                            self._progress(
                                f"⚠ 片段 {i + 1}: TTS 需加速 {raw_audio_speed:.2f}x 超上限 "
                                f"{self.tts_overshoot_max_speed}x，回退视频慢放 {slowdown_pct}%",
                                base_pct,
                            )
                    else:
                        # 旧行为：慢放视频匹配 TTS
                        scene_duration = audio_duration
                        speed = video_duration / audio_duration
                        slowdown_pct = round((1 - speed) * 100, 1)
                        self._progress(
                            f"⚠ 片段 {i + 1}: TTS {audio_duration/1_000_000:.2f}s > 视频段 "
                            f"{video_duration/1_000_000:.2f}s，视频慢放 {slowdown_pct}% 匹配 TTS，"
                            f"讲解将完整保留",
                            base_pct,
                        )

                video_time_range = trange(current_time, scene_duration)

                vs_kwargs: dict[str, Any] = {}
                if speed != 1.0:
                    vs_kwargs["speed"] = speed
                if self.mute_original_video:
                    vs_kwargs["volume"] = 0.0
                media_seg = draft.VideoSegment(seg.media_path, video_time_range, **vs_kwargs)

                # 音频入轨：逐句 or 单段。
                # 统一加速时，每句的目标时长 = 它在整段 TTS 中的比例 × 场景总时长（video_duration 或 audio_duration），
                # 避免用 round(s_dur / speed) 累积尾差导致总长 ≠ scene_duration 而被判重叠。
                if sentence_materials is not None:
                    # 计算每句在段内的时长比例
                    total_audio = sum(sentence_durations)  # μs
                    # 音频总目标时长 = scene_duration（=video_duration（speed_audio）或 audio_duration（slow_video回退））
                    total_segment_time = scene_duration
                    sub_offset = current_time
                    for idx, (s_info, s_dur) in enumerate(zip(seg.sentences, sentence_durations)):
                        remaining = (current_time + scene_duration) - sub_offset
                        if remaining <= 0:
                            break  # 已超出段边界，不再放置更多音频
                        if idx < len(seg.sentences) - 1:
                            ratio = s_dur / max(total_audio, 1)
                            target_dur = min(remaining, max(1, int(round(total_segment_time * ratio))))
                        else:
                            # 最后一句：精确填充剩下的时间
                            target_dur = remaining
                        script.add_segment(
                            draft.AudioSegment(
                                s_info.audio_path,
                                trange(sub_offset, target_dur),
                                source_timerange=trange(0, s_dur),
                            ),
                            "音频轨道",
                        )
                        sub_offset += target_dur
                else:
                    if audio_speed != 1.0:
                        scaled_dur = int(round(audio_duration / audio_speed))
                        script.add_segment(
                            draft.AudioSegment(
                                seg.audio_path,
                                trange(current_time, scaled_dur),
                                source_timerange=trange(0, audio_duration),
                            ),
                            "音频轨道",
                        )
                    else:
                        script.add_segment(
                            draft.AudioSegment(seg.audio_path, trange(current_time, audio_duration)),
                            "音频轨道",
                        )
                add_movement = self.add_video_movement
                time_range = video_time_range  # 供下游位移/转场计算使用
            else:
                time_range = trange(current_time, audio_duration)
                scene_duration = audio_duration
                # 音频入轨：逐句 or 单段
                if sentence_materials is not None:
                    sub_offset = current_time
                    for s_info, s_dur in zip(seg.sentences, sentence_durations):
                        script.add_segment(
                            draft.AudioSegment(s_info.audio_path, trange(sub_offset, s_dur)),
                            "音频轨道",
                        )
                        sub_offset += s_dur
                else:
                    script.add_segment(draft.AudioSegment(seg.audio_path, time_range), "音频轨道")

                # 媒体
                if is_img:
                    media_seg = draft.VideoSegment(seg.media_path, time_range)
                    add_movement = self.add_image_movement
                else:
                    vm = draft.VideoMaterial(seg.media_path)
                    video_duration = vm.duration
                    if self.crop_video and audio_duration < video_duration:
                        kwargs = {"source_timerange": trange(0, audio_duration)}
                        if self.mute_original_video:
                            kwargs["volume"] = 0.0
                        media_seg = draft.VideoSegment(seg.media_path, time_range, **kwargs)
                    else:
                        speed = video_duration / audio_duration
                        kwargs = {"speed": speed}
                        if self.mute_original_video:
                            kwargs["volume"] = 0.0
                        media_seg = draft.VideoSegment(seg.media_path, time_range, **kwargs)
                    add_movement = self.add_video_movement

            media_seg.clip_settings = ClipSettings(transform_x=0, transform_y=0)

            # 计算 cover_scale：图片刚好铺满画布的最小缩放比
            # 缩放 ≥ cover_scale 时，任意位移都不会露黑边
            cover_scale = 1.0
            if is_img:
                with Image.open(seg.media_path) as _img:
                    _img_w, _img_h = _img.size
                cover_scale = cover_scale_for_canvas(width, height, _img_w, _img_h)

            if add_movement:
                effect = self.camera_effects[i % len(self.camera_effects)]
                effect(media_seg, scene_duration, base_scale=cover_scale)
            else:
                # 静止画面也保留一点安全缩放，避免刚好卡在 cover_scale 临界值露边
                safe_scale = cover_scale * 1.02
                media_seg.add_keyframe(KeyframeProperty.uniform_scale, 0, safe_scale)
                for _p in (KeyframeProperty.position_x, KeyframeProperty.position_y):
                    media_seg.add_keyframe(_p, 0, 0)
                    media_seg.add_keyframe(_p, scene_duration, 0)

            # pyJianYingDraft 的转场要加在“前一个片段”上，才会作用于前后片段之间
            # 为避免库在 add_segment 时复制对象，先给上一段加转场，再写入轨道。
            if previous_media_seg is not None:
                if self.fade_transition:
                    previous_media_seg.add_transition(TransitionType.叠化, duration=self.transition_duration)
                script.add_segment(previous_media_seg, "媒体轨道")
            previous_media_seg = media_seg

            # ---- 字幕入轨 ----
            # 用比例分配保证总长严格等于 scene_duration，避免 round() 累积尾差导致重叠。
            if sentence_materials is not None:
                sub_offset = current_time
                total_audio = sum(sentence_durations)
                scene_end = current_time + scene_duration
                for idx, (s_info, s_dur) in enumerate(zip(seg.sentences, sentence_durations)):
                    remaining = scene_end - sub_offset
                    if remaining <= 0:
                        break
                    txt = s_info.text.strip() or _smart_separators.sub('', s_info.text).strip()
                    if idx < len(seg.sentences) - 1:
                        ratio = s_dur / max(total_audio, 1)
                        text_dur = min(remaining, max(1, int(round(scene_duration * ratio))))
                    else:
                        text_dur = remaining
                    text_seg = draft.TextSegment(
                        txt, trange(sub_offset, text_dur), **subtitle_kwargs,
                    )
                    script.add_segment(text_seg, "字幕轨道")
                    sub_offset += text_dur
            elif self.split_subtitles:
                sentences = smart_split(seg.subtitle)
                total_len = max(len(seg.subtitle), 1)
                sub_time = current_time
                # 字幕总时长：加速音频时 = scene_duration，否则 = audio_duration
                subtitle_total = scene_duration if audio_speed != 1.0 else audio_duration
                for sentence in sentences:
                    ratio = len(sentence) / total_len
                    sub_dur = max(int(subtitle_total * ratio), 100000)
                    txt_clean = _smart_separators.sub('', sentence).strip() or sentence
                    text_seg = draft.TextSegment(
                        txt_clean,
                        trange(sub_time, sub_dur),
                        **subtitle_kwargs,
                    )
                    script.add_segment(text_seg, "字幕轨道")
                    sub_time += sub_dur
            else:
                text_seg = draft.TextSegment(
                    seg.subtitle, time_range,
                    **subtitle_kwargs,
                )
                script.add_segment(text_seg, "字幕轨道")

            current_time += scene_duration
            total_duration = current_time

        if previous_media_seg is not None:
            script.add_segment(previous_media_seg, "媒体轨道")

        # 背景图片
        if self.background_image:
            self._progress("添加背景图片", 92)
            script.add_segment(
                draft.VideoSegment(self.background_image, trange(0, total_duration)),
                "背景图片轨道",
            )

        # 背景音乐
        if self.background_music:
            self._progress("添加背景音乐", 95)
            music_dur = draft.AudioMaterial(self.background_music).duration
            cur = 0
            while cur < total_duration:
                seg_dur = min(music_dur, total_duration - cur)
                script.add_segment(
                    draft.AudioSegment(
                        self.background_music,
                        trange(cur, seg_dur),
                        volume=0.3,
                        source_timerange=trange(0, seg_dur),
                    ),
                    "背景音乐轨道",
                )
                cur += seg_dur

        self._progress("保存草稿", 98)
        script.save()
        self._progress("完成", 100)

        return f"剪映草稿 '{self.draft_name}' 创建成功！"

    # ---- 标题模式 ----

    def build_with_title(
        self,
        title_text: str,
        author_text: str,
        title_audio_path: str,
        title_media_path: str,
        poem_segments: list[SegmentInfo],
    ) -> str:
        """带标题卡的构建模式。

        轨道布局：
          Track 1: 媒体轨道（标题背景 → 诗句配图）
          Track 2: 音频轨道（标题朗读 → 诗句朗读）
          Track 3: 标题轨道（居中大字，无底框）
          Track 4: 字幕轨道（底部小字，半透明黑底）

        标题和诗句使用两条独立的 Text Track，
        标题可以单独调整样式而不影响字幕。
        """
        self._progress("开始构建草稿（标题模式）", 0)

        # --- 确定画布 ---
        if self.canvas_width and self.canvas_height:
            width, height = self.canvas_width, self.canvas_height
        else:
            all_media = [title_media_path] + [s.media_path for s in poem_segments]
            first_img = next(
                (p for p in all_media if is_image_file(p)),
                all_media[0],
            )
            with Image.open(first_img) as img:
                width, height = img.size
        is_landscape = width > height

        # 字号
        title_font_size = 12.0 if is_landscape else 24.0
        author_font_size = 6.0 if is_landscape else 12.0
        subtitle_kwargs = self.subtitle_preset.to_text_segment_kwargs(is_landscape=is_landscape)

        self._progress(f"画布: {width}x{height}", 5)

        # --- 标题音频时长 ---
        title_duration = draft.AudioMaterial(title_audio_path).duration
        self._progress(f"标题时长: {title_duration / 1_000_000:.1f}s", 8)

        # --- 创建草稿（允许覆盖） ---
        df = draft.DraftFolder(self.draft_folder_path)
        if df.has_draft(self.draft_name):
            self._progress("覆盖已有草稿", 4)

        script = df.create_draft(self.draft_name, width, height, allow_replace=True)

        # --- 建轨道 ---
        script.add_track(TrackType.video, "媒体轨道") \
              .add_track(TrackType.audio, "音频轨道") \
              .add_track(TrackType.text, "标题轨道") \
              .add_track(TrackType.text, "字幕轨道")
        self._progress("轨道创建完毕", 10)

        # ============================================================
        #  标题卡
        # ============================================================
        self._progress("插入标题卡", 12)

        title_range = trange(0, title_duration)

        # 背景图（微缩放入场）
        title_img = draft.VideoSegment(title_media_path, title_range)
        title_img.clip_settings = ClipSettings(transform_x=0, transform_y=0)
        title_img.add_keyframe(KeyframeProperty.uniform_scale, 0, 1.0)
        title_img.add_keyframe(KeyframeProperty.uniform_scale, time_offset=title_duration, value=1.08)
        script.add_segment(title_img, "媒体轨道")

        # 音频
        script.add_segment(draft.AudioSegment(title_audio_path, title_range), "音频轨道")

        # 标题文字（大字，居中偏上）
        script.add_segment(
            draft.TextSegment(
                title_text, title_range,
                font=draft.FontType.文轩体,
                style=TextStyle(
                    color=(1.0, 1.0, 1.0), size=title_font_size,
                    align=1, auto_wrapping=True, max_line_width=0.8,
                ),
                clip_settings=ClipSettings(transform_y=-0.10),
            ),
            "标题轨道",
        )

        # 作者文字（小字，居中偏下）
        if author_text:
            script.add_segment(
                draft.TextSegment(
                    author_text, title_range,
                    font=draft.FontType.文轩体,
                    style=TextStyle(
                        color=(0.85, 0.85, 0.85), size=author_font_size,
                        align=1, auto_wrapping=True, max_line_width=0.8,
                    ),
                    clip_settings=ClipSettings(transform_y=0.15),
                ),
                "标题轨道",
            )

        # ============================================================
        #  诗句
        # ============================================================
        current_time = title_duration
        total = len(poem_segments)
        previous_media_seg = title_img

        for i, seg in enumerate(poem_segments, start=1):
            base_pct = 15 + int(75 * i / total)
            self._progress(f"标题模式片段 {i}/{total}", base_pct)

            audio_material = draft.AudioMaterial(seg.audio_path)
            audio_duration = audio_material.duration
            time_range = trange(current_time, audio_duration)

            # 音频
            script.add_segment(draft.AudioSegment(seg.audio_path, time_range), "音频轨道")

            # 媒体
            is_img = is_image_file(seg.media_path)
            media_seg = draft.VideoSegment(seg.media_path, time_range)
            media_seg.clip_settings = ClipSettings(transform_x=0, transform_y=0)

            cover_scale = 1.0
            if is_img:
                with Image.open(seg.media_path) as _img:
                    _img_w, _img_h = _img.size
                cover_scale = cover_scale_for_canvas(width, height, _img_w, _img_h)

            if (is_img and self.add_image_movement) or (not is_img and self.add_video_movement):
                effect = self.camera_effects[(i - 1) % len(self.camera_effects)]
                effect(media_seg, audio_duration, base_scale=cover_scale)
            else:
                media_seg.add_keyframe(KeyframeProperty.uniform_scale, 0, cover_scale * 1.02)
                for _p in (KeyframeProperty.position_x, KeyframeProperty.position_y):
                    media_seg.add_keyframe(_p, 0, 0)
                    media_seg.add_keyframe(_p, audio_duration, 0)

            if self.fade_transition and previous_media_seg is not None:
                previous_media_seg.add_transition(TransitionType.叠化, duration=self.transition_duration)

            script.add_segment(media_seg, "媒体轨道")
            previous_media_seg = media_seg

            # 字幕
            if self.split_subtitles:
                sentences = smart_split(seg.subtitle)
                total_len = max(len(seg.subtitle), 1)
                sub_time = current_time
                for sentence in sentences:
                    ratio = len(sentence) / total_len
                    sub_dur = max(int(audio_duration * ratio), 100000)
                    txt_clean = _smart_separators.sub('', sentence).strip() or sentence
                    script.add_segment(
                        draft.TextSegment(
                            txt_clean, trange(sub_time, sub_dur),
                            **subtitle_kwargs,
                        ),
                        "字幕轨道",
                    )
                    sub_time += sub_dur
            else:
                script.add_segment(
                    draft.TextSegment(
                        seg.subtitle, time_range,
                        **subtitle_kwargs,
                    ),
                    "字幕轨道",
                )

            current_time += audio_duration

        self._progress("保存草稿", 98)
        script.save()
        self._progress("完成（标题模式）", 100)

        return f"剪映草稿 '{self.draft_name}' 创建成功！（标题卡 + {total} 句诗）"


def create_jianying_draft_with_media_v2(
    draft_name: str,
    subtitle_texts: list[str],
    audio_paths: list[str],
    media_paths: list[str],
    draft_folder_path: str,
    add_image_movement: bool = True,
    add_video_movement: bool = True,
    split_subtitles: bool = True,
    mute_original_video: bool = False,
    crop_video: bool = False,
    background_image: str | None = None,
    background_music: str | None = None,
    on_progress: ProgressFn | None = None,
    fade_transition: bool = True,
) -> str:
    """与原版函数签名对齐的入口"""
    builder = JianyingDraftBuilder(
        draft_name=draft_name,
        draft_folder_path=draft_folder_path,
        on_progress=on_progress,
        add_image_movement=add_image_movement,
        add_video_movement=add_video_movement,
        split_subtitles=split_subtitles,
        fade_transition=fade_transition,
        mute_original_video=mute_original_video,
        crop_video=crop_video,
        background_image=background_image,
        background_music=background_music,
    )
    segments = [
        SegmentInfo(sub, aud, med)
        for sub, aud, med in zip(subtitle_texts, audio_paths, media_paths)
    ]
    return builder._build(segments)
