"""AICoreBrain: runs the four AI features for each patient and turns their events into actions.

No camera, threads or Qt here (that is runtime.py), so it is unit-testable:
    on_observation(pid, obs)      pose landmarks -> posture (3), fall risk (4), wrists for objects (7)
    on_holistic(pid, frame)       face + hands during a medication window -> intake detector
    on_detections(pid, frame)     object detector output -> object memory (7)
    tick(now, home_patient)       timers: posture rules, fall check-ins, medication milestones
    handle_transcript(pid, text)  voice: fall check-in replies, "kính của tôi đâu?"
Actions: TTS through ``speak``, rows + outbox events through AICoreStore (portal SSE),
urgent caregiver requests through the existing voice-request service (fall, non-trial only).
"""

from __future__ import annotations

import logging
import re
from collections import deque
import time
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Callable

from pydantic import BaseModel, Field, ValidationError

from features.fall_risk.risk_engine.domain import CheckinResult, FallConfig, SafetyState
from features.fall_risk.risk_engine.engine import (
    CheckinRequested,
    FallEventChanged,
    FallRiskEngine,
    GaitBaselineUpdated,
    RiskSignalDetected,
    SafetyChanged,
)
from shared.ai_runtime.store import AICoreStore, epoch
from features.medication.intake_monitoring.detector import IntakeDetector
from features.medication.intake_monitoring.domain import HolisticFrame, IntakeConfig, IntakeVerdict
from features.medication.intake_monitoring.policy import DoseAction, MonitorPolicy, due_actions, skipped_speech
from features.object_finder.domain import DetectionFrame, ObjectConfig, Region, RegisteredItem, SightingStatus
from features.object_finder.engine import ObjectMemory, SightingChanged
from features.posture_activity.domain import Audience, RulesConfig, Zone
from features.posture_activity.engine.engine import (
    NightBedExit,
    PostureEngine,
    ReminderChanged,
    SegmentClosed,
    StateChanged,
)
from shared.vision.pose.observation import Observation

log = logging.getLogger("AICore")
UTC = timezone.utc
TERMINAL = ("CONFIRMED", "SKIPPED", "MISSED", "CANCELLED")
FOREVER = 10**12


class PostureSettings(BaseModel):
    rules: RulesConfig = Field(default_factory=RulesConfig)
    zones: list[Zone] = Field(default_factory=list, max_length=20)


class FallSettings(BaseModel):
    config: FallConfig = Field(default_factory=FallConfig)  # alerts_enabled=True by default; False = trial mode
    gait: dict = Field(default_factory=dict)


class ObjectSettings(BaseModel):
    config: ObjectConfig = Field(default_factory=ObjectConfig)
    regions: list[Region] = Field(default_factory=list, max_length=30)
    items: list[dict] = Field(default_factory=list, max_length=50)


class MedicationSettings(BaseModel):
    enabled: bool = True
    camera_confirms: bool = Field(True, description="VERIFIED intake marks the dose CONFIRMED (attributed to the camera)")
    policy: MonitorPolicy = Field(default_factory=MonitorPolicy)
    intake: IntakeConfig = Field(default_factory=IntakeConfig)
    pillbox_region_id: str | None = Field(None, description="object-finder region where the pills are kept")
    honorific: str = Field("bác", max_length=20)


SETTINGS = {"posture": PostureSettings, "fall": FallSettings, "objects": ObjectSettings, "medication": MedicationSettings}


def load_settings(store: AICoreStore, patient_id: str, feature: str):
    model = SETTINGS[feature]
    try:
        return model.model_validate(store.get_settings(patient_id, feature))
    except ValidationError:
        log.warning("Invalid %s settings for %s, using defaults", feature, patient_id)
        return model()


@dataclass
class _Session:
    patient_id: str
    posture: PostureEngine
    fall: FallRiskEngine
    objects: ObjectMemory
    intake: IntakeDetector
    med: MedicationSettings
    fall_trial: bool
    episode_active: bool = False
    checkin_event: str | None = None
    active_doses: set = field(default_factory=set)
    last_seen: float = 0.0
    loaded_at: float = 0.0
    confirmed_at: float = 0.0  # last dose confirmed by the camera (on-camera "đã ghi nhận" message)


