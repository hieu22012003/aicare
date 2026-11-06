"""
Capture high-resolution verification screenshots for UI refactor and video test mode.
Ensures correct import order (mediapipe, torch before PyQt5).
Saves to docs/screenshots/ and artifact directory.
"""

import os
import sys
import shutil

# Ensure project root is in sys.path
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

# 1. Pre-import mediapipe & torch
try:
    import mediapipe
except Exception:
    pass
try:
    import torch
except Exception:
    pass

import cv2
import numpy as np
from PyQt5.QtCore import Qt, QSize
from PyQt5.QtWidgets import QApplication
from PyQt5.QtGui import QImage, QPixmap

from apps.desktop.dashboard import DashboardWindow, STYLESHEET
from apps.desktop.registration_dialog import RegistrationDialog
from apps.desktop.theme import (
    COLOR_DANGER_BG, COLOR_DANGER, COLOR_DANGER_BORDER,
    COLOR_WARNING_BG, COLOR_WARNING, COLOR_WARNING_BORDER,
    COLOR_TEST_BG, COLOR_PRIMARY,
    LIGHT_THEME_STYLESHEET
)

ARTIFACT_DIR = r"C:\Users\USER\.gemini\antigravity\brain\cc07d36d-6362-4a21-95c3-893f0f2ec920\screenshots"
DOCS_DIR = r"f:\AI_Rehabilitation_Safety_Assistant\docs\screenshots"

os.makedirs(ARTIFACT_DIR, exist_ok=True)
os.makedirs(DOCS_DIR, exist_ok=True)

def save_pixmap(pix, filename):
    docs_path = os.path.join(DOCS_DIR, filename)
    artifact_path = os.path.join(ARTIFACT_DIR, filename)
    pix.save(docs_path, "PNG")
    pix.save(artifact_path, "PNG")
    print(f"Saved: {filename} ({pix.width()}x{pix.height()})")

