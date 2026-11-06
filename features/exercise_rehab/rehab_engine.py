"""
Core Rehabilitation AI Engine for AI Rehabilitation Assistant.
Implements MediaPipe Pose landmark extraction, biomechanical angle calculations,
Finite State Machines (FSM) for repetition counting, form penalty scoring,
real-time HUD telemetry overlay rendering, and periodic TTS voice alerts.

Prioritized Exercises:
1. Arm Raise (Nâng tay qua đầu / Vươn vai)
2. Sit-to-Stand (Đứng lên ngồi xuống / Co duỗi chân)
3. Marching (Đi bộ tại chỗ / Nâng cao đùi)
"""

from __future__ import annotations

import logging
import math
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Tuple

import cv2
import numpy as np

try:
    from shared import config
    import mediapipe as mp
    MP_AVAILABLE = True
except (ImportError, Exception):
    mp = None
    MP_AVAILABLE = False

logger = logging.getLogger("RehabEngine")

# Standard 33-landmark MediaPipe Pose skeletal connections
POSE_CONNECTIONS = [
    (11, 12), (11, 13), (13, 15), (12, 14), (14, 16),
    (11, 23), (12, 24), (23, 24),
    (23, 25), (24, 26), (25, 27), (26, 28),
    (27, 29), (28, 30), (29, 31), (30, 32),
    (0, 1), (1, 2), (2, 3), (3, 7), (0, 4), (4, 5), (5, 6), (6, 8), (9, 10),
]


def calculate_angle(a: Any, b: Any, c: Any) -> float:
    """
    Calculate planar angle at vertex b formed by points a, b, c in degrees.
    Points can be MediaPipe landmarks or objects with .x, .y attributes.
    """
    ax, ay = (a.x, a.y) if hasattr(a, "x") else (a[0], a[1])
    bx, by = (b.x, b.y) if hasattr(b, "x") else (b[0], b[1])
    cx, cy = (c.x, c.y) if hasattr(c, "x") else (c[0], c[1])

    radians = math.atan2(cy - by, cx - bx) - math.atan2(ay - by, ax - bx)
    angle = math.degrees(abs(radians))
    if angle > 180.0:
        angle = 360.0 - angle
    return float(angle)


@dataclass
class ExerciseMetrics:
    exercise_id: str
    exercise_name: str
    target_reps: int
    repetitions: int
    correct_reps: int
    incorrect_reps: int
    stage: str
    score: float
    feedback: str
    fps: float


class BaseExerciseTracker:
    """Base class for exercise movement state machines."""

    base_stage: str = "idle"

    def __init__(self, target_reps: int = 10) -> None:
        self.target_reps = target_reps
        self.reps = 0
        self.correct_reps = 0
        self.incorrect_reps = 0
        self.stage = self.base_stage
        self.feedback = "Chuẩn bị vào tư thế"
        self.form_scores: List[float] = []
        self.rep_penalties = 0.0
        self.rep_issues: List[str] = []
        self.is_paused = False

    def pause(self) -> None:
        """Pause tracker and cancel any partial in-progress repetition."""
        self.is_paused = True
        self.stage = self.base_stage
        self.rep_penalties = 0.0
        self.rep_issues = []

    def resume(self) -> None:
        """Resume tracker in clean base stage."""
        self.is_paused = False
        self.stage = self.base_stage
        self.rep_penalties = 0.0
        self.rep_issues = []

    def reset_in_progress_rep(self) -> None:
        """Cancel partially started rep when observation is lost mid-movement."""
        self.stage = self.base_stage
        self.rep_penalties = 0.0
        self.rep_issues = []

    def process(self, lm: Any, w: int, h: int) -> Tuple[str, str, float]:
        raise NotImplementedError


