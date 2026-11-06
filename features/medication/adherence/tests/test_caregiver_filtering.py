"""
Tests for Caregiver Filtering: Active vs Expired Relationships, Verification, and Priority.
"""

import unittest
from datetime import datetime, timezone

from features.medication.adherence.adapters.caregiver_repository import SqlCaregiverRepository
from features.medication.adherence.database.connection import DatabaseManager
from features.medication.adherence.database.seed_data import seed_database


class TestCaregiverFiltering(unittest.TestCase):
    def setUp(self):
        self.db = DatabaseManager(":memory:")
        seed_database(self.db)
        self.caregiver_repo = SqlCaregiverRepository(self.db)
        self.now = datetime(2026, 10, 5, 12, 0, tzinfo=timezone.utc)

    def test_expired_caregiver_excluded_from_active_list(self):
        # In seed data:
        # caregiver_001 has active relationship until 2099
        # caregiver_003 has expired relationship that ended on 2025-01-01
        active_caregivers = self.caregiver_repo.get_active_caregivers_for_patient("patient_001", at_time=self.now)

        # Must only return caregiver_001
        caregiver_ids = [cg.caregiver_id for cg in active_caregivers]
        self.assertIn("caregiver_001", caregiver_ids)
        self.assertNotIn("caregiver_003", caregiver_ids)

    def test_unverified_email_caregiver_excluded(self):
        # Set caregiver_001 email_verified to 0
        with self.db.connect() as conn:
            conn.execute("UPDATE caregivers SET email_verified = 0 WHERE caregiver_id = 'caregiver_001'")

        active_caregivers = self.caregiver_repo.get_active_caregivers_for_patient("patient_001", at_time=self.now)
        self.assertEqual(len(active_caregivers), 0)

    def test_all_caregivers_listed_for_management(self):
        all_caregivers = self.caregiver_repo.list_caregivers_for_patient("patient_001")
        cg_ids = [cg.caregiver_id for cg in all_caregivers]
        # Should include both active and expired for administrative viewing
        self.assertIn("caregiver_001", cg_ids)
        self.assertIn("caregiver_003", cg_ids)


if __name__ == "__main__":
    unittest.main()
