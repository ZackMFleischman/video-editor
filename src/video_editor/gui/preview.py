"""Source-clip preview + timeline playback.

Two modes:
- Single-clip preview: clicking a clip in the timeline loads it here, auto-plays.
- Timeline play: pressing Space / Play in the main window plays the whole
  project, swapping the underlying media as the playhead crosses clips.

Backends: libvlc (preferred, via python-vlc) or QMediaPlayer (fallback).

Limitation in timeline preview: the underlying player can only render one
source at a time, so we show the topmost video track at each moment (or, if
no video is present, the topmost audio clip). The exported file uses the
full multi-track FFmpeg composite — not this preview.
"""
from __future__ import annotations

import logging
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional

from PyQt6.QtCore import QObject, QThread, QTimer, Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QSlider,
    QVBoxLayout,
    QWidget,
)


class _PreviewRenderWorker(QObject):
    progress = pyqtSignal(float)
    finished = pyqtSignal(str)
    failed = pyqtSignal(str)

    def __init__(self, project: Project):
        super().__init__()
        self._project = project

    def run(self):
        try:
            out = preview_render.render_preview(self._project, progress=self.progress.emit)
            self.finished.emit(str(out))
        except Exception as e:
            log.exception("preview render failed")
            self.failed.emit(str(e))

from ..core.model import Project, TrackType
from ..core import preview_render
from ..core.ffmpeg_engine import ffmpeg_available


log = logging.getLogger(__name__)


def _try_import_vlc():
    try:
        import vlc  # type: ignore
        vlc.Instance("--quiet")
        log.info(
            "python-vlc loaded; libvlc version: %s",
            vlc.libvlc_get_version().decode(errors="replace") if hasattr(vlc, "libvlc_get_version") else "?",
        )
        return vlc
    except Exception:
        log.exception("Failed to load python-vlc / libvlc; falling back to Qt backend.")
        return None


_vlc = _try_import_vlc()