def _norm(text: str) -> str:
    text = unicodedata.normalize("NFC", text).lower().strip()
    return re.sub(r"[.!?…,]+", " ", text).strip()


OK_WORDS = ("tôi ổn", "tôi không sao", "không sao", "tôi vẫn ổn", "ổn rồi")
HELP_WORDS = ("cứu", "giúp tôi", "đau quá", "không dậy được", "không đứng dậy được")
FIND_PATTERNS = (
    r"(?:đâu|ở đâu|để đâu|nằm đâu)(?: rồi| nhỉ| vậy| thế)?$",
    r"^(?:tìm giúp|tìm hộ|tìm dùm|tìm)\b",
    r"^(?:có )?(?:thấy|nhìn thấy) .+ (?:đâu )?không$",
)


def is_find_object(text: str) -> bool:
    t = _norm(text)
    return any(re.search(p, t) for p in FIND_PATTERNS)


class AICoreBrain:
    SETTINGS_REFRESH_SECONDS = 30.0

    def __init__(self, store: AICoreStore, speak: Callable[[str], None] | None = None,
                 clock: Callable[[], float] = time.time,
                 speak_to: Callable[[str, str], None] | None = None) -> None:
        """speak(text): local speaker (desktop app). speak_to(patient_id, text): per-patient (cloud backend)."""
        self.store = store
        self.speak = speak or (lambda text: None)
        self.speak_to = speak_to
        self.clock = clock
        self.sessions: dict[str, _Session] = {}
        # Patients whose dose time just arrived (the home app brings the camera page forward). Bounded: the
        # cloud backend never reads it.
        self.due_events: deque = deque(maxlen=20)

    def _say(self, patient_id: str, text: str) -> None:
        if self.speak_to is not None:
            self.speak_to(patient_id, text)
        else:
            self.speak(text)

    # ------------------------------------------------------------ sessions

    def session(self, patient_id: str) -> _Session:
        s = self.sessions.get(patient_id)
        now = self.clock()
        if s is None:
            s = self._new_session(patient_id)
            self.sessions[patient_id] = s
        elif now - s.loaded_at > self.SETTINGS_REFRESH_SECONDS:
            self._apply_settings(s)
        return s

    def _new_session(self, pid: str) -> _Session:
        p = load_settings(self.store, pid, "posture")
        f = load_settings(self.store, pid, "fall")
        o = load_settings(self.store, pid, "objects")
        m = load_settings(self.store, pid, "medication")
        fall = FallRiskEngine(pid, config=f.config, zones=p.zones)
        if f.gait:
            fall.restore(gait_speed=f.gait.get("speed"), gait_bouts=int(f.gait.get("bouts", 0)))
        objects = ObjectMemory(pid, config=o.config, regions=o.regions, items=self._items(pid, o))
        since = (datetime.now(UTC) - timedelta(days=30)).isoformat()
        objects.restore(self.store.sightings(pid), self.store.place_counts(pid, since))
        s = _Session(pid, PostureEngine(pid, config=p.rules, zones=p.zones), fall, objects, IntakeDetector(m.intake), m,
                     fall_trial=not f.config.alerts_enabled, loaded_at=self.clock())
        return s

    @staticmethod
    def _items(pid: str, o: ObjectSettings) -> list[RegisteredItem]:
        out = []
        for i in o.items:
            try:
                out.append(RegisteredItem(i["item_id"], pid, i["type_key"], i["name"], i.get("owner"), list(i.get("synonyms", [])),
                                          list(i.get("embeddings", [])), i.get("embedder_id"), float(i.get("created_at", 0))))
            except (KeyError, TypeError, ValueError):
                continue
        return out

    def _apply_settings(self, s: _Session) -> None:
        pid = s.patient_id
        p = load_settings(self.store, pid, "posture")
        f = load_settings(self.store, pid, "fall")
        o = load_settings(self.store, pid, "objects")
        m = load_settings(self.store, pid, "medication")
        s.posture.set_config(p.rules)
        s.posture.set_zones(p.zones)
        s.fall.set_config(f.config)
        s.fall.set_zones(p.zones)
        s.fall_trial = not f.config.alerts_enabled
        s.objects.set_config(o.config)
        s.objects.set_regions(o.regions)
        for item in self._items(pid, o):
            s.objects.upsert_item(item)
        if m.intake != s.med.intake:
            s.intake.config = m.intake
        s.med = m
        s.loaded_at = self.clock()

    # ------------------------------------------------------------ inputs

    def on_observation(self, pid: str, obs: Observation) -> None:
        s = self.session(pid)
        if obs.landmarks is not None:
            s.last_seen = obs.ts
        self._posture(s, s.posture.ingest(obs))
        self._fall(s, s.fall.ingest(obs))
        if obs.landmarks:
            from shared.vision.pose.landmarks import PoseLandmark

            wrists = [(lm.x, lm.y) for lm in (obs.landmarks[PoseLandmark.LEFT_WRIST], obs.landmarks[PoseLandmark.RIGHT_WRIST])
                      if lm.visibility >= 0.5]
            if wrists:
                s.objects.note_wrists(obs.ts, obs.camera_index, wrists)
        if obs.person_boxes:
            s.objects.note_persons(obs.ts, obs.camera_index, [b[:4] for b in obs.person_boxes])

    def on_detections(self, pid: str, frame: DetectionFrame) -> None:
        s = self.session(pid)
        for e in s.objects.ingest(frame):
            if isinstance(e, SightingChanged):
                self.store.save_sighting(e.sighting, e.change)

    def medication_active(self, pid: str) -> bool:
        s = self.sessions.get(pid)
        return bool(s and s.active_doses and s.med.enabled)

    def pillbox_box(self, pid: str):
        """Normalized bbox of the pill region (for near_pillbox), or None."""
        s = self.sessions.get(pid)
        if not s or not s.med.pillbox_region_id:
            return None
        r = next((r for r in s.objects.regions if r.region_id == s.med.pillbox_region_id), None)
        return r.bbox if r else None

    def on_holistic(self, pid: str, frame: HolisticFrame) -> None:
        s = self.session(pid)
        if not self.medication_active(pid):
            return
        for obs in s.intake.feed(frame):
            self._intake(s, obs)

    # ------------------------------------------------------------ timers

    def tick(self, now: float | None = None, home_patient: str | None = None) -> None:
        now = self.clock() if now is None else now
        if home_patient:
            self.session(home_patient)
        for s in list(self.sessions.values()):
            self._posture(s, s.posture.tick(now))
            self._fall(s, s.fall.tick(now))
            if s.med.enabled:
                self._medication_tick(s, now)

    # ------------------------------------------------------------ posture / fall

    def _posture(self, s: _Session, events) -> None:
        for e in events:
            if isinstance(e, StateChanged):
                self.store.save_posture_state(e.state)
            elif isinstance(e, SegmentClosed):
                self.store.add_segment(e.segment)
            elif isinstance(e, ReminderChanged):
                self.store.save_reminder(e.reminder, e.change)
                if e.change in ("created", "redelivered") and e.reminder.audience == Audience.ELDER and not s.episode_active:
                    self._say(s.patient_id, e.reminder.message)
            elif isinstance(e, NightBedExit):
                s.fall.note_night_bed_exit(e.ts)

    def _fall(self, s: _Session, events) -> None:
        for e in events:
            if isinstance(e, FallEventChanged):
                self.store.save_fall_event(e.event, e.change)
                if e.change == "escalated" and not e.event.trial:
                    self._urgent_request(s, e.event)
                if not e.event.is_open and s.checkin_event == e.event.event_id:
                    s.checkin_event = None
            elif isinstance(e, CheckinRequested):
                s.checkin_event = e.event_id
                self._say(s.patient_id, e.prompt)
            elif isinstance(e, RiskSignalDetected):
                self.store.add_signal(e.signal)
            elif isinstance(e, GaitBaselineUpdated):
                f = load_settings(self.store, s.patient_id, "fall")
                f.gait = {"speed": e.speed, "bouts": e.bouts}
                self.store.set_settings(s.patient_id, "fall", f.model_dump(mode="json"), "ai_core")
            elif isinstance(e, SafetyChanged):
                active = e.snapshot.get("state") == SafetyState.EVENT_ACTIVE.value
                if active != s.episode_active:
                    s.episode_active = active
                    # A fall in progress outranks sitting/lying reminders (feature 3).
                    s.posture.hold_reminders(FOREVER if active else None)

    def _urgent_request(self, s: _Session, ev) -> None:
        """Reuse the 'cứu tôi' path: an urgent request to doctor + caregivers with email delivery."""
        try:
            from features.voice_assistant.local_rules.requests import RequestService

            service = RequestService(self.store.raw())
            action = {"intent": "urgent_help", "roles": ["doctor", "caregiver"], "priority": "urgent"}
            recipients = service.resolve(s.patient_id, action["roles"])
            decision = {"patient_id": s.patient_id, "utterance_id": "ai_fall_" + ev.event_id,
                        "original_text": ev.caregiver_message or ev.message,
                        "context_refs": {"ai_fall_event": ev.event_id}, "rule_ids": ["ai_fall_escalated"]}
            service.create(decision, action, recipients)
        except Exception:
            log.exception("Could not create urgent request for fall event %s", ev.event_id)

    # ------------------------------------------------------------ objects

    def answer_where(self, pid: str, query: str, now: float | None = None):
        s = self.session(pid)
        return s.objects.answer(query, self.clock() if now is None else now)

    # ------------------------------------------------------------ medication

    def _medication_tick(self, s: _Session, now: float) -> None:
        pol = s.med.policy
        start = datetime.fromtimestamp(now - pol.close_minutes * 60 - 60, UTC).isoformat()
        end = datetime.fromtimestamp(now + pol.open_before_minutes * 60 + 1, UTC).isoformat()
        doses = self.store.doses_in_window(s.patient_id, start, end)
        speak_due, speak_overdue = [], []
        for d in doses:
            occ = d["occurrence_id"]
            scheduled = epoch(d["scheduled_at_utc"])
            done = self.store.monitor_done(occ)
            taken = d["state"] in TERMINAL
            for a in skipped_speech(pol, scheduled, now, done):
                done.add(a.value)
            actions = due_actions(pol, scheduled, now, {DoseAction(a) for a in done if a in DoseAction._value2member_map_}, taken)
            for a in actions:
                done.add(a.value)
                event, payload = None, {}
                if a == DoseAction.OPEN:
                    s.active_doses.add(occ)
                    event = "monitoring_started"
                elif a == DoseAction.SPEAK_DUE:
                    speak_due.append(d)
                    event = "due_reminded"
                elif a == DoseAction.OVERDUE:
                    speak_overdue.append(d)
                    observed = now - s.last_seen < 120 if s.last_seen else False
                    event, payload = "not_taken", {"minutes": pol.overdue_minutes, "patient_in_view": observed}
                elif a == DoseAction.ESCALATE:
                    self.store.escalate_overdue(occ, s.patient_id)
                    event, payload = "escalated", {"minutes": pol.escalate_minutes}
                elif a == DoseAction.CLOSE:
                    s.active_doses.discard(occ)
                    event = "monitoring_closed"
                self.store.mark_monitor(occ, s.patient_id, done, event, dict(payload, medication=d["medication_name_snapshot"]))
            if not taken and d["state"] not in TERMINAL and DoseAction.OPEN.value in done and DoseAction.CLOSE.value not in done:
                s.active_doses.add(occ)
        if not s.active_doses:
            for obs in s.intake.flush(now):
                self._intake(s, obs)
        h = s.med.honorific
        if speak_due:
            names = ", ".join(sorted({d["medication_name_snapshot"] for d in speak_due}))
            where = self._pillbox_hint(s, now)
            self.due_events.append(s.patient_id)
            # Leading BEL: the speaker plays the reminder chime first (field 09/11).
            self._say(s.patient_id, f"\a{h.capitalize()} ơi, đến giờ uống thuốc {names} rồi.{where} "
                                    f"{h.capitalize()} cầm viên thuốc đưa lên miệng trước camera nhé.")
        if speak_overdue:
            in_view = bool(s.last_seen and now - s.last_seen < 120)
            extra = "" if in_view else f" Uống xong {h} bấm nút Đã uống giúp cháu nhé."
            self._say(s.patient_id, f"Đến giờ uống thuốc rồi, {h} chưa uống. {h.capitalize()} uống ngay giúp cháu nhé.{extra}")

    def _pillbox_hint(self, s: _Session, now: float) -> str:
        a = s.objects.answer("hộp thuốc", now)
        if a.status == "found" and a.sighting is not None and a.sighting.status == SightingStatus.SEEN:
            return f" Hộp thuốc ở {s.objects.place_text(a.sighting)}."
        return ""

    def _intake(self, s: _Session, obs) -> None:
        open_doses = [d for d in self._open_doses(s) if d["occurrence_id"] in s.active_doses]
        if not open_doses:
            return
        first_time = min(d["scheduled_at_utc"] for d in open_doses)
        batch = [d for d in open_doses if d["scheduled_at_utc"] == first_time]  # pills due together, taken together
        h = s.med.honorific
        if obs.verdict == IntakeVerdict.VERIFIED and s.med.camera_confirms:
            confirmed = False
            for d in batch:
                ev = self.store.add_evidence(d["occurrence_id"], s.patient_id, obs)
                confirmed |= self.store.confirm_dose_by_camera(d["occurrence_id"], s.patient_id, ev, obs.ts)
            if confirmed:
                s.confirmed_at = obs.ts
                self._say(s.patient_id, f"Cháu đã ghi nhận {h} uống thuốc rồi ạ.")
        else:
            for d in batch:
                self.store.add_evidence(d["occurrence_id"], s.patient_id, obs)
            self._say(s.patient_id, f"{h.capitalize()} vừa uống thuốc phải không ạ? Nếu đúng, {h} nói 'tôi đã uống thuốc rồi' "
                       f"hoặc bấm nút Đã uống nhé.")

    def _open_doses(self, s: _Session) -> list[dict]:
        if not s.active_doses:
            return []
        now = self.clock()
        start = datetime.fromtimestamp(now - 24 * 3600, UTC).isoformat()
        end = datetime.fromtimestamp(now + 3600, UTC).isoformat()
        return [d for d in self.store.doses_in_window(s.patient_id, start, end)
                if d["occurrence_id"] in s.active_doses and d["state"] not in TERMINAL]

    # ------------------------------------------------------------ voice

    def handle_transcript(self, pid: str, text: str, now: float | None = None) -> str | None:
        """Reply text when the AI features own this utterance, else None (existing handlers continue)."""
        now = self.clock() if now is None else now
        s = self.session(pid)
        t = _norm(text)
        if s.checkin_event:
            result = None
            if any(w in t for w in HELP_WORDS):
                result = CheckinResult.HELP
            elif any(w in t for w in OK_WORDS):
                result = CheckinResult.OK
            if result is not None:
                event_id, s.checkin_event = s.checkin_event, None
                try:
                    self._fall(s, s.fall.checkin(event_id, result, now, text))
                except KeyError:
                    return None
                return ("Cháu đã báo người nhà ngay." if result == CheckinResult.HELP
                        else "Vâng, cháu ghi nhận bác ổn. Bác đứng dậy từ từ nhé.")
        if is_find_object(text):
            answer = s.objects.answer(text, now)
            if answer.status != "unknown_item":
                return answer.text
        return None
