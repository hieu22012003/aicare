"""
Fall Monitoring Service with Temporal Verification FSM & False-Alert Filtering.
States: NORMAL -> UNOBSERVABLE -> CAMERA_DISCONNECTED -> PAUSED -> SUSPECTED -> CONFIRMING -> FALL_CONFIRMED -> COOLDOWN.
Saves evidence snapshot to data/fall_events/ and triggers Caregiver Gmail alert.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from functools import wraps
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

import cv2
import numpy as np

from shared import config
from shared.config import (
    FALL_COOLDOWN_SECONDS,
    FALL_CONFIDENCE_THRESHOLD,
    FALL_CONFIRM_SECONDS,
    FALL_EVENTS_DIR,
)
from features.fall_risk.hfd_model.fall_detector import FallDetector, PostureMetrics
from shared.services.email_service import DeliveryResult, DeliveryStatus, EmailService
from shared.services.user_service import UserService

logger = logging.getLogger("FallMonitor")


def _pipeline_guard(method):
    @wraps(method)
    def guarded(self, *args, **kwargs):
        with self._pipeline_lock:
            return method(self, *args, **kwargs)
    return guarded


@dataclass
class FallEvent:
    event_id: str
    user_id: str
    user_name: str
    timestamp: str
    confidence: float
    snapshot_path: str
    caregiver_email: str
    email_status: str  # "sent", "failed", "disabled", "no_caregiver", "unconfigured", "smtp_accepted", "sending"
    identity_status: str = "verified"  # "verified" or "unknown"
    delivery_details: str = ""
    retries: int = 0
    video_path: Optional[str] = None


class FallMonitor:
    """
    Manages Fall State Machine with Temporal Confirmation and Evidence Recording.
    Guarantees no single-frame false alarms, filters bending/squatting,
    handles unobservable views, distinguishes camera disconnection,
    supports pausing/resuming during registration, and enforces cooldown period.
    """

    # FSM State Constants
    STATE_NORMAL = "NORMAL"
    STATE_UNOBSERVABLE = "UNOBSERVABLE"
    STATE_DISCONNECTED = "CAMERA_DISCONNECTED"
    STATE_PAUSED = "PAUSED"
    STATE_SUSPECTED = "SUSPECTED"
    STATE_CONFIRMING = "CONFIRMING"
    STATE_FALL_CONFIRMED = "FALL_CONFIRMED"
    STATE_COOLDOWN = "COOLDOWN"

    def __init__(
        self,
        confirm_seconds: float = 0.8,
        cooldown_seconds: float = 2.0,
        confidence_threshold: float = FALL_CONFIDENCE_THRESHOLD,
        user_service: Optional[UserService] = None,
        email_service: Optional[EmailService] = None,
    ) -> None:
        self.confirm_seconds = confirm_seconds
        self._pipeline_lock = threading.RLock()
        self.cooldown_seconds = cooldown_seconds
        self.confidence_threshold = confidence_threshold

        # Legacy metrics remain import-compatible; live inference owns one shared
        # pipeline and must not allocate a second MediaPipe model on startup.
        self.detector = None
        self.pipeline = None
        self._pipeline_frame_index = 0
        self._pipeline_tracks = []
        self._backend_retry_at = 0.
        self.user_service = user_service or UserService()
        self.email_service = email_service or EmailService()

        # FSM tracking
        self.current_state: str = self.STATE_UNOBSERVABLE
        self.suspect_start_time: Optional[float] = None
        self.confirm_start_time: Optional[float] = None
        self.cooldown_start_time: Optional[float] = None
        self.unobservable_since: Optional[float] = None
        self.unobservable_reason: str = ""
        self.is_paused: bool = False
        self._consecutive_observable_frames: int = 0

        # Last event record
        self.last_event: Optional[FallEvent] = None
        self.last_metrics: Optional[PostureMetrics] = None
        self.current_user_id: str = "unknown"
        self.current_user_name: str = "Người dùng"
        self.identity_status: str = "unknown"
        self._dispatched_event_ids: Set[str] = set()

        # Evidence folder
        self.events_dir: Path = FALL_EVENTS_DIR
        self.events_dir.mkdir(parents=True, exist_ok=True)

    @property
    def detector(self):
        """Lazy compatibility for older callers; live never runs this model."""
        if self._legacy_detector is None:
            self._legacy_detector = FallDetector()
        return self._legacy_detector

    @detector.setter
    def detector(self,value):
        self._legacy_detector=value

    def pause(self) -> None:
        """Pause fall monitoring (e.g., when user opens registration form)."""
        logger.info("Fall monitoring paused.")
        self.is_paused = True
        self.reset_pipeline()
        self.suspect_start_time = None
        self.confirm_start_time = None
        self._consecutive_observable_frames = 0
        if self.current_state in (self.STATE_NORMAL, self.STATE_SUSPECTED, self.STATE_CONFIRMING, self.STATE_UNOBSERVABLE):
            self.current_state = self.STATE_PAUSED

    def resume(self) -> None:
        """Resume fall monitoring cleanly without carrying over stale timers."""
        logger.info("Fall monitoring resumed.")
        self.is_paused = False
        self.suspect_start_time = None
        self.confirm_start_time = None
        self._consecutive_observable_frames = 0
        self.current_state = self.STATE_NORMAL

    def set_current_user(self, user_id: str, user_name: Optional[str] = None) -> None:
        """Update active recognized identity for alert dispatching."""
        if not user_id or str(user_id) in ("unknown", "None", ""):
            self.current_user_id = "unknown"
            self.current_user_name = "Người chưa xác định (Người lạ)"
            self.identity_status = "unknown"
            return

        self.current_user_id = str(user_id)
        self.identity_status = "verified"
        if user_name:
            self.current_user_name = user_name
        else:
            profile = self.user_service.get_user_profile(self.current_user_id)
            if profile:
                self.current_user_name = profile.get("name", f"Người dùng {self.current_user_id}")
            else:
                self.current_user_name = f"Người dùng {self.current_user_id}"

    def get_active_caregiver_email(self) -> str:
        """Resolve caregiver email for the currently active user according to safety policy."""
        if self.current_user_id == "unknown" or not self.current_user_id:
            return getattr(config, "DEFAULT_CAREGIVER_EMAIL", "") or ""
        return self.user_service.get_caregiver_email(self.current_user_id) or ""

    _get_active_caregiver_email = get_active_caregiver_email

    @_pipeline_guard
    def process_frame(
        self,
        frame: Optional[np.ndarray],
        camera_connected: bool = True,
        frame_fresh: bool = True,
        pts_sec: Optional[float] = None,
        raw_frame: Optional[np.ndarray] = None,
    ) -> Tuple[np.ndarray, Dict[str, Any]]:
        """
        Execute frame analysis, step Temporal FSM, and render safety overlay.
        Returns: (annotated_frame, status_dict)
        """
        from features.fall_risk.pipeline.fall_pipeline import FallPipeline, FallConfig, FramePacket
        now = time.monotonic() if pts_sec is None else pts_sec
        canvas = frame.copy() if frame is not None and frame.size else np.zeros((480,640,3),dtype=np.uint8)
        if self.is_paused or not camera_connected or not frame_fresh or frame is None:
            self.reset_pipeline()
            self.current_state = self.STATE_PAUSED if self.is_paused else self.STATE_DISCONNECTED if not camera_connected else self.STATE_UNOBSERVABLE
            self.unobservable_reason = "Capture unavailable, paused or stale"
            self.last_metrics = None
            return self._draw_fsm_badge(canvas), self.get_status()
        if self.pipeline is None:
            if self.current_state == 'MODEL_UNAVAILABLE' and now < self._backend_retry_at:
                return self._draw_fsm_badge(canvas), self.get_status()
            try:
                self.pipeline = FallPipeline(config=FallConfig(confirm_seconds=self.confirm_seconds, cooldown_seconds=self.cooldown_seconds))
            except (OSError, RuntimeError, ValueError) as error:
                self.current_state = 'MODEL_UNAVAILABLE'
                self.unobservable_reason = str(error)
                self._backend_retry_at = now+5.
                return self._draw_fsm_badge(canvas), self.get_status()
        packet = FramePacket('camera','camera',self._pipeline_frame_index,now,
                             raw_frame if raw_frame is not None else frame,self.pipeline.run_id)
        self._pipeline_frame_index += 1
        before = len(self.pipeline.events)
        try:
            self._pipeline_tracks = self.pipeline.process(packet)
        except (OSError, RuntimeError, ValueError) as error:
            self.reset_pipeline()
            self.current_state = 'MODEL_UNAVAILABLE'
            self.unobservable_reason = str(error)
            self._backend_retry_at = now+5.
            return self._draw_fsm_badge(canvas), self.get_status()
        priority = ['FALL_CONFIRMED','CONFIRMING','SUSPECTED','ON_FLOOR_UNCERTAIN','UNOBSERVABLE','UNCERTAIN','WARMING_UP','COOLDOWN','RECOVERED','NORMAL']
        states = [t['state'] for t in self._pipeline_tracks]
        self.current_state = next((x for x in priority if x in states),self.STATE_UNOBSERVABLE)
        if self.current_state not in ('FALL_CONFIRMED','CONFIRMING','SUSPECTED') and any(not n.resolved for n in self.pipeline.check_notices):
            self.current_state = 'NEEDS_CHECK'
        self.unobservable_reason = "" if states and self.current_state not in ('UNOBSERVABLE','UNCERTAIN') else "Insufficient observations"
        for event in self.pipeline.events[before:]:
            # Until held-out validation and spatial face-to-track association exist,
            # camera detections remain in-memory trial events, with no patient writes.
            event.notification_status = 'DRY_RUN_NOT_VALIDATED'
        annotated = self.pipeline.render(canvas,self._pipeline_tracks)
        return self._draw_fsm_badge(annotated), self.get_status()

    @_pipeline_guard
    def reset_pipeline(self):
        if self.pipeline is not None:
            self.pipeline.close()
        self.pipeline = None
        self._pipeline_tracks = []
        self._pipeline_frame_index = 0

    def _update_fsm(self, metrics: PostureMetrics, frame: np.ndarray, now: float) -> None:
        """Evaluate temporal transitions according to safety state machine."""
        if self.is_paused:
            self.current_state = self.STATE_PAUSED
            self.suspect_start_time = None
            self.confirm_start_time = None
            return

        # 1. Observability Check
        if not metrics.is_observable:
            self._consecutive_observable_frames = 0
            # If person disappeared while in suspected/confirming, CANCEL suspicion immediately!
            # Do NOT confirm a fall just because someone disappeared or was occluded.
            if self.current_state in (self.STATE_SUSPECTED, self.STATE_CONFIRMING):
                logger.info(
                    "Lost observability during fall suspicion (%s). Canceling suspect timer -> UNOBSERVABLE.",
                    metrics.unobservable_reason,
                )
            if self.current_state in (self.STATE_NORMAL, self.STATE_SUSPECTED, self.STATE_CONFIRMING):
                self.current_state = self.STATE_UNOBSERVABLE
                self.unobservable_since = now
                self.unobservable_reason = metrics.unobservable_reason
                self.suspect_start_time = None
                self.confirm_start_time = None
            return

        # Restored observability with stabilization filter (require >= 3 consecutive frames)
        self._consecutive_observable_frames += 1
        if self.current_state in (self.STATE_UNOBSERVABLE, self.STATE_DISCONNECTED, self.STATE_PAUSED):
            if self._consecutive_observable_frames >= 3:
                logger.info("Fall monitor view stabilized and restored to observable. State -> NORMAL.")
                self.current_state = self.STATE_NORMAL
                self.unobservable_since = None
                self.unobservable_reason = ""
                self.suspect_start_time = None
                self.confirm_start_time = None
            else:
                # Still stabilizing
                return

        is_suspect = metrics.is_fall_suspect and (metrics.fall_confidence >= self.confidence_threshold)

        if self.current_state == self.STATE_NORMAL:
            if is_suspect:
                self.current_state = self.STATE_SUSPECTED
                self.suspect_start_time = now
                logger.info(
                    "Fall state: NORMAL -> SUSPECTED (confidence=%.2f, torso_angle=%.1f°)",
                    metrics.fall_confidence,
                    metrics.torso_angle,
                )

        elif self.current_state == self.STATE_SUSPECTED:
            if not is_suspect:
                # User recovered immediately or movement was brief (e.g., normal bending)
                logger.debug("User recovered from SUSPECTED posture -> NORMAL")
                self.current_state = self.STATE_NORMAL
                self.suspect_start_time = None
            elif self.suspect_start_time and (now - self.suspect_start_time >= 0.5):
                # Persistent abnormal condition for 0.5s -> begin strict confirmation timer
                self.current_state = self.STATE_CONFIRMING
                self.confirm_start_time = now
                logger.info("Fall state: SUSPECTED -> CONFIRMING (awaiting %ss)", self.confirm_seconds)

        elif self.current_state == self.STATE_CONFIRMING:
            if not is_suspect:
                # User stood back up or recovered vertical posture during confirmation window!
                logger.info("User recovered to upright posture during confirmation window. Resetting to NORMAL.")
                self.current_state = self.STATE_NORMAL
                self.confirm_start_time = None
                self.suspect_start_time = None
            elif self.confirm_start_time and (now - self.confirm_start_time >= self.confirm_seconds):
                # Fall confirmed by continuous temporal evidence!
                self.current_state = self.STATE_FALL_CONFIRMED
                logger.warning(
                    "Fall state: CONFIRMING -> FALL_CONFIRMED! (Persistent down for %.1fs, confidence=%.2f)",
                    self.confirm_seconds,
                    metrics.fall_confidence,
                )
                self._handle_fall_confirmed(frame, metrics)

        elif self.current_state == self.STATE_FALL_CONFIRMED:
            # Transition to COOLDOWN to prevent duplicate spamming
            self.current_state = self.STATE_COOLDOWN
            self.cooldown_start_time = now
            logger.info("Fall state: FALL_CONFIRMED -> COOLDOWN (%ss)", self.cooldown_seconds)

        elif self.current_state == self.STATE_COOLDOWN:
            if self.cooldown_start_time and (now - self.cooldown_start_time >= self.cooldown_seconds):
                if not is_suspect:
                    self.current_state = self.STATE_NORMAL
                    self.cooldown_start_time = None
                    logger.info("Cooldown expired and posture safe. Fall state: COOLDOWN -> NORMAL.")

    def _handle_fall_confirmed(self, frame: np.ndarray, metrics: PostureMetrics) -> None:
        """Block the legacy dispatch entry point for this unvalidated release."""
        # Legacy compatibility entry point cannot bypass the trial-only release gate.
        # A future validated release needs explicit per-track identity association.
        logger.info("Legacy dispatch blocked: fall pipeline NOT VERIFIED")
        return
    def _persist_event(self, event_dir: Path, event: FallEvent) -> None:
        """Atomically persist event JSON."""
        event_json_path = event_dir / "event.json"
        try:
            with open(event_json_path, "w", encoding="utf-8") as f:
                json.dump(asdict(event), f, ensure_ascii=False, indent=2)
            logger.info("Fall event persisted: %s (%s)", event.event_id, event.email_status)
        except Exception as e:
            logger.error("Failed saving event.json: %s", e)

    def _draw_fsm_badge(self, frame: np.ndarray) -> np.ndarray:
        """Render prominent safety badge at top-right of video stream."""
        h, w = frame.shape[:2]
        badge_w, badge_h = 240, 42
        bx = max(10, w - badge_w - 14)
        by = 14

        if self.current_state in (self.STATE_NORMAL, "RECOVERED"):
            badge_text = "● THỬ NGHIỆM: CHƯA GHI NHẬN NGÃ"
            bg_color = (25, 60, 25)
            border_color = (50, 205, 50)
            text_color = (80, 240, 80)
        elif self.current_state == self.STATE_PAUSED:
            badge_text = "⏸️ TẠM DỪNG GIÁM SÁT"
            bg_color = (30, 40, 50)
            border_color = (245, 158, 11)
            text_color = (245, 158, 11)
        elif self.current_state == self.STATE_DISCONNECTED:
            badge_text = "❌ MẤT KẾT NỐI CAMERA"
            bg_color = (20, 20, 60)
            border_color = (239, 68, 68)
            text_color = (239, 68, 68)
        elif self.current_state in (self.STATE_UNOBSERVABLE, "UNCERTAIN", "ON_FLOOR_UNCERTAIN", "NEEDS_CHECK", "MODEL_UNAVAILABLE", "WARMING_UP"):
            badge_text = "👁️ GÓC NHÌN BỊ KHUẤT"
            bg_color = (20, 45, 60)
            border_color = (0, 165, 255)
            text_color = (0, 215, 255)
            if self.current_state == 'MODEL_UNAVAILABLE':badge_text = 'KHÔNG THỂ PHÂN TÍCH — KIỂM TRA MODEL'
            if self.current_state == 'WARMING_UP':badge_text = 'ĐANG TÍCH LŨY QUAN SÁT'
        elif self.current_state in (self.STATE_SUSPECTED, self.STATE_CONFIRMING):
            badge_text = "⚠️ NGHI NGỜ NGÃ (XÁC NHẬN)"
            bg_color = (15, 60, 100)
            border_color = (0, 165, 255)
            text_color = (50, 200, 255)
        elif self.current_state == self.STATE_FALL_CONFIRMED:
            badge_text = "🚨 CẢNH BÁO: ĐÃ PHÁT HIỆN NGÃ!"
            bg_color = (20, 20, 120)
            border_color = (0, 0, 255)
            text_color = (255, 255, 255)
        else:  # COOLDOWN
            badge_text = "⏱️ ĐANG HỒI PHỤC (COOLDOWN)"
            bg_color = (50, 50, 60)
            border_color = (140, 140, 160)
            text_color = (200, 220, 255)

        overlay = frame.copy()
        cv2.rectangle(overlay, (bx, by), (bx + badge_w, by + badge_h), bg_color, -1)
        cv2.addWeighted(overlay, 0.75, frame, 0.25, 0, frame)
        cv2.rectangle(frame, (bx, by), (bx + badge_w, by + badge_h), border_color, 2)

        from shared.camera.drawing_utils import draw_vietnamese_text
        draw_vietnamese_text(
            frame,
            badge_text,
            (bx + 10, by + 12),
            font_size=13,
            color=text_color,
            stroke_color=(0, 0, 0),
            stroke_width=2,
        )
        return frame

    @_pipeline_guard
    def get_status(self) -> Dict[str, Any]:
        """Return comprehensive snapshot of current fall monitoring telemetry."""
        posture = self.last_metrics.posture if self.last_metrics else "UNKNOWN"
        posture_vi = self.last_metrics.posture_vi if self.last_metrics else "Không rõ"
        confidence = self.last_metrics.fall_confidence if self.last_metrics else 0.0

        if self.current_user_id == "unknown":
            caregiver_email = getattr(config, "DEFAULT_CAREGIVER_EMAIL", "") or ""
        else:
            caregiver_email = self.user_service.get_caregiver_email(self.current_user_id) or ""

        last_ev_desc = "Không có"
        if self.last_event:
            ev_time = self.last_event.timestamp.split("T")[-1][:8] if "T" in self.last_event.timestamp else ""
            status_desc = {
                "sent": "Đã gửi Gmail",
                "failed": "Lỗi gửi Gmail",
                "sending": "Đang gửi",
                "unconfigured": "Chưa cấu hình Gmail",
                "no_caregiver": "Chưa có email",
            }.get(self.last_event.email_status, self.last_event.email_status)
            last_ev_desc = f"Ngã lúc {ev_time} ({status_desc})"

        # Determine accurate baseline email status
        smtp_configured = self.email_service.is_configured()
        if not smtp_configured:
            current_email_state = "Chưa cấu hình tài khoản gửi Gmail"
        elif not caregiver_email:
            current_email_state = "Chưa có Gmail người thân"
        else:
            current_email_state = "Sẵn sàng gửi cảnh báo"

        # If a recent event is in flight or completed, display its result
        if self.last_event:
            ev_st = self.last_event.email_status
            if ev_st in ("sent", "smtp_accepted", "SMTP_ACCEPTED"):
                email_status = "SMTP đã chấp nhận; chưa xác minh thư đến"
            elif ev_st in ("sending", "SENDING"):
                email_status = "Đang gửi cảnh báo..."
            elif ev_st in ("failed", "FAILED", "failed_auth", "FAILED_AUTH", "failed_network"):
                email_status = "Không thể gửi Gmail (Thất bại)"
            elif ev_st in ("no_email", "no_caregiver"):
                email_status = "Chưa có Gmail người thân"
            elif ev_st in ("unconfigured",):
                email_status = "Chưa cấu hình tài khoản gửi Gmail"
            else:
                email_status = ev_st
        else:
            email_status = current_email_state

        return {
            "backend": getattr(self.pipeline.backend, 'metadata', {}) if self.pipeline else {},
            "tracks": self._pipeline_tracks,
            "trial_events": [asdict(e) for e in self.pipeline.events] if self.pipeline else [],
            "check_notices": [asdict(n) for n in self.pipeline.check_notices] if self.pipeline else [],
            "deployment_status": "NOT VERIFIED — camera alerts are trial-only",
            "fall_state": self.current_state,
            "posture": posture,
            "posture_vi": posture_vi,
            "confidence": confidence,
            "current_user_id": self.current_user_id,
            "current_user_name": self.current_user_name,
            "identity_status": self.identity_status,
            "caregiver_email": caregiver_email,
            "caregiver_email_present": bool(caregiver_email),
            "smtp_configured": smtp_configured,
            "last_fall_event": last_ev_desc,
            "email_status": "Thử nghiệm — chưa bật Gmail thật (NOT VERIFIED)",
            "unobservable_reason": self.unobservable_reason,
            "is_paused": self.is_paused,
            "last_event": asdict(self.last_event) if self.last_event else None,
        }
