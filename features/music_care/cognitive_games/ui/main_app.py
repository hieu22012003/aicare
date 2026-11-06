"""
Main Desktop Application for Cognitive Memory Games & Relaxation Module.
Integrates:
- Card Matching, Sequence Recall, and Relaxing Music.
- Simulated Patient identity adapter with camera fallback toggle (Criterion 7).
- Multi-role simulation switcher (Patient, Caregiver, Clinician) enforcing Criterion 6.
- Role-based progress dashboard (P4) with non-diagnostic safeguards.
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path
from typing import Optional

# Ensure project root is in sys.path when script is executed directly
PROJECT_ROOT = Path(__file__).resolve().parents[4]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QFont, QIcon
from PyQt5.QtWidgets import (
    QAction,
    QApplication,
    QComboBox,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QStackedWidget,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from features.music_care.cognitive_games.adapters.care_team_repository import SqlCareTeamRepository
from features.music_care.cognitive_games.adapters.game_repository import SqlGameRepository
from features.music_care.cognitive_games.adapters.identity_adapter import MockPatientIdentityAdapter
from features.music_care.cognitive_games.adapters.music_repository import SqlMusicRepository
from features.music_care.cognitive_games.database.connection import DatabaseManager
from features.music_care.cognitive_games.database.seed_data import seed_cognitive_games_database
from features.music_care.cognitive_games.domain.models import DifficultyLevel
from features.music_care.cognitive_games.services.audio_service import AudioService
from features.music_care.cognitive_games.services.cognitive_service import CognitiveService
from features.music_care.cognitive_games.ui.card_matching_widget import CardMatchingWidget
from features.music_care.cognitive_games.ui.music_player_widget import MusicPlayerWidget
from features.music_care.cognitive_games.ui.progress_dashboard_widget import ProgressDashboardWidget
from features.music_care.cognitive_games.ui.sequence_recall_widget import SequenceRecallWidget

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("CognitiveMainApp")


class MainWindow(QMainWindow):
    def __init__(self, db_manager: DatabaseManager) -> None:
        super().__init__()
        self.db = db_manager

        # Initialize Repositories and Adapters
        self.care_team_repo = SqlCareTeamRepository(self.db)
        self.game_repo = SqlGameRepository(self.db)
        self.music_repo = SqlMusicRepository(self.db)
        self.identity_adapter = MockPatientIdentityAdapter(initial_patient_id="pat-001", camera_active=True)

        # Initialize Services
        self.cognitive_service = CognitiveService(
            game_repo=self.game_repo,
            care_team_repo=self.care_team_repo,
            identity_adapter=self.identity_adapter,
        )
        self.audio_service = AudioService(self.music_repo)

        # Current Context
        self.current_patient_id = "pat-001"
        self.current_actor_id = "pat-001"
        self.current_actor_role = "patient"

        self._init_window()
        self._init_ui()
        self._on_patient_changed()

    def _init_window(self) -> None:
        self.setWindowTitle("Trợ Lý Rèn Luyện Trí Nhớ & Thư Giãn Cho Người Cao Tuổi")
        self.resize(1180, 840)
        self.setStyleSheet(
            """
            QMainWindow {
                background-color: #F8F9FA;
            }
            QLabel {
                font-family: Arial, sans-serif;
            }
            QTabWidget::pane {
                border: 1px solid #CFD8DC;
                background-color: #FFFFFF;
                border-radius: 8px;
            }
            QTabBar::tab {
                background: #ECEFF1;
                border: 1px solid #CFD8DC;
                padding: 14px 28px;
                font-size: 15pt;
                font-weight: bold;
                color: #37474F;
                border-top-left-radius: 8px;
                border-top-right-radius: 8px;
            }
            QTabBar::tab:selected {
                background: #FFFFFF;
                border-bottom: 3px solid #1E88E5;
                color: #1565C0;
            }
            """
        )

    def _init_ui(self) -> None:
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)
        main_layout.setContentsMargins(16, 16, 16, 16)
        main_layout.setSpacing(12)

        # 1. Top Simulation Control Bar
        top_bar = self._build_top_simulation_bar()
        main_layout.addWidget(top_bar)

        # 2. Main Stacked Widget (to switch between Home Tabs and Fullscreen Game Mode)
        self.main_stack = QStackedWidget()
        main_layout.addWidget(self.main_stack, 1)

        # View 0: Tabs (Today Activities + Progress Dashboard)
        self.tabs_container = QTabWidget()
        self.tabs_container.setFont(QFont("Arial", 14))

        self.tab_activities = self._build_activities_tab()
        self.tabs_container.addTab(self.tab_activities, "🎮 Hoạt Động Hôm Nay")

        self.dashboard_widget = ProgressDashboardWidget(self.cognitive_service)
        self.tabs_container.addTab(self.dashboard_widget, "📊 Tiến Trình & Lịch Sử")
        self.tabs_container.currentChanged.connect(self._on_tab_changed)

        self.main_stack.addWidget(self.tabs_container)

        # View 1: Card Matching Widget
        self.card_widget = CardMatchingWidget(self.cognitive_service, on_back=self._return_to_main_menu)
        self.main_stack.addWidget(self.card_widget)

        # View 2: Sequence Recall Widget
        self.sequence_widget = SequenceRecallWidget(self.cognitive_service, on_back=self._return_to_main_menu)
        self.main_stack.addWidget(self.sequence_widget)

    def _build_top_simulation_bar(self) -> QWidget:
        bar = QFrame()
        bar.setStyleSheet(
            """
            QFrame {
                background-color: #ECEFF1;
                border: 1px solid #B0BEC5;
                border-radius: 8px;
                padding: 6px;
            }
            """
        )
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(12, 6, 12, 6)
        layout.setSpacing(14)

        # Patient Selector
        lbl_p = QLabel("👤 Bệnh nhân:")
        lbl_p.setFont(QFont("Arial", 13, QFont.Bold))
        layout.addWidget(lbl_p)

        self.combo_patient = QComboBox()
        self.combo_patient.setFont(QFont("Arial", 13))
        self.combo_patient.addItem("Nguyễn Văn An (pat-001)", "pat-001")
        self.combo_patient.addItem("Trần Thị Mai (pat-002)", "pat-002")
        self.combo_patient.currentIndexChanged.connect(self._on_patient_changed)
        layout.addWidget(self.combo_patient)

        # Actor Role Selector (Demonstrating Criterion 6)
        lbl_role = QLabel("🎭 Vai trò thao tác:")
        lbl_role.setFont(QFont("Arial", 13, QFont.Bold))
        layout.addWidget(lbl_role)

        self.combo_actor = QComboBox()
        self.combo_actor.setFont(QFont("Arial", 13))
        self.combo_actor.addItem("Bệnh nhân tự dùng (pat-001)", ("pat-001", "patient"))
        self.combo_actor.addItem("Người nhà hợp lệ (cg-001: Nguyễn Thị Bình)", ("cg-001", "caregiver"))
        self.combo_actor.addItem("Người nhà không quyền (cg-002)", ("cg-002", "caregiver"))
        self.combo_actor.addItem("Bác sĩ có quyền (doc-001: BS. Lê Văn Cường)", ("doc-001", "clinician"))
        self.combo_actor.addItem("Bác sĩ hết hạn (doc-002: BS. Phạm Thị Dung)", ("doc-002", "clinician"))
        self.combo_actor.currentIndexChanged.connect(self._on_actor_changed)
        layout.addWidget(self.combo_actor)

        # Camera status toggle (Criterion 7)
        self.btn_camera = QPushButton("📷 Camera: Đang BẬT")
        self.btn_camera.setFont(QFont("Arial", 12, QFont.Bold))
        self.btn_camera.setCheckable(True)
        self.btn_camera.setChecked(True)
        self.btn_camera.setStyleSheet(
            """
            QPushButton {
                background-color: #4CAF50;
                color: #FFFFFF;
                border-radius: 6px;
                padding: 6px 12px;
            }
            QPushButton:checked { background-color: #35634D; }
            QPushButton:!checked { background-color: #757575; }
            """
        )
        self.btn_camera.clicked.connect(self._on_camera_toggled)
        layout.addWidget(self.btn_camera)

        layout.addStretch()

        # Reload button
        btn_reload = QPushButton("🔄 Tải Lại")
        btn_reload.setFont(QFont("Arial", 12, QFont.Bold))
        btn_reload.setStyleSheet("background-color: #1976D2; color: #FFF; border-radius: 6px; padding: 6px 14px;")
        btn_reload.clicked.connect(self._on_reload_clicked)
        layout.addWidget(btn_reload)

        return bar

    def _build_activities_tab(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(18)

        # Welcome Banner
        self.welcome_label = QLabel("Chào mừng Bác Nguyễn Văn An tham gia các hoạt động hôm nay!")
        self.welcome_label.setFont(QFont("Arial", 18, QFont.Bold))
        self.welcome_label.setStyleSheet("color: #0D47A1;")
        layout.addWidget(self.welcome_label)

        motto = QLabel("“Luyện tập và giải trí phù hợp với bạn.” Không tạo áp lực, vui vẻ mỗi ngày.")
        motto.setFont(QFont("Arial", 14))
        motto.setStyleSheet("color: #455A64;")
        layout.addWidget(motto)

        # Two Main Memory Games Box
        games_row = QHBoxLayout()
        games_row.setSpacing(20)

        # 1. Card Matching Launcher Card
        card_box = QGroupBox("🧩 Lật Hình Ghép Cặp")
        card_box.setFont(QFont("Arial", 16, QFont.Bold))
        c_layout = QVBoxLayout(card_box)
        c_layout.setSpacing(12)

        c_desc = QLabel("Tìm các cặp hình đồ vật quen thuộc. Rèn luyện trí nhớ không gian và sự tập trung nhẹ nhàng.")
        c_desc.setFont(QFont("Arial", 14))
        c_desc.setWordWrap(True)
        c_layout.addWidget(c_desc)

        # Difficulty selector on card
        c_diff_box = QHBoxLayout()
        lbl_c_diff = QLabel("Chọn độ khó:")
        lbl_c_diff.setFont(QFont("Arial", 13, QFont.Bold))
        lbl_c_diff.setStyleSheet("color: #E65100;")
        c_diff_box.addWidget(lbl_c_diff)

        self.combo_card_diff = QComboBox()
        self.combo_card_diff.setFont(QFont("Arial", 13))
        self.combo_card_diff.setMinimumHeight(42)
        self.combo_card_diff.addItem("4 cặp (8 thẻ - Vừa)", "medium")
        self.combo_card_diff.addItem("6 cặp (12 thẻ - Khá)", "hard")
        self.combo_card_diff.addItem("8 cặp (16 thẻ - Thử thách)", "expert")
        self.combo_card_diff.addItem("3 cặp (6 thẻ - Khởi động)", "easy")
        c_diff_box.addWidget(self.combo_card_diff)
        c_layout.addLayout(c_diff_box)

        btn_start_cards = QPushButton("▶ Bắt Đầu Lật Hình")
        btn_start_cards.setMinimumHeight(56)
        btn_start_cards.setFont(QFont("Arial", 16, QFont.Bold))
        btn_start_cards.setStyleSheet(
            """
            QPushButton {
                background-color: #1976D2;
                color: #FFFFFF;
                border-radius: 10px;
            }
            QPushButton:hover { background-color: #1565C0; }
            """
        )
        btn_start_cards.clicked.connect(self._launch_card_matching)
        c_layout.addWidget(btn_start_cards)

        games_row.addWidget(card_box)

        # 2. Sequence Recall Launcher Card
        seq_box = QGroupBox("🔢 Nhớ Trình Tự")
        seq_box.setFont(QFont("Arial", 16, QFont.Bold))
        s_layout = QVBoxLayout(seq_box)
        s_layout.setSpacing(12)

        s_desc = QLabel("Quan sát thứ tự các hình xuất hiện và chọn lại đúng thứ tự đó. Đáp án được ẩn khi chọn.")
        s_desc.setFont(QFont("Arial", 14))
        s_desc.setWordWrap(True)
        s_layout.addWidget(s_desc)

        # Difficulty selector on sequence card
        s_diff_box = QHBoxLayout()
        lbl_s_diff = QLabel("Chọn độ khó:")
        lbl_s_diff.setFont(QFont("Arial", 13, QFont.Bold))
        lbl_s_diff.setStyleSheet("color: #526E58;")
        s_diff_box.addWidget(lbl_s_diff)

        self.combo_seq_diff = QComboBox()
        self.combo_seq_diff.setFont(QFont("Arial", 13))
        self.combo_seq_diff.setMinimumHeight(42)
        self.combo_seq_diff.addItem("3 hình (5 vòng - Vừa)", "medium")
        self.combo_seq_diff.addItem("4 hình (5 vòng - Thử thách)", "hard")
        self.combo_seq_diff.addItem("5 hình (5 vòng - Nâng cao)", "expert")
        self.combo_seq_diff.addItem("2 hình (5 vòng - Khởi động)", "easy")
        s_diff_box.addWidget(self.combo_seq_diff)
        s_layout.addLayout(s_diff_box)

        btn_start_seq = QPushButton("▶ Bắt Đầu Nhớ Trình Tự")
        btn_start_seq.setMinimumHeight(56)
        btn_start_seq.setFont(QFont("Arial", 16, QFont.Bold))
        btn_start_seq.setStyleSheet(
            """
            QPushButton {
                background-color: #526E58;
                color: #FFFFFF;
                border-radius: 10px;
            }
            QPushButton:hover { background-color: #435D49; }
            """
        )
        btn_start_seq.clicked.connect(self._launch_sequence_recall)
        s_layout.addWidget(btn_start_seq)

        games_row.addWidget(seq_box)

        layout.addLayout(games_row)

        # 3. Relaxing Music Embedded Widget
        self.music_widget = MusicPlayerWidget(self.audio_service)
        layout.addWidget(self.music_widget)

        return widget

    def _on_patient_changed(self) -> None:
        """Handles switching patient profile. Closes old profile before opening new one (Criterion 7)."""
        new_patient_id = self.combo_patient.currentData()
        if new_patient_id == self.current_patient_id:
            return

        # Ensure any active audio is stopped when switching patient (Criterion 7)
        if self.audio_service.is_playing:
            self.audio_service.stop_playback(stopped_reason="patient_switch")

        self.current_patient_id = new_patient_id
        self.identity_adapter.set_simulated_patient(new_patient_id)
        self.music_widget.set_patient_id(new_patient_id)

        # Update labels and recommendations
        pat_name = "Bác Nguyễn Văn An" if new_patient_id == "pat-001" else "Bác Trần Thị Mai"
        self.welcome_label.setText(f"Chào mừng {pat_name} tham gia các hoạt động hôm nay!")

        # Dynamic difficulty recommendations (Section 4)
        c_diff = self.cognitive_service.get_suggested_difficulty(new_patient_id, "card_matching")
        s_diff = self.cognitive_service.get_suggested_difficulty(new_patient_id, "sequence_recall")

        c_val = c_diff.value if hasattr(c_diff, "value") else str(c_diff)
        s_val = s_diff.value if hasattr(s_diff, "value") else str(s_diff)

        idx_c = self.combo_card_diff.findData(c_val)
        if idx_c >= 0:
            self.combo_card_diff.setCurrentIndex(idx_c)

        idx_s = self.combo_seq_diff.findData(s_val)
        if idx_s >= 0:
            self.combo_seq_diff.setCurrentIndex(idx_s)

        # Update actor combo if currently in patient role
        if self.current_actor_role == "patient":
            self.current_actor_id = new_patient_id

        self._sync_dashboard()

    def _on_actor_changed(self) -> None:
        data = self.combo_actor.currentData()
        if data:
            self.current_actor_id, self.current_actor_role = data
            self._sync_dashboard()

    def _on_camera_toggled(self) -> None:
        """Toggles camera simulation. Demonstrates fallback to PIN/session when camera fails (Criterion 7)."""
        is_active = self.btn_camera.isChecked()
        self.identity_adapter.set_camera_active(is_active)
        if is_active:
            self.btn_camera.setText("📷 Camera: Đang BẬT")
            self.btn_camera.setStyleSheet("background-color: #35634D; color: #FFF; border-radius: 6px; padding: 6px 12px;")
            QMessageBox.information(
                self,
                "Nhận diện khuôn mặt",
                "Camera nhận diện đang hoạt động. Hồ sơ bệnh nhân được tải tự động.",
            )
        else:
            self.btn_camera.setText("📷 Camera: Đang TẮT (Chế độ PIN/Dự phòng)")
            self.btn_camera.setStyleSheet("background-color: #D32F2F; color: #FFF; border-radius: 6px; padding: 6px 12px;")
            QMessageBox.warning(
                self,
                "Chế độ dự phòng khi camera lỗi (Criterion 7)",
                "Camera không khả dụng!\n\n"
                "Hệ thống tự động kích hoạt phiên dự phòng (PIN/phiên đã xác thực).\n"
                "Người dùng vẫn sử dụng đầy đủ trò chơi và âm nhạc mà không bị gián đoạn.",
            )

    def _on_reload_clicked(self) -> None:
        self._on_patient_changed()
        self._sync_dashboard()
        QMessageBox.information(self, "Đã làm mới", "Đã cập nhật lại toàn bộ dữ liệu mới nhất.")

    def _sync_dashboard(self) -> None:
        self.dashboard_widget.set_actor(
            actor_id=self.current_actor_id,
            actor_role=self.current_actor_role,
            target_patient_id=self.current_patient_id,
        )

    def _on_tab_changed(self, index: int) -> None:
        if index == 1:
            self._sync_dashboard()

    def _launch_card_matching(self) -> None:
        diff = self.combo_card_diff.currentData() or "medium"
        self.card_widget.start_game(patient_id=self.current_patient_id, difficulty=diff)
        self.main_stack.setCurrentIndex(1)

    def _launch_sequence_recall(self) -> None:
        diff = self.combo_seq_diff.currentData() or "medium"
        self.sequence_widget.start_game(patient_id=self.current_patient_id, difficulty=diff)
        self.main_stack.setCurrentIndex(2)

    def _return_to_main_menu(self) -> None:
        self.main_stack.setCurrentIndex(0)
        self._sync_dashboard()


def run_app() -> None:
    # Ensure database is seeded
    db = DatabaseManager()
    seed_cognitive_games_database(db)

    app = QApplication.instance()
    if not app:
        app = QApplication(sys.argv)

    window = MainWindow(db)
    window.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    run_app()
