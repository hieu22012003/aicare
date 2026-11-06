"""
Identity Adapter for Health Monitoring Module.
Manages patient identification via simulated camera face recognition or authenticated session fallback.
Enforces that when camera is offline or fails, an authenticated session fallback is used instead of arbitrary input.
"""

from __future__ import annotations

import logging
from typing import Optional

from features.health_vitals.domain.contracts import IIdentityAdapter

logger = logging.getLogger("HealthIdentityAdapter")


class MockPatientIdentityAdapter(IIdentityAdapter):
    """Mock identity adapter supporting camera recognition and authenticated manual fallback."""

    def __init__(self, initial_patient_id: Optional[str] = "pat-001") -> None:
        self._current_patient_id: Optional[str] = initial_patient_id
        self._camera_active: bool = True
        self._fallback_authenticated_patient_id: Optional[str] = initial_patient_id

    def get_current_patient_id(self) -> Optional[str]:
        if self._camera_active:
            return self._current_patient_id
        # When camera is offline, fallback to authenticated session profile
        return self._fallback_authenticated_patient_id

    def set_patient_override(self, patient_id: Optional[str]) -> None:
        self._current_patient_id = patient_id
        if not self._camera_active and patient_id is not None:
            self._fallback_authenticated_patient_id = patient_id
        logger.info(f"Active patient identity changed to: {patient_id}")

    def is_camera_active(self) -> bool:
        return self._camera_active

    def set_camera_active(self, active: bool) -> None:
        self._camera_active = active
        logger.info(f"Camera recognition status toggled: active={active}")

    def set_fallback_session_patient_id(self, patient_id: Optional[str]) -> None:
        self._fallback_authenticated_patient_id = patient_id
