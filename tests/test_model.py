"""Round-trip Project save/load and basic clip math."""
from __future__ import annotations

import json
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from video_editor.core.model import (
    Clip,
    Effect,
    Project,
    TextOverlay,
    TrackType,
    TransitionType,
)


def test_project_default_tracks():
    p = Project()
    assert len(p.tracks) == 2
    assert p.tracks[0].type == TrackType.VIDEO
    assert p.tracks[1].type == TrackType.AUDIO


def test_clip_durations():
    c = Clip(source_path="x.mp4", track_id="tid", start=2.0, in_point=0.0, out_point=10.0, speed=2.0)
    assert c.source_duration == 10.0
    assert c.timeline_duration == 5.0
    assert c.end == 7.0


def test_save_load_roundtrip(tmp_path):
    p = Project(name="demo", width=1280, height=720, fps=24)
    vt = p.tracks[0]
    at = p.tracks[1]
    c1 = Clip(
        source_path=r"C:\videos\a.mp4",
        track_id=vt.id,
        start=0.0,
        in_point=0.0,
        out_point=5.0,
        speed=1.0,
        effects=[Effect.brightness(0.1), Effect.grayscale()],
        text_overlays=[TextOverlay(text="hello", start=0.5, duration=2.0)],
        transition_in=TransitionType.CROSSFADE,
    )
    c2 = Clip(
        source_path=r"C:\audio\b.mp3",
        track_id=at.id,
        start=1.0,
        in_point=0.0,
        out_point=4.0,
        volume=0.7,
    )
    p.add_clip(c1)
    p.add_clip(c2)

    out = tmp_path / "demo.vproj"
    p.save(out)

    raw = json.loads(out.read_text())
    assert raw["name"] == "demo"
    assert len(raw["clips"]) == 2

    loaded = Project.load(out)
    assert loaded.name == "demo"
    assert len(loaded.clips) == 2
    by_id = {c.id: c for c in loaded.clips}
    assert by_id[c1.id].effects[0].name == "brightness"
    assert by_id[c1.id].text_overlays[0].text == "hello"
    assert by_id[c1.id].transition_in == TransitionType.CROSSFADE
    assert by_id[c2.id].volume == 0.7


def test_split_math():
    p = Project()
    vt = p.tracks[0]
    c = Clip(source_path="x.mp4", track_id=vt.id, start=0.0, in_point=0.0, out_point=10.0, speed=1.0)
    p.add_clip(c)
    # Simulate split at timeline t=4.0
    t = 4.0
    offset_into_clip = (t - c.start) * c.speed
    split_source_time = c.in_point + offset_into_clip
    new = Clip(
        source_path=c.source_path, track_id=c.track_id, start=t,
        in_point=split_source_time, out_point=c.out_point, speed=c.speed,
    )
    c.out_point = split_source_time
    p.add_clip(new)
    assert c.end == 4.0
    assert new.end == 10.0
    assert c.timeline_duration + new.timeline_duration == 10.0
