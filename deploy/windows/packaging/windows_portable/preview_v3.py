"""Capture the actual V3 Qt dashboard with hardware disabled."""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
PACKAGE = ROOT/'releases/portable-v3/V3/AICare'
sys.path.insert(0, str(PACKAGE/'app'))
import portable_entry
portable_entry.configure(ROOT/'runtime/portable-qa/V3/preview')
os.environ.update(AI_CARE_TEST_MODE='1', AI_CARE_TEST_DATA_DIR=str(ROOT/'runtime/portable-qa/V3/preview'),
                  QT_QPA_PLATFORM='offscreen', VOICE_AI_ENABLED='false')
import mediapipe
import torch
from PyQt5.QtWidgets import QApplication
from PyQt5.QtGui import QFont, QFontDatabase
from shared.camera.camera_manager import CameraManager
CameraManager.start_camera = lambda self: None
from shared.database.connection import UnifiedDatabaseManager
from shared.database.migrator import Migrator
Migrator(UnifiedDatabaseManager()).apply_all()
from apps.desktop.dashboard import DashboardWindow, STYLESHEET
app = QApplication([])
QFontDatabase.addApplicationFont('C:/Windows/Fonts/segoeui.ttf')
app.setFont(QFont('Segoe UI', 11))
app.setStyleSheet(STYLESHEET)
window = DashboardWindow()
window.show()
window.showNormal()
window.resize(1440, 960)
app.processEvents()
app.processEvents()
output = ROOT/'releases/portable-v3/previews'
output.mkdir(exist_ok=True)
window.grab().save(str(output/'desktop.png'))
window.medication_tabs.setCurrentIndex(1)
app.processEvents()
window.grab().save(str(output/'desktop-rehabilitation.png'))
window.close()
os._exit(0)
