"""
Unit tests for StateService in AI Rehabilitation Assistant.
Verifies shared state initialization, updates, and persistence.
"""

from __future__ import annotations

import unittest
from pathlib import Path
import tempfile

from shared.services.state_service import StateService


class TestStateService(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.state_file = Path(self.temp_dir.name) / "state.json"
        self.service = StateService(state_file=self.state_file)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_default_state(self):
        state = self.service.get_state()
        self.assertIn("patient_id", state)
        self.assertIn("name", state)
        self.assertIn("current_emotion", state)
        self.assertIn("active_exercise", state)
        self.assertIn("repetitions", state)
        self.assertIn("score", state)
        self.assertEqual(state["repetitions"], 0)
        self.assertEqual(state["score"], 0.0)

    def test_update_state(self):
        updated = self.service.update_state(
            current_emotion="Vui vẻ",
            active_exercise="arm_raise",
            repetitions=5,
            score=92.5,
        )
        self.assertEqual(updated["current_emotion"], "Vui vẻ")
        self.assertEqual(updated["active_exercise"], "arm_raise")
        self.assertEqual(updated["repetitions"], 5)
        self.assertEqual(updated["score"], 92.5)

        # Check persistence across fresh read
        loaded = self.service.get_state()
        self.assertEqual(loaded["current_emotion"], "Vui vẻ")
        self.assertEqual(loaded["repetitions"], 5)
        self.assertEqual(loaded["score"], 92.5)


if __name__ == "__main__":
    unittest.main()
