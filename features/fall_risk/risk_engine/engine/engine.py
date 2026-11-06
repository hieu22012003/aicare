"""FallRiskEngine: falls (F1–F3), check-in & escalation, dizziness signs (C1–C4).

Pure and deterministic like feature 3's engine: time comes from observation timestamps
or an explicit ``now``; no I/O. One engine per patient, fed the same observations as
feature 3 through the shared /v1/vision ingest.

Design rules carried over from AICareDemo's audited pipeline:
* never conclude a fall because the person disappeared (rule 1);
* a fall needs an observed upright -> low transition with fast hip descent (F1),
  otherwise a sustained low posture outside bed/sofa zones (F2);
* one event per episode: no new event until the person has been upright >= recovery_seconds.
"""

from __future__ import annotations

import math
import statistics
import uuid
from collections import Counter, deque
from collections.abc import Callable, Iterable
from dataclasses import dataclass, replace
from typing import Union

from features.fall_risk.risk_engine.domain import (
    SAFETY_LABEL_VI,
    Checkin,
    CheckinResult,
    EventStatus,
    FallConfig,
    FallEvent,
    FallKind,
    FallLabel,
    RiskSignal,
    SafetyState,
    SignalCode,
)
from shared.vision.pose.geometry import BodyFeatures, compute_features
from shared.vision.pose.observation import Identity, Observation
from shared.vision.zones import REST_ZONES, Zone, has_rest_zone, zone_at
from features.posture_activity.domain import Posture
from features.posture_activity.engine.classifier import classify
from features.posture_activity.engine.timeutil import local_date, local_dt

PROMPTS = {
    1: "Bác có sao không? Nếu bác ổn, bác nói 'tôi ổn' hoặc bấm nút.",
    2: "Bác ơi, bác có nghe không? Nếu cần giúp, bác nói 'cứu tôi'.",
}
MAX_CHECKINS = 2
HISTORY_SEC = 12.0
CLIP_PRE_SEC = 10.0
CLIP_POST_SEC = 20.0
CLIP_MIN_INTERVAL = 0.09  # ~10 frames per second in stored clips
MISSING_AFTER_SEC = 5.0
LOW_FORGET_SEC = 30.0
SIGNAL_DEDUP_SEC = 60.0
WALK_SPEED = 0.25  # torso lengths per second


# ---------------------------------------------------------------- events


@dataclass(frozen=True, slots=True)
class SafetyChanged:
    patient_id: str
    snapshot: dict


@dataclass(frozen=True, slots=True)
class FallEventChanged:
    event: FallEvent
    change: str  # created | rechecking | checkin | escalated | long_lie | stood_up | user_ok | self_recovered | acknowledged | labeled


@dataclass(frozen=True, slots=True)
class CheckinRequested:
    patient_id: str
    event_id: str
    attempt: int
    prompt: str
    ts: float


@dataclass(frozen=True, slots=True)
class RiskSignalDetected:
    signal: RiskSignal


@dataclass(frozen=True, slots=True)
class SkeletonClipReady:
    patient_id: str
    event_id: str
    frames: list


@dataclass(frozen=True, slots=True)
class GaitBaselineUpdated:
    patient_id: str
    speed: float
    bouts: int


FallEngineEvent = Union[
    SafetyChanged, FallEventChanged, CheckinRequested, RiskSignalDetected, SkeletonClipReady, GaitBaselineUpdated
]


@dataclass(slots=True)
class _Frame:
    ts: float
    label: str  # upright | low | sitting | other
    hip_x: float  # pixels
    hip_y: float
    torso: float
    hip_norm: tuple[float, float]
    source: str  # pose | box
    feats: BodyFeatures | None = None


_POSTURE_TO_LABEL = {
    Posture.STANDING: "upright",
    Posture.BENDING: "upright",
    Posture.LYING: "low",
    Posture.SITTING: "sitting",
}


def _copy(ev: FallEvent) -> FallEvent:
    return replace(ev, checkins=[replace(c) for c in ev.checkins])


