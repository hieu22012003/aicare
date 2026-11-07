"""Cloud-backend mode: the AI runs in the web server, home devices send landmarks and fetch speech.

Home device (camera box, old phone, the desktop app) runs MediaPipe locally and sends results,
so no video leaves the home:

    POST /api/device/patients/{id}/observations   pose landmarks (33 points, normalized)
    POST /api/device/patients/{id}/holistic       face (468) + hands (21) in pixels, during medication windows
    POST /api/device/patients/{id}/detections     object detector output (normalized boxes)
    POST /api/device/patients/{id}/voice          {"text": transcript} -> {"reply": ...}
    GET  /api/device/patients/{id}/commands?after=<id>   speech to play + whether to run Holistic now
    GET  /api/device/patients/{id}/snapshot       medication plan, doses and music list made on the web
    POST /api/device/patients/{id}/sync           rows recorded at home (emotion, exercise, vitals, games,
                                                  face sightings) + dose actions pressed at home
    GET  /api/device/patients/{id}/music/{track}  family music file (audio bytes)

Auth: header ``X-Device-Key``. AICARE_DEVICE_KEYS = "key1:patient_a|patient_b,key2:*".
A background ticker drives the medication milestones and posture/fall timers every second.
"""

from __future__ import annotations

import hmac
import logging
import os
import threading
import time
from datetime import datetime, timedelta, timezone

from pydantic import BaseModel, Field, ValidationError, field_validator

from shared.ai_runtime.brain import AICoreBrain
from shared.ai_runtime.store import AICoreStore
from features.medication.intake_monitoring.domain import HolisticFrame
from features.object_finder.domain import DetectionFrame, DetectionIn
from shared.vision.pose.landmarks import NUM_LANDMARKS, Landmark
from shared.vision.pose.observation import Identity, Observation

log = logging.getLogger("AICore")
UTC = timezone.utc

# ------------------------------------------------------------------ payloads

Box = tuple[float, float, float, float]


class ObservationIn(BaseModel):
    ts: float
    landmarks: list[tuple[float, float, float, float]] | None = Field(None, description="33 x [x, y, z, visibility], normalized")
    image_width: int = Field(640, ge=1, le=8192)
    image_height: int = Field(480, ge=1, le=8192)
    recognized: bool = Field(True, description="the face recognised on the device is this patient")
    camera_index: int = Field(0, ge=0, le=16)
    person_boxes: list[tuple[float, float, float, float, float]] = Field(default_factory=list, max_length=10)

    @field_validator("landmarks")
    @classmethod
    def _n(cls, v):
        if v is not None and len(v) != NUM_LANDMARKS:
            raise ValueError(f"landmarks must have {NUM_LANDMARKS} points")
        return v

    def to_observation(self) -> Observation:
        lms = tuple(Landmark(*p) for p in self.landmarks) if self.landmarks else None
        return Observation(self.ts, lms, self.image_width, self.image_height,
                           Identity.PATIENT if self.recognized else Identity.UNKNOWN, self.camera_index, tuple(self.person_boxes))


class HolisticIn(BaseModel):
    ts: float
    face: list[tuple[float, float]] | None = Field(None, description="468 points, pixels")
    left_hand: list[tuple[float, float]] | None = None
    right_hand: list[tuple[float, float]] | None = None
    recognized: bool = True
    phone_in_hand: bool = False
    cup_near_mouth: bool = False
    near_pillbox: bool = False

    @field_validator("face")
    @classmethod
    def _face(cls, v):
        if v is not None and len(v) < 468:
            raise ValueError("face must have 468 points")
        return v

    @field_validator("left_hand", "right_hand")
    @classmethod
    def _hand(cls, v):
        if v is not None and len(v) != 21:
            raise ValueError("a hand must have 21 points")
        return v

    def to_frame(self) -> HolisticFrame:
        t = lambda pts: tuple(tuple(p) for p in pts) if pts else None  # noqa: E731
        return HolisticFrame(self.ts, t(self.face), t(self.left_hand), t(self.right_hand), self.recognized,
                             self.phone_in_hand, self.cup_near_mouth, self.near_pillbox)


class DetectionItem(BaseModel):
    label: str = Field(min_length=1, max_length=40)
    score: float = Field(ge=0, le=1)
    box: Box


class DetectionFrameIn(BaseModel):
    ts: float
    camera_index: int = Field(0, ge=0, le=16)
    detections: list[DetectionItem] = Field(default_factory=list, max_length=200)
    person_boxes: list[Box] = Field(default_factory=list, max_length=20)
    scanned_regions: list[str] | None = None

    def to_frame(self) -> DetectionFrame:
        return DetectionFrame(self.ts, self.camera_index, tuple(DetectionIn(d.label, d.score, d.box) for d in self.detections),
                              tuple(self.person_boxes), tuple(self.scanned_regions) if self.scanned_regions is not None else None)


class Batch(BaseModel):
    observations: list[ObservationIn] = Field(default_factory=list, max_length=600)
    frames: list[HolisticIn] = Field(default_factory=list, max_length=600)
    detections: list[DetectionFrameIn] = Field(default_factory=list, max_length=300)


# ------------------------------------------------------------------ service


