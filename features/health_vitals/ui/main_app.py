"""
Standalone Multi-Role Runner and Simulation App for Health Monitoring Module (P0-P2).
Allows launching the complete standalone module with role switching,
doctor dashboard, patient entry UI, camera simulation, and time-travel testing.
"""

from __future__ import annotations

import logging
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

# Auto-detect Wayland on Linux GNOME when not explicitly set, to avoid xcb crash
if "QT_QPA_PLATFORM" not in os.environ and os.environ.get("XDG_SESSION_TYPE") == "wayland":
    os.environ["QT_QPA_PLATFORM"] = "wayland;xcb"

# Ensure project root is on sys.path
root_dir = Path(__file__).resolve().parents[3]
if str(root_dir) not in sys.path:
    sys.path.insert(0, str(root_dir))

from features.health_vitals.adapters.care_team_repository import SqlCareTeamRepository
from features.health_vitals.adapters.identity_adapter import MockPatientIdentityAdapter
from features.health_vitals.adapters.observation_repository import SqlObservationRepository
from features.health_vitals.adapters.patient_access_repository import SqlPatientAccessRepository
from features.health_vitals.database.connection import DatabaseManager
from features.health_vitals.database.seed_data import seed_database
from features.health_vitals.services.monitoring_service import MonitoringService
from features.health_vitals.ui.clinician_dashboard_ui import ClinicianDashboardWidget
from features.health_vitals.ui.patient_entry_ui import PatientEntryWidget

logger = logging.getLogger("HealthMainApp")

try:
    from PyQt5.QtCore import QTimer
    from PyQt5.QtWidgets import (
        QApplication,
        QComboBox,
        QFrame,
        QHBoxLayout,
        QLabel,
        QMainWindow,
        QPushButton,
        QTabWidget,
        QVBoxLayout,
        QWidget,
    )
    PYQT_AVAILABLE = True
except ImportError:
    PYQT_AVAILABLE = False
    QMainWindow = object


class AppClock:
    """Real-time clock tracking actual wall-clock time with simulation offset."""

    def __init__(self) -> None:
        self._offset = timedelta(0)

    def get_time(self) -> datetime:
        # Real-time wall clock with current local timezone + simulation offset
        return datetime.now().astimezone() + self._offset

    def advance(self, hours: int = 24) -> datetime:
        self._offset += timedelta(hours=hours)
        return self.get_time()

    def reset_to_now(self) -> datetime:
        self._offset = timedelta(0)
        return self.get_time()


