"""
Caregiver User Interface (Giao diện Người nhà) for Medication Adherence Module.
Allows managing care recipients, creating prescriptions with distinct product strength vs dose,
7-day schedule previews, monitoring overdue incidents, and logging caregiver verifications.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional
from zoneinfo import ZoneInfo

from features.medication.adherence.domain.contracts import (
    ICaregiverRepository,
    IMedicationRepository,
    IPatientRepository,
)
from features.medication.adherence.domain.models import (
    Alert,
    AlertStatus,
    Medication,
    MedicationSchedule,
    Patient,
    Prescription,
    PrescriptionItem,
    PrescriptionSource,
)
from features.medication.adherence.services.adherence_service import AdherenceService

logger = logging.getLogger("CaregiverUI")

try:
    from PyQt5.QtCore import Qt
    from PyQt5.QtWidgets import (
        QAbstractItemView,
        QCheckBox,
        QComboBox,
        QDialog,
        QDialogButtonBox,
        QFileDialog,
        QFormLayout,
        QFrame,
        QHBoxLayout,
        QHeaderView,
        QLabel,
        QLineEdit,
        QMessageBox,
        QPushButton,
        QSpinBox,
        QTableWidget,
        QTableWidgetItem,
        QVBoxLayout,
        QWidget,
    )
    PYQT_AVAILABLE = True
except ImportError:
    PYQT_AVAILABLE = False
    QWidget = object


class CaregiverPresenter:
    """Headless Presenter containing 100% of Caregiver workflow logic."""

    def __init__(
        self,
        service: AdherenceService,
        on_refresh: Optional[Callable[[], None]] = None,
    ) -> None:
        self.service = service
        self.on_refresh = on_refresh
        self.patients = self.service.patient_repo.list_all()
        self.active_patient_id: Optional[str] = self.patients[0].patient_id if self.patients else None

    def set_active_patient(self, patient_id: str) -> None:
        self.active_patient_id = patient_id
        if self.on_refresh:
            self.on_refresh()

    def get_schedules(self) -> List[MedicationSchedule]:
        if not self.active_patient_id:
            return []
        return self.service.med_repo.list_schedules_for_patient(self.active_patient_id)

    def get_active_alerts(self) -> List[Alert]:
        if not self.active_patient_id:
            return []
        return self.service.med_repo.list_active_alerts(self.active_patient_id)

    def preview_7_days(
        self,
        start_date_str: str,
        local_times: List[str],
        days_of_week: List[int],
        timezone_str: str = "Asia/Ho_Chi_Minh",
        end_date_str: Optional[str] = None,
    ) -> List[str]:
        """Generate list of ISO formatted times for a 7-day preview."""
        try:
            zone = ZoneInfo(timezone_str)
        except Exception:
            zone = ZoneInfo("Asia/Ho_Chi_Minh")

        start = date.fromisoformat(start_date_str)
        end = min(start + timedelta(days=6), date.fromisoformat(end_date_str)) if end_date_str else start + timedelta(days=6)

        results: List[str] = []
        curr = start
        while curr <= end:
            if curr.weekday() in days_of_week:
                for hhmm in sorted(local_times):
                    results.append(f"{curr.isoformat()} lúc {hhmm} ({timezone_str})")
            curr += timedelta(days=1)
        return results

    def add_medication_plan(
        self,
        brand_name: str,
        product_strength: str,
        strength_unit: str,
        dose_amount: float,
        dose_unit: str,
        local_times: List[str],
        days_of_week: List[int],
        start_date_str: str,
        original_instructions: str,
        generic_name: str = "",
        route: str = "Đường uống",
        end_date_str: Optional[str] = None,
        image_path: str = "",
        safety_notes: str = "",
        caregiver_alert_enabled: bool = True,
    ) -> MedicationSchedule:
        if not self.active_patient_id:
            raise ValueError("Chưa chọn bệnh nhân")

        # 1. Create or reuse Medication
        med_id = f"med_{uuid.uuid4().hex[:8]}"
        med = Medication(
            medication_id=med_id,
            brand_name=brand_name.strip(),
            generic_name=generic_name.strip(),
            product_strength=product_strength.strip(),
            strength_unit=strength_unit.strip(),
            dosage_form="Viên nén",
            image_path=image_path.strip(),
            safety_notes=safety_notes.strip(),
        )
        self.service.med_repo.save_medication(med)

        # 2. Create Prescription
        rx_id = f"rx_{uuid.uuid4().hex[:8]}"
        prescription = Prescription(
            prescription_id=rx_id,
            patient_id=self.active_patient_id,
            entered_by_user_id="user_care_001",
            prescription_code=f"DON-{uuid.uuid4().hex[:6].upper()}",
            prescribed_date=start_date_str,
            source=PrescriptionSource.CAREGIVER_TRANSCRIBED,
            valid_from=start_date_str,
            valid_to=end_date_str,
        )

        # 3. Create Prescription Item (Distinct dose amount vs product strength)
        item_id = f"item_{uuid.uuid4().hex[:8]}"
        item = PrescriptionItem(
            prescription_item_id=item_id,
            prescription_id=rx_id,
            medication_id=med_id,
            dose_amount=float(dose_amount),
            dose_unit=dose_unit.strip(),
            route_of_administration=route.strip(),
            original_instructions=original_instructions.strip(),
            start_date=start_date_str,
            end_date=end_date_str,
        )

        # 4. Create Schedule
        sched_id = f"sched_{uuid.uuid4().hex[:8]}"
        schedule = MedicationSchedule(
            schedule_id=sched_id,
            prescription_item_id=item_id,
            revision=1,
            timezone="Asia/Ho_Chi_Minh",
            local_times=sorted(list(set(local_times))),
            days_of_week=sorted(list(set(days_of_week))),
            reminder_delay_minutes=10,
            grace_period_minutes=60,
            caregiver_alert_enabled=caregiver_alert_enabled,
            is_active=True,
            effective_from=datetime.now(timezone.utc).isoformat(),
        )

        self.service.med_repo.save_prescription(prescription, [(item, schedule)])
        if self.on_refresh:
            self.on_refresh()
        return schedule


if PYQT_AVAILABLE:
    class ScheduleEntryDialog(QDialog):
        """Modal dialog to enter a new medication plan with 7-day preview."""

        def __init__(self, presenter: CaregiverPresenter, parent: Optional[QWidget] = None) -> None:
            super().__init__(parent)
            self.presenter = presenter
            self.setWindowTitle("Khai Báo Thuốc & Lịch Nhắc (Người Nhà Nhập Theo Đơn)")
            self.setMinimumWidth(580)
            self._init_ui()

        def _init_ui(self) -> None:
            layout = QVBoxLayout(self)
            layout.setSpacing(12)

            info = QLabel(
                "ℹ️ Vui lòng nhập nguyên văn tên thuốc, hàm lượng trên vỏ hộp và liều uống theo đơn của bác sĩ.\n"
                "Người nhà chịu trách nhiệm kiểm tra thông tin đối chiếu với đơn thuốc gốc."
            )
            info.setWordWrap(True)
            info.setStyleSheet("color: #14233F; background: #EAF2FE; padding: 10px; border-radius: 6px;")
            layout.addWidget(info)

            form = QFormLayout()
            form.setSpacing(10)

            self.txt_brand = QLineEdit()
            self.txt_brand.setPlaceholderText("Ví dụ: Amlor 5mg, Glucophage 500mg")
            form.addRow("Tên biệt dược / Thuốc (*):", self.txt_brand)

            # Separate Product Strength from Dose per intake!
            h_strength = QHBoxLayout()
            self.txt_strength = QLineEdit("500")
            self.txt_strength_unit = QLineEdit("mg")
            h_strength.addWidget(self.txt_strength, 2)
            h_strength.addWidget(self.txt_strength_unit, 1)
            form.addRow("Hàm lượng sản phẩm (*):", h_strength)

            h_dose = QHBoxLayout()
            self.spn_dose = QSpinBox()
            self.spn_dose.setRange(1, 100)
            self.spn_dose.setValue(1)
            self.txt_dose_unit = QLineEdit("viên")
            h_dose.addWidget(self.spn_dose, 2)
            h_dose.addWidget(self.txt_dose_unit, 1)
            form.addRow("Liều uống mỗi lần (*):", h_dose)

            self.txt_times = QLineEdit("08:00")
            self.txt_times.setPlaceholderText("Các giờ trong ngày cách nhau bởi dấu phẩy: 08:00, 20:00")
            form.addRow("Giờ uống (HH:MM):", self.txt_times)

            self.txt_start_date = QLineEdit(date.today().isoformat())
            form.addRow("Ngày bắt đầu (YYYY-MM-DD):", self.txt_start_date)

            self.txt_instructions = QLineEdit()
            self.txt_instructions.setPlaceholderText("Ví dụ: Uống sau bữa ăn sáng 15 phút, uống nhiều nước")
            form.addRow("Hướng dẫn nguyên văn theo đơn:", self.txt_instructions)

            self.chk_alert = QCheckBox("Bật gửi cảnh báo cho người nhà khi quá hạn chưa xác nhận")
            self.chk_alert.setChecked(True)
            form.addRow(self.chk_alert)

            layout.addLayout(form)

            # 7-day preview button
            btn_preview = QPushButton("🔍 Xem Trước Lịch 7 Ngày Tới")
            btn_preview.setStyleSheet("background-color: #F0F5FD; border: 1.5px solid #2878F0; color: #2878F0; font-weight: bold; min-height: 36px;")
            btn_preview.clicked.connect(self._preview_7_days)
            layout.addWidget(btn_preview)

            # Dialog Buttons
            buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
            buttons.accepted.connect(self._save)
            buttons.rejected.connect(self.reject)
            layout.addWidget(buttons)

        def _preview_7_days(self) -> None:
            try:
                times = [t.strip() for t in self.txt_times.text().split(",") if t.strip()]
                start = self.txt_start_date.text().strip()
                preview_list = self.presenter.preview_7_days(start, times, [0, 1, 2, 3, 4, 5, 6])
                preview_text = "\n".join(preview_list) if preview_list else "Không có lần uống nào."
                QMessageBox.information(self, "Xem trước 7 ngày", f"Các lần uống dự kiến trong 7 ngày tới:\n\n{preview_text}")
            except Exception as e:
                QMessageBox.warning(self, "Lỗi định dạng", f"Vui lòng kiểm tra lại giờ hoặc ngày: {e}")

        def _save(self) -> None:
            brand = self.txt_brand.text().strip()
            if not brand:
                QMessageBox.warning(self, "Thiếu thông tin", "Vui lòng nhập tên thuốc")
                return

            times = [t.strip() for t in self.txt_times.text().split(",") if t.strip()]
            if not times:
                QMessageBox.warning(self, "Thiếu thông tin", "Vui lòng nhập ít nhất một mốc giờ (HH:MM)")
                return

            try:
                self.presenter.add_medication_plan(
                    brand_name=brand,
                    product_strength=self.txt_strength.text().strip(),
                    strength_unit=self.txt_strength_unit.text().strip(),
                    dose_amount=float(self.spn_dose.value()),
                    dose_unit=self.txt_dose_unit.text().strip(),
                    local_times=times,
                    days_of_week=[0, 1, 2, 3, 4, 5, 6],
                    start_date_str=self.txt_start_date.text().strip(),
                    original_instructions=self.txt_instructions.text().strip(),
                    caregiver_alert_enabled=self.chk_alert.isChecked(),
                )
                self.accept()
            except Exception as e:
                QMessageBox.critical(self, "Lỗi lưu lịch", str(e))


    class CaregiverWidget(QWidget):
        """Main Caregiver Control Panel Widget."""

        def __init__(self, service: AdherenceService, parent: Optional[QWidget] = None) -> None:
            super().__init__(parent)
            self.presenter = CaregiverPresenter(service, on_refresh=self.refresh_ui)
            self._init_ui()
            self.refresh_ui()

        def _init_ui(self) -> None:
            layout = QVBoxLayout(self)
            layout.setContentsMargins(14, 14, 14, 14)
            layout.setSpacing(12)

            # Top Bar: Patient Selector & Add Schedule
            top_bar = QHBoxLayout()
            top_bar.addWidget(QLabel("Người cao tuổi:"))
            self.cmb_patient = QComboBox()
            for p in self.presenter.patients:
                self.cmb_patient.addItem(f"{p.full_name} ({p.patient_id})", p.patient_id)
            self.cmb_patient.currentIndexChanged.connect(self._on_patient_changed)
            top_bar.addWidget(self.cmb_patient)

            top_bar.addStretch()
            btn_add = QPushButton("➕ Thêm Đơn & Lịch Thuốc Mới")
            btn_add.setStyleSheet("background-color: #2878F0; color: white; font-weight: bold; padding: 8px 16px; border-radius: 6px;")
            btn_add.clicked.connect(self._open_add_dialog)
            top_bar.addWidget(btn_add)
            layout.addLayout(top_bar)

            # Active Alerts Section (Overdue & SOS)
            lbl_alerts = QLabel("⚠️ SỰ CỐ & CẢNH BÁO QUÁ HẠN CẦN XỬ LÝ")
            lbl_alerts.setStyleSheet("font-size: 16px; font-weight: bold; color: #C3293E;")
            layout.addWidget(lbl_alerts)

            self.tbl_alerts = QTableWidget(0, 5)
            self.tbl_alerts.setHorizontalHeaderLabels(["Mã cảnh báo", "Loại sự cố", "Thời điểm phát sinh", "Trạng thái", "Thao tác"])
            self.tbl_alerts.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
            self.tbl_alerts.setSelectionBehavior(QAbstractItemView.SelectRows)
            layout.addWidget(self.tbl_alerts)

            # Medication Schedules Section
            lbl_scheds = QLabel("📋 DANH SÁCH LỊCH THUỐC ĐANG HIỆU LỰC")
            lbl_scheds.setStyleSheet("font-size: 16px; font-weight: bold; color: #14233F;")
            layout.addWidget(lbl_scheds)

            self.tbl_scheds = QTableWidget(0, 5)
            self.tbl_scheds.setHorizontalHeaderLabels(["Mã lịch", "Phiên bản", "Giờ uống", "Thông báo người nhà", "Múi giờ"])
            self.tbl_scheds.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
            layout.addWidget(self.tbl_scheds)

        def _on_patient_changed(self) -> None:
            pid = self.cmb_patient.currentData()
            if pid:
                self.presenter.set_active_patient(pid)

        def _open_add_dialog(self) -> None:
            dlg = ScheduleEntryDialog(self.presenter, self)
            if dlg.exec_() == QDialog.Accepted:
                self.refresh_ui()

        def refresh_ui(self) -> None:
            # Refresh Alerts
            alerts = self.presenter.get_active_alerts()
            self.tbl_alerts.setRowCount(len(alerts))
            for i, a in enumerate(alerts):
                self.tbl_alerts.setItem(i, 0, QTableWidgetItem(a.alert_id[:8]))
                self.tbl_alerts.setItem(i, 1, QTableWidgetItem(a.alert_type.value))
                self.tbl_alerts.setItem(i, 2, QTableWidgetItem(a.created_at_utc[:19]))
                self.tbl_alerts.setItem(i, 3, QTableWidgetItem(a.status.value))

                btn_resolve = QPushButton("Ghi nhận đã hỗ trợ")
                btn_resolve.clicked.connect(lambda _, aid=a.occurrence_id: self._resolve_alert(aid))
                self.tbl_alerts.setCellWidget(i, 4, btn_resolve)

            # Refresh Schedules
            scheds = self.presenter.get_schedules()
            self.tbl_scheds.setRowCount(len(scheds))
            for i, s in enumerate(scheds):
                self.tbl_scheds.setItem(i, 0, QTableWidgetItem(s.schedule_id[:8]))
                self.tbl_scheds.setItem(i, 1, QTableWidgetItem(f"v{s.revision}"))
                self.tbl_scheds.setItem(i, 2, QTableWidgetItem(", ".join(s.local_times)))
                self.tbl_scheds.setItem(i, 3, QTableWidgetItem("Bật ✓" if s.caregiver_alert_enabled else "Tắt ✕"))
                self.tbl_scheds.setItem(i, 4, QTableWidgetItem(s.timezone))

        def _resolve_alert(self, occurrence_id: str) -> None:
            reply = QMessageBox.question(
                self,
                "Xử lý cảnh báo",
                "Xác nhận đã liên hệ người cao tuổi và xử lý xong sự cố?",
                QMessageBox.Yes | QMessageBox.No,
            )
            if reply == QMessageBox.Yes:
                self.presenter.service.caregiver_resolve(
                    occurrence_id=occurrence_id,
                    caregiver_id="caregiver_001",
                    resolution_type="caregiver_reported_taken",
                    notes="Người nhà đã liên hệ và xác nhận",
                )
                self.refresh_ui()
else:
    class CaregiverWidget:
        def __init__(self, *args, **kwargs):
            pass
