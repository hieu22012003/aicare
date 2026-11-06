"""MediaPipe Holistic for the medication window: pose (33) + face mesh (468) + both hands (21).

Outside medication windows the cheaper Pose model (ai_core.shared.pose.estimator) is used.
Blocking calls: run them on the AI worker thread, never on the Qt UI thread.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass

import numpy as np

from ai_core.shared.pose.landmarks import NUM_LANDMARKS, Landmark


@dataclass(frozen=True, slots=True)
class HolisticResult:
    pose: tuple[Landmark, ...] | None
    face: tuple[tuple[float, float], ...] | None  # pixels
    left_hand: tuple[tuple[float, float], ...] | None
    right_hand: tuple[tuple[float, float], ...] | None


def _pixels(landmarks, w: int, h: int):
    if landmarks is None:
        return None
    return tuple((float(p.x) * w, float(p.y) * h) for p in landmarks.landmark)


class HolisticEstimator:
    def __init__(self, min_detection_confidence: float = 0.5, min_tracking_confidence: float = 0.5) -> None:
        import mediapipe as mp

        self._lock = threading.Lock()
        self._model = mp.solutions.holistic.Holistic(
            static_image_mode=False,
            model_complexity=1,
            refine_face_landmarks=False,
            min_detection_confidence=min_detection_confidence,
            min_tracking_confidence=min_tracking_confidence,
        )

    def process(self, bgr: np.ndarray) -> HolisticResult:
        import cv2

        h, w = bgr.shape[:2]
        rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
        with self._lock:
            r = self._model.process(rgb)
        pose = None
        if r.pose_landmarks is not None and len(r.pose_landmarks.landmark) >= NUM_LANDMARKS:
            pose = tuple(Landmark(float(p.x), float(p.y), float(p.z), float(p.visibility or 0.0))
                         for p in r.pose_landmarks.landmark[:NUM_LANDMARKS])
        face = _pixels(r.face_landmarks, w, h)
        if face is not None and len(face) < 468:
            face = None
        return HolisticResult(pose, face, _pixels(r.left_hand_landmarks, w, h), _pixels(r.right_hand_landmarks, w, h))

    def close(self) -> None:
        with self._lock:
            self._model.close()
