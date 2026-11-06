"""Domain types for feature 4 – fall and dizziness risk."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from pydantic import BaseModel, Field, field_validator


class SafetyState(str, Enum):
    NORMAL = "NORMAL"
    SUSPECTED = "SUSPECTED"  # upright -> low with fast descent, waiting for confirmation
    LOW_UNCONFIRMED = "LOW_UNCONFIRMED"  # low outside rest zones without an observed fall
    EVENT_ACTIVE = "EVENT_ACTIVE"  # a fall event is open (checking or escalated)
    UNOBSERVED = "UNOBSERVED"


SAFETY_LABEL_VI = {
    SafetyState.NORMAL: "Bình thường",
    SafetyState.SUSPECTED: "Nghi ngã, đang xác nhận",
    SafetyState.LOW_UNCONFIRMED: "Đang ở tư thế thấp",
    SafetyState.EVENT_ACTIVE: "Đang xử lý cảnh báo ngã",
    SafetyState.UNOBSERVED: "Không quan sát được",
}


class FallKind(str, Enum):
    F1_FALL = "F1"  # observed upright -> low transition
    F2_ON_FLOOR = "F2"  # lying on the floor, fall itself not observed


class EventStatus(str, Enum):
    CHECKING = "CHECKING"
    ESCALATED = "ESCALATED"
    USER_OK = "USER_OK"
    SELF_RECOVERED = "SELF_RECOVERED"
    ACKNOWLEDGED = "ACKNOWLEDGED"


OPEN_EVENT_STATUSES = frozenset({EventStatus.CHECKING, EventStatus.ESCALATED})


class FallLabel(str, Enum):
    CONFIRMED_FALL = "confirmed_fall"
    FALSE_ALARM = "false_alarm"


class CheckinResult(str, Enum):
    OK = "ok"  # "tôi ổn" or button
    HELP = "help"  # "cứu", "đau", "ngã"
    NO_RESPONSE = "no_response"
    STOOD_UP = "stood_up"


class SignalCode(str, Enum):
    C1_SWAY = "C1"
    C2_GAIT = "C2"
    C3_HARD_SIT = "C3"
    C4_QUICK_RESIT = "C4"


@dataclass(slots=True)
class Checkin:
    ts: float
    attempt: int
    result: CheckinResult | None = None  # None = question asked, no answer yet
    transcript: str | None = None


@dataclass(slots=True)
class FallEvent:
    event_id: str
    patient_id: str
    kind: FallKind
    status: EventStatus
    detected_at: float
    suspected_at: float
    identity: str
    trial: bool
    message: str
    attempts: int = 1
    next_action_at: float | None = None
    escalated_at: float | None = None
    closed_at: float | None = None
    closed_by: str | None = None
    long_lie: bool = False
    stood_up_at: float | None = None
    caregiver_message: str | None = None
    label: FallLabel | None = None
    label_by: str | None = None
    note: str | None = None
    checkins: list[Checkin] = field(default_factory=list)
    updated_at: float = 0.0

    @property
    def is_open(self) -> bool:
        return self.status in OPEN_EVENT_STATUSES


@dataclass(frozen=True, slots=True)
class RiskSignal:
    signal_id: str
    patient_id: str
    code: SignalCode
    ts: float
    value: float
    threshold: float
    alerted: bool
    message: str


class FallConfig(BaseModel):
    """Per-patient sensitivity. Starting values; tune with labelled data before enabling alerts."""

    timezone: str = "Asia/Ho_Chi_Minh"
    # False = trial mode (rule 8): events are stored and shown, but no email/webhook is sent.
    alerts_enabled: bool = False
    checkin_wait_seconds: float = Field(30, ge=5, le=300)
    second_checkin_wait_seconds: float = Field(15, ge=5, le=300)
    transition_window_seconds: float = Field(2.0, ge=0.5, le=10)
    fall_descent_speed: float = Field(0.6, ge=0.1, le=5, description="torso lengths per second")
    min_drop: float = Field(0.5, ge=0.1, le=3, description="hip drop in torso lengths")
    low_confirm_seconds: float = Field(2.0, ge=0.5, le=30)
    floor_seconds: float = Field(20, ge=3, le=600)
    floor_seconds_after_night_exit: float = Field(10, ge=3, le=600)
    night_exit_sensitive_minutes: float = Field(30, ge=0, le=240)
    long_lie_seconds: float = Field(60, ge=10, le=1800)
    recovery_seconds: float = Field(10, ge=2, le=120)
    dizziness_enabled: bool = True
    sway_threshold: float = Field(0.15, ge=0.02, le=2)
    sway_window_seconds: float = Field(3, ge=1, le=30)
    gait_slow_ratio: float = Field(0.6, ge=0.1, le=1)
    gait_irregular_cv: float = Field(0.35, ge=0.05, le=2)
    gait_min_bouts_for_baseline: int = Field(5, ge=1, le=100)
    hard_sit_speed: float = Field(1.2, ge=0.2, le=10)
    quick_resit_seconds: float = Field(5, ge=1, le=60)
    quick_resit_count: int = Field(2, ge=2, le=10)
    quick_resit_window_minutes: float = Field(10, ge=1, le=120)
    max_signal_alerts_per_day: int = Field(3, ge=0, le=50)
    signal_cooldown_minutes: float = Field(30, ge=0, le=720)

    @field_validator("timezone")
    @classmethod
    def _check_tz(cls, value: str) -> str:
        from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError(f"unknown timezone: {value}") from exc
        return value