class BackendAIService:
    """One brain for the whole web process, guarded by a lock; ticks once a second."""

    def __init__(self, db, clock=time.time) -> None:
        self.store = AICoreStore(db)
        self.brain = AICoreBrain(self.store, clock=clock, speak_to=self.store.enqueue_speech)
        self.clock = clock
        self.lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_scan = 0.0

    def start(self) -> None:
        if not self.store.ready():
            log.warning("AI tables missing; AI backend ticker not started")
            return
        self._thread = threading.Thread(target=self._run, name="AIBackendTicker", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _run(self) -> None:
        while not self._stop.wait(1.0):
            try:
                self.tick()
            except Exception:
                log.exception("AI backend tick failed")

    def tick(self) -> None:
        now = self.clock()
        if now - self._last_scan >= 30:
            self._last_scan = now
            start = datetime.fromtimestamp(now - 2 * 3600, UTC).isoformat()
            end = datetime.fromtimestamp(now + 3600, UTC).isoformat()
            with self.lock:
                for pid in self.store.patients_with_doses(start, end):
                    self.brain.session(pid)  # medication timers need a session even without a camera
            self.store.purge_commands(datetime.fromtimestamp(now - 86400, UTC).isoformat())
        with self.lock:
            self.brain.tick(now)

    def ingest(self, pid: str, batch: Batch) -> dict:
        with self.lock:
            for o in sorted(batch.observations, key=lambda o: o.ts):
                if o.recognized:
                    self.brain.on_observation(pid, o.to_observation())
            for f in sorted(batch.frames, key=lambda f: f.ts):
                self.brain.on_holistic(pid, f.to_frame())
            for d in sorted(batch.detections, key=lambda d: d.ts):
                self.brain.on_detections(pid, d.to_frame())
            return {"accepted": {"observations": len(batch.observations), "frames": len(batch.frames),
                                 "detections": len(batch.detections)},
                    "medication_monitoring": self.brain.medication_active(pid)}

    def voice(self, pid: str, text: str) -> str | None:
        with self.lock:
            return self.brain.handle_transcript(pid, text)

    def commands(self, pid: str, after: int) -> dict:
        with self.lock:
            self.brain.session(pid)
            active = self.brain.medication_active(pid)
            pillbox = self.brain.pillbox_box(pid)
        return {"commands": self.store.commands_after(pid, after),
                "monitoring": {"holistic": active, "pillbox_box": pillbox}}


_service: BackendAIService | None = None


def get_service(db) -> BackendAIService:
    global _service
    if _service is None:
        _service = BackendAIService(db)
    return _service


def set_service(service: BackendAIService | None) -> None:
    global _service
    _service = service


# ------------------------------------------------------------------ routing


def _device_keys() -> dict[str, set[str]]:
    keys: dict[str, set[str]] = {}
    for part in (os.getenv("AICARE_DEVICE_KEYS") or "").split(","):
        if ":" not in part:
            continue
        key, patients = part.split(":", 1)
        if len(key.strip()) >= 16:
            keys[key.strip()] = {p.strip() for p in patients.split("|") if p.strip()}
    return keys


def _authorized(headers: dict | None, patient_id: str) -> bool:
    supplied = ""
    for k, v in (headers or {}).items():
        if k.lower() == "x-device-key":
            supplied = v or ""
    if not supplied:
        return False
    for key, patients in _device_keys().items():
        if hmac.compare_digest(key, supplied):
            return "*" in patients or patient_id in patients
    return False


def handle_device_route(db, method: str, path: str, query: dict, body: dict | None, headers: dict | None):
    parts = path.strip("/").split("/")  # api device patients {id} {action} [{track_id}]
    if len(parts) not in (5, 6) or parts[:3] != ["api", "device", "patients"]:
        return 404, {"error": "Endpoint không tồn tại"}
    pid, action = parts[3], parts[4]
    extra = parts[5] if len(parts) == 6 else None
    if not _authorized(headers, pid):
        return 401, {"error": "Thiếu hoặc sai X-Device-Key cho bệnh nhân này"}
    if not db.fetchone("SELECT 1 FROM patients WHERE patient_id=?", (pid,)):
        return 404, {"error": "Không tìm thấy bệnh nhân"}
    service = get_service(db)
    body = body or {}
    try:
        if method == "POST" and action == "observations":
            return 200, service.ingest(pid, Batch(observations=body.get("observations", [])))
        if method == "POST" and action == "holistic":
            return 200, service.ingest(pid, Batch(frames=body.get("frames", [])))
        if method == "POST" and action == "detections":
            return 200, service.ingest(pid, Batch(detections=body.get("frames", [])))
        if method == "POST" and action == "voice":
            text = str(body.get("text") or "")[:300]
            return 200, {"reply": service.voice(pid, text) if text else None}
        if method == "GET" and action == "snapshot" and extra is None:
            from shared.ai_runtime.replication import snapshot_for_device

            return 200, snapshot_for_device(db, pid)
        if method == "POST" and action == "sync" and extra is None:
            from shared.ai_runtime.replication import apply_from_device

            return 200, apply_from_device(db, pid, body)
        if method == "GET" and action == "music" and extra:
            from features.music_care.family_music_service import FamilyMusicService

            music = FamilyMusicService(db)
            row = music.get_track(pid, extra)
            if not row or not music.audio_path(row).is_file():
                return 404, {"error": "Không tìm thấy tệp nhạc"}
            return 200, {"_audio_path": str(music.audio_path(row)), "content_type": row["content_type"]}
        if extra is not None:
            return 404, {"error": "Endpoint không tồn tại"}
        if method == "GET" and action == "commands":
            raw = query.get("after", "0")
            raw = raw[0] if isinstance(raw, list) else raw
            return 200, service.commands(pid, int(raw or 0))
    except ValidationError as exc:
        return 422, {"error": "Dữ liệu không hợp lệ", "detail": exc.errors(include_url=False, include_context=False)[:5]}
    except ValueError:
        return 400, {"error": "Tham số không hợp lệ"}
    return 404, {"error": "Endpoint không tồn tại"}