class ArmRaiseTracker(BaseExerciseTracker):
    """
    Arm Raise (Vươn vai / Nâng tay qua đầu) FSM Tracker.
    Stages: down -> raising -> hold_peak -> lowering -> down
    Form Checks:
      - Elevation angle: shoulder angle >= 142 deg at peak
      - Elbow straightness: elbow angle >= 130 deg
      - Bilateral symmetry: left vs right shoulder angle diff <= 25 deg
      - Peak hold duration: >= 0.4 seconds

    Observability: Requires arms & shoulders (11, 12, 13, 14, 15, 16).
    Lower body/table occlusion does NOT invalidate arm raises.
    Mid-rep occlusion cancels partial rep to prevent phantom counting.
    """

    base_stage = "down"

    def __init__(self, target_reps: int = 10) -> None:
        super().__init__(target_reps=target_reps)
        self.exercise_name = "Nâng tay qua đầu (Arm Raise)"
        self.stage = "down"
        self._hold_start = 0.0
        self._occlusion_start = 0.0

    def reset_in_progress_rep(self) -> None:
        super().reset_in_progress_rep()
        self._occlusion_start = 0.0

    def process(self, lm: Any, w: int, h: int) -> Tuple[str, str, float]:
        now = time.time()
        if self.is_paused:
            curr_score = float(np.mean(self.form_scores)) if (self.form_scores and self.reps > 0) else 0.0
            return "down", "Bài tập đang tạm dừng", round(curr_score, 1)

        # Indices: Left shoulder=11, Right shoulder=12, Left elbow=13, Right elbow=14,
        # Left wrist=15, Right wrist=16, Left hip=23, Right hip=24
        try:
            ls, rs = lm[11], lm[12]
            le, re = lm[13], lm[14]
            lw, rw = lm[15], lm[16]
            lh, rh = lm[23], lm[24]
            # Landmark observability check specifically for arm exercise
            if min(ls.visibility, rs.visibility, le.visibility, re.visibility, lw.visibility, rw.visibility) < 0.35:
                if self._occlusion_start == 0.0:
                    self._occlusion_start = now
                elif (now - self._occlusion_start) > 1.2:
                    if self.stage != "down":
                        self.reset_in_progress_rep()
                curr_score = float(np.mean(self.form_scores)) if (self.form_scores and self.reps > 0) else 0.0
                return self.stage, f"Góc nhìn bị khuất: Vui lòng lùi xa để thấy hai tay (Đã ghi nhận {self.reps} lần)", round(curr_score, 1)
            self._occlusion_start = 0.0
        except Exception:
            curr_score = float(np.mean(self.form_scores)) if (self.form_scores and self.reps > 0) else 0.0
            return self.stage, f"Không thấy đầy đủ khớp tay (Đã ghi nhận {self.reps} lần)", round(curr_score, 1)

        # Robust torso vector: if hips are occluded by desk (e.g. visibility < 0.35),
        # use a vertical torso downward surrogate from shoulder so arm raise is fully functional!
        lh_vis = getattr(lh, "visibility", 1.0)
        rh_vis = getattr(rh, "visibility", 1.0)
        class _Pt:
            def __init__(self, x, y):
                self.x = x
                self.y = y
        ref_lh = lh if lh_vis >= 0.35 else _Pt(ls.x, ls.y + 0.35)
        ref_rh = rh if rh_vis >= 0.35 else _Pt(rs.x, rs.y + 0.35)

        left_sh_ang = calculate_angle(ref_lh, ls, lw)
        right_sh_ang = calculate_angle(ref_rh, rs, rw)
        left_el_ang = calculate_angle(ls, le, lw)
        right_el_ang = calculate_angle(rs, re, rw)

        sh_ang = (left_sh_ang + right_sh_ang) / 2.0
        symmetry_diff = abs(left_sh_ang - right_sh_ang)
        now = time.time()

        if self.stage == "down":
            if sh_ang > 60:
                self.stage = "raising"
                self.rep_penalties = 0.0
                self.rep_issues = []
                self.feedback = "Đang nâng tay lên..."

        elif self.stage == "raising":
            if left_el_ang < 130 or right_el_ang < 130:
                self.rep_penalties += 5.0
                if "Duỗi thẳng khuỷu tay" not in self.rep_issues:
                    self.rep_issues.append("Duỗi thẳng khuỷu tay")
                self.feedback = "Hãy duỗi thẳng khuỷu tay hơn"

            if symmetry_diff > 25:
                self.rep_penalties += 5.0
                if "Nâng đều 2 tay" not in self.rep_issues:
                    self.rep_issues.append("Nâng đều 2 tay")

            if sh_ang >= 142:
                self.stage = "hold_peak"
                self._hold_start = now
                self.feedback = "Giữ nguyên 1 nhịp trên cao..."

        elif self.stage == "hold_peak":
            hold_time = now - self._hold_start
            if sh_ang < 130:
                if hold_time < 0.35:
                    self.rep_penalties += 10.0
                    self.rep_issues.append("Chưa giữ đủ nhịp")
                self.stage = "lowering"
                self.feedback = "Hạ tay xuống chậm rãi..."
            elif hold_time >= 0.5:
                self.feedback = "Rất tốt! Hạ tay xuống đều"

        elif self.stage == "lowering":
            if sh_ang <= 50:
                self.stage = "down"
                self.reps += 1
                rep_score = max(50.0, 100.0 - self.rep_penalties)
                self.form_scores.append(rep_score)

                if self.rep_penalties < 15.0:
                    self.correct_reps += 1
                    self.feedback = f"Đúng tư thế! (Lần {self.reps})"
                else:
                    self.incorrect_reps += 1
                    issue_msg = ", ".join(self.rep_issues[:1]) or "Biên độ chưa chuẩn"
                    self.feedback = f"Lần {self.reps}: {issue_msg}"

        if self.form_scores:
            curr_score = float(np.mean(self.form_scores))
        elif self.stage in ("raising", "hold_peak", "descending"):
            curr_score = max(50.0, 100.0 - self.rep_penalties)
        else:
            curr_score = 0.0
        return self.stage, self.feedback, round(curr_score, 1)