class StandaloneHealthMainWindow(QMainWindow if PYQT_AVAILABLE else object):
    """Integrated Desktop Window hosting Clinician Dashboard, Patient Entry UI, and Simulation Bar."""

    def __init__(self, db_path: Optional[str] = None) -> None:
        if not PYQT_AVAILABLE:
            raise RuntimeError("PyQt5 is not available in the current Python environment.")
        super().__init__()
        self.setWindowTitle("AI HEALTHCARE — Module Nhật Ký Sức Khỏe & Bác Sĩ Theo Dõi Từ Xa (P0-P2)")
        self.resize(1200, 800)

        # 1. Initialize Subsystems
        self.sim_clock = AppClock()
        self.db = DatabaseManager(db_path or "data/health_standalone.sqlite3")
        seed_database(self.db)

        self.identity_adapter = MockPatientIdentityAdapter("pat-001")
        self.patient_access_repo = SqlPatientAccessRepository(self.db)
        self.care_team_repo = SqlCareTeamRepository(self.db)
        self.obs_repo = SqlObservationRepository(self.db)

        self.service = MonitoringService(
            obs_repo=self.obs_repo,
            care_team_repo=self.care_team_repo,
            patient_access_repo=self.patient_access_repo,
            clock=self.sim_clock.get_time,
        )

        self._init_ui()

    def _init_ui(self) -> None:
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)
        main_layout.setContentsMargins(8, 8, 8, 8)
        main_layout.setSpacing(6)

        # --- SIMULATION & ROLE BAR ---
        sim_bar = QFrame()
        sim_bar.setStyleSheet("background-color: #0f172a; color: white; border-radius: 6px; padding: 6px;")
        sim_layout = QHBoxLayout(sim_bar)
        sim_layout.setContentsMargins(8, 4, 8, 4)

        sim_layout.addWidget(QLabel("<b>[BỘ ĐIỀU KHIỂN]</b> Vai trò:"))
        self.combo_role = QComboBox()
        self.combo_role.setStyleSheet("background-color: #1e293b; color: white; font-weight: bold; padding: 4px;")
        self.combo_role.addItem("BS.CKII Lê Quang (doc-001, Tim mạch - Xem Cụ An & Cụ Bình)", "doc-001")
        self.combo_role.addItem("ThS.BS Phạm Mai (doc-002, Nội tiết - Xem Cụ An, Bị chặn Cụ Bình)", "doc-002")
        self.combo_role.addItem("Cụ Nguyễn Văn An (pat-001 - Bệnh nhân A)", "pat-001")
        self.combo_role.addItem("Anh Nguyễn Văn Bình (usr-cg-001 - Con trai chăm Cụ An)", "cg-001")
        self.combo_role.currentIndexChanged.connect(self._on_role_changed)
        sim_layout.addWidget(self.combo_role)

        sim_layout.addSpacing(16)
        self.btn_camera = QPushButton("Camera: BẬT")
        self.btn_camera.setStyleSheet("background-color: #059669; color: white; font-weight: bold; padding: 4px 8px; border-radius: 4px;")
        self.btn_camera.clicked.connect(self._toggle_camera)
        sim_layout.addWidget(self.btn_camera)

        sim_layout.addSpacing(16)
        cur_t = self.sim_clock.get_time()
        self.lbl_clock = QLabel(f"Thời gian: {cur_t.strftime('%d/%m/%Y %H:%M:%S')}")
        self.lbl_clock.setStyleSheet("font-weight: bold; color: #38bdf8;")
        sim_layout.addWidget(self.lbl_clock)

        self.btn_advance_time = QPushButton("+24 Giờ")
        self.btn_advance_time.setStyleSheet("background-color: #475569; color: white; padding: 4px 8px; border-radius: 4px;")
        self.btn_advance_time.clicked.connect(self._advance_time)
        sim_layout.addWidget(self.btn_advance_time)

        self.btn_reset_time = QPushButton("⏱ Giờ Thực")
        self.btn_reset_time.setStyleSheet("background-color: #334155; color: white; padding: 4px 8px; border-radius: 4px;")
        self.btn_reset_time.clicked.connect(self._reset_time)
        sim_layout.addWidget(self.btn_reset_time)

        sim_layout.addSpacing(16)
        self.btn_global_reload = QPushButton("🔄 TẢI LẠI DỮ LIỆU")
        self.btn_global_reload.setStyleSheet("background-color: #0284c7; color: white; font-weight: bold; padding: 4px 12px; border-radius: 4px;")
        self.btn_global_reload.setToolTip("Tải lại tức thì toàn bộ dữ liệu trên cả 2 màn hình từ CSDL")
        self.btn_global_reload.clicked.connect(self._reload_all_data)
        sim_layout.addWidget(self.btn_global_reload)

        main_layout.addWidget(sim_bar)

        # --- TAB CONTAINER: CLINICIAN DASHBOARD & PATIENT ENTRY ---
        self.tabs = QTabWidget()
        self.tabs.setStyleSheet("""
            QTabBar::tab { font-size: 14px; font-weight: bold; padding: 10px 24px; }
            QTabBar::tab:selected { background: #0284c7; color: white; }
        """)

        # 1. Clinician Tab
        self.clinician_widget = ClinicianDashboardWidget(self.service, clinician_id="doc-001")
        self.tabs.addTab(self.clinician_widget, "🩺 Dashboard Bác Sĩ (Theo Dõi Từ Xa)")

        # 2. Patient Entry Tab
        self.patient_widget = PatientEntryWidget(self.service, patient_id="pat-001", actor_user_id="usr-pat-001")
        self.tabs.addTab(self.patient_widget, "📝 Nhập Số Đo (Người Bệnh / Người Thân)")

        # Connect real-time synchronization signals
        self.patient_widget.measurement_saved.connect(self.clinician_widget.refresh_all)
        self.tabs.currentChanged.connect(self._on_tab_changed)

        main_layout.addWidget(self.tabs)

        # Timers: Clock tick (1s) & Real-time Auto-Refresh (2s)
        self.clock_timer = QTimer(self)
        self.clock_timer.timeout.connect(self._on_clock_tick)
        self.clock_timer.start(1000)

        self.poll_timer = QTimer(self)
        self.poll_timer.timeout.connect(self._on_auto_poll)
        self.poll_timer.start(2000)

    def _on_clock_tick(self) -> None:
        cur_t = self.sim_clock.get_time()
        self.lbl_clock.setText(f"Thời gian: {cur_t.strftime('%d/%m/%Y %H:%M:%S')}")

    def _on_auto_poll(self) -> None:
        # Real-time background sync when doctor tab is open
        if self.tabs.currentIndex() == 0:
            self.clinician_widget.refresh_all()

    def _on_tab_changed(self, index: int) -> None:
        if index == 0:
            self.clinician_widget.refresh_all()
        elif index == 1:
            self.patient_widget.refresh_data()

    def _on_role_changed(self, index: int) -> None:
        role_data = self.combo_role.currentData()
        if role_data.startswith("doc-"):
            self.tabs.setCurrentIndex(0)
            self.clinician_widget.set_clinician(role_data)
        elif role_data == "pat-001":
            self.tabs.setCurrentIndex(1)
            self.patient_widget.set_active_patient("pat-001", "usr-pat-001")
        elif role_data == "cg-001":
            self.tabs.setCurrentIndex(1)
            self.patient_widget.set_active_patient("pat-001", "usr-cg-001")

    def _toggle_camera(self) -> None:
        active = not self.identity_adapter.is_camera_active()
        self.identity_adapter.set_camera_active(active)
        if active:
            self.btn_camera.setText("Camera: BẬT")
            self.btn_camera.setStyleSheet("background-color: #059669; color: white; font-weight: bold; padding: 4px 8px; border-radius: 4px;")
        else:
            self.btn_camera.setText("Camera: TẮT (Fallback)")
            self.btn_camera.setStyleSheet("background-color: #dc2626; color: white; font-weight: bold; padding: 4px 8px; border-radius: 4px;")

    def _advance_time(self) -> None:
        new_time = self.sim_clock.advance(hours=24)
        self.lbl_clock.setText(f"Thời gian: {new_time.strftime('%d/%m/%Y %H:%M:%S')}")
        self.clinician_widget.refresh_all()
        self.patient_widget.refresh_data()

    def _reset_time(self) -> None:
        new_time = self.sim_clock.reset_to_now()
        self.lbl_clock.setText(f"Thời gian: {new_time.strftime('%d/%m/%Y %H:%M:%S')}")
        self.clinician_widget.refresh_all()
        self.patient_widget.refresh_data()

    def _reload_all_data(self) -> None:
        self.clinician_widget.refresh_all()
        self.patient_widget.refresh_data()



def run_standalone_app() -> None:
    if not PYQT_AVAILABLE:
        print("PyQt5 is not available. Please install PyQt5 or run CLI demo via demo_cli.py.")
        sys.exit(1)

    app = QApplication(sys.argv)
    window = StandaloneHealthMainWindow()
    window.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    run_standalone_app()
