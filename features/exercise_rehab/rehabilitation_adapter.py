"""
Rehabilitation Module Adapter for AI Rehabilitation Assistant.
Directly interfaces with RehabilitationEngine for real-time Pose state tracking,
form evaluation, repetition counting, and objective scoring without camera locking.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Tuple

import numpy as np

from shared.config import (
    MODULES_DIR,
    SHARED_RESULTS_DIR,
    SHARED_DIR,
    EXERCISES,
)
from features.exercise_rehab.rehab_engine import RehabilitationEngine

logger = logging.getLogger("RehabilitationAdapter")


class RehabilitationAdapter:
    """Stable In-Memory Rehabilitation Adapter wrapping RehabilitationEngine."""

    def __init__(self, voice_feedback_fn: Optional[Callable[[str], None]] = None) -> None:
        self.result_file: Path = SHARED_RESULTS_DIR / "rehabilitation.json"
        self.state_file: Path = SHARED_DIR / "state.json"
        self.engine = RehabilitationEngine(voice_feedback_fn=voice_feedback_fn)
        self._is_active = False
        self._active_exercise = "arm_raise"

    def select_exercise(self, exercise_id: str, target_reps: Optional[int] = None) -> None:
        """Select active exercise ('sit_to_stand', 'arm_raise', 'marching')."""
        norm_id = exercise_id.lower().replace("-", "_")
        if norm_id in ("arm_raises", "arm_raise", "vươn vai", "nâng tay"):
            norm_id = "arm_raise"
        elif norm_id in ("sit_to_stand", "co duỗi chân", "đứng lên ngồi xuống"):
            norm_id = "sit_to_stand"
        elif norm_id in ("marching", "march_steps", "đi bộ tại chỗ", "nâng cao đùi"):
            norm_id = "marching"

        self._active_exercise = norm_id
        self.engine.select_exercise(norm_id, target_reps=target_reps)
        logger.info("RehabilitationAdapter selected: %s (target_reps=%s)", norm_id, target_reps)

    def set_target_reps(self, target_reps: int) -> None:
        """Dynamically update target reps in active exercise engine."""
        self.engine.set_target_reps(target_reps)

    def start_exercise(self, exercise_id: Optional[str] = None, target_reps: Optional[int] = None) -> bool:
        """Begin exercise session."""
        if exercise_id:
            self.select_exercise(exercise_id, target_reps=target_reps)
        elif target_reps:
            self.set_target_reps(target_reps)
        self._is_active = True
        self.engine.reset_session()
        self._persist_current_metrics()
        return True

    def pause(self) -> None:
        """Pause rehabilitation exercise engine."""
        self.engine.pause()

    def resume(self) -> None:
        """Resume rehabilitation exercise engine."""
        self.engine.resume()

    def stop_exercise(self) -> Dict[str, Any]:
        """Finish exercise session and return final score/metrics."""
        self._is_active = False
        metrics = self.get_result()
        score_desc = f"{metrics.get('score')}%" if metrics.get("repetitions", 0) > 0 else "--"
        logger.info("Finished exercise %s with score: %s", metrics.get("exercise_name"), score_desc)
        return metrics

    def process_frame(self, frame: np.ndarray) -> Tuple[np.ndarray, Dict[str, Any]]:
        """
        Process incoming frame, evaluate movement form, and return annotated frame.
        """
        annotated_frame, metrics = self.engine.process_frame(frame)
        self._persist_current_metrics(metrics)
        return annotated_frame, metrics

    def _persist_current_metrics(self, metrics: Optional[Dict[str, Any]] = None) -> None:
        """Save latest exercise results to local JSON."""
        if metrics is None:
            metrics = self.engine.get_metrics()

        reps = metrics.get("repetitions", 0)
        has_score = metrics.get("has_score", reps > 0)
        score_val = metrics.get("score", 0.0) if has_score else 0.0

        payload = {
            "patient_id": "patient_001",
            "exercise": metrics.get("exercise_id", self._active_exercise),
            "exercise_name": metrics.get("exercise_name", "Phục hồi chức năng"),
            "target_reps": metrics.get("target_reps", 10),
            "repetitions": reps,
            "correct_reps": metrics.get("correct_reps", 0),
            "incorrect_reps": metrics.get("incorrect_reps", 0),
            "stage": metrics.get("stage", "ready"),
            "score": score_val,
            "has_score": has_score,
            "feedback": metrics.get("feedback", "Đang tập luyện"),
            "fps": metrics.get("fps", 30.0),
            "timestamp": datetime.now().isoformat(),
        }

        try:
            with open(self.result_file, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)

            if self.state_file.exists():
                with open(self.state_file, "r", encoding="utf-8") as f:
                    state = json.load(f)
                state["active_exercise"] = payload["exercise"]
                state["exercise_stage"] = payload["stage"]
                state["repetitions"] = payload["repetitions"]
                state["correct_reps"] = payload["correct_reps"]
                state["incorrect_reps"] = payload["incorrect_reps"]
                state["score"] = payload["score"]
                state["has_score"] = payload["has_score"]
                state["form_feedback"] = payload["feedback"]
                if reps > 0 and has_score:
                    state["last_exercise"] = f"{payload['exercise_name']} ({payload['repetitions']} lần - {payload['score']}%)"
                else:
                    state["last_exercise"] = f"{payload['exercise_name']} (0 lần)"
                state["last_update"] = datetime.now().isoformat()
                with open(self.state_file, "w", encoding="utf-8") as f:
                    json.dump(state, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.debug("Error writing rehabilitation JSON: %s", e)

    def get_result(self) -> Dict[str, Any]:
        """Read latest rehabilitation summary from local JSON."""
        if self.result_file.exists():
            try:
                with open(self.result_file, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
        metrics = dict(self.engine.get_metrics())
        if "exercise" not in metrics:
            metrics["exercise"] = metrics.get("exercise_id", "")
        return metrics

    def record_exercise(
        self,
        exercise_name: str,
        repetitions: int = 15,
        score: float = 80.0,
        correct: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Record manual or simulated exercise completion."""
        corr = correct if correct is not None else int(round(repetitions * (score / 100.0)))
        incorr = max(0, repetitions - corr)
        data = {
            "patient_id": "patient_001",
            "exercise": exercise_name.lower().replace(" ", "_"),
            "exercise_name": exercise_name,
            "repetitions": repetitions,
            "correct_reps": corr,
            "incorrect_reps": incorr,
            "correct": corr,
            "score": score,
            "feedback": "Hoàn thành bài tập",
            "timestamp": datetime.now().isoformat(),
        }
        self._persist_current_metrics(data)
        return data

    # Backward compatibility stubs
    def start(self, exercise: str = "arm_raises") -> bool:
        return self.start_exercise(exercise)

    def stop(self) -> None:
        self.stop_exercise()

    def is_running(self) -> bool:
        return self._is_active
