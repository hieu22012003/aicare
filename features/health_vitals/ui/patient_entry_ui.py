"""
Elderly and Caregiver Measurement Entry UI Widget for Health Monitoring (P1).
Provides high-contrast, large-font inputs for logging blood pressure (2 components in one form),
vitals, self-reported symptoms, and viewing doctor feedback.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from features.health_vitals.domain.models import MeasurementComponent

logger = logging.getLogger("PatientEntryUI")


def format_display_dt(iso_str: Optional[str]) -> str:
    """Format ISO datetime to Vietnam local DD/MM/YYYY HH:MM:SS format."""
    if not iso_str:
        return "---"
    try:
        dt = datetime.fromisoformat(iso_str)
        if dt.tzinfo:
            dt = dt.astimezone()
        return dt.strftime("%d/%m/%Y %H:%M:%S")
    except Exception:
        return iso_str[:19].replace("T", " ")


try:
    from PyQt5.QtCore import QDateTime, Qt, pyqtSignal
    from PyQt5.QtGui import QFont
    from PyQt5.QtWidgets import (
        QComboBox,
        QDateTimeEdit,
        QFrame,
        QGroupBox,
        QHBoxLayout,
        QHeaderView,
        QLabel,

        QLineEdit,
        QMessageBox,
        QPushButton,
        QSpinBox,
        QTableWidget,
        QTableWidgetItem,
        QTextEdit,
        QVBoxLayout,
        QWidget,
    )
    PYQT_AVAILABLE = True
except ImportError:
    PYQT_AVAILABLE = False
    QWidget = object


class PatientEntryWidget(QWidget if PYQT_AVAILABLE else object):
    """Entry form for patients and caregivers with large fonts and simple workflows."""

    measurement_saved = pyqtSignal()

    def __init__(self, service, patient_id: str = "pat-001", actor_user_id: str = "usr-pat-001", parent=None) -> None:
        if not PYQT_AVAILABLE:
            raise RuntimeError("PyQt5 is required for PatientEntryWidget.")
        super().__init__(parent)
        self.service = service
        self.patient_id = patient_id
        self.actor_user_id = actor_user_id
        self._init_ui()

    def set_active_patient(self, patient_id: str, actor_user_id: str) -> None:
        self.patient_id = patient_id
        self.actor_user_id = actor_user_id
        self.refresh_data()

    def _init_ui(self) -> None:
        main_layout = QVBoxLayout(self)
        main_layout.setSpacing(16)
        main_layout.setContentsMargins(16, 16, 16, 16)

        # Title
        self.lbl_title = QLabel(f"NHẬT KÝ SỨC KHỎE — BỆNH NHÂN: {self.patient_id}")
        title_font = QFont()
        title_font.setPointSize(16)
        title_font.setBold(True)
        self.lbl_title.setFont(title_font)
        main_layout.addWidget(self.lbl_title)

        content_layout = QHBoxLayout()

        # LEFT COLUMN: Entry Forms
        left_box = QVBoxLayout()

        # Group 0: Measurement Time picker (Section 2 spec: default current, editable)
        time_group = QGroupBox("Thời Điểm Đo Thực Tế (Giờ thực tế từ máy đo)")
        time_group.setStyleSheet("QGroupBox { font-weight: bold; font-size: 13px; }")
        time_layout = QHBoxLayout(time_group)

        self.dt_measured = QDateTimeEdit()
        self.dt_measured.setCalendarPopup(True)
        self.dt_measured.setDisplayFormat("dd/MM/yyyy HH:mm:ss")
        self.dt_measured.setDateTime(QDateTime.currentDateTime())
        self.dt_measured.setStyleSheet("font-size: 14px; font-weight: bold; padding: 4px;")
        time_layout.addWidget(self.dt_measured)

        self.btn_now = QPushButton("⏱ Đặt Giờ Hiện Tại")
        self.btn_now.setStyleSheet("background-color: #f1f5f9; padding: 4px 8px; border-radius: 4px; font-weight: bold;")
        self.btn_now.clicked.connect(self._reset_time_to_now)
        time_layout.addWidget(self.btn_now)
        left_box.addWidget(time_group)

        # Group 1: Blood Pressure Form (2 components in 1 form)
        bp_group = QGroupBox("Ghi Nhận Huyết Áp (Máy đo bắp tay)")
        bp_group.setStyleSheet("QGroupBox { font-weight: bold; font-size: 14px; }")
        bp_layout = QVBoxLayout(bp_group)


        row_sys_dia = QHBoxLayout()
        row_sys_dia.addWidget(QLabel("Tâm thu (SYS):"))
        self.spin_sys = QSpinBox()
        self.spin_sys.setRange(50, 300)
        self.spin_sys.setValue(125)
        self.spin_sys.setStyleSheet("font-size: 15px; font-weight: bold; padding: 4px;")
        row_sys_dia.addWidget(self.spin_sys)
        row_sys_dia.addWidget(QLabel("mmHg"))

        row_sys_dia.addSpacing(16)
        row_sys_dia.addWidget(QLabel("Tâm trương (DIA):"))
        self.spin_dia = QSpinBox()
        self.spin_dia.setRange(30, 200)
        self.spin_dia.setValue(80)
        self.spin_dia.setStyleSheet("font-size: 15px; font-weight: bold; padding: 4px;")
        row_sys_dia.addWidget(self.spin_dia)
        row_sys_dia.addWidget(QLabel("mmHg"))
        bp_layout.addLayout(row_sys_dia)

        row_dev = QHBoxLayout()
        row_dev.addWidget(QLabel("Tên máy đo:"))
        self.txt_bp_device = QLineEdit("Máy đo bắp tay Omron")
        row_dev.addWidget(self.txt_bp_device)

        row_dev.addWidget(QLabel("Hoàn cảnh:"))
        self.txt_bp_context = QLineEdit("Nghỉ ngơi 15 phút")
        row_dev.addWidget(self.txt_bp_context)
        bp_layout.addLayout(row_dev)

        self.btn_save_bp = QPushButton("LƯU KẾT QUẢ HUYẾT ÁP")
        self.btn_save_bp.setStyleSheet("background-color: #0284c7; color: white; font-weight: bold; font-size: 14px; padding: 8px; border-radius: 4px;")
        self.btn_save_bp.clicked.connect(self._on_save_blood_pressure)
        bp_layout.addWidget(self.btn_save_bp)
        left_box.addWidget(bp_group)

        # Group 2: Other Vitals (Heart rate, SpO2, Temp, Glucose)
        vital_group = QGroupBox("Ghi Nhận Chỉ Số Khác")
        vital_group.setStyleSheet("QGroupBox { font-weight: bold; font-size: 14px; }")
        vital_layout = QVBoxLayout(vital_group)

        row_v = QHBoxLayout()
        row_v.addWidget(QLabel("Loại chỉ số:"))
        self.combo_vital = QComboBox()
        self.combo_vital.addItem("Nhịp tim (bpm)", "heart_rate")
        self.combo_vital.addItem("SpO2 (%)", "spo2")
        self.combo_vital.addItem("Thân nhiệt (°C)", "temperature")
        self.combo_vital.addItem("Đường huyết (mg/dL)", "blood_glucose")
        row_v.addWidget(self.combo_vital)

        row_v.addWidget(QLabel("Giá trị:"))
        self.txt_vital_val = QLineEdit("75")
        self.txt_vital_val.setStyleSheet("font-size: 15px; font-weight: bold; padding: 4px;")
        row_v.addWidget(self.txt_vital_val)
        vital_layout.addLayout(row_v)

        self.btn_save_vital = QPushButton("LƯU CHỈ SỐ")
        self.btn_save_vital.setStyleSheet("background-color: #10b981; color: white; font-weight: bold; font-size: 14px; padding: 8px; border-radius: 4px;")
        self.btn_save_vital.clicked.connect(self._on_save_vital)
        vital_layout.addWidget(self.btn_save_vital)
        left_box.addWidget(vital_group)

        # Group 3: Symptoms
        sym_group = QGroupBox("Ghi Nhận Triệu Chứng Tự Khai Báo")
        sym_group.setStyleSheet("QGroupBox { font-weight: bold; font-size: 14px; }")
        sym_layout = QVBoxLayout(sym_group)

        self.txt_symptom = QLineEdit()
        self.txt_symptom.setPlaceholderText("Ví dụ: Hơi choáng nhẹ, đau mỏi gáy, buồn nôn...")
        sym_layout.addWidget(self.txt_symptom)

        row_discomfort = QHBoxLayout()
        row_discomfort.addWidget(QLabel("Mức độ khó chịu (1: Nhẹ, 5: Rất khó chịu):"))
        self.spin_discomfort = QSpinBox()
        self.spin_discomfort.setRange(1, 5)
        self.spin_discomfort.setValue(1)
        row_discomfort.addWidget(self.spin_discomfort)
        sym_layout.addLayout(row_discomfort)

        self.btn_save_sym = QPushButton("GỬI BÁO CÁO TRIỆU CHỨNG")
        self.btn_save_sym.setStyleSheet("background-color: #8b5cf6; color: white; font-weight: bold; font-size: 14px; padding: 8px; border-radius: 4px;")
        self.btn_save_sym.clicked.connect(self._on_save_symptom)
        sym_layout.addWidget(self.btn_save_sym)
        left_box.addWidget(sym_group)

        content_layout.addLayout(left_box, 1)

        # RIGHT COLUMN: History & Doctor Feedback
        right_box = QVBoxLayout()

        # Doctor Advice Card
        adv_group = QGroupBox("Lời Dặn / Phản Hồi Từ Bác Sĩ")
        adv_group.setStyleSheet("QGroupBox { font-weight: bold; font-size: 14px; }")
        adv_layout = QVBoxLayout(adv_group)
        self.lbl_advice = QLabel("Đang tải phản hồi của bác sĩ...")
        self.lbl_advice.setWordWrap(True)
        self.lbl_advice.setStyleSheet("background-color: #f0fdf4; border: 1px solid #bbf7d0; padding: 12px; border-radius: 6px; font-size: 13px; color: #166534;")
        adv_layout.addWidget(self.lbl_advice)
        right_box.addWidget(adv_group)

        # History Table
        hist_group = QGroupBox("Số Đo Gần Đây Của Bệnh Nhân")
        hist_group.setStyleSheet("QGroupBox { font-weight: bold; font-size: 14px; }")
        hist_layout = QVBoxLayout(hist_group)

        hist_top = QHBoxLayout()
        hist_top.addWidget(QLabel("Lịch sử các lần đo gần nhất:"))
        hist_top.addStretch()
        self.btn_reload_patient = QPushButton("🔄 TẢI LẠI LỊCH SỬ")
        self.btn_reload_patient.setStyleSheet("background-color: #0284c7; color: white; font-weight: bold; padding: 4px 10px; border-radius: 4px;")
        self.btn_reload_patient.clicked.connect(self.refresh_data)
        hist_top.addWidget(self.btn_reload_patient)
        hist_layout.addLayout(hist_top)

        self.table_history = QTableWidget()
        self.table_history.setColumnCount(5)
        self.table_history.setHorizontalHeaderLabels(["Thời điểm đo", "Loại chỉ số", "Giá trị", "Nguồn / Máy", "Trạng thái"])
        self.table_history.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        hist_layout.addWidget(self.table_history)
        right_box.addWidget(hist_group)

        content_layout.addLayout(right_box, 1)
        main_layout.addLayout(content_layout)

        self.refresh_data()

    def _reset_time_to_now(self) -> None:
        self.dt_measured.setDateTime(QDateTime.currentDateTime())

    def _get_measured_at_iso(self) -> str:
        meas_dt = self.dt_measured.dateTime().toPyDateTime()
        if meas_dt.tzinfo is None:
            meas_dt = meas_dt.astimezone()
        return meas_dt.isoformat()

    def refresh_data(self) -> None:
        self.lbl_title.setText(f"NHẬT KÝ SỨC KHỎE — BỆNH NHÂN: {self.patient_id}")
        self._refresh_history_table()
        self._refresh_doctor_advice()

    def _refresh_history_table(self) -> None:
        obs_list = self.service.obs_repo.get_observations(self.patient_id, limit=20)
        self.table_history.setRowCount(len(obs_list))
        for row, o in enumerate(obs_list):
            dt_s = format_display_dt(o.measured_at)
            self.table_history.setItem(row, 0, QTableWidgetItem(dt_s))
            self.table_history.setItem(row, 1, QTableWidgetItem(o.type_name))
            self.table_history.setItem(row, 2, QTableWidgetItem(f"{o.value_numeric} {o.unit_code}"))
            self.table_history.setItem(row, 3, QTableWidgetItem(o.device_label))
            self.table_history.setItem(row, 4, QTableWidgetItem(o.status))

    def _refresh_doctor_advice(self) -> None:
        try:
            msgs = self.service.get_patient_messages(self.actor_user_id, self.patient_id)
            if not msgs:
                self.lbl_advice.setText("Chưa có tin nhắn hoặc lời dặn mới từ bác sĩ phụ trách.")
            else:
                latest = msgs[0]
                status_txt = "Đã đọc" if latest.is_read_by_patient else "Mới (Chưa đọc)"
                dt_msg = format_display_dt(latest.created_at)
                self.lbl_advice.setText(
                    f"<b>Bác sĩ:</b> {latest.clinician_name} ({dt_msg})<br>"
                    f"<b>Lời dặn:</b> <i>\"{latest.body}\"</i><br>"
                    f"<small>Trạng thái: {status_txt}</small>"
                )
                if not latest.is_read_by_patient:
                    self.service.mark_message_as_read(latest.message_id, self.actor_user_id)
        except Exception as e:
            self.lbl_advice.setText(f"Không thể tải phản hồi: {e}")

    def _on_save_blood_pressure(self) -> None:
        sys_v = float(self.spin_sys.value())
        dia_v = float(self.spin_dia.value())
        dev_label = self.txt_bp_device.text().strip()
        ctx = self.txt_bp_context.text().strip()
        meas_iso = self._get_measured_at_iso()

        try:
            self.service.record_blood_pressure(
                patient_id=self.patient_id,
                entered_by_user_id=self.actor_user_id,
                systolic=sys_v,
                diastolic=dia_v,
                measured_at=meas_iso,
                device_label=dev_label,
                context=ctx,
            )
            QMessageBox.information(self, "Thành công", f"Đã lưu huyết áp {int(sys_v)}/{int(dia_v)} mmHg thành công lúc {format_display_dt(meas_iso)}!")
            self._reset_time_to_now()
            self.refresh_data()
            self.measurement_saved.emit()
        except Exception as e:
            QMessageBox.critical(self, "Lỗi nhập", str(e))

    def _on_save_vital(self) -> None:
        code = self.combo_vital.currentData()
        unit_map = {"heart_rate": "bpm", "spo2": "%", "temperature": "°C", "blood_glucose": "mg/dL"}
        unit = unit_map.get(code, "")
        try:
            val = float(self.txt_vital_val.text().strip())
        except ValueError:
            QMessageBox.warning(self, "Lỗi nhập", "Vui lòng nhập giá trị số hợp lệ.")
            return

        meas_iso = self._get_measured_at_iso()
        try:
            comp = MeasurementComponent(type_code=code, value_numeric=val, unit_code=unit)
            self.service.record_measurement(
                patient_id=self.patient_id,
                entered_by_user_id=self.actor_user_id,
                components=[comp],
                measured_at=meas_iso,
            )
            QMessageBox.information(self, "Thành công", f"Đã lưu chỉ số {val} {unit} thành công lúc {format_display_dt(meas_iso)}!")
            self._reset_time_to_now()
            self.refresh_data()
            self.measurement_saved.emit()
        except Exception as e:
            QMessageBox.critical(self, "Lỗi nhập", str(e))

    def _on_save_symptom(self) -> None:
        text = self.txt_symptom.text().strip()
        if not text:
            QMessageBox.warning(self, "Thiếu thông tin", "Vui lòng nhập mô tả triệu chứng.")
            return

        meas_iso = self._get_measured_at_iso()
        try:
            self.service.record_symptom(
                patient_id=self.patient_id,
                reported_by_user_id=self.actor_user_id,
                text=text,
                discomfort_level=self.spin_discomfort.value(),
                reported_at=meas_iso,
            )
            QMessageBox.information(self, "Thành công", "Đã gửi triệu chứng thành công!")
            self.txt_symptom.clear()
            self._reset_time_to_now()
            self.refresh_data()
        except Exception as e:
            QMessageBox.critical(self, "Lỗi", str(e))

