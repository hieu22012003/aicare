"""
Fall Detection & Posture Kinematics Module.
Analyzes MediaPipe Pose landmarks in real time to calculate torso angle,
bounding box aspect ratio, vertical center of gravity, and posture states.
Distinguishes normal standing, sitting, walking, bending, and fallen positions.
"""

from __future__ import annotations

import logging
import math
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

import cv2
import mediapipe as mp
import numpy as np

from shared.camera.drawing_utils import draw_vietnamese_text

logger = logging.getLogger("FallDetector")


@dataclass
class PostureMetrics:
    posture: str  # "STANDING", "SITTING", "BENDING", "LYING", "UNKNOWN"
    posture_vi: str  # "Đứng", "Ngồi", "Cúi người", "Nằm / Ngã", "Không rõ"
    torso_angle: float  # Angle relative to vertical (degrees)
    aspect_ratio: float  # Width / Height of body bounding box
    hip_level: float  # Normalized Y level of hips (0.0=top, 1.0=bottom/floor)
    is_fall_suspect: bool  # True if horizontal & low to ground
    fall_confidence: float  # [0.0 - 1.0]
    bbox: Optional[Tuple[int, int, int, int]]  # (x, y, w, h)
    landmarks: Optional[Any] = None
    is_observable: bool = True
    unobservable_reason: str = ""

    def __post_init__(self):
        if self.posture == 'UNKNOWN':
            self.is_observable = False
            self.unobservable_reason = self.unobservable_reason or self.posture_vi


