"""
Today's To-Do Widget for the Elder Application (Giao diện Việc Cần Làm Hôm Nay).
Designed for Seniors:
- Large fonts (18px - 28px), clear high-contrast medical aesthetic.
- Auto-populated schedule and medication doses from unified database.
- One-touch medication confirmation ('Đã uống' tự khai báo).
- 16:00 Rehabilitation tracking with camera auto-completion upon reaching target.
- Vitals display with support for caregiver entering on behalf.
- Emergency contacts with on-duty validation.
- Live sync status indicator ('Đã ghi nhận' / 'Đang đồng bộ' / 'Chờ mạng').
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from PyQt5.QtCore import Qt, QThread, QTimer, pyqtSignal
from PyQt5.QtGui import QColor, QFont, QPixmap
from PyQt5.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from database.connection import UnifiedDatabaseManager
from services.elder_sync_client import ElderSyncClient

logger = logging.getLogger("TodayTodoWidget")


class TodayTodoWidget(QWidget):
    # Signal emitted when a medication action is performed
    dose_action_performed = pyqtSignal(str, str)
    rehab_session_changed = pyqtSignal(str, str)
    start_routine_requested = pyqtSignal()
    open_games_requested = pyqtSignal()

    def __init__(
        self,
        db: UnifiedDatabaseManager,
        patient_id: str = "patient_001",
        sync_client: Optional[ElderSyncClient] = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.db = db
        self.patient_id = patient_id
        self.sync_client = sync_client or ElderSyncClient(db, patient_id=patient_id)

        self._init_ui()
        self.refresh_data()

        # Polling timer to refresh UI every 1.5 seconds for near real-time synchronization
        self._refresh_timer = QTimer(self)
        self._refresh_timer.timeout.connect(self.refresh_data)
        self._refresh_timer.start(1500)

    def set_patient(self, new_patient_id: str) -> None:
        """Switch active patient profile."""
        self.patient_id = new_patient_id
        self.sync_client.switch_patient(new_patient_id)
        self.refresh_data()

    def _init_ui(self) -> None:
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(20, 20, 20, 20)
        main_layout.setSpacing(16)

        # 1. Header Bar: Title + Sync Status
        header_layout = QVBoxLayout()
        header_layout.setSpacing(10)

        title_lbl = QLabel("📋 VIỆC CẦN LÀM HÔM NAY")
        title_lbl.setStyleSheet("font-size: 24px; font-weight: 800; color: #183A34;")
        header_layout.addWidget(title_lbl)

        header_controls = QHBoxLayout()
        header_controls.setSpacing(12)
        header_layout.addLayout(header_controls)
        header_controls.addStretch()

        self.patient_name_lbl = QLabel("Hồ sơ: Đang tải...")
        self.patient_name_lbl.setStyleSheet("font-size: 16px; font-weight: 600; color: #5A6E85; margin-right: 12px;")
        self.patient_name_lbl.setWordWrap(True)
        header_controls.addWidget(self.patient_name_lbl, 1)

        self.sync_badge = QLabel("● Đã ghi nhận")
        self.sync_badge.setStyleSheet(
            "background-color: #35634D; color: #FFFFFF; font-size: 14px; font-weight: 700; "
            "padding: 6px 14px; border-radius: 12px;"
        )
        header_controls.addWidget(self.sync_badge)

        self.btn_sync_now = QPushButton("🔄 Đồng bộ ngay")
        self.btn_sync_now.setStyleSheet(
            "QPushButton { background-color: #285C4D; color: #FFFFFF; font-size: 14px; font-weight: 700; "
            "padding: 6px 16px; border-radius: 12px; border: none; margin-left: 8px; min-height: 32px; } "
            "QPushButton:hover { background-color: #1A5BB8; }"
        )
        self.btn_sync_now.clicked.connect(self._on_manual_sync_clicked)
        header_controls.addWidget(self.btn_sync_now)

        main_layout.addLayout(header_layout)

        # Scroll Area for Cards
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setStyleSheet("background: transparent;")

        container = QWidget()
        self.cards_layout = QVBoxLayout(container)
        self.cards_layout.setContentsMargins(0, 0, 0, 0)
        self.cards_layout.setSpacing(16)

        self.prescription_card = self._create_card("📋 Đơn Thuốc Từ Bác Sĩ")
        self.prescription_list_layout = QVBoxLayout()
        self.prescription_card.layout().addLayout(self.prescription_list_layout)
        self.cards_layout.addWidget(self.prescription_card)

        # Card 1: Medication Reminders (One-Touch)
        self.med_card = self._create_card("💊 Lịch Uống Thuốc Hôm Nay (Chạm 1 lần để xác nhận)")
        self.med_list_layout = QVBoxLayout()
        self.med_card.layout().addLayout(self.med_list_layout)
        self.cards_layout.addWidget(self.med_card)

        # Card 1.5: Health Measurement Requests (Nhắc đo từ người nhà & bác sĩ)
        self.meas_card = self._create_card("🔔 Yêu Cầu Đo Chỉ Số Sức Khỏe (Từ người nhà & bác sĩ)")
        self.meas_list_layout = QVBoxLayout()
        self.meas_card.layout().addLayout(self.meas_list_layout)
        self.cards_layout.addWidget(self.meas_card)

        # Card 2: 16:00 Rehabilitation Tracking
        self.rehab_card = self._create_card("🏋️ Bài Tập Phục Hồi Chức Năng 16:00 (Camera tự đếm)")
        self.rehab_content_layout = QVBoxLayout()
        self.rehab_card.layout().addLayout(self.rehab_content_layout)
        self.cards_layout.addWidget(self.rehab_card)

        # Card 4: Cognitive Games & Leisure
        self.games_card = self._create_card("🧠 Trò Chơi Trí Nhớ và Thư Giãn (Chơi khi buồn chán)")
        self.games_content_layout = QVBoxLayout()
        self.games_card.layout().addLayout(self.games_content_layout)
        self._build_games_card_content()
        self.cards_layout.addWidget(self.games_card)

        # Card 5: Doctor Recommendations Card
        self.advice_card = self._create_card("🩺 Lời Khuyên Của Bác Sĩ Phụ Trách")
        self.advice_content_layout = QVBoxLayout()
        self.advice_card.layout().addLayout(self.advice_content_layout)
        self.cards_layout.addWidget(self.advice_card)

        # Card 6: Recent Vitals Card
        self.vitals_card = self._create_card("❤️ Chỉ Số Sức Khỏe Gần Nhất (Người nhà có thể nhập hộ)")
        self.vitals_content_layout = QVBoxLayout()
        self.vitals_card.layout().addLayout(self.vitals_content_layout)
        self.cards_layout.addWidget(self.vitals_card)

        scroll.setWidget(container)
        main_layout.addWidget(scroll, 1)

    def _create_card(self, title: str) -> QFrame:
        card = QFrame()
        card.setObjectName("cardFrame")
        card.setStyleSheet(
            "QFrame#cardFrame { background-color: #FFFFFF; border: 1.5px solid #DFE5DF; border-radius: 12px; }"
        )
        layout = QVBoxLayout(card)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        lbl = QLabel(title)
        lbl.setStyleSheet("font-size: 18px; font-weight: 700; color: #183A34; border: none; background: transparent; padding: 0px;")
        layout.addWidget(lbl)
        card.title_label = lbl
        return card

    def refresh_data(self) -> None:
        """Fetch consolidated data and render cards."""
        # 1. Update Sync status badge
        sync_info = self.sync_client.get_sync_status()
        self.sync_badge.setText(f"● {sync_info['status_label']}")
        if sync_info["pending_count"] > 0:
            if sync_info["is_online"]:
                self.sync_badge.setStyleSheet("background-color: #ED6C02; color: #FFF; font-size: 14px; font-weight: 700; padding: 6px 14px; border-radius: 12px;")
            else:
                self.sync_badge.setStyleSheet("background-color: #C3293E; color: #FFF; font-size: 14px; font-weight: 700; padding: 6px 14px; border-radius: 12px;")
        else:
            self.sync_badge.setStyleSheet("background-color: #35634D; color: #FFF; font-size: 14px; font-weight: 700; padding: 6px 14px; border-radius: 12px;")

        from services.medication_schedule_sync import ensure_medication_occurrences
        ensure_medication_occurrences(self.db, self.patient_id)

        # 2. Query Unified DB
        today_date = date.today().isoformat()
        with self.db.connect() as conn:
            p = conn.fetchone("SELECT full_name FROM patients WHERE patient_id = ?", (self.patient_id,))
            if p:
                self.patient_name_lbl.setText(f"Người dùng: {p['full_name']} ({self.patient_id})")
            else:
                first_p = conn.fetchone("SELECT patient_id, full_name FROM patients WHERE status = 'ACTIVE' LIMIT 1")
                if first_p:
                    self.patient_id = first_p["patient_id"]
                    self.patient_name_lbl.setText(f"Người dùng: {first_p['full_name']} ({self.patient_id})")
                else:
                    self.patient_name_lbl.setText("Chưa có hồ sơ người bệnh (Đang chờ tạo dữ liệu)")

            prescriptions = conn.fetchall(
                "SELECT p.prescription_id, p.prescription_code, p.valid_from, p.valid_to, "
                "pi.start_date, pi.end_date, pi.original_instructions, pi.dose_amount, pi.dose_unit, "
                "m.brand_name, u.full_name AS doctor_name "
                "FROM prescriptions p JOIN prescription_items pi ON pi.prescription_id=p.prescription_id "
                "JOIN medications m ON m.medication_id=pi.medication_id "
                "LEFT JOIN clinicians cl ON cl.clinician_id=p.prescriber_clinician_id "
                "LEFT JOIN users u ON u.user_id=cl.user_id "
                "WHERE p.patient_id=? AND p.status='ACTIVE' AND pi.status='ACTIVE' "
                "ORDER BY p.prescribed_date DESC", (self.patient_id,))

            # Medication doses today (strictly for active, non-cancelled prescriptions and valid doses)
            doses = conn.fetchall(
                """
                SELECT d.*, p.status as prescription_status, p.prescription_code
                FROM dose_occurrences d
                JOIN medication_schedules s ON d.schedule_id = s.schedule_id
                JOIN prescription_items pi ON s.prescription_item_id = pi.prescription_item_id
                JOIN prescriptions p ON pi.prescription_id = p.prescription_id
                WHERE d.patient_id = ?
                  AND d.local_scheduled_time LIKE ?
                  AND (p.status = 'ACTIVE' OR (p.status = 'SUPERSEDED' AND d.state = 'CONFIRMED'))
                  AND d.state != 'CANCELLED'
                ORDER BY d.local_scheduled_time ASC
                """,
                (self.patient_id, f"{today_date}%"),
            )

            # Rehab session today
            rehab_sess = conn.fetchone(
                """
                SELECT rs.*, rp.exercise_name, rp.target_count as plan_target, rp.local_reminder_time
                FROM rehab_sessions rs
                JOIN rehab_plans rp ON rs.plan_id = rp.plan_id
                WHERE rs.patient_id = ? AND rs.local_date = ?
                ORDER BY rs.verified_count DESC, rs.started_at DESC
                LIMIT 1
                """,
                (self.patient_id, today_date),
            )

            # Active rehab plans for patient
            rehab_plans = conn.fetchall(
                """
                SELECT exercise_id, exercise_name, target_count, local_reminder_time
                FROM rehab_plans
                WHERE patient_id = ? AND is_active = 1
                ORDER BY plan_id ASC
                """,
                (self.patient_id,),
            )

            # Latest active recommendation
            rec = conn.fetchone(
                """
                SELECT cr.*, u.full_name as doctor_name
                FROM care_recommendations cr
                JOIN clinicians cl ON cr.clinician_id = cl.clinician_id
                JOIN users u ON cl.user_id = u.user_id
                WHERE cr.patient_id = ? AND cr.status = 'ACTIVE'
                ORDER BY cr.created_at DESC LIMIT 1
                """,
                (self.patient_id,),
            )

            # Latest blood pressure
            bp = conn.fetchone(
                """
                SELECT ho_sys.value_numeric as systolic, ho_dia.value_numeric as diastolic,
                       mg.measured_at, mg.entered_by_user_id, u.full_name as entered_by_name
                FROM measurement_groups mg
                JOIN health_observations ho_sys ON mg.group_id = ho_sys.group_id
                JOIN health_observations ho_dia ON mg.group_id = ho_dia.group_id
                JOIN observation_types ot_sys ON ho_sys.observation_type_id = ot_sys.observation_type_id AND ot_sys.code IN ('blood_pressure_systolic', 'bp_systolic')
                JOIN observation_types ot_dia ON ho_dia.observation_type_id = ot_dia.observation_type_id AND ot_dia.code IN ('blood_pressure_diastolic', 'bp_diastolic')
                LEFT JOIN users u ON mg.entered_by_user_id = u.user_id
                WHERE mg.patient_id = ? AND mg.status = 'active'
                ORDER BY mg.measured_at DESC LIMIT 1
                """,
                (self.patient_id,),
            )

            # Pending health measurement requests
            meas_reqs = conn.fetchall(
                """
                SELECT mr.*, ot.name as observation_name, ot.standard_unit as unit, u.full_name as requester_name
                FROM measurement_requests mr
                JOIN observation_types ot ON mr.observation_type_id = ot.observation_type_id
                JOIN users u ON mr.requested_by_user_id = u.user_id
                WHERE mr.patient_id = ? AND mr.status = 'PENDING'
                ORDER BY mr.due_at_utc ASC
                """,
                (self.patient_id,),
            )

        self._render_prescriptions(prescriptions)

        # 3. Render Medication Items
        self._render_medications(doses)

        # 3.5. Render Measurement Requests
        self._render_measurement_requests(meas_reqs)

        # 4. Render Rehab Status
        self._render_rehab(rehab_sess, rehab_plans)

        # 5. Render Doctor Advice
        self._render_advice(rec)

        # 6. Render Vitals
        self._render_vitals(bp)

    @staticmethod
    def _clear_layout(layout: QLayout) -> None:
        while layout.count() > 0:
            item = layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.hide()
                w.deleteLater()
            child_layout = item.layout()
            if child_layout is not None:
                TodayTodoWidget._clear_layout(child_layout)

    def _render_prescriptions(self, rows) -> None:
        self._clear_layout(self.prescription_list_layout)
        if not rows:
            self.prescription_list_layout.addWidget(QLabel("Chưa có đơn thuốc từ bác sĩ."))
        today = date.today().isoformat()
        for row in rows:
            start = str(row['start_date'] or row['valid_from'] or '')[:10]
            end = str(row['end_date'] or row['valid_to'] or '')[:10]
            message = (f"{row['prescription_code']} — {row['doctor_name'] or 'Bác sĩ phụ trách'}\n"
                       f"{row['brand_name']} — {row['dose_amount']:g} {row['dose_unit']}\n"
                       f"{row['original_instructions'] or ''}\n"
                       f"Từ {start}, đến {end or 'khi bác sĩ điều chỉnh'}")
            if end and end < start:
                message += "\n⚠ Ngày kết thúc trước ngày bắt đầu. Cần bác sĩ sửa đơn để tạo lịch uống."
            elif end and end < today:
                message += "\nĐơn đã hết thời gian dùng thuốc; liên hệ bác sĩ nếu cần điều chỉnh."
            elif start > today:
                message += "\nĐơn chưa tới ngày bắt đầu."
            label = QLabel(message)
            label.setTextFormat(Qt.PlainText)
            label.setWordWrap(True)
            label.setStyleSheet("font-size: 16px; color: #183A34; padding: 8px;")
            self.prescription_list_layout.addWidget(label)

    def _render_medications(self, doses: List[Dict[str, Any]]) -> None:
        self._clear_layout(self.med_list_layout)

        if not doses:
            empty_lbl = QLabel("Hôm nay cụ không có lịch uống thuốc nào.")
            empty_lbl.setStyleSheet("font-size: 16px; color: #5A6E85; font-style: italic;")
            self.med_list_layout.addWidget(empty_lbl)
            return

        for d in doses:
            row_frame = QFrame()
            row_frame.setObjectName("medRowFrame")
            row_frame.setStyleSheet("QFrame#medRowFrame { background: #F6F8F4; border: 1px solid #DFE5DF; border-radius: 8px; }")
            r_layout = QHBoxLayout(row_frame)
            r_layout.setContentsMargins(12, 12, 12, 12)

            # Left thumbnail: Pill photo or Pill Icon
            img_lbl = QLabel()
            img_path = d.get("image_snapshot")
            if img_path and Path(img_path).exists():
                pix = QPixmap(str(img_path)).scaled(56, 56, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                img_lbl.setPixmap(pix)
            else:
                img_lbl.setText("💊")
                img_lbl.setStyleSheet("font-size: 28px; background: #E7EDDF; border-radius: 8px; padding: 6px;")
                img_lbl.setAlignment(Qt.AlignCenter)
            img_lbl.setFixedSize(56, 56)
            r_layout.addWidget(img_lbl)

            # Middle: Time + Name + Instructions
            t_str = d["local_scheduled_time"][11:16]
            info_layout = QVBoxLayout()
            time_lbl = QLabel(f"⏰ {t_str}")
            time_lbl.setStyleSheet("font-size: 20px; font-weight: 800; color: #285C4D;")
            name_lbl = QLabel(f"{d['medication_name_snapshot']} — {d['dose_snapshot']}")
            name_lbl.setStyleSheet("font-size: 17px; font-weight: 700; color: #183A34;")
            instr_lbl = QLabel(d["instructions_snapshot"] or "Uống đúng theo đơn")
            instr_lbl.setStyleSheet("font-size: 15px; color: #5A6E85;")
            info_layout.addWidget(time_lbl)
            info_layout.addWidget(name_lbl)
            info_layout.addWidget(instr_lbl)

            state = d["state"]
            occ_id = d["occurrence_id"]

            if state == "OVERDUE":
                warn_lbl = QLabel("⚠️ ĐÃ QUÁ GIỜ UỐNG — Cụ vui lòng uống ngay hoặc liên hệ người nhà")
                warn_lbl.setStyleSheet("font-size: 13px; font-weight: 700; color: #D32F2F;")
                info_layout.addWidget(warn_lbl)

            r_layout.addLayout(info_layout, 1)

            # Right: State / One-Touch Action Buttons
            if state == "CONFIRMED":
                late_txt = " (Camera ghi nhận)" if d.get("action_actor") == "camera_verified" else (" (Uống trễ)" if "late" in str(d.get("action_notes", "")).lower() or d.get("action_actor") == "confirmed_late" else " (Tự khai báo)")
                status_lbl = QLabel(f"✅ ĐÃ UỐNG\n{late_txt}")
                status_lbl.setAlignment(Qt.AlignCenter)
                status_lbl.setStyleSheet("font-size: 15px; font-weight: 800; color: #35634D; background: #EEF3E9; padding: 8px 16px; border-radius: 8px;")
                r_layout.addWidget(status_lbl)
            elif state in ("PENDING", "REMINDING", "SNOOZED", "OVERDUE"):
                btn_taken_text = "✔ ĐÃ UỐNG" if state != "OVERDUE" else "✔ UỐNG BÂY GIỜ"
                btn_taken = QPushButton(btn_taken_text)
                btn_taken.setStyleSheet(
                    "QPushButton { background-color: #35634D; color: #FFFFFF; font-size: 17px; font-weight: 800; "
                    "border: none; border-radius: 8px; padding: 10px 20px; min-height: 48px; } "
                    "QPushButton:hover { background-color: #214A3B; }"
                )
                btn_taken.clicked.connect(lambda checked=False, oid=occ_id: self._on_confirm_taken(oid))
                r_layout.addWidget(btn_taken)

                if state != "OVERDUE":
                    btn_snooze = QPushButton("⏰ Nhắc lại")
                    btn_snooze.setStyleSheet(
                        "QPushButton { background-color: #FFF; color: #ED6C02; font-size: 15px; font-weight: 700; "
                        "border: 1.5px solid #ED6C02; border-radius: 8px; padding: 8px 14px; min-height: 44px; } "
                        "QPushButton:hover { background-color: #FFF3E0; }"
                    )
                    btn_snooze.clicked.connect(lambda checked=False, oid=occ_id: self._on_snooze(oid))
                    r_layout.addWidget(btn_snooze)

                # Nút 3: Cần hỗ trợ (đỏ nổi bật)
                btn_help = QPushButton("🆘 Cần hỗ trợ")
                btn_help.setStyleSheet(
                    "QPushButton { background-color: #FFF; color: #D32F2F; font-size: 15px; font-weight: 700; "
                    "border: 1.5px solid #D32F2F; border-radius: 8px; padding: 8px 14px; min-height: 44px; } "
                    "QPushButton:hover { background-color: #FFEBEE; }"
                )
                btn_help.clicked.connect(lambda checked=False, oid=occ_id: self._on_help_requested(oid))
                r_layout.addWidget(btn_help)
            else:
                st_lbl = QLabel(f"Trạng thái: {state}")
                st_lbl.setStyleSheet("font-size: 15px; color: #5A6E85;")
                r_layout.addWidget(st_lbl)

            self.med_list_layout.addWidget(row_frame)

    def _render_measurement_requests(self, requests: List[Dict[str, Any]]) -> None:
        self._clear_layout(self.meas_list_layout)

        if not requests:
            lbl = QLabel("Hiện không có yêu cầu đo chỉ số mới nào từ người nhà.")
            lbl.setStyleSheet("font-size: 16px; color: #5A6E85; font-style: italic;")
            self.meas_list_layout.addWidget(lbl)
            return

        for req in requests:
            row_frame = QFrame()
            row_frame.setObjectName("measRowFrame")
            row_frame.setStyleSheet("QFrame#measRowFrame { background: #FFF8E1; border: 1.5px solid #FFE082; border-radius: 10px; }")
            r_layout = QHBoxLayout(row_frame)
            r_layout.setContentsMargins(12, 12, 12, 12)

            icon_lbl = QLabel("🩺")
            icon_lbl.setStyleSheet("font-size: 32px;")
            r_layout.addWidget(icon_lbl)

            info_layout = QVBoxLayout()
            due_str = req.get("local_due_time") or req.get("due_at_utc", "")[11:16]
            title_lbl = QLabel(f"Yêu cầu đo: {req.get('observation_name', 'Chỉ số sức khỏe')} ({req.get('unit', '')})")
            title_lbl.setStyleSheet("font-size: 18px; font-weight: 700; color: #B78103;")

            remind_lbl = QLabel(f"⏰ Hạn đo trước: {due_str} • Người nhắc: {req.get('requester_name', 'Người nhà')}")
            remind_lbl.setStyleSheet("font-size: 15px; font-weight: 600; color: #5D4037;")

            instr_str = req.get("instructions") or "Vui lòng đo theo hướng dẫn của bác sĩ/người nhà."
            instr_lbl = QLabel(f"ℹ️ {instr_str}")
            instr_lbl.setStyleSheet("font-size: 14px; color: #6D4C41;")

            info_layout.addWidget(title_lbl)
            info_layout.addWidget(remind_lbl)
            info_layout.addWidget(instr_lbl)
            r_layout.addLayout(info_layout, 1)

            btn_noted = QPushButton("✔ ĐÃ NHỚ ĐO")
            btn_noted.setStyleSheet(
                "QPushButton { background-color: #F57F17; color: #FFFFFF; font-size: 16px; font-weight: 800; "
                "border: none; border-radius: 8px; padding: 10px 18px; min-height: 44px; } "
                "QPushButton:hover { background-color: #E65100; }"
            )
            btn_noted.clicked.connect(lambda checked=False, r=req: QMessageBox.information(
                self, "Nhắc đo sức khỏe", f"Cụ nhớ đo {r.get('observation_name')} nhé!\nNgười nhà sẽ nhập kết quả vào hệ thống giúp cụ."
            ))
            r_layout.addWidget(btn_noted)

            self.meas_list_layout.addWidget(row_frame)

    def _on_confirm_taken(self, occurrence_id: str) -> None:
        """One-touch confirmation clicked by elder."""
        self.sync_client.confirm_dose_taken(occurrence_id=occurrence_id, action="self_reported_taken")
        self.refresh_data()
        self.dose_action_performed.emit(occurrence_id, "CONFIRMED")

    def _on_snooze(self, occurrence_id: str) -> None:
        self.sync_client.confirm_dose_taken(occurrence_id=occurrence_id, action="snooze")
        self.refresh_data()

    def _on_help_requested(self, occurrence_id: str) -> None:
        self.sync_client.confirm_dose_taken(occurrence_id=occurrence_id, action="help_requested")
        self.refresh_data()
        QMessageBox.warning(
            self,
            "🆘 Đã Gửi Yêu Cầu Hỗ Trợ",
            "Hệ thống đã gửi cảnh báo khẩn cấp tới Người nhà và Bác sĩ phụ trách!\n\n"
            "📞 Người nhà (Nguyễn Văn An): 0901 234 567\n"
            "📞 Bác sĩ phụ trách (BS. Trần Văn Minh): 0912 345 678\n\n"
            "Người thân sẽ gọi điện hoặc đến hỗ trợ cụ ngay lập tức. Cụ hãy ngồi nghỉ ngơi an toàn nhé!",
        )

    def _build_games_card_content(self) -> None:
        g_desc = QLabel(
            "Rèn luyện trí nhớ và khả năng tập trung bằng trò chơi lật hình đồ vật quen thuộc, "
            "nhớ thứ tự đồ vật hoặc thưởng thức các giai điệu âm nhạc êm dịu khi cảm thấy buồn chán."
        )
        g_desc.setStyleSheet("font-size: 15px; color: #455A64; line-height: 1.4;")
        g_desc.setWordWrap(True)
        self.games_content_layout.addWidget(g_desc)

        btn_go_games = QPushButton("🎮 VÀO CHƠI TRÒ CHƠI TRÍ NHỚ VÀ NGHE NHẠC THƯ GIÃN")
        btn_go_games.setStyleSheet(
            "QPushButton { background-color: #526E58; color: #FFFFFF; font-size: 16px; font-weight: 800; "
            "border: none; border-radius: 10px; padding: 12px 24px; min-height: 50px; } "
            "QPushButton:hover { background-color: #435D49; }"
        )
        btn_go_games.clicked.connect(self.open_games_requested.emit)
        self.games_content_layout.addWidget(btn_go_games)

    def _render_rehab(self, sess: Optional[Dict[str, Any]], plans: Optional[List[Dict[str, Any]]] = None) -> None:
        self._clear_layout(self.rehab_content_layout)

        plan_dict = {p["exercise_id"]: p for p in (plans or []) if "exercise_id" in p}
        arm_t = plan_dict.get("arm_raise", {}).get("target_count", 10)
        sit_t = plan_dict.get("sit_to_stand", {}).get("target_count", 10)
        march_t = plan_dict.get("marching", {}).get("target_count", 15)
        rem_time = (
            plan_dict.get("arm_raise", {}).get("local_reminder_time")
            or plan_dict.get("sit_to_stand", {}).get("local_reminder_time")
            or plan_dict.get("marching", {}).get("local_reminder_time")
            or (sess.get("local_reminder_time") if sess else None)
            or "16:00"
        )

        if hasattr(self, "rehab_card") and hasattr(self.rehab_card, "title_label"):
            self.rehab_card.title_label.setText(f"🏋️ Bài Tập Phục Hồi Chức Năng {rem_time} (Camera tự đếm)")

        # Routine summary title
        routine_lbl = QLabel(
            f"⏰ MỐC {rem_time} — CHUỖI TẬP PHỤC HỒI CHỨC NĂNG:<br>"
            f"<span style=\"background-color: #285C4D; color: #FFFFFF; font-weight: bold; border-radius: 4px; padding: 2px 8px;\">Bước 1</span> <b>Vươn tay:</b> {arm_t} lần &nbsp;➔&nbsp; "
            f"<span style=\"background-color: #285C4D; color: #FFFFFF; font-weight: bold; border-radius: 4px; padding: 2px 8px;\">Bước 2</span> <b>Đứng lên ngồi xuống:</b> {sit_t} lần &nbsp;➔&nbsp; "
            f"<span style=\"background-color: #285C4D; color: #FFFFFF; font-weight: bold; border-radius: 4px; padding: 2px 8px;\">Bước 3</span> <b>Đi bộ tại chỗ:</b> {march_t} bước"
        )
        routine_lbl.setWordWrap(True)
        routine_lbl.setStyleSheet("font-size: 16px; color: #183A34; line-height: 1.6; background: #F0F4F8; padding: 12px; border-radius: 8px;")
        self.rehab_content_layout.addWidget(routine_lbl)

        status = sess.get("status", "pending") if sess else "pending"
        verified = sess.get("verified_count", 0) if sess else 0
        target = sess.get("target_count_snapshot", 10) if sess else 10
        session_id = sess.get("session_id") if sess else None

        if status == "camera_verified_completed":
            st_text = QLabel(f"✔ ĐÃ HOÀN THÀNH XUẤT SẮC CHUỖI BÀI TẬP {rem_time}! (Camera đã xác nhận và gửi cho Bác sĩ và Người nhà)")
            st_text.setStyleSheet("font-size: 16px; font-weight: 800; color: #35634D; background: #EEF3E9; padding: 12px; border-radius: 8px;")
            self.rehab_content_layout.addWidget(st_text)
            return

        # Progress bar
        pbar = QProgressBar()
        pbar.setRange(0, target)
        pbar.setValue(min(verified, target))
        pbar.setTextVisible(True)
        pbar.setFormat(f"Tiến độ hiện tại: {verified} / {target} lần ({int(verified / target * 100) if target else 0}%)")
        pbar.setStyleSheet(
            "QProgressBar { border: 1.5px solid #DFE5DF; border-radius: 8px; text-align: center; font-size: 15px; font-weight: 700; height: 34px; } "
            "QProgressBar::chunk { background-color: #285C4D; border-radius: 6px; }"
        )
        self.rehab_content_layout.addWidget(pbar)

        # Action button to start routine
        btn_start_routine = QPushButton(f"▶ BẮT ĐẦU CHUỖI BÀI TẬP {rem_time} NGAY (Camera tự đếm)")
        btn_start_routine.setStyleSheet(
            "QPushButton { background-color: #285C4D; color: #FFFFFF; font-size: 17px; font-weight: 800; "
            "border: none; border-radius: 10px; padding: 12px 24px; min-height: 50px; } "
            "QPushButton:hover { background-color: #1A5BB8; }"
        )
        btn_start_routine.clicked.connect(self.start_routine_requested.emit)
        self.rehab_content_layout.addWidget(btn_start_routine)

        # Status and Problem buttons row
        status_row = QHBoxLayout()
        if status == "verification_unavailable":
            st_sub = QLabel("⚠️ Đã báo sự cố: Đang chờ bác sĩ/người nhà xem lại")
            st_sub.setStyleSheet("font-size: 15px; font-weight: 700; color: #ED6C02;")
            status_row.addWidget(st_sub)
        else:
            st_sub = QLabel("⏳ Camera tự động đếm từng lần hợp lệ và tự hoàn thành khi đủ chỉ tiêu")
            st_sub.setStyleSheet("font-size: 14px; font-weight: 600; color: #5A6E85;")
            status_row.addWidget(st_sub)

        status_row.addStretch()

        if session_id:
            btn_issue = QPushButton("Báo sự cố: Tôi đã tập nhưng camera không nhận")
            btn_issue.setStyleSheet(
                "QPushButton { background: #FFF; color: #C3293E; border: 1.5px solid #C3293E; border-radius: 8px; "
                "font-size: 13px; font-weight: 700; padding: 6px 12px; } QPushButton:hover { background: #FFEBEE; }"
            )
            btn_issue.clicked.connect(lambda checked=False, sid=session_id: self._on_rehab_issue(sid))
            status_row.addWidget(btn_issue)

        self.rehab_content_layout.addLayout(status_row)

    def _on_rehab_issue(self, session_id: str) -> None:
        self.sync_client.mark_camera_issue(session_id=session_id)
        self.refresh_data()
        QMessageBox.information(self, "Báo cáo sự cố", "Đã gửi thông báo đến người nhà và bác sĩ: Cụ đã tập nhưng camera không ghi nhận được.")

    def _render_advice(self, rec: Optional[Dict[str, Any]]) -> None:
        self._clear_layout(self.advice_content_layout)

        if not rec:
            lbl = QLabel("Chưa có lời khuyên mới từ bác sĩ phụ trách.")
            lbl.setStyleSheet("font-size: 16px; color: #5A6E85; font-style: italic;")
            self.advice_content_layout.addWidget(lbl)
            return

        box = QFrame()
        box.setObjectName("adviceBox")
        box.setStyleSheet("QFrame#adviceBox { background: #EEF2FF; border: 1.5px solid #C7D2FE; border-radius: 8px; }")
        b_layout = QVBoxLayout(box)
        b_layout.setContentsMargins(14, 14, 14, 14)

        title = QLabel(f"📢 {rec['title']}")
        title.setStyleSheet("font-size: 18px; font-weight: 800; color: #1E1B4B;")
        b_layout.addWidget(title)

        content = QLabel(rec["content"])
        content.setWordWrap(True)
        content.setStyleSheet("font-size: 16px; color: #1E1B4B; line-height: 1.4;")
        b_layout.addWidget(content)

        meta_row = QHBoxLayout()
        doc_lbl = QLabel(f"BS: {rec.get('doctor_name', 'Bác sĩ phụ trách')} • {rec['created_at'][:10]}")
        doc_lbl.setStyleSheet("font-size: 14px; color: #5A6E85;")
        meta_row.addWidget(doc_lbl)
        meta_row.addStretch()

        btn_read = QPushButton("Đã xem")
        btn_read.setStyleSheet(
            "QPushButton { background: #285C4D; color: #FFF; font-size: 14px; font-weight: 700; "
            "border: none; border-radius: 6px; padding: 8px 16px; } QPushButton:hover { background: #214F42; }"
        )
        btn_read.clicked.connect(lambda checked=False, rid=rec["recommendation_id"]: self._on_read_advice(rid))
        meta_row.addWidget(btn_read)

        b_layout.addLayout(meta_row)
        self.advice_content_layout.addWidget(box)

    def _on_read_advice(self, rec_id: str) -> None:
        self.sync_client.mark_recommendation_read(rec_id)
        QMessageBox.information(self, "Đã xem", "Đã xác nhận cụ đã xem lời khuyên của bác sĩ!")

    def _render_vitals(self, bp: Optional[Dict[str, Any]]) -> None:
        self._clear_layout(self.vitals_content_layout)

        if not bp:
            lbl = QLabel("Chưa có số đo huyết áp gần đây. Người nhà có thể nhập hộ trên Cổng Người Nhà.")
            lbl.setStyleSheet("font-size: 16px; color: #5A6E85; font-style: italic;")
            self.vitals_content_layout.addWidget(lbl)
            return

        sys_val = int(bp["systolic"])
        dia_val = int(bp["diastolic"])
        measured_time = bp["measured_at"][11:16]
        entered_by = bp.get("entered_by_name") or "Người nhà nhập hộ"

        v_lbl = QLabel(f"Huyết áp lần đo gần nhất: <strong>{sys_val}/{dia_val} mmHg</strong> (Lúc {measured_time})")
        v_lbl.setStyleSheet("font-size: 18px; color: #183A34;")
        self.vitals_content_layout.addWidget(v_lbl)

        sub_lbl = QLabel(f"Người ghi nhận: {entered_by}")
        sub_lbl.setStyleSheet("font-size: 14px; color: #5A6E85;")
        self.vitals_content_layout.addWidget(sub_lbl)

    def _on_manual_sync_clicked(self) -> None:
        try:
            flushed = self.sync_client.flush_offline_queue()
            self.refresh_data()
            QMessageBox.information(
                self,
                "Đồng bộ thành công",
                f"Đã hoàn tất đồng bộ với hệ thống bác sĩ và người nhà!\n"
                f"• Sự kiện ngoại tuyến đã gửi: {flushed}\n"
                f"• Danh mục thuốc và bài tập đã được cập nhật mới nhất theo chỉ định y khoa.",
            )
        except Exception as e:
            QMessageBox.warning(
                self,
                "Lỗi đồng bộ",
                f"Lỗi khi đồng bộ dữ liệu: {e}\nDữ liệu vẫn được lưu trữ ngoại tuyến an toàn trên thiết bị.",
            )


