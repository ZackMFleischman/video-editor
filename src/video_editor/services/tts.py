"""ElevenLabs text-to-speech."""
from __future__ import annotations

from pathlib import Path
from typing import Optional

import requests

from ..core import config


API_BASE = "https://api.elevenlabs.io/v1"


def list_voices(api_key: Optional[str] = None) -> list[dict]:
    """Return a list of {voice_id, name, category} dicts."""
    key = api_key or config.get("elevenlabs_api_key")
    if not key:
        raise RuntimeError("No ElevenLabs API key set. Open Settings and paste your key.")
    r = requests.get(
        f"{API_BASE}/voices",
        headers={"xi-api-key": key, "Accept": "application/json"},
        timeout=20,
    )
    r.raise_for_status()
    data = r.json()
    return [
        {"voice_id": v["voice_id"], "name": v["name"], "category": v.get("category", "")}
        for v in data.get("voices", [])
    ]


def synthesize(
    text: str,
    voice_id: str,
    out_path: str | Path,
    api_key: Optional[str] = None,
    model_id: str = "eleven_multilingual_v2",
    stability: float = 0.5,
    similarity_boost: float = 0.75,
) -> Path:
    """Generate speech audio for `text` using `voice_id` and write to `out_path` (mp3)."""
    key = api_key or config.get("elevenlabs_api_key")
    if not key:
        raise RuntimeError("No ElevenLabs API key set. Open Settings and paste your key.")
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)

    r = requests.post(
        f"{API_BASE}/text-to-speech/{voice_id}",
        headers={
            "xi-api-key": key,
            "Accept": "audio/mpeg",
            "Content-Type": "application/json",
        },
        json={
            "text": text,
            "model_id": model_id,
            "voice_settings": {
                "stability": stability,
                "similarity_boost": similarity_boost,
            },
        },
        timeout=120,
    )
    if not r.ok:
        raise RuntimeError(f"ElevenLabs API error {r.status_code}: {r.text[:300]}")
    out.write_bytes(r.content)
    return out
