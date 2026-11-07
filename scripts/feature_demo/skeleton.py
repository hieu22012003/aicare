"""Synthetic 33-point MediaPipe-style skeletons for exercise / fall demos, and a stick-figure renderer.

Coordinates are normalized (x right, y down) like MediaPipe Pose. ``body(**joints)`` builds a frontal
skeleton from a few joint angles so exercise trackers can be driven frame by frame.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np


@dataclass
class P:
    x: float
    y: float
    z: float = 0.0
    visibility: float = 0.99


def _pt(x, y, vis=0.99):
    return P(float(x), float(y), 0.0, vis)


def body(hip=(0.5, 0.62), tilt=0.0, l_arm=10.0, r_arm=10.0, l_elbow=0.0, r_elbow=0.0, l_arm_rot=None, r_arm_rot=None,
         knee_bend=0.0, hip_drop=0.0, l_knee_lift=0.0, r_knee_lift=0.0, side_view=False):
    """Angles in degrees.

    l_arm / r_arm: arm elevation from hanging down (0) through horizontal (90) to overhead (180).
    l_arm_rot / r_arm_rot: absolute arm direction (deg, 0 = pointing right, 90 = down) for circles.
    knee_bend: 0 standing straight .. 90 sitting (knees go forward in x when side_view).
    l_knee_lift / r_knee_lift: thigh raised forward (shown as knee moving up).
    """
    hx, hy = hip[0], hip[1] + hip_drop
    t = math.radians(tilt)
    torso = 0.25
    sx, sy = hx + torso * math.sin(t), hy - torso * math.cos(t)
    pts = [_pt(sx, sy - 0.1)] * 33
    pts = list(pts)
    # head / face points (0..10) around the nose
    nose = (sx + 0.06 * math.sin(t), sy - 0.1 * math.cos(t))
    for i in range(11):
        pts[i] = _pt(nose[0] + (i % 3 - 1) * 0.01, nose[1] + (i // 4) * 0.005)
    # shoulders / hips perpendicular to the torso
    px, py = math.cos(t), math.sin(t)
    ls = (sx + 0.08 * px, sy + 0.08 * py)  # person's left = image right
    rs = (sx - 0.08 * px, sy - 0.08 * py)
    lh = (hx + 0.055 * px, hy + 0.055 * py)
    rh = (hx - 0.055 * px, hy - 0.055 * py)
    pts[11], pts[12], pts[23], pts[24] = _pt(*ls), _pt(*rs), _pt(*lh), _pt(*rh)

    def arm(sh, elev, elbow, side, rot):
        # direction angle in image coords (deg): down = 90; raise outward to the person's side
        if rot is None:
            d = 90 - side * elev  # left (side=+1) rotates towards image right/up
        else:
            d = rot
        d += math.degrees(t)
        a = math.radians(d)
        el = (sh[0] + 0.13 * math.cos(a), sh[1] + 0.13 * math.sin(a))
        a2 = math.radians(d - side * elbow)
        wr = (el[0] + 0.12 * math.cos(a2), el[1] + 0.12 * math.sin(a2))
        return el, wr

    le, lw = arm(ls, l_arm, l_elbow, +1, l_arm_rot)
    re, rw = arm(rs, r_arm, r_elbow, -1, r_arm_rot)
    pts[13], pts[15], pts[14], pts[16] = _pt(*le), _pt(*lw), _pt(*re), _pt(*rw)
    for i, w in ((17, lw), (19, lw), (21, lw), (18, rw), (20, rw), (22, rw)):
        pts[i] = _pt(*w)

    def leg(hp, bend, lift):
        if side_view:
            k = math.radians(bend)
            knee = (hp[0] + 0.2 * math.sin(k), hp[1] + 0.2 * math.cos(k))
            ankle = (knee[0], knee[1] + 0.2)
        else:
            knee = (hp[0], hp[1] + 0.2 - 0.14 * lift)
            ankle = (hp[0], hp[1] + 0.4 - 0.1 * lift)
        return knee, ankle

    lk, la = leg(lh, knee_bend, l_knee_lift)
    rk, ra = leg(rh, knee_bend, r_knee_lift)
    pts[25], pts[27], pts[26], pts[28] = _pt(*lk), _pt(*la), _pt(*rk), _pt(*ra)
    for i, a in ((29, la), (31, la), (30, ra), (32, ra)):
        pts[i] = _pt(a[0] + (0.02 if i > 30 else 0), a[1] + 0.01)
    return tuple(pts)


EDGES = [(11, 12), (11, 13), (13, 15), (12, 14), (14, 16), (11, 23), (12, 24), (23, 24), (23, 25), (25, 27), (24, 26),
         (26, 28), (27, 31), (28, 32)]


def draw(lms, w=640, h=480, img=None, color=(80, 200, 120), thickness=6):
    import cv2

    if img is None:
        img = np.full((h, w, 3), 245, np.uint8)
    p = [(int(q.x * w), int(q.y * h)) for q in lms]
    for a, b in EDGES:
        cv2.line(img, p[a], p[b], color, thickness, cv2.LINE_AA)
    head = p[0]
    cv2.circle(img, head, int(0.045 * h), color, thickness, cv2.LINE_AA)
    for i in (11, 12, 13, 14, 15, 16, 23, 24, 25, 26, 27, 28):
        cv2.circle(img, p[i], thickness, (40, 90, 60), -1, cv2.LINE_AA)
    return img
