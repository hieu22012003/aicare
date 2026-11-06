"""
Tests for RBAC Permissions and Identity Resolution (Criteria 6 & 7).
Verifies:
- Patient A cannot read Patient B data
- Caregiver permissions with can_view_health = 1
- Clinician permissions with active, non-expired care_team relation
- Camera fallback to PIN/session when camera is deactivated
"""

import unittest

from features.music_care.cognitive_games.adapters.care_team_repository import SqlCareTeamRepository
from features.music_care.cognitive_games.adapters.game_repository import SqlGameRepository
from features.music_care.cognitive_games.adapters.identity_adapter import MockPatientIdentityAdapter
from features.music_care.cognitive_games.database.connection import DatabaseManager
from features.music_care.cognitive_games.database.seed_data import seed_database
from features.music_care.cognitive_games.services.cognitive_service import CognitiveService


class TestPermissionsAndIdentity(unittest.TestCase):
    def setUp(self) -> None:
        self.db = DatabaseManager(db_path=":memory:")
        self.db.initialize_schema()
        seed_database(self.db)

        self.care_team_repo = SqlCareTeamRepository(self.db)
        self.game_repo = SqlGameRepository(self.db)
        self.identity_adapter = MockPatientIdentityAdapter(initial_patient_id="pat-001")

        self.service = CognitiveService(
            game_repo=self.game_repo,
            care_team_repo=self.care_team_repo,
            identity_adapter=self.identity_adapter,
        )

    def test_camera_fallback_when_inactive(self) -> None:
        # Initial: camera active
        self.assertTrue(self.identity_adapter.is_camera_active())
        self.assertEqual(self.service.get_active_patient_id(), "pat-001")

        # Deactivate camera (simulate camera failure, Criterion 7)
        self.identity_adapter.set_camera_active(False)
        self.assertFalse(self.identity_adapter.is_camera_active())
        # Patient ID is still resolvable via authenticated session/PIN fallback
        self.assertEqual(self.service.get_active_patient_id(), "pat-001")

    def test_patient_cannot_view_another_patient(self) -> None:
        # pat-001 viewing pat-001 is permitted
        prog = self.service.get_patient_progress(
            actor_id="pat-001",
            actor_role="patient",
            patient_id="pat-001",
        )
        self.assertIsInstance(prog, list)

        # pat-001 trying to view pat-002 MUST raise PermissionError (Criterion 6)
        with self.assertRaises(PermissionError):
            self.service.get_patient_progress(
                actor_id="pat-001",
                actor_role="patient",
                patient_id="pat-002",
            )

    def test_caregiver_permissions(self) -> None:
        # cg-001 has active permission for pat-001
        self.assertTrue(self.care_team_repo.can_caregiver_view_progress("cg-001", "pat-001"))
        prog = self.service.get_patient_progress(
            actor_id="cg-001",
            actor_role="caregiver",
            patient_id="pat-001",
        )
        self.assertIsInstance(prog, list)

        # cg-002 has NO permission for pat-001 -> MUST raise PermissionError
        self.assertFalse(self.care_team_repo.can_caregiver_view_progress("cg-002", "pat-001"))
        with self.assertRaises(PermissionError):
            self.service.get_patient_progress(
                actor_id="cg-002",
                actor_role="caregiver",
                patient_id="pat-001",
            )

    def test_clinician_permissions_and_expiration(self) -> None:
        # doc-001 has active consent for pat-001
        self.assertTrue(self.care_team_repo.is_clinician_authorized("doc-001", "pat-001"))
        prog = self.service.get_patient_progress(
            actor_id="doc-001",
            actor_role="clinician",
            patient_id="pat-001",
        )
        self.assertIsInstance(prog, list)

        # doc-002 has EXPIRED consent for pat-001 (expired in 2025) -> MUST raise PermissionError
        self.assertFalse(self.care_team_repo.is_clinician_authorized("doc-002", "pat-001"))
        with self.assertRaises(PermissionError):
            self.service.get_patient_progress(
                actor_id="doc-002",
                actor_role="clinician",
                patient_id="pat-001",
            )


if __name__ == "__main__":
    unittest.main()
