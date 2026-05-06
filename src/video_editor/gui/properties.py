"""Right-side properties panel for the selected clip."""
from __future__ import annotations

from typing import Optional

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from ..core.model import Clip, Effect, Project, TextOverlay


class PropertiesPanel(QWidget):
    clipUpdated = pyqtSignal(str)  # clip id

    def __init__(self):
        super().__init__()
        self.project: Optional[Project] = None
        self.clip: Optional[Clip] = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        self._title = QLabel("No clip selected")
        self._title.setStyleSheet("font-weight: bold; font-size: 14px;")
        layout.addWidget(self._title)

        self._form_box = QGroupBox("Clip")
        form = QFormLayout(self._form_box)
        self.start_spin = QDoubleSpinBox(); self.start_spin.setRange(0, 1e6); self.start_spin.setDecimals(2); self.start_spin.setSingleStep(0.1)
        self.in_spin = QDoubleSpinBox(); self.in_spin.setRange(0, 1e6); self.in_spin.setDecimals(2); self.in_spin.setSingleStep(0.1)
        self.out_spin = QDoubleSpinBox(); self.out_spin.setRange(0, 1e6); self.out_spin.setDecimals(2); self.out_spin.setSingleStep(0.1)
        self.speed_spin = QDoubleSpinBox(); self.speed_spin.setRange(0.25, 4.0); self.speed_spin.setDecimals(2); self.speed_spin.setSingleStep(0.05)
        self.volume_spin = QDoubleSpinBox(); self.volume_spin.setRange(0.0, 4.0); self.volume_spin.setDecimals(2); self.volume_spin.setSingleStep(0.05)

        form.addRow("Timeline start (s)", self.start_spin)
        form.addRow("In point (s)", self.in_spin)
        form.addRow("Out point (s)", self.out_spin)
        form.addRow("Speed", self.speed_spin)
        form.addRow("Volume", self.volume_spin)
        layout.addWidget(self._form_box)

        # Effects
        self._fx_box = QGroupBox("Video effects")
        fx = QFormLayout(self._fx_box)
        self.brightness_spin = QDoubleSpinBox(); self.brightness_spin.setRange(-1.0, 1.0); self.brightness_spin.setDecimals(2); self.brightness_spin.setSingleStep(0.05); self.brightness_spin.setValue(0.0)
        self.contrast_spin = QDoubleSpinBox(); self.contrast_spin.setRange(0.0, 3.0); self.contrast_spin.setDecimals(2); self.contrast_spin.setSingleStep(0.05); self.contrast_spin.setValue(1.0)
        self.saturation_spin = QDoubleSpinBox(); self.saturation_spin.setRange(0.0, 3.0); self.saturation_spin.setDecimals(2); self.saturation_spin.setSingleStep(0.05); self.saturation_spin.setValue(1.0)
        self.blur_spin = QDoubleSpinBox(); self.blur_spin.setRange(0.0, 50.0); self.blur_spin.setDecimals(1); self.blur_spin.setSingleStep(0.5); self.blur_spin.setValue(0.0)
        self.gray_chk = QCheckBox("Grayscale")
        fx.addRow("Brightness", self.brightness_spin)
        fx.addRow("Contrast", self.contrast_spin)
        fx.addRow("Saturation", self.saturation_spin)
        fx.addRow("Blur σ", self.blur_spin)
        fx.addRow("", self.gray_chk)
        layout.addWidget(self._fx_box)

        # Text overlay (single, simple)
        self._text_box = QGroupBox("Text overlay (one)")
        tlay = QFormLayout(self._text_box)
        self.text_edit = QLineEdit()
        self.text_start = QDoubleSpinBox(); self.text_start.setRange(0, 1e6); self.text_start.setDecimals(2)
        self.text_dur = QDoubleSpinBox(); self.text_dur.setRange(0, 1e6); self.text_dur.setDecimals(2); self.text_dur.setValue(2.0)
        self.text_x = QSpinBox(); self.text_x.setRange(0, 8000); self.text_x.setValue(50)
        self.text_y = QSpinBox(); self.text_y.setRange(0, 8000); self.text_y.setValue(50)
        self.text_size = QSpinBox(); self.text_size.setRange(8, 400); self.text_size.setValue(48)
        self.text_color = QComboBox(); self.text_color.addItems(["white", "yellow", "black", "red", "lime", "cyan"])
        tlay.addRow("Text", self.text_edit)
        tlay.addRow("Start (clip-local s)", self.text_start)
        tlay.addRow("Duration (s)", self.text_dur)
        tlay.addRow("X", self.text_x)
        tlay.addRow("Y", self.text_y)
        tlay.addRow("Size", self.text_size)
        tlay.addRow("Color", self.text_color)
        layout.addWidget(self._text_box)

        self.apply_btn = QPushButton("Apply")
        self.apply_btn.clicked.connect(self._on_apply)
        layout.addWidget(self.apply_btn)
        layout.addStretch(1)

        self._set_enabled(False)

    def _set_enabled(self, on: bool):
        for w in (self._form_box, self._fx_box, self._text_box, self.apply_btn):
            w.setEnabled(on)

    def set_project(self, project: Project):
        self.project = project
        self.set_clip(None)

    def set_clip(self, clip: Optional[Clip]):
        self.clip = clip
        if clip is None or self.project is None:
            self._title.setText("No clip selected")
            self._set_enabled(False)
            return
        from pathlib import Path
        self._title.setText(Path(clip.source_path).name)
        self._set_enabled(True)
        self.start_spin.setValue(clip.start)
        self.in_spin.setValue(clip.in_point)
        self.out_spin.setValue(clip.out_point)
        self.speed_spin.setValue(clip.speed)
        self.volume_spin.setValue(clip.volume)

        # Effects: read first matching by name (or default)
        self.brightness_spin.setValue(_eff_value(clip, "brightness", "value", 0.0))
        self.contrast_spin.setValue(_eff_value(clip, "contrast", "value", 1.0))
        self.saturation_spin.setValue(_eff_value(clip, "saturation", "value", 1.0))
        self.blur_spin.setValue(_eff_value(clip, "blur", "sigma", 0.0))
        self.gray_chk.setChecked(any(e.name == "grayscale" for e in clip.effects))

        if clip.text_overlays:
            o = clip.text_overlays[0]
            self.text_edit.setText(o.text)
            self.text_start.setValue(o.start)
            self.text_dur.setValue(o.duration)
            self.text_x.setValue(o.x)
            self.text_y.setValue(o.y)
            self.text_size.setValue(o.font_size)
            idx = self.text_color.findText(o.color)
            if idx >= 0:
                self.text_color.setCurrentIndex(idx)
        else:
            self.text_edit.setText("")
            self.text_start.setValue(0.0)
            self.text_dur.setValue(2.0)
            self.text_x.setValue(50)
            self.text_y.setValue(50)
            self.text_size.setValue(48)

    def _on_apply(self):
        if self.clip is None:
            return
        c = self.clip
        c.start = self.start_spin.value()
        c.in_point = self.in_spin.value()
        c.out_point = self.out_spin.value()
        c.speed = max(0.05, self.speed_spin.value())
        c.volume = self.volume_spin.value()

        new_effects: list[Effect] = []
        if self.brightness_spin.value() != 0.0:
            new_effects.append(Effect.brightness(self.brightness_spin.value()))
        if self.contrast_spin.value() != 1.0:
            new_effects.append(Effect.contrast(self.contrast_spin.value()))
        if self.saturation_spin.value() != 1.0:
            new_effects.append(Effect.saturation(self.saturation_spin.value()))
        if self.blur_spin.value() > 0:
            new_effects.append(Effect.blur(self.blur_spin.value()))
        if self.gray_chk.isChecked():
            new_effects.append(Effect.grayscale())
        c.effects = new_effects

        text = self.text_edit.text().strip()
        if text:
            c.text_overlays = [TextOverlay(
                text=text,
                start=self.text_start.value(),
                duration=self.text_dur.value(),
                x=self.text_x.value(),
                y=self.text_y.value(),
                font_size=self.text_size.value(),
                color=self.text_color.currentText(),
            )]
        else:
            c.text_overlays = []

        self.clipUpdated.emit(c.id)


def _eff_value(clip: Clip, name: str, key: str, default: float) -> float:
    for e in clip.effects:
        if e.name == name:
            v = e.params.get(key, default)
            try:
                return float(v)
            except Exception:
                return default
    return default
