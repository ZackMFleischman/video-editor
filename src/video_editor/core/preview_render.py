"""On-demand preview rendering with hash-based caching.

Renders the project to a temp MP4 with FFmpeg's ultrafast preset so the
PreviewPanel can play one continuous file (no codec swaps mid-stream).
Cached by a hash of the project state — re-renders only when something
playback-relevant changed.
"""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Callable, Optional

from .ffmpeg_engine import render_project
from .model import Project


def _project_hash(project: Project) -> str:
    """Hash the parts of the project that affect rendered output."""
    payload = {
        "w": project.width,
        "h": project.height,
        "fps": project.fps,
        "tracks": [
            {"id": t.id, "type": t.type.value, "muted": t.muted, "volume": t.volume}
            for t in project.tracks
        ],
        "clips": [
            {
                "id": c.id,
                "src": c.source_path,
                "tid": c.track_id,
                "start": round(c.start, 3),
                "in": round(c.in_point, 3),
                "out": round(c.out_point, 3),
                "speed": c.speed,
                "vol": c.volume,
                "fx": [{"n": e.name, "p": e.params} for e in c.effects],
                "txt": [
                    {
                        "t": o.text, "s": o.start, "d": o.duration,
                        "x": o.x, "y": o.y, "fs": o.font_size, "c": o.color,
                    }
                    for o in c.text_overlays
                ],
            }
            for c in project.clips
        ],
    }
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(raw).hexdigest()[:16]


def cache_dir() -> Path:
    d = Path(tempfile.gettempdir()) / "VideoEditor_preview"
    d.mkdir(parents=True, exist_ok=True)
    return d


def cached_path(project: Project) -> Path:
    return cache_dir() / f"preview_{_project_hash(project)}.mp4"


def is_cached(project: Project) -> bool:
    p = cached_path(project)
    return p.exists() and p.stat().st_size > 1000


def render_preview(
    project: Project,
    progress: Optional[Callable[[float], None]] = None,
) -> Path:
    """Return a path to a rendered preview MP4 of the project. Cached."""
    out = cached_path(project)
    if is_cached(project):
        if progress:
            progress(1.0)
        return out
    # Clean up old previews to avoid eating disk forever (keep last 8)
    files = sorted(cache_dir().glob("preview_*.mp4"), key=lambda p: p.stat().st_mtime, reverse=True)
    for old in files[8:]:
        try:
            old.unlink()
        except OSError:
            pass
    render_project(project, str(out), progress=progress, quality="preview")
    return out
