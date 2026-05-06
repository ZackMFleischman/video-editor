# Video Editor

A multi-track GUI video editor built in Python with PyQt6 and FFmpeg.
Includes an AI speech overlay tool that turns a script (or instructions for a script) into voiced narration via the Anthropic Claude API and ElevenLabs.

## Features

- Multi-track timeline (multiple video + audio tracks, drag to move/resize)
- Trim, split, delete, speed control (0.25x–4x), per-clip volume
- Video effects: brightness, contrast, saturation, blur, grayscale
- Text overlays per clip (font size, color, position, duration)
- Source-clip preview with scrubbing
- Project save/load (`.vproj` JSON)
- Export to MP4 via FFmpeg with progress
- AI speech overlay:
  - Provide a script, OR provide instructions and Claude writes one
  - Pick from your ElevenLabs voices
  - Generated audio is dropped onto an audio track at the playhead

## Requirements

- Windows 10/11
- Python 3.10+
- FFmpeg on your `PATH` (install: `winget install Gyan.FFmpeg`)
- API keys (only needed for AI speech):
  - Anthropic API key (Claude)
  - ElevenLabs API key

## Quick start

Double-click `setup.bat` once, then `run.bat` to launch.

Or manually:

```bat
py -3 -m venv .venv
.venv\Scripts\pip install -r requirements.txt
.venv\Scripts\python -m video_editor
```

## Using AI speech overlay

1. Open `File > Settings`, paste your Anthropic and ElevenLabs API keys.
2. Click `AI Speech` in the toolbar (or `Tools > AI Speech Overlay…`).
3. Either paste a script, or pick "Generate from instructions", describe what you want, and click "Generate script".
4. Click "Load voices", pick one, then "Synthesize and Insert".
5. The generated MP3 is saved next to your project (in `generated_audio/`) and dropped on the timeline at the playhead.

API keys are stored locally in `%APPDATA%\VideoEditor\config.json`.

## Project layout

```
src/video_editor/
  __main__.py          # entry point
  core/
    model.py           # Project, Track, Clip, Effect, TextOverlay
    ffmpeg_engine.py   # probe, thumbnails, render
    config.py          # API key storage
  services/
    ai_script.py       # Claude script generation
    tts.py             # ElevenLabs TTS
  gui/
    main_window.py     # MainWindow, menus, file ops, export
    timeline.py        # multi-track timeline view
    preview.py         # QMediaPlayer-based source preview
    properties.py      # right-side properties panel
    dialogs.py         # Settings + AI Speech dialogs
```

## Notes

- The preview shows the *source* of the selected clip, not a live timeline render. Use `Export Video` to see the full composited result.
- The render strategy composites every video track onto a base canvas with `overlay`, mixes audio tracks with `amix`, and re-encodes to H.264/AAC at the project's resolution and frame rate.
- Drag clip edges to trim; drag the body to move; drag a clip onto another track of the same type to reassign it.
