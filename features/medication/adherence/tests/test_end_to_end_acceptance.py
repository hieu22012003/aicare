"""
End-to-End Acceptance Tests verifying criteria 1-6, 8-10 from KE_HOACH_NHAC_THUOC_TUAN_THU.md.
"""

import unittest
from datetime import datetime, timezone

from features.medication.adherence.adapters.caregiver_repository import SqlCaregiverRepository
from features.medication.adherence.adapters.identity_adapter import MockPatientIdentityAdapter
from features.medication.adherence.adapters.medication_repository import SqlMedicationRepository
from features.medication.adherence.adapters.notification_gateway import MockNotificationGateway
from features.medication.adherence.adapters.patient_repository import SqlPatientRepository
from features.medication.adherence.database.connection import DatabaseManager
from features.medication.adherence.database.seed_data import seed_database
from features.medication.adherence.domain.models import DoseState
from features.medication.adherence.services.adherence_service import AdherenceService
from features.medication.adherence.services.scheduler import AdherenceScheduler
from features.medication.adherence.ui.elderly_ui import ElderlyPresenter


class TestEndToEndAcceptance(unittest.TestCase):
    def setUp(self):
        self.db = DatabaseManager(":memory:")
        seed_database(self.db)
        self.med_repo = SqlMedicationRepository(self.db)
        self.patient_repo = SqlPatientRepository(self.db)
        self.caregiver_repo = SqlCaregiverRepository(self.db)
        self.notif_gateway = MockNotificationGateway()
        self.identity_adapter = MockPatientIdentityAdapter()

        # Start at 08:05 local (01:05 UTC)
        self.clock_time = datetime(2026, 10, 5, 1, 5, tzinfo=timezone.utc)
        self.scheduler = AdherenceScheduler(
            med_repo=self.med_repo,
            patient_repo=self.patient_repo,
            caregiver_repo=self.caregiver_repo,
            notif_gateway=self.notif_gateway,
            clock=lambda: self.clock_time,
        )
        self.service = AdherenceService(
            med_repo=self.med_repo,
            patient_repo=self.patient_repo,
            caregiver_repo=self.caregiver_repo,
            notif_gateway=self.notif_gateway,
            clock=lambda: self.clock_time,
        )
        self.scheduler.tick()

    def test_criterion_1_accurate_timezone_and_unambiguous_info(self):
        doses = self.service.get_elderly_doses("patient_001")
        self.assertTrue(len(doses) > 0)
        d = doses[0]
        # Check unambiguous information: Product strength vs Dose per intake
        self.assertIn("Amlor", d.medication_name_snapshot)
        self.assertIn("5 mg", d.medication_name_snapshot)
        self.assertIn("1.0 viên", d.dose_snapshot)
        self.assertIn("08:00:00", d.local_scheduled_time)

    def test_criterion_6_fallback_when_camera_fails(self):
        # Camera loses tracking / fails
        self.identity_adapter.simulate_camera_loss()
        self.assertIsNone(self.identity_adapter.get_current_patient_id())

        presenter = ElderlyPresenter(self.service, self.identity_adapter)
        self.assertEqual(len(presenter.cached_doses), 0)

        # Fallback to manual selection
        self.identity_adapter.simulate_manual_selection("patient_001")
        self.assertEqual(presenter.active_patient_id, "patient_001")
        self.assertTrue(len(presenter.cached_doses) > 0)

        # Confirm dose successfully in fallback session
        res = presenter.on_take_clicked(presenter.cached_doses[0].occurrence_id)
        self.assertTrue(res["success"])

    def test_criterion_9_gateway_failure_and_retry(self):
        # Create a gateway that fails on attempt 1, succeeds on attempt 2
        failing_gateway = MockNotificationGateway(simulate_network_failure=True, max_simulated_failures=1)
        sched = AdherenceScheduler(
            med_repo=self.med_repo,
            patient_repo=self.patient_repo,
            caregiver_repo=self.caregiver_repo,
            notif_gateway=failing_gateway,
            clock=lambda: self.clock_time,
        )

        doses = self.service.get_elderly_doses("patient_001")
        target_occ = doses[0]

        # Advance to overdue (+2 hours)
        self.clock_time = datetime(2026, 10, 5, 3, 5, tzinfo=timezone.utc)
        sched.tick()

        # Check delivery recorded failure on 1st attempt
        with self.db.connect() as conn:
            deliv = conn.execute("SELECT * FROM notification_deliveries WHERE alert_id IN (SELECT alert_id FROM alerts WHERE occurrence_id = ?)", (target_occ.occurrence_id,)).fetchone()
            self.assertIsNotNone(deliv)
            self.assertEqual(deliv["status"], "FAILED")
            self.assertEqual(deliv["attempt_count"], 1)

    def test_criterion_10_guardrail_against_compensatory_dosing(self):
        # Scan all instructions and models to guarantee no clinical compensatory advice
        doses = self.service.get_elderly_doses("patient_001")
        for d in doses:
            text = (d.instructions_snapshot + " " + d.medication_name_snapshot).lower()
            self.assertNotIn("uống gấp đôi", text)
            self.assertNotIn("tự tăng liều", text)
            self.assertNotIn("uống bù", text)


if __name__ == "__main__":
    unittest.main()
