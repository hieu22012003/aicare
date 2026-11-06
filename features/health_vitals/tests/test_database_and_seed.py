"""
Unit tests for Database Schema, Foreign Keys, CHECK constraints, and Seed Data.
"""

from __future__ import annotations

import sys
from pathlib import Path

root_dir = Path(__file__).resolve().parents[3]
if str(root_dir) not in sys.path:
    sys.path.insert(0, str(root_dir))

import sqlite3
import unittest

from features.health_vitals.database.connection import DatabaseManager
from features.health_vitals.database.seed_data import seed_database


class TestDatabaseAndSeed(unittest.TestCase):
    def setUp(self) -> None:
        self.db = DatabaseManager(":memory:")
        seed_database(self.db)

    def test_all_tables_exist(self) -> None:
        expected_tables = {
            "users",
            "patients",
            "caregivers",
            "clinicians",
            "patient_caregivers",
            "care_team",
            "observation_types",
            "measurement_groups",
            "health_observations",
            "symptom_entries",
            "clinician_reviews",
            "clinical_messages",
            "message_receipts",
            "monitoring_plans",
            "patient_thresholds",
        }
        with self.db.connect() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
            tables = {row["name"] for row in cursor.fetchall()}
            for table in expected_tables:
                self.assertIn(table, tables, f"Bảng {table} phải tồn tại trong CSDL")

    def test_foreign_key_enforcement(self) -> None:
        """Violating foreign key constraint must raise sqlite3.IntegrityError."""
        with self.db.connect() as conn:
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute(
                    """
                    INSERT INTO measurement_groups (
                        group_id, patient_id, measured_at, recorded_at,
                        entered_by_user_id, source_type, device_label,
                        status, client_request_id
                    ) VALUES ('grp-fake', 'non_existent_patient', '2026-10-01T00:00:00Z', '2026-10-01T00:00:00Z',
                             'usr-pat-001', 'manual_patient', 'Test', 'active', 'req-fk-fail')
                    """
                )

    def test_check_constraints_enforcement(self) -> None:
        """CHECK constraint on source_type must reject invalid values."""
        with self.db.connect() as conn:
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute(
                    """
                    INSERT INTO measurement_groups (
                        group_id, patient_id, measured_at, recorded_at,
                        entered_by_user_id, source_type, device_label,
                        status, client_request_id
                    ) VALUES ('grp-chk', 'pat-001', '2026-10-01T00:00:00Z', '2026-10-01T00:00:00Z',
                             'usr-pat-001', 'invalid_source_type_here', 'Test', 'active', 'req-chk-fail')
                    """
                )

    def test_unique_component_per_group(self) -> None:
        """UNIQUE(group_id, observation_type_id) must prevent duplicate types in the same group."""
        with self.db.connect() as conn:
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute(
                    """
                    INSERT INTO health_observations (
                        observation_id, group_id, patient_id, observation_type_id,
                        value_numeric, unit_code, status
                    ) VALUES ('obs-dup', 'grp-today-latest', 'pat-001', 'ot-sys', 130.0, 'mmHg', 'active')
                    """
                )


if __name__ == "__main__":
    unittest.main()
