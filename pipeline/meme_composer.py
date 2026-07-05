"""Meme-style Jianying draft composer.

Design notes (matching B-station meme video pacing):
  * Photos are static by default (no camera keyframes); enable with add_movement=True
  * BGM is written directly onto an audio track; loops if shorter than the video,
    or gets truncated to the total video duration if longer
  * With fit_to_bgm=True, per-photo duration = bgm_duration / photo_count
    (so the video is exactly as long as the BGM track)
  * Default fit_mode='contain' with bg_blur=True: no letterbox black bars,
    the background is a heavily-blurred cover-cropped copy of the same image
    (TikTok / Xiaohongshu style)
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable

import pyJianYingDraft as draft
from pyJianYingDraft import (
    ClipSettings, KeyframeProperty, TrackType, TransitionType, trange,
)
from PIL import Image

from pipeline.blur_bg import get_blurred_bg
from pipeline.camera import CAMERA_PRESETS
from pipeline.draft_composer import cover_scale_for_canvas


ProgressFn = Callable[[str, int], None]


def get_audio_duration_us(path: str) -> int:
    """Return audio duration in microseconds via pyJianYingDraft AudioMaterial."""
    mat = draft.AudioMaterial(path)
    return int(mat.duration)


def compose_meme_draft(
    image_paths: list[str],
    *,
    draft_name: str,
    draft_folder_path: str,
    canvas_width: int = 1080,
    canvas_height: int = 1080,
    seconds_per_image: float = 8.0,
    add_movement: bool = False,
    fade_transition: bool = True,
    transition_duration: str = "0.3s",
    camera_preset: str = "mixed",
    fit_mode: str = "contain",
    bg_blur: bool = True,
    bgm_path: str | None = None,
    bgm_volume: float = 0.8,
    fit_to_bgm: bool = False,
    on_progress: ProgressFn | None = None,
) -> str:
    """Assemble a set of photos into a Jianying draft.

    Args:
        image_paths:        Local paths in display order.
        draft_name:         Draft name in Jianying (unique, timestamp suffix suggested).
        draft_folder_path:  Jianying draft folder.
        canvas_width/height: Canvas (meme default: 1080x1080 square).
        seconds_per_image:  Per-photo dwell time; ignored when fit_to_bgm=True.
                            Default 8s (matches typical B-station meme video pacing).
        add_movement:       Add pan/zoom keyframes; default off for meme feel.
        fade_transition:    Crossfade between neighbors; default on, 0.3s.
        camera_preset:      One of pipeline.camera.CAMERA_PRESETS keys.
        fit_mode:           'contain' (default, full image visible)
                            or 'cover' (fills canvas, crops overhang).
        bg_blur:            When fit_mode='contain', fill the letterbox area with a
                            heavily-blurred cover-cropped copy of the same image
                            (no black bars). Default True.
        bgm_path:           Optional audio path; None means no audio track.
        bgm_volume:         0.0-1.0 (default 0.8; BGM is the star in memes).
        fit_to_bgm:         Match total video length to BGM length.
        on_progress:        Callback (msg, pct).
    """
    if not image_paths:
        raise SystemExit("compose_meme_draft: image_paths cannot be empty")
    if fit_mode not in ("contain", "cover"):
        raise SystemExit(f"fit_mode must be 'contain' or 'cover', got {fit_mode!r}")

    use_blur_bg = (fit_mode == "contain" and bg_blur)

    def prog(msg: str, pct: int) -> None:
        if on_progress:
            on_progress(msg, pct)

    prog("init draft", 0)

    camera_effects = CAMERA_PRESETS.get(camera_preset, CAMERA_PRESETS["mixed"])

    if fit_to_bgm:
        if not bgm_path:
            raise SystemExit("--fit-to-bgm requires --bgm")
        bgm_dur = get_audio_duration_us(bgm_path)
        duration_us = max(300_000, bgm_dur // len(image_paths))
    else:
        duration_us = int(seconds_per_image * 1_000_000)

    # ---- Pre-generate blurred backgrounds (cached) ----
    bg_paths: list[str] = []
    if use_blur_bg:
        prog("pre-generating blurred backgrounds", 2)
        for i, img_path in enumerate(image_paths, start=1):
            bp = get_blurred_bg(img_path, (canvas_width, canvas_height))
            bg_paths.append(str(bp))
            if i % 5 == 0 or i == len(image_paths):
                prog(f"blur bg {i}/{len(image_paths)}", 2 + int(3 * i / len(image_paths)))

    df = draft.DraftFolder(draft_folder_path)
    script = df.create_draft(draft_name, canvas_width, canvas_height, allow_replace=True)

    # Track layering: bg_track added FIRST (lowest layer), main_track ON TOP
    if use_blur_bg:
        script.add_track(TrackType.video, "bg_track")
    tb = script.add_track(TrackType.video, "main_track")
    if bgm_path:
        tb.add_track(TrackType.audio, "bgm_track")
    prog("tracks created", 6)

    total = len(image_paths)
    current_time = 0
    prev_main_seg = None
    prev_bg_seg = None

    for i, img_path in enumerate(image_paths, start=1):
        base_pct = 6 + int(84 * i / total)
        prog(f"segment {i}/{total}", base_pct)

        time_range = trange(current_time, duration_us)

        # ---- Background layer (blurred cover-crop of same image) ----
        if use_blur_bg:
            bg_seg = draft.VideoSegment(bg_paths[i - 1], time_range)
            bg_seg.clip_settings = ClipSettings(transform_x=0, transform_y=0)
            # Blurred image is already canvas-sized; scale 1.0 = perfect fit
            bg_seg.add_keyframe(KeyframeProperty.uniform_scale, 0, 1.0)
            for prop in (KeyframeProperty.position_x, KeyframeProperty.position_y):
                bg_seg.add_keyframe(prop, 0, 0)
                bg_seg.add_keyframe(prop, duration_us, 0)
        else:
            bg_seg = None

        # ---- Main layer (contain or cover, per fit_mode) ----
        main_seg = draft.VideoSegment(img_path, time_range)
        main_seg.clip_settings = ClipSettings(transform_x=0, transform_y=0)

        with Image.open(img_path) as _im:
            iw, ih = _im.size
        if fit_mode == "cover":
            base_scale = cover_scale_for_canvas(canvas_width, canvas_height, iw, ih)
        else:
            base_scale = 1.0

        if add_movement:
            effect = camera_effects[(i - 1) % len(camera_effects)]
            effect(main_seg, duration_us, base_scale=base_scale)
        else:
            static_scale = base_scale * 1.02 if fit_mode == "cover" else base_scale
            main_seg.add_keyframe(KeyframeProperty.uniform_scale, 0, static_scale)
            for prop in (KeyframeProperty.position_x, KeyframeProperty.position_y):
                main_seg.add_keyframe(prop, 0, 0)
                main_seg.add_keyframe(prop, duration_us, 0)

        # ---- Commit previous segments (delayed so we can attach transitions) ----
        if prev_main_seg is not None:
            if fade_transition:
                prev_main_seg.add_transition(TransitionType.叠化, duration=transition_duration)
                if prev_bg_seg is not None:
                    prev_bg_seg.add_transition(TransitionType.叠化, duration=transition_duration)
            script.add_segment(prev_main_seg, "main_track")
            if prev_bg_seg is not None:
                script.add_segment(prev_bg_seg, "bg_track")

        prev_main_seg = main_seg
        prev_bg_seg = bg_seg
        current_time += duration_us

    # Commit last pair
    if prev_main_seg is not None:
        script.add_segment(prev_main_seg, "main_track")
        if prev_bg_seg is not None:
            script.add_segment(prev_bg_seg, "bg_track")

    total_duration = current_time

    # ---- BGM ----
    if bgm_path:
        prog("attach bgm", 93)
        bgm_dur = get_audio_duration_us(bgm_path)
        cur = 0
        while cur < total_duration:
            seg_dur = min(bgm_dur, total_duration - cur)
            script.add_segment(
                draft.AudioSegment(
                    bgm_path,
                    trange(cur, seg_dur),
                    volume=bgm_volume,
                    source_timerange=trange(0, seg_dur),
                ),
                "bgm_track",
            )
            cur += seg_dur

    prog("save draft", 98)
    script.save()
    prog("done", 100)

    total_seconds = total_duration / 1_000_000
    each_seconds = duration_us / 1_000_000
    notes = []
    if bgm_path:
        notes.append(f"bgm={Path(bgm_path).name}")
    notes.append(f"fit={fit_mode}" + (" +blur-bg" if use_blur_bg else ""))
    tail = " | ".join(notes)
    return (f"Jianying draft '{draft_name}' created. "
            f"({total} photos, {each_seconds:.2f}s each, total {total_seconds:.1f}s | {tail})")
