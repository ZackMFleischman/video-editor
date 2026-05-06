"""Entry point: python -m video_editor"""
from __future__ import annotations

import logging
import os
import sys
import traceback
from datetime import datetime
from pathlib import Path


def _setup_logging() -> Path:
    """Always log to a file we can find later, even when launched via pythonw."""
    log_dir = Path(os.environ.get("APPDATA", str(Path.home()))) / "VideoEditor" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"app_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[
            logging.FileHandler(log_path, encoding="utf-8"),
            logging.StreamHandler(sys.stderr),
        ],
    )

    def _excepthook(exc_type, exc, tb):
        logging.critical(
            "UNCAUGHT EXCEPTION:\n%s",
            "".join(traceback.format_exception(exc_type, exc, tb)),
        )
        # Also write a "latest" pointer so users always know where the log is
        latest = log_dir / "latest.log"
        try:
            latest.write_text(log_path.read_text(encoding="utf-8"), encoding="utf-8")
        except Exception:
            pass

    sys.excepthook = _excepthook
    # Mirror to a stable "latest" path so it's easy to tail
    try:
        (log_dir / "latest.log").write_text(f"Log started: {log_path}\n", encoding="utf-8")
    except Exception:
        pass
    return log_path


def main() -> int:
    log_path = _setup_logging()
    logging.info("Starting Video Editor — log: %s", log_path)

    from PyQt6.QtWidgets import QApplication
    from .gui.main_window import MainWindow

    app = QApplication(sys.argv)
    app.setApplicationName("Video Editor")
    app.setStyle("Fusion")

    try:
        win = MainWindow()
    except Exception:
        logging.exception("Failed to construct MainWindow")
        raise

    win.show()
    logging.info("Main window shown")
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