class PreviewPanel(QWidget):
    positionChanged = pyqtSignal(float)
    timelinePlayheadChanged = pyqtSignal(float)  # emitted only during timeline play

    def __init__(self):
        super().__init__()
        self._current_path: Optional[str] = None
        self._project: Optional[Project] = None

        # --- timeline playback state ---
        self._tl_playing = False
        self._tl_pos = 0.0
        self._tl_rendered_path: Optional[str] = None  # currently-loaded preview file
        self._tl_pending_play_at: Optional[float] = None  # play-after-render request
        self._tl_render_thread: Optional[QThread] = None
        self._tl_timer = QTimer(self)
        self._tl_timer.setInterval(50)
        self._tl_timer.timeout.connect(self._tl_tick)
        self._tl_resume_after_scrub = False

        # --- common controls ---
        self.play_btn = QPushButton("▶")
        self.play_btn.setFixedWidth(40)
        self.play_btn.clicked.connect(self._toggle_play)

        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setRange(0, 1000)
        # Slider scrubs the TIMELINE (not the underlying clip) when a project exists.
        self.slider.sliderPressed.connect(self._on_slider_pressed)
        self.slider.sliderMoved.connect(self._on_slider_moved)
        self.slider.sliderReleased.connect(self._on_slider_released)

        self.time_lbl = QLabel("0:00 / 0:00")
        self.time_lbl.setMinimumWidth(110)
        self.time_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.open_ext_btn = QPushButton("Open externally")
        self.open_ext_btn.setToolTip("Open this clip in your system's default video player.")
        self.open_ext_btn.clicked.connect(self._open_externally)
        self.open_ext_btn.setEnabled(False)

        self.source_lbl = QLabel("(no clip loaded)")
        self.source_lbl.setStyleSheet("color: #aaa;")
        self.source_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.error_lbl = QLabel("")
        self.error_lbl.setStyleSheet("color: #ff8080;")
        self.error_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.error_lbl.setWordWrap(True)
        self.error_lbl.hide()

        self.backend_lbl = QLabel("")
        self.backend_lbl.setStyleSheet("color: #777; font-size: 10px;")
        self.backend_lbl.setAlignment(Qt.AlignmentFlag.AlignRight)

        if _vlc is not None:
            self._backend: _Backend = _VLCBackend(self)
            self.backend_lbl.setText("backend: VLC")
        else:
            self._backend = _QtBackend(self)
            self.backend_lbl.setText("backend: Qt (libvlc not found)")

        # Render-progress overlay (shown above the video area while rendering)
        self.render_overlay = QWidget()
        self.render_overlay.setStyleSheet("background-color: rgba(0,0,0,180);")
        self.render_overlay.hide()
        ov = QVBoxLayout(self.render_overlay)
        ov.setContentsMargins(40, 20, 40, 20)
        self.render_lbl = QLabel("Rendering preview…")
        self.render_lbl.setStyleSheet("color: white; font-size: 14px;")
        self.render_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.render_bar = QProgressBar()
        self.render_bar.setRange(0, 100)
        ov.addStretch(1)
        ov.addWidget(self.render_lbl)
        ov.addWidget(self.render_bar)
        ov.addStretch(1)

        ctrl = QHBoxLayout()
        ctrl.addWidget(self.play_btn)
        ctrl.addWidget(self.slider)
        ctrl.addWidget(self.time_lbl)
        ctrl.addWidget(self.open_ext_btn)

        # Stack the video widget and the render overlay so the overlay can sit
        # on top during render.
        from PyQt6.QtWidgets import QStackedLayout
        video_container = QWidget()
        stack = QStackedLayout(video_container)
        stack.setStackingMode(QStackedLayout.StackingMode.StackAll)
        stack.addWidget(self._backend.widget())
        stack.addWidget(self.render_overlay)
        # Make sure the video stays on the bottom and the overlay paints over it
        self.render_overlay.raise_()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.addWidget(video_container, 1)
        layout.addWidget(self.source_lbl)
        layout.addWidget(self.error_lbl)
        layout.addLayout(ctrl)
        layout.addWidget(self.backend_lbl)

        self._tick = QTimer(self)
        self._tick.setInterval(200)
        self._tick.timeout.connect(self._refresh_position)
        self._tick.start()

    # ---- single-clip API ----

    def set_project(self, project: Optional[Project]):
        self._project = project

    def load(self, path: Optional[str]):
        # Switching to single-clip preview cancels any timeline playback.
        if self._tl_playing:
            self.pause_timeline()
        self._current_path = path
        self.error_lbl.hide()
        self.error_lbl.setText("")
        if not path:
            self._backend.load(None)
            self.source_lbl.setText("(no clip loaded)")
            self.open_ext_btn.setEnabled(False)
            return
        self.source_lbl.setText(Path(path).name)
        self.open_ext_btn.setEnabled(True)
        try:
            self._backend.load_and_play(path, 0.0)
        except Exception as e:
            self._show_error(str(e))

    # ---- timeline-play API ----

    def is_timeline_playing(self) -> bool:
        return self._tl_playing

    def is_rendering(self) -> bool:
        return self._tl_render_thread is not None

    def play_timeline_from(self, t: float) -> bool:
        """Play the whole timeline from time `t` (seconds)."""
        if not self._project or self._project.duration <= 0:
            return False
        if not ffmpeg_available():
            self._show_error("FFmpeg is required for timeline playback.")
            return False
        self._tl_pos = max(0.0, min(t, self._project.duration))
        self.error_lbl.hide()
        if preview_render.is_cached(self._project):
            self._tl_play_cached(self._tl_pos, autoplay=True)
            return True
        # Render in background
        self._tl_pending_play_at = self._tl_pos
        self._start_render(then_autoplay=True)
        return True

    def pause_timeline(self):
        if not self._tl_playing:
            return
        self._tl_playing = False
        self._tl_timer.stop()
        try:
            self._backend.pause_only()
        except Exception:
            pass
        log.info("Timeline paused at %.2fs", self._tl_pos)

    def stop_timeline(self):
        self.pause_timeline()
        self._tl_pos = 0.0

    def _tl_play_cached(self, t: float, autoplay: bool):
        """Load the cached preview file and (optionally) start playing at `t`."""
        path = str(preview_render.cached_path(self._project))
        self._tl_rendered_path = path
        self._current_path = path
        self.source_lbl.setText(f"timeline preview — {self._project.duration:.1f}s")
        self.open_ext_btn.setEnabled(True)
        try:
            self._backend.load_and_play(path, t)
        except Exception as e:
            self._show_error(str(e))
            return
        if not autoplay:
            QTimer.singleShot(120, self._backend.pause_only)
            self._tl_playing = False
            return
        self._tl_playing = True
        self._tl_timer.start()
        log.info("Timeline play (rendered) from %.2fs", t)

    def _tl_tick(self):
        if not self._tl_playing or not self._project:
            return
        # Advance based on the underlying player's position so audio and video
        # stay in sync even if a frame stutters.
        try:
            pos_ms, dur_ms, playing = self._backend.position_state()
        except Exception:
            return
        if dur_ms <= 0:
            return
        self._tl_pos = pos_ms / 1000.0
        # Stop at end (player may keep going slightly past end)
        if self._tl_pos >= self._project.duration - 0.02:
            self._tl_pos = self._project.duration
            self.pause_timeline()
            self.timelinePlayheadChanged.emit(self._tl_pos)
            return
        self.timelinePlayheadChanged.emit(self._tl_pos)

    # ---- background render orchestration ----

    def _start_render(self, then_autoplay: bool):
        if self._tl_render_thread is not None:
            return  # already rendering
        if not self._project or not self._project.clips:
            return
        self.render_overlay.show()
        self.render_lbl.setText("Rendering preview…")
        self.render_bar.setValue(0)
        worker = _PreviewRenderWorker(self._project)
        thread = QThread(self)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.progress.connect(lambda p: self.render_bar.setValue(int(p * 100)))
        worker.finished.connect(lambda path: self._on_render_done(path, then_autoplay))
        worker.failed.connect(self._on_render_failed)
        worker.finished.connect(thread.quit)
        worker.failed.connect(thread.quit)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(self._clear_render_thread)
        self._tl_render_thread = thread
        thread.start()

    def _clear_render_thread(self):
        self._tl_render_thread = None

    def _on_render_done(self, path: str, then_autoplay: bool):
        self.render_overlay.hide()
        if then_autoplay and self._tl_pending_play_at is not None:
            t = self._tl_pending_play_at
            self._tl_pending_play_at = None
            self._tl_play_cached(t, autoplay=True)
        else:
            t = self._tl_pos
            self._tl_play_cached(t, autoplay=False)

    def _on_render_failed(self, err: str):
        self.render_overlay.hide()
        self._show_error(f"Preview render failed: {err}")

    def _find_top_clip_at(self, t: float):
        if not self._project:
            return None
        # Prefer a video clip on the highest video track
        for track in reversed(self._project.tracks):
            if track.type != TrackType.VIDEO:
                continue
            for clip in self._project.clips_for_track(track.id):
                if clip.start <= t < clip.end:
                    return clip
        # Otherwise any clip (audio-only at this time)
        for track in reversed(self._project.tracks):
            for clip in self._project.clips_for_track(track.id):
                if clip.start <= t < clip.end:
                    return clip
        return None

    # ---- common controls ----

    def _toggle_play(self):
        # The play button targets whichever mode is active.
        if self._tl_playing:
            self.pause_timeline()
            return
        if not self._current_path:
            return
        try:
            self._backend.toggle_play()
        except Exception as e:
            self._show_error(str(e))

    # ---- slider (timeline-scope when project loaded) ----

    def _on_slider_pressed(self):
        if self._project and self._project.duration > 0:
            self._tl_resume_after_scrub = self._tl_playing
            if self._tl_playing:
                self.pause_timeline()

    def _on_slider_moved(self, val: int):
        if not self._project or self._project.duration <= 0:
            try:
                self._backend.seek_fraction(val / 1000.0)
            except Exception:
                pass
            return
        t = (val / 1000.0) * self._project.duration
        self._scrub_to(t)
        # Notify the timeline view so its visual playhead moves along
        self.timelinePlayheadChanged.emit(t)

    def _on_slider_released(self):
        if not self._project or self._project.duration <= 0:
            return
        t = (self.slider.value() / 1000.0) * self._project.duration
        if self._tl_resume_after_scrub:
            self.play_timeline_from(t)
        self._tl_resume_after_scrub = False

    def _scrub_to(self, t: float):
        """Show the frame at timeline time `t` without playing."""
        if not self._project:
            return
        self._tl_pos = t
        if preview_render.is_cached(self._project):
            self._tl_play_cached(t, autoplay=False)
            return
        # No cached render yet — show the source clip's frame at this time as
        # an approximation while waiting for the render. (No effects/text.)
        clip = self._find_top_clip_at(t)
        if clip is None:
            try:
                self._backend.pause_only()
            except Exception:
                pass
            self.source_lbl.setText("(no clip at this time)")
            return
        offset = t - clip.start
        source_time = max(0.0, clip.in_point + offset * clip.speed)
        self._current_path = clip.source_path
        self.source_lbl.setText(f"{Path(clip.source_path).name}  (raw — render to see effects)")
        self.open_ext_btn.setEnabled(True)
        try:
            self._backend.load_and_play(clip.source_path, source_time)
            QTimer.singleShot(120, self._backend.pause_only)
        except Exception as e:
            self._show_error(str(e))

    def _refresh_position(self):
        # Don't fight the user while they're scrubbing
        if self.slider.isSliderDown():
            return
        if self._tl_playing:
            if self._project and self._project.duration > 0:
                frac = self._tl_pos / self._project.duration
                self.slider.blockSignals(True)
                self.slider.setValue(int(1000 * frac))
                self.slider.blockSignals(False)
                self.time_lbl.setText(
                    f"{_fmt(int(self._tl_pos * 1000))} / {_fmt(int(self._project.duration * 1000))}"
                )
            self.play_btn.setText("⏸")
            return
        # Paused: if a project exists, slider represents the timeline; otherwise
        # represents the underlying clip.
        if self._project and self._project.duration > 0:
            frac = self._tl_pos / self._project.duration if self._project.duration > 0 else 0.0
            self.slider.blockSignals(True)
            self.slider.setValue(int(1000 * frac))
            self.slider.blockSignals(False)
            self.time_lbl.setText(
                f"{_fmt(int(self._tl_pos * 1000))} / {_fmt(int(self._project.duration * 1000))}"
            )
            self.play_btn.setText("▶")
            return
        if not self._current_path:
            return
        try:
            pos, dur, playing = self._backend.position_state()
        except Exception:
            return
        self.play_btn.setText("⏸" if playing else "▶")
        if dur > 0:
            self.slider.blockSignals(True)
            self.slider.setValue(int(1000 * pos / dur))
            self.slider.blockSignals(False)
            self.time_lbl.setText(f"{_fmt(pos)} / {_fmt(dur)}")
            self.positionChanged.emit(pos / 1000.0)
        else:
            self.time_lbl.setText("0:00 / 0:00")

    def _show_error(self, msg: str):
        self.error_lbl.setText(
            f"Preview error: {msg}\n"
            f"Use 'Open externally' to view the file. Export still works."
        )
        self.error_lbl.show()

    def _open_externally(self):
        if not self._current_path:
            return
        try:
            if sys.platform.startswith("win"):
                os.startfile(self._current_path)  # type: ignore[attr-defined]
            elif sys.platform == "darwin":
                subprocess.Popen(["open", self._current_path])
            else:
                subprocess.Popen(["xdg-open", self._current_path])
        except Exception as e:
            self._show_error(str(e))


