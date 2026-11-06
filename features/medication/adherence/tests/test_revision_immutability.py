"""
Tests for Schedule Revision Immutability and History Preservation (Criterion 8).
"""

import unittest
from datetime import datetime, timezone

from features.medication.adherence.adapters.caregiver_repository import SqlCaregiverRepository
from features.medication.adherence.adapters.medication_repository import SqlMedicationRepository
from features.medication.adherence.adapters.notification_gateway import MockNotificationGateway
from features.medication.adherence.adapters.patient_repository import SqlPatientRepository
from features.medication.adherence.database.connection import DatabaseManager
from features.medication.adherence.database.seed_data import seed_database
from features.medication.adherence.domain.models import DoseState
from features.medication.adherence.services.adherence_service import AdherenceService
from features.medication.adherence.services.scheduler import AdherenceScheduler


class TestRevisionImmutability(unittest.TestCase):
    def setUp(self):
        self.db = DatabaseManager(":memory:")
        seed_database(self.db)
        self.med_repo = SqlMedicationRepository(self.db)
        self.patient_repo = SqlPatientRepository(self.db)
        self.caregiver_repo = SqlCaregiverRepository(self.db)
        self.notif_gateway = MockNotificationGateway()

        self.fixed_now = datetime(2026, 10, 5, 1, 10, tzinfo=timezone.utc)
        self.scheduler = AdherenceScheduler(
            med_repo=self.med_repo,
            patient_repo=self.patient_repo,
            caregiver_repo=self.caregiver_repo,
            notif_gateway=self.notif_gateway,
            clock=lambda: self.fixed_now,
        )
        self.service = AdherenceService(
            med_repo=self.med_repo,
            patient_repo=self.patient_repo,
            caregiver_repo=self.caregiver_repo,
            notif_gateway=self.notif_gateway,
            clock=lambda: self.fixed_now,
        )
        self.scheduler.tick()

    def test_criterion_8_plan_revision_preserves_past_and_cancels_future(self):
        schedules = self.med_repo.list_schedules_for_patient("patient_001")
        old_sched = schedules[0]
        self.assertEqual(old_sched.revision, 1)

        # Confirm today's occurrence
        doses = self.service.get_elderly_doses("patient_001")
        today_occ = doses[0]
        self.service.record_take(today_occ.occurrence_id, "patient_001")
        self.assertEqual(today_occ.revision, 1)

        # Change schedule: shift from 08:00 to 09:00
        old_sched.local_times = ["09:00"]
        new_sched = self.med_repo.update_schedule(old_sched)
        self.assertEqual(new_sched.revision, 2)

        # Regenerate occurrences with scheduler
        self.scheduler.tick()

        # Check in database:
        # 1. Today's confirmed occurrence from revision 1 is still CONFIRMED
        confirmed_occ = self.med_repo.get_occurrence(today_occ.occurrence_id)
        self.assertEqual(confirmed_occ.state, DoseState.CONFIRMED)
        self.assertEqual(confirmed_occ.revision, 1)

        # 2. Future pending occurrences of revision 1 were marked CANCELLED
        with self.db.connect() as conn:
            cancelled_r1 = conn.execute(
                "SELECT COUNT(*) FROM dose_occurrences WHERE schedule_id = ? AND revision = 1 AND state = 'CANCELLED'",
                (old_sched.schedule_id,),
            ).fetchone()[0]
            self.assertTrue(cancelled_r1 > 0)

            # 3. New occurrences exist under revision 2
            r2_count = conn.execute(
                "SELECT COUNT(*) FROM dose_occurrences WHERE schedule_id = ? AND revision = 2",
                (old_sched.schedule_id,),
            ).fetchone()[0]
            self.assertTrue(r2_count > 0)


if __name__ == "__main__":
    unittest.main()