class FallRiskEngine:
    def __init__(
        self,
        patient_id: str,
        config: FallConfig | None = None,
        zones: Iterable[Zone] = (),
        id_factory: Callable[[], str] | None = None,
    ) -> None:
        self.patient_id = patient_id
        self.config = config or FallConfig()
        self.zones: list[Zone] = list(zones)
        self._new_id = id_factory or (lambda: uuid.uuid4().hex)

        self._last_ts: float | None = None
        self._last_obs_ts: float | None = None
        self._history: deque[_Frame] = deque()
        self._labels: deque[tuple[float, str]] = deque()
        self._stable: str | None = None
        self._last_upright: _Frame | None = None

        self._suspected_at: float | None = None
        self._suspect_last_low: float | None = None
        self._low_since: float | None = None
        self._low_last_seen: float | None = None

        self._event: FallEvent | None = None  # event of the current episode
        self._episode_open = False
        self._upright_since: float | None = None
        self._waiting: dict[str, FallEvent] = {}  # escalated events whose episode ended, awaiting ack

        self._clip_pre: deque[dict] = deque()
        self._clip_collect: tuple[str, list, float] | None = None
        self._night_exit_at: float | None = None

        self._sway: deque[tuple[float, float, float]] = deque()
        self._bout: list[tuple[float, float, float, float | None]] | None = None
        self._baseline_speed: float | None = None
        self._baseline_bouts = 0
        self._sit_ref: _Frame | None = None
        self._sit_checked = False
        self._stand_start: float | None = None
        self._quick_resits: deque[float] = deque()
        self._signal_last: dict[SignalCode, float] = {}
        self._alerts_by_day: dict[str, int] = {}
        self._last_alert_ts: float | None = None
        self._last_state_key: tuple | None = None

    # ------------------------------------------------------------ configuration

    @property
    def tz(self) -> str:
        return self.config.timezone

    def set_config(self, config: FallConfig) -> None:
        self.config = config

    def set_zones(self, zones: Iterable[Zone]) -> None:
        self.zones = list(zones)

    def note_night_bed_exit(self, ts: float) -> None:
        """From feature 3: the person left the bed at night -> more sensitive F2 for a while."""
        self._night_exit_at = ts

    def restore(self, waiting: Iterable[FallEvent] = (), gait_speed: float | None = None, gait_bouts: int = 0) -> None:
        for ev in waiting:
            if ev.is_open:
                self._waiting[ev.event_id] = _copy(ev)
        self._baseline_speed = gait_speed
        self._baseline_bouts = gait_bouts

    # ------------------------------------------------------------ queries

    @property
    def last_ts(self) -> float | None:
        return self._last_ts

    @property
    def open_events(self) -> list[FallEvent]:
        events = [_copy(e) for e in self._waiting.values()]
        if self._event is not None and self._event.is_open:
            events.append(_copy(self._event))
        return sorted(events, key=lambda e: e.detected_at)

    def get_event(self, event_id: str) -> FallEvent | None:
        if self._event is not None and self._event.event_id == event_id:
            return _copy(self._event)
        ev = self._waiting.get(event_id)
        return _copy(ev) if ev else None

    def state(self, now: float | None = None) -> SafetyState:
        now = now if now is not None else (self._last_ts or 0.0)
        if self._episode_open and self._event is not None:
            return SafetyState.EVENT_ACTIVE
        if self._last_obs_ts is None or now - self._last_obs_ts > MISSING_AFTER_SEC:
            return SafetyState.UNOBSERVED
        if self._suspected_at is not None:
            return SafetyState.SUSPECTED
        if self._low_since is not None:
            return SafetyState.LOW_UNCONFIRMED
        return SafetyState.NORMAL

    def snapshot(self, now: float | None = None) -> dict:
        now = now if now is not None else (self._last_ts or 0.0)
        state = self.state(now)
        return {
            "patient_id": self.patient_id,
            "state": state.value,
            "state_label": SAFETY_LABEL_VI[state],
            "active_event_id": self._event.event_id if self._episode_open and self._event else None,
            "open_event_ids": [e.event_id for e in self.open_events],
            "last_seen": self._last_obs_ts,
            "updated_at": now,
        }

    # ------------------------------------------------------------ inputs

    def ingest(self, obs: Observation) -> list[FallEngineEvent]:
        if self._last_ts is not None and obs.ts < self._last_ts - 1.0:
            return []
        now = obs.ts if self._last_ts is None else max(obs.ts, self._last_ts)
        self._last_ts = now

        frame = self._frame(obs)
        self._record_clip(now, obs, frame)
        events: list[FallEngineEvent] = []
        if frame is None:
            events += self._on_missing(now)
        else:
            self._last_obs_ts = now
            self._history.append(frame)
            while self._history and now - self._history[0].ts > HISTORY_SEC:
                self._history.popleft()
            stable_before = self._stable
            self._update_stable(now, frame.label)
            events += self._fall_logic(now, frame, obs.identity)
            if obs.identity == Identity.PATIENT and self.config.dizziness_enabled and not self._episode_open:
                events += self._dizziness(now, frame, stable_before)
        events += self._timers(now)
        events += self._flush_clip(now)
        events += self._state_event(now)
        return events

    def tick(self, now: float) -> list[FallEngineEvent]:
        if self._last_ts is None or now < self._last_ts:
            return []
        self._last_ts = now
        events: list[FallEngineEvent] = []
        if self._last_obs_ts is not None and now - self._last_obs_ts > MISSING_AFTER_SEC:
            events += self._on_missing(now)
        events += self._timers(now)
        events += self._flush_clip(now)
        events += self._state_event(now)
        return events

    def checkin(self, event_id: str, result: CheckinResult, now: float, transcript: str | None = None) -> list[FallEngineEvent]:
        ev = self._find_open(event_id)
        pending = next((c for c in reversed(ev.checkins) if c.result is None), None)
        if pending is not None:
            pending.result, pending.transcript = result, transcript
        else:
            ev.checkins.append(Checkin(now, ev.attempts, result, transcript))
        ev.updated_at = now
        if result == CheckinResult.HELP:
            if ev.status == EventStatus.CHECKING:
                return self._escalate(ev, now, "help")
            return [FallEventChanged(_copy(ev), "checkin")]
        if result == CheckinResult.NO_RESPONSE:
            if ev.status == EventStatus.CHECKING:
                return self._next_checkin_or_escalate(ev, now)
            return [FallEventChanged(_copy(ev), "checkin")]
        if ev.status == EventStatus.CHECKING:
            status = EventStatus.USER_OK if result == CheckinResult.OK else EventStatus.SELF_RECOVERED
            return self._close(ev, status, now, "elder" if result == CheckinResult.OK else "auto")
        if result == CheckinResult.OK:
            ev.caregiver_message = (ev.caregiver_message or ev.message) + " Người thân đã trả lời: ổn."
        return [FallEventChanged(_copy(ev), "checkin")]

    def acknowledge(
        self, event_id: str, by: str, now: float, label: FallLabel | None = None, note: str | None = None
    ) -> list[FallEngineEvent]:
        ev = None
        if self._event is not None and self._event.event_id == event_id:
            ev = self._event
        elif event_id in self._waiting:
            ev = self._waiting[event_id]
        if ev is None:
            raise KeyError(event_id)
        if label is not None:
            ev.label, ev.label_by = label, by
        if note:
            ev.note = note
        if ev.is_open:
            events = self._close(ev, EventStatus.ACKNOWLEDGED, now, by)
            self._waiting.pop(event_id, None)
            return events
        ev.updated_at = now
        return [FallEventChanged(_copy(ev), "labeled")]

    # ------------------------------------------------------------ frames

    def _frame(self, obs: Observation) -> _Frame | None:
        w, h = max(1, obs.image_width), max(1, obs.image_height)
        f = compute_features(obs) if obs.landmarks else None
        if f is not None:
            label = _POSTURE_TO_LABEL.get(classify(f), "other")
            return _Frame(obs.ts, label, f.hip_px[0], f.hip_px[1], f.torso_px, f.hip_norm, "pose", f)
        if obs.person_boxes:
            x, y, bw, bh, _ = max(obs.person_boxes, key=lambda b: b[2] * b[3] * b[4])
            wpx, hpx = bw * w, bh * h
            if wpx < 4 or hpx < 4:
                return None
            if wpx / hpx >= 1.3:
                label, torso, cy = "low", wpx / 3.3, y + bh * 0.5
            elif hpx / wpx >= 1.8:
                label, torso, cy = "upright", hpx / 3.3, y + bh * 0.55
            else:
                label, torso, cy = "other", max(wpx, hpx) / 3.3, y + bh * 0.55
            cx = x + bw * 0.5
            return _Frame(obs.ts, label, cx * w, cy * h, torso, (cx, cy), "box")
        return None

    def _update_stable(self, now: float, label: str) -> None:
        self._labels.append((now, label))
        while len(self._labels) > 5 or (self._labels and now - self._labels[0][0] > 1.5):
            self._labels.popleft()
        top, n = Counter(lbl for _, lbl in self._labels).most_common(1)[0]
        if n >= 3:
            self._stable = top

    def _in_rest_zone(self, frame: _Frame) -> bool:
        zone = zone_at(frame.hip_norm, self.zones)
        return zone is not None and zone.zone_type in REST_ZONES

    # ------------------------------------------------------------ falls

    def _fall_logic(self, now: float, fr: _Frame, identity: Identity) -> list[FallEngineEvent]:
        cfg = self.config
        events: list[FallEngineEvent] = []
        if fr.label == "upright":
            self._last_upright = fr
            self._suspected_at = None
            self._low_since = None
            if self._upright_since is None:
                self._upright_since = now
            if self._episode_open and now - self._upright_since >= cfg.recovery_seconds:
                events += self._recovered(now)
            return events

        self._upright_since = None
        if fr.label != "low":
            if self._suspected_at is not None and now - (self._suspect_last_low or now) > 0.5:
                self._suspected_at = None
            if self._low_since is not None and now - (self._low_last_seen or now) > 3.0:
                self._low_since = None
            return events

        # ---- low frame
        if self._episode_open:
            ev = self._event
            if ev is not None and not ev.long_lie and now - ev.detected_at >= cfg.long_lie_seconds:
                ev.long_lie = True
                ev.updated_at = now
                base = ev.caregiver_message or ev.message
                ev.caregiver_message = base + " Vẫn đang ở tư thế thấp sau hơn 1 phút."
                if ev.status == EventStatus.CHECKING:
                    return self._escalate(ev, now, "long_lie")
                events.append(FallEventChanged(_copy(ev), "long_lie"))
            return events

        if self._in_rest_zone(fr):
            # Lying down on a bed or sofa, even quickly, is rest, not a fall.
            self._suspected_at = None
            self._low_since = None
            return events

        up = self._last_upright
        if self._suspected_at is None and up is not None and now - up.ts <= cfg.transition_window_seconds:
            drop = (fr.hip_y - up.hip_y) / max(up.torso, 1.0)
            speed = drop / max(now - up.ts, 1e-3)
            if drop >= cfg.min_drop and speed >= cfg.fall_descent_speed:
                self._suspected_at = now
        if self._suspected_at is not None:
            self._suspect_last_low = now
            if now - self._suspected_at >= cfg.low_confirm_seconds:
                suspected = self._suspected_at
                self._suspected_at = None
                return self._open_event(FallKind.F1_FALL, now, suspected, identity)
            return events

        if not has_rest_zone(self.zones):
            return events  # F2 needs bed/sofa zones to tell resting from lying on the floor
        if self._low_since is None:
            self._low_since = now
        self._low_last_seen = now
        threshold = cfg.floor_seconds
        if self._night_exit_at is not None and now - self._night_exit_at <= cfg.night_exit_sensitive_minutes * 60:
            threshold = cfg.floor_seconds_after_night_exit
        if now - self._low_since >= threshold:
            since = self._low_since
            self._low_since = None
            return self._open_event(FallKind.F2_ON_FLOOR, now, since, identity)
        return events

    def _on_missing(self, now: float) -> list[FallEngineEvent]:
        # Rule 1: disappearing is never evidence of a fall; drop the pending suspicion.
        self._suspected_at = None
        if self._low_since is not None and now - (self._low_last_seen or now) > LOW_FORGET_SEC:
            self._low_since = None
        return []

    def _open_event(self, kind: FallKind, now: float, suspected_at: float, identity: Identity) -> list[FallEngineEvent]:
        cfg = self.config
        hhmm = local_dt(suspected_at, self.tz).strftime("%H:%M")
        who = "" if identity == Identity.PATIENT else " (người chưa xác định)"
        message = (
            f"Phát hiện dấu hiệu ngã lúc {hhmm}{who}. Đang hỏi lại."
            if kind == FallKind.F1_FALL
            else f"Phát hiện người nằm dưới sàn lúc {hhmm}{who}, không thấy lúc ngã. Đang hỏi lại."
        )
        ev = FallEvent(
            event_id=self._new_id(),
            patient_id=self.patient_id,
            kind=kind,
            status=EventStatus.CHECKING,
            detected_at=now,
            suspected_at=suspected_at,
            identity=identity.value,
            trial=not cfg.alerts_enabled,
            message=message,
            attempts=1,
            next_action_at=now + cfg.checkin_wait_seconds,
            checkins=[Checkin(now, 1)],
            updated_at=now,
        )
        self._event = ev
        self._episode_open = True
        self._upright_since = None
        self._low_since = None
        self._clip_collect = (ev.event_id, list(self._clip_pre), now + CLIP_POST_SEC)
        return [FallEventChanged(_copy(ev), "created"), CheckinRequested(self.patient_id, ev.event_id, 1, PROMPTS[1], now)]

    def _timers(self, now: float) -> list[FallEngineEvent]:
        ev = self._event
        if ev is None or ev.status != EventStatus.CHECKING or ev.next_action_at is None or now < ev.next_action_at:
            return []
        pending = next((c for c in reversed(ev.checkins) if c.result is None), None)
        if pending is not None:
            pending.result = CheckinResult.NO_RESPONSE
        return self._next_checkin_or_escalate(ev, now)

    def _next_checkin_or_escalate(self, ev: FallEvent, now: float) -> list[FallEngineEvent]:
        if ev.attempts < MAX_CHECKINS:
            ev.attempts += 1
            ev.next_action_at = now + self.config.second_checkin_wait_seconds
            ev.checkins.append(Checkin(now, ev.attempts))
            ev.updated_at = now
            return [
                FallEventChanged(_copy(ev), "rechecking"),
                CheckinRequested(self.patient_id, ev.event_id, ev.attempts, PROMPTS[ev.attempts], now),
            ]
        return self._escalate(ev, now, "no_response")

    def _escalate(self, ev: FallEvent, now: float, reason: str) -> list[FallEngineEvent]:
        hhmm = local_dt(ev.suspected_at, self.tz).strftime("%H:%M")
        what = "Nghi ngã" if ev.kind == FallKind.F1_FALL else "Nằm dưới sàn"
        reasons = {
            "help": f"{what} lúc {hhmm}, người thân kêu cứu.",
            "no_response": f"{what} lúc {hhmm}, người thân không trả lời sau {MAX_CHECKINS} lần hỏi.",
            "long_lie": f"{what} lúc {hhmm}, vẫn ở tư thế thấp sau hơn 1 phút.",
        }
        ev.status = EventStatus.ESCALATED
        ev.escalated_at = now
        ev.next_action_at = None
        ev.caregiver_message = reasons.get(reason, ev.message)
        ev.updated_at = now
        return [FallEventChanged(_copy(ev), "escalated")]

    def _close(self, ev: FallEvent, status: EventStatus, now: float, by: str) -> list[FallEngineEvent]:
        ev.status = status
        ev.closed_at = now
        ev.closed_by = by
        ev.next_action_at = None
        ev.updated_at = now
        return [FallEventChanged(_copy(ev), status.value.lower())]

    def _recovered(self, now: float) -> list[FallEngineEvent]:
        self._episode_open = False
        ev = self._event
        self._event = None
        if ev is None:
            return []
        ev.stood_up_at = now
        if ev.status == EventStatus.CHECKING:
            ev.checkins.append(Checkin(now, ev.attempts, CheckinResult.STOOD_UP))
            return self._close(ev, EventStatus.SELF_RECOVERED, now, "auto")
        if ev.status == EventStatus.ESCALATED:
            self._waiting[ev.event_id] = ev
            ev.updated_at = now
            return [FallEventChanged(_copy(ev), "stood_up")]
        return []

    def _find_open(self, event_id: str) -> FallEvent:
        if self._event is not None and self._event.event_id == event_id and self._event.is_open:
            return self._event
        if event_id in self._waiting:
            return self._waiting[event_id]
        raise KeyError(event_id)

    # ------------------------------------------------------------ skeleton clips

    def _record_clip(self, now: float, obs: Observation, fr: _Frame | None) -> None:
        entry = {
            "t": round(now, 3),
            "label": fr.label if fr else None,
            "lm": [[round(p.x, 3), round(p.y, 3), round(p.visibility, 2)] for p in obs.landmarks]
            if obs.landmarks
            else None,
            "box": [round(v, 3) for v in max(obs.person_boxes, key=lambda b: b[2] * b[3])[:4]]
            if obs.person_boxes
            else None,
        }
        if not self._clip_pre or now - self._clip_pre[-1]["t"] >= CLIP_MIN_INTERVAL:
            self._clip_pre.append(entry)
            while self._clip_pre and now - self._clip_pre[0]["t"] > CLIP_PRE_SEC:
                self._clip_pre.popleft()
            if self._clip_collect is not None:
                self._clip_collect[1].append(entry)

    def _flush_clip(self, now: float) -> list[FallEngineEvent]:
        if self._clip_collect is None or now < self._clip_collect[2]:
            return []
        event_id, frames, _ = self._clip_collect
        self._clip_collect = None
        return [SkeletonClipReady(self.patient_id, event_id, frames)]

    # ------------------------------------------------------------ dizziness

    def _hip_speed(self, now: float, fr: _Frame) -> float:
        ref = next((h for h in self._history if now - h.ts <= 1.0), None)
        if ref is None or now - ref.ts < 0.3:
            return 0.0
        return math.hypot(fr.hip_x - ref.hip_x, fr.hip_y - ref.hip_y) / max(fr.torso, 1.0) / (now - ref.ts)

    def _dizziness(self, now: float, fr: _Frame, stable_before: str | None) -> list[FallEngineEvent]:
        cfg = self.config
        events: list[FallEngineEvent] = []
        speed = self._hip_speed(now, fr) if fr.source == "pose" else 0.0
        walking = fr.label == "upright" and speed >= WALK_SPEED

        # C1: sway while standing still
        if fr.label == "upright" and not walking and fr.feats is not None and fr.feats.shoulder_px is not None:
            self._sway.append((now, fr.feats.shoulder_px[0], fr.torso))
            while self._sway and now - self._sway[0][0] > cfg.sway_window_seconds:
                self._sway.popleft()
            span = now - self._sway[0][0]
            if span >= cfg.sway_window_seconds * 0.9 and len(self._sway) >= 5:
                xs = [s[1] for s in self._sway]
                torso = statistics.median(s[2] for s in self._sway)
                amplitude = (max(xs) - min(xs)) / max(torso, 1.0)
                if amplitude >= cfg.sway_threshold:
                    events += self._signal(
                        SignalCode.C1_SWAY, now, amplitude, cfg.sway_threshold,
                        f"Thân người lắc mạnh khi đứng (biên độ {amplitude:.2f} lần chiều dài thân).",
                    )
                    self._sway.clear()
        else:
            self._sway.clear()

        # C2: walking bouts vs personal baseline
        if walking:
            sep = None
            f = fr.feats
            if f is not None and f.ankle_left_px and f.ankle_right_px:
                sep = abs(f.ankle_left_px[0] - f.ankle_right_px[0]) / max(fr.torso, 1.0)
            if self._bout is None:
                self._bout = []
            self._bout.append((now, fr.hip_x, fr.torso, sep))
        elif self._bout is not None:
            events += self._end_bout()

        # C3: hard sit-down (uncontrolled descent into sitting)
        if fr.label == "upright":
            self._sit_ref, self._sit_checked = fr, False
        elif fr.label == "sitting" and self._sit_ref is not None and not self._sit_checked:
            dt = now - self._sit_ref.ts
            if dt > 1.5:
                self._sit_checked = True
            elif dt > 0:
                drop = (fr.hip_y - self._sit_ref.hip_y) / max(self._sit_ref.torso, 1.0)
                rate = drop / dt
                if drop >= 0.15 and rate >= cfg.hard_sit_speed:
                    self._sit_checked = True
                    events += self._signal(
                        SignalCode.C3_HARD_SIT, now, rate, cfg.hard_sit_speed,
                        "Ngồi sụp xuống nhanh, không kiểm soát.",
                    )

        # C4: stands up then sits straight back down, repeatedly
        stable = self._stable
        if stable != stable_before:
            if stable == "upright" and stable_before == "sitting":
                self._stand_start = now
            elif stable == "sitting" and stable_before == "upright" and self._stand_start is not None:
                if now - self._stand_start < cfg.quick_resit_seconds:
                    self._quick_resits.append(now)
                self._stand_start = None
        while self._quick_resits and now - self._quick_resits[0] > cfg.quick_resit_window_minutes * 60:
            self._quick_resits.popleft()
        if len(self._quick_resits) >= cfg.quick_resit_count:
            events += self._signal(
                SignalCode.C4_QUICK_RESIT, now, float(len(self._quick_resits)), float(cfg.quick_resit_count),
                f"Đứng lên rồi ngồi xuống ngay {len(self._quick_resits)} lần trong {int(cfg.quick_resit_window_minutes)} phút.",
            )
            self._quick_resits.clear()
        return events

    def _end_bout(self) -> list[FallEngineEvent]:
        cfg = self.config
        bout, self._bout = self._bout or [], None
        if len(bout) < 5 or bout[-1][0] - bout[0][0] < 3.0:
            return []
        duration = bout[-1][0] - bout[0][0]
        torso = statistics.median(b[2] for b in bout)
        path = sum(abs(b[1] - a[1]) for a, b in zip(bout, bout[1:]))
        speed = path / max(torso, 1.0) / duration

        cv = None
        seps = [(b[0], b[3]) for b in bout if b[3] is not None]
        if len(seps) >= 8:
            values = [s for _, s in seps]
            mean = statistics.fmean(values)
            peaks = [
                seps[i][0]
                for i in range(1, len(seps) - 1)
                if values[i] > mean and values[i] >= values[i - 1] and values[i] > values[i + 1]
            ]
            intervals = [b - a for a, b in zip(peaks, peaks[1:])]
            if len(intervals) >= 4 and statistics.fmean(intervals) > 0:
                cv = statistics.pstdev(intervals) / statistics.fmean(intervals)

        end = bout[-1][0]
        if self._baseline_speed is not None and self._baseline_bouts >= cfg.gait_min_bouts_for_baseline:
            ratio = speed / self._baseline_speed if self._baseline_speed > 0 else 1.0
            if ratio < cfg.gait_slow_ratio:
                return self._signal(
                    SignalCode.C2_GAIT, end, ratio, cfg.gait_slow_ratio,
                    f"Đi chậm hơn bình thường ({ratio * 100:.0f}% so với mức thường ngày).",
                )
        if cv is not None and cv >= cfg.gait_irregular_cv:
            return self._signal(
                SignalCode.C2_GAIT, end, cv, cfg.gait_irregular_cv, f"Nhịp bước không đều (độ lệch {cv:.2f})."
            )
        self._baseline_speed = speed if self._baseline_speed is None else 0.8 * self._baseline_speed + 0.2 * speed
        self._baseline_bouts += 1
        return [GaitBaselineUpdated(self.patient_id, round(self._baseline_speed, 4), self._baseline_bouts)]

    def _signal(self, code: SignalCode, now: float, value: float, threshold: float, message: str) -> list[FallEngineEvent]:
        cfg = self.config
        last = self._signal_last.get(code)
        if last is not None and now - last < SIGNAL_DEDUP_SEC:
            return []
        self._signal_last[code] = now
        day = local_date(now, self.tz)
        alerted = (
            self._alerts_by_day.get(day, 0) < cfg.max_signal_alerts_per_day
            and (self._last_alert_ts is None or now - self._last_alert_ts >= cfg.signal_cooldown_minutes * 60)
        )
        if alerted:
            self._alerts_by_day[day] = self._alerts_by_day.get(day, 0) + 1
            self._last_alert_ts = now
        signal = RiskSignal(self._new_id(), self.patient_id, code, now, round(value, 4), threshold, alerted, message)
        return [RiskSignalDetected(signal)]

    # ------------------------------------------------------------ state

    def _state_event(self, now: float) -> list[FallEngineEvent]:
        snap = self.snapshot(now)
        key = (snap["state"], snap["active_event_id"], tuple(snap["open_event_ids"]))
        if key == self._last_state_key:
            return []
        self._last_state_key = key
        return [SafetyChanged(self.patient_id, snap)]
