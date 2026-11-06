"""
Unit tests for Permission Checks, Care Team Expirations, and Role-based Access Control.
"""

from __future__ import annotations

import sys
from pathlib import Path

root_dir = Path(__file__).resolve().parents[3]
if str(root_dir) not in sys.path:
    sys.path.insert(0, str(root_dir))

import unittest

from features.health_vitals.adapters.care_team_repository import SqlCareTeamRepository
from features.health_vitals.adapters.observation_repository import SqlObservationRepository
from features.health_vitals.adapters.patient_access_repository import SqlPatientAccessRepository
from features.health_vitals.database.connection import DatabaseManager
from features.health_vitals.database.seed_data import seed_database
from features.health_vitals.services.monitoring_service import MonitoringService


class TestPermissionsAndCareTeam(unittest.TestCase):
    def setUp(self) -> None:
        self.db = DatabaseManager(":memory:")
        seed_database(self.db)
        self.obs_repo = SqlObservationRepository(self.db)
        self.care_team_repo = SqlCareTeamRepository(self.db)
        self.patient_access_repo = SqlPatientAccessRepository(self.db)
        self.service = MonitoringService(self.obs_repo, self.care_team_repo, self.patient_access_repo)

    def test_authorized_clinician_can_access(self) -> None:
        """Dr. Quang has active care_team for Patient A and Patient B."""
        self.assertTrue(self.care_team_repo.is_clinician_authorized("doc-001", "pat-001"))
        self.assertTrue(self.care_team_repo.is_clinician_authorized("doc-001", "pat-002"))

        # Service access succeeds without error
        prof = self.service.get_patient_profile("doc-001", "pat-001")
        self.assertEqual(prof["patient"].patient_id, "pat-001")

    def test_expired_clinician_is_denied(self) -> None:
        """Dr. Mai has expired care_team for Patient B (expired in 2025)."""
        self.assertFalse(self.care_team_repo.is_clinician_authorized("doc-002", "pat-002"))
        with self.assertRaises(PermissionError):
            self.service.get_patient_profile("doc-002", "pat-002")

    def test_caregiver_access_filtering(self) -> None:
        """Caregiver 1 can access Patient A, but cannot access Patient B."""
        self.assertTrue(self.patient_access_repo.can_user_access_patient("usr-cg-001", "pat-001", "can_view_health"))
        self.assertFalse(self.patient_access_repo.can_user_access_patient("usr-cg-001", "pat-002", "can_view_health"))

    def test_unauthorized_user_cannot_record_measurement(self) -> None:
        """User from family of Patient B cannot record measurement for Patient A."""
        with self.assertRaises(PermissionError):
            self.service.record_blood_pressure(
                patient_id="pat-001",
                entered_by_user_id="usr-cg-002",  # Caregiver for Patient B only
                systolic=120.0,
                diastolic=80.0,
            )


if __name__ == "__main__":
    unittest.main()
