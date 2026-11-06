"""One analysed camera frame, shared by every pose-based feature (3, 4, 7)."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from shared.vision.pose.landmarks import Landmark

# (x, y, w, h, score) normalized to the image, from an optional person detector.
PersonBox = tuple[float, float, float, float, float]


class Identity(str, Enum):
    PATIENT = "patient"  # the monitored person (face recognition or single-occupant setup)
    UNKNOWN = "unknown"  # someone is there but identity is not confirmed
    OTHER = "other"  # a different, known person


@dataclass(frozen=True, slots=True)
class Observation:
    ts: float  # epoch seconds (UTC)
    landmarks: tuple[Landmark, ...] | None  # 33 MediaPipe-layout landmarks, None when no pose
    image_width: int = 640
    image_height: int = 480
    identity: Identity = Identity.PATIENT
    camera_index: int = 0
    # Person boxes from a detector. Lets fall detection see a person lying down even when
    # the pose model finds no skeleton (MediaPipe often misses people on the floor).
    person_boxes: tuple[PersonBox, ...] = ()
