"""Same as test_preview_render_qthread but with libvlc initialized first
(matching the GUI's environment). Goal: prove or disprove that libvlc
is interfering with the FFmpeg subprocess pipes.
"""
from __future__ import annotations

import glob
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from PyQt6.QtCore import QObject, QThread, QTimer, pyqtSignal
from PyQt6.QtWidgets import QApplication

from video_editor.core.ffmpeg_engine import probe
from video_editor.core.model import Clip, Project
from video_editor.core import preview_render


def find_a_video() -> str:
    files = glob.glob(r"C:\Users\zFlei\Downloads\*.mp4")
    if not files:
        raise RuntimeError("No mp4 files in Downloads")
    return files[0]


class Worker(QObject):
    finished = pyqtSignal(str)
    failed = pyqtSignal(str)

    def __init__(self, project: Project):
        super().__init__()
        self._project = project

    def run(self):
        try:
            t0 = time.time()
            print(f"[worker] calling render_preview", flush=True)
            out = preview_render.render_preview(self._project, progress=lambda p: print(f"  progress {p*100:.0f}%", flush=True))
            print(f"[worker] returned after {time.time()-t0:.2f}s", flush=True)
            self.finished.emit(str(out))
        except Exception as e:
            import traceback; traceback.print_exc()
            self.failed.emit(str(e))


def main() -> int:
    # Clear cache so we exercise the render
    for f in preview_render.cache_dir().glob("preview_*.mp4"):
        try:
            f.unlink()
        except OSError:
            pass

    app = QApplication(sys.argv)

    # === Initialize VLC the way the GUI does ===
    print("Initializing VLC...", flush=True)
    import vlc  # type: ignore
    vlc.Instance("--quiet")  # the throwaway probe
    instance = vlc.Instance("--quiet")
    player = instance.media_player_new()
    print("VLC initialized; instance=%s, player=%s" % (instance, player), flush=True)

    src = find_a_video()
    print(f"Source: {src}", flush=True)
    p = Project(name="t", width=1280, height=720, fps=30)
    info = probe(src)
    p.add_clip(Clip(source_path=src, track_id=p.tracks[0].id, start=0.0, in_point=0.0, out_point=info.duration))
    print(f"Project duration: {p.duration:.2f}s", flush=True)

    keepalive: list = []

    def go():
        worker = Worker(p)
        thread = QThread(app)
        worker.moveToThread(thread)
        keepalive.append((worker, thread))
        thread.started.connect(worker.run)

        def on_finished(path):
            print(f"FINISHED: {path}", flush=True)
            thread.quit()
            thread.wait(2000)
            QTimer.singleShot(100, app.quit)

        def on_failed(err):
            print(f"FAILED: {err}", flush=True)
            thread.quit()
            thread.wait(2000)
            QTimer.singleShot(100, app.quit)

        worker.finished.connect(on_finished)
        worker.failed.connect(on_failed)

        def timeout():
            if not thread.isFinished():
                print("TIMEOUT after 25s", flush=True)
                thread.quit()
                thread.wait(2000)
                QTimer.singleShot(100, app.quit)

        QTimer.singleShot(25000, timeout)
        thread.start()

    QTimer.singleShot(100, go)
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
