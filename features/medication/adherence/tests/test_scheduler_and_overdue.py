"""
Tests for Scheduler, Timezone Accuracy, Overdue Transitions, and Audit Logging (Criteria 1, 5).
"""

import unittest
from datetime import datetime, timedelta, timezone

from features.medication.adherence.adapters.caregiver_repository import SqlCaregiverRepository
from features.medication.adherence.adapters.medication_repository import SqlMedicationRepository
from features.medication.adherence.adapters.notification_gateway import MockNotificationGateway
from features.medication.adherence.adapters.patient_repository import SqlPatientRepository
from features.medication.adherence.database.connection import DatabaseManager
from features.medication.adherence.database.seed_data import seed_database
from features.medication.adherence.domain.models import DoseState
from features.medication.adherence.services.adherence_service import AdherenceService
from features.medication.adherence.services.scheduler import AdherenceScheduler


class TestSchedulerAndOverdue(unittest.TestCase):
    def setUp(self):
        self.db = DatabaseManager(":memory:")
        seed_database(self.db)
        self.med_repo = SqlMedicationRepository(self.db)
        self.patient_repo = SqlPatientRepository(self.db)
        self.caregiver_repo = SqlCaregiverRepository(self.db)
        self.notif_gateway = MockNotificationGateway()

        # Start at 07:50 UTC+7 (00:50 UTC) on 2026-10-05
        # The 08:00 UTC+7 dose is at 01:00 UTC
        self.curr_time = datetime(2026, 10, 5, 0, 50, tzinfo=timezone.utc)
        self.scheduler = AdherenceScheduler(
            med_repo=self.med_repo,
            patient_repo=self.patient_repo,
            caregiver_repo=self.caregiver_repo,
            notif_gateway=self.notif_gateway,
            clock=lambda: self.curr_time,
        )
        self.service = AdherenceService(
            med_repo=self.med_repo,
            patient_repo=self.patient_repo,
            caregiver_repo=self.caregiver_repo,
            notif_gateway=self.notif_gateway,
            clock=lambda: self.curr_time,
        )

    def test_timezone_accurate_generation_and_transitions(self):
        # 1. Initial tick generates occurrences
        self.scheduler.tick()
        occurrences = self.med_repo.get_due_occurrences("patient_001")
        self.assertTrue(len(occurrences) > 0)
        first_occ = occurrences[0]

        # In Asia/Ho_Chi_Minh, 08:00 is 01:00 UTC
        self.assertIn("08:00:00", first_occ.local_scheduled_time)
        self.assertIn("T01:00:00", first_occ.scheduled_at_utc)
        self.assertEqual(first_occ.state, DoseState.PENDING)

        # 2. Advance time to 08:01 local (01:01 UTC) -> Becomes REMINDING
        self.curr_time = datetime(2026, 10, 5, 1, 1, tzinfo=timezone.utc)
        reminding_ids = self.scheduler.tick()
        self.assertIn(first_occ.occurrence_id, reminding_ids)

        updated_occ = self.med_repo.get_occurrence(first_occ.occurrence_id)
        self.assertEqual(updated_occ.state, DoseState.REMINDING)

        # 3. Advance time past grace period (+60 min, grace deadline is 02:00 UTC) -> Becomes OVERDUE
        self.curr_time = datetime(2026, 10, 5, 2, 5, tzinfo=timezone.utc)
        self.scheduler.tick()

        overdue_occ = self.med_repo.get_occurrence(first_occ.occurrence_id)
        self.assertEqual(overdue_occ.state, DoseState.OVERDUE)

        # 4. Check that alert was generated and dispatched for patient_001
        alert = self.med_repo.get_alert_by_occurrence(first_occ.occurrence_id, "OVERDUE")
        self.assertIsNotNone(alert)
        p1_alerts = self.notif_gateway.get_sent_alerts_for_patient("patient_001")
        self.assertEqual(len(p1_alerts), 1)
        self.assertEqual(p1_alerts[0]["patient_id"], "patient_001")

        # 5. Repeated ticks do NOT duplicate alerts or deliveries
        self.scheduler.tick()
        self.scheduler.tick()
        with self.db.connect() as conn:
            cnt = conn.execute("SELECT COUNT(*) FROM alerts WHERE occurrence_id = ?", (first_occ.occurrence_id,)).fetchone()[0]
            self.assertEqual(cnt, 1)

        # 6. Late confirmation after overdue preserves the overdue alert
        take_res = self.service.record_take(first_occ.occurrence_id, "patient_001")
        self.assertTrue(take_res["success"])
        confirmed_occ = self.med_repo.get_occurrence(first_occ.occurrence_id)
        self.assertEqual(confirmed_occ.state, DoseState.CONFIRMED)

        # Overdue alert is still preserved in DB
        alert_after = self.med_repo.get_alert_by_occurrence(first_occ.occurrence_id, "OVERDUE")
        self.assertIsNotNone(alert_after)

    def test_downtime_gap_recording(self):
        self.scheduler.tick()
        # Simulate 10-minute downtime jump
        self.curr_time += timedelta(minutes=10)
        self.scheduler.tick()

        with self.db.connect() as conn:
            gaps = conn.execute("SELECT * FROM coverage_audit WHERE event_type = 'DOWNTIME'").fetchall()
            self.assertTrue(len(gaps) >= 1)


if __name__ == "__main__":
    unittest.main()
