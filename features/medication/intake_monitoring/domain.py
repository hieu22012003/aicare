"""Types for camera-observed medication intake (pure, no I/O)."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from pydantic import BaseModel, Field

Point = tuple[float, float]  # pixels


class Hand(str, Enum):
    ANY = "any"
    RIGHT = "right"
    LEFT = "left"


class IntakeVerdict(str, Enum):
    VERIFIED = "VERIFIED"  # gesture + drinking or pill-handling context: dose counted as taken
    LIKELY = "LIKELY"  # gesture only: the elder is asked to confirm, nothing is recorded as taken


class IntakeConfig(BaseModel):
    """Starting thresholds. Tune with labelled sessions before turning trial mode off."""

    hand: Hand = Hand.ANY
    pinch_ratio: float = Field(0.25, gt=0, le=1, description="thumb-index tip gap / hand width")
    pinch_recent_seconds: float = Field(3.0, gt=0, le=30)
    mouth_distance_ratio: float = Field(0.5, gt=0, le=2, description="fingertip-mouth gap / face width")
    hold_seconds: float = Field(0.3, gt=0, le=5)
    long_hold_reject_seconds: float = Field(5.0, gt=0, le=60, description="phone call, chin rest")
    mouth_open_ratio: float = Field(0.35, gt=0, le=2, description="inner lip gap / mouth width")
    open_slack_seconds: float = Field(1.0, ge=0, le=5)
    drink_window_seconds: float = Field(60.0, gt=0, le=600)
    head_tilt_drop: float = Field(0.06, gt=0, le=0.5, description="drop of the nose position ratio when tilting back")
    context_window_seconds: float = Field(120.0, gt=0, le=1800)


@dataclass(frozen=True, slots=True)
class HolisticFrame:
    """What the intake detector needs from one camera frame (pixel coordinates).

    face: the 468 MediaPipe face-mesh points or None; hands: 21 points each or None.
    """

    ts: float
    face: tuple[Point, ...] | None
    left_hand: tuple[Point, ...] | None
    right_hand: tuple[Point, ...] | None
    identity_ok: bool = True  # the recognised person is the patient
    phone_in_hand: bool = False  # from the object detector
    cup_near_mouth: bool = False  # from the object detector
    near_pillbox: bool = False  # a hand is in the "where the pills are kept" region


@dataclass(slots=True)
class IntakeObservation:
    ts: float
    verdict: IntakeVerdict
    hand: str
    contact_at: float
    mouth_open_at: float
    pinch_at: float | None = None
    drink_at: float | None = None
    context_at: float | None = None
    score: float = 0.0
    steps: dict = field(default_factory=dict)
