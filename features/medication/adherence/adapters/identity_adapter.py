"""
Patient Identity Adapters for Medication Adherence Module.
Provides camera feed integration and simulation mock with strict Anti-Cross-Contamination.
"""

from __future__ import annotations

import logging
import threading
from datetime import datetime, timezone
from typing import Callable, List, Optional

from features.medication.adherence.domain.contracts import IPatientIdentityAdapter

logger = logging.getLogger("IdentityAdapter")


class PatientIdentityAdapter(IPatientIdentityAdapter):
    """
    Thread-safe adapter for managing patient identity streamed from Camera or UI.
    Enforces Anti-Cross-Contamination: Switching patients immediately notifies
    subscribers so active patient views/buffers are cleared before loading the new identity.
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._current_patient_id: Optional[str] = None
        self._last_observed_at: Optional[str] = None
        self._source: str = "init"
        self._confidence: float = 0.0
        self._subscribers: List[Callable[[Optional[str], Optional[str]], None]] = []

    def get_current_patient_id(self) -> Optional[str]:
        with self._lock:
            return self._current_patient_id

    def get_identity_info(self) -> dict:
        with self._lock:
            return {
                "patient_id": self._current_patient_id,
                "observed_at": self._last_observed_at,
                "source": self._source,
                "confidence": self._confidence,
            }

    def set_identity(
        self, patient_id: Optional[str], source: str = "camera", confidence: float = 1.0
    ) -> None:
        """
        Set active patient identity. If patient changes, trigger callbacks to purge
        old session and prevent cross-contamination.
        """
        old_id = None
        notify = False

        with self._lock:
            norm_id = patient_id.strip() if patient_id else None
            if norm_id != self._current_patient_id:
                old_id = self._current_patient_id
                self._current_patient_id = norm_id
                notify = True

            self._source = source
            self._confidence = confidence
            self._last_observed_at = datetime.now(timezone.utc).isoformat()

        if notify:
            logger.info("Patient identity changed: '%s' -> '%s' (source=%s)", old_id, norm_id, source)
            for cb in self._subscribers:
                try:
                    cb(old_id, norm_id)
                except Exception as e:
                    logger.error("Error in identity change subscriber: %s", e)

    def subscribe_identity_changed(
        self, callback: Callable[[Optional[str], Optional[str]], None]
    ) -> None:
        with self._lock:
            if callback not in self._subscribers:
                self._subscribers.append(callback)


class MockPatientIdentityAdapter(PatientIdentityAdapter):
    """
    Mock Identity Adapter for unit tests and offline UI development.
    Enables manual switching between patients or simulating camera occlusion.
    """

    def simulate_camera_recognition(self, patient_id: str, confidence: float = 0.95) -> None:
        self.set_identity(patient_id=patient_id, source="camera_mock", confidence=confidence)

    def simulate_camera_loss(self) -> None:
        self.set_identity(patient_id=None, source="camera_loss", confidence=0.0)

    def simulate_manual_selection(self, patient_id: str) -> None:
        self.set_identity(patient_id=patient_id, source="manual_selection", confidence=1.0)