# ---------------- backends ----------------

class _Backend:
    def widget(self) -> QWidget: ...
    def load(self, path: Optional[str]) -> None: ...
    def load_and_play(self, path: str, seek_seconds: float = 0.0) -> None: ...
    def toggle_play(self) -> None: ...
    def pause_only(self) -> None: ...
    def seek_fraction(self, frac: float) -> None: ...
    def prepare(self, path: str) -> None:
        """Pre-parse a media file so a subsequent load_and_play is fast."""
        pass
    def position_state(self) -> tuple[int, int, bool]:
        return (0, 0, False)


class _VLCBackend(_Backend):
    def __init__(self, parent: PreviewPanel):
        self._parent = parent
        self._instance = _vlc.Instance("--quiet")
        if self._instance is None:
            raise RuntimeError("vlc.Instance() returned None — is libvlc on PATH?")
        self._player = self._instance.media_player_new()
        self._frame = QFrame()
        self._frame.setStyleSheet("background-color: #000;")
        self._frame.setMinimumHeight(240)
        self._bound = False
        # Cache of pre-parsed media. We MUST hold references; otherwise the
        # async parse gets cancelled when the Media is GC'd.
        self._prepared: dict[str, object] = {}
        # Single, persistent handler for the Playing event. We update
        # _pending_seek_ms before each play() call.
        self._pending_seek_ms = 0
        try:
            self._player.event_manager().event_attach(
                _vlc.EventType.MediaPlayerPlaying, self._on_playing
            )
        except Exception:
            log.exception("Could not attach MediaPlayerPlaying handler")
        log.info("VLC backend ready")

    def _on_playing(self, _event):
        ms = self._pending_seek_ms
        if ms > 0:
            try:
                self._player.set_time(ms)
            except Exception:
                pass
            self._pending_seek_ms = 0

    def widget(self) -> QWidget:
        return self._frame

    def _bind_hwnd(self):
        if self._bound:
            return
        wid = int(self._frame.winId())
        if sys.platform.startswith("win"):
            self._player.set_hwnd(wid)
        elif sys.platform == "darwin":
            self._player.set_nsobject(wid)
        else:
            self._player.set_xwindow(wid)
        self._bound = True

    def load(self, path: Optional[str]) -> None:
        self._bind_hwnd()
        if not path:
            self._player.stop()
            self._player.set_media(None)
            return
        log.info("VLC: load (no autoplay) %s", path)
        media = self._instance.media_new(path)
        self._player.set_media(media)

    def load_and_play(self, path: str, seek_seconds: float = 0.0) -> None:
        self._bind_hwnd()
        log.info("VLC: load_and_play %s @%.2fs", path, seek_seconds)
        media = self._prepared.pop(path, None) or self._instance.media_new(path)
        self._player.set_media(media)
        # Set up the seek to happen as soon as VLC starts playing.
        self._pending_seek_ms = int(seek_seconds * 1000) if seek_seconds > 0.05 else 0
        # Best-effort early seek before play; the Playing-event handler does it again.
        if self._pending_seek_ms > 0:
            try:
                self._player.set_time(self._pending_seek_ms)
            except Exception:
                pass
        self._player.play()

    def prepare(self, path: str) -> None:
        if path in self._prepared:
            return
        try:
            media = self._instance.media_new(path)
            media.parse_with_options(_vlc.MediaParseFlag.local, 0)  # async
            self._prepared[path] = media  # hold ref; otherwise the parse is lost
        except Exception:
            pass

    def toggle_play(self) -> None:
        self._bind_hwnd()
        if self._player.is_playing():
            self._player.pause()
        else:
            self._player.play()

    def pause_only(self) -> None:
        if self._player.is_playing():
            self._player.pause()

    def seek_fraction(self, frac: float) -> None:
        if self._player.get_length() > 0:
            self._player.set_position(max(0.0, min(1.0, frac)))

    def position_state(self) -> tuple[int, int, bool]:
        return (
            max(0, int(self._player.get_time())),
            max(0, int(self._player.get_length())),
            bool(self._player.is_playing()),
        )


