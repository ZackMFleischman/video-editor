"""Settings + AI speech dialogs."""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from PyQt6.QtCore import QObject, Qt, QThread, pyqtSignal
from PyQt6.QtWidgets import (
    QButtonGroup,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QRadioButton,
    QVBoxLayout,
    QWidget,
)

from ..core import config
from ..services import ai_script, tts


class SettingsDialog(QDialog):
    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.resize(520, 220)

        self.anthropic = QLineEdit(config.get("anthropic_api_key", ""))
        self.anthropic.setEchoMode(QLineEdit.EchoMode.Password)
        self.elevenlabs = QLineEdit(config.get("elevenlabs_api_key", ""))
        self.elevenlabs.setEchoMode(QLineEdit.EchoMode.Password)

        form = QFormLayout()
        form.addRow("Anthropic API key", self.anthropic)
        form.addRow("ElevenLabs API key", self.elevenlabs)

        info = QLabel("Keys are stored locally in %APPDATA%\\VideoEditor\\config.json")
        info.setStyleSheet("color: #999;")

        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        btns.accepted.connect(self._save)
        btns.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(info)
        layout.addStretch(1)
        layout.addWidget(btns)

    def _save(self):
        config.set("anthropic_api_key", self.anthropic.text().strip())
        config.set("elevenlabs_api_key", self.elevenlabs.text().strip())
        self.accept()


# ---- AI speech dialog ----

class _ScriptWorker(QObject):
    finished = pyqtSignal(str)
    failed = pyqtSignal(str)

    def __init__(self, instructions: str, target_seconds: Optional[float]):
        super().__init__()
        self._instructions = instructions
        self._target = target_seconds

    def run(self):
        try:
            text = ai_script.generate_script(self._instructions, target_seconds=self._target)
            self.finished.emit(text)
        except Exception as e:
            self.failed.emit(str(e))


class _TTSWorker(QObject):
    finished = pyqtSignal(str)  # output path
    failed = pyqtSignal(str)

    def __init__(self, text: str, voice_id: str, out_path: Path):
        super().__init__()
        self._text = text
        self._voice = voice_id
        self._out = out_path

    def run(self):
        try:
            p = tts.synthesize(self._text, self._voice, self._out)
            self.finished.emit(str(p))
        except Exception as e:
            self.failed.emit(str(e))


class _VoicesWorker(QObject):
    finished = pyqtSignal(list)
    failed = pyqtSignal(str)

    def run(self):
        try:
            voices = tts.list_voices()
            self.finished.emit(voices)
        except Exception as e:
            self.failed.emit(str(e))


