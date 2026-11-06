"""
Repository and Service contracts (interfaces) for Health Monitoring Module.
Decouples domain logic from SQLite implementation and external identity frameworks.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Dict, List, Optional

from features.health_vitals.domain.models import (
    AggregatedPoint,
    BloodPressureReading,
    ClinicalMessage,
    Clinician,
    ClinicianReview,
    HealthObservation,
    MeasurementComponent,
    MeasurementGroup,
    ObservationType,
    Patient,
    PatientCardSummary,
    SymptomEntry,
)


class IPatientAccessRepository(ABC):
    @abstractmethod
    def get_patient(self, patient_id: str) -> Optional[Patient]:
        ...

    @abstractmethod
    def can_user_access_patient(self, user_id: str, patient_id: str, required_permission: str = "can_view_health") -> bool:
        ...


class ICareTeamRepository(ABC):
    @abstractmethod
    def is_clinician_authorized(self, clinician_id: str, patient_id: str) -> bool:
        ...

    @abstractmethod
    def get_clinician_patients(self, clinician_id: str) -> List[PatientCardSummary]:
        ...

    @abstractmethod
    def get_clinician(self, clinician_id: str) -> Optional[Clinician]:
        ...


class IObservationRepository(ABC):
    @abstractmethod
    def get_observation_types(self) -> Dict[str, ObservationType]:
        ...

    @abstractmethod
    def create_measurement_group(
        self,
        group: MeasurementGroup,
        components: List[MeasurementComponent],
    ) -> MeasurementGroup:
        ...

    @abstractmethod
    def get_group_by_request_id(self, client_request_id: str) -> Optional[MeasurementGroup]:
        ...

    @abstractmethod
    def get_observations(
        self,
        patient_id: str,
        type_codes: Optional[List[str]] = None,
        start_time: Optional[str] = None,
        end_time: Optional[str] = None,
        status: str = "active",
        limit: int = 100,
        offset: int = 0,
    ) -> List[HealthObservation]:
        ...

    @abstractmethod
    def get_blood_pressure_readings(
        self,
        patient_id: str,
        start_time: Optional[str] = None,
        end_time: Optional[str] = None,
        status: str = "active",
        limit: int = 100,
    ) -> List[BloodPressureReading]:
        ...

    @abstractmethod
    def correct_observation(
        self,
        observation_id: str,
        new_value: float,
        new_unit: str,
        entered_by_user_id: str,
        client_request_id: str,
        notes: str = "",
    ) -> HealthObservation:
        ...

    @abstractmethod
    def create_review(self, review: ClinicianReview) -> ClinicianReview:
        ...

    @abstractmethod
    def get_latest_review(self, patient_id: str, clinician_id: str) -> Optional[ClinicianReview]:
        ...

    @abstractmethod
    def create_message(self, message: ClinicalMessage) -> ClinicalMessage:
        ...

    @abstractmethod
    def get_messages(self, patient_id: str, limit: int = 50) -> List[ClinicalMessage]:
        ...

    @abstractmethod
    def mark_message_read(self, message_id: str, user_id: str) -> None:
        ...

    @abstractmethod
    def record_symptom(self, symptom: SymptomEntry) -> SymptomEntry:
        ...

    @abstractmethod
    def get_symptoms(self, patient_id: str, limit: int = 20) -> List[SymptomEntry]:
        ...


class IIdentityAdapter(ABC):
    @abstractmethod
    def get_current_patient_id(self) -> Optional[str]:
        ...

    @abstractmethod
    def set_patient_override(self, patient_id: Optional[str]) -> None:
        ...

    @abstractmethod
    def is_camera_active(self) -> bool:
        ...

    @abstractmethod
    def set_camera_active(self, active: bool) -> None:
        ...
