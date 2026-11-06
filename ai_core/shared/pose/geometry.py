"""Body geometry features computed from 33 pose landmarks (shared by features 3, 4, 7).

All angles are computed in pixel space (normalized coordinates scaled by image size),
so a non-square image does not distort them.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from ai_core.shared.pose.landmarks import Landmark, PoseLandmark as P
from ai_core.shared.pose.observation import Observation

Point = tuple[float, float]


@dataclass(frozen=True, slots=True)
class BodyFeatures:
    torso_angle: float  # degrees from vertical: 0 = upright, 90 = horizontal
    torso_px: float  # shoulder-mid to hip-mid length in pixels (body scale)
    torso_orientation: float  # signed angle of the shoulder->hip vector, for repositioning
    aspect_ratio: float  # width / height of the visible-landmark bounding box
    hip_norm: Point  # hip midpoint, normalized [0, 1] (used for zones)
    hip_px: Point
    legs_visible: bool
    thigh_angle: float | None = None  # degrees from horizontal: 0 = flat (sitting side view), 90 = vertical
    knee_angle: float | None = None  # hip-knee-ankle angle: 180 = straight leg
    thigh_ratio: float | None = None  # vertical thigh extent / torso length (small when sitting facing camera)
    shoulder_px: Point | None = None
    ankle_left_px: Point | None = None
    ankle_right_px: Point | None = None
    wrists_norm: tuple[Point, ...] = ()  # visible wrists, normalized [0, 1]


def _visible_mid(lms: tuple[Landmark, ...], a: int, b: int, min_vis: float, w: int, h: int) -> Point | None:
    points = [(lms[i].x * w, lms[i].y * h) for i in (a, b) if lms[i].visibility >= min_vis]
    if not points:
        return None
    return (sum(p[0] for p in points) / len(points), sum(p[1] for p in points) / len(points))


def _angle_at(a: Point, b: Point, c: Point) -> float:
    """Angle ABC in degrees."""
    v1 = (a[0] - b[0], a[1] - b[1])
    v2 = (c[0] - b[0], c[1] - b[1])
    n1 = math.hypot(*v1)
    n2 = math.hypot(*v2)
    if n1 < 1e-6 or n2 < 1e-6:
        return 180.0
    cos = (v1[0] * v2[0] + v1[1] * v2[1]) / (n1 * n2)
    return math.degrees(math.acos(max(-1.0, min(1.0, cos))))


def compute_features(obs: Observation, min_visibility: float = 0.5) -> BodyFeatures | None:
    lms = obs.landmarks
    if not lms or len(lms) < 33:
        return None
    w, h = max(1, obs.image_width), max(1, obs.image_height)

    shoulder = _visible_mid(lms, P.LEFT_SHOULDER, P.RIGHT_SHOULDER, min_visibility, w, h)
    hip = _visible_mid(lms, P.LEFT_HIP, P.RIGHT_HIP, min_visibility, w, h)
    if shoulder is None or hip is None:
        return None

    dx, dy = hip[0] - shoulder[0], hip[1] - shoulder[1]
    torso_px = math.hypot(dx, dy)
    if torso_px < 8.0:  # person too small / degenerate detection
        return None
    torso_angle = math.degrees(math.atan2(abs(dx), abs(dy)))
    torso_orientation = math.degrees(math.atan2(dy, dx))

    visible = [(lm.x * w, lm.y * h) for lm in lms if lm.visibility >= min_visibility]
    xs = [p[0] for p in visible]
    ys = [p[1] for p in visible]
    bw = max(1.0, max(xs) - min(xs))
    bh = max(1.0, max(ys) - min(ys))

    knee = _visible_mid(lms, P.LEFT_KNEE, P.RIGHT_KNEE, min_visibility, w, h)
    ankle = _visible_mid(lms, P.LEFT_ANKLE, P.RIGHT_ANKLE, min_visibility, w, h)
    legs_visible = knee is not None and ankle is not None

    thigh_angle = knee_angle = thigh_ratio = None
    if knee is not None:
        tdx, tdy = knee[0] - hip[0], knee[1] - hip[1]
        thigh_angle = math.degrees(math.atan2(abs(tdy), abs(tdx))) if math.hypot(tdx, tdy) > 1e-6 else 90.0
        thigh_ratio = abs(tdy) / torso_px
        if ankle is not None:
            knee_angle = _angle_at(hip, knee, ankle)

    def visible_px(i: int) -> Point | None:
        lm = lms[i]
        return (lm.x * w, lm.y * h) if lm.visibility >= min_visibility else None

    wrists = tuple((lms[i].x, lms[i].y) for i in (P.LEFT_WRIST, P.RIGHT_WRIST) if lms[i].visibility >= min_visibility)

    return BodyFeatures(
        torso_angle=torso_angle,
        torso_px=torso_px,
        torso_orientation=torso_orientation,
        aspect_ratio=bw / bh,
        hip_norm=(hip[0] / w, hip[1] / h),
        hip_px=hip,
        legs_visible=legs_visible,
        thigh_angle=thigh_angle,
        knee_angle=knee_angle,
        thigh_ratio=thigh_ratio,
        shoulder_px=shoulder,
        ankle_left_px=visible_px(P.LEFT_ANKLE),
        ankle_right_px=visible_px(P.RIGHT_ANKLE),
        wrists_norm=wrists,
    )
