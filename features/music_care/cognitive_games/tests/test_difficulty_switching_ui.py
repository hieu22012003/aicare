"""
Tests for UI Difficulty Level Selection & Seamless Transitions:
- CardMatchingWidget: switching among easy, medium, hard, expert
- SequenceRecallWidget: switching among easy, medium, hard, expert
- Ensures no sqlite3.IntegrityError: CHECK constraint failed: difficulty
- Verifies board pair count and sequence length adjust accurately
"""

import os
import sys
import unittest
from pathlib import Path

# Set offscreen platform for headless test execution
os.environ["QT_QPA_PLATFORM"] = "offscreen"

PROJECT_ROOT = Path(__file__).resolve().parents[4]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from PyQt5.QtWidgets import QApplication

from features.music_care.cognitive_games.adapters.care_team_repository import SqlCareTeamRepository
from features.music_care.cognitive_games.adapters.game_repository import SqlGameRepository
from features.music_care.cognitive_games.adapters.identity_adapter import MockPatientIdentityAdapter
from features.music_care.cognitive_games.database.connection import DatabaseManager
from features.music_care.cognitive_games.database.seed_data import seed_database
from features.music_care.cognitive_games.domain.models import DifficultyLevel, GameStatus
from features.music_care.cognitive_games.services.cognitive_service import CognitiveService
from features.music_care.cognitive_games.ui.card_matching_widget import CardMatchingWidget
from features.music_care.cognitive_games.ui.sequence_recall_widget import SequenceRecallWidget

# Ensure QApplication singleton
app = QApplication.instance()
if app is None:
    app = QApplication([sys.argv[0]])


class TestDifficultySwitchingUI(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.db = DatabaseManager(":memory:")
        cls.db.initialize_schema()
        seed_database(cls.db)

        cls.game_repo = SqlGameRepository(cls.db)
        cls.care_team_repo = SqlCareTeamRepository(cls.db)
        cls.identity_adapter = MockPatientIdentityAdapter(initial_patient_id="pat-001")
        cls.cognitive_service = CognitiveService(
            game_repo=cls.game_repo,
            care_team_repo=cls.care_team_repo,
            identity_adapter=cls.identity_adapter,
        )

    def test_card_matching_all_difficulty_levels(self) -> None:
        """Verifies selecting each difficulty level in CardMatchingWidget succeeds without SQLite errors."""
        widget = CardMatchingWidget(service=self.cognitive_service)

        difficulty_expectations = [
            ("easy", 6),     # 3 pairs -> 6 cards
            ("medium", 8),   # 4 pairs -> 8 cards
            ("hard", 12),    # 6 pairs -> 12 cards
            ("expert", 16),  # 8 pairs -> 16 cards
        ]

        for diff, expected_cards in difficulty_expectations:
            # Simulate changing difficulty via combo box
            idx = widget.combo_diff.findData(diff)
            self.assertGreaterEqual(idx, 0, f"Difficulty '{diff}' not found in combo_diff")
            widget.combo_diff.setCurrentIndex(idx)

            # Check that board is rendered with expected card count
            self.assertIsNotNone(widget.current_session)
            self.assertEqual(widget.current_session.difficulty, diff)
            self.assertEqual(len(widget.board), expected_cards)
            self.assertEqual(len(widget.card_buttons), expected_cards)
            self.assertEqual(widget.total_pairs_count, expected_cards // 2)

        widget.deleteLater()

    def test_sequence_recall_all_difficulty_levels(self) -> None:
        """Verifies selecting each difficulty level in SequenceRecallWidget succeeds without SQLite errors."""
        widget = SequenceRecallWidget(service=self.cognitive_service)

        difficulty_expectations = [
            ("easy", 2),    # 2 items
            ("medium", 3),  # 3 items
            ("hard", 4),    # 4 items
            ("expert", 5),  # 5 items
        ]

        for diff, expected_length in difficulty_expectations:
            # Simulate changing difficulty via combo box
            idx = widget.combo_diff.findData(diff)
            self.assertGreaterEqual(idx, 0, f"Difficulty '{diff}' not found in combo_diff")
            widget.combo_diff.setCurrentIndex(idx)

            # Check session and sequence length
            self.assertIsNotNone(widget.current_session)
            self.assertEqual(widget.current_session.difficulty, diff)
            self.assertEqual(len(widget.target_seq), expected_length)
            self.assertEqual(widget.current_round, 1)

        widget.deleteLater()

    def test_clean_session_transition_on_difficulty_change(self) -> None:
        """Verifies changing difficulty mid-game properly marks prior session as abandoned."""
        widget = CardMatchingWidget(service=self.cognitive_service)

        # Start on medium
        widget.start_game(patient_id="pat-001", difficulty="medium")
        first_session_id = widget.current_session.session_id

        # Switch to expert
        widget.start_game(patient_id="pat-001", difficulty="expert")
        second_session_id = widget.current_session.session_id

        self.assertNotEqual(first_session_id, second_session_id)

        # Verify first session is recorded as abandoned in DB
        old_sess = self.game_repo.get_session(first_session_id)
        self.assertIsNotNone(old_sess)
        self.assertEqual(old_sess.status, GameStatus.ABANDONED.value)

        # Verify second session is in progress
        new_sess = self.game_repo.get_session(second_session_id)
        self.assertIsNotNone(new_sess)
        self.assertEqual(new_sess.status, GameStatus.IN_PROGRESS.value)
        self.assertEqual(new_sess.difficulty, "expert")

        widget.deleteLater()


if __name__ == "__main__":
    unittest.main()
