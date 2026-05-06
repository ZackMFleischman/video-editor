"""User config: stored in %APPDATA%\\VideoEditor\\config.json on Windows."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


def config_dir() -> Path:
    appdata = os.environ.get("APPDATA")
    if appdata:
        d = Path(appdata) / "VideoEditor"
    else:
        d = Path.home() / ".video_editor"
    d.mkdir(parents=True, exist_ok=True)
    return d


def config_path() -> Path:
    return config_dir() / "config.json"


def load_config() -> dict[str, Any]:
    p = config_path()
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text())
    except Exception:
        return {}


def save_config(cfg: dict[str, Any]) -> None:
    config_path().write_text(json.dumps(cfg, indent=2))


def get(key: str, default: Any = None) -> Any:
    return load_config().get(key, default)


def set(key: str, value: Any) -> None:
    cfg = load_config()
    cfg[key] = value
    save_config(cfg)
