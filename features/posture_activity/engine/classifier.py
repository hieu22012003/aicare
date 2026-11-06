"""Rule-based per-frame posture classifier (phase 1).

Returns ``None`` when the frame is ambiguous; the temporal smoother then keeps the
previous posture instead of flipping. WALKING is decided later from motion over time.
"""

from __future__ import annotations

from dataclasses import dataclass

from features.posture_activity.domain import Posture
from features.posture_activity.engine.geometry import BodyFeatures


@dataclass(frozen=True, slots=True)
class ClassifierThresholds:
    lying_torso_deg: float = 60.0  # torso this far from vertical = lying
    lying_torso_deg_wide: float = 45.0  # ...or this far when the body box is wide
    lying_aspect: float = 1.3
    # Body box at least this wide relative to its height = body is horizontal even if the
    # torso angle is underestimated (seen on real fall footage: aspect 2.1, torso 41°).
    lying_very_wide_aspect: float = 1.8
    lying_very_wide_torso_deg: float = 30.0
    upright_torso_deg: float = 35.0
    bend_knee_deg: float = 140.0
    bend_thigh_deg: float = 60.0
    sit_thigh_deg: float = 45.0  # thigh close to horizontal (side view)
    sit_knee_deg: float = 125.0  # knee clearly bent
    sit_thigh_ratio: float = 0.45  # thigh foreshortened (facing the camera)
    stand_knee_deg: float = 150.0
    stand_thigh_deg: float = 60.0
    stand_thigh_ratio: float = 0.45


DEFAULT_THRESHOLDS = ClassifierThresholds()


def _sitting_cues(f: BodyFeatures, t: ClassifierThresholds) -> bool:
    return (
        (f.thigh_angle is not None and f.thigh_angle < t.sit_thigh_deg)
        or (f.knee_angle is not None and f.knee_angle < t.sit_knee_deg)
        or (f.thigh_ratio is not None and f.thigh_ratio < t.sit_thigh_ratio)
    )


def classify(f: BodyFeatures, t: ClassifierThresholds = DEFAULT_THRESHOLDS) -> Posture | None:
    if (
        f.torso_angle >= t.lying_torso_deg
        or (f.aspect_ratio >= t.lying_aspect and f.torso_angle >= t.lying_torso_deg_wide)
        or (f.aspect_ratio >= t.lying_very_wide_aspect and f.torso_angle >= t.lying_very_wide_torso_deg)
    ):
        return Posture.LYING

    if not f.legs_visible:
        # Upright torso with hidden legs (behind a table, cropped frame): could be sitting or standing.
        return None

    if f.torso_angle >= t.upright_torso_deg:
        # Torso leaning forward: bending with straight legs, or sitting while leaning.
        straight_legs = (f.knee_angle or 0.0) >= t.bend_knee_deg and (f.thigh_angle or 0.0) >= t.bend_thigh_deg
        if straight_legs and not _sitting_cues(f, t):
            return Posture.BENDING
        if _sitting_cues(f, t):
            return Posture.SITTING
        return None

    if _sitting_cues(f, t):
        return Posture.SITTING
    if (
        (f.knee_angle or 0.0) >= t.stand_knee_deg
        and (f.thigh_angle or 0.0) >= t.stand_thigh_deg
        and (f.thigh_ratio or 0.0) >= t.stand_thigh_ratio
    ):
        return Posture.STANDING
    return None
