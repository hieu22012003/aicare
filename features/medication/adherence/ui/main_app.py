"""
Standalone Multi-Role Runner and Simulation App for Medication Adherence Module.
Allows running the complete P0-P3 standalone module with role switching,
camera identity simulation, and time-travel testing.
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
root_dir = Path(__file__).resolve().parents[4]
if str(root_dir) not in sys.path:
    sys.path.insert(0, str(root_dir))

from features.medication.adherence.adapters.caregiver_repository import SqlCaregiverRepository
from features.medication.adherence.adapters.identity_adapter import MockPatientIdentityAdapter
from features.medication.adherence.adapters.medication_repository import SqlMedicationRepository
from features.medication.adherence.adapters.notification_gateway import MockNotificationGateway
from features.medication.adherence.adapters.patient_repository import SqlPatientRepository
from features.medication.adherence.database.connection import DatabaseManager
from features.medication.adherence.database.seed_data import seed_database
from features.medication.adherence.services.adherence_service import AdherenceService
from features.medication.adherence.services.scheduler import AdherenceScheduler
from features.medication.adherence.ui.caregiver_ui import CaregiverWidget
from features.medication.adherence.ui.elderly_ui import ElderlyWidget

logger = logging.getLogger("MedicationMainApp")

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


class SimulatedTime:
    def __init__(self) -> None:
        self.current_time = datetime.now(timezone.utc)

    def get_time(self) -> datetime:
        return self.current_time

    def advance(self, minutes: int = 60) -> datetime:
        self.current_time += timedelta(minutes=minutes)
        return self.current_time


class StandaloneMainWindow(QMainWindow if PYQT_AVAILABLE else object):
    """Integrated Desktop Window hosting Elderly UI, Caregiver UI, and Simulation Bar."""

    def __init__(self, db_path: Optional[str] = None) -> None:
        if not PYQT_AVAILABLE:
            raise RuntimeError("PyQt5 is not available in the current Python environment.")
        super().__init__()
        self.setWindowTitle("AI HEALTHCARE — Module Nhắc Thuốc & Theo Dõi Tuân Thủ Độc Lập")
        self.resize(1100, 750)

        # 1. Initialize Subsystems
        self.sim_clock = SimulatedTime()
        self.db = DatabaseManager(db_path or "data/medication_standalone.sqlite3")
        seed_database(self.db)

        self.identity_adapter = MockPatientIdentityAdapter()
        self.patient_repo = SqlPatientRepository(self.db)
        self.caregiver_repo = SqlCaregiverRepository(self.db)
        self.med_repo = SqlMedicationRepository(self.db)
        self.notif_gateway = MockNotificationGateway()

        self.scheduler = AdherenceScheduler(
            med_repo=self.med_repo,
            patient_repo=self.patient_repo,
            caregiver_repo=self.caregiver_repo,
            notif_gateway=self.notif_gateway,
            clock=self.sim_clock.get_time,
        )

        self.service = AdherenceService(
            med_repo=self.med_repo,
            patient_repo=self.patient_repo,
            caregiver_repo=self.caregiver_repo,
            notif_gateway=self.notif_gateway,
            clock=self.sim_clock.get_time,
        )

        # Default to Patient 1 recognized
        self.identity_adapter.simulate_camera_recognition("patient_001")

        # Initial scheduler tick
        self.scheduler.tick()

        # 2. Build UI Layout
        self._init_ui()

        # 3. Background periodic scheduler timer (every 10 seconds)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._on_tick)
        self.timer.start(10000)

    def _init_ui(self) -> None:
        central = QWidget(self)
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(10)

        # Simulation Bar at Top
        sim_bar = QFrame()
        sim_bar.setStyleSheet("background-color: #F8FAFC; border: 1.5px solid #CBD5E1; border-radius: 8px; padding: 6px;")
        s_lay = QHBoxLayout(sim_bar)

        s_lay.addWidget(QLabel("🎮 Bảng Điều Khiển Mô Phỏng:"))

        self.cmb_sim_patient = QComboBox()
        self.cmb_sim_patient.addItem("Camera: Cụ Hoàng Anh (patient_001)", "patient_001")
        self.cmb_sim_patient.addItem("Camera: Cụ Mai Hương (patient_002)", "patient_002")
        self.cmb_sim_patient.addItem("Camera: Mất tín hiệu / Tắt nhận diện", "")
        self.cmb_sim_patient.currentIndexChanged.connect(self._on_sim_identity_changed)
        s_lay.addWidget(self.cmb_sim_patient)

        self.btn_advance_1h = QPushButton("⏩ Tua Nhanh 1 Giờ")
        self.btn_advance_1h.setStyleSheet("background-color: #E2E8F0; font-weight: bold; padding: 6px 12px;")
        self.btn_advance_1h.clicked.connect(lambda: self._advance_time(60))
        s_lay.addWidget(self.btn_advance_1h)

        self.lbl_sim_clock = QLabel(f"Đồng hồ: {self.sim_clock.get_time().strftime('%H:%M %d/%m/%Y')} UTC")
        s_lay.addWidget(self.lbl_sim_clock)
        s_lay.addStretch()

        root.addWidget(sim_bar)

        # Tabs: Elderly View vs Caregiver View
        self.tabs = QTabWidget()
        self.elderly_widget = ElderlyWidget(self.service, self.identity_adapter, self)
        self.caregiver_widget = CaregiverWidget(self.service, self)

        self.tabs.addTab(self.elderly_widget, "👴 Giao Diện Người Cao Tuổi")
        self.tabs.addTab(self.caregiver_widget, "👨‍👩‍👧 Giao Diện Người Nhà / Người Chăm Sóc")
        root.addWidget(self.tabs, 1)

    def _on_sim_identity_changed(self) -> None:
        target_id = self.cmb_sim_patient.currentData()
        if target_id:
            self.identity_adapter.simulate_camera_recognition(target_id)
        else:
            self.identity_adapter.simulate_camera_loss()

    def _advance_time(self, minutes: int) -> None:
        new_time = self.sim_clock.advance(minutes)
        self.lbl_sim_clock.setText(f"Đồng hồ: {new_time.strftime('%H:%M %d/%m/%Y')} UTC")
        self._on_tick()

    def _on_tick(self) -> None:
        self.scheduler.tick()
        if hasattr(self.elderly_widget, "refresh_ui"):
            self.elderly_widget.refresh_ui()
        if hasattr(self.caregiver_widget, "refresh_ui"):
            self.caregiver_widget.refresh_ui()


def run_standalone_demo() -> None:
    """Launch the application in either GUI mode or Headless mode."""
    if PYQT_AVAILABLE:
        app = QApplication(sys.argv)
        win = StandaloneMainWindow()
        win.show()
        sys.exit(app.exec_())
    else:
        print("PyQt5 not installed in this environment. Running headless demonstration...")
        db = DatabaseManager(":memory:")
        seed_database(db)
        pat_repo = SqlPatientRepository(db)
        for p in pat_repo.list_all():
            print(f"- Found Patient: {p.full_name} ({p.patient_id}), Timezone: {p.timezone}")
        print("Headless demonstration completed successfully.")


if __name__ == "__main__":
    run_standalone_demo()
