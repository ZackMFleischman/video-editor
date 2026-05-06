"""Reproduce the GUI render flow without the GUI.

Spins up a QApplication and runs the same _PreviewRenderWorker on a QThread
exactly the way PreviewPanel does, so we can verify (or repro) hangs that
only happen in the threaded context.

Run directly:  python tests/test_preview_render_qthread.py
"""
from __future__ import annotations

import glob
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from PyQt6.QtCore import QCoreApplication, QObject, QThread, QTimer, pyqtSignal
from PyQt6.QtWidgets import QApplication

from video_editor.core.model import Clip, Project, TrackType
from video_editor.core import preview_render
from video_editor.core.ffmpeg_engine import probe


def find_a_video() -> str:
    for cand in [
        r"C:\Users\zFlei\Downloads\Boutte 1 Close Up.mp4",
        r"C:\Users\zFlei\Downloads\Diggs 3.mp4",
        r"C:\Users\zFlei\Downloads\Kyle TD.mp4",
    ]:
        if os.path.exists(cand):
            return cand
    files = glob.glob(r"C:\Users\zFlei\Downloads\*.mp4")
    if not files:
        raise RuntimeError("No mp4 files in Downloads to test with.")
    return files[0]


def make_project(srcs: list[str]) -> Project:
    p = Project(name="test", width=1280, height=720, fps=30)
    vt = p.tracks[0]
    start = 0.0
    for s in srcs:
        info = probe(s)
        p.add_clip(
            Clip(
                source_path=s,
                track_id=vt.id,
                start=start,
                in_point=0.0,
                out_point=info.duration if info.duration > 0 else 0.0,
            )
        )
        start += info.duration
    print(f"Project duration: {p.duration:.2f}s, {len(p.clips)} clip(s)")
    return p


class Worker(QObject):
    progress = pyqtSignal(float)
    finished = pyqtSignal(str)
    failed = pyqtSignal(str)

    def __init__(self, project: Project):
        super().__init__()
        self._project = project

    def run(self):
        try:
            t0 = time.time()
            out = preview_render.render_preview(self._project, progress=self.progress.emit)
            print(f"  worker.run() returning after {time.time() - t0:.2f}s")
            self.finished.emit(str(out))
        except Exception as e:
            import traceback
            traceback.print_exc()
            self.failed.emit(str(e))


def main() -> int:
    # Make sure cache is clear so we actually exercise the render path
    cache = preview_render.cache_dir()
    for f in cache.glob("preview_*.mp4"):
        try:
            f.unlink()
        except OSError:
            pass

    app = QApplication(sys.argv)

    src = find_a_video()
    print("Source:", src)
    # Try with 1, 2, then 4 clips so we know which case (if any) hangs
    cases = [
        ("1 clip", [src]),
        ("2 clips", [src, src]),
        ("4 clips", [src] * 4),
    ]

    results: list[tuple[str, str]] = []
    keepalive: list = []  # prevent GC of workers and threads

    def run_case(idx: int):
        if idx >= len(cases):
            print("\nALL CASES DONE")
            for name, status in results:
                print(f"  {name}: {status}")
            QTimer.singleShot(100, app.quit)
            return

        name, srcs = cases[idx]
        # Clear cache between cases
        for f in cache.glob("preview_*.mp4"):
            try:
                f.unlink()
            except OSError:
                pass
        print(f"\n=== {name} ===")
        proj = make_project(srcs)

        worker = Worker(proj)
        thread = QThread(app)  # parent = app keeps it alive
        worker.setParent(None)  # workers can't have a parent before moveToThread
        worker.moveToThread(thread)
        keepalive.append((worker, thread))
        thread.started.connect(worker.run)

        def on_progress(p: float):
            print(f"  progress: {p * 100:.0f}%")

        def on_finished(path: str):
            print(f"  finished: {path}")
            results.append((name, "OK"))
            thread.quit()
            thread.wait(2000)
            QTimer.singleShot(50, lambda: run_case(idx + 1))

        def on_failed(err: str):
            print(f"  FAILED: {err}")
            results.append((name, f"FAIL: {err[:120]}"))
            thread.quit()
            thread.wait(2000)
            QTimer.singleShot(50, lambda: run_case(idx + 1))

        worker.progress.connect(on_progress)
        worker.finished.connect(on_finished)
        worker.failed.connect(on_failed)

        # 25 second timeout per case
        def timeout():
            if not thread.isFinished():
                print("  TIMEOUT after 25s — killing.")
                results.append((name, "HANG (timeout)"))
                thread.quit()
                thread.wait(1000)
                QTimer.singleShot(50, lambda: run_case(idx + 1))

        QTimer.singleShot(25000, timeout)
        thread.start()

    QTimer.singleShot(0, lambda: run_case(0))
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
