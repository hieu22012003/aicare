"""Desktop app as a home device of the cloud backend (Render).

When AICARE_CLOUD_URL and AICARE_DEVICE_KEY are set, the desktop app does not run the AI brain
locally. It runs MediaPipe on its own camera and sends only landmarks to the server, which owns the
medication schedule, the reminders and the caregiver web portal:

    capture thread : camera frame -> Pose (or Holistic while a medication window is open) -> queue
    network thread : POST /observations + /holistic every second
                     GET  /commands every 2 s -> speak new lines, follow monitoring.holistic
When the server opens a medication window (5 minutes before the dose) the camera is switched on
(``wake_camera``) even if it was stopped, and Holistic tracking starts so the server can see the
right hand bring the pill to the open mouth. No video leaves the home.

Environment
    AICARE_CLOUD_URL         e.g. https://aicare.onrender.com
    AICARE_DEVICE_KEY        one of the keys in the server's AICARE_DEVICE_KEYS (>= 16 characters)
    AICARE_CLOUD_PATIENT_ID  patient id on the server (default: the patient bound in the app)
"""

from __future__ import annotations

import logging
import os
import threading
import time
from collections import deque
from typing import Callable

log = logging.getLogger("AICloudDevice")

PatientProvider = Callable[[], tuple[str | None, bool]]


def cloud_settings() -> tuple[str, str] | None:
    url = (os.getenv("AICARE_CLOUD_URL") or "").strip().rstrip("/")
    key = (os.getenv("AICARE_DEVICE_KEY") or "").strip()
    return (url, key) if url and len(key) >= 16 else None


def _age_seconds(iso: str | None) -> float:
    from datetime import datetime, timezone

    try:
        return (datetime.now(timezone.utc) - datetime.fromisoformat(iso)).total_seconds()
    except Exception:
        return 0.0


class HttpTransport:
    """Tiny JSON client (requests), separated so tests can talk to an in-process server."""

    def __init__(self, base_url: str, key: str, timeout: float = 8.0) -> None:
        import requests

        self.base = base_url.rstrip("/")
        self.session = requests.Session()
        self.session.headers.update({"X-Device-Key": key, "Content-Type": "application/json"})
        self.timeout = timeout

    def get(self, path: str, params: dict | None = None) -> dict:
        r = self.session.get(self.base + path, params=params, timeout=self.timeout)
        r.raise_for_status()
        return r.json()

    def post(self, path: str, body: dict) -> dict:
        r = self.session.post(self.base + path, json=body, timeout=self.timeout)
        r.raise_for_status()
        return r.json()


