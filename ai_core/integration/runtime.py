"""Background worker inside the desktop app: camera frames -> MediaPipe -> AICoreBrain.

* Reads the latest RAW frame from the existing CameraManager (never the annotated one), ~8 fps.
  Simulated/test frames are skipped (``latest_capture_valid``).
* Pose model normally; Holistic (pose + face + hands) only while a medication window is open.
* Optional ONNX object detector every few seconds (env AI_CORE_DETECTOR_MODEL).
* Timers tick once a second even when nobody is in view (medication reminders).
Runs on its own daemon thread; the Qt UI thread is never blocked. Enable/disable with
AI_CORE_ENABLED (default on; off in AI_CARE_TEST_MODE unless explicitly enabled).
"""

from __future__ import annotations

import logging
import os
import threading
import time
from typing import Callable

from ai_core.integration.brain import AICoreBrain
from ai_core.integration.store import AICoreStore
from ai_core.medication_intake_monitoring.domain import HolisticFrame
from ai_core.object_finder.domain import DetectionFrame, DetectionIn
from ai_core.shared.pose.observation import Identity, Observation

log = logging.getLogger("AICore")

PatientProvider = Callable[[], tuple[str | None, bool]]  # (bound patient id, face currently recognised)


def enabled() -> bool:
    value = os.getenv("AI_CORE_ENABLED")
    if value is not None:
        return value.strip().lower() in ("1", "true", "yes", "on")
    return os.getenv("AI_CARE_TEST_MODE") != "1"


def _inside(p, box) -> bool:
    x, y, w, h = box
    return x <= p[0] <= x + w and y <= p[1] <= y + h


class AICoreRuntime:
    def __init__(
        self,
        db,
        camera_manager,
        patient_provider: PatientProvider,
        speak: Callable[[str], None],
        fps: float = 8.0,
        detector_model: str | None = None,
        detector_interval: float = 3.0,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.store = AICoreStore(db)
        self.brain = AICoreBrain(self.store, speak, clock)
        self.camera = camera_manager
        self.provider = patient_provider
        self.period = 1.0 / max(1.0, fps)
        self.detector_model = detector_model if detector_model is not None else os.getenv("AI_CORE_DETECTOR_MODEL") or None
        self.detector_interval = detector_interval
        self.clock = clock
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()  # brain access: worker thread vs voice/UI calls
        self._pose = self._holistic = self._detector = None
        self._detector_failed = False
        self._last_pts = None
        self._last_tick = 0.0
        self._last_det = 0.0
        self._last_dets: list = []

    # ------------------------------------------------------------ lifecycle

    def start(self) -> bool:
        if not self.store.ready():
            log.warning("AI core tables missing (migration 014 not applied); AI features off")
            return False
        self._thread = threading.Thread(target=self._run, name="AICoreWorker", daemon=True)
        self._thread.start()
        log.info("AI core started (posture, fall risk, objects, medication intake)")
        return True

    def stop(self, timeout: float = 3.0) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout)
        for model in (self._pose, self._holistic):
            try:
                if model is not None:
                    model.close()
            except Exception:
                pass

    def _run(self) -> None:
        while not self._stop.is_set():
            started = time.monotonic()
            try:
                self.step()
            except Exception:
                log.exception("AI core step failed")
            self._stop.wait(max(0.0, self.period - (time.monotonic() - started)))

    # ------------------------------------------------------------ one cycle

    def step(self) -> None:
        now = self.clock()
        pid, recognised = self.provider()
        frame, pts, valid = self.camera.get_capture_packet()
        if pid and recognised and frame is not None and valid and pts != self._last_pts:
            self._last_pts = pts
            self._process(pid, frame, now)
        if now - self._last_tick >= 1.0:
            self._last_tick = now
            with self._lock:
                self.brain.tick(now, home_patient=pid)

    def _process(self, pid: str, frame, now: float) -> None:
        h, w = frame.shape[:2]
        with self._lock:
            medication = self.brain.medication_active(pid)
            pillbox = self.brain.pillbox_box(pid)
        detections = self._detect(frame, now)
        if medication:
            if self._holistic is None:
                from ai_core.integration.vision import HolisticEstimator

                self._holistic = HolisticEstimator()
            res = self._holistic.process(frame)
            obs = Observation(now, res.pose, w, h, Identity.PATIENT, 0, self._person_boxes())
            hands = [p for hand in (res.left_hand, res.right_hand) if hand for p in hand]
            near_pillbox = bool(pillbox and any(_inside((x / w, y / h), pillbox) for x, y in hands))
            mouth = None
            if res.face:
                from ai_core.medication_intake_monitoring.detector import mouth_center

                mx, my = mouth_center(res.face)
                mouth = (mx / w, my / h)
            cup = bool(mouth and any(d.label == "cup" and _inside(mouth, self._grow(d.box)) for d in self._last_dets))
            phone = any(d.label == "cell phone" and any(_inside((x / w, y / h), d.box) for x, y in hands) for d in self._last_dets)
            hframe = HolisticFrame(now, res.face, res.left_hand, res.right_hand, True, phone, cup, near_pillbox)
            with self._lock:
                self.brain.on_observation(pid, obs)
                self.brain.on_holistic(pid, hframe)
        else:
            if self._pose is None:
                from ai_core.shared.pose.estimator import PoseEstimator

                self._pose = PoseEstimator()
            landmarks = self._pose.process(frame, int(now * 1000))
            with self._lock:
                self.brain.on_observation(pid, Observation(now, landmarks, w, h, Identity.PATIENT, 0, self._person_boxes()))
        if detections is not None:
            with self._lock:
                self.brain.on_detections(pid, detections)

    @staticmethod
    def _grow(box, k: float = 0.5):
        x, y, bw, bh = box
        return (x - bw * k, y - bh * k, bw * (1 + 2 * k), bh * (1 + 2 * k))

    def _person_boxes(self):
        return tuple((*d.box, d.score) for d in self._last_dets if d.label == "person")

    def _detect(self, frame, now: float):
        if not self.detector_model or self._detector_failed or now - self._last_det < self.detector_interval:
            return None
        self._last_det = now
        if self._detector is None:
            try:
                from ai_core.shared.detect import OnnxDetector

                self._detector = OnnxDetector(self.detector_model)
            except Exception:
                self._detector_failed = True
                log.exception("Object detector unavailable (install onnxruntime, check AI_CORE_DETECTOR_MODEL)")
                return None
        self._last_dets = self._detector.detect(frame)
        dets = tuple(DetectionIn(d.label, d.score, d.box) for d in self._last_dets if d.label != "person")
        return DetectionFrame(now, 0, dets, tuple(d.box for d in self._last_dets if d.label == "person"))

    # ------------------------------------------------------------ voice / UI

    def handle_transcript(self, patient_id: str, text: str) -> str | None:
        with self._lock:
            return self.brain.handle_transcript(patient_id, text)