class SitToStandTracker(BaseExerciseTracker):
    """
    Sit-to-Stand (Co duỗi chân / Đứng lên ngồi xuống / Squat) FSM Tracker.
    Dual-mode bi-directional cycle support:
      - Squat (Start Standing): Standing -> Descending -> Bottom (Sitting) -> Ascending -> Standing (Complete Rep!)
      - Sit-to-Stand (Start Sitting): Sitting -> Ascending -> Standing -> Descending -> Sitting (Complete Rep!)
    Resilient to momentary ankle occlusions (camera edge, desk/chair) and transient frame drops.
    """

    base_stage = "ready"

    def __init__(self, target_reps: int = 10) -> None:
        super().__init__(target_reps=target_reps)
        self.exercise_name = "Đứng lên ngồi xuống (Sit-to-Stand / Squat)"
        self.stage = "ready"
        self._had_bottom = False
        self._had_top = False
        self._occlusion_start = 0.0

    def reset_in_progress_rep(self) -> None:
        """Cancel partially started rep when observation is lost mid-movement."""
        self.stage = "ready"
        self._had_bottom = False
        self._had_top = False
        self.rep_penalties = 0.0
        self.rep_issues = []
        self._occlusion_start = 0.0

    def process(self, lm: Any, w: int, h: int) -> Tuple[str, str, float]:
        now = time.time()
        if self.is_paused:
            curr_score = float(np.mean(self.form_scores)) if (self.form_scores and self.reps > 0) else 0.0
            return "ready", "Bài tập đang tạm dừng", round(curr_score, 1)

        try:
            lh, rh = lm[23], lm[24]
            lk, rk = lm[25], lm[26]
            la, ra = lm[27], lm[28]
            ls, rs = lm[11], lm[12]

            # Primary lower-body observability check: hips and knees
            hip_vis = min(lh.visibility, rh.visibility)
            knee_vis = min(lk.visibility, rk.visibility)

            if hip_vis < 0.30 or knee_vis < 0.30:
                if self._occlusion_start == 0.0:
                    self._occlusion_start = now
                elif (now - self._occlusion_start) > 1.2:
                    self.reset_in_progress_rep()
                curr_score = float(np.mean(self.form_scores)) if (self.form_scores and self.reps > 0) else 0.0
                return self.stage, f"Góc nhìn bị khuất: Vui lòng lùi xa để thấy thân người (Đã ghi nhận {self.reps} lần)", round(curr_score, 1)

            self._occlusion_start = 0.0
        except Exception:
            curr_score = float(np.mean(self.form_scores)) if (self.form_scores and self.reps > 0) else 0.0
            return self.stage, f"Không thấy đầy đủ phần chân (Đã ghi nhận {self.reps} lần)", round(curr_score, 1)

        # Ankle surrogate: if ankles are blocked by table/chair or bottom frame edge,
        # project vertically downward from knees
        class _Pt:
            def __init__(self, x: float, y: float) -> None:
                self.x = x
                self.y = y

        ref_la = la if getattr(la, "visibility", 1.0) >= 0.25 else _Pt(lk.x, max(1.0, lk.y + 0.35))
        ref_ra = ra if getattr(ra, "visibility", 1.0) >= 0.25 else _Pt(rk.x, max(1.0, rk.y + 0.35))

        left_knee = calculate_angle(lh, lk, ref_la)
        right_knee = calculate_angle(rh, rk, ref_ra)
        knee_angle = (left_knee + right_knee) / 2.0

        sh_mid = ((ls.x + rs.x) / 2.0, (ls.y + rs.y) / 2.0)
        hip_mid = ((lh.x + rh.x) / 2.0, (lh.y + rh.y) / 2.0)
        torso_angle = math.degrees(math.atan2(abs(sh_mid[0] - hip_mid[0]), max(1e-4, abs(hip_mid[1] - sh_mid[1]))))

        # Hysteresis thresholds:
        # Full upright extension: >= 155 deg
        # Descending inflection: < 140 deg
        # Deep flexion (bottom / sitting): <= 118 deg
        # Ascending inflection: > 125 deg

        if self.stage == "ready":
            if knee_angle >= 155.0:
                self.stage = "standing"
                self._had_top = True
                self.feedback = "Sẵn sàng hạ người xuống..."
            elif knee_angle <= 118.0:
                self.stage = "sitting"
                self._had_bottom = True
                self.feedback = "Sẵn sàng đứng lên..."
            else:
                self.feedback = "Chuẩn bị vào tư thế"

        elif self.stage == "standing":
            self._had_top = True
            if knee_angle < 140.0:
                self.stage = "descending"
                self.feedback = "Hạ người xuống, giữ lưng thẳng..."

        elif self.stage == "descending":
            if torso_angle > 45.0 and "Lưng gập quá sâu" not in self.rep_issues:
                self.rep_penalties += 5.0
                self.rep_issues.append("Lưng gập quá sâu")

            if knee_angle <= 118.0:
                self.stage = "sitting"
                self._had_bottom = True
                self.feedback = "Rất tốt! Đứng thẳng người lên"

        elif self.stage == "sitting":
            self._had_bottom = True
            if knee_angle > 125.0:
                self.stage = "ascending"
                self.feedback = "Đang đứng lên, giữ thẳng lưng..."

        elif self.stage == "ascending":
            if torso_angle > 45.0 and "Lưng gập quá sâu" not in self.rep_issues:
                self.rep_penalties += 5.0
                self.rep_issues.append("Lưng gập quá sâu")

            if knee_angle >= 155.0:
                self.stage = "standing"
                self._had_top = True
                # Check if full cycle (both bottom flexion and upright extension) was completed!
                if self._had_bottom:
                    self.reps += 1
                    rep_score = max(50.0, 100.0 - self.rep_penalties)
                    self.form_scores.append(rep_score)
                    if self.rep_penalties < 15.0:
                        self.correct_reps += 1
                        self.feedback = f"Đúng tư thế! (Lần {self.reps})"
                    else:
                        self.incorrect_reps += 1
                        issue_msg = ", ".join(self.rep_issues[:1]) or "Đứng chưa thẳng"
                        self.feedback = f"Lần {self.reps}: {issue_msg}"
                    self._had_bottom = False
                    self.rep_penalties = 0.0
                    self.rep_issues = []
                else:
                    self.feedback = "Tốt! Đứng thẳng hoàn toàn"

        if self.form_scores:
            curr_score = float(np.mean(self.form_scores))
        elif self.stage in ("ascending", "standing", "descending"):
            curr_score = max(50.0, 100.0 - self.rep_penalties)
        else:
            curr_score = 0.0
        return self.stage, self.feedback, round(curr_score, 1)


