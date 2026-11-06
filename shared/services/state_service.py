"""
State Service for AI Rehabilitation & Safety Assistant.
Manages global shared state synchronization between vision modules, fall monitor, and Dashboard.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

from shared.config import DEFAULT_PATIENT_ID, DEFAULT_PATIENT_NAME, SHARED_DIR

logger = logging.getLogger("StateService")


class StateService:
    """Provides thread-safe access and persistence for shared/state.json."""

    def __init__(self, state_file: Optional[Path] = None) -> None:
        self.state_file: Path = state_file or (SHARED_DIR / "state.json")
        self._ensure_file()

    def _ensure_file(self) -> None:
        """Ensure state.json exists with valid schema."""
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        if not self.state_file.exists():
            default_state = {
                "patient_id": DEFAULT_PATIENT_ID,
                "user_id": DEFAULT_PATIENT_ID,
                "name": DEFAULT_PATIENT_NAME,
                "current_emotion": "Bình thường",
                "emotion_confidence": 0.85,
                # Fall / Safety
                "fall_state": "NORMAL",
                "fall_confidence": 0.0,
                "last_fall_event": "Không có",
                "caregiver_email": "",
                "email_status": "Ready",
                # Rehabilitation
                "active_exercise": None,
                "exercise_stage": "idle",
                "repetitions": 0,
                "correct_reps": 0,
                "incorrect_reps": 0,
                "score": 0.0,
                "form_feedback": "Sẵn sàng tập luyện",
                "last_exercise": "Chưa có",
                "camera_mode": "monitor",
                "last_update": datetime.now().isoformat(),
            }
            with open(self.state_file, "w", encoding="utf-8") as f:
                json.dump(default_state, f, ensure_ascii=False, indent=2)

    def get_state(self) -> Dict[str, Any]:
        """Read and parse the current global state."""
        try:
            with open(self.state_file, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.error("Error reading state file: %s", e)
            return {
                "patient_id": DEFAULT_PATIENT_ID,
                "user_id": DEFAULT_PATIENT_ID,
                "name": DEFAULT_PATIENT_NAME,
                "current_emotion": "Bình thường",
                "emotion_confidence": 0.85,
                "fall_state": "NORMAL",
                "fall_confidence": 0.0,
                "last_fall_event": "Không có",
                "caregiver_email": "",
                "email_status": "Ready",
                "active_exercise": None,
                "exercise_stage": "idle",
                "repetitions": 0,
                "correct_reps": 0,
                "incorrect_reps": 0,
                "score": 0.0,
                "form_feedback": "Sẵn sàng tập luyện",
                "last_exercise": "Chưa có",
                "camera_mode": "monitor",
                "last_update": datetime.now().isoformat(),
            }

    def update_state(self, **kwargs: Any) -> Dict[str, Any]:
        """Update selected fields in shared state."""
        state = self.get_state()
        state.update(kwargs)
        state["last_update"] = datetime.now().isoformat()

        try:
            with open(self.state_file, "w", encoding="utf-8") as f:
                json.dump(state, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error("Failed to write state file: %s", e)

        return state