class AISpeechDialog(QDialog):
    """Two-mode dialog: script OR instructions -> Claude -> TTS -> audio file."""

    speechReady = pyqtSignal(str)  # emits path to generated mp3

    def __init__(self, project_dir: Path, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setWindowTitle("AI Speech Overlay")
        self.resize(640, 540)
        self.project_dir = project_dir

        self.mode_script = QRadioButton("I have a script")
        self.mode_instr = QRadioButton("Generate from instructions (uses Claude)")
        self.mode_script.setChecked(True)
        mg = QButtonGroup(self)
        mg.addButton(self.mode_script)
        mg.addButton(self.mode_instr)
        mg.buttonClicked.connect(self._refresh_mode)

        mode_row = QHBoxLayout()
        mode_row.addWidget(self.mode_script)
        mode_row.addWidget(self.mode_instr)
        mode_row.addStretch(1)

        self.instr_label = QLabel("Instructions:")
        self.instructions = QPlainTextEdit()
        self.instructions.setPlaceholderText(
            "e.g., 30-second intro for a YouTube video about urban beekeeping. Tone: warm, curious."
        )
        self.target_sec = QDoubleSpinBox()
        self.target_sec.setRange(0, 600)
        self.target_sec.setDecimals(0)
        self.target_sec.setSuffix(" s")
        self.target_sec.setValue(30)
        self.gen_btn = QPushButton("Generate script")
        self.gen_btn.clicked.connect(self._generate_script)

        target_row = QHBoxLayout()
        target_row.addWidget(QLabel("Target length:"))
        target_row.addWidget(self.target_sec)
        target_row.addStretch(1)
        target_row.addWidget(self.gen_btn)

        self.script_label = QLabel("Script:")
        self.script = QPlainTextEdit()
        self.script.setPlaceholderText("Paste or write your script here, or generate it above.")

        self.voice_combo = QComboBox()
        self.voice_combo.addItem("(load voices)", None)
        self.refresh_voices_btn = QPushButton("Load voices")
        self.refresh_voices_btn.clicked.connect(self._load_voices)

        voice_row = QHBoxLayout()
        voice_row.addWidget(QLabel("Voice:"))
        voice_row.addWidget(self.voice_combo, 1)
        voice_row.addWidget(self.refresh_voices_btn)

        self.status = QLabel("")
        self.status.setStyleSheet("color: #aaa;")

        self.btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        self.synth_btn = self.btns.addButton("Synthesize and Insert", QDialogButtonBox.ButtonRole.AcceptRole)
        self.synth_btn.clicked.connect(self._synthesize)
        self.btns.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addLayout(mode_row)
        layout.addWidget(self.instr_label)
        layout.addWidget(self.instructions)
        layout.addLayout(target_row)
        layout.addWidget(self.script_label)
        layout.addWidget(self.script, 1)
        layout.addLayout(voice_row)
        layout.addWidget(self.status)
        layout.addWidget(self.btns)

        self._refresh_mode()
        self._workers: list[QThread] = []

    def _refresh_mode(self):
        instr = self.mode_instr.isChecked()
        for w in (self.instr_label, self.instructions, self.gen_btn, self.target_sec):
            w.setVisible(instr)

    def _generate_script(self):
        text = self.instructions.toPlainText().strip()
        if not text:
            QMessageBox.warning(self, "Missing input", "Enter instructions first.")
            return
        self._set_busy("Generating script...")
        worker = _ScriptWorker(text, self.target_sec.value() or None)
        self._run_worker(worker, on_finish=self._on_script_ready)

    def _on_script_ready(self, text: str):
        self.script.setPlainText(text)
        self._set_busy("Script generated.")

    def _load_voices(self):
        self._set_busy("Loading voices...")
        worker = _VoicesWorker()
        self._run_worker(worker, on_finish=self._on_voices_ready)

    def _on_voices_ready(self, voices: list):
        self.voice_combo.clear()
        if not voices:
            self.voice_combo.addItem("(no voices found)", None)
            self._set_busy("No voices.")
            return
        for v in voices:
            label = f"{v['name']} ({v['category']})" if v.get("category") else v["name"]
            self.voice_combo.addItem(label, v["voice_id"])
        # restore last used
        last = config.get("last_voice_id")
        if last:
            for i in range(self.voice_combo.count()):
                if self.voice_combo.itemData(i) == last:
                    self.voice_combo.setCurrentIndex(i)
                    break
        self._set_busy("Voices loaded.")

    def _synthesize(self):
        text = self.script.toPlainText().strip()
        if not text:
            QMessageBox.warning(self, "Missing script", "Write or generate a script first.")
            return
        voice_id = self.voice_combo.currentData()
        if not voice_id:
            QMessageBox.warning(self, "Pick a voice", "Load voices and pick one.")
            return
        config.set("last_voice_id", voice_id)
        out_dir = self.project_dir / "generated_audio"
        out_dir.mkdir(parents=True, exist_ok=True)
        from datetime import datetime
        out_path = out_dir / f"speech_{datetime.now().strftime('%Y%m%d_%H%M%S')}.mp3"

        self._set_busy("Calling ElevenLabs...")
        worker = _TTSWorker(text, voice_id, out_path)
        self._run_worker(worker, on_finish=self._on_tts_done)

    def _on_tts_done(self, path: str):
        self._set_busy("Done.")
        self.speechReady.emit(path)
        self.accept()

    def _set_busy(self, msg: str):
        self.status.setText(msg)

    def _run_worker(self, worker: QObject, on_finish):
        thread = QThread(self)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.finished.connect(on_finish)
        worker.failed.connect(self._on_failed)
        worker.finished.connect(thread.quit)
        worker.failed.connect(thread.quit)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        # Keep references so they are not GC'd
        self._workers.append(thread)
        thread.finished.connect(lambda: self._workers.remove(thread) if thread in self._workers else None)
        thread.start()

    def _on_failed(self, err: str):
        self._set_busy("")
        QMessageBox.critical(self, "Error", err)
