"""Main editor window."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from PyQt6.QtCore import QObject, Qt, QThread, pyqtSignal
from PyQt6.QtGui import QAction, QKeySequence
from PyQt6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QProgressDialog,
    QSplitter,
    QStatusBar,
    QToolBar,
    QVBoxLayout,
    QWidget,
)

from ..core import config
from ..core.ffmpeg_engine import ffmpeg_available, probe, render_project
from ..core.model import Clip, Project, TrackType, TransitionType
from .dialogs import AISpeechDialog, SettingsDialog
from .preview import PreviewPanel
from .properties import PropertiesPanel
from .timeline import TimelineView


class _RenderWorker(QObject):
    progress = pyqtSignal(float)
    finished = pyqtSignal(str)
    failed = pyqtSignal(str)

    def __init__(self, project: Project, out_path: str):
        super().__init__()
        self._project = project
        self._out = out_path

    def run(self):
        try:
            render_project(self._project, self._out, progress=self.progress.emit)
            self.finished.emit(self._out)
        except Exception as e:
            self.failed.emit(str(e))


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Video Editor")
        self.resize(1500, 1000)

        self.project = Project()
        self.project_dir = self._default_project_dir()

        # Widgets
        self.preview = PreviewPanel()
        self.properties = PropertiesPanel()
        self.timeline = TimelineView()
        self.timeline.set_project(self.project)
        self.properties.set_project(self.project)
        self.preview.set_project(self.project)

        # Layout: top split (preview | properties), bottom: timeline
        top = QSplitter(Qt.Orientation.Horizontal)
        top.addWidget(self.preview)
        top.addWidget(self.properties)
        top.setStretchFactor(0, 3)
        top.setStretchFactor(1, 1)

        # Make sure neither pane gets squashed when the user resizes the window.
        self.timeline.setMinimumHeight(260)
        self.preview.setMinimumHeight(280)

        outer = QSplitter(Qt.Orientation.Vertical)
        outer.addWidget(top)
        outer.addWidget(self.timeline)
        outer.setStretchFactor(0, 3)
        outer.setStretchFactor(1, 4)
        outer.setSizes([520, 460])

        container = QWidget()
        container_layout = QVBoxLayout(container)
        container_layout.setContentsMargins(0, 0, 0, 0)
        container_layout.addWidget(outer)
        self.setCentralWidget(container)

        self.setStatusBar(QStatusBar())

        self._build_menus()
        self._build_toolbar()
        self._wire_signals()
        self._workers: list[QThread] = []
        self._update_title()

        if not ffmpeg_available():
            self.statusBar().showMessage(
                "FFmpeg not found on PATH — install ffmpeg to render and probe media."
            )

    # ---- defaults ----

    def _default_project_dir(self) -> Path:
        d = Path.home() / "Videos" / "VideoEditorProjects"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _update_title(self):
        name = self.project.name or "Untitled"
        suffix = ""
        if self.project.file_path:
            suffix = f" — {self.project.file_path}"
        self.setWindowTitle(f"Video Editor — {name}{suffix}")

    # ---- menus & toolbar ----

    def _build_menus(self):
        mb = self.menuBar()

        file_menu = mb.addMenu("&File")
        self._add_action(file_menu, "&New Project", self._new_project, QKeySequence.StandardKey.New)
        self._add_action(file_menu, "&Open Project...", self._open_project, QKeySequence.StandardKey.Open)
        self._add_action(file_menu, "&Save Project", self._save_project, QKeySequence.StandardKey.Save)
        self._add_action(file_menu, "Save Project &As...", self._save_project_as, QKeySequence.StandardKey.SaveAs)
        file_menu.addSeparator()
        self._add_action(file_menu, "&Import Media...", self._import_media, QKeySequence("Ctrl+I"))
        self._add_action(file_menu, "&Export Video...", self._export_video, QKeySequence("Ctrl+E"))
        file_menu.addSeparator()
        self._add_action(file_menu, "Settings...", self._open_settings)
        file_menu.addSeparator()
        self._add_action(file_menu, "E&xit", self.close, QKeySequence("Ctrl+Q"))

        edit_menu = mb.addMenu("&Edit")
        self._add_action(edit_menu, "Play / Pause timeline", self._toggle_timeline_play, QKeySequence(Qt.Key.Key_Space))
        self._add_action(edit_menu, "Split clip at playhead", self._split_at_playhead, QKeySequence("S"))
        self._add_action(edit_menu, "Delete selected clip", self._delete_selected, QKeySequence(Qt.Key.Key_Delete))

        tracks_menu = mb.addMenu("&Tracks")
        self._add_action(tracks_menu, "Add video track", lambda: self._add_track(TrackType.VIDEO))
        self._add_action(tracks_menu, "Add audio track", lambda: self._add_track(TrackType.AUDIO))

        tools_menu = mb.addMenu("T&ools")
        self._add_action(tools_menu, "AI Speech Overlay...", self._open_ai_speech, QKeySequence("Ctrl+Shift+A"))

        help_menu = mb.addMenu("&Help")
        self._add_action(help_menu, "About", self._about)

    def _add_action(self, menu, text, slot, shortcut: Optional[QKeySequence | str] = None):
        a = QAction(text, self)
        a.triggered.connect(slot)
        if shortcut:
            a.setShortcut(shortcut)
        menu.addAction(a)
        return a

    def _build_toolbar(self):
        tb = QToolBar()
        tb.setMovable(False)
        self.addToolBar(tb)
        self.play_action = tb.addAction("▶ Play", self._toggle_timeline_play)
        self.play_action.setToolTip("Play / pause the timeline (Space)")
        tb.addSeparator()
        tb.addAction("Import", self._import_media)
        tb.addAction("Split", self._split_at_playhead)
        tb.addAction("Delete", self._delete_selected)
        tb.addSeparator()
        tb.addAction("AI Speech", self._open_ai_speech)
        tb.addSeparator()
        tb.addAction("Export", self._export_video)
        tb.addSeparator()
        tb.addWidget(QLabel("Zoom:"))
        tb.addAction("-", lambda: self.timeline.set_zoom(self.timeline.px_per_sec / 1.25))
        tb.addAction("+", lambda: self.timeline.set_zoom(self.timeline.px_per_sec * 1.25))

    # ---- wiring ----

    def _wire_signals(self):
        self.timeline.clipSelected.connect(self._on_clip_selected)
        self.timeline.clipChanged.connect(lambda _id: (self.properties.set_clip(self.project.get_clip(_id)), self.timeline._refresh_layout()))
        self.timeline.playheadMoved.connect(self._on_playhead_moved)
        self.properties.clipUpdated.connect(lambda _id: self.timeline._refresh_layout())
        self.preview.timelinePlayheadChanged.connect(self.timeline.set_playhead)

    def _on_playhead_moved(self, t: float):
        # User scrubbed the timeline ruler. If we were playing, restart from there.
        if self.preview.is_timeline_playing():
            self.preview.play_timeline_from(t)

    def _toggle_timeline_play(self):
        if self.preview.is_timeline_playing():
            self.preview.pause_timeline()
            if hasattr(self, "play_action"):
                self.play_action.setText("▶ Play")
            return
        if not self.project.clips:
            self.statusBar().showMessage("Add some clips first.", 3000)
            return
        # If playhead is at the end, rewind
        start_t = self.timeline.playhead
        if start_t >= self.project.duration - 0.05:
            start_t = 0.0
            self.timeline.set_playhead(0.0)
        ok = self.preview.play_timeline_from(start_t)
        if ok and hasattr(self, "play_action"):
            self.play_action.setText("⏸ Pause")

    def _on_clip_selected(self, clip_id: str):
        clip = self.project.get_clip(clip_id)
        if not clip:
            return
        self.properties.set_clip(clip)
        self.preview.load(clip.source_path)

    # ---- file ops ----

    def _new_project(self):
        self.project = Project()
        self.timeline.set_project(self.project)
        self.properties.set_project(self.project)
        self.preview.set_project(self.project)
        self.preview.load(None)
        self._update_title()

    def _open_project(self):
        path, _ = QFileDialog.getOpenFileName(self, "Open Project", str(self.project_dir), "Video Editor Project (*.vproj)")
        if not path:
            return
        try:
            self.project = Project.load(path)
        except Exception as e:
            QMessageBox.critical(self, "Open failed", str(e))
            return
        self.timeline.set_project(self.project)
        self.properties.set_project(self.project)
        self.preview.set_project(self.project)
        self._update_title()

    def _save_project(self):
        if not self.project.file_path:
            return self._save_project_as()
        try:
            self.project.save(self.project.file_path)
            self.statusBar().showMessage(f"Saved {self.project.file_path}", 3000)
        except Exception as e:
            QMessageBox.critical(self, "Save failed", str(e))

    def _save_project_as(self):
        default = str(self.project_dir / f"{self.project.name or 'project'}.vproj")
        path, _ = QFileDialog.getSaveFileName(self, "Save Project", default, "Video Editor Project (*.vproj)")
        if not path:
            return
        if not path.lower().endswith(".vproj"):
            path += ".vproj"
        try:
            self.project.save(path)
            self.project.name = Path(path).stem
            self._update_title()
            self.statusBar().showMessage(f"Saved {path}", 3000)
        except Exception as e:
            QMessageBox.critical(self, "Save failed", str(e))

    def _import_media(self):
        start_dir = str(Path.home() / "Downloads")
        paths, _ = QFileDialog.getOpenFileNames(
            self,
            "Import media",
            start_dir,
            "Media files (*.mp4 *.mov *.mkv *.webm *.avi *.mp3 *.wav *.m4a *.aac);;All files (*)",
        )
        if not paths:
            return
        for p in paths:
            self._add_media(p)
        self.timeline._refresh_layout()

    def _add_media(self, path: str):
        try:
            info = probe(path)
        except Exception as e:
            QMessageBox.warning(self, "Probe failed", f"{path}\n\n{e}")
            return

        # Find or create a target track
        if info.has_video:
            track = next((t for t in self.project.tracks if t.type == TrackType.VIDEO), None)
            if track is None:
                track = self.project.add_track(TrackType.VIDEO)
        else:
            track = next((t for t in self.project.tracks if t.type == TrackType.AUDIO), None)
            if track is None:
                track = self.project.add_track(TrackType.AUDIO)

        # Append at end of that track
        existing = self.project.clips_for_track(track.id)
        start = existing[-1].end if existing else 0.0
        clip = Clip(
            source_path=path,
            track_id=track.id,
            start=start,
            in_point=0.0,
            out_point=info.duration if info.duration > 0 else 0.0,
        )
        self.project.add_clip(clip)
        self.statusBar().showMessage(f"Imported {Path(path).name}", 3000)

    # ---- editing ----

    def _split_at_playhead(self):
        cid = self.timeline.selected_clip_id()
        clips = [self.project.get_clip(cid)] if cid else []
        if not clips or clips[0] is None:
            # Fall back: split any clip under the playhead
            t = self.timeline.playhead
            clips = [c for c in self.project.clips if c.start < t < c.end]
        t = self.timeline.playhead
        for clip in clips:
            if clip is None or not (clip.start < t < clip.end):
                continue
            offset_into_clip = (t - clip.start) * clip.speed
            split_source_time = clip.in_point + offset_into_clip
            new_clip = Clip(
                source_path=clip.source_path,
                track_id=clip.track_id,
                start=t,
                in_point=split_source_time,
                out_point=clip.out_point,
                speed=clip.speed,
                volume=clip.volume,
            )
            clip.out_point = split_source_time
            self.project.add_clip(new_clip)
        self.timeline._refresh_layout()

    def _delete_selected(self):
        cid = self.timeline.selected_clip_id()
        if not cid:
            return
        self.project.remove_clip(cid)
        self.properties.set_clip(None)
        self.timeline._refresh_layout()

    def _add_track(self, kind: TrackType):
        self.project.add_track(kind)
        self.timeline._refresh_layout()

    # ---- AI speech ----

    def _open_ai_speech(self):
        if not config.get("elevenlabs_api_key"):
            QMessageBox.information(
                self, "API key needed",
                "Set your ElevenLabs API key in File > Settings first."
            )
            return
        # Use saved project's directory if available, else default
        if self.project.file_path:
            pdir = Path(self.project.file_path).parent
        else:
            pdir = self.project_dir
        dlg = AISpeechDialog(pdir, self)
        dlg.speechReady.connect(self._insert_speech_clip)
        dlg.exec()

    def _insert_speech_clip(self, audio_path: str):
        try:
            info = probe(audio_path)
        except Exception as e:
            QMessageBox.warning(self, "Audio probe failed", str(e))
            return
        # Insert on first audio track at playhead
        track = next((t for t in self.project.tracks if t.type == TrackType.AUDIO), None)
        if track is None:
            track = self.project.add_track(TrackType.AUDIO)
        clip = Clip(
            source_path=audio_path,
            track_id=track.id,
            start=self.timeline.playhead,
            in_point=0.0,
            out_point=info.duration if info.duration > 0 else 0.0,
        )
        self.project.add_clip(clip)
        self.timeline._refresh_layout()
        self.statusBar().showMessage(f"Inserted speech: {Path(audio_path).name}", 3000)

    # ---- export ----

    def _export_video(self):
        if not self.project.clips:
            QMessageBox.information(self, "Nothing to export", "Add some clips first.")
            return
        if not ffmpeg_available():
            QMessageBox.critical(self, "FFmpeg missing", "Install FFmpeg and ensure it is on your PATH.")
            return
        default = str(self.project_dir / f"{self.project.name or 'export'}.mp4")
        path, _ = QFileDialog.getSaveFileName(self, "Export video", default, "MP4 (*.mp4)")
        if not path:
            return
        if not path.lower().endswith(".mp4"):
            path += ".mp4"

        progress = QProgressDialog("Rendering...", "Cancel", 0, 100, self)
        progress.setWindowModality(Qt.WindowModality.WindowModal)
        progress.setAutoClose(True)
        progress.setValue(0)

        worker = _RenderWorker(self.project, path)
        thread = QThread(self)
        worker.moveToThread(thread)

        def _on_progress(p: float):
            progress.setValue(int(p * 100))

        def _on_done(out_path: str):
            progress.setValue(100)
            QMessageBox.information(self, "Export complete", f"Saved to:\n{out_path}")

        def _on_fail(err: str):
            progress.cancel()
            QMessageBox.critical(self, "Render failed", err)

        thread.started.connect(worker.run)
        worker.progress.connect(_on_progress)
        worker.finished.connect(_on_done)
        worker.failed.connect(_on_fail)
        worker.finished.connect(thread.quit)
        worker.failed.connect(thread.quit)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        self._workers.append(thread)
        thread.finished.connect(lambda: self._workers.remove(thread) if thread in self._workers else None)
        thread.start()

    # ---- misc ----

    def _open_settings(self):
        dlg = SettingsDialog(self)
        dlg.exec()

    def _about(self):
        QMessageBox.about(
            self,
            "About",
            "Video Editor\n\nPyQt6 + FFmpeg.\nAI speech via Anthropic Claude + ElevenLabs.",
        )
