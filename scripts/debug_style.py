import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

os.environ.setdefault("PROTOCOL_BUFFERS_PYTHON_IMPLEMENTATION", "python")
if sys.platform != "win32":
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    import mediapipe
except Exception:
    pass

try:
    import torch
except Exception:
    pass

from PyQt5.QtWidgets import QApplication
from apps.desktop.dashboard import DashboardWindow, STYLESHEET

app = QApplication(sys.argv)
app.setStyleSheet(STYLESHEET)
w = DashboardWindow()

print("w.btn_start objectName:", w.btn_start.objectName())
print("w.btn_start isEnabled:", w.btn_start.isEnabled())
print("w.btn_stop isEnabled:", w.btn_stop.isEnabled())
print("w.btn_reset isEnabled:", w.btn_reset.isEnabled())

# Let's check how Qt resolves styles for BtnStart
style = w.btn_start.style()
print("btn_start style:", style)
w.close()

