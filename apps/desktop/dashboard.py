"""
Main Dashboard for AI Rehabilitation & Safety Assistant using PyQt5.
Theme: Modern Medical Center (Light canvas, navy text, royal blue accents).
Face, emotion, rehabilitation and durable medication reminders.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import time
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import cv2
import numpy as np
from PyQt5.QtCore import Qt, QTimer, pyqtSignal, QSize, QRect
from PyQt5.QtGui import QColor, QFont, QImage, QPixmap, QPainter
from PyQt5.QtWidgets import (
    QApplication,
    QButtonGroup,
    QComboBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QSizePolicy,
    QSlider,
    QSplitter,
    QStackedWidget,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from apps.desktop.registration_dialog import PatientManagementDialog, RegistrationDialog
from apps.desktop.router import DashboardRouter
from apps.desktop.state import AppState
from apps.desktop.theme import (
    COLOR_BORDER,
    COLOR_DANGER,
    COLOR_PRIMARY,
    COLOR_PRIMARY_BG,
    COLOR_SUCCESS,
    COLOR_WARNING,
    LIGHT_THEME_STYLESHEET,
    get_badge_stylesheet,
)
from shared.config import (
    CAMERA_INDEX,
    DEFAULT_PATIENT_ID,
    DEFAULT_PATIENT_NAME,
    EXERCISES,
    MODULES_DIR,
)
from shared.camera.camera_manager import CameraManager
from apps.desktop.today_todo_widget import TodayTodoWidget
from apps.desktop.cognitive_games_widget import CognitiveGamesWidget
from shared.database.connection import UnifiedDatabaseManager
from shared.database.outbox_helper import insert_outbox_event
from features.exercise_rehab.rehab_session_adapter import RehabSessionAdapter
from shared.config import DATA_DIR, SHARED_DIR

logger = logging.getLogger("Dashboard")

# Export STYLESHEET for compatibility with apps/desktop/main.py
STYLESHEET = LIGHT_THEME_STYLESHEET

# Direct button stylesheets for crisp medical aesthetic (accessible rehab panel)
BTN_START_STYLE = (
    "QPushButton { background-color: #285C4D; color: #FFFFFF; font-weight: 700; font-size: 13px; "
    "border: none; border-radius: 8px; padding: 8px 14px; min-height: 40px; }"
    "QPushButton:hover { background-color: #214F42; }"
    "QPushButton:disabled { background-color: #E4EBDD; color: #84998A; }"
)
BTN_STOP_STYLE = (
    "QPushButton { background-color: #C3293E; color: #FFFFFF; font-weight: 700; font-size: 13px; "
    "border: none; border-radius: 8px; padding: 8px 14px; min-height: 40px; }"
    "QPushButton:hover { background-color: #A52133; }"
    "QPushButton:disabled { background-color: #F8D7DA; color: #C47F87; }"
)
BTN_RESET_STYLE = (
    "QPushButton { background-color: #FFFFFF; color: #183A34; font-weight: 600; font-size: 13px; "
    "border: 1px solid #DFE5DF; border-radius: 8px; padding: 8px 14px; min-height: 40px; }"
    "QPushButton:hover { background-color: #EDF2E9; border-color: #285C4D; color: #285C4D; }"
)
BTN_REPORT_STYLE = (
    "QPushButton { background-color: #FFFFFF; color: #285C4D; font-weight: 700; font-size: 13px; "
    "border: 1px solid #D8E3D0; border-radius: 8px; padding: 8px 14px; min-height: 38px; }"
    "QPushButton:hover { background-color: #EAF0E2; border-color: #285C4D; }"
)
EX_ACTIVE_STYLE = (
    "QPushButton { background-color: #EAF0E2; color: #285C4D; font-weight: 700; font-size: 13px; "
    "border: 1.5px solid #285C4D; border-radius: 8px; padding: 8px 12px; text-align: left; min-height: 36px; }"
)
EX_INACTIVE_STYLE = (
    "QPushButton { background-color: #FFFFFF; color: #183A34; font-weight: 600; font-size: 13px; "
    "border: 1px solid #DFE5DF; border-radius: 8px; padding: 8px 12px; text-align: left; min-height: 36px; }"
    "QPushButton:hover { background-color: #EDF2E9; border-color: #ABC0AA; }"
)


class VideoDisplayWidget(QWidget):
    """
    High-performance, aspect-ratio-preserving video viewport widget.
    Renders camera frames using QPainter with KeepAspectRatio and dark letterbox padding.
    Prevents resize loops and decouples frame resolution from layout sizeHint.
    """

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._pixmap: Optional[QPixmap] = None
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setMinimumSize(320, 240)
        self.setStyleSheet("background-color: #0E1626; border-radius: 8px;")

    def set_frame_pixmap(self, pix: QPixmap) -> None:
        """Update pixmap and trigger flicker-free repaint."""
        self._pixmap = pix
        self.update()

    def sizeHint(self) -> QSize:
        return QSize(640, 480)

    def minimumSizeHint(self) -> QSize:
        return QSize(320, 240)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("#0E1626"))
        if self._pixmap and not self._pixmap.isNull():
            w, h = self.width(), self.height()
            pw, ph = self._pixmap.width(), self._pixmap.height()
            if pw <= 0 or ph <= 0:
                return
            # Strict KeepAspectRatio letterboxing / pillarboxing
            scale = min(w / pw, h / ph)
            nw = max(1, int(pw * scale))
            nh = max(1, int(ph * scale))
            nx = (w - nw) // 2
            ny = (h - nh) // 2
            painter.setRenderHint(QPainter.SmoothPixmapTransform, True)
            painter.drawPixmap(QRect(nx, ny, nw, nh), self._pixmap)
        else:
            painter.setPen(QColor("#70817D"))
            font = painter.font()
            font.setPointSize(12)
            font.setBold(True)
            painter.setFont(font)
            painter.drawText(self.rect(), Qt.AlignCenter, "📹 Đang kết nối tín hiệu camera...")


class DashboardWindow(QMainWindow):
    """
    Main Application Window for AI Rehabilitation & Safety Assistant.
    Unified single-screen interface supporting:
      1. Personalization (Face Recognition)
      2. Emotion Context
      3. Rehabilitation Exercise Engine (Arm Raise, Sit-to-Stand, Marching)
      4. Medication reminders and self-reported history
    """

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("AI REHABILITATION & SAFETY ASSISTANT — Hệ Thống Trợ Lý Phục Hồi & An Toàn Y Tế")
        self.setMinimumSize(1150, 720)
        self.setStyleSheet(STYLESHEET)

        from features.medication.medication_migration import archive_legacy
        archive_legacy(DATA_DIR, SHARED_DIR)
        self.state = AppState()
        self.app_state = self.state
        self.router = DashboardRouter(self)
        self.active_exercise_id = "arm_raise"
        self.current_patient_id = ""
        self.current_patient_name = "Chưa nhận diện"

        # Configurable Routine Mode Sequence State
        self.is_routine_mode = False
        self.routine_step_idx = 0
        self.rehab_plans: Dict[str, Dict[str, Any]] = {}
        self.routine_time = "16:00"
        self._last_rehab_plans_signature = ""
        self._last_rehab_check_time = 0.0
        self.routine_steps = [
            {"id": "arm_raise", "name": "Nâng tay qua đầu (Vươn vai)", "target": 10},
            {"id": "sit_to_stand", "name": "Đứng lên ngồi xuống (Co duỗi chân)", "target": 10},
            {"id": "marching", "name": "Đi bộ tại chỗ (Nâng cao đùi)", "target": 15},
        ]
        self._last_16h_alert_date = ""

        # Mode: "camera" or "video_test"
        self.current_source_mode = "camera"
        # Initialize CameraManager Singleton
        self.camera_manager = CameraManager.get_instance(camera_index=CAMERA_INDEX)
        self.camera_manager.register_processor(self._process_video_frame)
        self.camera_manager.frame_ready.connect(self._on_frame_ready)

        # Initialize UI Components
        self._init_ui()

        from features.emotion.emotion_summary_service import EmotionSummaryService
        self.emotion_summary_service = EmotionSummaryService(self.unified_db)
        self._last_emotion_sample_pts = 0.0

        # Load dynamic rehab plans configured by caregiver
        self._load_rehab_plans_from_db()

        # Start Camera capture engine
        self.camera_manager.start_camera()

        # AI features (posture, fall risk, object finder, medication intake): background worker,
        # fails safe and can be turned off with AI_CORE_ENABLED=0.
        from shared.ai_runtime.app_hooks import start_ai_core
        self.ai_core = start_ai_core(self)

        # Mental support: when the emotion summary says "sad", speak a suggestion and start music.
        self._comfort_timer = QTimer(self)
        self._comfort_timer.timeout.connect(self._check_sadness_comfort)
        self._comfort_timer.start(60_000)

        # Setup 1-second periodic timer for UI sync
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._on_timer_tick)
        self.timer.start(1000)

        # Start maximized for full visual experience
        self.showMaximized()

    def _init_ui(self) -> None:
        """Construct the unified single-window user interface."""
        central = QWidget(self)
        self.setCentralWidget(central)
        shell_layout = QHBoxLayout(central)
        shell_layout.setContentsMargins(0, 0, 0, 0)
        shell_layout.setSpacing(0)
        sidebar = QFrame()
        sidebar.setObjectName("DesktopSidebar")
        sidebar.setFixedWidth(216)
        navigation = QVBoxLayout(sidebar)
        navigation.setContentsMargins(18, 30, 18, 22)
        navigation.setSpacing(10)
        monogram = QLabel("AI")
        monogram.setObjectName("SidebarMonogram")
        monogram.setFixedSize(42, 42)
        monogram.setAlignment(Qt.AlignCenter)
        navigation.addWidget(monogram)
        brand = QLabel("AI CARE")
        brand.setObjectName("SidebarBrand")
        navigation.addWidget(brand)
        subtitle = QLabel("Chăm sóc & phục hồi")
        subtitle.setObjectName("SidebarCaption")
        navigation.addWidget(subtitle)
        navigation.addSpacing(34)
        caption = QLabel("KHÔNG GIAN CHĂM SÓC")
        caption.setObjectName("SidebarCaption")
        navigation.addWidget(caption)
        navigation.addSpacing(6)
        self._navigation_buttons = []
        for index, title in enumerate(("Việc cần làm hôm nay", "Nhận diện & Phục hồi", "Trí nhớ & Thư giãn")):
            button = QPushButton(title)
            button.setObjectName("SidebarNav")
            button.setCheckable(True)
            button.setChecked(index == 0)
            button.setMinimumHeight(56)
            button.setCursor(Qt.PointingHandCursor)
            button.clicked.connect(lambda checked, i=index: self.medication_tabs.setCurrentIndex(i))
            navigation.addWidget(button)
            self._navigation_buttons.append(button)
        navigation.addStretch()
        footer = QLabel("●  Xử lý trên thiết bị\n\nCamera · Bài tập · Thuốc")
        footer.setObjectName("SidebarFooter")
        footer.setWordWrap(True)
        navigation.addWidget(footer)
        shell_layout.addWidget(sidebar)
        workspace = QWidget()
        workspace.setObjectName("DesktopWorkspace")
        root_layout = QVBoxLayout(workspace)
        root_layout.setContentsMargins(20, 16, 20, 12)
        root_layout.setSpacing(12)
        shell_layout.addWidget(workspace, 1)

        # 1. TOP HEADER BAR — compact, single row
        header = QFrame()
        header.setObjectName("HeaderBar")
        lay_header = QHBoxLayout(header)
        lay_header.setContentsMargins(16, 6, 16, 6)
        lay_header.setSpacing(10)

        # Branding Title & Subtitle
        v_title = QVBoxLayout()
        v_title.setSpacing(1)
        lbl_brand = QLabel("🏥 AI REHAB & SAFETY")
        lbl_brand.setObjectName("BrandTitle")
        lbl_sub = QLabel("Phục Hồi Chức Năng và Nhắc Uống Thuốc")
        lbl_sub.setObjectName("BrandSubtitle")
        v_title.addWidget(lbl_brand)
        v_title.addWidget(lbl_sub)
        lay_header.addLayout(v_title)

        lay_header.addStretch()

        # Active Patient Chip in Header
        self.lbl_header_patient = QLabel("👤 Chưa nhận diện người dùng")
        self.lbl_header_patient.setStyleSheet(get_badge_stylesheet("primary", font_size=13, padding="5px 12px", rounded=12))
        self.lbl_header_patient.setMaximumWidth(360)
        self.lbl_header_patient.setWordWrap(False)
        self.lbl_header_patient.setToolTip("Người dùng hiện tại đang được theo dõi")
        lay_header.addWidget(self.lbl_header_patient)

        # Hardware Camera Status Badge
        is_hw = self.camera_manager.is_hardware_camera()
        cam_text = "● Camera: Phần cứng" if is_hw else "● Camera: Giả lập"
        cam_kind = "success" if is_hw else "warning"
        self.lbl_cam_badge = QLabel(cam_text)
        self.lbl_cam_badge.setStyleSheet(get_badge_stylesheet(cam_kind, font_size=12, padding="4px 10px", rounded=10))
        lay_header.addWidget(self.lbl_cam_badge)

        # Digital Clock
        self.lbl_clock = QLabel(datetime.now().strftime("%H:%M:%S | %d/%m/%Y"))
        self.lbl_clock.setStyleSheet("font-size: 13px; font-weight: 700; color: #667B74; padding-left: 4px;")
        lay_header.addWidget(self.lbl_clock)

        root_layout.addWidget(header)

        # Reminder banner — compact
        banner = QLabel("Ứng dụng cần chạy để nhắc đúng giờ • Xác nhận uống thuốc là tự khai")
        banner.setStyleSheet("font-size: 12px; padding: 3px 10px; color: #667B74; background-color: #F8FAF7;")
        root_layout.addWidget(banner)

        # 3. MAIN SPLITTER BODY — the rehab tab content
        splitter = QSplitter(Qt.Horizontal)
        splitter.setHandleWidth(3)
        splitter.setChildrenCollapsible(False)

        # ==================== LEFT COLUMN: CAMERA & CONTEXT CARDS (~70-75%) ====================
        left_widget = QWidget()
        left_layout = QVBoxLayout(left_widget)
        left_layout.setContentsMargins(8, 4, 4, 4)
        left_layout.setSpacing(4)

        # Top Context Cards: [Danh tính] & [Ngữ cảnh cảm xúc]
        top_cards = QHBoxLayout()
        top_cards.setSpacing(6)

        # Card: Nhận diện khuôn mặt & Danh tính
        card_user = QFrame()
        card_user.setProperty("class", "ui-card")
        lay_card_user = QVBoxLayout(card_user)
        lay_card_user.setContentsMargins(10, 6, 10, 6)
        lay_card_user.setSpacing(2)

        top_u_row = QHBoxLayout()
        top_u_row.setSpacing(4)
        lbl_u_title = QLabel("👤 Danh tính")
        lbl_u_title.setStyleSheet("font-size: 12px; font-weight: 700; color: #285C4D;")
        top_u_row.addWidget(lbl_u_title)
        top_u_row.addStretch()

        btn_register = QPushButton("➕ Đăng ký")
        btn_register.setProperty("class", "btn-pill")
        btn_register.setStyleSheet("font-size: 11px; padding: 2px 8px; border-radius: 8px;")
        btn_register.setToolTip("Điền thông tin cá nhân và chụp ảnh mẫu khuôn mặt")
        btn_register.clicked.connect(self._on_open_registration)

        btn_manage = QPushButton("👥 Danh sách")
        btn_manage.setProperty("class", "btn-pill-secondary")
        btn_manage.setStyleSheet("font-size: 11px; padding: 2px 8px; border-radius: 8px;")
        btn_manage.setToolTip("Xem danh sách thông tin người bệnh đã đăng ký")
        btn_manage.clicked.connect(self._on_open_patients_manager)

        top_u_row.addWidget(btn_register)
        top_u_row.addWidget(btn_manage)
        lay_card_user.addLayout(top_u_row)

        self.lbl_face_status = QLabel("🟢 Đang quét nhận diện khuôn mặt...")
        self.lbl_face_status.setStyleSheet("font-size: 13px; font-weight: 700; color: #183A34;")
        self.lbl_face_status.setWordWrap(True)

        self.lbl_face_sub = QLabel("Tự động nhận diện người dùng đã đăng ký trong cơ sở dữ liệu.")
        self.lbl_face_sub.setStyleSheet("font-size: 11px; color: #70817D;")
        self.lbl_face_sub.setWordWrap(True)

        lay_card_user.addWidget(self.lbl_face_status)
        lay_card_user.addWidget(self.lbl_face_sub)
        top_cards.addWidget(card_user, 1)

        # Card: Cảm xúc (Emotion State)
        card_emo = QFrame()
        card_emo.setProperty("class", "ui-card")
        lay_card_emo = QVBoxLayout(card_emo)
        lay_card_emo.setContentsMargins(10, 6, 10, 6)
        lay_card_emo.setSpacing(2)

        lbl_e_title = QLabel("😊 Ngữ cảnh cảm xúc")
        lbl_e_title.setStyleSheet("font-size: 12px; font-weight: 700; color: #285C4D;")

        self.lbl_current_emotion = QLabel("Trạng thái: Đang theo dõi...")
        self.lbl_current_emotion.setStyleSheet("font-size: 13px; font-weight: 700; color: #183A34;")
        self.lbl_current_emotion.setWordWrap(True)

        self.lbl_emotion_note = QLabel("Theo dõi biểu cảm để nhận biết trạng thái tinh thần người cao tuổi.")
        self.lbl_emotion_note.setStyleSheet("font-size: 11px; color: #70817D;")
        self.lbl_emotion_note.setWordWrap(True)

        lay_card_emo.addWidget(lbl_e_title)
        lay_card_emo.addWidget(self.lbl_current_emotion)
        lay_card_emo.addWidget(self.lbl_emotion_note)
        top_cards.addWidget(card_emo, 1)

        left_layout.addLayout(top_cards)

        # Video Viewport Container — KeepAspectRatio, dark background
        self.video_container = QFrame()
        self.video_container.setObjectName("VideoContainer")
        lay_vid = QVBoxLayout(self.video_container)
        lay_vid.setContentsMargins(2, 2, 2, 2)

        self.video_display = VideoDisplayWidget()
        lay_vid.addWidget(self.video_display)

        left_layout.addWidget(self.video_container, 1)

        # Bottom Bar under Video: Stacked Widget (Live Guidance VS Video Playback Controls)
        self.stack_under_video = QStackedWidget()
        self.stack_under_video.setMaximumHeight(60)

        # Page 0: Live Exercise Guidance Subtitle Card
        card_guidance = QFrame()
        card_guidance.setObjectName("CardGuidance")
        card_guidance.setStyleSheet(
            "QFrame#CardGuidance { background-color: #EAF0E2; border: 1.5px solid #D8E3D0; border-radius: 8px; }"
            "QLabel { border: none; background: transparent; }"
        )
        lay_g = QHBoxLayout(card_guidance)
        lay_g.setContentsMargins(8, 4, 8, 4)
        self.banner_guidance = QLabel("💡 Hướng dẫn tư thế: Đứng trước camera, thẳng người và giữ khoảng cách 1.5m - 2.5m.")
        self.banner_guidance.setStyleSheet("font-size: 12px; font-weight: 600; color: #285C4D; border: none; background: transparent;")
        self.banner_guidance.setWordWrap(True)
        lay_g.addWidget(self.banner_guidance)
        self.stack_under_video.addWidget(card_guidance)

        left_layout.addWidget(self.stack_under_video)

        splitter.addWidget(left_widget)

        # ==================== RIGHT COLUMN: REHABILITATION CONTROLS (~25-30%) ====================
        right_scroll = QScrollArea()
        right_scroll.setWidgetResizable(True)
        right_scroll.setFrameShape(QFrame.NoFrame)
        right_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        right_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        right_scroll.setStyleSheet("QScrollArea { background-color: transparent; border: none; }")

        right_widget = QWidget()
        right_layout = QVBoxLayout(right_widget)
        right_layout.setContentsMargins(4, 4, 8, 4)
        right_layout.setSpacing(8)

        # Stacked Right Panel: Page 0 = Rehabilitation Controls, Page 1 = Video Test Evaluation
        self.stack_right_panel = QStackedWidget()

        # ---------- PANEL 0: LIVE REHABILITATION & SAFETY CONTROLS ----------
        panel_rehab = QWidget()
        lay_p_rehab = QVBoxLayout(panel_rehab)
        lay_p_rehab.setContentsMargins(0, 0, 0, 0)
        lay_p_rehab.setSpacing(8)

        # 1. Exercise Selection Card
        card_ex = QFrame()
        card_ex.setProperty("class", "ui-card-highlight")
        lay_card_ex = QVBoxLayout(card_ex)
        lay_card_ex.setContentsMargins(10, 10, 10, 10)
        lay_card_ex.setSpacing(6)

        lbl_ex_title = QLabel("🏋️ Chọn bài tập phục hồi")
        lbl_ex_title.setStyleSheet("font-size: 13px; font-weight: 700; color: #285C4D;")
        lay_card_ex.addWidget(lbl_ex_title)

        # 16:00 Sequential Routine Button — word wrap enabled
        self.btn_start_routine = QPushButton("🔄 Bắt đầu chuỗi bài tập 16:00\n(10 vươn tay ➔ 10 đứng ngồi ➔ 15 đi bộ)")
        self.btn_start_routine.setStyleSheet(
            "QPushButton { background-color: #356C60; color: #FFFFFF; font-weight: 700; font-size: 12px; "
            "border: none; border-radius: 8px; padding: 8px 10px; min-height: 40px; } "
            "QPushButton:hover { background-color: #28574D; }"
        )
        self.btn_start_routine.clicked.connect(self._on_start_routine_mode)
        lay_card_ex.addWidget(self.btn_start_routine)

        self.lbl_routine_status = QLabel("")
        self.lbl_routine_status.setStyleSheet("font-size: 11px; font-weight: 600; color: #356C60; padding: 1px 2px;")
        self.lbl_routine_status.setWordWrap(True)
        lay_card_ex.addWidget(self.lbl_routine_status)

        # Individual Exercise Selection Buttons
        self.btn_ex_arm = QPushButton("🏋️ 1. Nâng tay qua đầu\n     (Arm Raise) • 10 lần")
        self.btn_ex_arm.setStyleSheet(EX_ACTIVE_STYLE)
        self.btn_ex_arm.clicked.connect(lambda: self._select_exercise("arm_raise"))

        self.btn_ex_sit = QPushButton("🦵 2. Đứng lên ngồi xuống\n     (Sit-to-Stand) • 10 lần")
        self.btn_ex_sit.setStyleSheet(EX_INACTIVE_STYLE)
        self.btn_ex_sit.clicked.connect(lambda: self._select_exercise("sit_to_stand"))

        self.btn_ex_march = QPushButton("🚶 3. Đi bộ tại chỗ\n     (Marching) • 15 bước")
        self.btn_ex_march.setStyleSheet(EX_INACTIVE_STYLE)
        self.btn_ex_march.clicked.connect(lambda: self._select_exercise("marching"))

        self.btn_ex_circles = QPushButton("🔄 4. Xoay khớp vai\n     (Arm Circles) • 10 vòng")
        self.btn_ex_circles.setStyleSheet(EX_INACTIVE_STYLE)
        self.btn_ex_circles.clicked.connect(lambda: self._select_exercise("arm_circles"))

        self.btn_ex_bend = QPushButton("🤸 5. Nghiêng người giãn cơ\n     (Side Bend) • 8 lần")
        self.btn_ex_bend.setStyleSheet(EX_INACTIVE_STYLE)
        self.btn_ex_bend.clicked.connect(lambda: self._select_exercise("side_bend"))

        lay_card_ex.addWidget(self.btn_ex_arm)
        lay_card_ex.addWidget(self.btn_ex_sit)
        lay_card_ex.addWidget(self.btn_ex_march)
        lay_card_ex.addWidget(self.btn_ex_circles)
        lay_card_ex.addWidget(self.btn_ex_bend)

        # Controls row: Start & Stop & Pause (Row 1)
        ctrl_row_1 = QHBoxLayout()
        ctrl_row_1.setSpacing(6)

        self.btn_start = QPushButton("▶️ Bắt đầu tập")
        self.btn_start.setObjectName("BtnStart")
        self.btn_start.setStyleSheet(BTN_START_STYLE)
        self.btn_start.clicked.connect(self._on_start_exercise)

        self.btn_stop = QPushButton("⏹️ Dừng")
        self.btn_stop.setObjectName("BtnStop")
        self.btn_stop.setStyleSheet(BTN_STOP_STYLE)
        self.btn_stop.setEnabled(False)
        self.btn_stop.clicked.connect(self._on_stop_exercise)

        ctrl_row_1.addWidget(self.btn_start, 1)
        ctrl_row_1.addWidget(self.btn_stop, 1)
        self.btn_pause = QPushButton('⏸ Tạm dừng')
        self.btn_pause.setStyleSheet(BTN_RESET_STYLE)
        self.btn_pause.setEnabled(False)
        self.btn_pause.clicked.connect(self._on_pause_exercise)
        ctrl_row_1.addWidget(self.btn_pause, 1)
        lay_card_ex.addLayout(ctrl_row_1)

        # Controls row: Reset (Row 2)
        self.btn_reset = QPushButton("🔄 Đặt lại bài tập")
        self.btn_reset.setObjectName("BtnReset")
        self.btn_reset.setStyleSheet(BTN_RESET_STYLE)
        self.btn_reset.clicked.connect(self._on_reset_exercise)
        lay_card_ex.addWidget(self.btn_reset)

        lay_p_rehab.addWidget(card_ex)

        # 2. Live Evaluation & Scoring Card
        card_eval = QFrame()
        card_eval.setProperty("class", "ui-card")
        lay_eval = QVBoxLayout(card_eval)
        lay_eval.setContentsMargins(10, 10, 10, 10)
        lay_eval.setSpacing(6)

        lbl_eval_title = QLabel("📊 Đánh giá tiến độ")
        lbl_eval_title.setStyleSheet("font-size: 13px; font-weight: 700; color: #183A34;")
        lay_eval.addWidget(lbl_eval_title)

        # Progress bar
        self.lbl_progress = QLabel("Tiến độ: 0 / 12 lần hoàn thành (0%)")
        self.lbl_progress.setStyleSheet("font-size: 12px; color: #183A34; font-weight: 600;")
        self.lbl_progress.setWordWrap(True)
        self.progress_bar = QProgressBar()
        self.progress_bar.setStyleSheet(
            "QProgressBar { background-color: #EDF2F7; border: 1px solid #DFE5DF; border-radius: 4px; text-align: center; color: #183A34; font-weight: bold; font-size: 11px; height: 16px; }"
            "QProgressBar::chunk { background-color: #285C4D; border-radius: 3px; }"
        )
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        lay_eval.addWidget(self.lbl_progress)
        lay_eval.addWidget(self.progress_bar)

        # Metrics row
        metrics_row = QHBoxLayout()
        metrics_row.setSpacing(4)
        self.lbl_correct_cnt = QLabel("✓ Đúng: 0")
        self.lbl_correct_cnt.setStyleSheet(get_badge_stylesheet("success", font_size=11, padding="2px 6px"))

        self.lbl_incorrect_cnt = QLabel("⚠️ Chưa chuẩn: 0")
        self.lbl_incorrect_cnt.setStyleSheet(get_badge_stylesheet("danger", font_size=11, padding="2px 6px"))

        self.lbl_score_val = QLabel("Điểm: --")
        self.lbl_score_val.setStyleSheet("font-size: 14px; font-weight: 800; color: #285C4D; padding: 2px 4px;")

        metrics_row.addWidget(self.lbl_correct_cnt)
        metrics_row.addWidget(self.lbl_incorrect_cnt)
        metrics_row.addStretch()
        metrics_row.addWidget(self.lbl_score_val)
        lay_eval.addLayout(metrics_row)

        # Show result report button
        btn_modal_report = QPushButton("📋 Xem báo cáo kết quả chi tiết")
        btn_modal_report.setObjectName("BtnReport")
        btn_modal_report.setStyleSheet(BTN_REPORT_STYLE)
        btn_modal_report.clicked.connect(self.router.show_exercise_result_modal)
        lay_eval.addWidget(btn_modal_report)

        lay_p_rehab.addWidget(card_eval)

        self.stack_right_panel.addWidget(panel_rehab)

        right_layout.addWidget(self.stack_right_panel)
        right_layout.addStretch()

        right_scroll.setWidget(right_widget)
        right_scroll.setMinimumWidth(380)
        right_scroll.setMaximumWidth(420)
        splitter.addWidget(right_scroll)

        # Splitter sizing: ưu tiên ~73% cho camera, ~27% cho phục hồi
        splitter.setSizes([780, 430])
        splitter.setStretchFactor(0, 73)
        splitter.setStretchFactor(1, 27)
        self.rehab_splitter = splitter

        self.unified_db = UnifiedDatabaseManager()
        self.rehab_session_adapter = RehabSessionAdapter(self.unified_db)
        self._synced_rehab_reps = 0
        self._active_db_session_id: Optional[str] = None
        self.today_todo_widget = TodayTodoWidget(self.unified_db, patient_id="patient_001", parent=self)
        self.cognitive_games_widget = CognitiveGamesWidget(patient_id="patient_001", parent=self)

        self.medication_tabs = QTabWidget()
        self.medication_tabs.setObjectName("MainWorkspaceTabs")
        self.medication_tabs.tabBar().hide()
        self.medication_tabs.currentChanged.connect(
            lambda index: [button.setChecked(i == index) for i, button in enumerate(self._navigation_buttons)])
        self.medication_tabs.addTab(self.today_todo_widget, "📋 Việc cần làm hôm nay")
        self.medication_tabs.addTab(splitter, "🏋️ Nhận diện và Phục hồi")
        self.medication_tabs.addTab(self.cognitive_games_widget, "🧠 Trò chơi trí nhớ và Thư giãn")

        self.today_todo_widget.open_games_requested.connect(lambda: self.medication_tabs.setCurrentWidget(self.cognitive_games_widget))
        self.today_todo_widget.start_routine_requested.connect(self._on_start_routine_mode)
        self.medication_panel = None
        root_layout.addWidget(self.medication_tabs, 1)
        self.local_voice_controller = None
        self.voice_dispatcher = None
        from apps.desktop.local_voice_widget import LocalVoiceWidget
        self.local_voice_widget = LocalVoiceWidget(parent=self, activate=self._enable_local_voice)
        root_layout.addWidget(self.local_voice_widget)
        self.medication_tabs.currentChanged.connect(self._invalidate_voice_context)
        import os
        if os.getenv('VOICE_AI_ENABLED', 'false').lower() == 'true':
            self._enable_local_voice()

        # 4. BOTTOM STATUS BAR
        self.status_bar_lbl = QLabel(f"Thông báo lúc {datetime.now().strftime('%H:%M:%S')}: Nhắc thuốc khi ứng dụng đang mở")
        self.status_bar_lbl.setObjectName("BottomStatusBar")
        self.status_bar_lbl.setWordWrap(True)
        root_layout.addWidget(self.status_bar_lbl)

    def _process_video_frame(self, frame: np.ndarray) -> np.ndarray:
        """
        Master Video Processing Callback attached to CameraManager.
        Directly executes Face Recognition, Emotion, or Rehabilitation pipelines
        without any camera conflicts.
        """
        if self.current_source_mode != "camera":
            return frame

        mode = self.camera_manager.get_mode()
        if mode == "registration":
            return frame

        is_cam_hw = self.camera_manager.is_hardware_camera()
        capture_pts = getattr(self.camera_manager, "latest_capture_pts", time.monotonic())
        is_cam_hw = is_cam_hw and getattr(self.camera_manager, "latest_capture_valid", False)

        if mode == "rehabilitation":
            # 1. Real-time Emotion Recognition on clean frame
            emo_res = self.router.emotion_adapter.process_frame(frame)
            self._record_camera_emotion(frame, emo_res, is_cam_hw)

            # 2. Full Body Pose, Exercise State Machine, Form HUD
            annotated_frame, metrics = self.router.rehab_adapter.process_frame(frame)

            # 3. Overlay live emotion badge at top right
            annotated_frame = self.router.emotion_adapter.draw_emotion_badge(
                annotated_frame, emo_res["emotion_vi"], emo_res["confidence"]
            )
            return self._draw_medication_banner(annotated_frame)
        else:
            # 1. Real-time Emotion Recognition on clean frame
            emo_res = self.router.emotion_adapter.process_frame(frame)
            self._record_camera_emotion(frame, emo_res, is_cam_hw)

            # 2. Autonomous Face Recognition BBox & Tracking
            annotated_frame = self.router.face_adapter.draw_tracking_overlay(frame)

            # 3. Render Emotion Badge
            annotated_frame = self.router.emotion_adapter.draw_emotion_badge(
                annotated_frame, emo_res["emotion_vi"], emo_res["confidence"]
            )
            return self._draw_medication_banner(annotated_frame)

    def _draw_medication_banner(self, frame: np.ndarray) -> np.ndarray:
        """While a medication window is open the video says so (and what the camera sees) on top."""
        try:
            from shared.ai_runtime.app_hooks import get_runtime
            from shared.ai_runtime.med_banner import banner_lines, draw_banner

            runtime = get_runtime()
            if runtime is None or not hasattr(runtime, "medication_view"):
                return frame
            pid = getattr(self, "_active_resolved_pid", None) or getattr(self.today_todo_widget, "patient_id", None)
            active, policy, live = runtime.medication_view(pid)
            return draw_banner(frame, banner_lines(self.unified_db, pid, active, policy, live))
        except Exception:
            logger.debug("medication banner failed", exc_info=True)
            return frame

    def _record_camera_emotion(self, frame: np.ndarray, result: Dict[str, Any], hardware_camera: bool) -> None:
        """Sample fresh camera results, never a stale JSON result or a test video."""
        now_pts = time.monotonic()
        if not hardware_camera or now_pts - self._last_emotion_sample_pts < 1.0:
            return
        self._last_emotion_sample_pts = now_pts
        # Monitor mode already recognised this very frame in draw_tracking_overlay: reuse it (running the
        # recognizer twice on one frame skewed the identity tracking). Rehab mode has no face overlay: check here.
        if self.camera_manager.get_mode() == "rehabilitation":
            face = self.router.face_adapter.recognize_face(frame)
        else:
            face = self.router.face_adapter.get_latest_recognition()
        if face.get('status') != 'recognized' or not face.get('patient_id'):
            return
        patient_id = self._resolve_patient_id(face['patient_id'])
        label = result.get('dominant_emotion') if result.get('face_detected') else None
        try:
            self.emotion_summary_service.record(patient_id, label)
        except Exception:
            logger.exception('Failed recording camera emotion summary')

    def _on_frame_ready(self, q_img: QImage) -> None:
        """Render processed frame onto VideoDisplayWidget with zero resize loops."""
        if self.current_source_mode == "camera" and not q_img.isNull():
            pix = QPixmap.fromImage(q_img)
            self.video_display.set_frame_pixmap(pix)

    def resizeEvent(self, event) -> None:
        """Adaptive responsive layout for wide vs narrow screens."""
        super().resizeEvent(event)
        if hasattr(self, "rehab_splitter") and self.rehab_splitter:
            if self.width() < 960:
                if self.rehab_splitter.orientation() != Qt.Vertical:
                    self.rehab_splitter.setOrientation(Qt.Vertical)
                    self.rehab_splitter.setSizes([420, 300])
            else:
                if self.rehab_splitter.orientation() != Qt.Horizontal:
                    self.rehab_splitter.setOrientation(Qt.Horizontal)
                    w = self.width()
                    cam_w = int((w - 256) * 0.64)
                    ctrl_w = max(380, w - 256 - cam_w)
                    self.rehab_splitter.setSizes([cam_w, ctrl_w])

    # ==================== LIVE UI ACTIONS & LOGIC ====================
    def _load_rehab_plans_from_db(self, patient_id: Optional[str] = None) -> None:
        """
        Dynamically query active rehabilitation plans for active patient from unified DB.
        Updates routine targets, button titles, and scheduled reminder times in real-time.
        """
        try:
            pid = self._resolve_patient_id(patient_id or self.current_patient_id)
            with self.unified_db.connect() as conn:
                rows = conn.fetchall(
                    """
                    SELECT plan_id, exercise_id, exercise_name, target_count, local_reminder_time, version
                    FROM rehab_plans
                    WHERE patient_id = ? AND is_active = 1
                    ORDER BY plan_id ASC
                    """,
                    (pid,),
                )
                if not rows:
                    from features.exercise_rehab.rehab_session_adapter import ensure_default_rehab_plans
                    rows = ensure_default_rehab_plans(conn, pid)
                    conn.commit()

            # Generate signature to detect changes
            sig = f"{len(rows)}_" + "_".join(
                f"{r.get('plan_id', '')}:{r.get('version', 1)}:{r.get('target_count', 10)}:{r.get('local_reminder_time', '16:00')}"
                for r in rows
            )
            if sig == getattr(self, "_last_rehab_plans_signature", ""):
                return  # No changes

            self._last_rehab_plans_signature = sig
            self.rehab_plans = {r["exercise_id"]: dict(r) for r in rows if "exercise_id" in r}

            arm_t = self.rehab_plans.get("arm_raise", {}).get("target_count", 10)
            sit_t = self.rehab_plans.get("sit_to_stand", {}).get("target_count", 10)
            march_t = self.rehab_plans.get("marching", {}).get("target_count", 15)

            # Determine primary reminder time (use first available or default 16:00)
            rem_time = (
                self.rehab_plans.get("arm_raise", {}).get("local_reminder_time")
                or self.rehab_plans.get("sit_to_stand", {}).get("local_reminder_time")
                or self.rehab_plans.get("marching", {}).get("local_reminder_time")
                or "16:00"
            )
            self.routine_time = rem_time

            # Update routine sequence steps
            self.routine_steps = [
                {"id": "arm_raise", "name": "Nâng tay qua đầu (Vươn vai)", "target": arm_t},
                {"id": "sit_to_stand", "name": "Đứng lên ngồi xuống (Co duỗi chân)", "target": sit_t},
                {"id": "marching", "name": "Đi bộ tại chỗ (Nâng cao đùi)", "target": march_t},
            ]

            # Update button labels dynamically
            if hasattr(self, "btn_start_routine") and self.btn_start_routine:
                self.btn_start_routine.setText(
                    f"🔄 Bắt đầu chuỗi bài tập {rem_time}\n({arm_t} vươn tay ➔ {sit_t} đứng ngồi ➔ {march_t} đi bộ)"
                )
            if hasattr(self, "btn_ex_arm") and self.btn_ex_arm:
                self.btn_ex_arm.setText(f"🏋️ 1. Nâng tay qua đầu\n     (Arm Raise) • {arm_t} lần")
            if hasattr(self, "btn_ex_sit") and self.btn_ex_sit:
                self.btn_ex_sit.setText(f"🦵 2. Đứng lên ngồi xuống\n     (Sit-to-Stand) • {sit_t} lần")
            if hasattr(self, "btn_ex_march") and self.btn_ex_march:
                self.btn_ex_march.setText(f"🚶 3. Đi bộ tại chỗ\n     (Marching) • {march_t} bước")

            # Update active tracker's target reps
            active_target = self.rehab_plans.get(self.active_exercise_id, {}).get("target_count", 10)
            if hasattr(self, "router") and hasattr(self.router, "rehab_adapter"):
                self.router.rehab_adapter.set_target_reps(active_target)

            # Refresh today todo widget
            if hasattr(self, "today_todo_widget"):
                self.today_todo_widget.refresh_data()

            logger.info(
                "Loaded & synchronized rehab plans for %s: arm=%d, sit=%d, march=%d, time=%s",
                pid, arm_t, sit_t, march_t, rem_time
            )
        except Exception as e:
            logger.warning("Error loading rehab plans from DB: %s", e)

    def _select_exercise(self, exercise_id: str) -> None:
        """Highlight selected exercise button and configure target repetitions."""
        self._invalidate_voice_context()
        self.active_exercise_id = exercise_id
        buttons = {
            "arm_raise": self.btn_ex_arm,
            "sit_to_stand": self.btn_ex_sit,
            "marching": self.btn_ex_march,
            "arm_circles": self.btn_ex_circles,
            "side_bend": self.btn_ex_bend,
        }
        for ex_id, btn in buttons.items():
            if ex_id == exercise_id:
                btn.setStyleSheet(EX_ACTIVE_STYLE)
            else:
                btn.setStyleSheet(EX_INACTIVE_STYLE)

        target = self.rehab_plans.get(exercise_id, {}).get("target_count")
        if target is None:
            target = next((s["target"] for s in self.routine_steps if s["id"] == exercise_id),
                          EXERCISES.get(exercise_id, {}).get("target_reps", 10))

        self.router.rehab_adapter.select_exercise(exercise_id, target_reps=target)
        self.banner_guidance.setText(f"💡 Hướng dẫn tư thế: {EXERCISES.get(exercise_id, {}).get('instructions', '')}")
        self.update_dashboard_ui()

    def _on_start_routine_mode(self) -> None:
        """Start sequential rehabilitation routine: arm raises -> sit-to-stands -> marching steps."""
        self.medication_tabs.setCurrentIndex(1)  # Switch to Rehab tab
        self.is_routine_mode = True
        self.routine_step_idx = 0
        step = self.routine_steps[0]
        self.lbl_routine_status.setText(f"🔄 Đang tập Bước 1/3: {step['name']} ({step['target']} lần)")
        self._select_exercise(step["id"])
        self._on_start_exercise()
        self.show_status_message(f"Bắt đầu chuỗi bài tập {self.routine_time}! Bước 1/3: {step['name']} ({step['target']} lần)")

    def _complete_entire_routine(self) -> None:
        """Mark today's routine session completed, notify portals, and celebrate with user."""
        self.is_routine_mode = False
        self.lbl_routine_status.setText(f"🏆 XUẤT SẮC: ĐÃ HOÀN THÀNH TOÀN BỘ CHUỖI BÀI TẬP {self.routine_time}!")
        self.router.stop_exercise()
        self.btn_start.setEnabled(True)
        self.btn_stop.setEnabled(False)
        pid = self._resolve_patient_id(self.current_patient_id)

        arm_t = next((s["target"] for s in self.routine_steps if s["id"] == "arm_raise"), 10)
        sit_t = next((s["target"] for s in self.routine_steps if s["id"] == "sit_to_stand"), 10)
        march_t = next((s["target"] for s in self.routine_steps if s["id"] == "marching"), 15)
        total_t = arm_t + sit_t + march_t

        try:
            today_date = date.today().isoformat()
            now_iso = datetime.now(timezone.utc).isoformat()
            routine_notes = f"Hoàn thành trọn vẹn chuỗi bài tập {self.routine_time} ({arm_t} vươn tay, {sit_t} đứng ngồi, {march_t} đi bộ)"
            with self.unified_db.connect() as conn:
                conn.execute(
                    """
                    UPDATE rehab_sessions
                    SET status = 'camera_verified_completed', ended_at = ?, notes = ?
                    WHERE patient_id = ? AND local_date = ?
                    """,
                    (now_iso, routine_notes, pid, today_date),
                )
                insert_outbox_event(
                    conn=conn,
                    patient_id=pid,
                    event_type="rehab.session_updated",
                    aggregate_type="rehab_session",
                    aggregate_id=self._active_db_session_id or f"routine_{today_date}",
                    aggregate_version=total_t,
                    occurred_at=now_iso,
                    actor_id=pid,
                    source="camera_ai",
                    payload_minimal={
                        "patient_id": pid,
                        "session_id": self._active_db_session_id or f"routine_{today_date}",
                        "status": "camera_verified_completed",
                        "routine_completed": True,
                        "verified_count": total_t,
                        "target_count": total_t,
                        "notes": routine_notes,
                    },
                )
        except Exception as e:
            logger.warning("Error finalizing routine in DB: %s", e)

        if hasattr(self, "today_todo_widget"):
            self.today_todo_widget.refresh_data()

        QMessageBox.information(
            self,
            "🎉 Chúc Mừng Hoàn Thành Buổi Tập!",
            f"🎉 XUẤT SẮC!\n\n"
            f"Cụ đã hoàn thành toàn bộ chuỗi bài tập {self.routine_time} hôm nay:\n"
            f"  ✓ {arm_t} lần Nâng tay qua đầu (Vươn vai)\n"
            f"  ✓ {sit_t} lần Đứng lên ngồi xuống (Co duỗi chân)\n"
            f"  ✓ {march_t} bước Đi bộ tại chỗ (Nâng cao đùi)\n\n"
            f"Hệ thống đã tự động báo kết quả hoàn thành đến Người nhà và Bác sĩ!"
        )

    def _check_16h_reminder(self, now_dt: datetime) -> None:
        """Check scheduled rehab reminder according to configured routine_time (default 16:00)."""
        today_str = now_dt.strftime("%Y-%m-%d")
        rem_time = getattr(self, "routine_time", "16:00")
        try:
            r_hour, r_minute = [int(p) for p in rem_time.split(":")[:2]]
        except Exception:
            r_hour, r_minute = 16, 0

        # Trigger if current hour matches (and minute matches or within first 5 mins of reminder)
        is_time_to_remind = (now_dt.hour == r_hour and now_dt.minute == r_minute) or (now_dt.hour == r_hour and r_minute == 0)

        if is_time_to_remind and getattr(self, "_last_16h_alert_date", "") != today_str:
            is_completed = False
            try:
                pid = self._resolve_patient_id(self.current_patient_id)
                with self.unified_db.connect() as conn:
                    row = conn.fetchone(
                        "SELECT status FROM rehab_sessions WHERE patient_id = ? AND local_date = ? AND status = 'camera_verified_completed'",
                        (pid, today_str),
                    )
                    if row:
                        is_completed = True
            except Exception:
                pass

            if not is_completed:
                self._last_16h_alert_date = today_str
                if "--test" in sys.argv:
                    logger.info("Rehab reminder triggered in test mode (dialog suppressed).")
                    return

                arm_t = next((s["target"] for s in self.routine_steps if s["id"] == "arm_raise"), 10)
                sit_t = next((s["target"] for s in self.routine_steps if s["id"] == "sit_to_stand"), 10)
                march_t = next((s["target"] for s in self.routine_steps if s["id"] == "marching"), 15)

                reply = QMessageBox.question(
                    self,
                    f"⏰ {rem_time} — Giờ Tập Thể Dục Phục Hồi Chức Năng",
                    f"Đã đến {rem_time}!\n\n"
                    f"Chào cụ! Đã đến giờ tập thể dục phục hồi chức năng hôm nay theo kế hoạch người nhà đã đặt.\n"
                    f"Chuỗi bài tập gồm:\n"
                    f"  • Bước 1: {arm_t} lần Vươn tay qua đầu\n"
                    f"  • Bước 2: {sit_t} lần Đứng lên ngồi xuống\n"
                    f"  • Bước 3: {march_t} bước Đi bộ tại chỗ\n\n"
                    f"Camera sẽ tự đếm và báo cáo với Người nhà và Bác sĩ khi cụ tập xong.\n"
                    f"Cụ có muốn bắt đầu chuỗi bài tập ngay bây giờ không?",
                    QMessageBox.Yes | QMessageBox.No,
                    QMessageBox.Yes,
                )
                if reply == QMessageBox.Yes:
                    self._on_start_routine_mode()

    def _on_start_exercise(self, expected_plan=None) -> None:
        """Start rehabilitation exercise."""
        self._invalidate_voice_context()
        expected_plan = expected_plan if isinstance(expected_plan, dict) else None
        sess = None
        self._synced_rehab_reps = 0
        try:
            pid = self._resolve_patient_id(self.current_patient_id)
            sess = self.rehab_session_adapter.get_or_create_daily_session(pid, self.active_exercise_id, expected_plan=expected_plan)
            self._active_db_session_id = sess.get("session_id")
        except Exception as e:
            logger.warning("Could not initialize daily rehab DB session: %s", e)
            self._active_db_session_id = None
            if expected_plan is not None:
                raise
        target = self.rehab_plans.get(self.active_exercise_id, {}).get("target_count")
        if target is None:
            target = next((s["target"] for s in self.routine_steps if s["id"] == self.active_exercise_id), 10)
        if expected_plan is not None and sess:
            target = sess['target_count_snapshot']
        self.router.start_exercise(self.active_exercise_id, target_reps=target)
        self.btn_start.setEnabled(False)
        self.btn_stop.setEnabled(True)
        self.btn_pause.setEnabled(True)
        self.btn_pause.setText("⏸ Tạm dừng")

    def _on_pause_exercise(self) -> None:
        self._invalidate_voice_context()
        if not self.btn_stop.isEnabled():
            return
        if self.router.rehab_adapter.engine.is_paused:
            self.router.rehab_adapter.resume()
            self.btn_pause.setText('⏸ Tạm dừng')
        else:
            self.router.rehab_adapter.pause()
            self.btn_pause.setText('▶ Tiếp tục')

    def _on_stop_exercise(self) -> None:
        """Stop exercise and return to monitoring."""
        self._invalidate_voice_context()
        self.is_routine_mode = False
        if hasattr(self, "lbl_routine_status"):
            self.lbl_routine_status.setText("")
        self.router.stop_exercise()
        self.btn_start.setEnabled(True)
        self.btn_stop.setEnabled(False)
        self.btn_pause.setEnabled(False)
        self.router.show_exercise_result_modal()

    def _on_reset_exercise(self) -> None:
        """Reset exercise metrics."""
        self._invalidate_voice_context()
        self._synced_rehab_reps = 0
        self._active_db_session_id = None
        self.router.rehab_adapter.start_exercise(self.active_exercise_id)
        self.update_dashboard_ui()

    def _on_open_registration(self) -> None:
        """Open dedicated User Registration modal with safe analyzer pausing."""
        self._invalidate_voice_context()
        old_mode = self.camera_manager.get_mode()
        self.camera_manager.set_mode("registration")
        self.router.rehab_adapter.pause()
        self.show_status_message("Đang mở cửa sổ đăng ký — Tạm dừng bài tập & phân tích")
        try:
            dlg = RegistrationDialog(self.router.face_adapter, self)
            dlg.patient_registered.connect(self._on_patient_registered)
            dlg.exec_()
        finally:
            self.router.rehab_adapter.resume()
            self.camera_manager.set_mode(old_mode)
            self.show_status_message("Đã hoàn tất đăng ký — Tiếp tục giám sát")
            self.update_dashboard_ui()

    def _on_open_patients_manager(self) -> None:
        """Open Patient List & Management modal."""
        self.router.rehab_adapter.pause()
        try:
            dlg = PatientManagementDialog(self.router.face_adapter, self)
            dlg.exec_()
        finally:
            self.router.rehab_adapter.resume()
            self.update_dashboard_ui()

    def _on_patient_registered(self, profile: dict) -> None:
        """Callback triggered when a new user is enrolled and model retrained."""
        pid = profile.get("patient_id") or profile.get("user_id", "")
        name = profile.get("name", "")
        self._invalidate_voice_context()
        self.current_patient_id = pid
        self.current_patient_name = name
        self.lbl_header_patient.setText(f"👤 Đang theo dõi: {name} (Mã: {pid})")
        self.show_status_message(f"Đã đăng ký và huấn luyện nhận diện người dùng: {name} (Mã: {pid})")
        resolved_pid = self._resolve_patient_id(pid)
        self._load_rehab_plans_from_db(resolved_pid)
        if hasattr(self, "today_todo_widget"):
            self.today_todo_widget.set_patient(resolved_pid)
        if hasattr(self, "cognitive_games_widget"):
            self.cognitive_games_widget.set_patient(resolved_pid)
        self.update_dashboard_ui()

    def _enable_local_voice(self) -> None:
        """Prepare local STT; the microphone widget queues an explicit recording request."""
        if self.local_voice_controller is not None:
            return
        from features.voice_assistant.local_voice.config import VoiceConfig
        from features.voice_assistant.local_voice.controller import VoiceSessionController
        from features.voice_assistant.local_voice.dispatcher import CommandDispatcher
        try:
            self.voice_dispatcher = CommandDispatcher(self)
            self.local_voice_controller = VoiceSessionController(
                VoiceConfig.from_env(), self.voice_dispatcher.snapshot, self)
            self.local_voice_widget.attach(self.local_voice_controller, self.voice_dispatcher)
        except Exception as exc:
            logger.warning('Local voice unavailable: %s', exc)
            if self.local_voice_controller:
                self.local_voice_controller.shutdown()
            self.local_voice_controller = None
            self.local_voice_widget.controller = None
            self.local_voice_widget.update_state('off', 'Chưa chuẩn bị được nhận giọng nói. Hãy dùng các nút trên màn hình hoặc nhấn micro để thử lại.')

    def _invalidate_voice_context(self, *args) -> None:
        if getattr(self, 'voice_dispatcher', None):
            self.voice_dispatcher.invalidate()

    def _check_sadness_comfort(self):
        pid = getattr(self, "_active_resolved_pid", None)
        if not pid:
            return
        try:
            if getattr(self, "_comfort", None) is None:
                from features.music_care.comfort import SadnessComfort
                from features.voice_assistant.voice_ai.audio.tts_player import get_tts_coordinator

                self._comfort = SadnessComfort(self.unified_db, get_tts_coordinator().speak, self._play_comfort_music,
                                               honorific=self._elder_honorific(pid))
            self._comfort.check(pid)
        except Exception:
            logger.debug("comfort check failed", exc_info=True)

    def _elder_honorific(self, pid):
        try:
            from shared.ai_runtime.store import AICoreStore

            return (AICoreStore(self.unified_db).get_settings(pid, "medication") or {}).get("honorific") or "bác"
        except Exception:
            return "bác"

    def _play_comfort_music(self):
        """Prefer a track the family uploaded; otherwise the first relaxing track."""
        w = getattr(getattr(self, "cognitive_games_widget", None), "music_widget", None)
        if w is None or w.audio_service.is_playing:
            return False
        w._refresh_track_list()
        if w.combo_tracks.count() == 0:
            return False
        family = next((i for i in range(w.combo_tracks.count()) if str(w.combo_tracks.itemData(i)).startswith("family_")), 0)
        w.combo_tracks.setCurrentIndex(family)
        w._on_play_clicked()
        return w.audio_service.is_playing

    def _log_face_sighting(self, patient_id, who):
        """Face monitoring log (first/last seen per day, strangers); never breaks the UI loop."""
        if not patient_id:
            return
        try:
            if getattr(self, "_face_sighting_log", None) is None:
                from features.face_monitoring.sighting_log import FaceSightingLog

                self._face_sighting_log = FaceSightingLog(self.unified_db)
            self._face_sighting_log.record(patient_id, who)
        except Exception:
            logger.debug("face sighting log failed", exc_info=True)

    def _resolve_patient_id(self, r_id: str) -> str:
        """Resolve face recognition legacy id or camera patient id to canonical patient_id."""
        if not r_id:
            return "patient_001"
        r_str = str(r_id).strip()
        if r_str in ("1", "pat-001", "patient_001"):
            return "patient_001"
        if r_str in ("2", "pat-002", "patient_002"):
            return "patient_002"
        try:
            if hasattr(self, "unified_db") and self.unified_db:
                with self.unified_db.connect() as conn:
                    row = conn.fetchone("SELECT patient_id FROM legacy_patient_map WHERE legacy_id = ?", (r_str,))
                    if row and row.get("patient_id"):
                        return row["patient_id"]
        except Exception:
            pass
        return r_str if r_str.startswith("patient_") else f"patient_{r_str.zfill(3)}"

    def update_dashboard_ui(self) -> None:
        """Synchronize telemetry metrics into UI components."""
        if self.current_source_mode != "camera":
            return
        if getattr(self, "_is_updating_ui", False):
            return
        self._is_updating_ui = True
        try:
            self._do_update_dashboard_ui()
        finally:
            self._is_updating_ui = False

    def _do_update_dashboard_ui(self) -> None:
        # 1. Face Recognition Telemetry
        face_info = self.router.face_adapter.get_latest_recognition()
        f_status = face_info.get("status", "no_face")
        profile = {}
        if f_status == "recognized":
            r_name = face_info.get("name", "Người dùng")
            r_id = face_info.get("patient_id", "1")
            r_conf = face_info.get("confidence", 0.95)
            profile = face_info.get("profile") or {}
            age = profile.get("age", 70)
            gender = profile.get("gender", "Nam")
            self.lbl_face_status.setText(f"🟢 Đã nhận diện: {r_name} (Mã: {r_id}, {age}T - {gender})")
            self.lbl_face_status.setStyleSheet("font-size: 15px; font-weight: 700; color: #167D62;")
            self.lbl_face_sub.setText(f"Độ tin cậy: {r_conf * 100:.1f}% | Hồ sơ: Đã xác thực ✓")
            if str(self.current_patient_id) != str(r_id):
                self._invalidate_voice_context()
            self.current_patient_id = r_id
            self.current_patient_name = r_name
            self.lbl_header_patient.setText(f"👤 Đang theo dõi: {r_name} (Mã: {r_id})")
            self.router.update_recognized_user(r_id, r_name)
            resolved_pid = self._resolve_patient_id(r_id)
            self._log_face_sighting(resolved_pid, "patient")
            if getattr(self, "_active_resolved_pid", None) != resolved_pid:
                self._active_resolved_pid = resolved_pid
                self._load_rehab_plans_from_db(resolved_pid)
                if hasattr(self, "today_todo_widget"):
                    self.today_todo_widget.set_patient(resolved_pid)
                if hasattr(self, "cognitive_games_widget"):
                    self.cognitive_games_widget.set_patient(resolved_pid)
        elif f_status == "uncertain":
            r_name = face_info.get("name", "Đang xác minh...")
            self.lbl_face_status.setText(f"🟡 {r_name}")
            self.lbl_face_status.setStyleSheet("font-size: 15px; font-weight: 700; color: #A96300;")
            self.lbl_face_sub.setText("Vui lòng giữ góc nhìn ổn định hướng về camera để xác nhận danh tính.")
        elif f_status == "unknown":
            if getattr(self, "_previous_voice_face_status", None) != "unknown":
                self._invalidate_voice_context()
            self.lbl_face_status.setText("🟠 Phát hiện người lạ (Chưa đăng ký)")
            self.lbl_face_status.setStyleSheet("font-size: 15px; font-weight: 700; color: #A96300;")
            self.lbl_face_sub.setText("Chưa có hồ sơ trong CSDL. Bấm [➕ Đăng Ký Người Mới] để thêm thông tin.")
            self.lbl_header_patient.setText("👤 Đang theo dõi: Người lạ (Chưa đăng ký)")
            self._log_face_sighting(getattr(self, "_active_resolved_pid", None), "stranger")
        else:
            self.lbl_face_status.setText("⚪ Chưa phát hiện khuôn mặt")
            self.lbl_face_status.setStyleSheet("font-size: 15px; font-weight: 700; color: #667B74;")
            self.lbl_face_sub.setText("Vui lòng nhìn thẳng vào camera, hoặc bấm [➕ Đăng Ký Người Mới].")

        self._previous_voice_face_status = f_status

        # 2. Rehabilitation Metrics
        metrics = self.router.rehab_adapter.get_result()
        reps = metrics.get("repetitions", 0)
        target = metrics.get("target_reps", 10)
        correct = metrics.get("correct_reps", 0)
        incorrect = metrics.get("incorrect_reps", 0)
        score = metrics.get("score", 0.0)
        has_score = metrics.get("has_score", reps > 0)
        feedback = metrics.get("feedback", "Chuẩn bị")

        pct = min(100, int((reps / max(1, target)) * 100))
        self.progress_bar.setValue(pct)
        self.lbl_progress.setText(f"Tiến độ: {reps} / {target} lần hoàn thành ({pct}%)")

        self.lbl_correct_cnt.setText(f"✓ Đúng: {correct}")
        self.lbl_incorrect_cnt.setText(f"⚠️ Chưa chuẩn: {incorrect}")
        if reps == 0 or not has_score:
            self.lbl_score_val.setText("Điểm: --")
        else:
            self.lbl_score_val.setText(f"Điểm: {score:.1f}%")
        self.banner_guidance.setText(f"💡 Hướng dẫn tư thế: {feedback}")

        # Synchronize new camera-counted repetitions into unified DB & outbox
        last_synced = getattr(self, "_synced_rehab_reps", 0)
        if reps > last_synced and hasattr(self, "rehab_session_adapter"):
            try:
                pid = self._resolve_patient_id(self.current_patient_id)
                if not getattr(self, "_active_db_session_id", None):
                    sess = self.rehab_session_adapter.get_or_create_daily_session(pid, self.active_exercise_id)
                    self._active_db_session_id = sess.get("session_id")

                if self._active_db_session_id:
                    for rep_idx in range(last_synced + 1, reps + 1):
                        self.rehab_session_adapter.record_verified_rep(
                            self._active_db_session_id,
                            rep_number=rep_idx,
                            score=float(score) if score > 0 else 90.0,
                        )
                    self._synced_rehab_reps = reps
                    if hasattr(self, "today_todo_widget"):
                        self.today_todo_widget.refresh_data()
            except Exception as e:
                logger.warning("Error syncing camera rehab reps to DB: %s", e)

        # Check routine step transition if in routine mode
        if getattr(self, "is_routine_mode", False) and reps >= target:
            if self.routine_step_idx == 0:
                self.routine_step_idx = 1
                next_step = self.routine_steps[1]
                prev_target = self.routine_steps[0]["target"]
                self.lbl_routine_status.setText(
                    f"🎉 Xong Bước 1 ({prev_target} Vươn tay)! ➔ Đang tập Bước 2/3: {next_step['name']} ({next_step['target']} lần)"
                )
                self._select_exercise(next_step["id"])
                self._on_start_exercise()
                self.show_status_message(f"🎉 Hoàn thành Bước 1! Bắt đầu Bước 2/3: {next_step['name']} ({next_step['target']} lần)")
            elif self.routine_step_idx == 1:
                self.routine_step_idx = 2
                next_step = self.routine_steps[2]
                prev_target = self.routine_steps[1]["target"]
                self.lbl_routine_status.setText(
                    f"🎉 Xong Bước 2 ({prev_target} Đứng ngồi)! ➔ Đang tập Bước 3/3: {next_step['name']} ({next_step['target']} bước)"
                )
                self._select_exercise(next_step["id"])
                self._on_start_exercise()
                self.show_status_message(f"🎉 Hoàn thành Bước 2! Bắt đầu Bước 3/3: {next_step['name']} ({next_step['target']} bước)")
            elif self.routine_step_idx == 2:
                self._complete_entire_routine()

        # 3. Emotion Metrics
        emo = self.router.emotion_adapter.get_result()
        self.lbl_current_emotion.setText(f"Trạng thái: {emo['emotion_vi']} ({emo['confidence'] * 100:.1f}%)")

    def _on_timer_tick(self) -> None:
        """Periodic real-time UI refresh every 1 second."""
        now_dt = datetime.now()
        self.lbl_clock.setText(now_dt.strftime("%H:%M:%S | %d/%m/%Y"))
        self._refresh_camera_badge()

        # Live sync rehab plans configured by caregiver from web portal every 2 seconds
        now_ts = now_dt.timestamp()
        if now_ts - getattr(self, "_last_rehab_check_time", 0.0) >= 2.0:
            self._last_rehab_check_time = now_ts
            self._load_rehab_plans_from_db()

        self._check_16h_reminder(now_dt)
        if self.current_source_mode == "camera":
            self.update_dashboard_ui()

    def _refresh_camera_badge(self) -> None:
        """The badge was set once at start-up, before the camera had opened, so it kept saying "Giả lập"."""
        cm = self.camera_manager
        is_hw = bool(cm.is_hardware_camera() and getattr(cm, "latest_capture_valid", False))
        if getattr(self, "_cam_badge_hw", None) is is_hw:
            return
        self._cam_badge_hw = is_hw
        self.lbl_cam_badge.setText("● Camera: Phần cứng" if is_hw else "● Camera: Giả lập")
        self.lbl_cam_badge.setStyleSheet(get_badge_stylesheet("success" if is_hw else "warning", font_size=12,
                                                              padding="4px 10px", rounded=10))

    def show_status_message(self, message: str) -> None:
        """Display status notification in bottom bar."""
        now_dt = datetime.now()
        self.status_bar_lbl.setText(f"Thông báo lúc {now_dt.strftime('%H:%M:%S')}: {message}")

    def closeEvent(self, event) -> None:
        """Clean shutdown of camera engine and video players."""
        if getattr(self, "local_voice_widget", None):
            self.local_voice_widget.shutdown()
        if getattr(self, "local_voice_controller", None):
            self.local_voice_controller.shutdown()
        self.timer.stop()
        if getattr(self, "ai_core", None) is not None:
            from shared.ai_runtime.app_hooks import stop_ai_core
            stop_ai_core(self.ai_core)
            self.ai_core = None
        if getattr(self, "medication_panel", None) is not None:
            self.medication_panel.shutdown()
        self.camera_manager.stop_camera()
        self.camera_manager.unregister_processor(self._process_video_frame)
        event.accept()
