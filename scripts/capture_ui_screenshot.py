"""
Script to capture UI screenshots for Baseline and Refactored verification.
Supports 1366x768 and 1920x1080 resolutions.
"""

import os
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

# Ensure media/temp config
os.environ.setdefault("PROTOCOL_BUFFERS_PYTHON_IMPLEMENTATION", "python")
if sys.platform != "win32":
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

# On Windows, pre-initialize mediapipe and torch BEFORE PyQt5 to prevent C-runtime DLL collision
try:
    import mediapipe
except Exception:
    pass

try:
    import torch
except Exception:
    pass

from PyQt5.QtCore import QSize, Qt
from PyQt5.QtWidgets import QApplication

def capture_screenshots(prefix: str = "after"):
    app = QApplication.instance()
    if not app:
        app = QApplication(sys.argv)
        
    from apps.desktop.dashboard import DashboardWindow, STYLESHEET
    app.setStyleSheet(STYLESHEET)

    output_dir = os.path.join(os.getcwd(), "docs", "screenshots")
    os.makedirs(output_dir, exist_ok=True)

    # Resolution 1: 1366 x 768
    win_1366 = DashboardWindow()
    win_1366.showNormal()
    win_1366.setGeometry(0, 0, 1366, 768)
    win_1366.resize(1366, 768)
    win_1366.show()
    app.processEvents()
    time.sleep(0.5)
    app.processEvents()

    pix_1366 = win_1366.grab()
    path_1366 = os.path.join(output_dir, f"{prefix}_dashboard_1366x768.png")
    pix_1366.save(path_1366, "PNG")
    print(f"Saved: {path_1366} (Size: {pix_1366.width()}x{pix_1366.height()})")
    win_1366.close()

    # Resolution 2: 1920 x 1080
    win_1920 = DashboardWindow()
    win_1920.showNormal()
    win_1920.setGeometry(0, 0, 1920, 1080)
    win_1920.resize(1920, 1080)
    win_1920.show()
    app.processEvents()
    time.sleep(0.5)
    app.processEvents()

    pix_1920 = win_1920.grab()
    path_1920 = os.path.join(output_dir, f"{prefix}_dashboard_1920x1080.png")
    pix_1920.save(path_1920, "PNG")
    print(f"Saved: {path_1920} (Size: {pix_1920.width()}x{pix_1920.height()})")
    win_1920.close()

    # Registration Dialog capture
    from apps.desktop.registration_dialog import RegistrationDialog
    from features.face_monitoring.face_adapter import FaceAdapter
    face_adapter = FaceAdapter()
    dlg = RegistrationDialog(face_adapter=face_adapter)
    dlg.resize(900, 700)
    dlg.show()
    app.processEvents()
    time.sleep(0.3)
    app.processEvents()
    pix_dlg = dlg.grab()
    path_dlg = os.path.join(output_dir, f"{prefix}_registration_dialog.png")
    pix_dlg.save(path_dlg, "PNG")
    print(f"Saved: {path_dlg} (Size: {pix_dlg.width()}x{pix_dlg.height()})")
    dlg.close()

if __name__ == "__main__":
    prefix = sys.argv[1] if len(sys.argv) > 1 else "baseline"
    capture_screenshots(prefix)
