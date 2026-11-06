"""
Tests for 3NF Relational Database Schema, Constraints, and Seed Data.
"""

import sqlite3
import unittest

from features.medication.adherence.database.connection import DatabaseManager
from features.medication.adherence.database.seed_data import seed_database


class TestDatabaseAndSeed(unittest.TestCase):
    def setUp(self):
        self.db = DatabaseManager(":memory:")

    def test_schema_creates_all_tables(self):
        with self.db.connect() as conn:
            rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
            table_names = {r["name"] for r in rows}
            expected_tables = {
                "users", "patients", "caregivers", "clinicians", "patient_caregivers",
                "care_team", "medications", "prescriptions", "prescription_items",
                "medication_schedules", "dose_occurrences", "dose_events", "alerts",
                "notification_deliveries", "coverage_audit", "meta"
            }
            self.assertTrue(expected_tables.issubset(table_names), f"Missing tables: {expected_tables - table_names}")

    def test_foreign_key_enforcement(self):
        with self.db.connect() as conn:
            # Trying to insert a patient with non-existent user_id
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute(
                    "INSERT INTO patients (patient_id, user_id, full_name) VALUES ('p_test', 'non_existent_user', 'Test')"
                )

    def test_check_constraint_enforcement(self):
        seed_database(self.db)
        with self.db.connect() as conn:
            # Trying to insert a prescription item with non-positive dose_amount
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute(
                    """
                    INSERT INTO prescription_items (
                        prescription_item_id, prescription_id, medication_id,
                        dose_amount, dose_unit, route_of_administration,
                        original_instructions, start_date, status
                    ) VALUES ('pi_err', 'rx_001', 'med_001', -1.0, 'viên', 'Đường uống', 'Uống lỗi', '2026-10-01', 'ACTIVE')
                    """
                )

    def test_seed_data_counts_and_idempotency(self):
        # 1st run
        seed_database(self.db)
        with self.db.connect() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM patients").fetchone()[0], 2)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM caregivers").fetchone()[0], 3)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM patient_caregivers").fetchone()[0], 3)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM medications").fetchone()[0], 2)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM prescriptions").fetchone()[0], 2)

        # 2nd run (idempotent)
        seed_database(self.db)
        with self.db.connect() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM patients").fetchone()[0], 2)


if __name__ == "__main__":
    unittest.main()
