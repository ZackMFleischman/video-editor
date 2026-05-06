"""Project data model: Project, Track, Clip, Effect, TextOverlay."""
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field, asdict
from enum import Enum
from pathlib import Path
from typing import Any, Optional


class TrackType(str, Enum):
    VIDEO = "video"
    AUDIO = "audio"


class TransitionType(str, Enum):
    NONE = "none"
    CROSSFADE = "crossfade"
    FADE_BLACK = "fade_black"


@dataclass
class Effect:
    name: str
    params: dict = field(default_factory=dict)

    @staticmethod
    def brightness(value: float) -> "Effect":
        return Effect("brightness", {"value": value})

    @staticmethod
    def contrast(value: float) -> "Effect":
        return Effect("contrast", {"value": value})

    @staticmethod
    def saturation(value: float) -> "Effect":
        return Effect("saturation", {"value": value})

    @staticmethod
    def blur(sigma: float) -> "Effect":
        return Effect("blur", {"sigma": sigma})

    @staticmethod
    def grayscale() -> "Effect":
        return Effect("grayscale", {})


@dataclass
class TextOverlay:
    text: str
    start: float
    duration: float
    x: int = 50
    y: int = 50
    font_size: int = 48
    color: str = "white"
    box: bool = True


@dataclass
class Clip:
    source_path: str
    track_id: str
    start: float           # timeline start (seconds)
    in_point: float = 0.0  # source in-point (seconds)
    out_point: float = 0.0 # source out-point (seconds); 0 = end-of-source
    speed: float = 1.0
    volume: float = 1.0
    transition_in: TransitionType = TransitionType.NONE
    transition_in_duration: float = 0.5
    effects: list[Effect] = field(default_factory=list)
    text_overlays: list[TextOverlay] = field(default_factory=list)
    id: str = field(default_factory=lambda: uuid.uuid4().hex)

    @property
    def source_duration(self) -> float:
        return max(0.0, self.out_point - self.in_point)

    @property
    def timeline_duration(self) -> float:
        if self.speed <= 0:
            return 0.0
        return self.source_duration / self.speed

    @property
    def end(self) -> float:
        return self.start + self.timeline_duration


@dataclass
class Track:
    type: TrackType
    name: str
    muted: bool = False
    solo: bool = False
    volume: float = 1.0
    id: str = field(default_factory=lambda: uuid.uuid4().hex)


@dataclass
class Project:
    name: str = "Untitled"
    width: int = 1920
    height: int = 1080
    fps: int = 30
    sample_rate: int = 48000
    tracks: list[Track] = field(default_factory=list)
    clips: list[Clip] = field(default_factory=list)
    file_path: Optional[str] = None

    def __post_init__(self):
        if not self.tracks:
            self.tracks.append(Track(TrackType.VIDEO, "Video 1"))
            self.tracks.append(Track(TrackType.AUDIO, "Audio 1"))

    def track_by_id(self, track_id: str) -> Optional[Track]:
        for t in self.tracks:
            if t.id == track_id:
                return t
        return None

    def clips_for_track(self, track_id: str) -> list[Clip]:
        return sorted(
            (c for c in self.clips if c.track_id == track_id),
            key=lambda c: c.start,
        )

    def add_track(self, type: TrackType, name: Optional[str] = None) -> Track:
        if name is None:
            n = sum(1 for t in self.tracks if t.type == type) + 1
            name = f"{'Video' if type == TrackType.VIDEO else 'Audio'} {n}"
        t = Track(type, name)
        self.tracks.append(t)
        return t

    def add_clip(self, clip: Clip) -> None:
        self.clips.append(clip)

    def remove_clip(self, clip_id: str) -> None:
        self.clips = [c for c in self.clips if c.id != clip_id]

    def get_clip(self, clip_id: str) -> Optional[Clip]:
        for c in self.clips:
            if c.id == clip_id:
                return c
        return None

    @property
    def duration(self) -> float:
        return max((c.end for c in self.clips), default=0.0)

    # --- serialization ---

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "width": self.width,
            "height": self.height,
            "fps": self.fps,
            "sample_rate": self.sample_rate,
            "tracks": [asdict(t) for t in self.tracks],
            "clips": [_clip_to_dict(c) for c in self.clips],
        }

    @staticmethod
    def from_dict(data: dict[str, Any]) -> "Project":
        tracks = [
            Track(
                type=TrackType(t["type"]),
                name=t["name"],
                muted=t.get("muted", False),
                solo=t.get("solo", False),
                volume=t.get("volume", 1.0),
                id=t["id"],
            )
            for t in data.get("tracks", [])
        ]
        clips = [_dict_to_clip(c) for c in data.get("clips", [])]
        proj = Project(
            name=data.get("name", "Untitled"),
            width=data.get("width", 1920),
            height=data.get("height", 1080),
            fps=data.get("fps", 30),
            sample_rate=data.get("sample_rate", 48000),
            tracks=tracks,
            clips=clips,
        )
        return proj

    def save(self, path: str | Path) -> None:
        p = Path(path)
        p.write_text(json.dumps(self.to_dict(), indent=2))
        self.file_path = str(p)

    @staticmethod
    def load(path: str | Path) -> "Project":
        p = Path(path)
        proj = Project.from_dict(json.loads(p.read_text()))
        proj.file_path = str(p)
        return proj


def _clip_to_dict(c: Clip) -> dict[str, Any]:
    d = asdict(c)
    d["transition_in"] = c.transition_in.value
    d["effects"] = [{"name": e.name, "params": e.params} for e in c.effects]
    d["text_overlays"] = [asdict(t) for t in c.text_overlays]
    return d


def _dict_to_clip(d: dict[str, Any]) -> Clip:
    return Clip(
        source_path=d["source_path"],
        track_id=d["track_id"],
        start=d["start"],
        in_point=d.get("in_point", 0.0),
        out_point=d.get("out_point", 0.0),
        speed=d.get("speed", 1.0),
        volume=d.get("volume", 1.0),
        transition_in=TransitionType(d.get("transition_in", "none")),
        transition_in_duration=d.get("transition_in_duration", 0.5),
        effects=[Effect(e["name"], e.get("params", {})) for e in d.get("effects", [])],
        text_overlays=[TextOverlay(**t) for t in d.get("text_overlays", [])],
        id=d.get("id", uuid.uuid4().hex),
    )
