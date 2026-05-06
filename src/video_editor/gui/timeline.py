"""Timeline widget: tracks, clips, playhead. Pure QGraphicsView based."""
from __future__ import annotations

from typing import Optional

from PyQt6.QtCore import QPointF, QRectF, Qt, pyqtSignal
from PyQt6.QtGui import QBrush, QColor, QFont, QMouseEvent, QPainter, QPen, QWheelEvent
from PyQt6.QtWidgets import (
    QGraphicsItem,
    QGraphicsRectItem,
    QGraphicsScene,
    QGraphicsTextItem,
    QGraphicsView,
)

from ..core.model import Clip, Project, Track, TrackType


TRACK_HEIGHT = 50
TRACK_LABEL_WIDTH = 110
RULER_HEIGHT = 26
DEFAULT_PX_PER_SEC = 80


class _ClipItem(QGraphicsRectItem):
    def __init__(self, clip: Clip, color: QColor, parent_view: "TimelineView"):
        super().__init__()
        self.clip = clip
        self.parent_view = parent_view
        self.setBrush(QBrush(color))
        self.setPen(QPen(QColor("#222"), 1))
        # We handle movement ourselves — Qt's ItemIsMovable behaved erratically here.
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, True)
        self.setAcceptHoverEvents(True)
        self._label = QGraphicsTextItem(self._label_text(), self)
        self._label.setDefaultTextColor(QColor("white"))
        f = QFont()
        f.setPointSize(8)
        self._label.setFont(f)
        self._label.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
        self._label.setAcceptHoverEvents(False)

        # Drag state — set on press, cleared on release.
        self._mode: Optional[str] = None  # "move" | "left" | "right"
        self._press_scene_x = 0.0
        self._press_scene_y = 0.0
        self._press_pos_x = 0.0
        self._press_pos_y = 0.0
        self._orig_in = 0.0
        self._orig_out = 0.0
        self._orig_start = 0.0

    def _label_text(self) -> str:
        from pathlib import Path
        return Path(self.clip.source_path).name

    def update_geometry(self, px_per_sec: float, y: float):
        x = TRACK_LABEL_WIDTH + self.clip.start * px_per_sec
        w = max(2.0, self.clip.timeline_duration * px_per_sec)
        self.setRect(0, 0, w, TRACK_HEIGHT - 8)
        self.setPos(x, y + 4)
        self._label.setPos(6, 4)

    def hoverMoveEvent(self, e):
        local_x = e.pos().x()
        w = self.rect().width()
        if local_x < 6 or local_x > w - 6:
            self.setCursor(Qt.CursorShape.SizeHorCursor)
        else:
            self.setCursor(Qt.CursorShape.SizeAllCursor)
        super().hoverMoveEvent(e)

    def mousePressEvent(self, e):
        local_x = e.pos().x()
        w = self.rect().width()
        self._press_scene_x = e.scenePos().x()
        self._press_scene_y = e.scenePos().y()
        self._press_pos_x = self.pos().x()
        self._press_pos_y = self.pos().y()
        self._orig_in = self.clip.in_point
        self._orig_out = self.clip.out_point
        self._orig_start = self.clip.start

        if local_x < 6:
            self._mode = "left"
        elif local_x > w - 6:
            self._mode = "right"
        else:
            self._mode = "move"

        # Selection — make sure this item is selected so the properties panel updates.
        scene = self.scene()
        if scene is not None:
            scene.clearSelection()
        self.setSelected(True)
        e.accept()

    def mouseMoveEvent(self, e):
        if self._mode is None:
            return
        dx_scene = e.scenePos().x() - self._press_scene_x
        if self._mode == "move":
            # Move the rect itself directly so it tracks the cursor exactly.
            new_x = max(TRACK_LABEL_WIDTH, self._press_pos_x + dx_scene)
            new_y = self._press_pos_y + (e.scenePos().y() - self._press_scene_y)
            self.setPos(new_x, new_y)
            # Update the model live (no refresh — keeps it smooth)
            timeline_x = new_x - TRACK_LABEL_WIDTH
            self.clip.start = max(0.0, timeline_x / self.parent_view.px_per_sec)
        elif self._mode == "left":
            dt = dx_scene / self.parent_view.px_per_sec
            new_in = max(0.0, self._orig_in + dt * self.clip.speed)
            if self._orig_out > 0 and new_in >= self._orig_out - 0.05:
                new_in = self._orig_out - 0.05
            delta_in = new_in - self._orig_in
            self.clip.in_point = new_in
            self.clip.start = max(0.0, self._orig_start + delta_in / self.clip.speed)
            self.parent_view._update_clip_geometry(self.clip.id)
        elif self._mode == "right":
            dt = dx_scene / self.parent_view.px_per_sec
            new_out = max(self._orig_in + 0.05, self._orig_out + dt * self.clip.speed)
            self.clip.out_point = new_out
            self.parent_view._update_clip_geometry(self.clip.id)
        e.accept()

    def mouseReleaseEvent(self, e):
        if self._mode is None:
            return
        if self._mode == "move":
            # Snap timeline start to 0.1s
            self.clip.start = round(max(0.0, self.clip.start) * 10) / 10
            # Drop onto whichever track row the rect's center is over.
            cy = self.pos().y() + (TRACK_HEIGHT - 8) / 2
            new_track_id = self.parent_view._track_at_y(cy)
            if new_track_id and new_track_id != self.clip.track_id:
                project = self.parent_view.project
                new_track = project.track_by_id(new_track_id) if project else None
                current_track = project.track_by_id(self.clip.track_id) if project else None
                if new_track and current_track and new_track.type == current_track.type:
                    self.clip.track_id = new_track_id
        self._mode = None
        self.parent_view._refresh_layout()
        self.parent_view.clipChanged.emit(self.clip.id)
        super().mouseReleaseEvent(e)


