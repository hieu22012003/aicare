"""
Abstract Contracts and Interfaces for Medication Adherence and Monitoring Module.
Ensures zero coupling with concrete database engines or external hardware pipelines.
"""

from __future__ import annotations

import abc
from datetime import datetime
from typing import Callable, List, Optional, Tuple

from features.medication.adherence.domain.models import (
    Alert,
    Caregiver,
    DoseEvent,
    DoseOccurrence,
    DoseState,
    Medication,
    MedicationSchedule,
    NotificationDelivery,
    Patient,
    PatientCaregiver,
    Prescription,
    PrescriptionItem,
)


class IPatientIdentityAdapter(abc.ABC):
    """Contract for external patient identification input (e.g. Camera or Mock)."""

    @abc.abstractmethod
    def get_current_patient_id(self) -> Optional[str]:
        """Return currently identified patient ID, or None if no patient active."""
        pass

    @abc.abstractmethod
    def set_identity(self, patient_id: Optional[str], source: str = "camera", confidence: float = 1.0) -> None:
        """Update active patient identity."""
        pass

    @abc.abstractmethod
    def subscribe_identity_changed(self, callback: Callable[[Optional[str], Optional[str]], None]) -> None:
        """Register listener for identity transitions (old_patient_id, new_patient_id)."""
        pass


class IPatientRepository(abc.ABC):
    """Contract for Patient entities storage and retrieval."""

    @abc.abstractmethod
    def get_by_id(self, patient_id: str) -> Optional[Patient]:
        pass

    @abc.abstractmethod
    def list_all(self) -> List[Patient]:
        pass

    @abc.abstractmethod
    def save(self, patient: Patient) -> None:
        pass


class ICaregiverRepository(abc.ABC):
    """Contract for Caregiver entities and PatientCaregiver relationship queries."""

    @abc.abstractmethod
    def get_by_id(self, caregiver_id: str) -> Optional[Caregiver]:
        pass

    @abc.abstractmethod
    def get_active_caregivers_for_patient(
        self, patient_id: str, at_time: Optional[datetime] = None
    ) -> List[PatientCaregiver]:
        """
        Query all active, consented caregivers with verified contact info,
        sorted by notification_priority ASC.
        """
        pass

    @abc.abstractmethod
    def list_caregivers_for_patient(self, patient_id: str) -> List[PatientCaregiver]:
        """List all caregivers for a patient including expired ones for audit/management."""
        pass


class IMedicationRepository(abc.ABC):
    """Contract for Prescription, Schedule, Occurrence, Event, and Alert persistence."""

    @abc.abstractmethod
    def get_medication(self, medication_id: str) -> Optional[Medication]:
        pass

    @abc.abstractmethod
    def save_medication(self, medication: Medication) -> None:
        pass

    @abc.abstractmethod
    def list_medications(self) -> List[Medication]:
        pass

    @abc.abstractmethod
    def save_prescription(
        self, prescription: Prescription, items: List[Tuple[PrescriptionItem, MedicationSchedule]]
    ) -> None:
        pass

    @abc.abstractmethod
    def update_schedule(self, schedule: MedicationSchedule) -> MedicationSchedule:
        """Save a new revision of schedule, cancelling future occurrences of superseded revision."""
        pass

    @abc.abstractmethod
    def list_schedules_for_patient(self, patient_id: str, active_only: bool = True) -> List[MedicationSchedule]:
        pass

    @abc.abstractmethod
    def get_due_occurrences(
        self, patient_id: Optional[str] = None, start_utc: Optional[str] = None, end_utc: Optional[str] = None
    ) -> List[DoseOccurrence]:
        pass

    @abc.abstractmethod
    def get_occurrence(self, occurrence_id: str) -> Optional[DoseOccurrence]:
        pass

    @abc.abstractmethod
    def save_occurrences(self, occurrences: List[DoseOccurrence]) -> None:
        pass

    @abc.abstractmethod
    def update_occurrence_state(
        self,
        occurrence_id: str,
        new_state: DoseState,
        action_actor: Optional[str] = None,
        action_notes: Optional[str] = None,
        snoozed_until_utc: Optional[str] = None,
    ) -> DoseOccurrence:
        pass

    @abc.abstractmethod
    def record_event(self, event: DoseEvent) -> None:
        pass

    @abc.abstractmethod
    def list_events_for_occurrence(self, occurrence_id: str) -> List[DoseEvent]:
        pass

    @abc.abstractmethod
    def create_alert(self, alert: Alert) -> Alert:
        pass

    @abc.abstractmethod
    def get_alert_by_occurrence(self, occurrence_id: str, alert_type: str) -> Optional[Alert]:
        pass

    @abc.abstractmethod
    def list_active_alerts(self, patient_id: Optional[str] = None) -> List[Alert]:
        pass

    @abc.abstractmethod
    def create_deliveries(self, deliveries: List[NotificationDelivery]) -> None:
        pass

    @abc.abstractmethod
    def update_delivery(self, delivery: NotificationDelivery) -> None:
        pass

    @abc.abstractmethod
    def record_coverage_gap(self, start_utc: str, end_utc: str, event_type: str, notes: str = "") -> None:
        pass


class INotificationGateway(abc.ABC):
    """Contract for Caregiver alert delivery (Email, SMS, Mock)."""

    @abc.abstractmethod
    def send_alert(self, delivery: NotificationDelivery, alert: Alert, patient: Patient) -> NotificationDelivery:
        """Attempt delivery of alert, returning updated delivery status and metadata."""
        pass
