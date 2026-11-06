"""
Medication Repository Implementation for Medication Adherence Module.
Handles 3NF persistence for medications, prescriptions, schedules, occurrences,
audit events, alerts, and notification deliveries.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import List, Optional, Tuple

from features.medication.adherence.database.connection import DatabaseManager
from features.medication.adherence.domain.contracts import IMedicationRepository
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
    Medication,
    MedicationSchedule,
    NotificationDelivery,
    Prescription,
    PrescriptionItem,
    PrescriptionSource,
)

logger = logging.getLogger("MedicationRepository")


class SqlMedicationRepository(IMedicationRepository):
    """SQL Implementation of IMedicationRepository."""

    def __init__(self, db: DatabaseManager) -> None:
        self.db = db

    # ==================== MEDICATIONS CATALOG ====================
    def get_medication(self, medication_id: str) -> Optional[Medication]:
        if not medication_id:
            return None
        with self.db.connect() as conn:
            row = conn.execute(
                "SELECT * FROM medications WHERE medication_id = ?",
                (medication_id.strip(),),
            ).fetchone()
            if not row:
                return None
            return Medication(
                medication_id=row["medication_id"],
                brand_name=row["brand_name"],
                generic_name=row["generic_name"] or "",
                product_strength=row["product_strength"],
                strength_unit=row["strength_unit"],
                dosage_form=row["dosage_form"],
                image_path=row["image_path"] or "",
                safety_notes=row["safety_notes"] or "",
            )

    def save_medication(self, medication: Medication) -> None:
        with self.db.connect() as conn:
            conn.execute(
                """
                INSERT INTO medications (medication_id, brand_name, generic_name, product_strength,
                                         strength_unit, dosage_form, image_path, safety_notes)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(medication_id) DO UPDATE SET
                    brand_name=excluded.brand_name,
                    generic_name=excluded.generic_name,
                    product_strength=excluded.product_strength,
                    strength_unit=excluded.strength_unit,
                    dosage_form=excluded.dosage_form,
                    image_path=excluded.image_path,
                    safety_notes=excluded.safety_notes
                """,
                (
                    medication.medication_id,
                    medication.brand_name,
                    medication.generic_name,
                    medication.product_strength,
                    medication.strength_unit,
                    medication.dosage_form,
                    medication.image_path,
                    medication.safety_notes,
                ),
            )

    def list_medications(self) -> List[Medication]:
        with self.db.connect() as conn:
            rows = conn.execute("SELECT * FROM medications ORDER BY brand_name").fetchall()
            return [
                Medication(
                    medication_id=r["medication_id"],
                    brand_name=r["brand_name"],
                    generic_name=r["generic_name"] or "",
                    product_strength=r["product_strength"],
                    strength_unit=r["strength_unit"],
                    dosage_form=r["dosage_form"],
                    image_path=r["image_path"] or "",
                    safety_notes=r["safety_notes"] or "",
                )
                for r in rows
            ]

    # ==================== PRESCRIPTIONS & SCHEDULES ====================
    def save_prescription(
        self, prescription: Prescription, items: List[Tuple[PrescriptionItem, MedicationSchedule]]
    ) -> None:
        with self.db.connect() as conn:
            conn.execute(
                """
                INSERT INTO prescriptions (prescription_id, patient_id, entered_by_user_id, prescription_code,
                                          prescribed_date, prescriber_clinician_id, source, document_url,
                                          status, valid_from, valid_to)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(prescription_id) DO UPDATE SET
                    status=excluded.status, valid_to=excluded.valid_to
                """,
                (
                    prescription.prescription_id,
                    prescription.patient_id,
                    prescription.entered_by_user_id,
                    prescription.prescription_code,
                    prescription.prescribed_date,
                    prescription.prescriber_clinician_id,
                    str(prescription.source.value if hasattr(prescription.source, "value") else prescription.source),
                    prescription.document_url,
                    prescription.status,
                    prescription.valid_from,
                    prescription.valid_to,
                ),
            )

            for item, sched in items:
                conn.execute(
                    """
                    INSERT INTO prescription_items (prescription_item_id, prescription_id, medication_id,
                                                   dose_amount, dose_unit, route_of_administration,
                                                   frequency_description, original_instructions,
                                                   start_date, end_date, status)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(prescription_item_id) DO UPDATE SET
                        dose_amount=excluded.dose_amount,
                        dose_unit=excluded.dose_unit,
                        original_instructions=excluded.original_instructions,
                        status=excluded.status
                    """,
                    (
                        item.prescription_item_id,
                        item.prescription_id,
                        item.medication_id,
                        float(item.dose_amount),
                        item.dose_unit,
                        item.route_of_administration,
                        item.frequency_description,
                        item.original_instructions,
                        item.start_date,
                        item.end_date,
                        item.status,
                    ),
                )

                conn.execute(
                    """
                    INSERT INTO medication_schedules (schedule_id, prescription_item_id, revision, timezone,
                                                     local_times, days_of_week, reminder_delay_minutes,
                                                     grace_period_minutes, caregiver_alert_enabled,
                                                     is_active, effective_from, effective_to)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(schedule_id, revision) DO UPDATE SET
                        is_active=excluded.is_active,
                        effective_to=excluded.effective_to
                    """,
                    (
                        sched.schedule_id,
                        sched.prescription_item_id,
                        sched.revision,
                        sched.timezone,
                        json.dumps(sched.local_times),
                        json.dumps(sched.days_of_week),
                        int(sched.reminder_delay_minutes),
                        int(sched.grace_period_minutes),
                        1 if sched.caregiver_alert_enabled else 0,
                        1 if sched.is_active else 0,
                        sched.effective_from,
                        sched.effective_to,
                    ),
                )

    def update_schedule(self, schedule: MedicationSchedule) -> MedicationSchedule:
        """
        Create a new revision of schedule. Cancel future PENDING occurrences of superseded revision.
        Never mutate past occurrences or completed events.
        """
        now_iso = datetime.now(timezone.utc).isoformat()
        with self.db.connect() as conn:
            # Find latest revision
            row = conn.execute(
                "SELECT MAX(revision) FROM medication_schedules WHERE schedule_id = ?",
                (schedule.schedule_id,),
            ).fetchone()
            curr_rev = row[0] if row and row[0] is not None else 0
            new_rev = curr_rev + 1

            new_sched = MedicationSchedule(
                schedule_id=schedule.schedule_id,
                prescription_item_id=schedule.prescription_item_id,
                revision=new_rev,
                timezone=schedule.timezone,
                local_times=schedule.local_times,
                days_of_week=schedule.days_of_week,
                reminder_delay_minutes=schedule.reminder_delay_minutes,
                grace_period_minutes=schedule.grace_period_minutes,
                caregiver_alert_enabled=schedule.caregiver_alert_enabled,
                is_active=schedule.is_active,
                effective_from=now_iso,
                effective_to=schedule.effective_to,
            )

            # Insert new revision
            conn.execute(
                """
                INSERT INTO medication_schedules (schedule_id, prescription_item_id, revision, timezone,
                                                 local_times, days_of_week, reminder_delay_minutes,
                                                 grace_period_minutes, caregiver_alert_enabled,
                                                 is_active, effective_from, effective_to)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    new_sched.schedule_id,
                    new_sched.prescription_item_id,
                    new_sched.revision,
                    new_sched.timezone,
                    json.dumps(new_sched.local_times),
                    json.dumps(new_sched.days_of_week),
                    new_sched.reminder_delay_minutes,
                    new_sched.grace_period_minutes,
                    1 if new_sched.caregiver_alert_enabled else 0,
                    1 if new_sched.is_active else 0,
                    new_sched.effective_from,
                    new_sched.effective_to,
                ),
            )

            # Cancel only FUTURE PENDING occurrences of superseded revision
            conn.execute(
                """
                UPDATE dose_occurrences
                SET state = 'CANCELLED'
                WHERE schedule_id = ?
                  AND scheduled_at_utc > ?
                  AND state = 'PENDING'
                """,
                (schedule.schedule_id, now_iso),
            )

            logger.info("Schedule %s bumped to revision %d; future pending doses cancelled", schedule.schedule_id, new_rev)
            return new_sched

    def list_schedules_for_patient(self, patient_id: str, active_only: bool = True) -> List[MedicationSchedule]:
        if not patient_id:
            return []
        query = """
            SELECT s.*
            FROM medication_schedules s
            JOIN prescription_items pi ON s.prescription_item_id = pi.prescription_item_id
            JOIN prescriptions p ON pi.prescription_id = p.prescription_id
            WHERE p.patient_id = ?
              AND s.revision = (
                  SELECT MAX(revision) FROM medication_schedules WHERE schedule_id = s.schedule_id
              )
        """
        if active_only:
            query += " AND s.is_active = 1 AND p.status = 'ACTIVE' AND pi.status = 'ACTIVE'"

        with self.db.connect() as conn:
            rows = conn.execute(query, (patient_id.strip(),)).fetchall()
            return [
                MedicationSchedule(
                    schedule_id=r["schedule_id"],
                    prescription_item_id=r["prescription_item_id"],
                    revision=r["revision"],
                    timezone=r["timezone"],
                    local_times=json.loads(r["local_times"]),
                    days_of_week=json.loads(r["days_of_week"]),
                    reminder_delay_minutes=r["reminder_delay_minutes"],
                    grace_period_minutes=r["grace_period_minutes"],
                    caregiver_alert_enabled=bool(r["caregiver_alert_enabled"]),
                    is_active=bool(r["is_active"]),
                    effective_from=r["effective_from"],
                    effective_to=r["effective_to"],
                )
                for r in rows
            ]

    # ==================== DOSE OCCURRENCES ====================
    def get_due_occurrences(
        self, patient_id: Optional[str] = None, start_utc: Optional[str] = None, end_utc: Optional[str] = None
    ) -> List[DoseOccurrence]:
        query = "SELECT * FROM dose_occurrences WHERE 1=1"
        params = []
        if patient_id:
            query += " AND patient_id = ?"
            params.append(patient_id.strip())
        if start_utc:
            query += " AND scheduled_at_utc >= ?"
            params.append(start_utc)
        if end_utc:
            query += " AND scheduled_at_utc <= ?"
            params.append(end_utc)

        query += " ORDER BY scheduled_at_utc ASC"

        with self.db.connect() as conn:
            rows = conn.execute(query, tuple(params)).fetchall()
            return [self._row_to_occurrence(r) for r in rows]

    def get_occurrence(self, occurrence_id: str) -> Optional[DoseOccurrence]:
        if not occurrence_id:
            return None
        with self.db.connect() as conn:
            row = conn.execute(
                "SELECT * FROM dose_occurrences WHERE occurrence_id = ?",
                (occurrence_id.strip(),),
            ).fetchone()
            return self._row_to_occurrence(row) if row else None

    def save_occurrences(self, occurrences: List[DoseOccurrence]) -> None:
        if not occurrences:
            return
        with self.db.connect() as conn:
            for occ in occurrences:
                conn.execute(
                    """
                    INSERT OR IGNORE INTO dose_occurrences (
                        occurrence_id, schedule_id, revision, scheduled_at_utc,
                        local_scheduled_time, grace_deadline_utc, state,
                        medication_name_snapshot, dose_snapshot, instructions_snapshot,
                        image_snapshot, patient_id, snooze_count, snoozed_until_utc,
                        action_taken_at_utc, action_actor, action_notes
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        occ.occurrence_id,
                        occ.schedule_id,
                        occ.revision,
                        occ.scheduled_at_utc,
                        occ.local_scheduled_time,
                        occ.grace_deadline_utc,
                        occ.state.value if hasattr(occ.state, "value") else str(occ.state),
                        occ.medication_name_snapshot,
                        occ.dose_snapshot,
                        occ.instructions_snapshot,
                        occ.image_snapshot,
                        occ.patient_id,
                        occ.snooze_count,
                        occ.snoozed_until_utc,
                        occ.action_taken_at_utc,
                        occ.action_actor,
                        occ.action_notes,
                    ),
                )

    def update_occurrence_state(
        self,
        occurrence_id: str,
        new_state: DoseState,
        action_actor: Optional[str] = None,
        action_notes: Optional[str] = None,
        snoozed_until_utc: Optional[str] = None,
    ) -> DoseOccurrence:
        now_iso = datetime.now(timezone.utc).isoformat()
        with self.db.connect() as conn:
            occ = self.get_occurrence(occurrence_id)
            if not occ:
                raise ValueError(f"Occurrence {occurrence_id} not found")

            snooze_inc = 1 if new_state == DoseState.SNOOZED else 0
            action_taken_at = now_iso if new_state in (DoseState.CONFIRMED, DoseState.SKIPPED) else occ.action_taken_at_utc

            conn.execute(
                """
                UPDATE dose_occurrences
                SET state = ?,
                    snooze_count = snooze_count + ?,
                    snoozed_until_utc = ?,
                    action_taken_at_utc = ?,
                    action_actor = COALESCE(?, action_actor),
                    action_notes = COALESCE(?, action_notes)
                WHERE occurrence_id = ?
                """,
                (
                    new_state.value if hasattr(new_state, "value") else str(new_state),
                    snooze_inc,
                    snoozed_until_utc,
                    action_taken_at,
                    action_actor,
                    action_notes,
                    occurrence_id,
                ),
            )
            return self.get_occurrence(occurrence_id)

    def _row_to_occurrence(self, r: any) -> DoseOccurrence:
        return DoseOccurrence(
            occurrence_id=r["occurrence_id"],
            schedule_id=r["schedule_id"],
            revision=r["revision"],
            scheduled_at_utc=r["scheduled_at_utc"],
            local_scheduled_time=r["local_scheduled_time"],
            grace_deadline_utc=r["grace_deadline_utc"],
            state=DoseState(r["state"]),
            medication_name_snapshot=r["medication_name_snapshot"],
            dose_snapshot=r["dose_snapshot"],
            instructions_snapshot=r["instructions_snapshot"],
            image_snapshot=r["image_snapshot"] or "",
            patient_id=r["patient_id"],
            snooze_count=r["snooze_count"],
            snoozed_until_utc=r["snoozed_until_utc"],
            action_taken_at_utc=r["action_taken_at_utc"],
            action_actor=r["action_actor"],
            action_notes=r["action_notes"],
        )

    # ==================== DOSE EVENTS AUDIT LOG ====================
    def record_event(self, event: DoseEvent) -> None:
        with self.db.connect() as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO dose_events (event_id, occurrence_id, event_type, actor_id,
                                                   actor_role, timestamp_utc, idempotency_key, metadata)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event.event_id,
                    event.occurrence_id,
                    event.event_type.value if hasattr(event.event_type, "value") else str(event.event_type),
                    event.actor_id,
                    event.actor_role,
                    event.timestamp_utc,
                    event.idempotency_key,
                    json.dumps(event.metadata, ensure_ascii=False),
                ),
            )

    def list_events_for_occurrence(self, occurrence_id: str) -> List[DoseEvent]:
        if not occurrence_id:
            return []
        with self.db.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM dose_events WHERE occurrence_id = ? ORDER BY timestamp_utc ASC",
                (occurrence_id.strip(),),
            ).fetchall()
            return [
                DoseEvent(
                    event_id=r["event_id"],
                    occurrence_id=r["occurrence_id"],
                    event_type=DoseEventType(r["event_type"]),
                    timestamp_utc=r["timestamp_utc"],
                    actor_id=r["actor_id"],
                    actor_role=r["actor_role"],
                    idempotency_key=r["idempotency_key"],
                    metadata=json.loads(r["metadata"] or "{}"),
                )
                for r in rows
            ]

    # ==================== ALERTS & NOTIFICATION DELIVERIES ====================
    def create_alert(self, alert: Alert) -> Alert:
        with self.db.connect() as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO alerts (alert_id, occurrence_id, patient_id, alert_type,
                                              status, created_at_utc, resolved_at_utc,
                                              resolved_by, resolution_notes)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    alert.alert_id,
                    alert.occurrence_id,
                    alert.patient_id,
                    alert.alert_type.value if hasattr(alert.alert_type, "value") else str(alert.alert_type),
                    alert.status.value if hasattr(alert.status, "value") else str(alert.status),
                    alert.created_at_utc,
                    alert.resolved_at_utc,
                    alert.resolved_by,
                    alert.resolution_notes,
                ),
            )
            # Query back
            row = conn.execute(
                "SELECT * FROM alerts WHERE occurrence_id = ? AND alert_type = ?",
                (
                    alert.occurrence_id,
                    alert.alert_type.value if hasattr(alert.alert_type, "value") else str(alert.alert_type),
                ),
            ).fetchone()
            return self._row_to_alert(row)

    def get_alert_by_occurrence(self, occurrence_id: str, alert_type: str) -> Optional[Alert]:
        with self.db.connect() as conn:
            row = conn.execute(
                "SELECT * FROM alerts WHERE occurrence_id = ? AND alert_type = ?",
                (occurrence_id.strip(), alert_type.strip()),
            ).fetchone()
            return self._row_to_alert(row) if row else None

    def list_active_alerts(self, patient_id: Optional[str] = None) -> List[Alert]:
        query = "SELECT * FROM alerts WHERE status != 'RESOLVED'"
        params = []
        if patient_id:
            query += " AND patient_id = ?"
            params.append(patient_id.strip())
        query += " ORDER BY created_at_utc DESC"

        with self.db.connect() as conn:
            rows = conn.execute(query, tuple(params)).fetchall()
            return [self._row_to_alert(r) for r in rows]

    def _row_to_alert(self, r: any) -> Alert:
        return Alert(
            alert_id=r["alert_id"],
            occurrence_id=r["occurrence_id"],
            patient_id=r["patient_id"],
            alert_type=AlertType(r["alert_type"]),
            status=AlertStatus(r["status"]),
            created_at_utc=r["created_at_utc"],
            resolved_at_utc=r["resolved_at_utc"],
            resolved_by=r["resolved_by"],
            resolution_notes=r["resolution_notes"],
        )

    def create_deliveries(self, deliveries: List[NotificationDelivery]) -> None:
        if not deliveries:
            return
        with self.db.connect() as conn:
            for d in deliveries:
                conn.execute(
                    """
                    INSERT OR IGNORE INTO notification_deliveries (
                        delivery_id, alert_id, caregiver_id, channel, recipient_snapshot,
                        status, attempt_count, last_attempt_at_utc, error_message, idempotency_key
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        d.delivery_id,
                        d.alert_id,
                        d.caregiver_id,
                        d.channel.value if hasattr(d.channel, "value") else str(d.channel),
                        d.recipient_snapshot,
                        d.status.value if hasattr(d.status, "value") else str(d.status),
                        d.attempt_count,
                        d.last_attempt_at_utc,
                        d.error_message,
                        d.idempotency_key,
                    ),
                )

    def update_delivery(self, delivery: NotificationDelivery) -> None:
        with self.db.connect() as conn:
            conn.execute(
                """
                UPDATE notification_deliveries
                SET status = ?,
                    attempt_count = ?,
                    last_attempt_at_utc = ?,
                    error_message = ?
                WHERE delivery_id = ?
                """,
                (
                    delivery.status.value if hasattr(delivery.status, "value") else str(delivery.status),
                    delivery.attempt_count,
                    delivery.last_attempt_at_utc,
                    delivery.error_message,
                    delivery.delivery_id,
                ),
            )

    def record_coverage_gap(self, start_utc: str, end_utc: str, event_type: str, notes: str = "") -> None:
        with self.db.connect() as conn:
            conn.execute(
                """
                INSERT INTO coverage_audit (audit_id, start_utc, end_utc, event_type, notes)
                VALUES (?, ?, ?, ?, ?)
                """,
                (uuid.uuid4().hex, start_utc, end_utc, event_type, notes),
            )
