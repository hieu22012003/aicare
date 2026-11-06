"""Pose estimation shared by F3 (posture), F4 (fall risk) and F9 (exercise).

Run pose once per frame and hand the same landmarks to every feature.
"""

from ai_core.shared.pose.landmarks import NUM_LANDMARKS, Landmark, PoseLandmark

__all__ = ["NUM_LANDMARKS", "Landmark", "PoseLandmark"]
