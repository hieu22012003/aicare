"""
Elderly User Interface (Giao diện Người cao tuổi) for Medication Adherence Module.
Features large accessible cards, clear contrast, product strength vs intake dose distinction,
and 3 prominent action buttons: 'Đã uống' (self-reported), 'Nhắc lại' (snooze), and 'Cần hỗ trợ' (SOS).
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Dict, List, Optional

from features.medication.adherence.domain.contracts import IPatientIdentityAdapter
from features.medication.adherence.domain.models import DoseOccurrence, DoseState
from features.medication.adherence.services.adherence_service import AdherenceService

logger = logging.getLogger("ElderlyUI")

try:
    from PyQt5.QtCore import Qt, QTimer
    from PyQt5.QtGui import QColor, QFont, QPixmap
    from PyQt5.QtWidgets import (
        QApplication,
        QDialog,
        QFrame,
        QHBoxLayout,
        QLabel,
        QMessageBox,
        QPushButton,
        QScrollArea,
        QVBoxLayout,
        QWidget,
    )
    PYQT_AVAILABLE = True
except ImportError:
    PYQT_AVAILABLE = False
    QWidget = object


class ElderlyPresenter:
    """Headless Presenter containing 100% of the elderly UI business and interaction logic."""

    def __init__(
        self,
        service: AdherenceService,
        identity_adapter: IPatientIdentityAdapter,
        on_refresh: Optional[Callable[[], None]] = None,
    ) -> None:
        self.service = service
        self.identity_adapter = identity_adapter
        self.on_refresh = on_refresh
        self.active_patient_id: Optional[str] = identity_adapter.get_current_patient_id()
        self.cached_doses: List[DoseOccurrence] = []
        self.selected_dose: Optional[DoseOccurrence] = None

        # Subscribe to identity changes for anti-cross-contamination
        self.identity_adapter.subscribe_identity_changed(self._handle_identity_changed)
        self.reload_doses()

    def _handle_identity_changed(self, old_id: Optional[str], new_id: Optional[str]) -> None:
        """Purge old patient data immediately when identity changes to prevent leakage."""
        logger.info("ElderlyPresenter: Purging data for patient '%s', switching to '%s'", old_id, new_id)
        self.active_patient_id = new_id
        self.cached_doses.clear()
        self.selected_dose = None
        self.reload_doses()
        if self.on_refresh:
            self.on_refresh()

    def reload_doses(self) -> List[DoseOccurrence]:
        if not self.active_patient_id:
            self.cached_doses = []
            self.selected_dose = None
            return []
        self.cached_doses = self.service.get_elderly_doses(self.active_patient_id)
        if self.selected_dose:
            # Re-select matching occurrence
            match = next((d for d in self.cached_doses if d.occurrence_id == self.selected_dose.occurrence_id), None)
            self.selected_dose = match or (self.cached_doses[0] if self.cached_doses else None)
        elif self.cached_doses:
            self.selected_dose = self.cached_doses[0]
        return self.cached_doses

    def on_take_clicked(self, occurrence_id: str) -> Dict[str, Any]:
        if not self.active_patient_id:
            raise ValueError("Chưa xác nhận danh tính người cao tuổi")
        result = self.service.record_take(
            occurrence_id=occurrence_id,
            patient_id=self.active_patient_id,
            actor="Người cao tuổi",
        )
        self.reload_doses()
        if self.on_refresh:
            self.on_refresh()
        return result

    def on_snooze_clicked(self, occurrence_id: str, minutes: int = 10) -> Dict[str, Any]:
        if not self.active_patient_id:
            raise ValueError("Chưa xác nhận danh tính người cao tuổi")
        result = self.service.record_snooze(
            occurrence_id=occurrence_id,
            patient_id=self.active_patient_id,
            snooze_minutes=minutes,
        )
        self.reload_doses()
        if self.on_refresh:
            self.on_refresh()
        return result

    def on_help_clicked(self, occurrence_id: str, details: str = "") -> Dict[str, Any]:
        if not self.active_patient_id:
            raise ValueError("Chưa xác nhận danh tính người cao tuổi")
        result = self.service.record_help_request(
            occurrence_id=occurrence_id,
            patient_id=self.active_patient_id,
            details=details,
        )
        self.reload_doses()
        if self.on_refresh:
            self.on_refresh()
        return result


if PYQT_AVAILABLE:
    class ElderlyWidget(QWidget):
        """Accessible PyQt5 Widget for Elderly medication reminder and confirmation."""

        def __init__(
            self,
            service: AdherenceService,
            identity_adapter: IPatientIdentityAdapter,
            parent: Optional[QWidget] = None,
        ) -> None:
            super().__init__(parent)
            self.presenter = ElderlyPresenter(service, identity_adapter, on_refresh=self.refresh_ui)
            self._init_ui()
            self.refresh_ui()

        def _init_ui(self) -> None:
            layout = QVBoxLayout(self)
            layout.setContentsMargins(16, 16, 16, 16)
            layout.setSpacing(12)

            # Header & Status
            self.header_card = QFrame()
            self.header_card.setStyleSheet("background-color: #EAF2FE; border-radius: 12px; padding: 12px;")
            h_lay = QHBoxLayout(self.header_card)

            self.lbl_patient_info = QLabel("👤 Đang quét nhận diện khuôn mặt...")
            self.lbl_patient_info.setStyleSheet("font-size: 20px; font-weight: bold; color: #14233F;")
            h_lay.addWidget(self.lbl_patient_info)
            h_lay.addStretch()

            self.lbl_notice = QLabel("Lịch hôm nay • Nhắc thuốc khi đến giờ")
            self.lbl_notice.setStyleSheet("font-size: 16px; color: #52627B;")
            h_lay.addWidget(self.lbl_notice)
            layout.addWidget(self.header_card)

            # Scroll Area for Medication Cards
            self.scroll = QScrollArea()
            self.scroll.setWidgetResizable(True)
            self.scroll.setStyleSheet("border: none; background: transparent;")

            self.cards_container = QWidget()
            self.cards_layout = QVBoxLayout(self.cards_container)
            self.cards_layout.setSpacing(16)
            self.scroll.setWidget(self.cards_container)

            layout.addWidget(self.scroll, 1)

            # Bottom Safety Disclaimer Banner
            banner = QLabel("⚠️ Xác nhận uống thuốc là tự khai báo. Nếu lỡ liều hoặc thắc mắc, vui lòng nhấn 'Cần hỗ trợ' để liên hệ người nhà.")
            banner.setWordWrap(True)
            banner.setStyleSheet("font-size: 15px; color: #7A4E00; background: #FFF8E6; padding: 10px; border-radius: 8px;")
            layout.addWidget(banner)

        def refresh_ui(self) -> None:
            """Synchronize presenter state into UI."""
            pid = self.presenter.active_patient_id
            if pid:
                patient = self.presenter.service.patient_repo.get_by_id(pid)
                p_name = patient.full_name if patient else pid
                self.lbl_patient_info.setText(f"👤 Người dùng: {p_name} (Mã: {pid})")
            else:
                self.lbl_patient_info.setText("⚪ Chưa phát hiện người dùng (Đang chờ camera hoặc chọn hồ sơ)")

            # Clear existing cards
            while self.cards_layout.count():
                child = self.cards_layout.takeAt(0)
                if child.widget():
                    child.widget().deleteLater()

            doses = self.presenter.cached_doses
            if not doses:
                lbl_empty = QLabel("Hiện không có liều thuốc nào cần uống hoặc đang chờ xác nhận.")
                lbl_empty.setStyleSheet("font-size: 18px; color: #52627B; padding: 30px; text-align: center;")
                lbl_empty.setAlignment(Qt.AlignCenter)
                self.cards_layout.addWidget(lbl_empty)
                return

            for dose in doses:
                card = self._create_dose_card(dose)
                self.cards_layout.addWidget(card)

            self.cards_layout.addStretch()

        def _create_dose_card(self, dose: DoseOccurrence) -> QFrame:
            card = QFrame()
            # Visual styling based on state
            if dose.state == DoseState.OVERDUE:
                border_color = "#C3293E"
                bg_color = "#FFF5F5"
            elif dose.state == DoseState.REMINDING:
                border_color = "#2878F0"
                bg_color = "#F0F6FF"
            elif dose.state == DoseState.CONFIRMED:
                border_color = "#167D62"
                bg_color = "#F2F9F6"
            else:
                border_color = "#DFE8F3"
                bg_color = "#FFFFFF"

            card.setStyleSheet(f"""
                QFrame {{
                    background-color: {bg_color};
                    border: 2.5px solid {border_color};
                    border-radius: 14px;
                    padding: 16px;
                }}
            """)

            v_box = QVBoxLayout(card)
            v_box.setSpacing(10)

            # Top Row: Time & State Badge
            top_row = QHBoxLayout()
            time_part = dose.local_scheduled_time.split("T")[-1][:5]
            lbl_time = QLabel(f"⏰ Giờ uống: {time_part}")
            lbl_time.setStyleSheet("font-size: 22px; font-weight: 800; color: #14233F;")
            top_row.addWidget(lbl_time)

            top_row.addStretch()
            state_text = {
                DoseState.PENDING: "Sắp tới",
                DoseState.REMINDING: "ĐẾN GIỜ UỐNG THUỐC",
                DoseState.SNOOZED: f"Đang hoãn nhắc ({dose.snooze_count} lần)",
                DoseState.CONFIRMED: "✓ ĐÃ XÁC NHẬN UỐNG",
                DoseState.OVERDUE: "⚠️ QUÁ HẠN CHƯA XÁC NHẬN",
            }.get(dose.state, str(dose.state.value))

            lbl_badge = QLabel(state_text)
            lbl_badge.setStyleSheet(f"font-size: 16px; font-weight: bold; color: {border_color};")
            top_row.addWidget(lbl_badge)
            v_box.addLayout(top_row)

            # Middle Row: Medication Info (Clear Separation: Product Strength vs Dose per intake!)
            lbl_med = QLabel(f"💊 {dose.medication_name_snapshot}")
            lbl_med.setStyleSheet("font-size: 24px; font-weight: 900; color: #0C1E3C;")
            v_box.addWidget(lbl_med)

            lbl_dose = QLabel(f"👉 Liều uống lần này: {dose.dose_snapshot}")
            lbl_dose.setStyleSheet("font-size: 20px; font-weight: bold; color: #2878F0;")
            v_box.addWidget(lbl_dose)

            if dose.instructions_snapshot:
                lbl_inst = QLabel(f"📝 Chỉ dẫn: {dose.instructions_snapshot}")
                lbl_inst.setWordWrap(True)
                lbl_inst.setStyleSheet("font-size: 16px; color: #4B5563;")
                v_box.addWidget(lbl_inst)

            # Action Buttons Row
            btn_row = QHBoxLayout()
            btn_row.setSpacing(12)

            btn_take = QPushButton("✓ ĐÃ UỐNG")
            btn_take.setStyleSheet("""
                QPushButton {
                    background-color: #167D62; color: white; font-size: 18px; font-weight: bold;
                    min-height: 48px; border-radius: 8px; padding: 8px 16px;
                }
                QPushButton:hover { background-color: #11634D; }
                QPushButton:disabled { background-color: #A3D4C7; }
            """)
            btn_take.setEnabled(dose.state != DoseState.CONFIRMED)
            btn_take.clicked.connect(lambda _, oid=dose.occurrence_id: self._on_take(oid))
            btn_row.addWidget(btn_take, 2)

            btn_snooze = QPushButton("⏳ Nhắc lại sau")
            btn_snooze.setStyleSheet("""
                QPushButton {
                    background-color: #E68A00; color: white; font-size: 17px; font-weight: bold;
                    min-height: 48px; border-radius: 8px; padding: 8px 14px;
                }
                QPushButton:hover { background-color: #BA6E00; }
                QPushButton:disabled { background-color: #F8D7A4; }
            """)
            btn_snooze.setEnabled(dose.state in (DoseState.REMINDING, DoseState.SNOOZED, DoseState.OVERDUE))
            btn_snooze.clicked.connect(lambda _, oid=dose.occurrence_id: self._on_snooze(oid))
            btn_row.addWidget(btn_snooze, 1)

            btn_help = QPushButton("🚨 CẦN HỖ TRỢ")
            btn_help.setStyleSheet("""
                QPushButton {
                    background-color: #C3293E; color: white; font-size: 17px; font-weight: bold;
                    min-height: 48px; border-radius: 8px; padding: 8px 14px;
                }
                QPushButton:hover { background-color: #A01F31; }
            """)
            btn_help.clicked.connect(lambda _, oid=dose.occurrence_id: self._on_help(oid))
            btn_row.addWidget(btn_help, 1)

            v_box.addLayout(btn_row)
            return card

        def _on_take(self, occurrence_id: str) -> None:
            reply = QMessageBox.question(
                self,
                "Xác nhận uống thuốc",
                "Bạn xác nhận là đã uống xong liều thuốc này?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.Yes,
            )
            if reply == QMessageBox.Yes:
                res = self.presenter.on_take_clicked(occurrence_id)
                QMessageBox.information(self, "Thông báo", res.get("message", "Đã xác nhận"))

        def _on_snooze(self, occurrence_id: str) -> None:
            try:
                res = self.presenter.on_snooze_clicked(occurrence_id, minutes=10)
                QMessageBox.information(self, "Nhắc lại sau", res.get("message", "Đã hoãn nhắc"))
            except ValueError as e:
                QMessageBox.warning(self, "Không thể hoãn", str(e))

        def _on_help(self, occurrence_id: str) -> None:
            res = self.presenter.on_help_clicked(occurrence_id)
            contact = res.get("caregiver_contact", {})
            msg = (
                f"{res.get('message', '')}\n\n"
                f"Người liên hệ: {contact.get('name', 'Người thân')} ({contact.get('phone', '')})\n"
                f"Hotline cấp cứu: {res.get('emergency_hotline', '115')}\n\n"
                "Vui lòng ngồi yên tĩnh nghỉ ngơi trong khi chờ người nhà liên hệ lại."
            )
            QMessageBox.critical(self, "Yêu cầu trợ giúp khẩn cấp", msg)
else:
    class ElderlyWidget:
        def __init__(self, *args, **kwargs):
            pass
