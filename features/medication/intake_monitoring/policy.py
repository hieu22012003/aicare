"""When to watch, speak and alert for one scheduled dose (pure).

Minutes are counted from the scheduled time:
    -5  OPEN            camera intake monitoring starts
     0  SPEAK_DUE       "đến giờ uống thuốc" + where the pill box is
   +10  OVERDUE         "bác chưa uống" + alert on the caregiver web portal
   +30  ESCALATE        urgent alert through the existing OVERDUE alert + email path
   +60  CLOSE           monitoring stops (the existing scheduler owns MISSED)
A taken dose only needs CLOSE. After a restart, missed speech milestones are not replayed: only
the latest one is spoken, so the elder never hears three reminders in a row.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field, model_validator


class DoseAction(str, Enum):
    OPEN = "open"
    SPEAK_DUE = "speak_due"
    OVERDUE = "overdue"
    ESCALATE = "escalate"
    CLOSE = "close"


SPEECH = (DoseAction.SPEAK_DUE, DoseAction.OVERDUE)


class MonitorPolicy(BaseModel):
    open_before_minutes: float = Field(5, ge=0, le=60)
    # Field 09/11: the family expects the reminder + web warning ~5 minutes after the dose time, not 10.
    overdue_minutes: float = Field(5, ge=1, le=240)
    escalate_minutes: float = Field(20, ge=1, le=600)
    close_minutes: float = Field(60, ge=1, le=1440)

    @model_validator(mode="after")
    def _order(self) -> "MonitorPolicy":
        if not (self.overdue_minutes < self.escalate_minutes < self.close_minutes):
            raise ValueError("overdue < escalate < close is required")
        return self

    def milestones(self, scheduled_at: float) -> list[tuple[float, DoseAction]]:
        m = 60.0
        return [
            (scheduled_at - self.open_before_minutes * m, DoseAction.OPEN),
            (scheduled_at, DoseAction.SPEAK_DUE),
            (scheduled_at + self.overdue_minutes * m, DoseAction.OVERDUE),
            (scheduled_at + self.escalate_minutes * m, DoseAction.ESCALATE),
            (scheduled_at + self.close_minutes * m, DoseAction.CLOSE),
        ]


def due_actions(policy: MonitorPolicy, scheduled_at: float, now: float, done: set, taken: bool) -> list[DoseAction]:
    if taken:
        return [] if DoseAction.CLOSE in done else [DoseAction.CLOSE]
    passed = [a for t, a in policy.milestones(scheduled_at) if t <= now and a not in done]
    if DoseAction.CLOSE in passed:
        return [DoseAction.CLOSE]
    speech = [a for a in passed if a in SPEECH]
    keep = speech[-1:]  # only the latest reminder is spoken
    skipped = [a for a in speech if a not in keep]
    return [a for a in passed if a not in skipped]


def skipped_speech(policy: MonitorPolicy, scheduled_at: float, now: float, done: set) -> list[DoseAction]:
    """Speech milestones that passed but are superseded (mark them done without speaking)."""
    passed = [a for t, a in policy.milestones(scheduled_at) if t <= now and a not in done and a in SPEECH]
    return passed[:-1]
