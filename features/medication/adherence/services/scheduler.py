"""
Background Scheduler for Medication Adherence Module.
Executes completely independent of camera state and UI windows.
Maintains timezone-aware occurrence generation, downtime auditing, state transitions,
and caregiver overdue alert dispatching.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import date, datetime, time, timedelta, timezone
from typing import Callable, List, Optional
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

logger = logging.getLogger("AdherenceScheduler")

UTC = timezone.utc


def default_clock() -> datetime:
    return datetime.now(UTC)


class AdherenceScheduler:
    """
    Independent Medication Adherence Scheduler.
    Runs periodically (e.g. every 10-15s), generating occurrences up to 7 days ahead,
    transitioning dose states, monitoring system downtime, and triggering caregiver alerts.
    """

    def __init__(
        self,
        med_repo: IMedicationRepository,
        patient_repo: IPatientRepository,
        caregiver_repo: ICaregiverRepository,
        notif_gateway: INotificationGateway,
        clock: Callable[[], datetime] = default_clock,
        catchup_minutes: int = 60,
    ) -> None:
        self.med_repo = med_repo
        self.patient_repo = patient_repo
        self.caregiver_repo = caregiver_repo
        self.notif_gateway = notif_gateway
        self.clock = clock
        self.catchup_minutes = catchup_minutes
        self._last_heartbeat: Optional[datetime] = None
        self._running = False

    def tick(self) -> List[str]:
        """
        Execute one scheduling evaluation pass.
        Returns list of occurrence_ids that transitioned to REMINDING in this tick.
        """
        now = self.clock()
        now_iso = now.isoformat()
        reminding_ids: List[str] = []

        # 1. Downtime & Clock Jump Auditing
        if self._last_heartbeat is not None:
            gap_seconds = (now - self._last_heartbeat).total_seconds()
            if now < self._last_heartbeat:
                logger.warning("Clock moved backward: from %s to %s", self._last_heartbeat, now)
                self.med_repo.record_coverage_gap(
                    self._last_heartbeat.isoformat(), now_iso, "CLOCK_BACKWARD", "System clock shifted backwards"
                )
            elif gap_seconds > 90.0:
                logger.warning("System downtime detected: gap of %.1f seconds", gap_seconds)
                self.med_repo.record_coverage_gap(
                    self._last_heartbeat.isoformat(), now_iso, "DOWNTIME", f"Heartbeat gap of {gap_seconds:.1f}s"
                )
        self._last_heartbeat = now

        # 2. Generate 7-Day Occurrences for all active patients & schedules
        patients = self.patient_repo.list_all()
        for patient in patients:
            schedules = self.med_repo.list_schedules_for_patient(patient.patient_id, active_only=True)
            for sched in schedules:
                self._generate_future_occurrences(sched, patient.patient_id, now)

        # 3. Transition Occurrences States
        # Query occurrences that might need transition: PENDING, REMINDING, SNOOZED, OVERDUE
        active_occurrences = self.med_repo.get_due_occurrences(
            start_utc=(now - timedelta(days=2)).isoformat(),
            end_utc=(now + timedelta(hours=1)).isoformat(),
        )

        for occ in active_occurrences:
            if occ.state in (DoseState.CONFIRMED, DoseState.SKIPPED, DoseState.MISSED, DoseState.CANCELLED):
                continue

            scheduled_at = datetime.fromisoformat(occ.scheduled_at_utc)
            grace_deadline = datetime.fromisoformat(occ.grace_deadline_utc)
            age_minutes = (now - scheduled_at).total_seconds() / 60.0

            # State check: MISSED (after 24 hours without confirmation)
            if age_minutes >= 1440.0:
                if occ.state != DoseState.MISSED:
                    self.med_repo.update_occurrence_state(occ.occurrence_id, DoseState.MISSED)
                    self.med_repo.record_event(
                        DoseEvent(
                            event_id=uuid.uuid4().hex,
                            occurrence_id=occ.occurrence_id,
                            event_type=DoseEventType.OVERDUE_UNCONFIRMED,
                            timestamp_utc=now_iso,
                            actor_id="system",
                            actor_role="system",
                            idempotency_key=f"missed:{occ.occurrence_id}",
                            metadata={"reason": "Exceeded 24 hours without confirmation"},
                        )
                    )
                continue

            # State check: OVERDUE (past grace period)
            if now >= grace_deadline:
                if occ.state != DoseState.OVERDUE:
                    logger.info("Occurrence %s transitioned to OVERDUE (grace deadline %s passed)", occ.occurrence_id, occ.grace_deadline_utc)
                    self.med_repo.update_occurrence_state(occ.occurrence_id, DoseState.OVERDUE)
                    self.med_repo.record_event(
                        DoseEvent(
                            event_id=uuid.uuid4().hex,
                            occurrence_id=occ.occurrence_id,
                            event_type=DoseEventType.OVERDUE_UNCONFIRMED,
                            timestamp_utc=now_iso,
                            actor_id="system",
                            actor_role="system",
                            idempotency_key=f"overdue:{occ.occurrence_id}",
                            metadata={"grace_deadline": occ.grace_deadline_utc},
                        )
                    )
                    # Trigger caregiver alerts
                    self._trigger_overdue_alerts(occ, now)
                continue

            # State check: SNOOZED -> REMINDING
            if occ.state == DoseState.SNOOZED:
                if occ.snoozed_until_utc and now >= datetime.fromisoformat(occ.snoozed_until_utc):
                    logger.info("Occurrence %s snooze expired; returning to REMINDING", occ.occurrence_id)
                    self.med_repo.update_occurrence_state(occ.occurrence_id, DoseState.REMINDING)
                    reminding_ids.append(occ.occurrence_id)
                continue

            # State check: PENDING -> REMINDING
            if occ.state == DoseState.PENDING and now >= scheduled_at:
                logger.info("Occurrence %s is now due (%s); transitioning to REMINDING", occ.occurrence_id, occ.scheduled_at_utc)
                self.med_repo.update_occurrence_state(occ.occurrence_id, DoseState.REMINDING)
                self.med_repo.record_event(
                    DoseEvent(
                        event_id=uuid.uuid4().hex,
                        occurrence_id=occ.occurrence_id,
                        event_type=DoseEventType.REMINDED,
                        timestamp_utc=now_iso,
                        actor_id="system",
                        actor_role="system",
                        idempotency_key=f"reminded:{occ.occurrence_id}",
                    )
                )
                reminding_ids.append(occ.occurrence_id)

        return reminding_ids

    def _generate_future_occurrences(
        self, sched: any, patient_id: str, now: datetime
    ) -> None:
        """
        Project occurrences for next 7 days in the schedule's local timezone.
        Handles DST nonexistent hour skips and duplicate folds cleanly.
        """
        try:
            zone = ZoneInfo(sched.timezone)
        except Exception:
            zone = ZoneInfo("Asia/Ho_Chi_Minh")

        now_local = now.astimezone(zone)
        start_date = now_local.date()
        end_date = start_date + timedelta(days=7)

        # Get metadata snapshots from database
        with self.med_repo.db.connect() as conn:
            row = conn.execute(
                """
                SELECT m.brand_name, m.product_strength, m.strength_unit, m.image_path,
                       pi.dose_amount, pi.dose_unit, pi.original_instructions,
                       pi.start_date, pi.end_date
                FROM medication_schedules s
                JOIN prescription_items pi ON s.prescription_item_id = pi.prescription_item_id
                JOIN medications m ON pi.medication_id = m.medication_id
                WHERE s.schedule_id = ? AND s.revision = ?
                """,
                (sched.schedule_id, sched.revision),
            ).fetchone()

        if not row:
            return

        brand_name = row["brand_name"]
        prod_strength = f"{row['product_strength']} {row['strength_unit']}".strip()
        dose_str = f"{row['dose_amount']} {row['dose_unit']}".strip()
        med_name_snapshot = f"{brand_name} ({prod_strength})"
        instructions_snapshot = row["original_instructions"]
        image_snapshot = row["image_path"] or ""

        # Limit window to prescription item validity
        item_start = date.fromisoformat(row["start_date"])
        effective_start = max(start_date, item_start)
        effective_end = end_date
        if row["end_date"]:
            item_end = date.fromisoformat(row["end_date"])
            effective_end = min(effective_end, item_end)

        occurrences_to_save: List[DoseOccurrence] = []

        curr_date = effective_start
        while curr_date <= effective_end:
            if curr_date.weekday() in sched.days_of_week:
                for hhmm in sched.local_times:
                    try:
                        t_parts = [int(x) for x in hhmm.split(":")]
                        loc_time = time(t_parts[0], t_parts[1])
                    except Exception:
                        continue

                    # Construct timezone-aware local datetime with DST handling
                    local_dt = datetime.combine(curr_date, loc_time, tzinfo=zone)
                    utc_dt = local_dt.astimezone(UTC)

                    # Verify no non-existent DST hour
                    if utc_dt.astimezone(zone).replace(tzinfo=None) != local_dt.replace(tzinfo=None):
                        continue

                    scheduled_at_utc = utc_dt.isoformat()
                    grace_deadline_utc = (utc_dt + timedelta(minutes=sched.grace_period_minutes)).isoformat()
                    occ_id = f"{sched.schedule_id}:{sched.revision}:{scheduled_at_utc}"

                    occ = DoseOccurrence(
                        occurrence_id=occ_id,
                        schedule_id=sched.schedule_id,
                        revision=sched.revision,
                        scheduled_at_utc=scheduled_at_utc,
                        local_scheduled_time=local_dt.strftime("%Y-%m-%dT%H:%M:%S"),
                        grace_deadline_utc=grace_deadline_utc,
                        state=DoseState.PENDING,
                        medication_name_snapshot=med_name_snapshot,
                        dose_snapshot=dose_str,
                        instructions_snapshot=instructions_snapshot,
                        image_snapshot=image_snapshot,
                        patient_id=patient_id,
                    )
                    occurrences_to_save.append(occ)

            curr_date += timedelta(days=1)

        self.med_repo.save_occurrences(occurrences_to_save)

    def _trigger_overdue_alerts(self, occ: DoseOccurrence, now: datetime) -> None:
        """
        Create Alert and dispatch notifications to active, eligible caregivers.
        """
        now_iso = now.isoformat()
        # 1. Create or retrieve Alert record
        alert_obj = Alert(
            alert_id=uuid.uuid4().hex,
            occurrence_id=occ.occurrence_id,
            patient_id=occ.patient_id,
            alert_type=AlertType.OVERDUE,
            status=AlertStatus.NEW,
            created_at_utc=now_iso,
        )
        saved_alert = self.med_repo.create_alert(alert_obj)

        # 2. Query eligible active caregivers
        caregivers = self.caregiver_repo.get_active_caregivers_for_patient(occ.patient_id, at_time=now)
        if not caregivers:
            logger.warning(
                "No active/eligible caregivers found for patient %s on overdue occurrence %s",
                occ.patient_id,
                occ.occurrence_id,
            )
            return

        patient = self.patient_repo.get_by_id(occ.patient_id)
        if not patient:
            return

        deliveries: List[NotificationDelivery] = []
        for cg in caregivers:
            delivery_id = f"{saved_alert.alert_id}:{cg.caregiver_id}:EMAIL"
            delivery = NotificationDelivery(
                delivery_id=delivery_id,
                alert_id=saved_alert.alert_id,
                caregiver_id=cg.caregiver_id,
                channel=DeliveryChannel.EMAIL,
                recipient_snapshot=cg.caregiver_email,
                status=DeliveryStatus.QUEUED,
                attempt_count=0,
                idempotency_key=delivery_id,
            )
            deliveries.append(delivery)

        self.med_repo.create_deliveries(deliveries)

        # 3. Dispatch deliveries via Gateway
        for delivery in deliveries:
            updated_delivery = self.notif_gateway.send_alert(delivery, saved_alert, patient)
            self.med_repo.update_delivery(updated_delivery)
