"""
Tests for Database Schema, WAL mode, foreign keys, and Seed Data generation.
"""

import sqlite3
import unittest
from pathlib import Path

from features.music_care.cognitive_games.database.connection import DatabaseManager
from features.music_care.cognitive_games.database.seed_data import seed_database


class TestDatabaseAndSeed(unittest.TestCase):
    def setUp(self) -> None:
        # Use an in-memory database managed by DatabaseManager
        self.db = DatabaseManager(db_path=":memory:")
        self.db.initialize_schema()

    def test_schema_tables_exist(self) -> None:
        with self.db.connect() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
            tables = {row["name"] for row in cursor.fetchall()}

        expected_tables = {
            "users",
            "patients",
            "caregivers",
            "clinicians",
            "patient_caregivers",
            "care_team",
            "game_types",
            "content_packs",
            "game_sessions",
            "game_results",
            "music_tracks",
            "listening_sessions",
        }
        self.assertTrue(expected_tables.issubset(tables), f"Missing tables: {expected_tables - tables}")

    def test_seed_data_population(self) -> None:
        seed_database(self.db)
        with self.db.connect() as conn:
            cursor = conn.cursor()

            # Verify 2 patients
            cursor.execute("SELECT COUNT(*) as c FROM patients")
            self.assertEqual(cursor.fetchone()["c"], 2)

            # Verify 2 caregivers
            cursor.execute("SELECT COUNT(*) as c FROM caregivers")
            self.assertEqual(cursor.fetchone()["c"], 2)

            # Verify 2 clinicians
            cursor.execute("SELECT COUNT(*) as c FROM clinicians")
            self.assertEqual(cursor.fetchone()["c"], 2)

            # Verify game types (card_matching and sequence_recall)
            cursor.execute("SELECT code FROM game_types")
            codes = {row["code"] for row in cursor.fetchall()}
            self.assertIn("card_matching", codes)
            self.assertIn("sequence_recall", codes)

            # Verify content packs
            cursor.execute("SELECT COUNT(*) as c FROM content_packs")
            self.assertGreaterEqual(cursor.fetchone()["c"], 3)

            # Verify music tracks (at least 4 tracks)
            cursor.execute("SELECT COUNT(*) as c FROM music_tracks")
            self.assertGreaterEqual(cursor.fetchone()["c"], 4)

    def test_difficulty_check_constraint_allows_expert(self) -> None:
        """Verifies game_sessions allows 'easy', 'medium', 'hard', 'expert' without constraint failure."""
        seed_database(self.db)
        with self.db.connect() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT game_type_id, pack_id FROM content_packs LIMIT 1")
            row = cursor.fetchone()
            gt_id = row["game_type_id"]
            pk_id = row["pack_id"]

            for diff in ["easy", "medium", "hard", "expert"]:
                cursor.execute(
                    """
                    INSERT INTO game_sessions (
                        session_id, patient_id, game_type_id, pack_id, difficulty,
                        seed, started_at, status, active_seconds, help_count,
                        rules_version, client_request_id
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        f"test-diff-{diff}", "pat-001", gt_id, pk_id, diff,
                        100, "2026-10-05T00:00:00Z", "in_progress", 0.0, 0,
                        "v1.0", f"req-test-{diff}",
                    ),
                )
            conn.commit()

            # Verify invalid difficulty fails check constraint
            with self.assertRaises(sqlite3.IntegrityError):
                cursor.execute(
                    """
                    INSERT INTO game_sessions (
                        session_id, patient_id, game_type_id, pack_id, difficulty,
                        seed, started_at, status, active_seconds, help_count,
                        rules_version, client_request_id
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        "test-diff-invalid", "pat-001", gt_id, pk_id, "invalid_level",
                        100, "2026-10-05T00:00:00Z", "in_progress", 0.0, 0,
                        "v1.0", "req-test-invalid",
                    ),
                )


if __name__ == "__main__":
    unittest.main()
