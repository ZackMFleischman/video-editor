"""End-to-end: launch the actual MainWindow, programmatically import a clip,
then verify the auto-render completes within a timeout. No human required.
"""
from __future__ import annotations

import glob
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QApplication

from video_editor.core import preview_render
from video_editor.gui.main_window import MainWindow


def find_a_video() -> str:
    files = glob.glob(r"C:\Users\zFlei\Downloads\*.mp4")
    if not files:
        raise RuntimeError("No mp4 files in Downloads")
    return files[0]


def main() -> int:
    # Clear cache so we exercise the render
    for f in preview_render.cache_dir().glob("preview_*.mp4"):
        try:
            f.unlink()
        except OSError:
            pass

    app = QApplication(sys.argv)
    win = MainWindow()
    win.show()

    src = find_a_video()
    print(f"Using source: {src}")

    state = {"start": None, "rendered": False}

    def trigger_import():
        state["start"] = time.time()
        print(f"[t=0] Triggering _add_media({src})")
        win._add_media(src)
        win.timeline._refresh_layout()
        win.preview.mark_dirty()
        # Force-fire the debounce immediately so we don't wait
        win.preview._dirty_timer.stop()
        win.preview._dirty_fire()
        print(f"[t={time.time()-state['start']:.2f}s] Triggered render; waiting…")

    def check():
        if state["rendered"]:
            return
        elapsed = time.time() - state["start"]
        if preview_render.is_cached(win.project):
            print(f"[t={elapsed:.2f}s] Render finished, cache file present.")
            state["rendered"] = True
            QTimer.singleShot(500, app.quit)
            return
        if elapsed > 30:
            print(f"[t={elapsed:.2f}s] TIMEOUT — render never completed.")
            print(f"  is_rendering: {win.preview.is_rendering()}")
            print(f"  render_thread: {win.preview._tl_render_thread}")
            QTimer.singleShot(100, app.quit)
            return
        QTimer.singleShot(250, check)

    QTimer.singleShot(500, trigger_import)
    QTimer.singleShot(900, check)

    rc = app.exec()
    print(f"App exited (rc={rc}); rendered={state['rendered']}")
    return 0 if state["rendered"] else 1


if __name__ == "__main__":
    sys.exit(main())