class FallDetector:
    """
    Real-time AI Pose Kinematics Fall Detector.
    Evaluates body orientation without camera conflict or heavy deep learning models.
    """

    def __init__(self, min_detection_confidence: float = 0.50, min_tracking_confidence: float = 0.50) -> None:
        self.mp_pose = mp.solutions.pose
        self.pose = self.mp_pose.Pose(
            static_image_mode=False,
            model_complexity=1,
            smooth_landmarks=True,
            min_detection_confidence=min_detection_confidence,
            min_tracking_confidence=min_tracking_confidence,
        )
        self._last_hip_y: Optional[float] = None
        self._last_time: float = time.time()
        self._velocity: float = 0.0

    def analyze_frame(self, frame: np.ndarray) -> PostureMetrics:
        """Analyze person pose in frame and calculate kinematics metrics."""
        if frame is None or frame.size == 0:
            return PostureMetrics(
                posture="UNKNOWN",
                posture_vi="Không rõ",
                torso_angle=0.0,
                aspect_ratio=0.0,
                hip_level=0.0,
                is_fall_suspect=False,
                fall_confidence=0.0,
                bbox=None,
            )

        h, w = frame.shape[:2]
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        results = self.pose.process(rgb)

        if not results.pose_landmarks:
            return PostureMetrics(
                posture="UNKNOWN",
                posture_vi="Không phát hiện người",
                torso_angle=0.0,
                aspect_ratio=0.0,
                hip_level=0.0,
                is_fall_suspect=False,
                fall_confidence=0.0,
                bbox=None,
            )

        lms = results.pose_landmarks.landmark

        # Key landmark points
        # 11: left shoulder, 12: right shoulder
        # 23: left hip, 24: right hip
        # 25: left knee, 26: right knee
        # 27: left ankle, 28: right ankle
        sh_l, sh_r = lms[11], lms[12]
        hip_l, hip_r = lms[23], lms[24]
        knee_l, knee_r = lms[25], lms[26]
        ank_l, ank_r = lms[27], lms[28]

        # Calculate bounding box of visible body
        xs = [lm.x * w for lm in lms if lm.visibility > 0.4]
        ys = [lm.y * h for lm in lms if lm.visibility > 0.4]

        if not xs or not ys:
            return PostureMetrics(
                posture="UNKNOWN",
                posture_vi="Người bị khuất",
                torso_angle=0.0,
                aspect_ratio=0.0,
                hip_level=0.0,
                is_fall_suspect=False,
                fall_confidence=0.0,
                bbox=None,
                landmarks=results.pose_landmarks,
            )

        bx1, by1 = max(0, int(min(xs))), max(0, int(min(ys)))
        bx2, by2 = min(w, int(max(xs))), min(h, int(max(ys)))
        bw = max(10, bx2 - bx1)
        bh = max(10, by2 - by1)
        aspect_ratio = float(bw) / float(bh)

        # Mid-shoulder and Mid-hip points in normalized coordinates
        sh_mid_x = (sh_l.x + sh_r.x) / 2.0
        sh_mid_y = (sh_l.y + sh_r.y) / 2.0
        hip_mid_x = (hip_l.x + hip_r.x) / 2.0
        hip_mid_y = (hip_l.y + hip_r.y) / 2.0

        # Torso inclination angle relative to vertical axis (degrees)
        dx = hip_mid_x - sh_mid_x
        dy = hip_mid_y - sh_mid_y
        torso_angle = math.degrees(math.atan2(abs(dx), max(1e-4, abs(dy))))

        # Vertical velocity tracking
        now = time.time()
        dt = max(1e-3, now - self._last_time)
        if self._last_hip_y is not None:
            self._velocity = (hip_mid_y - self._last_hip_y) / dt
        self._last_hip_y = hip_mid_y
        self._last_time = now

        # Leg height (vertical distance from hip to ankle)
        ank_mid_y = (ank_l.y + ank_r.y) / 2.0
        knee_mid_y = (knee_l.y + knee_r.y) / 2.0
        leg_height = ank_mid_y - hip_mid_y

        # Multi-factor fall confidence score calculation
        # Factor 1: Aspect Ratio score (normal vertical < 0.8 -> 0; horizontal > 1.25 -> 1.0)
        s_ar = min(1.0, max(0.0, (aspect_ratio - 0.80) / 0.50))
        # Factor 2: Torso angle score (upright < 25° -> 0; horizontal > 55° -> 1.0)
        s_angle = min(1.0, max(0.0, (torso_angle - 28.0) / 32.0))
        # Factor 3: Floor proximity (hips near bottom half of frame, legs collapsed)
        s_floor = min(1.0, max(0.0, (hip_mid_y - 0.60) / 0.25))

        fall_confidence = 0.40 * s_ar + 0.35 * s_angle + 0.25 * s_floor

        # Distinguish Bending vs Falling:
        # When bending to pick up something, hips stay elevated and legs remain upright (leg_height >= 0.20)
        is_standing_leg = (leg_height >= 0.20) and (hip_mid_y < 0.68)
        is_bending = is_standing_leg and (torso_angle > 35.0)

        # Sitting: Torso upright (< 35°), but knees bent and hips lower than standing
        is_sitting = (torso_angle < 32.0) and (abs(hip_mid_y - knee_mid_y) < 0.18) and (aspect_ratio < 0.95)

        # Lying / Fallen horizontal condition:
        # Torso is tilted, hips are down on floor or legs collapsed horizontally, and NOT merely bending
        is_horizontal = (aspect_ratio >= 1.15) and (torso_angle >= 50.0)
        is_ground_level = (hip_mid_y >= 0.68) or (leg_height < 0.18)

        if is_bending:
            is_fall_suspect = False
            fall_confidence = min(fall_confidence, 0.45)
        else:
            is_fall_suspect = (is_horizontal and is_ground_level and fall_confidence >= 0.60) or (fall_confidence >= 0.72)

        if is_fall_suspect:
            posture = "LYING"
            posture_vi = "Nằm / Dấu hiệu ngã"
        elif is_bending:
            posture = "BENDING"
            posture_vi = "Cúi người"
        elif is_sitting:
            posture = "SITTING"
            posture_vi = "Ngồi"
        else:
            posture = "STANDING"
            posture_vi = "Đứng bình thường"

        return PostureMetrics(
            posture=posture,
            posture_vi=posture_vi,
            torso_angle=round(torso_angle, 1),
            aspect_ratio=round(aspect_ratio, 2),
            hip_level=round(hip_mid_y, 2),
            is_fall_suspect=is_fall_suspect,
            fall_confidence=round(float(fall_confidence), 3),
            bbox=(bx1, by1, bw, bh),
            landmarks=results.pose_landmarks,
        )

    def draw_overlay(self, frame: np.ndarray, metrics: PostureMetrics) -> np.ndarray:
        """Render informative Pose & Safety HUD overlay on frame."""
        if frame is None or metrics.bbox is None:
            return frame

        annotated = frame.copy()
        bx, by, bw, bh = metrics.bbox

        # Choose color based on safety posture
        if metrics.is_fall_suspect:
            box_color = (0, 0, 235)  # BGR: Red (Danger / Fall)
            badge_bg = (0, 0, 180)
            tag_text = f"⚠️ NGUY CƠ NGÃ ({metrics.fall_confidence*100:.0f}%)"
        elif metrics.posture == "BENDING":
            box_color = (0, 165, 255)  # Orange: Bending
            badge_bg = (0, 130, 200)
            tag_text = f"Cúi người ({metrics.torso_angle:.0f}°)"
        elif metrics.posture == "SITTING":
            box_color = (255, 191, 0)  # Cyan-blue: Sitting
            badge_bg = (180, 130, 0)
            tag_text = "Tư thế: Ngồi"
        else:
            box_color = (80, 220, 80)  # Green: Standing normal
            badge_bg = (50, 150, 50)
            tag_text = "Tư thế: Đứng an toàn"

        # Draw bounding box
        cv2.rectangle(annotated, (bx, by), (bx + bw, by + bh), box_color, 2)

        # Draw corner tech accents
        clen = max(10, min(bw, bh) // 5)
        cv2.line(annotated, (bx, by), (bx + clen, by), box_color, 3)
        cv2.line(annotated, (bx, by), (bx, by + clen), box_color, 3)
        cv2.line(annotated, (bx + bw, by), (bx + bw - clen, by), box_color, 3)
        cv2.line(annotated, (bx + bw, by), (bx + bw, by + clen), box_color, 3)
        cv2.line(annotated, (bx, by + bh), (bx + clen, by + bh), box_color, 3)
        cv2.line(annotated, (bx, by + bh), (bx, by + bh - clen), box_color, 3)
        cv2.line(annotated, (bx + bw, by + bh), (bx + bw - clen, by + bh), box_color, 3)
        cv2.line(annotated, (bx + bw, by + bh), (bx + bw, by + bh - clen), box_color, 3)

        # Draw pose connections if landmarks available
        if metrics.landmarks:
            try:
                mp.solutions.drawing_utils.draw_landmarks(
                    annotated,
                    metrics.landmarks,
                    self.mp_pose.POSE_CONNECTIONS,
                    landmark_drawing_spec=mp.solutions.drawing_utils.DrawingSpec(
                        color=(0, 229, 250), thickness=2, circle_radius=2
                    ),
                    connection_drawing_spec=mp.solutions.drawing_utils.DrawingSpec(
                        color=box_color, thickness=2
                    ),
                )
            except Exception:
                pass

        # Posture Tag label
        tx = max(12, bx)
        ty = max(24, by - 12)
        draw_vietnamese_text(
            annotated,
            tag_text,
            (tx, ty),
            font_size=15,
            color=(255, 255, 255),
            stroke_color=(0, 0, 0),
            stroke_width=2,
        )

        return annotated
