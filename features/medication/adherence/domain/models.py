"""
Domain Models and Enums for Medication Adherence and Monitoring Module.
Strict adherence to 3NF relational design and clinical safety requirements.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


class DoseState(str, enum.Enum):
    PENDING = "PENDING"
    REMINDING = "REMINDING"
    SNOOZED = "SNOOZED"
    CONFIRMED = "CONFIRMED"
    SKIPPED = "SKIPPED"
    OVERDUE = "OVERDUE"
    MISSED = "MISSED"
    CANCELLED = "CANCELLED"


class DoseEventType(str, enum.Enum):
    REMINDED = "reminded"
    SNOOZED = "snoozed"
    SELF_REPORTED_TAKEN = "self_reported_taken"
    CAREGIVER_REPORTED_TAKEN = "caregiver_reported_taken"
    HELP_REQUESTED = "help_requested"
    OVERDUE_UNCONFIRMED = "overdue_unconfirmed"
    RESOLVED = "resolved"


class AlertType(str, enum.Enum):
    OVERDUE = "OVERDUE"
    HELP_REQUESTED = "HELP_REQUESTED"


class AlertStatus(str, enum.Enum):
    NEW = "NEW"
    DISPATCHED = "DISPATCHED"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    RESOLVED = "RESOLVED"


class DeliveryChannel(str, enum.Enum):
    EMAIL = "EMAIL"
    SMS = "SMS"
    IN_APP = "IN_APP"


class DeliveryStatus(str, enum.Enum):
    QUEUED = "QUEUED"
    SENT = "SENT"
    DELIVERED = "DELIVERED"
    FAILED = "FAILED"
    ACKNOWLEDGED = "ACKNOWLEDGED"


class PrescriptionSource(str, enum.Enum):
    CLINICIAN_ENTERED = "clinician_entered"
    UPLOADED_DOCUMENT = "uploaded_document"
    CAREGIVER_TRANSCRIBED = "caregiver_transcribed"


@dataclass
class User:
    user_id: str
    email: str
    phone: str
    full_name: str
    is_active: bool = True
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


@dataclass
class Patient:
    patient_id: str
    full_name: str
    user_id: Optional[str] = None
    date_of_birth: Optional[str] = None
    gender: str = "Nam"
    timezone: str = "Asia/Ho_Chi_Minh"
    status: str = "ACTIVE"
    notes: str = ""


@dataclass
class Caregiver:
    caregiver_id: str
    user_id: str
    email_verified: bool = True
    phone_verified: bool = True
    user_info: Optional[User] = None


@dataclass
class Clinician:
    clinician_id: str
    user_id: str
    workplace: str = ""
    specialty: str = ""
    license_number: str = ""
    is_verified: bool = True
    user_info: Optional[User] = None


@dataclass
class PatientCaregiver:
    patient_caregiver_id: str
    patient_id: str
    caregiver_id: str
    relationship_type: str = "Người thân"
    is_primary: bool = True
    notification_priority: int = 1
    can_edit_schedule: bool = True
    can_view_health: bool = True
    consent_status: str = "GRANTED"
    valid_from: str = "2020-01-01T00:00:00+00:00"
    valid_to: str = "2099-12-31T23:59:59+00:00"
    is_active: bool = True
    caregiver_name: str = ""
    caregiver_email: str = ""
    caregiver_phone: str = ""

    def is_currently_valid(self, at_time: Optional[datetime] = None) -> bool:
        """Check if the caregiver relation is active and within valid date window."""
        if not self.is_active or self.consent_status != "GRANTED":
            return False
        now = at_time or datetime.now(timezone.utc)
        try:
            v_from = datetime.fromisoformat(self.valid_from)
            v_to = datetime.fromisoformat(self.valid_to)
            return v_from <= now <= v_to
        except Exception:
            return False


@dataclass
class Medication:
    medication_id: str
    brand_name: str
    generic_name: str = ""
    product_strength: str = ""  # e.g. "500" or "5"
    strength_unit: str = "mg"   # e.g. "mg", "ml"
    dosage_form: str = "Viên nén"  # e.g. "Viên nén", "Viên nang", "Siro"
    image_path: str = ""
    safety_notes: str = ""


@dataclass
class Prescription:
    prescription_id: str
    patient_id: str
    entered_by_user_id: str
    prescription_code: str = ""
    prescribed_date: str = field(default_factory=lambda: datetime.now(timezone.utc).date().isoformat())
    prescriber_clinician_id: Optional[str] = None
    source: PrescriptionSource = PrescriptionSource.CAREGIVER_TRANSCRIBED
    document_url: str = ""
    status: str = "ACTIVE"
    valid_from: str = field(default_factory=lambda: datetime.now(timezone.utc).date().isoformat())
    valid_to: Optional[str] = None


@dataclass
class PrescriptionItem:
    prescription_item_id: str
    prescription_id: str
    medication_id: str
    dose_amount: float = 1.0     # e.g. 1.0 (distinct from product strength!)
    dose_unit: str = "viên"       # e.g. "viên", "gói", "thìa"
    route_of_administration: str = "Đường uống"
    frequency_description: str = "Hàng ngày"
    original_instructions: str = ""
    start_date: str = field(default_factory=lambda: datetime.now(timezone.utc).date().isoformat())
    end_date: Optional[str] = None
    status: str = "ACTIVE"
    # Resolved metadata for display
    medication: Optional[Medication] = None


@dataclass
class MedicationSchedule:
    schedule_id: str
    prescription_item_id: str
    revision: int = 1
    timezone: str = "Asia/Ho_Chi_Minh"
    local_times: List[str] = field(default_factory=lambda: ["08:00"])  # HH:MM strings
    days_of_week: List[int] = field(default_factory=lambda: [0, 1, 2, 3, 4, 5, 6])  # 0=Mon..6=Sun
    reminder_delay_minutes: int = 10
    grace_period_minutes: int = 60
    caregiver_alert_enabled: bool = True
    is_active: bool = True
    effective_from: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    effective_to: Optional[str] = None


@dataclass
class DoseOccurrence:
    occurrence_id: str
    schedule_id: str
    revision: int
    scheduled_at_utc: str
    local_scheduled_time: str
    grace_deadline_utc: str
    state: DoseState = DoseState.PENDING
    medication_name_snapshot: str = ""
    dose_snapshot: str = ""
    instructions_snapshot: str = ""
    image_snapshot: str = ""
    patient_id: str = ""
    snooze_count: int = 0
    snoozed_until_utc: Optional[str] = None
    action_taken_at_utc: Optional[str] = None
    action_actor: Optional[str] = None
    action_notes: Optional[str] = None


@dataclass
class DoseEvent:
    event_id: str
    occurrence_id: str
    event_type: DoseEventType
    timestamp_utc: str
    actor_id: str = "system"
    actor_role: str = "system"
    idempotency_key: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class Alert:
    alert_id: str
    occurrence_id: str
    patient_id: str
    alert_type: AlertType
    status: AlertStatus = AlertStatus.NEW
    created_at_utc: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    resolved_at_utc: Optional[str] = None
    resolved_by: Optional[str] = None
    resolution_notes: Optional[str] = None


@dataclass
class NotificationDelivery:
    delivery_id: str
    alert_id: str
    caregiver_id: str
    channel: DeliveryChannel = DeliveryChannel.EMAIL
    recipient_snapshot: str = ""
    status: DeliveryStatus = DeliveryStatus.QUEUED
    attempt_count: int = 0
    last_attempt_at_utc: Optional[str] = None
    error_message: str = ""
    idempotency_key: str = ""
