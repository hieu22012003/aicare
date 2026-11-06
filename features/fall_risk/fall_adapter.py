"""
Fall Module Adapter for AI Rehabilitation & Safety Assistant.
Integrates FallMonitor and FallDetector into the unified application pipeline.
Exposes real-time posture telemetry and fall alerts without camera locking.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import numpy as np

from shared.config import (
    SHARED_DATA_DIR,
    SHARED_RESULTS_DIR,
    SHARED_DIR,
)
from features.fall_risk.fall_monitor import FallMonitor

logger = logging.getLogger("FallAdapter")


class FallAdapter:
    """Stable In-Memory Fall Adapter wrapping FallMonitor."""

    def __init__(self, fall_monitor: Optional[FallMonitor] = None) -> None:
        self.result_file: Path = SHARED_RESULTS_DIR / "fall.json"
        self.state_file: Path = SHARED_DIR / "state.json"
        self.monitor = fall_monitor or FallMonitor()
        self._is_active = True

    def set_current_user(self, user_id: str, user_name: Optional[str] = None) -> None:
        """Update active recognized identity for alert dispatching."""
        self.monitor.set_current_user(user_id, user_name)

    def pause(self) -> None:
        """Pause fall monitoring FSM."""
        self.monitor.pause()

    def resume(self) -> None:
        """Resume fall monitoring FSM."""
        self.monitor.resume()

    def process_frame(
        self,
        frame: Optional[np.ndarray],
        camera_connected: bool = True,
        frame_fresh: bool = True,
        pts_sec: Optional[float] = None,
        raw_frame: Optional[np.ndarray] = None,
    ) -> Tuple[np.ndarray, Dict[str, Any]]:
        """
        Process frame through Pose Kinematics and Temporal FSM.
        Returns: (annotated_frame, telemetry_dict)
        """
        if not self._is_active:
            return frame if frame is not None else np.zeros((480, 640, 3), dtype=np.uint8), self.get_result()

        annotated, status = self.monitor.process_frame(
            frame, camera_connected=camera_connected, frame_fresh=frame_fresh,
            pts_sec=pts_sec, raw_frame=raw_frame
        )
        self._sync_state(status)
        return annotated, status

    def get_result(self) -> Dict[str, Any]:
        """Return latest fall monitoring status."""
        return self.monitor.get_status()

    def _sync_state(self, status: Dict[str, Any]) -> None:
        """Synchronize telemetry metrics into shared/results/fall.json and state.json."""
        try:
            with open(self.result_file, "w", encoding="utf-8") as f:
                json.dump(status, f, ensure_ascii=False, indent=2)

            if self.state_file.exists():
                try:
                    with open(self.state_file, "r", encoding="utf-8") as f:
                        state = json.load(f)
                    state["fall_state"] = status.get("fall_state", "NORMAL")
                    state["fall_confidence"] = status.get("confidence", 0.0)
                    state["last_fall_event"] = status.get("last_fall_event", "Không có")
                    state["caregiver_email"] = status.get("caregiver_email", "")
                    state["email_status"] = status.get("email_status", "Ready")
                    state["last_update"] = datetime.now().isoformat()
                    with open(self.state_file, "w", encoding="utf-8") as f:
                        json.dump(state, f, ensure_ascii=False, indent=2)
                except Exception:
                    pass
        except Exception as e:
            logger.debug("Error syncing fall state: %s", e)