class MarchingTracker(BaseExerciseTracker):
    """
    Marching (Đi bộ tại chỗ / Nâng cao đùi) FSM Tracker.
    Stages: stance -> left_up / right_up -> stance
    Form Checks:
      - Elevation height: knee raised higher than hip threshold (diff < 0.18)
      - Alternating leg execution

    Observability: Requires hips and knees (23, 24, 25, 26).
    """

    base_stage = "stance"

    def __init__(self, target_reps: int = 15) -> None:
        super().__init__(target_reps=target_reps)
        self.exercise_name = "Nâng cao đùi tại chỗ (Marching)"
        self.stage = "stance"
        self._last_foot = ""
        self._occlusion_start = 0.0

    def reset_in_progress_rep(self) -> None:
        super().reset_in_progress_rep()
        self._occlusion_start = 0.0

    def process(self, lm: Any, w: int, h: int) -> Tuple[str, str, float]:
        now = time.time()
        if self.is_paused:
            curr_score = float(np.mean(self.form_scores)) if (self.form_scores and self.reps > 0) else 0.0
            return "stance", "Bài tập đang tạm dừng", round(curr_score, 1)

        try:
            lh, rh = lm[23], lm[24]
            lk, rk = lm[25], lm[26]
            # Landmark observability check for marching legs
            if min(lh.visibility, rh.visibility, lk.visibility, rk.visibility) < 0.35:
                if self._occlusion_start == 0.0:
                    self._occlusion_start = now
                elif (now - self._occlusion_start) > 1.2:
                    if self.stage != "stance":
                        self.reset_in_progress_rep()
                curr_score = float(np.mean(self.form_scores)) if (self.form_scores and self.reps > 0) else 0.0
                return self.stage, f"Góc nhìn bị khuất: Vui lòng lùi xa để thấy hai chân (Đã ghi nhận {self.reps} lần)", round(curr_score, 1)
            self._occlusion_start = 0.0
        except Exception:
            curr_score = float(np.mean(self.form_scores)) if (self.form_scores and self.reps > 0) else 0.0
            return self.stage, f"Vui lòng lùi lại để thấy chân (Đã ghi nhận {self.reps} lần)", round(curr_score, 1)

        hip_y = (lh.y + rh.y) / 2.0
        diff_y = rk.y - lk.y  # Positive if left knee is higher (lower y)

        if self.stage == "stance":
            if diff_y > 0.065 and self._last_foot != "left":
                self.stage = "left_up"
                self._last_foot = "left"
                self.reps += 1
                if lk.y < hip_y + 0.18:
                    self.correct_reps += 1
                    self.form_scores.append(100.0)
                    self.feedback = f"Đùi trái nâng tốt! (Bước {self.reps})"
                else:
                    self.incorrect_reps += 1
                    self.form_scores.append(80.0)
                    self.feedback = "Nâng đùi trái cao hơn một chút"
                    self.rep_issues.append("Nâng đầu gối cao hơn")

            elif diff_y < -0.065 and self._last_foot != "right":
                self.stage = "right_up"
                self._last_foot = "right"
                self.reps += 1
                if rk.y < hip_y + 0.18:
                    self.correct_reps += 1
                    self.form_scores.append(100.0)
                    self.feedback = f"Đùi phải nâng tốt! (Bước {self.reps})"
                else:
                    self.incorrect_reps += 1
                    self.form_scores.append(80.0)
                    self.feedback = "Nâng đùi phải cao hơn một chút"
                    self.rep_issues.append("Nâng đầu gối cao hơn")

        elif self.stage in ("left_up", "right_up"):
            if abs(diff_y) < 0.035:
                self.stage = "stance"

        if self.form_scores:
            curr_score = float(np.mean(self.form_scores))
        elif self.stage in ("left_up", "right_up"):
            curr_score = 90.0
        else:
            curr_score = 0.0
        return self.stage, self.feedback, round(curr_score, 1)


