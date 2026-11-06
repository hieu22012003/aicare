"""Domain types for F3 – posture & activity monitoring."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import time
from enum import Enum

from pydantic import BaseModel, Field, field_validator

from ai_core.shared.pose.landmarks import Landmark  # noqa: F401  (re-exported)
from ai_core.shared.pose.observation import Identity, Observation  # noqa: F401  (re-exported)
from ai_core.shared.zones import Zone, ZoneType  # noqa: F401  (re-exported)


class Posture(str, Enum):
    STANDING = "STANDING"
    WALKING = "WALKING"
    SITTING = "SITTING"
    LYING = "LYING"
    BENDING = "BENDING"
    UNOBSERVED = "UNOBSERVED"


POSTURE_LABEL_VI: dict[Posture, str] = {
    Posture.STANDING: "Đứng",
    Posture.WALKING: "Đi lại",
    Posture.SITTING: "Ngồi",
    Posture.LYING: "Nằm",
    Posture.BENDING: "Cúi người",
    Posture.UNOBSERVED: "Không quan sát được",
}


class Location(str, Enum):
    BED = "BED"
    CHAIR = "CHAIR"
    SOFA = "SOFA"
    FLOOR = "FLOOR"
    OTHER = "OTHER"
    UNKNOWN = "UNKNOWN"


class RuleCode(str, Enum):
    R1_SITTING = "R1"
    R2_LYING_DAY = "R2"
    R3_REPOSITION = "R3"
    R4_LOW_ACTIVITY = "R4"
    R5_NIGHT_BED_EXIT = "R5"
    R6_ABSENCE = "R6"


class Audience(str, Enum):
    ELDER = "elder"
    CAREGIVER = "caregiver"


class ReminderStatus(str, Enum):
    DELIVERED = "DELIVERED"
    SNOOZED = "SNOOZED"
    ESCALATED = "ESCALATED"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    AUTO_RESOLVED = "AUTO_RESOLVED"
    EXPIRED = "EXPIRED"


OPEN_STATUSES = frozenset({ReminderStatus.DELIVERED, ReminderStatus.SNOOZED, ReminderStatus.ESCALATED})

# Day-summary buckets. Lying is split into rest (bed/sofa/unknown) and floor (outside rest zones).
SUMMARY_KEYS = ("standing", "walking", "sitting", "lying_rest", "lying_floor", "bending", "unobserved")


def summary_key(posture: Posture, location: Location) -> str:
    if posture == Posture.LYING:
        return "lying_floor" if location == Location.FLOOR else "lying_rest"
    return posture.value.lower()


@dataclass(frozen=True, slots=True)
class Segment:
    segment_id: str
    patient_id: str
    posture: Posture
    location: Location
    started_at: float
    ended_at: float
    confidence: float

    @property
    def duration(self) -> float:
        return max(0.0, self.ended_at - self.started_at)


@dataclass(slots=True)
class DaySummary:
    local_date: str
    seconds: dict[str, float] = field(default_factory=lambda: {k: 0.0 for k in SUMMARY_KEYS})
    exercise_seconds: float = 0.0
    sit_to_stand_count: int = 0
    longest_sitting_sec: float = 0.0
    night_bed_exits: int = 0

    def add(self, key: str, value: float) -> None:
        self.seconds[key] = self.seconds.get(key, 0.0) + max(0.0, value)

    def copy(self) -> "DaySummary":
        return replace(self, seconds=dict(self.seconds))

    @property
    def active_seconds(self) -> float:
        """Walking + structured exercise: the 'Vận động' bucket used by R4."""
        return self.seconds.get("walking", 0.0) + self.exercise_seconds

    def grouped(self) -> dict[str, float]:
        """Buckets shown in the daily donut chart."""
        s = self.seconds
        return {
            "lying": s.get("lying_rest", 0.0) + s.get("lying_floor", 0.0),
            "sitting": s.get("sitting", 0.0),
            "standing_moving": s.get("standing", 0.0) + s.get("walking", 0.0) + s.get("bending", 0.0),
            "exercise": self.exercise_seconds,
        }


@dataclass(slots=True)
class Reminder:
    reminder_id: str
    patient_id: str
    rule: RuleCode
    audience: Audience
    status: ReminderStatus
    triggered_at: float
    message: str
    attempt: int = 1
    snoozes: int = 0
    next_action_at: float | None = None
    snoozed_until: float | None = None
    escalated_at: float | None = None
    resolved_at: float | None = None
    resolved_by: str | None = None
    caregiver_message: str | None = None
    updated_at: float = 0.0

    @property
    def is_open(self) -> bool:
        return self.status in OPEN_STATUSES


@dataclass(frozen=True, slots=True)
class CurrentState:
    patient_id: str
    posture: Posture
    location: Location
    since: float | None
    observed: bool
    confidence: float
    last_seen: float | None
    sitting_streak_seconds: float
    updated_at: float


def _parse_hhmm(value: str | time) -> time:
    if isinstance(value, time):
        return value
    return time.fromisoformat(value)  # "HH:MM" or "HH:MM:SS"


class RulesConfig(BaseModel):
    """Per-patient thresholds. Defaults are starting points that a clinician should review."""

    timezone: str = "Asia/Ho_Chi_Minh"
    sleep_start: time = time(21, 30)
    sleep_end: time = time(5, 30)
    enabled_rules: list[RuleCode] = Field(
        default_factory=lambda: [
            RuleCode.R1_SITTING,
            RuleCode.R2_LYING_DAY,
            RuleCode.R4_LOW_ACTIVITY,
            RuleCode.R5_NIGHT_BED_EXIT,
            RuleCode.R6_ABSENCE,
        ]
    )
    sitting_minutes: float = Field(45, ge=5, le=480)
    lying_day_minutes: float = Field(90, ge=10, le=720)
    reposition_minutes: float = Field(120, ge=15, le=480)
    activity_goal_minutes: float = Field(30, ge=0, le=600)
    activity_check_time: time = time(16, 0)
    night_return_minutes: float = Field(20, ge=1, le=240)
    absence_hours: float = Field(3, ge=0.5, le=24)
    break_seconds: float = Field(60, ge=10, le=900)
    response_window_minutes: float = Field(10, ge=1, le=120)
    snooze_minutes: float = Field(15, ge=1, le=120)
    max_snoozes: int = Field(2, ge=0, le=10)
    cooldown_minutes: float = Field(30, ge=0, le=720)
    max_reminders_per_day: int = Field(6, ge=0, le=50)

    @field_validator("sleep_start", "sleep_end", "activity_check_time", mode="before")
    @classmethod
    def _parse_time(cls, value: str | time) -> time:
        return _parse_hhmm(value)

    @field_validator("timezone")
    @classmethod
    def _check_tz(cls, value: str) -> str:
        from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError(f"unknown timezone: {value}") from exc
        return value

    def enabled(self, rule: RuleCode) -> bool:
        return rule in self.enabled_rules
