"""
Main Entry Point for AI_for_older MVP System.
Launches the PyQt5 AI Care & Rehabilitation System Dashboard.
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

# `python apps/desktop/main.py` puts apps/desktop on sys.path; the packages live at the repo root.
_ROOT = Path(__file__).resolve().parents[2]
if not getattr(sys, "frozen", False) and str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

# Dispatch frozen child processes before importing Qt/CV or opening another dashboard.
if __name__ == '__main__':
    import multiprocessing
    multiprocessing.freeze_support()
    if len(sys.argv) == 3 and sys.argv[1] == '--local-tts':
        from features.voice_assistant.local_voice.tts_runtime import speak
        sys.exit(speak(sys.argv[2]))

# Configure environment for MediaPipe Protobuf & Qt platform plugins
os.environ.setdefault("PROTOCOL_BUFFERS_PYTHON_IMPLEMENTATION", "python")
import tempfile
os.environ.setdefault("MPLCONFIGDIR", os.path.join(tempfile.gettempdir(), "matplotlib-cache"))

# On Windows, pre-initialize mediapipe and torch BEFORE PyQt5 to prevent C-runtime DLL collision (WinError 1114)
try:
    import mediapipe
except Exception:
    pass

try:
    import torch
except Exception:
    pass

import PyQt5
from PyQt5.QtCore import QCoreApplication, QLockFile
from PyQt5.QtWidgets import QApplication

if not getattr(sys, "frozen", False):
    pyqt5_plugins = os.path.join(os.path.dirname(PyQt5.__file__), "Qt5", "plugins")
    pyqt5_platforms = os.path.join(pyqt5_plugins, "platforms")
    if os.path.exists(pyqt5_platforms):
        os.environ["QT_QPA_PLATFORM_PLUGIN_PATH"] = pyqt5_platforms
    QCoreApplication.setLibraryPaths([pyqt5_plugins])

from shared.config import LOG_FILE, DATA_DIR

logger = logging.getLogger("Main")


def main() -> int:
    """Initialize and run PyQt5 application event loop."""
    logger.info("Starting AI CARE & REHABILITATION SYSTEM (PyQt5)...")
    logger.info("Logging to: %s", LOG_FILE)

    app = QApplication(sys.argv)
    app.setApplicationName("AI Care & Rehabilitation System")

    # Configure high-fidelity cross-platform font with Vietnamese support
    from PyQt5.QtGui import QFont, QFontDatabase
    font_db = QFontDatabase()
    available_families = font_db.families()
    for preferred in ["Segoe UI", "Ubuntu", "DejaVu Sans", "Noto Sans", "Arial"]:
        if preferred in available_families:
            app_font = QFont(preferred, 11)
            app.setFont(app_font)
            break
    # A second scheduler must not recover another process's in-flight SMTP claim.
    lock = QLockFile(str(DATA_DIR / 'application.lock'))
    if not lock.tryLock(100):
        from PyQt5.QtWidgets import QMessageBox
        QMessageBox.information(None, 'Ứng dụng đang mở', 'Vui lòng sử dụng cửa sổ ứng dụng đang chạy.')
        return 0

    # Import DashboardWindow and STYLESHEET after QApplication is created
    from apps.desktop.dashboard import DashboardWindow, STYLESHEET
    app.setStyleSheet(STYLESHEET)

    # Automatically synchronize schema and patient profiles across JSON and unified database
    try:
        from shared.database.connection import UnifiedDatabaseManager
        from shared.database.migrator import Migrator
        from shared.services.patient_sync_service import sync_all_patient_profiles
        db = UnifiedDatabaseManager()
        Migrator(db).apply_all()
        sync_all_patient_profiles(db)
    except Exception as e:
        logger.warning("Auto patient profile sync on startup skipped: %s", e)

    # If --test flag is passed, instantiate window, test UI sync and exit cleanly
    if "--test" in sys.argv:
        window = DashboardWindow()
        window.update_dashboard_ui()
        window._on_timer_tick()
        app.processEvents()
        logger.info("Dashboard initialized and UI updated successfully in test mode. Exiting.")
        if hasattr(window, "camera_manager") and hasattr(window.camera_manager, "stop_camera"):
            window.camera_manager.stop_camera()
        window.close()
        os._exit(0)

    window = DashboardWindow()
    window.show()

    shutdown_file = os.environ.get('AI_CARE_SHUTDOWN_FILE')
    if shutdown_file:
        from PyQt5.QtCore import QTimer
        shutdown_timer = QTimer(app)
        def check_portable_shutdown():
            if os.path.isfile(shutdown_file):
                window.close()
                app.quit()
        shutdown_timer.timeout.connect(check_portable_shutdown)
        shutdown_timer.start(300)

    return app.exec_()


if __name__ == "__main__":
    import multiprocessing
    multiprocessing.freeze_support()
    sys.exit(main())
