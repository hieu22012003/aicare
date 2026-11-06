"""
Adherence Service Application Layer for Medication Adherence Module.
Coordinates dose retrieval, user actions (Take, Snooze, Help), caregiver resolutions,
and strict validation against patient identity cross-contamination.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional
from zoneinfo import ZoneInfo

from features.medication.adherence.domain.contracts import (
    ICaregiverRepository,
    IMedicationRepository,
    INotificationGateway,
    IPatientRepository,
)
from features.medication.adherence.domain.models import (
    Alert,
    AlertStatus,
    AlertType,
    DeliveryChannel,
    DeliveryStatus,
    DoseEvent,
    DoseEventType,
    DoseOccurrence,
    DoseState,
    NotificationDelivery,
)

logger = logging.getLogger("AdherenceService")

UTC = timezone.utc


class AdherenceService:
    """High-level service coordinating business operations for UI views."""

    def __init__(
        self,
        med_repo: IMedicationRepository,
        patient_repo: IPatientRepository,
        caregiver_repo: ICaregiverRepository,
        notif_gateway: INotificationGateway,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.med_repo = med_repo
        self.patient_repo = patient_repo
        self.caregiver_repo = caregiver_repo
        self.notif_gateway = notif_gateway
        self.clock = clock

    def get_elderly_doses(self, patient_id: str) -> List[DoseOccurrence]:
        """
        Retrieve doses relevant to the elderly dashboard for a specific patient:
        Includes today's doses, and any currently active (REMINDING, OVERDUE, SNOOZED).
        """
        if not patient_id:
            return []

        patient = self.patient_repo.get_by_id(patient_id)
        if not patient:
            return []

        try:
            zone = ZoneInfo(patient.timezone)
        except Exception:
            zone = ZoneInfo("Asia/Ho_Chi_Minh")

        now = self.clock()
        today_local_str = now.astimezone(zone).strftime("%Y-%m-%d")

        # Fetch occurrences
        occurrences = self.med_repo.get_due_occurrences(patient_id=patient_id)
        relevant: List[DoseOccurrence] = []
        for occ in occurrences:
            if occ.state in (DoseState.CANCELLED, DoseState.MISSED):
                continue
            is_active_state = occ.state in (DoseState.REMINDING, DoseState.OVERDUE, DoseState.SNOOZED)
            is_today = occ.local_scheduled_time.startswith(today_local_str)
            if is_active_state or is_today:
                relevant.append(occ)

        return relevant

    def record_take(
        self,
        occurrence_id: str,
        patient_id: str,
        actor: str = "Người cao tuổi",
        idempotency_key: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Record self-reported dose intake.
        Idempotent: Multiple calls for the same occurrence return the existing confirmation.
        Returns safety confirmation banner text.
        """
        now = self.clock()
        now_iso = now.isoformat()
        key = idempotency_key or f"take:{occurrence_id}:{patient_id}"

        occ = self.med_repo.get_occurrence(occurrence_id)
        if not occ:
            raise ValueError(f"Không tìm thấy lần nhắc {occurrence_id}")

        if occ.patient_id != patient_id:
            raise PermissionError(f"Từ chối thao tác: Lần nhắc thuộc bệnh nhân khác ({occ.patient_id} != {patient_id})")

        # Idempotent return if already confirmed
        if occ.state == DoseState.CONFIRMED:
            return {
                "success": True,
                "already_confirmed": True,
                "occurrence": occ,
                "message": "Đã ghi nhận bạn xác nhận uống thuốc trước đó.",
            }

        # Update state
        updated_occ = self.med_repo.update_occurrence_state(
            occurrence_id=occurrence_id,
            new_state=DoseState.CONFIRMED,
            action_actor=actor,
            action_notes="Xác nhận tự khai báo",
        )

        # Audit event
        self.med_repo.record_event(
            DoseEvent(
                event_id=uuid.uuid4().hex,
                occurrence_id=occurrence_id,
                event_type=DoseEventType.SELF_REPORTED_TAKEN,
                timestamp_utc=now_iso,
                actor_id=patient_id,
                actor_role="patient",
                idempotency_key=key,
                metadata={"confirmation_type": "self_report", "actor": actor},
            )
        )

        logger.info("Recorded intake for occurrence %s by %s", occurrence_id, actor)
        return {
            "success": True,
            "already_confirmed": False,
            "occurrence": updated_occ,
            "message": "Đã ghi nhận bạn xác nhận uống thuốc. (Thông tin tự khai báo)",
        }

    def record_snooze(
        self,
        occurrence_id: str,
        patient_id: str,
        snooze_minutes: int = 10,
        max_snoozes: int = 3,
    ) -> Dict[str, Any]:
        """
        Snooze reminder for configured minutes.
        Enforces maximum snooze limit. Does NOT push back original grace deadline.
        """
        now = self.clock()
        now_iso = now.isoformat()

        occ = self.med_repo.get_occurrence(occurrence_id)
        if not occ:
            raise ValueError(f"Không tìm thấy lần nhắc {occurrence_id}")

        if occ.patient_id != patient_id:
            raise PermissionError("Lần nhắc không thuộc về bệnh nhân hiện tại")

        if occ.state in (DoseState.CONFIRMED, DoseState.SKIPPED, DoseState.MISSED, DoseState.CANCELLED):
            raise ValueError(f"Không thể hoãn lần nhắc đang ở trạng thái {occ.state.value}")

        if occ.snooze_count >= max_snoozes:
            raise ValueError(f"Đã đạt số lần hoãn tối đa ({max_snoozes} lần). Vui lòng uống thuốc hoặc nhờ hỗ trợ.")

        snoozed_until = (now + timedelta(minutes=snooze_minutes)).isoformat()
        updated_occ = self.med_repo.update_occurrence_state(
            occurrence_id=occurrence_id,
            new_state=DoseState.SNOOZED,
            action_actor=patient_id,
            snoozed_until_utc=snoozed_until,
        )

        self.med_repo.record_event(
            DoseEvent(
                event_id=uuid.uuid4().hex,
                occurrence_id=occurrence_id,
                event_type=DoseEventType.SNOOZED,
                timestamp_utc=now_iso,
                actor_id=patient_id,
                actor_role="patient",
                idempotency_key=f"snooze:{occurrence_id}:{occ.snooze_count + 1}",
                metadata={"snooze_minutes": snooze_minutes, "snoozed_until_utc": snoozed_until},
            )
        )

        logger.info("Occurrence %s snoozed until %s", occurrence_id, snoozed_until)
        return {
            "success": True,
            "occurrence": updated_occ,
            "snoozed_until": snoozed_until,
            "remaining_snoozes": max_snoozes - updated_occ.snooze_count,
            "message": f"Đã hoãn nhắc thêm {snooze_minutes} phút.",
        }

    def record_help_request(
        self,
        occurrence_id: str,
        patient_id: str,
        details: str = "",
    ) -> Dict[str, Any]:
        """
        Create emergency assistance request:
        1. Generates unique incident reference code.
        2. Creates HELP_REQUESTED alert.
        3. Immediately triggers urgent dispatch to caregivers.
        4. Returns emergency guidelines and primary caregiver contact info.
        """
        now = self.clock()
        now_iso = now.isoformat()
        incident_code = f"SOS-{uuid.uuid4().hex[:6].upper()}"

        occ = self.med_repo.get_occurrence(occurrence_id)
        if not occ:
            raise ValueError(f"Không tìm thấy lần nhắc {occurrence_id}")

        if occ.patient_id != patient_id:
            raise PermissionError("Lần nhắc không thuộc về bệnh nhân hiện tại")

        # 1. Record Alert
        alert_obj = Alert(
            alert_id=uuid.uuid4().hex,
            occurrence_id=occurrence_id,
            patient_id=patient_id,
            alert_type=AlertType.HELP_REQUESTED,
            status=AlertStatus.NEW,
            created_at_utc=now_iso,
            resolution_notes=f"Mã sự cố: {incident_code}. {details}",
        )
        saved_alert = self.med_repo.create_alert(alert_obj)

        # 2. Record Event
        self.med_repo.record_event(
            DoseEvent(
                event_id=uuid.uuid4().hex,
                occurrence_id=occurrence_id,
                event_type=DoseEventType.HELP_REQUESTED,
                timestamp_utc=now_iso,
                actor_id=patient_id,
                actor_role="patient",
                idempotency_key=f"help:{occurrence_id}:{incident_code}",
                metadata={"incident_code": incident_code, "details": details},
            )
        )

        # 3. Query active caregivers & dispatch immediately
        caregivers = self.caregiver_repo.get_active_caregivers_for_patient(patient_id, at_time=now)
        patient = self.patient_repo.get_by_id(patient_id)
        deliveries: List[NotificationDelivery] = []

        primary_contact = {"name": "Người thân", "phone": "115"}
        if caregivers and patient:
            primary_cg = caregivers[0]
            primary_contact = {
                "name": primary_cg.caregiver_name,
                "phone": primary_cg.caregiver_phone or primary_cg.caregiver_email,
            }

            for cg in caregivers:
                delivery_id = f"{saved_alert.alert_id}:{cg.caregiver_id}:HELP"
                delivery = NotificationDelivery(
                    delivery_id=delivery_id,
                    alert_id=saved_alert.alert_id,
                    caregiver_id=cg.caregiver_id,
                    channel=DeliveryChannel.EMAIL,
                    recipient_snapshot=cg.caregiver_email,
                    status=DeliveryStatus.QUEUED,
                    idempotency_key=delivery_id,
                )
                deliveries.append(delivery)

            self.med_repo.create_deliveries(deliveries)
            for d in deliveries:
                updated_d = self.notif_gateway.send_alert(d, saved_alert, patient)
                self.med_repo.update_delivery(updated_d)

        logger.info("Emergency assistance requested for %s. Incident: %s", patient_id, incident_code)
        return {
            "success": True,
            "incident_code": incident_code,
            "caregiver_contact": primary_contact,
            "emergency_hotline": "115 (Cấp cứu)",
            "message": f"Đã gửi yêu cầu trợ giúp tới người nhà! Mã sự cố: {incident_code}",
        }

    def caregiver_resolve(
        self,
        occurrence_id: str,
        caregiver_id: str,
        resolution_type: str,
        notes: str = "",
    ) -> Dict[str, Any]:
        """
        Caregiver marks resolution:
        resolution_type can be 'caregiver_reported_taken', 'skipped', or 'unknown'.
        """
        now = self.clock()
        now_iso = now.isoformat()

        occ = self.med_repo.get_occurrence(occurrence_id)
        if not occ:
            raise ValueError(f"Không tìm thấy lần nhắc {occurrence_id}")

        new_state = DoseState.CONFIRMED if resolution_type == "caregiver_reported_taken" else occ.state
        if resolution_type == "skipped":
            new_state = DoseState.SKIPPED

        updated_occ = self.med_repo.update_occurrence_state(
            occurrence_id=occurrence_id,
            new_state=new_state,
            action_actor=caregiver_id,
            action_notes=f"Người nhà ghi nhận ({resolution_type}): {notes}",
        )

        event_type = DoseEventType.CAREGIVER_REPORTED_TAKEN if resolution_type == "caregiver_reported_taken" else DoseEventType.RESOLVED
        self.med_repo.record_event(
            DoseEvent(
                event_id=uuid.uuid4().hex,
                occurrence_id=occurrence_id,
                event_type=event_type,
                timestamp_utc=now_iso,
                actor_id=caregiver_id,
                actor_role="caregiver",
                idempotency_key=f"resolve:{occurrence_id}:{now_iso}",
                metadata={"resolution_type": resolution_type, "notes": notes},
            )
        )

        # Mark alerts resolved
        alert = self.med_repo.get_alert_by_occurrence(occurrence_id, AlertType.OVERDUE.value)
        if alert:
            with self.med_repo.db.connect() as conn:
                conn.execute(
                    """
                    UPDATE alerts
                    SET status = 'RESOLVED', resolved_at_utc = ?, resolved_by = ?, resolution_notes = ?
                    WHERE alert_id = ?
                    """,
                    (now_iso, caregiver_id, notes, alert.alert_id),
                )

        return {
            "success": True,
            "occurrence": updated_occ,
            "message": f"Đã cập nhật xử lý của người nhà: {resolution_type}",
        }
