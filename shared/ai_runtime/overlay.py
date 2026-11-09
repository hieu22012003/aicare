"""Live skeleton / hand overlay for the home app's camera view (field request 09/11).

The AI worker (8 fps) already runs MediaPipe Pose, or Holistic during a medication window. It publishes
the latest landmarks here; the camera thread draws them on the displayed frame, so no second pose model
runs on the UI side. Thread-safe, drawing never raises.
"""

from __future__ import annotations

import threading
import time
from collections import Counter, deque

POSE_EDGES = ((11, 12), (11, 13), (13, 15), (12, 14), (14, 16), (11, 23), (12, 24), (23, 24),
              (23, 25), (25, 27), (24, 26), (26, 28), (27, 31), (28, 32), (27, 29), (28, 30))
HAND_EDGES = ((0, 1), (1, 2), (2, 3), (3, 4), (0, 5), (5, 6), (6, 7), (7, 8), (5, 9), (9, 10), (10, 11),
              (11, 12), (9, 13), (13, 14), (14, 15), (15, 16), (13, 17), (0, 17), (17, 18), (18, 19), (19, 20))
THUMB_TIP, INDEX_TIP = 4, 8

POSTURE_TEXT = {"STANDING": "Đứng", "WALKING": "Đi lại", "SITTING": "Ngồi", "LYING": "Nằm", "BENDING": "Cúi người"}


def posture_guess(landmarks, w: int, h: int) -> tuple[str | None, bool]:
    """(posture key or None, legs visible) for one frame, with the same rules as the posture engine."""
    if not landmarks:
        return None, False
    try:
        from features.posture_activity.engine.classifier import classify
        from shared.vision.pose.geometry import compute_features
        from shared.vision.pose.observation import Observation

        f = compute_features(Observation(time.time(), tuple(landmarks), w, h), 0.5)
        if f is None:
            return None, False
        p = classify(f)
        return (p.value if p is not None else None), bool(f.legs_visible)
    except Exception:
        return None, False


