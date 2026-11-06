"""
User Registration and Patient Management Modals.
Completely decouples patient enrollment from the live monitoring and face recognition view,
providing clear personal information fields, caregiver emergency contact, and live camera snapshot capture.
Refactored with Modern Medical Center Theme (Light canvas, white cards, navy typography, royal blue accents).
Complies with KE_HOACH_REFACTOR_UI_AI_NGUOI_CAO_TUOI.md.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import cv2
import numpy as np
from PyQt5.QtCore import Qt, QTimer, pyqtSignal
from PyQt5.QtGui import QImage, QPixmap
from PyQt5.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from features.face_monitoring.face_adapter import FaceAdapter
from apps.desktop.theme import (
    COLOR_BORDER,
    COLOR_CANVAS,
    COLOR_DANGER,
    COLOR_PRIMARY,
    COLOR_PRIMARY_BG,
    COLOR_PRIMARY_HOVER,
    COLOR_SUCCESS,
    COLOR_SURFACE,
    COLOR_SURFACE_ALT,
    COLOR_TEXT_PRIMARY,
    COLOR_TEXT_SECONDARY,
    COLOR_WARNING,
    FONT_FAMILY,
    get_badge_stylesheet,
)
from shared.camera.camera_manager import CameraManager

logger = logging.getLogger("RegistrationDialog")

DIALOG_STYLESHEET = f"""
QDialog {{
    background-color: {COLOR_CANVAS};
    color: {COLOR_TEXT_PRIMARY};
    font-family: {FONT_FAMILY};
}}
QLabel {{
    color: {COLOR_TEXT_PRIMARY};
}}
QLabel.section-title {{
    font-size: 14px;
    font-weight: 700;
    color: {COLOR_PRIMARY};
    margin-bottom: 2px;
}}
QFrame.card {{
    background-color: {COLOR_SURFACE};
    border: 1px solid {COLOR_BORDER};
    border-radius: 12px;
    padding: 14px;
}}
QLineEdit, QSpinBox, QComboBox {{
    background-color: {COLOR_SURFACE};
    border: 1.5px solid {COLOR_BORDER};
    border-radius: 8px;
    color: {COLOR_TEXT_PRIMARY};
    padding: 7px 12px;
    font-size: 13px;
    min-height: 22px;
}}
QLineEdit:focus, QSpinBox:focus, QComboBox:focus {{
    border: 1.5px solid {COLOR_PRIMARY};
    background-color: {COLOR_SURFACE};
}}
QPushButton.btn-primary {{
    background-color: {COLOR_PRIMARY};
    color: #FFFFFF;
    font-size: 13px;
    font-weight: 700;
    border: none;
    border-radius: 8px;
    padding: 9px 18px;
    min-height: 24px;
}}
QPushButton.btn-primary:hover {{
    background-color: {COLOR_PRIMARY_HOVER};
}}
QPushButton.btn-capture {{
    background-color: {COLOR_PRIMARY};
    color: #FFFFFF;
    font-size: 13px;
    font-weight: 700;
    border: none;
    border-radius: 8px;
    padding: 8px 16px;
    min-height: 24px;
}}
QPushButton.btn-capture:hover {{
    background-color: {COLOR_PRIMARY_HOVER};
}}
QPushButton.btn-secondary {{
    background-color: {COLOR_SURFACE};
    color: {COLOR_TEXT_PRIMARY};
    font-size: 13px;
    font-weight: 600;
    border: 1px solid {COLOR_BORDER};
    border-radius: 8px;
    padding: 8px 16px;
}}
QPushButton.btn-secondary:hover {{
    background-color: #EDF2F7;
    border-color: {COLOR_PRIMARY};
}}
QPushButton.btn-danger {{
    background-color: {COLOR_DANGER};
    color: #FFFFFF;
    font-size: 13px;
    font-weight: 700;
    border: none;
    border-radius: 8px;
    padding: 8px 16px;
}}
QPushButton.btn-danger:hover {{
    background-color: #A52133;
}}
QTableWidget {{
    background-color: {COLOR_SURFACE};
    border: 1px solid {COLOR_BORDER};
    border-radius: 8px;
    gridline-color: #EDF2F7;
    color: {COLOR_TEXT_PRIMARY};
    selection-background-color: {COLOR_PRIMARY_BG};
    selection-color: {COLOR_TEXT_PRIMARY};
}}
QHeaderView::section {{
    background-color: {COLOR_SURFACE_ALT};
    color: {COLOR_TEXT_PRIMARY};
    font-weight: 700;
    font-size: 12px;
    border: none;
    border-bottom: 1px solid {COLOR_BORDER};
    padding: 8px;
}}
"""


class FaceBBoxTracker:
    """
    Robust Visual Object Tracker (OpenCV KCF) + DNN Face Detection Fusion.
    Guarantees silky-smooth, jitter-free bounding box tracking in registration preview:
    1. KCF visual correlation tracking runs on every frame at ~25-30 FPS, eliminating high-frequency jitter.
    2. Periodic DNN face redetection runs every 6-8 frames to prevent drift and re-anchor coordinates.
    3. IoU & spatial gating: Any spurious false positive (e.g. background textures, ceiling wood grains)
       is strictly rejected because its IoU with the tracked face is 0.
    4. EMA smoothing on coordinates ensures rock-solid stability even when stationary.
    5. Grace period: If face is momentarily occluded or turns slightly (up to ~600ms), tracking persists smoothly.
    """

    def __init__(self, face_adapter: FaceAdapter) -> None:
        self.face_adapter = face_adapter
        self._tracker: Optional[Any] = None
        self._tracked_box: Optional[Tuple[float, float, float, float]] = None  # (x, y, w, h)
        self._smooth_box: Optional[Tuple[float, float, float, float]] = None
        self._confidence: float = 0.0
        self._frames_since_detect: int = 0
        self._lost_frames: int = 0
        self._is_tracking: bool = False

    @property
    def is_tracking(self) -> bool:
        return self._is_tracking and self._smooth_box is not None

    def reset(self) -> None:
        self._tracker = None
        self._tracked_box = None
        self._smooth_box = None
        self._confidence = 0.0
        self._frames_since_detect = 0
        self._lost_frames = 0
        self._is_tracking = False

    @staticmethod
    def _compute_iou(boxA: Tuple[float, float, float, float], boxB: Tuple[float, float, float, float]) -> float:
        xA = max(boxA[0], boxB[0])
        yA = max(boxA[1], boxB[1])
        xB = min(boxA[0] + boxA[2], boxB[0] + boxB[2])
        yB = min(boxA[1] + boxA[3], boxB[1] + boxB[3])
        interW = max(0.0, xB - xA)
        interH = max(0.0, yB - yA)
        interArea = interW * interH
        areaA = max(1.0, boxA[2] * boxA[3])
        areaB = max(1.0, boxB[2] * boxB[3])
        unionArea = areaA + areaB - interArea
        return (interArea / unionArea) if unionArea > 0 else 0.0

    def _init_tracker(self, frame: np.ndarray, box: Tuple[float, float, float, float], conf: float) -> None:
        try:
            h, w = frame.shape[:2]
            bx = max(0, min(w - 20, int(round(box[0]))))
            by = max(0, min(h - 20, int(round(box[1]))))
            bw = max(20, min(w - bx, int(round(box[2]))))
            bh = max(20, min(h - by, int(round(box[3]))))

            tracker = cv2.TrackerKCF_create()
            tracker.init(frame, (bx, by, bw, bh))
            self._tracker = tracker
            self._tracked_box = (float(bx), float(by), float(bw), float(bh))
            if self._smooth_box is None:
                self._smooth_box = (float(bx), float(by), float(bw), float(bh))
            self._confidence = float(conf)
            self._frames_since_detect = 0
            self._lost_frames = 0
            self._is_tracking = True
        except Exception as e:
            logger.debug("Failed to init visual tracker: %s", e)
            self.reset()

    def update(self, frame: np.ndarray) -> Optional[Tuple[int, int, int, int, float]]:
        """
        Processes a new frame. Returns (x, y, w, h, confidence) if a face is tracked, else None.
        """
        if frame is None or frame.size == 0:
            return None

        h, w = frame.shape[:2]
        self._frames_since_detect += 1

        # 1. If currently tracking, step the visual tracker
        tracker_ok = False
        t_box: Optional[Tuple[float, float, float, float]] = None
        if self._is_tracking and self._tracker is not None:
            try:
                ok, raw_bbox = self._tracker.update(frame)
                if ok and raw_bbox is not None and len(raw_bbox) == 4:
                    rx, ry, rw, rh = raw_bbox
                    # Sanity check dimensions and boundaries
                    if rw >= 25 and rh >= 25 and 0.4 <= (rw / float(rh)) <= 2.2:
                        cx = rx + rw / 2.0
                        cy = ry + rh / 2.0
                        if 0 <= cx < w and 0 <= cy < h:
                            tracker_ok = True
                            t_box = (float(rx), float(ry), float(rw), float(rh))
            except Exception as e:
                tracker_ok = False

        # 2. Run detection if not tracking, or tracker failed, or periodic cadence reached (every 6 frames ~240ms)
        need_detection = (not self._is_tracking) or (not tracker_ok) or (self._frames_since_detect >= 6)

        if need_detection:
            prior = tuple(int(v) for v in self._smooth_box) if self._smooth_box is not None else None
            det = self.face_adapter.detect_face(frame, conf_threshold=0.42, prior_box=prior)

            if det is not None:
                dx, dy, dw, dh, dconf = det
                det_box = (float(dx), float(dy), float(dw), float(dh))

                if self._is_tracking and self._tracked_box is not None:
                    # Gating: Check IoU and center distance to current tracked face
                    iou = self._compute_iou(det_box, self._tracked_box)
                    cdist = np.sqrt(
                        ((dx + dw / 2.0) - (self._tracked_box[0] + self._tracked_box[2] / 2.0)) ** 2
                        + ((dy + dh / 2.0) - (self._tracked_box[1] + self._tracked_box[3] / 2.0)) ** 2
                    )
                    diag = np.sqrt(self._tracked_box[2] ** 2 + self._tracked_box[3] ** 2)

                    if iou > 0.25 or cdist < diag * 0.75:
                        # Detection matches current tracked face: update confidence and re-anchor if needed
                        self._confidence = 0.35 * dconf + 0.65 * self._confidence
                        self._lost_frames = 0
                        self._frames_since_detect = 0

                        # Re-anchor tracker if drift > 14 pixels to keep tracker accurate over time
                        if cdist > 14.0 or abs(dw - self._tracked_box[2]) > 14.0 or abs(dh - self._tracked_box[3]) > 14.0:
                            self._init_tracker(frame, det_box, self._confidence)
                    else:
                        # Detection is far away (false positive on background texture like wood knots)
                        if tracker_ok and self._lost_frames < 4:
                            # Tracker is still tracking the person: strictly ignore false positive
                            pass
                        elif not tracker_ok and self._lost_frames >= 4:
                            # Face genuinely moved to a new position after loss
                            self._init_tracker(frame, det_box, dconf)
                else:
                    # New face detected
                    self._init_tracker(frame, det_box, dconf)
            else:
                # Detector found no face in this frame
                if not tracker_ok:
                    self._lost_frames += 1
                    if self._lost_frames > 15:  # ~600ms grace period
                        self.reset()
                        return None
                else:
                    # Tracker is still going, slowly decay confidence
                    self._confidence = max(0.40, self._confidence * 0.98)

        # 3. If tracker was successful, update tracked_box
        if tracker_ok and t_box is not None:
            self._tracked_box = t_box
            self._lost_frames = 0

        # 4. Temporal low-pass filter (EMA) to completely eliminate visual jitter
        if self._is_tracking and self._tracked_box is not None:
            tx, ty, tw, th = self._tracked_box
            if self._smooth_box is None:
                self._smooth_box = (tx, ty, tw, th)
            else:
                sx, sy, sw, sh = self._smooth_box
                alpha = 0.30
                nx = alpha * tx + (1.0 - alpha) * sx
                ny = alpha * ty + (1.0 - alpha) * sy
                nw = alpha * tw + (1.0 - alpha) * sw
                nh = alpha * th + (1.0 - alpha) * sh

                # Deadband threshold
                if abs(nx - sx) < 1.0:
                    nx = sx
                if abs(ny - sy) < 1.0:
                    ny = sy
                if abs(nw - sw) < 1.5:
                    nw = sw
                if abs(nh - sh) < 1.5:
                    nh = sh

                self._smooth_box = (nx, ny, nw, nh)

            # Return clamped integer coordinates
            sx, sy, sw, sh = self._smooth_box
            fx = max(0, min(w - 1, int(round(sx))))
            fy = max(0, min(h - 1, int(round(sy))))
            fw_box = max(10, min(w - fx, int(round(sw))))
            fh_box = max(10, min(h - fy, int(round(sh))))
            return (fx, fy, fw_box, fh_box, float(self._confidence))

        return None

    def get_latest_box(self) -> Optional[Tuple[int, int, int, int]]:
        if self._smooth_box is not None:
            sx, sy, sw, sh = self._smooth_box
            return (int(round(sx)), int(round(sy)), int(round(sw)), int(round(sh)))
        return None


class RegistrationDialog(QDialog):
    """
    Dedicated User Registration Modal.
    Provides clear, prominent personal information inputs (Họ tên, ID, Tuổi, Giới tính, Ghi chú),
    caregiver contact and live camera face photo sample capture.
    """

    patient_registered = pyqtSignal(dict)

    def __init__(self, face_adapter: Optional[FaceAdapter] = None, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.face_adapter = face_adapter or FaceAdapter()
        self.camera_manager = CameraManager.get_instance()
        self.captured_faces: List[np.ndarray] = []

        self.setWindowTitle("Đăng Ký Hồ Sơ Người Dùng Mới — Thông Tin Cá Nhân & Nhận Diện")
        self.setMinimumSize(920, 620)
        self.setStyleSheet(DIALOG_STYLESHEET)

        self._init_ui()

        # High-performance Visual BBox Tracker with IoU Gating
        self.bbox_tracker = FaceBBoxTracker(self.face_adapter)
        self._last_guidance_state: Optional[bool] = None

        # Preview update timer (active only while dialog is visible)
        self.preview_timer = QTimer(self)
        self.preview_timer.timeout.connect(self._update_preview)

    def _init_ui(self) -> None:
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(20, 16, 20, 16)
        main_layout.setSpacing(12)

        # Header Title
        lbl_head = QLabel("➕ ĐĂNG KÝ HỒ SƠ NGƯỜI DÙNG / BỆNH NHÂN MỚI")
        lbl_head.setStyleSheet(f"font-size: 18px; font-weight: 800; color: {COLOR_TEXT_PRIMARY};")
        lbl_desc = QLabel(
            "Vui lòng điền đầy đủ thông tin bên dưới và chụp 2-3 góc ảnh khuôn mặt mẫu để hệ thống huấn luyện nhận diện tự động."
        )
        lbl_desc.setStyleSheet(f"font-size: 12px; color: {COLOR_TEXT_SECONDARY};")

        # Explicit Safety Pause Notice Banner
        lbl_pause_banner = QLabel(
            "Tiến trình bài tập tạm dừng trong cửa sổ này. Nhắc uống thuốc vẫn hoạt động."
        )
        lbl_pause_banner.setStyleSheet(get_badge_stylesheet("warning", font_size=12, padding="8px 14px", rounded=8))

        main_layout.addWidget(lbl_head)
        main_layout.addWidget(lbl_desc)
        main_layout.addWidget(lbl_pause_banner)

        # Body: Left Form (50%) + Right Camera Preview (50%)
        body_layout = QHBoxLayout()
        body_layout.setSpacing(14)

        # ==================== CỘT 1: ĐIỀN THÔNG TIN CÁ NHÂN ====================
        form_frame = QFrame()
        form_frame.setProperty("class", "card")
        lay_form = QVBoxLayout(form_frame)
        lay_form.setSpacing(10)

        # Section 1: Thông tin người được theo dõi
        lbl_f_title = QLabel("📋 1. THÔNG TIN NGƯỜI ĐƯỢC THEO DÕI")
        lbl_f_title.setProperty("class", "section-title")
        lay_form.addWidget(lbl_f_title)

        grid = QGridLayout()
        grid.setSpacing(10)

        # 1. Mã ID
        existing_patients = self.face_adapter.get_all_patients()
        next_id = str(len(existing_patients) + 1)
        lbl_id = QLabel("Mã hồ sơ (ID) (*):")
        lbl_id.setStyleSheet("font-weight: 600; color: #183A34;")
        self.edit_id = QLineEdit(next_id)
        self.edit_id.setPlaceholderText("VD: 001, 1...")
        self.edit_id.setFixedWidth(130)
        self.lbl_val_id = QLabel("")
        self.lbl_val_id.setStyleSheet("color: #C3293E; font-size: 12px; font-weight: 600;")
        self.lbl_val_id.setWordWrap(True)
        self.edit_id.textChanged.connect(lambda: self.lbl_val_id.setText(""))
        
        v_id = QVBoxLayout()
        v_id.setSpacing(2)
        v_id.addWidget(self.edit_id)
        v_id.addWidget(self.lbl_val_id)
        grid.addWidget(lbl_id, 0, 0, Qt.AlignTop)
        grid.addLayout(v_id, 0, 1)

        # 2. Họ và tên
        lbl_name = QLabel("Họ và tên (*):")
        lbl_name.setStyleSheet("font-weight: 600; color: #183A34;")
        self.edit_name = QLineEdit()
        self.edit_name.setPlaceholderText("Nhập đầy đủ họ và tên...")
        self.lbl_val_name = QLabel("")
        self.lbl_val_name.setStyleSheet("color: #C3293E; font-size: 12px; font-weight: 600;")
        self.lbl_val_name.setWordWrap(True)
        self.edit_name.textChanged.connect(lambda: self.lbl_val_name.setText(""))

        v_name = QVBoxLayout()
        v_name.setSpacing(2)
        v_name.addWidget(self.edit_name)
        v_name.addWidget(self.lbl_val_name)
        grid.addWidget(lbl_name, 1, 0, Qt.AlignTop)
        grid.addLayout(v_name, 1, 1)

        # 3. Tuổi
        lbl_age = QLabel("Tuổi:")
        lbl_age.setStyleSheet("font-weight: 600; color: #183A34;")
        self.spin_age = QSpinBox()
        self.spin_age.setRange(1, 120)
        self.spin_age.setValue(70)
        self.spin_age.setFixedWidth(130)
        grid.addWidget(lbl_age, 2, 0)
        grid.addWidget(self.spin_age, 2, 1)

        # 4. Giới tính
        lbl_gender = QLabel("Giới tính:")
        lbl_gender.setStyleSheet("font-weight: 600; color: #183A34;")
        self.combo_gender = QComboBox()
        self.combo_gender.addItems(["Nam", "Nữ", "Khác"])
        self.combo_gender.setFixedWidth(130)
        grid.addWidget(lbl_gender, 3, 0)
        grid.addWidget(self.combo_gender, 3, 1)

        # 5. Ghi chú bệnh lý
        lbl_notes = QLabel("Tiền sử / Bệnh lý:")
        lbl_notes.setStyleSheet("font-weight: 600; color: #183A34;")
        self.edit_notes = QLineEdit()
        self.edit_notes.setPlaceholderText("VD: Phục hồi vận động sau tai biến...")
        grid.addWidget(lbl_notes, 4, 0)
        grid.addWidget(self.edit_notes, 4, 1)

        # Section 2: Người thân & Tài khoản đăng nhập Cổng Người Nhà
        lbl_cg_title = QLabel("👥 2. TÀI KHOẢN NGƯỜI THÂN & EMAIL ĐĂNG NHẬP CỔNG WEB")
        lbl_cg_title.setProperty("class", "section-title")
        grid.addWidget(lbl_cg_title, 5, 0, 1, 2)

        lbl_cg_name = QLabel("Họ tên người thân (*):")
        lbl_cg_name.setStyleSheet("font-weight: 600; color: #183A34;")
        self.edit_cg_name = QLineEdit()
        self.edit_cg_name.setPlaceholderText("VD: Nguyễn Văn B (Bắt buộc)")
        self.lbl_val_cg_name = QLabel("")
        self.lbl_val_cg_name.setStyleSheet("color: #C3293E; font-size: 12px; font-weight: 600;")
        self.edit_cg_name.textChanged.connect(lambda: self.lbl_val_cg_name.setText(""))
        v_name = QVBoxLayout()
        v_name.setSpacing(2)
        v_name.addWidget(self.edit_cg_name)
        v_name.addWidget(self.lbl_val_cg_name)
        grid.addWidget(lbl_cg_name, 6, 0, Qt.AlignTop)
        grid.addLayout(v_name, 6, 1)

        lbl_cg_rel = QLabel("Mối quan hệ:")
        lbl_cg_rel.setStyleSheet("font-weight: 600; color: #183A34;")
        self.combo_cg_rel = QComboBox()
        self.combo_cg_rel.addItems(["Con cái", "Vợ / Chồng", "Cháu", "Người giám hộ", "Điều dưỡng / Bác sĩ", "Khác"])
        grid.addWidget(lbl_cg_rel, 7, 0)
        grid.addWidget(self.combo_cg_rel, 7, 1)

        lbl_cg_email = QLabel("Email đăng nhập Web (*):")
        lbl_cg_email.setStyleSheet(f"font-weight: 700; color: {COLOR_DANGER};")
        self.edit_cg_email = QLineEdit()
        self.edit_cg_email.setPlaceholderText("VD: contact-9fa9045b@example.com (Dùng đăng nhập web & nhận báo cáo)")
        self.lbl_val_email = QLabel("")
        self.lbl_val_email.setStyleSheet("color: #C3293E; font-size: 12px; font-weight: 600;")
        self.lbl_val_email.setWordWrap(True)
        self.edit_cg_email.textChanged.connect(lambda: self.lbl_val_email.setText(""))
        v_email = QVBoxLayout()
        v_email.setSpacing(2)
        v_email.addWidget(self.edit_cg_email)
        v_email.addWidget(self.lbl_val_email)
        grid.addWidget(lbl_cg_email, 8, 0, Qt.AlignTop)
        grid.addLayout(v_email, 8, 1)

        lbl_cg_phone = QLabel("Số điện thoại:")
        lbl_cg_phone.setStyleSheet("font-weight: 600; color: #183A34;")
        self.edit_cg_phone = QLineEdit()
        self.edit_cg_phone.setPlaceholderText("VD: 0912345678 (Tùy chọn)")
        grid.addWidget(lbl_cg_phone, 9, 0)
        grid.addWidget(self.edit_cg_phone, 9, 1)

        lbl_cg_pw = QLabel("Mật khẩu Web (*):")
        lbl_cg_pw.setStyleSheet(f"font-weight: 700; color: {COLOR_DANGER};")
        self.edit_cg_password = QLineEdit()
        self.edit_cg_password.setEchoMode(QLineEdit.Password)
        self.edit_cg_password.setPlaceholderText("Tối thiểu 6 ký tự (Dùng đăng nhập Cổng Người Nhà)")
        self.lbl_val_pw = QLabel("")
        self.lbl_val_pw.setStyleSheet("color: #C3293E; font-size: 12px; font-weight: 600;")
        self.edit_cg_password.textChanged.connect(lambda: self.lbl_val_pw.setText(""))
        v_pw = QVBoxLayout()
        v_pw.setSpacing(2)
        v_pw.addWidget(self.edit_cg_password)
        v_pw.addWidget(self.lbl_val_pw)
        grid.addWidget(lbl_cg_pw, 10, 0, Qt.AlignTop)
        grid.addLayout(v_pw, 10, 1)

        lbl_cg_pwc = QLabel("Nhập lại mật khẩu (*):")
        lbl_cg_pwc.setStyleSheet(f"font-weight: 700; color: {COLOR_DANGER};")
        self.edit_cg_password_confirm = QLineEdit()
        self.edit_cg_password_confirm.setEchoMode(QLineEdit.Password)
        self.edit_cg_password_confirm.setPlaceholderText("Xác nhận lại mật khẩu")
        self.lbl_val_pwc = QLabel("")
        self.lbl_val_pwc.setStyleSheet("color: #C3293E; font-size: 12px; font-weight: 600;")
        self.edit_cg_password_confirm.textChanged.connect(lambda: self.lbl_val_pwc.setText(""))
        v_pwc = QVBoxLayout()
        v_pwc.setSpacing(2)
        v_pwc.addWidget(self.edit_cg_password_confirm)
        v_pwc.addWidget(self.lbl_val_pwc)
        grid.addWidget(lbl_cg_pwc, 11, 0, Qt.AlignTop)
        grid.addLayout(v_pwc, 11, 1)

        self.chk_show_pw = QCheckBox("Hiển thị mật khẩu")
        self.chk_show_pw.setStyleSheet("font-size: 12px; color: #475569; margin-top: -2px;")
        self.chk_show_pw.toggled.connect(self._toggle_password_visibility)
        grid.addWidget(self.chk_show_pw, 12, 1)

        lay_form.addLayout(grid)

        # Section 3: Ảnh mẫu nhận diện
        lbl_p_title = QLabel("📸 3. ẢNH MẪU KHUÔN MẶT ĐÃ CHỤP")
        lbl_p_title.setProperty("class", "section-title")
        lay_form.addWidget(lbl_p_title)

        self.lbl_photo_status = QLabel("Chưa có ảnh mẫu nào. (Cần ít nhất 1 ảnh rõ mặt)")
        self.lbl_photo_status.setStyleSheet(get_badge_stylesheet("warning", font_size=12, padding="4px 10px"))
        lay_form.addWidget(self.lbl_photo_status)

        # Thumbnails strip
        self.thumb_layout = QHBoxLayout()
        self.thumb_layout.setSpacing(8)
        lay_form.addLayout(self.thumb_layout)

        # Photo Action Buttons
        btn_box_photo = QHBoxLayout()
        btn_box_photo.setSpacing(8)

        self.btn_capture = QPushButton("📸 Chụp Ảnh Ngay")
        self.btn_capture.setProperty("class", "btn-capture")
        self.btn_capture.clicked.connect(self._on_capture_click)

        self.btn_upload = QPushButton("📁 Chọn Ảnh...")
        self.btn_upload.setProperty("class", "btn-secondary")
        self.btn_upload.clicked.connect(self._on_upload_photo)

        self.btn_clear_photos = QPushButton("🗑️ Xóa Lại")
        self.btn_clear_photos.setProperty("class", "btn-secondary")
        self.btn_clear_photos.clicked.connect(self._on_clear_photos)

        btn_box_photo.addWidget(self.btn_capture)
        btn_box_photo.addWidget(self.btn_upload)
        btn_box_photo.addWidget(self.btn_clear_photos)
        lay_form.addLayout(btn_box_photo)

        lay_form.addStretch()

        scroll_form = QScrollArea()
        scroll_form.setWidgetResizable(True)
        scroll_form.setFrameShape(QFrame.NoFrame)
        scroll_form.setStyleSheet("QScrollArea { border: none; background: transparent; }")
        scroll_form.setWidget(form_frame)
        body_layout.addWidget(scroll_form, 50)

        # ==================== CỘT 2: CAMERA TRỰC TIẾP & HƯỚNG DẪN ====================
        cam_frame = QFrame()
        cam_frame.setProperty("class", "card")
        lay_cam = QVBoxLayout(cam_frame)
        lay_cam.setSpacing(10)

        lbl_c_title = QLabel("📷 4. CAMERA TRỰC TIẾP CĂN CHỈNH KHUÔN MẶT")
        lbl_c_title.setProperty("class", "section-title")
        lay_cam.addWidget(lbl_c_title)

        # Preview display
        self.preview_display = QLabel()
        self.preview_display.setMinimumSize(360, 270)
        self.preview_display.setAlignment(Qt.AlignCenter)
        self.preview_display.setStyleSheet(f"background-color: #0F141C; border: 1.5px solid {COLOR_BORDER}; border-radius: 8px;")
        lay_cam.addWidget(self.preview_display, 1)

        # Guiding Status
        self.lbl_guidance = QLabel("⚪ Đang kết nối luồng camera...")
        self.lbl_guidance.setStyleSheet(get_badge_stylesheet("primary", font_size=12, padding="6px 12px"))
        lay_cam.addWidget(self.lbl_guidance)

        # Instructions note
        lbl_note = QLabel(
            "💡 Hướng dẫn: Giữ khoảng cách 1.0m - 2.0m trước camera, ánh sáng rõ ràng. "
            "Nên chụp 2-3 góc mặt (nhìn thẳng, hơi nghiêng nhẹ) để tăng độ chính xác nhận diện."
        )
        lbl_note.setStyleSheet(f"font-size: 12px; color: {COLOR_TEXT_SECONDARY}; line-height: 1.4;")
        lbl_note.setWordWrap(True)
        lay_cam.addWidget(lbl_note)

        body_layout.addWidget(cam_frame, 50)
        main_layout.addLayout(body_layout, 1)

        # ==================== HÀNG NÚT DƯỚI CÙNG ====================
        lay_footer = QHBoxLayout()
        lay_footer.addStretch()

        self.btn_cancel = QPushButton("Hủy / Đóng")
        self.btn_cancel.setProperty("class", "btn-secondary")
        self.btn_cancel.clicked.connect(self.reject)

        self.btn_save = QPushButton("💾 Lưu Hồ Sơ và Huấn Luyện Nhận Diện")
        self.btn_save.setProperty("class", "btn-primary")
        self.btn_save.clicked.connect(self._on_save_patient)

        lay_footer.addWidget(self.btn_cancel)
        lay_footer.addWidget(self.btn_save)
        main_layout.addLayout(lay_footer)

    def _update_preview(self) -> None:
        """Render live camera feed with real-time visual face tracking and stabilized bounding box."""
        frame = self.camera_manager.get_latest_frame()
        if frame is None:
            return

        display_frame = frame.copy()
        h, w = display_frame.shape[:2]
        tracked = self.bbox_tracker.update(display_frame)

        if tracked is not None:
            bx, by, bw, bh, conf = tracked

            # Draw stabilized medical HUD bounding box
            color = (74, 222, 128)  # Emerald green
            cv2.rectangle(display_frame, (bx, by), (bx + bw, by + bh), color, 2)

            # Draw tech corner accents for modern medical HUD aesthetic
            clen = max(10, min(bw, bh) // 5)
            corner_color = (130, 255, 160)
            cv2.line(display_frame, (bx, by), (bx + clen, by), corner_color, 3)
            cv2.line(display_frame, (bx, by), (bx, by + clen), corner_color, 3)
            cv2.line(display_frame, (bx + bw, by), (bx + bw - clen, by), corner_color, 3)
            cv2.line(display_frame, (bx + bw, by), (bx + bw, by + clen), corner_color, 3)
            cv2.line(display_frame, (bx, by + bh), (bx + clen, by + bh), corner_color, 3)
            cv2.line(display_frame, (bx, by + bh), (bx, by + bh - clen), corner_color, 3)
            cv2.line(display_frame, (bx + bw, by + bh), (bx + bw - clen, by + bh), corner_color, 3)
            cv2.line(display_frame, (bx + bw, by + bh), (bx + bw, by + bh - clen), corner_color, 3)

            # Draw high-contrast text badge above box
            conf_pct = int(round(conf * 100))
            label_text = f"Face OK ({conf_pct}%)"
            font = cv2.FONT_HERSHEY_SIMPLEX
            font_scale = 0.55
            thickness = 2
            (tw_txt, th_txt), baseline = cv2.getTextSize(label_text, font, font_scale, thickness)
            badge_y = max(th_txt + 6, by - 6)
            cv2.rectangle(
                display_frame,
                (bx, badge_y - th_txt - 4),
                (bx + tw_txt + 8, badge_y + baseline),
                (20, 30, 45),
                -1,
            )
            cv2.putText(
                display_frame,
                label_text,
                (bx + 4, badge_y - 2),
                font,
                font_scale,
                color,
                thickness,
            )

            if self._last_guidance_state is not True:
                self.lbl_guidance.setText("🟢 Khuôn mặt rõ nét! Bấm 'Chụp Ảnh Ngay' để lấy mẫu.")
                self.lbl_guidance.setStyleSheet(get_badge_stylesheet("success", font_size=12, padding="6px 12px"))
                self._last_guidance_state = True
        else:
            if self._last_guidance_state is not False:
                self.lbl_guidance.setText("🟠 Chưa thấy khuôn mặt rõ. Vui lòng nhìn thẳng vào camera...")
                self.lbl_guidance.setStyleSheet(get_badge_stylesheet("warning", font_size=12, padding="6px 12px"))
                self._last_guidance_state = False

        # Convert to QPixmap
        bytes_per_line = display_frame.shape[2] * w
        rgb_frame = cv2.cvtColor(display_frame, cv2.COLOR_BGR2RGB)
        qimg = QImage(rgb_frame.data, w, h, bytes_per_line, QImage.Format_RGB888).copy()
        pix = QPixmap.fromImage(qimg).scaled(
            self.preview_display.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation
        )
        self.preview_display.setPixmap(pix)

    def _on_capture_click(self) -> None:
        """Capture face crop from latest frame using stabilized tracked bounding box."""
        frame = self.camera_manager.get_latest_frame()
        if frame is None:
            QMessageBox.warning(self, "Lỗi Camera", "Không nhận được tín hiệu từ camera.")
            return

        det = self.bbox_tracker.get_latest_box()
        if det is None:
            det = self.face_adapter.detect_face(frame)

        if det is None:
            QMessageBox.warning(
                self,
                "Không Tìm Thấy Mặt",
                "Chưa phát hiện khuôn mặt rõ nét trong khung hình. Vui lòng nhìn thẳng vào camera và thử lại.",
            )
            return

        face_crop = self.face_adapter.crop_face(frame, box=det)
        self.captured_faces.append(face_crop)
        self._refresh_thumbnails()

    def _on_upload_photo(self) -> None:
        """Allow user to select an existing face photo from disk."""
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Chọn ảnh khuôn mặt mẫu",
            "",
            "Image Files (*.jpg *.jpeg *.png *.bmp)",
        )
        if not file_path:
            return

        img = cv2.imread(file_path)
        if img is None:
            QMessageBox.warning(self, "Lỗi đọc tệp", "Không thể đọc tệp hình ảnh đã chọn.")
            return

        det = self.face_adapter.detect_face(img)
        face_crop = self.face_adapter.crop_face(img, box=det)
        self.captured_faces.append(face_crop)
        self._refresh_thumbnails()

    def _refresh_thumbnails(self) -> None:
        """Update photo count and thumbnail strip."""
        while self.thumb_layout.count():
            item = self.thumb_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        count = len(self.captured_faces)
        if count == 0:
            self.lbl_photo_status.setText("Chưa có ảnh mẫu nào. (Cần ít nhất 1 ảnh rõ mặt)")
            self.lbl_photo_status.setStyleSheet(get_badge_stylesheet("warning", font_size=12, padding="4px 10px"))
        else:
            self.lbl_photo_status.setText(f"✓ Đã có {count} ảnh mẫu khuôn mặt sẵn sàng.")
            self.lbl_photo_status.setStyleSheet(get_badge_stylesheet("success", font_size=12, padding="4px 10px"))

            for idx, crop in enumerate(self.captured_faces[-4:]):
                rgb = cv2.cvtColor(crop, cv2.COLOR_BGR2RGB)
                h, w, ch = rgb.shape
                qimg = QImage(rgb.data, w, h, ch * w, QImage.Format_RGB888).copy()
                lbl_thumb = QLabel()
                lbl_thumb.setFixedSize(60, 60)
                lbl_thumb.setScaledContents(True)
                lbl_thumb.setPixmap(QPixmap.fromImage(qimg))
                lbl_thumb.setStyleSheet(f"border: 2px solid {COLOR_PRIMARY}; border-radius: 6px;")
                self.thumb_layout.addWidget(lbl_thumb)

        self.thumb_layout.addStretch()

    def _toggle_password_visibility(self, checked: bool) -> None:
        """Toggle echo mode between Password and Normal for caregiver credentials."""
        mode = QLineEdit.Normal if checked else QLineEdit.Password
        self.edit_cg_password.setEchoMode(mode)
        self.edit_cg_password_confirm.setEchoMode(mode)

    def _on_clear_photos(self) -> None:
        """Clear captured face samples."""
        self.captured_faces.clear()
        self._refresh_thumbnails()

    def _on_save_patient(self) -> None:
        """Validate, register patient and caregiver, save photos, and trigger model retraining."""
        pid = self.edit_id.text().strip()
        name = self.edit_name.text().strip()
        age = self.spin_age.value()
        gender = self.combo_gender.currentText()
        notes = self.edit_notes.text().strip()

        has_err = False
        if not pid:
            self.lbl_val_id.setText("⚠️ Vui lòng nhập mã hồ sơ (ID)")
            has_err = True

        if not name:
            self.lbl_val_name.setText("⚠️ Vui lòng nhập họ và tên người cao tuổi")
            has_err = True

        cg_name = self.edit_cg_name.text().strip()
        cg_rel = self.combo_cg_rel.currentText()
        cg_email = self.edit_cg_email.text().strip()
        cg_phone = self.edit_cg_phone.text().strip()
        cg_pw = self.edit_cg_password.text().strip()
        cg_pwc = self.edit_cg_password_confirm.text().strip()

        if not cg_name:
            self.lbl_val_cg_name.setText("⚠️ Vui lòng nhập họ tên người thân")
            has_err = True

        from shared.services.user_service import UserService

        if not cg_email:
            self.lbl_val_email.setText("⚠️ Vui lòng nhập Email người thân để đăng nhập Web và nhận thông báo")
            has_err = True
        elif not UserService.validate_email(cg_email):
            self.lbl_val_email.setText(f"⚠️ '{cg_email}' không đúng định dạng email (VD: contact-0f251f49@example.com)")
            has_err = True

        if not cg_pw:
            self.lbl_val_pw.setText("⚠️ Vui lòng đặt mật khẩu đăng nhập Cổng Người Nhà")
            has_err = True
        elif len(cg_pw) < 6:
            self.lbl_val_pw.setText("⚠️ Mật khẩu phải có ít nhất 6 ký tự")
            has_err = True

        if not cg_pwc:
            self.lbl_val_pwc.setText("⚠️ Vui lòng nhập lại mật khẩu xác nhận")
            has_err = True
        elif cg_pw and cg_pwc != cg_pw:
            self.lbl_val_pwc.setText("⚠️ Mật khẩu xác nhận không khớp")
            has_err = True

        if has_err:
            QMessageBox.warning(self, "Thông Tin Chưa Hợp Lệ", "Vui lòng kiểm tra các trường thông tin được đánh dấu màu đỏ.")
            return

        if not self.captured_faces:
            # Auto-attempt capture from live frame
            frame = self.camera_manager.get_latest_frame()
            if frame is not None:
                det = self.bbox_tracker.get_latest_box() or self.face_adapter.detect_face(frame)
                if det is not None:
                    crop = self.face_adapter.crop_face(frame, box=det)
                    self.captured_faces.append(crop)

        if not self.captured_faces:
            QMessageBox.warning(
                self,
                "Chưa Có Ảnh Nhận Diện",
                "Hệ thống cần ít nhất 1 ảnh khuôn mặt mẫu. Hãy bấm '📸 Chụp Ảnh Ngay' hoặc '📁 Chọn Ảnh...' trước khi lưu.",
            )
            return

        # Register and train
        profile = self.face_adapter.register_patient(
            name=name,
            patient_id=pid,
            age=age,
            gender=gender,
            notes=notes,
            face_images=self.captured_faces,
            caregiver_name=cg_name,
            caregiver_relationship=cg_rel,
            caregiver_email=cg_email,
            caregiver_phone=cg_phone,
            caregiver_password=cg_pw,
        )

        QMessageBox.information(
            self,
            "Đăng Ký Thành Công",
            f"Đã lưu thành công hồ sơ người dùng:\n"
            f"• Mã ID: {pid}\n"
            f"• Họ tên người cao tuổi: {name} ({age} tuổi - {gender})\n\n"
            f"🔑 TÀI KHOẢN ĐĂNG NHẬP CỔNG NGƯỜI NHÀ (WEB):\n"
            f"• Họ tên người thân: {cg_name} ({cg_rel})\n"
            f"• Email đăng nhập: {cg_email}\n"
            f"• Mật khẩu: {cg_pw}\n"
            f"• Số điện thoại: {cg_phone or 'Chưa cập nhật'}\n\n"
            f"Người thân có thể dùng Email và Mật khẩu trên để đăng nhập ngay trên Web Cổng Người Nhà để theo dõi và trích xuất hồ sơ của {name}.",
        )

        self.preview_timer.stop()
        self.bbox_tracker.reset()
        self.patient_registered.emit(profile)
        self.accept()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self.preview_timer.start(40)  # ~25 FPS

    def hideEvent(self, event) -> None:
        self.preview_timer.stop()
        self.bbox_tracker.reset()
        self._last_guidance_state = None
        super().hideEvent(event)

    def done(self, r: int) -> None:
        self.preview_timer.stop()
        self.bbox_tracker.reset()
        super().done(r)

    def closeEvent(self, event) -> None:
        self.preview_timer.stop()
        self.bbox_tracker.reset()
        event.accept()


class PatientManagementDialog(QDialog):
    """
    Dialog to inspect and manage all registered patient profiles.
    Allows viewing all personal info, deleting, or registering new patients.
    """

    patient_selected = pyqtSignal(dict)

    def __init__(self, face_adapter: Optional[FaceAdapter] = None, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.face_adapter = face_adapter or FaceAdapter()

        self.setWindowTitle("Quản Lý Danh Sách Người Bệnh và Hồ Sơ")
        self.setMinimumSize(880, 500)
        self.setStyleSheet(DIALOG_STYLESHEET)

        self._init_ui()
        self._load_table_data()

    def _init_ui(self) -> None:
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(20, 18, 20, 18)
        main_layout.setSpacing(12)

        # Header
        top_row = QHBoxLayout()
        v_title = QVBoxLayout()
        lbl_head = QLabel("👥 DANH SÁCH HỒ SƠ NGƯỜI BỆNH ĐÃ ĐĂNG KÝ")
        lbl_head.setStyleSheet(f"font-size: 17px; font-weight: 800; color: {COLOR_TEXT_PRIMARY};")
        lbl_desc = QLabel("Xem danh sách thông tin cá nhân và dữ liệu nhận diện khuôn mặt trong hệ thống.")
        lbl_desc.setStyleSheet(f"font-size: 12px; color: {COLOR_TEXT_SECONDARY};")
        v_title.addWidget(lbl_head)
        v_title.addWidget(lbl_desc)
        top_row.addLayout(v_title)

        top_row.addStretch()

        btn_add = QPushButton("➕ Đăng Ký Người Mới")
        btn_add.setProperty("class", "btn-primary")
        btn_add.clicked.connect(self._on_open_register)
        top_row.addWidget(btn_add)

        main_layout.addLayout(top_row)

        # Table
        self.table = QTableWidget()
        self.table.setColumnCount(8)
        self.table.setHorizontalHeaderLabels([
            "Mã ID", "Họ và Tên", "Tuổi", "Giới Tính", "Người Thân", "Gmail Cảnh Báo", "Số Ảnh", "Ghi Chú"
        ])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(6, QHeaderView.ResizeToContents)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        main_layout.addWidget(self.table, 1)

        # Footer Actions
        lay_footer = QHBoxLayout()
        btn_delete = QPushButton("🗑️ Xóa Hồ Sơ Đang Chọn")
        btn_delete.setProperty("class", "btn-danger")
        btn_delete.clicked.connect(self._on_delete_selected)

        btn_close = QPushButton("Đóng")
        btn_close.setProperty("class", "btn-secondary")
        btn_close.clicked.connect(self.accept)

        lay_footer.addWidget(btn_delete)
        lay_footer.addStretch()
        lay_footer.addWidget(btn_close)
        main_layout.addLayout(lay_footer)

    def _load_table_data(self) -> None:
        """Populate table with registered patients."""
        patients = self.face_adapter.get_all_patients()
        self.table.setRowCount(len(patients))

        for row, p in enumerate(patients):
            cg = p.get("caregiver") or {}
            cg_name = cg.get("name", "—") if isinstance(cg, dict) else "—"
            cg_email = cg.get("email", "—") if isinstance(cg, dict) else "—"

            self.table.setItem(row, 0, QTableWidgetItem(str(p.get("patient_id") or p.get("user_id", ""))))
            self.table.setItem(row, 1, QTableWidgetItem(str(p.get("name", ""))))
            self.table.setItem(row, 2, QTableWidgetItem(str(p.get("age", ""))))
            self.table.setItem(row, 3, QTableWidgetItem(str(p.get("gender", ""))))
            self.table.setItem(row, 4, QTableWidgetItem(str(cg_name)))
            self.table.setItem(row, 5, QTableWidgetItem(str(cg_email)))
            self.table.setItem(row, 6, QTableWidgetItem(f"{p.get('image_count', 1)} ảnh"))
            self.table.setItem(row, 7, QTableWidgetItem(str(p.get("notes", ""))))

    def _on_delete_selected(self) -> None:
        """Delete selected patient profile."""
        cur_row = self.table.currentRow()
        if cur_row < 0:
            QMessageBox.warning(self, "Chưa chọn", "Vui lòng chọn một người dùng trong bảng để xóa.")
            return

        pid_item = self.table.item(cur_row, 0)
        name_item = self.table.item(cur_row, 1)
        if not pid_item or not name_item:
            return

        pid = pid_item.text()
        name = name_item.text()

        confirm = QMessageBox.question(
            self,
            "Xác Nhận Xóa",
            f"Bạn có chắc muốn xóa hồ sơ của:\n• Mã ID: {pid}\n• Họ tên: {name}?\n\n(Dữ liệu khuôn mặt mẫu cũng sẽ bị xóa)",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )

        if confirm == QMessageBox.Yes:
            self.face_adapter.delete_patient(pid)
            self._load_table_data()
            QMessageBox.information(self, "Đã Xóa", f"Đã xóa thành công hồ sơ của {name}.")

    def _on_open_register(self) -> None:
        """Open registration modal from manager."""
        dlg = RegistrationDialog(self.face_adapter, self)
        if dlg.exec_() == QDialog.Accepted:
            self._load_table_data()

