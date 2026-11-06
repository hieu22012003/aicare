"""
Tests for Multi-Patient Isolation and Anti-Cross-Contamination (Acceptance Criterion 7).
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
from features.medication.adherence.services.adherence_service import AdherenceService
from features.medication.adherence.services.scheduler import AdherenceScheduler
from features.medication.adherence.ui.elderly_ui import ElderlyPresenter


class TestPatientIsolation(unittest.TestCase):
    def setUp(self):
        self.db = DatabaseManager(":memory:")
        seed_database(self.db)
        self.med_repo = SqlMedicationRepository(self.db)
        self.patient_repo = SqlPatientRepository(self.db)
        self.caregiver_repo = SqlCaregiverRepository(self.db)
        self.notif_gateway = MockNotificationGateway()
        self.identity_adapter = MockPatientIdentityAdapter()

        self.fixed_now = datetime(2026, 10, 5, 8, 5, tzinfo=timezone.utc)
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
        # Generate occurrences for both patients
        self.scheduler.tick()

    def test_doses_isolated_per_patient(self):
        doses_p1 = self.service.get_elderly_doses("patient_001")
        doses_p2 = self.service.get_elderly_doses("patient_002")

        self.assertTrue(len(doses_p1) > 0)
        self.assertTrue(len(doses_p2) > 0)

        # Ensure no cross-talk in returned occurrences
        self.assertTrue(all(d.patient_id == "patient_001" for d in doses_p1))
        self.assertTrue(all(d.patient_id == "patient_002" for d in doses_p2))

        # Check specific medications
        self.assertTrue(any("Amlor" in d.medication_name_snapshot for d in doses_p1))
        self.assertFalse(any("Glucophage" in d.medication_name_snapshot for d in doses_p1))

        self.assertTrue(any("Glucophage" in d.medication_name_snapshot for d in doses_p2))
        self.assertFalse(any("Amlor" in d.medication_name_snapshot for d in doses_p2))

    def test_cannot_act_on_another_patients_dose(self):
        doses_p2 = self.service.get_elderly_doses("patient_002")
        dose_b = doses_p2[0]

        # Patient 1 attempts to confirm Patient 2's dose
        with self.assertRaises(PermissionError):
            self.service.record_take(
                occurrence_id=dose_b.occurrence_id,
                patient_id="patient_001",
                actor="Người cao tuổi",
            )

    def test_identity_switch_flushes_elderly_view_immediately(self):
        self.identity_adapter.simulate_camera_recognition("patient_001")
        presenter = ElderlyPresenter(self.service, self.identity_adapter)

        self.assertEqual(presenter.active_patient_id, "patient_001")
        self.assertTrue(any("Amlor" in d.medication_name_snapshot for d in presenter.cached_doses))

        # Switch camera identity to Patient 2
        self.identity_adapter.simulate_camera_recognition("patient_002")
        self.assertEqual(presenter.active_patient_id, "patient_002")
        self.assertTrue(all(d.patient_id == "patient_002" for d in presenter.cached_doses))
        self.assertFalse(any("Amlor" in d.medication_name_snapshot for d in presenter.cached_doses))


if __name__ == "__main__":
    unittest.main()
