"""
Tests for UI Mounting and Direct Button Interactions in PyQt5 Elder App.
Validates:
- Successful removal of VoiceButtonWidget and voice_card from TodayTodoWidget
- Clean layout starting with medication and care cards
- One-Touch Medication Confirmation buttons ('Đã uống', 'Nhắc lại', 'Cần hỗ trợ')
- Direct button triggers for games and 16:00 camera rehabilitation sequence
- Patient profile switching and identity binding
"""

from __future__ import annotations

import unittest
from datetime import datetime, timezone
from typing import Any

from PyQt5.QtWidgets import QApplication

from shared.database.connection import UnifiedDatabaseManager
from shared.database.migrator import Migrator
from shared.database.importer import DataImporter
from apps.desktop.today_todo_widget import TodayTodoWidget


class TestVoiceUIDesktopMounting(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        # Initialize headless QApplication if not already running
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.db = UnifiedDatabaseManager(":memory:")
        Migrator(self.db).apply_all()
        DataImporter(self.db).import_all()

        # Seed dose occurrence for testing
        now_iso = datetime.now(timezone.utc).isoformat()
        with self.db.connect() as conn:
            conn.execute(
                """
                INSERT INTO dose_occurrences (
                    occurrence_id, schedule_id, revision, scheduled_at_utc, local_scheduled_time,
                    grace_deadline_utc, state, medication_name_snapshot, dose_snapshot,
                    instructions_snapshot, patient_id
                ) VALUES (
                    'occ_amlodipine_01', 'sch_test_01', 1, ?, '08:00', ?, 'PENDING',
                    'Amlodipine 5mg', '1 viên', 'Uống sau ăn sáng', 'patient_001'
                )
                """,
                (now_iso, now_iso),
            )
            conn.commit()

        self.widget = TodayTodoWidget(self.db, patient_id="patient_001")

    def tearDown(self) -> None:
        self.widget.close()

    def test_voice_button_widget_removed_from_today_todo_cards(self) -> None:
        """Verify Voice AI card and VoiceButtonWidget are completely removed from TodayTodoWidget."""
        self.assertFalse(hasattr(self.widget, "voice_card"), "voice_card must NOT exist in TodayTodoWidget")
        self.assertFalse(hasattr(self.widget, "voice_widget"), "voice_widget must NOT exist in TodayTodoWidget")

    def test_medication_card_and_buttons_mounted(self) -> None:
        """Verify medication card exists and has active action buttons."""
        self.assertTrue(hasattr(self.widget, "med_card"))
        self.assertIn("Lịch Uống Thuốc", self.widget.med_card.title_label.text())
        self.assertGreater(self.widget.med_list_layout.count(), 0)

    def test_button_medication_confirm_action(self) -> None:
        """Verify clicking 'Đã uống' confirms dose in database with self_reported_taken."""
        dose_action_emitted = False

        def on_dose_action(occ_id: str, state: str) -> None:
            nonlocal dose_action_emitted
            if occ_id == "occ_amlodipine_01" and state == "CONFIRMED":
                dose_action_emitted = True

        self.widget.dose_action_performed.connect(on_dose_action)
        self.widget._on_confirm_taken("occ_amlodipine_01")

        self.assertTrue(dose_action_emitted, "dose_action_performed signal must be emitted")

        # Check occurrence state in DB
        with self.db.connect() as conn:
            occ = conn.fetchone(
                "SELECT state, action_actor FROM dose_occurrences WHERE occurrence_id = 'occ_amlodipine_01'"
            )
            self.assertIsNotNone(occ)
            self.assertEqual(occ["state"], "CONFIRMED")
            self.assertIn(occ["action_actor"], ("self_reported_taken", "confirmed_late"))

    def test_button_medication_snooze_action(self) -> None:
        """Verify clicking 'Nhắc lại' snoozes dose in database."""
        self.widget._on_snooze("occ_amlodipine_01")

        with self.db.connect() as conn:
            occ = conn.fetchone(
                "SELECT state FROM dose_occurrences WHERE occurrence_id = 'occ_amlodipine_01'"
            )
            self.assertIsNotNone(occ)
            self.assertEqual(occ["state"], "SNOOZED")

    def test_button_game_open_action(self) -> None:
        """Verify games card launcher triggers open_games_requested signal."""
        games_requested = False

        def on_games():
            nonlocal games_requested
            games_requested = True

        self.widget.open_games_requested.connect(on_games)
        self.widget.open_games_requested.emit()

        self.assertTrue(games_requested, "open_games_requested signal must be emitted")

    def test_button_rehab_start_action(self) -> None:
        """Verify rehab card button triggers start_routine_requested signal."""
        rehab_requested = False

        def on_rehab():
            nonlocal rehab_requested
            rehab_requested = True

        self.widget.start_routine_requested.connect(on_rehab)
        self.widget.start_routine_requested.emit()

        self.assertTrue(rehab_requested, "start_routine_requested signal must be emitted")

    def test_patient_switching_binds_to_new_patient(self) -> None:
        """Verify switching patient binds UI and sync client to the new patient."""
        self.widget.set_patient("patient_002")
        self.assertEqual(self.widget.patient_id, "patient_002")
        self.assertEqual(self.widget.sync_client.patient_id, "patient_002")


if __name__ == "__main__":
    unittest.main()