class LiveOverlay:
    def __init__(self, max_age: float = 0.7) -> None:
        self._lock = threading.Lock()
        self.max_age = max_age
        self._ts = 0.0
        self._size = (0, 0)
        self._pose = None
        self._hands: tuple = ()
        self._mouth = None
        self._votes: deque = deque(maxlen=12)
        self._legs = deque(maxlen=12)
        self._trail: deque = deque(maxlen=40)  # (ts, x, y) of the hand bringing the pill
        self.medication = False

    # ------------------------------------------------------------ AI worker side
    def update(self, ts: float, w: int, h: int, pose=None, hands=(), mouth=None, medication: bool = False) -> None:
        key, legs = posture_guess(pose, w, h)
        with self._lock:
            self._ts, self._size, self._pose, self._mouth = ts, (w, h), pose, mouth
            self._hands = tuple(hd for hd in hands if hd)
            self.medication = medication
            self._votes.append(key)
            self._legs.append(legs)
            if medication and self._hands:
                # Follow the fingertip nearest to the mouth (or the first hand when the face is not seen).
                pts = [((hd[THUMB_TIP][0] + hd[INDEX_TIP][0]) / 2, (hd[THUMB_TIP][1] + hd[INDEX_TIP][1]) / 2)
                       for hd in self._hands]
                if mouth:
                    pts.sort(key=lambda p: (p[0] - mouth[0]) ** 2 + (p[1] - mouth[1]) ** 2)
                self._trail.append((ts, pts[0][0], pts[0][1]))
            while self._trail and ts - self._trail[0][0] > 4.0:
                self._trail.popleft()

    def posture_text(self) -> str | None:
        with self._lock:
            if self._pose is None:
                return None
            votes = [v for v in self._votes if v]
            legs = sum(self._legs) >= len(self._legs) / 2 if self._legs else False
        if votes:
            key, n = Counter(votes).most_common(1)[0]
            if n >= max(2, len(votes) // 2):
                return f"Tư thế: {POSTURE_TEXT.get(key, key)}"
        if not legs:
            guess = self._upper_body_guess()
            if guess:
                return f"Tư thế: {guess} (ước đoán – camera chưa thấy chân)"
            return "Tư thế: chưa xác định (camera chưa thấy chân)"
        return "Tư thế: đang xác định…"

    def _upper_body_guess(self) -> str | None:
        """Only head and shoulders in view (laptop webcam): a hint, never stored as posture.

        Camera at table height in front of a seated person (laptop webcam). Measured on the customer's video
        09/11: seated, the shoulders sit in the lower part of the picture (y ~0.75); standing up in front of the
        camera, the face goes to the top edge and the shoulders rise to the upper half (y ~0.3). Shoulders
        tilted a lot = leaning / bending.
        """
        with self._lock:
            pose = self._pose
        if not pose or len(pose) < 13:
            return None
        nose, ls, rs = pose[0], pose[11], pose[12]
        if min(getattr(ls, "visibility", 1), getattr(rs, "visibility", 1)) < 0.5:
            return None
        tilt = abs(ls.y - rs.y) / max(1e-3, abs(ls.x - rs.x))
        if tilt > 0.6:
            return "Nghiêng / cúi người"
        shoulders_y = (ls.y + rs.y) / 2
        if getattr(nose, "visibility", 1) < 0.5 or nose.y < 0.06 or shoulders_y < 0.5:
            return "Đứng"
        if shoulders_y > 0.58:
            return "Ngồi"
        return None

    # ------------------------------------------------------------ camera thread side
    def draw(self, frame, skeleton: bool = True, now: float | None = None):
        if frame is None:
            return frame
        now = time.time() if now is None else now
        with self._lock:
            fresh = now - self._ts <= self.max_age
            size, pose, hands, trail = self._size, self._pose, self._hands, list(self._trail)
            medication = self.medication
        if not fresh:
            return frame
        try:
            import cv2

            h, w = frame.shape[:2]
            sx, sy = w / max(1, size[0]), h / max(1, size[1])
            if skeleton and pose:
                pts = [(int(p.x * w), int(p.y * h)) if getattr(p, "visibility", 1.0) >= 0.5 else None for p in pose]
                for a, b in POSE_EDGES:
                    if a < len(pts) and b < len(pts) and pts[a] and pts[b]:
                        cv2.line(frame, pts[a], pts[b], (60, 220, 255), 3, cv2.LINE_AA)
                for i, p in enumerate(pts):
                    if p and i >= 11:
                        cv2.circle(frame, p, 5, (20, 120, 255), -1, cv2.LINE_AA)
                label = self.posture_text()
                if label:
                    from shared.camera.drawing_utils import draw_vietnamese_text

                    y0 = h - 40
                    cv2.rectangle(frame, (8, y0 - 6), (min(w - 8, 30 + 11 * len(label)), y0 + 30), (30, 30, 30), -1)
                    draw_vietnamese_text(frame, label, (16, y0), font_size=max(15, w // 50), color=(120, 230, 255))
            if medication and hands:
                for hd in hands:
                    hp = [(int(x * sx), int(y * sy)) for x, y in hd]
                    for a, b in HAND_EDGES:
                        cv2.line(frame, hp[a], hp[b], (90, 255, 120), 2, cv2.LINE_AA)
                    for i, p in enumerate(hp):
                        cv2.circle(frame, p, 6 if i in (THUMB_TIP, INDEX_TIP) else 3,
                                   (0, 200, 255) if i in (THUMB_TIP, INDEX_TIP) else (90, 255, 120), -1, cv2.LINE_AA)
            if medication and len(trail) >= 2:
                tp = [(int(x * sx), int(y * sy)) for _, x, y in trail]
                for i in range(1, len(tp)):
                    cv2.line(frame, tp[i - 1], tp[i], (0, 200, 255), 2 + i * 3 // len(tp), cv2.LINE_AA)
        except Exception:
            pass
        return frame


OVERLAY = LiveOverlay()
