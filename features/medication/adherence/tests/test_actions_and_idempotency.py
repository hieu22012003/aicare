"""
Tests for Intake Actions, Idempotency, Snooze Limits, and Emergency Assistance (Criteria 2, 3, 4, 10).
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


class TestActionsAndIdempotency(unittest.TestCase):
    def setUp(self):
        self.db = DatabaseManager(":memory:")
        seed_database(self.db)
        self.med_repo = SqlMedicationRepository(self.db)
        self.patient_repo = SqlPatientRepository(self.db)
        self.caregiver_repo = SqlCaregiverRepository(self.db)
        self.notif_gateway = MockNotificationGateway()

        self.fixed_now = datetime(2026, 10, 5, 1, 5, tzinfo=timezone.utc)
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
        self.doses = self.service.get_elderly_doses("patient_001")
        self.occ = self.doses[0]

    def test_criterion_2_take_idempotent_and_no_clinical_verification_claim(self):
        # 1st click
        res1 = self.service.record_take(self.occ.occurrence_id, "patient_001")
        self.assertTrue(res1["success"])
        self.assertFalse(res1["already_confirmed"])
        self.assertIn("xác nhận uống thuốc", res1["message"].lower())
        self.assertNotIn("camera xác minh", res1["message"].lower())

        # 2nd click (idempotent double-click)
        res2 = self.service.record_take(self.occ.occurrence_id, "patient_001")
        self.assertTrue(res2["success"])
        self.assertTrue(res2["already_confirmed"])

        # Check only 1 SELF_REPORTED_TAKEN event was logged
        events = self.med_repo.list_events_for_occurrence(self.occ.occurrence_id)
        taken_events = [e for e in events if e.event_type.value == "self_reported_taken"]
        self.assertEqual(len(taken_events), 1)

    def test_criterion_3_snooze_limits_and_immutable_grace_deadline(self):
        original_grace = self.occ.grace_deadline_utc

        # 1st snooze
        res1 = self.service.record_snooze(self.occ.occurrence_id, "patient_001", snooze_minutes=10)
        self.assertTrue(res1["success"])
        self.assertEqual(res1["remaining_snoozes"], 2)

        # 2nd snooze
        res2 = self.service.record_snooze(self.occ.occurrence_id, "patient_001", snooze_minutes=10)
        self.assertEqual(res2["remaining_snoozes"], 1)

        # 3rd snooze
        res3 = self.service.record_snooze(self.occ.occurrence_id, "patient_001", snooze_minutes=10)
        self.assertEqual(res3["remaining_snoozes"], 0)

        # 4th snooze must fail (exceeded limit)
        with self.assertRaises(ValueError):
            self.service.record_snooze(self.occ.occurrence_id, "patient_001", snooze_minutes=10)

        # Grace deadline must NOT be changed
        occ_after = self.med_repo.get_occurrence(self.occ.occurrence_id)
        self.assertEqual(occ_after.grace_deadline_utc, original_grace)

    def test_criterion_4_help_request_creates_incident_and_dispatches_alert(self):
        res = self.service.record_help_request(
            occurrence_id=self.occ.occurrence_id,
            patient_id="patient_001",
            details="Cảm thấy chóng mặt nhẹ",
        )
        self.assertTrue(res["success"])
        self.assertTrue(res["incident_code"].startswith("SOS-"))
        self.assertEqual(res["emergency_hotline"], "115 (Cấp cứu)")
        self.assertIsNotNone(res["caregiver_contact"]["phone"])

        # Verify alert was created in DB
        alert = self.med_repo.get_alert_by_occurrence(self.occ.occurrence_id, "HELP_REQUESTED")
        self.assertIsNotNone(alert)

        # Verify immediate notification was sent
        self.assertTrue(len(self.notif_gateway.sent_alerts) >= 1)
        sent = self.notif_gateway.sent_alerts[-1]
        self.assertEqual(sent["alert_type"], "HELP_REQUESTED")
        self.assertEqual(sent["patient_id"], "patient_001")


if __name__ == "__main__":
    unittest.main()
