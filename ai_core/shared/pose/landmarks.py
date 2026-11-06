"""MediaPipe Pose landmark layout (33 points, coordinates normalized to the image)."""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum

NUM_LANDMARKS = 33


class PoseLandmark(IntEnum):
    NOSE = 0
    LEFT_SHOULDER = 11
    RIGHT_SHOULDER = 12
    LEFT_WRIST = 15
    RIGHT_WRIST = 16
    LEFT_HIP = 23
    RIGHT_HIP = 24
    LEFT_KNEE = 25
    RIGHT_KNEE = 26
    LEFT_ANKLE = 27
    RIGHT_ANKLE = 28


@dataclass(frozen=True, slots=True)
class Landmark:
    """One pose landmark. x, y in [0, 1] relative to image width/height."""

    x: float
    y: float
    z: float = 0.0
    visibility: float = 1.0
