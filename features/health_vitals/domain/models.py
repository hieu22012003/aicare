"""
Domain models and dataclasses for Health Monitoring & Remote Doctor Review (P0-P2).
Represents observations, measurement groups, reviews, messages, and aggregation units.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import List, Optional


class SourceType(str, Enum):
    MANUAL_PATIENT = "manual_patient"
    MANUAL_CAREGIVER = "manual_caregiver"
    DEVICE_IMPORT = "device_import"


class ObservationStatus(str, Enum):
    ACTIVE = "active"
    CORRECTED = "corrected"
    ENTERED_IN_ERROR = "entered_in_error"


class ReviewStatus(str, Enum):
    NO_DATA = "chưa có dữ liệu"
    UNREVIEWED = "chưa xem"
    REVIEWED = "đã xem"
    RESPONDED = "đã phản hồi"


class MessageStatus(str, Enum):
    SENT = "sent"
    DELIVERED = "delivered"
    READ = "read"


class MessageVisibility(str, Enum):
    PATIENT_AND_CAREGIVERS = "PATIENT_AND_CAREGIVERS"
    PATIENT_ONLY = "PATIENT_ONLY"
    CLINICIAN_ONLY = "CLINICIAN_ONLY"


class Resolution(str, Enum):
    RAW = "raw"
    DAY = "day"
    WEEK = "week"


@dataclass(frozen=True)
class User:
    user_id: str
    email: str
    phone: str
    full_name: str
    is_active: bool
    created_at: str


@dataclass(frozen=True)
class Patient:
    patient_id: str
    user_id: Optional[str]
    full_name: str
    date_of_birth: Optional[str]
    gender: str
    timezone: str
    status: str
    notes: str = ""


@dataclass(frozen=True)
class Caregiver:
    caregiver_id: str
    user_id: str
    email_verified: bool
    phone_verified: bool


@dataclass(frozen=True)
class Clinician:
    clinician_id: str
    user_id: str
    full_name: str
    workplace: str
    specialty: str
    license_number: str
    is_verified: bool


@dataclass(frozen=True)
class ObservationType:
    observation_type_id: str
    code: str
    name: str
    standard_unit: str
    min_plausible_value: float
    max_plausible_value: float
    category: str = "vitals"


@dataclass
class MeasurementComponent:
    """Input payload component for a measurement (e.g. systolic value in BP)."""
    type_code: str
    value_numeric: float
    unit_code: str
    notes: str = ""


@dataclass
class HealthObservation:
    observation_id: str
    group_id: str
    patient_id: str
    observation_type_id: str
    type_code: str
    type_name: str
    value_numeric: float
    unit_code: str
    measured_at: str
    recorded_at: str
    source_type: str
    device_label: str
    entered_by_name: str
    status: str = "active"
    corrects_observation_id: Optional[str] = None
    notes: str = ""


@dataclass
class MeasurementGroup:
    group_id: str
    patient_id: str
    measured_at: str
    recorded_at: str
    entered_by_user_id: str
    entered_by_name: str
    source_type: str
    device_label: str
    context: str
    status: str
    client_request_id: str
    observations: List[HealthObservation] = field(default_factory=list)


@dataclass
class BloodPressureReading:
    """Pair view of Blood Pressure (Systolic + Diastolic) within the same group."""
    group_id: str
    patient_id: str
    measured_at: str
    recorded_at: str
    systolic: Optional[float]
    diastolic: Optional[float]
    unit_code: str
    source_type: str
    device_label: str
    entered_by_name: str
    status: str
    notes: str = ""


@dataclass
class AggregatedPoint:
    """Statistical summary point for Day/Week aggregation with sample count n."""
    period_key: str  # e.g. "2026-10-01" or "2026-W40"
    type_code: str
    type_name: str
    mean_value: float
    median_value: float
    min_value: float
    max_value: float
    unit_code: str
    sample_count_n: int
    period_start: str
    period_end: str
    raw_observation_ids: List[str] = field(default_factory=list)


@dataclass
class ClinicianReview:
    review_id: str
    clinician_id: str
    patient_id: str
    reviewed_at: str
    period_start: str
    period_end: str
    status: str = "CONFIRMED"
    notes: str = ""


@dataclass
class ClinicalMessage:
    message_id: str
    patient_id: str
    clinician_id: str
    clinician_name: str
    review_id: Optional[str]
    observation_id: Optional[str]
    body: str
    visibility: str
    created_at: str
    status: str
    is_read_by_patient: bool = False


@dataclass
class SymptomEntry:
    symptom_id: str
    patient_id: str
    reported_by_user_id: str
    reported_by_name: str
    reported_at: str
    text: str
    discomfort_level: int
    status: str = "active"


@dataclass
class PatientCardSummary:
    patient_id: str
    full_name: str
    gender: str
    timezone: str
    last_measured_at: Optional[str]
    unreviewed_records_count: int
    last_reviewed_at: Optional[str]
    review_status: str  # 'chưa có dữ liệu', 'chưa xem', 'đã xem', 'đã phản hồi'
    latest_bp: Optional[str] = None
    latest_hr: Optional[str] = None
