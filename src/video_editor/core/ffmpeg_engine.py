"""FFmpeg-based render engine.

Builds an FFmpeg filter graph from a Project and renders to an output file.
Also provides probe/thumbnail utilities.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from .model import (
    Clip,
    Effect,
    Project,
    TextOverlay,
    Track,
    TrackType,
    TransitionType,
)


def ffmpeg_bin() -> str:
    return shutil.which("ffmpeg") or "ffmpeg"


def ffprobe_bin() -> str:
    return shutil.which("ffprobe") or "ffprobe"


def ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None


@dataclass
class MediaInfo:
    path: str
    duration: float
    width: int = 0
    height: int = 0
    has_video: bool = False
    has_audio: bool = False
    fps: float = 0.0


def probe(path: str) -> MediaInfo:
    """Return MediaInfo for a media file. Raises on missing file."""
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(path)
    cmd = [
        ffprobe_bin(),
        "-v", "error",
        "-print_format", "json",
        "-show_format",
        "-show_streams",
        str(p),
    ]
    out = subprocess.run(cmd, capture_output=True, text=True, check=True)
    data = json.loads(out.stdout or "{}")
    info = MediaInfo(path=str(p), duration=float(data.get("format", {}).get("duration", 0) or 0))
    for s in data.get("streams", []):
        ctype = s.get("codec_type")
        if ctype == "video":
            info.has_video = True
            info.width = int(s.get("width") or 0)
            info.height = int(s.get("height") or 0)
            r = s.get("r_frame_rate") or "0/1"
            try:
                num, den = r.split("/")
                info.fps = float(num) / float(den) if float(den) else 0.0
            except Exception:
                info.fps = 0.0
        elif ctype == "audio":
            info.has_audio = True
    return info


def extract_thumbnail(path: str, time: float, out_path: str, width: int = 160) -> bool:
    """Extract a single frame at `time` seconds."""
    cmd = [
        ffmpeg_bin(),
        "-y",
        "-ss", f"{time:.3f}",
        "-i", path,
        "-vframes", "1",
        "-vf", f"scale={width}:-1",
        out_path,
    ]
    r = subprocess.run(cmd, capture_output=True)
    return r.returncode == 0 and Path(out_path).exists()


# ---------- filter graph builders ----------

def _effect_filter(e: Effect) -> Optional[str]:
    """Translate Effect to an FFmpeg filter expression."""
    n = e.name
    p = e.params
    if n == "brightness":
        # value in [-1, 1]
        return f"eq=brightness={p.get('value', 0):.3f}"
    if n == "contrast":
        # value > 0; 1 = identity
        return f"eq=contrast={p.get('value', 1):.3f}"
    if n == "saturation":
        return f"eq=saturation={p.get('value', 1):.3f}"
    if n == "blur":
        return f"gblur=sigma={p.get('sigma', 1):.3f}"
    if n == "grayscale":
        return "hue=s=0"
    return None


def _text_filter(o: TextOverlay) -> str:
    text = o.text.replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")
    box = "1" if o.box else "0"
    return (
        f"drawtext=text='{text}':"
        f"x={o.x}:y={o.y}:"
        f"fontsize={o.font_size}:"
        f"fontcolor={o.color}:"
        f"box={box}:boxcolor=black@0.5:boxborderw=8:"
        f"enable='between(t,{o.start:.3f},{o.start + o.duration:.3f})'"
    )


def _build_clip_video_filters(clip: Clip, project: Project) -> str:
    """Filters applied to a single video clip after trimming."""
    parts: list[str] = []

    # Speed (video): setpts
    if clip.speed and clip.speed != 1.0:
        parts.append(f"setpts={1.0 / clip.speed:.6f}*PTS")

    # Effects
    for e in clip.effects:
        f = _effect_filter(e)
        if f:
            parts.append(f)

    # Scale + pad to project canvas
    parts.append(
        f"scale={project.width}:{project.height}:force_original_aspect_ratio=decrease,"
        f"pad={project.width}:{project.height}:(ow-iw)/2:(oh-ih)/2:color=black,"
        f"setsar=1,fps={project.fps}"
    )

    # Text overlays — overlay timestamps are clip-local
    for o in clip.text_overlays:
        parts.append(_text_filter(o))

    return ",".join(parts)


def _build_clip_audio_filters(clip: Clip) -> str:
    parts: list[str] = []
    if clip.speed and clip.speed != 1.0:
        # atempo accepts 0.5..2.0; chain for larger ranges
        s = clip.speed
        chain = []
        while s > 2.0:
            chain.append("atempo=2.0")
            s /= 2.0
        while s < 0.5:
            chain.append("atempo=0.5")
            s /= 0.5
        chain.append(f"atempo={s:.4f}")
        parts.extend(chain)
    if clip.volume != 1.0:
        parts.append(f"volume={clip.volume:.3f}")
    return ",".join(parts) if parts else "anull"


# ---------- main render ----------

def render_project(
    project: Project,
    output_path: str,
    progress: Optional[Callable[[float], None]] = None,
    quality: str = "final",
) -> None:
    """Render a project to `output_path` (mp4).

    `quality`:
      - "final": medium preset, CRF 20 (slower but high quality, used for export)
      - "preview": ultrafast preset, CRF 28 (fast, used for live timeline preview)
    """
    if not project.clips:
        raise ValueError("Project has no clips to render.")

    inputs: list[str] = []
    input_count = 0

    # Map clip.id -> input index
    clip_input_idx: dict[str, int] = {}
    for clip in project.clips:
        inputs += ["-i", clip.source_path]
        clip_input_idx[clip.id] = input_count
        input_count += 1

    filter_parts: list[str] = []
    video_track_outs: list[str] = []
    audio_track_outs: list[str] = []
    needs_blank_video = False
    needs_blank_audio = False

    # Build a base black canvas + silent audio for time gaps.
    base_video_label = "[base_v]"
    base_audio_label = "[base_a]"
    duration = project.duration
    filter_parts.append(
        f"color=c=black:s={project.width}x{project.height}:r={project.fps}:d={duration:.3f}{base_video_label}"
    )
    filter_parts.append(
        f"anullsrc=channel_layout=stereo:sample_rate={project.sample_rate}:d={duration:.3f}{base_audio_label}"
    )

    # Process each track individually, then composite.
    for track in project.tracks:
        clips = project.clips_for_track(track.id)
        if not clips:
            continue

        if track.type == TrackType.VIDEO:
            track_clip_labels: list[str] = []
            for clip in clips:
                idx = clip_input_idx[clip.id]
                trim = (
                    f"[{idx}:v]trim=start={clip.in_point:.3f}"
                    + (f":end={clip.out_point:.3f}" if clip.out_point > 0 else "")
                    + ",setpts=PTS-STARTPTS"
                )
                vf = _build_clip_video_filters(clip, project)
                label = f"[v_{clip.id}]"
                filter_parts.append(f"{trim},{vf}{label}")

                # Place this clip on the canvas with overlay enable window
                placed_label = f"[vp_{clip.id}]"
                # Use the track-accumulator approach: overlay on running canvas later
                track_clip_labels.append(label)

            # Build a per-track timeline by overlaying each clip onto base black at its start.
            running = base_video_label if not video_track_outs else video_track_outs[-1]
            # We'll build a fresh per-track canvas, then composite tracks at the end.
            track_canvas_label = f"[trkc_{track.id}_in]"
            filter_parts.append(
                f"color=c=black@0.0:s={project.width}x{project.height}:r={project.fps}:d={duration:.3f}{track_canvas_label}"
            )
            current = track_canvas_label
            for clip, vlabel in zip(clips, track_clip_labels):
                next_label = f"[trk_{track.id}_{clip.id}]"
                filter_parts.append(
                    f"{current}{vlabel}overlay=enable='between(t,{clip.start:.3f},{clip.end:.3f})':"
                    f"x=0:y=0:eof_action=pass{next_label}"
                )
                current = next_label
            video_track_outs.append(current)

        else:  # audio track
            track_audio_pieces: list[str] = []
            for clip in clips:
                idx = clip_input_idx[clip.id]
                trim = (
                    f"[{idx}:a]atrim=start={clip.in_point:.3f}"
                    + (f":end={clip.out_point:.3f}" if clip.out_point > 0 else "")
                    + ",asetpts=PTS-STARTPTS"
                )
                af = _build_clip_audio_filters(clip)
                # Delay so it starts at clip.start
                delay_ms = int(clip.start * 1000)
                delay = f",adelay={delay_ms}|{delay_ms}" if delay_ms > 0 else ""
                label = f"[a_{clip.id}]"
                filter_parts.append(f"{trim},{af}{delay}{label}")
                track_audio_pieces.append(label)

            if not track_audio_pieces:
                continue
            # Mix all clips on this track together
            mix_inputs = "".join(track_audio_pieces)
            track_mix_label = f"[atrk_{track.id}]"
            volume = track.volume * (0.0 if track.muted else 1.0)
            filter_parts.append(
                f"{mix_inputs}amix=inputs={len(track_audio_pieces)}:normalize=0,"
                f"volume={volume:.3f}{track_mix_label}"
            )
            audio_track_outs.append(track_mix_label)

    # Composite all video tracks on top of base
    if video_track_outs:
        current = base_video_label
        for i, lbl in enumerate(video_track_outs):
            out = "[vout]" if i == len(video_track_outs) - 1 else f"[vc_{i}]"
            filter_parts.append(f"{current}{lbl}overlay=eof_action=pass{out}")
            current = out
        final_video_label = "[vout]"
    else:
        # No video; just use base black
        filter_parts.append(f"{base_video_label}copy[vout]")
        final_video_label = "[vout]"
        needs_blank_video = True

    # Mix audio tracks
    if audio_track_outs:
        if len(audio_track_outs) == 1:
            filter_parts.append(f"{audio_track_outs[0]}anull[aout]")
        else:
            mix_in = "".join(audio_track_outs)
            filter_parts.append(
                f"{mix_in}amix=inputs={len(audio_track_outs)}:normalize=0[aout]"
            )
        final_audio_label = "[aout]"
    else:
        filter_parts.append(f"{base_audio_label}anull[aout]")
        final_audio_label = "[aout]"
        needs_blank_audio = True

    filter_complex = ";".join(filter_parts)

    if quality == "preview":
        preset, crf, abitrate = "ultrafast", "28", "128k"
    else:
        preset, crf, abitrate = "medium", "20", "192k"

    cmd = [
        ffmpeg_bin(),
        "-y",
        *inputs,
        "-filter_complex", filter_complex,
        "-map", final_video_label,
        "-map", final_audio_label,
        "-c:v", "libx264",
        "-preset", preset,
        "-crf", crf,
        "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        "-b:a", abitrate,
        "-r", str(project.fps),
        "-t", f"{duration:.3f}",
        output_path,
    ]

    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )

    last_progress = 0.0
    if proc.stdout is not None:
        for line in proc.stdout:
            if progress and "time=" in line:
                # parse time=00:00:00.00
                try:
                    t = line.split("time=", 1)[1].split(" ", 1)[0]
                    h, m, s = t.split(":")
                    cur = int(h) * 3600 + int(m) * 60 + float(s)
                    pct = max(0.0, min(1.0, cur / duration)) if duration > 0 else 0.0
                    if pct - last_progress >= 0.01:
                        last_progress = pct
                        progress(pct)
                except Exception:
                    pass
    code = proc.wait()
    if code != 0:
        raise RuntimeError(f"FFmpeg failed with exit code {code}")
    if progress:
        progress(1.0)