class TimelineView(QGraphicsView):
    clipSelected = pyqtSignal(str)  # clip id
    clipChanged = pyqtSignal(str)
    playheadMoved = pyqtSignal(float)  # seconds

    def __init__(self):
        super().__init__()
        self._scene = QGraphicsScene(self)
        self.setScene(self._scene)
        self.setRenderHints(QPainter.RenderHint.Antialiasing)
        self.setBackgroundBrush(QBrush(QColor("#1c1c1c")))
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        # Align scene to top-left so when the area is taller than content the
        # tracks stay pinned to the top instead of being centered.
        self.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        self.project: Optional[Project] = None
        self.px_per_sec: float = DEFAULT_PX_PER_SEC
        self.playhead: float = 0.0
        self._clip_items: dict[str, _ClipItem] = {}
        self._playhead_item: Optional[QGraphicsRectItem] = None
        self._scene.selectionChanged.connect(self._on_selection_changed)

    # ---- public ----

    def set_project(self, project: Project):
        self.project = project
        self._refresh_layout()

    def set_zoom(self, px_per_sec: float):
        self.px_per_sec = max(10.0, min(800.0, px_per_sec))
        self._refresh_layout()

    def set_playhead(self, t: float):
        self.playhead = max(0.0, t)
        self._update_playhead()

    def selected_clip_id(self) -> Optional[str]:
        for item in self._scene.selectedItems():
            if isinstance(item, _ClipItem):
                return item.clip.id
        return None

    # ---- internals ----

    def _track_at_y(self, y: float) -> Optional[str]:
        if not self.project:
            return None
        ry = y - RULER_HEIGHT
        idx = int(ry // TRACK_HEIGHT)
        if 0 <= idx < len(self.project.tracks):
            return self.project.tracks[idx].id
        return None

    def _update_clip_geometry(self, clip_id: str) -> None:
        """Update only the geometry of a single clip item (used during drag)."""
        item = self._clip_items.get(clip_id)
        if item is None or not self.project:
            return
        for i, track in enumerate(self.project.tracks):
            if track.id == item.clip.track_id:
                y = RULER_HEIGHT + i * TRACK_HEIGHT
                item.update_geometry(self.px_per_sec, y)
                return

    def _refresh_layout(self):
        self._scene.clear()
        self._clip_items.clear()
        self._playhead_item = None
        if not self.project:
            return

        duration = max(60.0, self.project.duration + 30.0)
        width = TRACK_LABEL_WIDTH + duration * self.px_per_sec + 20
        height = RULER_HEIGHT + len(self.project.tracks) * TRACK_HEIGHT + 20
        self._scene.setSceneRect(0, 0, width, height)

        # Ruler
        ruler = QGraphicsRectItem(0, 0, width, RULER_HEIGHT)
        ruler.setBrush(QBrush(QColor("#2a2a2a")))
        ruler.setPen(QPen(Qt.PenStyle.NoPen))
        self._scene.addItem(ruler)

        # Time ticks every second
        for s in range(0, int(duration) + 1):
            x = TRACK_LABEL_WIDTH + s * self.px_per_sec
            major = (s % 5 == 0)
            tick = QGraphicsRectItem(x, RULER_HEIGHT - (10 if major else 5), 1, 10 if major else 5)
            tick.setBrush(QBrush(QColor("#888")))
            tick.setPen(QPen(Qt.PenStyle.NoPen))
            self._scene.addItem(tick)
            if major:
                lbl = QGraphicsTextItem(_format_time(s))
                lbl.setDefaultTextColor(QColor("#ccc"))
                f = QFont(); f.setPointSize(7); lbl.setFont(f)
                lbl.setPos(x + 2, 2)
                self._scene.addItem(lbl)

        # Track rows
        for i, track in enumerate(self.project.tracks):
            y = RULER_HEIGHT + i * TRACK_HEIGHT
            row = QGraphicsRectItem(0, y, width, TRACK_HEIGHT)
            row.setBrush(QBrush(QColor("#252525") if i % 2 == 0 else QColor("#202020")))
            row.setPen(QPen(QColor("#111")))
            self._scene.addItem(row)

            label_bg = QGraphicsRectItem(0, y, TRACK_LABEL_WIDTH, TRACK_HEIGHT)
            label_bg.setBrush(QBrush(QColor("#303030")))
            label_bg.setPen(QPen(QColor("#111")))
            self._scene.addItem(label_bg)

            tag = "V" if track.type == TrackType.VIDEO else "A"
            lbl = QGraphicsTextItem(f"{tag}  {track.name}")
            lbl.setDefaultTextColor(QColor("white"))
            lbl.setPos(8, y + (TRACK_HEIGHT - 18) / 2)
            self._scene.addItem(lbl)

        # Clips
        for track_idx, track in enumerate(self.project.tracks):
            y = RULER_HEIGHT + track_idx * TRACK_HEIGHT
            clips = self.project.clips_for_track(track.id)
            for c in clips:
                color = QColor("#3a6ea5") if track.type == TrackType.VIDEO else QColor("#3a8a3a")
                item = _ClipItem(c, color, self)
                item.update_geometry(self.px_per_sec, y)
                self._scene.addItem(item)
                self._clip_items[c.id] = item

        self._update_playhead()

    def _update_playhead(self):
        if not self.project:
            return
        x = TRACK_LABEL_WIDTH + self.playhead * self.px_per_sec
        h = RULER_HEIGHT + len(self.project.tracks) * TRACK_HEIGHT
        if self._playhead_item is None:
            self._playhead_item = QGraphicsRectItem(x, 0, 2, h)
            self._playhead_item.setBrush(QBrush(QColor("#ff5050")))
            self._playhead_item.setPen(QPen(Qt.PenStyle.NoPen))
            self._playhead_item.setZValue(1000)
            self._scene.addItem(self._playhead_item)
        else:
            self._playhead_item.setRect(x, 0, 2, h)

    def _on_selection_changed(self):
        cid = self.selected_clip_id()
        if cid:
            self.clipSelected.emit(cid)

    # ---- input ----

    def wheelEvent(self, e: QWheelEvent):
        if e.modifiers() & Qt.KeyboardModifier.ControlModifier:
            factor = 1.2 if e.angleDelta().y() > 0 else 1 / 1.2
            self.set_zoom(self.px_per_sec * factor)
            e.accept()
            return
        super().wheelEvent(e)

    def mousePressEvent(self, e: QMouseEvent):
        # Click on ruler => move playhead
        scene_pt = self.mapToScene(e.pos())
        if scene_pt.y() < RULER_HEIGHT and scene_pt.x() > TRACK_LABEL_WIDTH:
            t = (scene_pt.x() - TRACK_LABEL_WIDTH) / self.px_per_sec
            self.set_playhead(max(0.0, t))
            self.playheadMoved.emit(self.playhead)
            return
        super().mousePressEvent(e)


def _format_time(seconds: float) -> str:
    s = int(seconds)
    m, s = divmod(s, 60)
    return f"{m}:{s:02d}"
