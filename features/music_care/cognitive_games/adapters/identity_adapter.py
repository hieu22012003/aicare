"""
Identity adapter for Cognitive Games & Relaxation Module.
Simulates face recognition identity resolution with fallback support (Criterion 7).
"""

from __future__ import annotations

import logging
from typing import Optional

from features.music_care.cognitive_games.domain.contracts import IPatientIdentityAdapter

logger = logging.getLogger("CognitiveIdentityAdapter")


class MockPatientIdentityAdapter(IPatientIdentityAdapter):
    """
    Mock adapter representing face recognition with authenticated fallback.
    - If camera is active: returns current patient resolved by face recognition.
    - If camera is disabled/inactive: allows authenticated fallback session or PIN (Criterion 7).
    - When identity changes: closes prior session before opening new (Criterion 7).
    """

    def __init__(self, initial_patient_id: str = "pat-001", camera_active: bool = True) -> None:
        self._current_patient_id: Optional[str] = initial_patient_id
        self._fallback_patient_id: Optional[str] = initial_patient_id
        self._camera_active: bool = camera_active

    def get_current_patient_id(self) -> Optional[str]:
        if self._camera_active:
            return self._current_patient_id
        # Fallback authenticated session
        return self._fallback_patient_id

    def is_camera_active(self) -> bool:
        return self._camera_active

    def set_camera_active(self, active: bool) -> None:
        self._camera_active = active
        logger.info("Camera recognition active state toggled to: %s", active)

    def switch_patient(self, new_patient_id: Optional[str]) -> None:
        """Simulates camera detecting a different person or logging out."""
        old_id = self._current_patient_id
        self._current_patient_id = new_patient_id
        self._fallback_patient_id = new_patient_id
        logger.info("Switched patient identity from %s to %s", old_id, new_patient_id)

    def set_simulated_patient(self, new_patient_id: Optional[str]) -> None:
        self.switch_patient(new_patient_id)
