"""
Application State Manager for AI Rehabilitation & Safety Assistant.
Provides reactive state access and formatted metrics for the unified Dashboard.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from shared.config import (
    DEFAULT_PATIENT_ID,
    DEFAULT_PATIENT_NAME,
    SHARED_DATA_DIR,
    SHARED_RESULTS_DIR,
    SHARED_DIR,
)
from shared.services.state_service import StateService


class AppState:
    """Central lightweight state accessor for AI Rehabilitation & Safety Assistant."""

    def __init__(self) -> None:
        self.state_service = StateService()
        self.patient_file = SHARED_DATA_DIR / "patient.json"

    def get_patient_info(self) -> Dict[str, Any]:
        """Read current registered patient / user profile."""
        if self.patient_file.exists():
            try:
                with open(self.patient_file, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
        return {
            "user_id": DEFAULT_PATIENT_ID,
            "patient_id": DEFAULT_PATIENT_ID,
            "name": DEFAULT_PATIENT_NAME,
            "age": 70,
            "caregiver": {
                "name": "Chưa đăng ký",
                "relationship": "Người thân",
                "email": "contact-51ee0022@example.com",
            },
        }

    def get_realtime_metrics(self) -> Dict[str, Any]:
        """Read all live metrics from state.json and results/."""
        state = self.state_service.get_state()
        return {
            "current_user_id": state.get("patient_id") or state.get("user_id", DEFAULT_PATIENT_ID),
            "current_user_name": state.get("name", DEFAULT_PATIENT_NAME),
            "name": state.get("name", DEFAULT_PATIENT_NAME),
            "patient_id": state.get("patient_id", DEFAULT_PATIENT_ID),
            # Emotion
            "emotion": state.get("current_emotion", "Bình thường"),
            "current_emotion": state.get("current_emotion", "Bình thường"),
            "emotion_confidence": state.get("emotion_confidence", 0.85),
            # Fall & Safety
            "fall_state": state.get("fall_state", "NORMAL"),
            "fall_confidence": state.get("fall_confidence", 0.0),
            "last_fall_event": state.get("last_fall_event", "Không có"),
            "caregiver_email": state.get("caregiver_email", ""),
            "email_status": state.get("email_status", "Ready"),
            # Rehabilitation
            "exercise": state.get("active_exercise", "arm_raise"),
            "active_exercise": state.get("active_exercise", "arm_raise"),
            "exercise_stage": state.get("exercise_stage", "idle"),
            "repetitions": state.get("repetitions", 0),
            "correct_repetitions": state.get("correct_reps", 0),
            "correct_reps": state.get("correct_reps", 0),
            "incorrect_reps": state.get("incorrect_reps", 0),
            "form_feedback": state.get("form_feedback", "Sẵn sàng tập luyện"),
            # Camera mode
            "camera_mode": state.get("camera_mode", "monitor"),
            "unobservable_reason": state.get("unobservable_reason", ""),
        }

    def update_fall_state(
        self,
        fall_state: str,
        confidence: float = 0.0,
        unobservable_reason: str = "",
    ) -> Dict[str, Any]:
        """Update fall monitoring state and confidence."""
        return self.state_service.update_state(
            fall_state=fall_state,
            fall_confidence=confidence,
            unobservable_reason=unobservable_reason,
        )

    def update_email_status(
        self,
        email_status: str,
        caregiver_email: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Update caregiver email status."""
        updates: Dict[str, Any] = {"email_status": email_status}
        if caregiver_email is not None:
            updates["caregiver_email"] = caregiver_email
        return self.state_service.update_state(**updates)