def main():
    app = QApplication.instance()
    if not app:
        app = QApplication(sys.argv)
    app.setStyleSheet(STYLESHEET)

    # 1. Dashboard Live Camera at 1366x768
    w = DashboardWindow()
    w.resize(1366, 768)
    w.show()
    app.processEvents()

    # Create dummy camera frame for clear video viewport
    dummy_frame = np.ones((720, 1280, 3), dtype=np.uint8) * 242
    cv2.putText(dummy_frame, "LIVE CAMERA FEED (720p 30 FPS)", (320, 340),
                cv2.FONT_HERSHEY_SIMPLEX, 1.2, (40, 120, 240), 2)
    cv2.putText(dummy_frame, "Status: Monitoring Active | Normal Posture Detected", (300, 400),
                cv2.FONT_HERSHEY_SIMPLEX, 0.8, (80, 100, 120), 2)
    w._render_frame_to_display(dummy_frame)
    app.processEvents()
    save_pixmap(w.grab(), "dashboard_live_1366x768.png")

    # 2. Dashboard Live Camera at 1920x1080
    w.resize(1920, 1080)
    app.processEvents()
    w._render_frame_to_display(dummy_frame)
    app.processEvents()
    save_pixmap(w.grab(), "dashboard_live_1920x1080.png")

    # 3. Fall Alert (DANGER) State at 1366x768
    w.resize(1366, 768)
    w.state.update_fall_state("FALL_CONFIRMED", confidence=0.96)
    w.state.update_email_status("Đã gửi cảnh báo khẩn cấp (SMTP_ACCEPTED)", caregiver_email="contact-418e9a5b@example.com")
    w.update_dashboard_ui()
    # Dummy fall frame
    fall_frame = np.ones((720, 1280, 3), dtype=np.uint8) * 230
    cv2.putText(fall_frame, "EMERGENCY: FALL DETECTED (CONFIDENCE: 96%)", (250, 340),
                cv2.FONT_HERSHEY_SIMPLEX, 1.2, (20, 20, 210), 3)
    cv2.putText(fall_frame, "Emergency Alert Email Dispatched to Caregiver", (320, 400),
                cv2.FONT_HERSHEY_SIMPLEX, 0.85, (40, 40, 180), 2)
    w._render_frame_to_display(fall_frame)
    app.processEvents()
    save_pixmap(w.grab(), "dashboard_safety_fall_alert.png")

    # 4. Camera Occluded / UNOBSERVABLE State at 1366x768
    w.state.update_fall_state("UNOBSERVABLE", unobservable_reason="Mất dấu khớp hông và đầu gối do bị bàn che")
    w.update_dashboard_ui()
    occ_frame = np.ones((720, 1280, 3), dtype=np.uint8) * 235
    cv2.putText(occ_frame, "WARNING: CAMERA UNOBSERVABLE / OCCLUDED", (280, 340),
                cv2.FONT_HERSHEY_SIMPLEX, 1.1, (20, 100, 200), 2)
    cv2.putText(occ_frame, "Lower body keypoints occluded by obstacle", (350, 400),
                cv2.FONT_HERSHEY_SIMPLEX, 0.8, (80, 80, 80), 2)
    w._render_frame_to_display(occ_frame)
    app.processEvents()
    save_pixmap(w.grab(), "dashboard_safety_camera_unobservable.png")

    # 5. Switch to Video Test Mode
    w._switch_to_video_test_mode()

    # Populate test tracks, timeline & metrics
    w.lbl_track_list.setText(
        "🔴 <b>P1 (Nam, Áo Busch)</b>: Ngã trên mặt đất (AR: 1.35)<br>"
        "🔴 <b>P2 (Nữ, Áo phao trắng)</b>: Ngã khuỵu chân (AR: 0.95)"
    )
    w.lbl_events_timeline.setText(
        "🚨 <b>P1 (Nam)</b> té ngã tại <b>15.77s</b> (Độ trễ: 0.97s, Mock Logged)<br>"
        "🚨 <b>P2 (Nữ)</b> té ngã tại <b>18.18s</b> (Độ trễ: 1.68s, Mock Logged)"
    )
    w.lbl_metrics_summary.setText(
        "<b>True Positives (TP):</b> 2 | <b>False Positives (FP):</b> 0 | <b>False Negatives (FN):</b> 0<br>"
        "<b>Precision:</b> 100.0% | <b>Recall:</b> 100.0% | <b>F1:</b> 1.000"
    )
    w.lbl_video_pts.setText("00:18 / 00:20 (23.98 FPS)")
    w.slider_video_timeline.setRange(0, 500)
    w.slider_video_timeline.setValue(436)

    dummy_test_frame = np.ones((720, 1280, 3), dtype=np.uint8) * 230
    cv2.putText(dummy_test_frame, "TEST VIDEO: Elderly Couple Slips and Falls (720p)", (220, 280),
                cv2.FONT_HERSHEY_SIMPLEX, 1.1, (160, 60, 20), 2)
    cv2.putText(dummy_test_frame, "PTS: 00:18.18 | Track P1: FALL_CONFIRMED | Track P2: FALL_CONFIRMED", (180, 340),
                cv2.FONT_HERSHEY_SIMPLEX, 0.75, (20, 120, 20), 2)
    # Draw simulated multi-person detection boxes
    cv2.rectangle(dummy_test_frame, (540, 460), (680, 530), (0, 0, 220), 3)
    cv2.putText(dummy_test_frame, "P1: FALL_CONFIRMED (AR=1.35)", (530, 450), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 0, 220), 2)
    cv2.rectangle(dummy_test_frame, (380, 440), (490, 520), (0, 0, 220), 3)
    cv2.putText(dummy_test_frame, "P2: FALL_CONFIRMED (AR=0.95)", (370, 430), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 0, 220), 2)

    w._render_frame_to_display(dummy_test_frame)
    app.processEvents()

    # 5a. Video Test Mode at 1366x768
    w.resize(1366, 768)
    app.processEvents()
    save_pixmap(w.grab(), "dashboard_video_test_1366x768.png")

    # 5b. Video Test Mode at 1920x1080
    w.resize(1920, 1080)
    app.processEvents()
    w._render_frame_to_display(dummy_test_frame)
    app.processEvents()
    save_pixmap(w.grab(), "dashboard_video_test_1920x1080.png")

    w.close()
    app.processEvents()

    # 6. Registration Dialog with In-line Validation Errors
    from PyQt5.QtWidgets import QMessageBox
    orig_warning = QMessageBox.warning
    QMessageBox.warning = lambda *a, **kw: None
    try:
        reg_dlg = RegistrationDialog(face_adapter=None)
        reg_dlg.edit_id.setText("") # Missing ID
        reg_dlg.edit_name.setText("") # Missing Name
        reg_dlg.edit_cg_email.setText("invalid_email_format") # Invalid email
        reg_dlg._on_save_patient() # Trigger in-line validation
        reg_dlg.resize(800, 650)
        reg_dlg.show()
        app.processEvents()
        save_pixmap(reg_dlg.grab(), "registration_dialog_inline_validation.png")
        reg_dlg.close()
        app.processEvents()
    finally:
        QMessageBox.warning = orig_warning

    print("All verification screenshots successfully captured!")

if __name__ == "__main__":
    main()
