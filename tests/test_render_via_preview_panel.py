"""Trigger PreviewPanel's _start_render directly. Goal: isolate whether
the bug is in PreviewPanel's worker plumbing vs. anywhere else.
"""
from __future__ import annotations

import glob
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QApplication

from video_editor.core import preview_render
from video_editor.core.ffmpeg_engine import probe
from video_editor.core.model import Clip, Project
from video_editor.gui.preview import PreviewPanel


def find_a_video() -> str:
    files = glob.glob(r"C:\Users\zFlei\Downloads\*.mp4")
    if not files:
        raise RuntimeError("No mp4 files in Downloads")
    return files[0]


def main() -> int:
    # Clear cache
    for f in preview_render.cache_dir().glob("preview_*.mp4"):
        try:
            f.unlink()
        except OSError:
            pass

    app = QApplication(sys.argv)

    # Create the PreviewPanel just like the GUI does
    pp = PreviewPanel()
    pp.show()  # we need it shown so VLC can bind HWND lazily

    src = find_a_video()
    print(f"Source: {src}", flush=True)
    p = Project(name="t", width=1280, height=720, fps=30)
    info = probe(src)
    p.add_clip(Clip(source_path=src, track_id=p.tracks[0].id, start=0.0, in_point=0.0, out_point=info.duration))
    pp.set_project(p)
    print(f"Project duration: {p.duration:.2f}s", flush=True)

    state = {"start": None, "rendered": False}

    def trigger():
        state["start"] = time.time()
        print(f"[t=0] calling _start_render(autoplay=False)", flush=True)
        pp._start_render(then_autoplay=False)
        print(f"[t={time.time()-state['start']:.2f}s] returned from _start_render; waiting", flush=True)

    def check():
        if state["rendered"]:
            return
        elapsed = time.time() - state["start"]
        if preview_render.is_cached(p):
            print(f"[t={elapsed:.2f}s] OK rendered.", flush=True)
            state["rendered"] = True
            QTimer.singleShot(200, app.quit)
            return
        if elapsed > 25:
            print(f"[t={elapsed:.2f}s] TIMEOUT", flush=True)
            print(f"  is_rendering: {pp.is_rendering()}", flush=True)
            print(f"  thread: {pp._tl_render_thread}", flush=True)
            if pp._tl_render_thread is not None:
                print(f"  thread.isRunning: {pp._tl_render_thread.isRunning()}", flush=True)
                print(f"  thread.isFinished: {pp._tl_render_thread.isFinished()}", flush=True)
            QTimer.singleShot(100, app.quit)
            return
        QTimer.singleShot(250, check)

    QTimer.singleShot(500, trigger)
    QTimer.singleShot(900, check)

    rc = app.exec()
    print(f"Exit (rc={rc}) rendered={state['rendered']}")
    return 0 if state["rendered"] else 1


if __name__ == "__main__":
    sys.exit(main())