class CloudDeviceRuntime:
    def __init__(
        self,
        transport,
        camera_manager,
        patient_provider: PatientProvider,
        speak: Callable[[str], None],
        wake_camera: Callable[[], None] | None = None,
        cloud_patient_id: str | None = None,
        fps: float = 8.0,
        pose_factory: Callable[[], object] | None = None,
        holistic_factory: Callable[[], object] | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.http = transport
        self.camera = camera_manager
        self.provider = patient_provider
        self.speak = speak
        self.wake_camera = wake_camera
        self.cloud_pid = cloud_patient_id or os.getenv("AICARE_CLOUD_PATIENT_ID") or None
        self.period = 1.0 / max(1.0, fps)
        self.pose_factory = pose_factory
        self.holistic_factory = holistic_factory
        self.clock = clock
        self.holistic_mode = False
        self.pillbox_box = None
        self.last_command = 0
        self.online = False
        self._pose = self._holistic = None
        self._last_pts = None
        self._obs: deque = deque(maxlen=600)
        self._frames: deque = deque(maxlen=600)
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []
        self._pid: str | None = None
        self._woken_for_window = False
        self._first_poll = True

    # ------------------------------------------------------------ lifecycle

    def start(self) -> bool:
        for name, target in (("AICloudCapture", self._capture_loop), ("AICloudNet", self._net_loop)):
            t = threading.Thread(target=target, name=name, daemon=True)
            t.start()
            self._threads.append(t)
        log.info("Cloud device mode: landmarks go to %s", getattr(self.http, "base", "server"))
        return True

    def stop(self, timeout: float = 3.0) -> None:
        self._stop.set()
        for t in self._threads:
            t.join(timeout)
        for model in (self._pose, self._holistic):
            try:
                if model is not None:
                    model.close()
            except Exception:
                pass

    def _capture_loop(self) -> None:
        while not self._stop.is_set():
            started = time.monotonic()
            try:
                self.capture_step()
            except Exception:
                log.exception("cloud capture step failed")
            self._stop.wait(max(0.0, self.period - (time.monotonic() - started)))

    def _net_loop(self) -> None:
        last_poll = 0.0
        while not self._stop.wait(1.0):
            try:
                self.flush()
                if time.monotonic() - last_poll >= 2.0:
                    last_poll = time.monotonic()
                    self.poll_commands()
                self.online = True
            except Exception as exc:
                if self.online:
                    log.warning("Cloud backend unreachable (%s); keeping data queued", exc)
                self.online = False

    # ------------------------------------------------------------ capture

    def patient_id(self) -> str | None:
        pid, _ = self.provider()
        return self.cloud_pid or pid

    def capture_step(self) -> None:
        local_pid, recognised = self.provider()
        pid = self.cloud_pid or local_pid
        self._pid = pid
        frame, pts, valid = self.camera.get_capture_packet()
        if not (pid and recognised and frame is not None and valid and pts != self._last_pts):
            return
        self._last_pts = pts
        now = self.clock()
        h, w = frame.shape[:2]
        if self.holistic_mode:
            if self._holistic is None:
                self._holistic = self._make_holistic()
            res = self._holistic.process(frame)
            self._add_observation(now, res.pose, w, h)
            hands = [p for hand in (res.left_hand, res.right_hand) if hand for p in hand]
            box = self.pillbox_box
            near = bool(box and any(box[0] <= x / w <= box[0] + box[2] and box[1] <= y / h <= box[1] + box[3]
                                    for x, y in hands))
            with self._lock:
                self._frames.append({
                    "ts": now, "recognized": True, "near_pillbox": near,
                    "face": [list(p) for p in res.face] if res.face else None,
                    "left_hand": [list(p) for p in res.left_hand] if res.left_hand else None,
                    "right_hand": [list(p) for p in res.right_hand] if res.right_hand else None,
                })
        else:
            if self._pose is None:
                self._pose = self._make_pose()
            self._add_observation(now, self._pose.process(frame, int(now * 1000)), w, h)

    def _add_observation(self, now: float, landmarks, w: int, h: int) -> None:
        with self._lock:
            self._obs.append({
                "ts": now, "image_width": int(w), "image_height": int(h), "recognized": True,
                "landmarks": [[p.x, p.y, p.z, p.visibility] for p in landmarks] if landmarks else None,
            })

    def _make_pose(self):
        if self.pose_factory:
            return self.pose_factory()
        from shared.vision.pose.estimator import PoseEstimator

        return PoseEstimator()

    def _make_holistic(self):
        if self.holistic_factory:
            return self.holistic_factory()
        from shared.ai_runtime.vision import HolisticEstimator

        return HolisticEstimator()

    # ------------------------------------------------------------ network

    def flush(self) -> None:
        pid = self._pid or self.patient_id()
        if not pid:
            return
        with self._lock:
            obs, frames = list(self._obs), list(self._frames)
            self._obs.clear()
            self._frames.clear()
        try:
            if obs:
                res = self.http.post(f"/api/device/patients/{pid}/observations", {"observations": obs})
                self._set_mode(bool(res.get("medication_monitoring")))
            if frames:
                res = self.http.post(f"/api/device/patients/{pid}/holistic", {"frames": frames})
                self._set_mode(bool(res.get("medication_monitoring")))
        except Exception:
            with self._lock:  # put them back (oldest first) so a short outage loses nothing
                self._obs.extendleft(reversed(obs))
                self._frames.extendleft(reversed(frames))
            raise

    def poll_commands(self) -> None:
        pid = self._pid or self.patient_id()
        if not pid:
            return
        data = self.http.get(f"/api/device/patients/{pid}/commands", {"after": str(self.last_command)})
        first, self._first_poll = self._first_poll, False
        for cmd in data.get("commands", []):
            self.last_command = max(self.last_command, int(cmd.get("command_id", 0)))
            text = (cmd.get("payload") or {}).get("text")
            if first and _age_seconds(cmd.get("created_at_utc")) > 120:
                continue  # app just started: do not read out a backlog of old reminders
            if cmd.get("kind") == "speak" and text:
                try:
                    self.speak(text)
                except Exception:
                    log.exception("TTS failed: %s", text)
        monitoring = data.get("monitoring") or {}
        self.pillbox_box = monitoring.get("pillbox_box")
        self._set_mode(bool(monitoring.get("holistic")))

    def _set_mode(self, holistic: bool) -> None:
        if holistic and not self.holistic_mode:
            log.info("Medication window open: camera on, tracking hands and mouth")
        self.holistic_mode = holistic
        if holistic and not self._woken_for_window and self.wake_camera is not None:
            self._woken_for_window = True
            self.wake_camera()
        if not holistic:
            self._woken_for_window = False

    # ------------------------------------------------------------ voice

    def medication_active(self, pid: str | None = None) -> bool:
        return self.holistic_mode

    def handle_transcript(self, patient_id: str, text: str) -> str | None:
        pid = self.cloud_pid or patient_id
        try:
            return self.http.post(f"/api/device/patients/{pid}/voice", {"text": text[:300]}).get("reply")
        except Exception:
            log.warning("Cloud voice request failed", exc_info=True)
            return None