class RehabilitationEngine:
    """
    Main Rehabilitation Vision Engine.
    Executes MediaPipe Pose, routes to the active exercise tracker,
    renders HUD overlay, and computes live performance scores.
    """

    TRACKERS = {
        "sit_to_stand": SitToStandTracker,
        "arm_raise": ArmRaiseTracker,
        "marching": MarchingTracker,
    }

    def __init__(self, voice_feedback_fn: Optional[Callable[[str], None]] = None) -> None:
        self.voice_feedback_fn = voice_feedback_fn
        self._pose = None
        self._pose_landmarker = None
        self._active_exercise_id = "arm_raise"
        self._tracker: BaseExerciseTracker = ArmRaiseTracker()
        self._last_spoken_rep = 0
        self._last_form_alert_time = time.time()
        self._last_process_time = time.time()
        self._lost_pose_start = 0.0
        self._lost_pose_ttl = 1.2
        self._fps = 30.0
        self.is_paused = False
        self._init_pose()

    def pause(self) -> None:
        """Pause exercise analysis and cancel partial reps."""
        self.is_paused = True
        if self._tracker:
            self._tracker.pause()

    def resume(self) -> None:
        """Resume exercise analysis in clean state."""
        self.is_paused = False
        if self._tracker:
            self._tracker.resume()

    def _init_pose(self) -> None:
        """Initialize pose estimation model (Legacy solutions.pose or modern Tasks PoseLandmarker)."""
        # 1. Try legacy MediaPipe solutions.pose (e.g. Windows Python 3.12)
        if MP_AVAILABLE and hasattr(mp, "solutions") and hasattr(mp.solutions, "pose"):
            try:
                mp_pose = mp.solutions.pose
                self._pose = mp_pose.Pose(
                    model_complexity=0,
                    min_detection_confidence=0.55,
                    min_tracking_confidence=0.55,
                )
                logger.info("Initialized legacy MediaPipe solutions.pose in RehabilitationEngine.")
                return
            except Exception as e:
                logger.debug("Legacy MediaPipe solutions.pose failed: %s", e)

        # 2. Modern MediaPipe Tasks PoseLandmarker (e.g. Linux Python 3.14 / mediapipe >= 0.10.30)
        try:
            from mediapipe.tasks.python import BaseOptions
            from mediapipe.tasks.python.vision import PoseLandmarker, PoseLandmarkerOptions, RunningMode
            from pathlib import Path

            model_candidates = [
                Path("features/exercise_rehab/models/pose_landmarker_lite.task"),
                Path(__file__).resolve().parent / "models" / "pose_landmarker_lite.task",
            ]
            try:
                from shared import config
                model_candidates.insert(0, config.resource_path("features/exercise_rehab/models/pose_landmarker_lite.task"))
            except Exception:
                pass

            model_path = None
            for cand in model_candidates:
                if cand.exists():
                    model_path = str(cand)
                    break

            if model_path:
                options = PoseLandmarkerOptions(
                    base_options=BaseOptions(model_asset_path=model_path),
                    running_mode=RunningMode.IMAGE,
                    num_poses=1,
                    min_pose_detection_confidence=0.45,
                    min_pose_presence_confidence=0.45,
                    min_tracking_confidence=0.45,
                )
                self._pose_landmarker = PoseLandmarker.create_from_options(options)
                logger.info("Initialized MediaPipe Tasks PoseLandmarker from %s.", model_path)
            else:
                logger.warning("MediaPipe PoseLandmarker model file not found in %s.", model_candidates)
        except Exception as e:
            logger.warning("Failed to initialize MediaPipe Pose: %s", e)

    def select_exercise(self, exercise_id: str, target_reps: Optional[int] = None) -> None:
        """Switch active exercise and instantiate its state machine tracker."""
        if exercise_id in self.TRACKERS:
            self._active_exercise_id = exercise_id
            tracker_cls = self.TRACKERS[exercise_id]
            if target_reps is not None and int(target_reps) > 0:
                self._tracker = tracker_cls(target_reps=int(target_reps))
            else:
                self._tracker = tracker_cls()
            self._last_spoken_rep = 0
            self._last_form_alert_time = time.time()
            logger.info("RehabilitationEngine selected exercise: %s (target_reps=%s)", exercise_id, getattr(self._tracker, "target_reps", None))

    def set_target_reps(self, target_reps: int) -> None:
        """Dynamically update target reps for the current tracker."""
        if hasattr(self, "_tracker") and self._tracker and target_reps > 0:
            self._tracker.target_reps = int(target_reps)
            logger.info("RehabilitationEngine updated target_reps to %d for %s", target_reps, self._active_exercise_id)

    def _get_rep_voice_prompt(self, reps: int, target: int, score: float) -> str:
        """Generate clear, polite, and encouraging Vietnamese repetition cues."""
        rep_cues = {
            1: "Một. Rất tốt ạ.",
            2: "Hai. Đúng tư thế rồi bác.",
            3: "Ba.",
            4: "Bốn. Động tác rất đều.",
            5: "Năm. Bác đã hoàn thành một nửa rồi nhé.",
            6: "Sáu.",
            7: "Bảy. Rất nhịp nhàng.",
            8: "Tám. Cố lên bác nhé.",
            9: "Chín. Một lần nữa thôi ạ.",
            10: "Mười. Chúc mừng bác đã hoàn thành bài tập xuất sắc!",
        }
        if reps in rep_cues:
            return rep_cues[reps]
        elif reps == target:
            return f"Lần thứ {reps}. Chúc mừng bác đã hoàn thành mục tiêu bài tập!"
        else:
            return f"Lần {reps}."

    def _get_form_correction_prompt(self, issue: str) -> Optional[str]:
        """Generate gentle, courteous Vietnamese form correction cues for older adults."""
        corrections = {
            "Duỗi thẳng khuỷu tay": "Bác chú ý duỗi thẳng khuỷu tay hơn nhé.",
            "Nâng đều 2 tay": "Bác hãy nâng đều hai tay qua vai nhé.",
            "Chưa giữ đủ nhịp": "Bác giữ tay trên cao một nhịp rồi hãy hạ xuống nhé.",
            "Lưng gập quá sâu": "Bác chú ý giữ thẳng lưng khi đứng lên nhé.",
            "Đứng chưa thẳng": "Bác hãy đứng thẳng người lên hoàn toàn nhé.",
            "Nâng đầu gối cao hơn": "Bác hãy nâng đầu gối cao hơn một chút nhé.",
        }
        return corrections.get(issue)

    def get_metrics(self) -> Dict[str, Any]:
        """Return snapshot of current exercise progress."""
        has_score = bool(self._tracker.form_scores and self._tracker.reps > 0)
        avg_score = float(np.mean(self._tracker.form_scores)) if has_score else 0.0
        return {
            "exercise_id": self._active_exercise_id,
            "exercise_name": getattr(self._tracker, "exercise_name", self._active_exercise_id),
            "target_reps": self._tracker.target_reps,
            "repetitions": self._tracker.reps,
            "correct_reps": self._tracker.correct_reps,
            "incorrect_reps": self._tracker.incorrect_reps,
            "stage": self._tracker.stage,
            "score": round(avg_score, 1) if has_score else 0.0,
            "has_score": has_score,
            "feedback": self._tracker.feedback,
            "fps": round(self._fps, 1),
            "is_paused": self.is_paused,
        }

    def reset_session(self) -> None:
        """Reset counters for current exercise."""
        self.select_exercise(self._active_exercise_id)

    def process_frame(self, frame: np.ndarray) -> Tuple[np.ndarray, Dict[str, Any]]:
        """
        Process frame with Pose, evaluate exercise state machine, and render HUD.
        """
        t0 = time.time()
        dt = max(1e-4, t0 - self._last_process_time)
        self._fps = 0.9 * self._fps + 0.1 * (1.0 / dt)
        self._last_process_time = t0

        h, w = frame.shape[:2]
        annotated = frame.copy()

        if self.is_paused:
            feedback = "Bài tập đang tạm dừng"
            annotated = self._render_hud(annotated, "paused", feedback, 0.0)
            return annotated, self.get_metrics()

        stage = self._tracker.stage
        feedback = self._tracker.feedback
        has_score = bool(self._tracker.form_scores and self._tracker.reps > 0)
        score = float(np.mean(self._tracker.form_scores)) if has_score else 0.0

        lm = None
        if self._pose is not None:
            try:
                rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                results = self._pose.process(rgb)

                if results.pose_landmarks:
                    mp_drawing = mp.solutions.drawing_utils
                    mp_pose = mp.solutions.pose
                    mp_drawing.draw_landmarks(
                        annotated,
                        results.pose_landmarks,
                        mp_pose.POSE_CONNECTIONS,
                        mp_drawing.DrawingSpec(color=(0, 220, 255), thickness=2, circle_radius=3),
                        mp_drawing.DrawingSpec(color=(220, 50, 120), thickness=2, circle_radius=2),
                    )

                    lm = results.pose_landmarks.landmark
            except Exception as e:
                logger.debug("Pose estimation error: %s", e)

        elif self._pose_landmarker is not None:
            try:
                rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                mp_img = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
                results = self._pose_landmarker.detect(mp_img)

                if results.pose_landmarks and len(results.pose_landmarks) > 0:
                    lm = results.pose_landmarks[0]
                    # Draw skeleton connections
                    for p1_idx, p2_idx in POSE_CONNECTIONS:
                        if p1_idx < len(lm) and p2_idx < len(lm):
                            p1, p2 = lm[p1_idx], lm[p2_idx]
                            v1 = getattr(p1, "visibility", 1.0)
                            v2 = getattr(p2, "visibility", 1.0)
                            if (v1 is None or v1 > 0.3) and (v2 is None or v2 > 0.3):
                                pt1 = (int(p1.x * w), int(p1.y * h))
                                pt2 = (int(p2.x * w), int(p2.y * h))
                                cv2.line(annotated, pt1, pt2, (0, 220, 255), 2, cv2.LINE_AA)
                    # Draw landmark points
                    for p in lm:
                        v = getattr(p, "visibility", 1.0)
                        if v is None or v > 0.3:
                            pt = (int(p.x * w), int(p.y * h))
                            cv2.circle(annotated, pt, 4, (220, 50, 120), -1, cv2.LINE_AA)
            except Exception as e:
                logger.debug("PoseLandmarker estimation error: %s", e)

        if lm is not None:
            self._lost_pose_start = 0.0
            stage, feedback, score = self._tracker.process(lm, w, h)
        else:
            now = time.time()
            if self._lost_pose_start == 0.0:
                self._lost_pose_start = now
            elif (now - self._lost_pose_start) > self._lost_pose_ttl:
                self._tracker.reset_in_progress_rep()

            stage = self._tracker.stage
            feedback = f"Vui lòng đứng trước camera để tập (Đã ghi nhận {self._tracker.reps} lần)"
            has_score = bool(self._tracker.form_scores and self._tracker.reps > 0)
            score = float(np.mean(self._tracker.form_scores)) if has_score else 0.0

        # Intelligent Event-Driven Vietnamese Rehabilitation Voice Coach
        now = time.time()
        current_reps = self._tracker.reps

        if self.voice_feedback_fn and not self.is_paused:
            # Event 1: Completed a repetition -> speak clear, encouraging count in Vietnamese
            if current_reps > self._last_spoken_rep:
                self._last_spoken_rep = current_reps
                self._last_form_alert_time = now # prevent form alert collision
                rep_cue = self._get_rep_voice_prompt(current_reps, self._tracker.target_reps, score)
                self.voice_feedback_fn(rep_cue)

            # Event 2: Form correction during movement -> spoken gently with >= 7.0s cooldown
            elif (now - self._last_form_alert_time >= 7.0) and self._tracker.rep_issues:
                if self._tracker.stage not in ("down", "sitting", "stance", "idle", "ready"):
                    latest_issue = self._tracker.rep_issues[-1]
                    coach_prompt = self._get_form_correction_prompt(latest_issue)
                    if coach_prompt:
                        self.voice_feedback_fn(coach_prompt)
                        self._last_form_alert_time = now

        # Render HUD Overlay
        annotated = self._render_hud(annotated, stage, feedback, score)

        metrics = self.get_metrics()
        return annotated, metrics

    def _render_hud(self, frame: np.ndarray, stage: str, feedback: str, score: float) -> np.ndarray:
        """Render compact, high-contrast biometric exercise HUD with crisp Vietnamese text."""
        from shared.camera.drawing_utils import draw_vietnamese_text

        h, w = frame.shape[:2]

        # 1. Top compact telemetry card (52px tall, ~320px wide)
        card_w = min(320, w - 20)
        cv2.rectangle(frame, (10, 10), (10 + card_w, 62), (18, 24, 36), -1)
        cv2.rectangle(frame, (10, 10), (10 + card_w, 62), (59, 130, 246), 1)

        raw_ex_name = getattr(self._tracker, "exercise_name", self._active_exercise_id)
        short_ex_name = raw_ex_name[:14] + ".." if len(raw_ex_name) > 14 else raw_ex_name
        reps_str = f"Lần tập: {self._tracker.reps}/{self._tracker.target_reps}"
        if self._tracker.reps > 0 and self._tracker.form_scores:
            score_str = f"Đúng: {score:.0f}%"
        else:
            score_str = "Đúng: --"

        # Friendly Vietnamese translation for movement stage
        stage_map = {
            "down": "Chuẩn bị",
            "raising": "Nâng tay",
            "hold_peak": "Giữ đỉnh",
            "lowering": "Hạ tay",
            "sitting": "Chuẩn bị",
            "standing_up": "Đứng lên",
            "standing": "Đứng thẳng",
            "sitting_down": "Ngồi xuống",
            "descending": "Hạ người",
            "ascending": "Đứng lên",
            "stepping_left": "Chân trái",
            "stepping_right": "Chân phải",
            "ready": "Sẵn sàng",
        }
        stage_vn = stage_map.get(stage.lower(), stage[:10])

        # Row 1: Exercise name & Repetitions
        draw_vietnamese_text(frame, short_ex_name, (18, 14), font_size=14, color=(255, 255, 255))
        draw_vietnamese_text(frame, reps_str, (170, 14), font_size=14, color=(248, 189, 56))

        # Row 2: Movement Stage & Form score
        draw_vietnamese_text(frame, f"Trạng thái: {stage_vn}", (18, 38), font_size=12, color=(203, 213, 225))
        draw_vietnamese_text(frame, score_str, (170, 38), font_size=12, color=(74, 222, 128))

        # 2. Bottom Live Feedback Bar (Slim 26px banner, truncated to avoid clipping)
        bar_y1 = h - 34
        cv2.rectangle(frame, (10, bar_y1), (w - 10, h - 8), (18, 24, 36), -1)
        cv2.rectangle(frame, (10, bar_y1), (w - 10, h - 8), (16, 185, 129), 1)

        # Truncate feedback so it never overflows right border
        short_fb = feedback[:42] + "..." if len(feedback) > 42 else feedback
        fb_text = f"💡 Hướng dẫn: {short_fb}"
        draw_vietnamese_text(frame, fb_text, (18, h - 28), font_size=12, color=(255, 255, 255))

        return frame