class _QtBackend(_Backend):
    def __init__(self, parent: PreviewPanel):
        from PyQt6.QtCore import QUrl
        from PyQt6.QtMultimedia import QAudioOutput, QMediaPlayer
        from PyQt6.QtMultimediaWidgets import QVideoWidget

        self._QUrl = QUrl
        self._QMediaPlayer = QMediaPlayer
        self._parent = parent
        self._player = QMediaPlayer(parent)
        self._audio = QAudioOutput(parent)
        self._player.setAudioOutput(self._audio)
        self._video = QVideoWidget(parent)
        self._video.setMinimumHeight(240)
        self._video.setStyleSheet("background-color: #000;")
        self._player.setVideoOutput(self._video)
        self._player.errorOccurred.connect(self._on_error)
        self._player.mediaStatusChanged.connect(self._on_status)

    def widget(self) -> QWidget:
        return self._video

    def load(self, path: Optional[str]) -> None:
        if not path:
            self._player.setSource(self._QUrl())
            return
        self._player.setSource(self._QUrl.fromLocalFile(path))
        self._player.pause()

    def load_and_play(self, path: str, seek_seconds: float = 0.0) -> None:
        self._player.setSource(self._QUrl.fromLocalFile(path))
        if seek_seconds > 0.05:
            self._player.setPosition(int(seek_seconds * 1000))
        self._player.play()

    def toggle_play(self) -> None:
        if self._player.playbackState() == self._QMediaPlayer.PlaybackState.PlayingState:
            self._player.pause()
        else:
            self._player.play()

    def pause_only(self) -> None:
        if self._player.playbackState() == self._QMediaPlayer.PlaybackState.PlayingState:
            self._player.pause()

    def seek_fraction(self, frac: float) -> None:
        dur = self._player.duration()
        if dur > 0:
            self._player.setPosition(int(dur * frac))

    def position_state(self) -> tuple[int, int, bool]:
        return (
            self._player.position(),
            self._player.duration(),
            self._player.playbackState() == self._QMediaPlayer.PlaybackState.PlayingState,
        )

    def _on_error(self, error, error_string: str):
        if error == self._QMediaPlayer.Error.NoError:
            return
        self._parent._show_error(error_string or "playback error")

    def _on_status(self, status):
        if status == self._QMediaPlayer.MediaStatus.InvalidMedia:
            self._parent._show_error(self._player.errorString() or "invalid media")


def _fmt(ms: int) -> str:
    s = max(0, ms // 1000)
    m, s = divmod(s, 60)
    return f"{m}:{s:02d}"
