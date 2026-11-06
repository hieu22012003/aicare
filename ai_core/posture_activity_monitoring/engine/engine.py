"""PostureEngine: per-patient posture tracking, segmentation, daily stats and reminder rules.

The engine is pure and synchronous. It never reads the clock: time always comes from
observation timestamps (``ingest``) or an explicit ``now`` (``tick``, actions), which keeps it
deterministic and easy to test. All persistence and networking live in the async service.

Pipeline for each observation:
    landmarks -> geometry features -> per-frame label (+ walking from motion)
    -> majority-vote smoothing (BR-01) -> 10 s commit delay (BR-02) -> segments
    -> streaks / day summary (BR-04) -> reminder rules R1–R6 with escalation.
"""

from __future__ import annotations

import math
import uuid
from collections import Counter, deque
from collections.abc import Callable, Iterable
from dataclasses import dataclass, replace
from typing import Union

from ai_core.posture_activity_monitoring.domain import (
    Audience,
    CurrentState,
    DaySummary,
    Identity,
    Location,
    Observation,
    Posture,
    Reminder,
    ReminderStatus,
    RuleCode,
    RulesConfig,
    Segment,
    Zone,
    summary_key,
)
from ai_core.posture_activity_monitoring.engine.classifier import DEFAULT_THRESHOLDS, ClassifierThresholds, classify
from ai_core.posture_activity_monitoring.engine.geometry import BodyFeatures, compute_features
from ai_core.posture_activity_monitoring.engine.smoother import MajorityVoteSmoother
from ai_core.posture_activity_monitoring.engine.timeutil import (
    at_local_time,
    in_window,
    local_date,
    local_dt,
    split_by_local_day,
)
from ai_core.posture_activity_monitoring.engine.zones import locate


@dataclass(frozen=True, slots=True)
class EngineParams:
    min_visibility: float = 0.5
    smoothing_window_sec: float = 3.0
    switch_share: float = 0.7
    min_votes: int = 3
    min_segment_sec: float = 10.0  # BR-02: a new posture must hold this long to become a segment
    stitch_gap_sec: float = 120.0  # BR-03: losing sight for less than this does not break a segment
    walk_window_sec: float = 2.0
    walk_min_span_sec: float = 0.8
    walk_speed: float = 0.25  # hip displacement in torso lengths per second
    walk_scale_rate: float = 0.15  # |d ln(torso size)| per second: walking toward/away from camera
    reposition_deg: float = 30.0
    reposition_shift: float = 0.5  # torso lengths
    reposition_hold_sec: float = 10.0
    out_of_order_tolerance_sec: float = 1.0
    observed_timeout_sec: float = 5.0


# ---------------------------------------------------------------- events


@dataclass(frozen=True, slots=True)
class StateChanged:
    state: CurrentState


@dataclass(frozen=True, slots=True)
class SegmentClosed:
    segment: Segment


@dataclass(frozen=True, slots=True)
class DaySummaryUpdated:
    patient_id: str
    summary: DaySummary


@dataclass(frozen=True, slots=True)
class ReminderChanged:
    reminder: Reminder
    change: str  # created | redelivered | snoozed | escalated | auto_resolved | acknowledged | expired


@dataclass(frozen=True, slots=True)
class NightBedExit:
    patient_id: str
    ts: float


EngineEvent = Union[StateChanged, SegmentClosed, DaySummaryUpdated, ReminderChanged, NightBedExit]

ESCALATING_RULES = frozenset({RuleCode.R1_SITTING, RuleCode.R2_LYING_DAY})
CAREGIVER_RULES = frozenset({RuleCode.R3_REPOSITION, RuleCode.R5_NIGHT_BED_EXIT, RuleCode.R6_ABSENCE})
MAX_DELIVERY_ATTEMPTS = 2


@dataclass(slots=True)
class _OpenSegment:
    posture: Posture
    location: Location
    started_at: float
    conf_sum: float = 0.0
    conf_n: int = 0

    def add_confidence(self, value: float) -> None:
        self.conf_sum += value
        self.conf_n += 1

    @property
    def confidence(self) -> float:
        return round(self.conf_sum / self.conf_n, 3) if self.conf_n else 0.0


def _hhmm(ts: float, tz_name: str) -> str:
    return local_dt(ts, tz_name).strftime("%H:%M")


class PostureEngine:
    def __init__(
        self,
        patient_id: str,
        config: RulesConfig | None = None,
        zones: Iterable[Zone] = (),
        thresholds: ClassifierThresholds = DEFAULT_THRESHOLDS,
        params: EngineParams | None = None,
        id_factory: Callable[[], str] | None = None,
    ) -> None:
        self.patient_id = patient_id
        self.config = config or RulesConfig()
        self.zones: list[Zone] = list(zones)
        self.thresholds = thresholds
        self.params = params or EngineParams()
        self._new_id = id_factory or (lambda: uuid.uuid4().hex)

        p = self.params
        self._smoother: MajorityVoteSmoother[Posture] = MajorityVoteSmoother(
            p.smoothing_window_sec, p.switch_share, p.min_votes
        )
        self._walk_hist: deque[tuple[float, float, float, float]] = deque()
        self._loc_votes: deque[tuple[float, Posture, Location]] = deque()
        self._smoothed: Posture | None = None
        self._smoothed_location: Location = Location.UNKNOWN

        self._committed: _OpenSegment | None = None
        self._pending: tuple[Posture, Location, float] | None = None
        self._reappear_ts: float | None = None
        self._last_ts: float | None = None
        self._last_valid_ts: float | None = None
        self._started = False  # becomes True on the first ingested observation

        self._days: dict[str, DaySummary] = {}
        self._dirty_days: set[str] = set()

        self._sit_start: float | None = None
        self._sit_break: float | None = None
        self._lie_start: float | None = None
        self._lie_break: float | None = None

        self._repo_ref: tuple[float, tuple[float, float], float] | None = None
        self._repo_since: float | None = None
        self._last_reposition_at: float | None = None

        self._in_bed_night = False
        self._night_exit_at: float | None = None
        self._night_exit_alerted_for: float | None = None
        self._absence_alerted_for: float | None = None

        self._reminders: dict[str, Reminder] = {}
        self._last_closed: dict[RuleCode, float] = {}
        self._elder_count: dict[str, int] = {}
        self._r4_done: set[str] = set()
        self._last_state_key: tuple | None = None
        self._hold_until: float | None = None  # set by feature 4 while a fall event is open

    # ------------------------------------------------------------ configuration

    @property
    def tz(self) -> str:
        return self.config.timezone

    def set_config(self, config: RulesConfig) -> None:
        self.config = config

    def set_zones(self, zones: Iterable[Zone]) -> None:
        self.zones = list(zones)

    def hold_reminders(self, until: float | None) -> None:
        """Pause R1/R2 (e.g. while a fall event is open); ``None`` releases the hold."""
        self._hold_until = until

    def restore(
        self,
        day_summaries: Iterable[DaySummary] = (),
        open_reminders: Iterable[Reminder] = (),
        elder_reminders_today: int = 0,
        r4_done_today: bool = False,
        today: str | None = None,
    ) -> None:
        """Reload persisted state after a restart (the open segment itself is not restored)."""
        for summary in day_summaries:
            self._days[summary.local_date] = summary.copy()
        for reminder in open_reminders:
            if reminder.is_open:
                self._reminders[reminder.reminder_id] = replace(reminder)
        if today:
            self._elder_count[today] = elder_reminders_today
            if r4_done_today:
                self._r4_done.add(today)

    # ------------------------------------------------------------ queries

    @property
    def last_ts(self) -> float | None:
        return self._last_ts

    @property
    def open_reminders(self) -> list[Reminder]:
        return sorted((replace(r) for r in self._reminders.values()), key=lambda r: r.triggered_at)

    def current_state(self, now: float | None = None) -> CurrentState:
        now = now if now is not None else (self._last_ts or 0.0)
        c = self._committed
        if c is not None and c.posture == Posture.UNOBSERVED:
            posture, location = Posture.UNOBSERVED, Location.UNKNOWN
        elif self._smoothed is not None:
            posture, location = self._smoothed, self._smoothed_location
        elif c is not None:
            posture, location = c.posture, c.location
        else:
            posture, location = Posture.UNOBSERVED, Location.UNKNOWN

        since: float | None = None
        if c is not None and (c.posture, c.location) == (posture, location):
            since = c.started_at
        elif self._pending is not None and self._pending[:2] == (posture, location):
            since = self._pending[2]

        observed = (
            posture != Posture.UNOBSERVED
            and self._last_valid_ts is not None
            and now - self._last_valid_ts <= self.params.observed_timeout_sec
        )
        streak = 0.0
        if self._sit_start is not None and self._sit_break is None:
            streak = max(0.0, now - self._sit_start)
        return CurrentState(
            patient_id=self.patient_id,
            posture=posture,
            location=location,
            since=since,
            observed=observed,
            confidence=round(self._smoother.share, 3) if posture != Posture.UNOBSERVED else 0.0,
            last_seen=self._last_valid_ts,
            sitting_streak_seconds=round(streak, 1),
            updated_at=now,
        )

    def day_summary(self, day: str, now: float | None = None) -> DaySummary:
        """Summary for a local day including the still-open segment up to ``now``."""
        summary = self._days.get(day, DaySummary(day)).copy()
        now = now if now is not None else self._last_ts
        c = self._committed
        if c is not None and now is not None and now > c.started_at:
            key = summary_key(c.posture, c.location)
            for chunk_day, seconds in split_by_local_day(c.started_at, now, self.tz):
                if chunk_day == day:
                    summary.add(key, seconds)
        if self._sit_start is not None and now is not None:
            end = self._sit_break if self._sit_break is not None else now
            if local_date(end, self.tz) == day:
                summary.longest_sitting_sec = max(summary.longest_sitting_sec, end - self._sit_start)
        return summary

    def knows_day(self, day: str) -> bool:
        return day in self._days

    def open_segment(self, now: float | None = None) -> Segment | None:
        """The segment still in progress, ending at ``now`` (not persisted yet)."""
        c = self._committed
        now = now if now is not None else self._last_ts
        if c is None or now is None or now <= c.started_at:
            return None
        return Segment("open", self.patient_id, c.posture, c.location, c.started_at, now, c.confidence)

    # ------------------------------------------------------------ inputs

    def ingest(self, obs: Observation) -> list[EngineEvent]:
        p = self.params
        if self._last_ts is not None and obs.ts < self._last_ts - p.out_of_order_tolerance_sec:
            return []
        now = obs.ts if self._last_ts is None else max(obs.ts, self._last_ts)
        self._last_ts = now
        self._started = True

        features = None
        if obs.identity == Identity.PATIENT:
            features = compute_features(obs, p.min_visibility)

        events: list[EngineEvent] = []
        if features is None:
            events += self._handle_no_observation(now)
        else:
            events += self._handle_observation(now, features)
        events += self._evaluate_rules(now)
        events += self._flush_days()
        events += self._state_event(now)
        return events

    def tick(self, now: float) -> list[EngineEvent]:
        """Advance time without a new observation (absence detection, timed rules)."""
        if not self._started or (self._last_ts is not None and now < self._last_ts):
            return []  # no camera data for this patient yet: nothing to infer
        self._last_ts = now
        events = self._handle_no_observation(now)
        events += self._evaluate_rules(now)
        events += self._flush_days()
        events += self._state_event(now)
        return events

    def add_exercise(self, started_at: float, ended_at: float) -> list[EngineEvent]:
        """Count a structured exercise session (e.g. F9 rehab) into the 'Vận động' bucket."""
        if ended_at <= started_at:
            raise ValueError("ended_at must be after started_at")
        for day, seconds in split_by_local_day(started_at, ended_at, self.tz):
            self._day(day).exercise_seconds += seconds
        events = self._evaluate_rules(self._last_ts or ended_at)
        return events + self._flush_days()

    def snooze(self, reminder_id: str, now: float) -> list[EngineEvent]:
        r = self._reminders.get(reminder_id)
        if r is None:
            raise KeyError(reminder_id)
        if r.audience != Audience.ELDER or r.status != ReminderStatus.DELIVERED:
            raise ValueError("Chỉ hoãn được lời nhắc đang hiển thị cho người cao tuổi")
        if r.snoozes >= self.config.max_snoozes:
            raise ValueError("Đã hết số lần hoãn cho lời nhắc này")
        r.snoozes += 1
        r.status = ReminderStatus.SNOOZED
        r.snoozed_until = now + self.config.snooze_minutes * 60
        r.next_action_at = r.snoozed_until
        r.updated_at = now
        return [ReminderChanged(replace(r), "snoozed")]

    def acknowledge(self, reminder_id: str, by: str, now: float) -> list[EngineEvent]:
        r = self._reminders.get(reminder_id)
        if r is None:
            raise KeyError(reminder_id)
        return self._close(r, ReminderStatus.ACKNOWLEDGED, now, by)

    def close(self, now: float | None = None) -> list[EngineEvent]:
        """Flush the open segment (graceful shutdown)."""
        c = self._committed
        events: list[EngineEvent] = []
        if c is not None:
            end = now if now is not None else (self._last_ts or c.started_at)
            if c.posture != Posture.UNOBSERVED and self._last_valid_ts is not None:
                end = min(end, max(self._last_valid_ts, c.started_at))
            events += self._emit_segment(c, end)
            self._committed = None
        return events + self._flush_days()

    # ------------------------------------------------------------ observation handling

    def _handle_observation(self, now: float, f: BodyFeatures) -> list[EngineEvent]:
        label = classify(f, self.thresholds)
        if label == Posture.STANDING:
            if self._is_moving(now, f):
                label = Posture.WALKING
        elif label is not None:
            self._walk_hist.clear()

        if self._committed is not None and self._committed.posture == Posture.UNOBSERVED and self._reappear_ts is None:
            self._reappear_ts = now
        self._last_valid_ts = now

        if label is not None:
            self._loc_votes.append((now, label, locate(f.hip_norm, label, self.zones)))
        while self._loc_votes and now - self._loc_votes[0][0] > self.params.smoothing_window_sec:
            self._loc_votes.popleft()

        smoothed = self._smoother.update(now, label)
        if smoothed is None:
            return []
        locations = Counter(loc for _, posture, loc in self._loc_votes if posture == smoothed)
        location = locations.most_common(1)[0][0] if locations else (
            self._committed.location if self._committed and self._committed.posture == smoothed else Location.UNKNOWN
        )
        self._smoothed, self._smoothed_location = smoothed, location

        if smoothed == Posture.LYING:
            self._track_reposition(now, f)
        else:
            self._repo_ref, self._repo_since = None, None
        return self._commit(now, smoothed, location)

    def _handle_no_observation(self, now: float) -> list[EngineEvent]:
        c = self._committed
        if c is None:
            # Nobody seen yet: absence time starts counting now.
            self._committed = _OpenSegment(Posture.UNOBSERVED, Location.UNKNOWN, now)
            return self._on_commit(None, Posture.UNOBSERVED, Location.UNKNOWN, now)
        if c.posture == Posture.UNOBSERVED:
            return []
        last_seen = self._last_valid_ts if self._last_valid_ts is not None else c.started_at
        if now - last_seen <= self.params.stitch_gap_sec:
            return []  # BR-03: short occlusion, keep the current segment
        events = self._switch(Posture.UNOBSERVED, Location.UNKNOWN, max(last_seen, c.started_at))
        self._smoother.reset()
        self._smoothed = None
        self._walk_hist.clear()
        self._loc_votes.clear()
        self._pending = None
        self._reappear_ts = None
        return events

    def _is_moving(self, now: float, f: BodyFeatures) -> bool:
        p = self.params
        hist = self._walk_hist
        hist.append((now, f.hip_px[0], f.hip_px[1], f.torso_px))
        while hist and now - hist[0][0] > p.walk_window_sec:
            hist.popleft()
        t0, x0, y0, s0 = hist[0]
        dt = now - t0
        if dt < p.walk_min_span_sec:
            return False
        torso = max(1.0, (s0 + f.torso_px) / 2)
        speed = math.hypot(f.hip_px[0] - x0, f.hip_px[1] - y0) / torso / dt
        scale_rate = abs(math.log(max(f.torso_px, 1.0) / max(s0, 1.0))) / dt
        return speed >= p.walk_speed or scale_rate >= p.walk_scale_rate

    def _track_reposition(self, now: float, f: BodyFeatures) -> None:
        p = self.params
        current = (f.torso_orientation, f.hip_px, f.torso_px)
        if self._repo_ref is None:
            self._repo_ref, self._repo_since = current, None
            return
        ref_angle, ref_hip, ref_torso = self._repo_ref
        turn = abs((f.torso_orientation - ref_angle + 180.0) % 360.0 - 180.0)
        shift = math.hypot(f.hip_px[0] - ref_hip[0], f.hip_px[1] - ref_hip[1]) / max(ref_torso, 1.0)
        if turn >= p.reposition_deg or shift >= p.reposition_shift:
            if self._repo_since is None:
                self._repo_since = now
            elif now - self._repo_since >= p.reposition_hold_sec:
                self._repo_ref, self._repo_since = current, None
                self._last_reposition_at = now
        else:
            self._repo_since = None

    # ------------------------------------------------------------ segments

    def _commit(self, now: float, posture: Posture, location: Location) -> list[EngineEvent]:
        c = self._committed
        if c is None:
            self._committed = _OpenSegment(posture, location, now)
            self._committed.add_confidence(self._smoother.share)
            return self._on_commit(None, posture, location, now)
        if (c.posture, c.location) == (posture, location):
            self._pending = None
            c.add_confidence(self._smoother.share)
            return []
        if c.posture == Posture.UNOBSERVED:
            at = self._reappear_ts if self._reappear_ts is not None else now
            self._reappear_ts = None
            return self._switch(posture, location, at)
        if self._pending is None or self._pending[:2] != (posture, location):
            self._pending = (posture, location, now)
        if now - self._pending[2] >= self.params.min_segment_sec:
            at = self._pending[2]
            self._pending = None
            return self._switch(posture, location, at)
        return []

    def _switch(self, posture: Posture, location: Location, at: float) -> list[EngineEvent]:
        prev = self._committed
        events: list[EngineEvent] = []
        if prev is not None:
            at = max(at, prev.started_at)
            events += self._emit_segment(prev, at)
        self._committed = _OpenSegment(posture, location, at)
        self._committed.add_confidence(self._smoother.share)
        events += self._on_commit(prev, posture, location, at)
        return events

    def _emit_segment(self, seg: _OpenSegment, end: float) -> list[EngineEvent]:
        if end <= seg.started_at:
            return []
        segment = Segment(
            segment_id=self._new_id(),
            patient_id=self.patient_id,
            posture=seg.posture,
            location=seg.location,
            started_at=seg.started_at,
            ended_at=end,
            confidence=seg.confidence,
        )
        key = summary_key(seg.posture, seg.location)
        for day, seconds in split_by_local_day(segment.started_at, segment.ended_at, self.tz):
            self._day(day).add(key, seconds)
        return [SegmentClosed(segment)]

    def _on_commit(
        self, prev: _OpenSegment | None, posture: Posture, location: Location, at: float
    ) -> list[EngineEvent]:
        events: list[EngineEvent] = []
        prev_posture = prev.posture if prev is not None else None

        if prev_posture == Posture.SITTING and posture in (Posture.STANDING, Posture.WALKING):
            self._day(local_date(at, self.tz)).sit_to_stand_count += 1

        if posture == Posture.SITTING:
            if self._sit_start is None:
                self._sit_start = at
            self._sit_break = None
        elif self._sit_start is not None and self._sit_break is None:
            self._sit_break = at

        if posture == Posture.LYING:
            if self._lie_start is None:
                self._lie_start = at
                self._last_reposition_at = at
            self._lie_break = None
        elif self._lie_start is not None and self._lie_break is None:
            self._lie_break = at

        sleeping = in_window(at, self.config.sleep_start, self.config.sleep_end, self.tz)
        if posture == Posture.LYING and location != Location.FLOOR:
            if sleeping:
                self._in_bed_night = True
            self._night_exit_at = None
        elif posture in (Posture.STANDING, Posture.WALKING) and self._in_bed_night and sleeping:
            self._in_bed_night = False
            self._night_exit_at = at
            self._day(local_date(at, self.tz)).night_bed_exits += 1
            events.append(NightBedExit(self.patient_id, at))
        return events

    def _end_streaks(self, now: float) -> None:
        brk = self.config.break_seconds
        if self._sit_break is not None and self._sit_start is not None and now - self._sit_break >= brk:
            day = self._day(local_date(self._sit_break, self.tz))
            day.longest_sitting_sec = max(day.longest_sitting_sec, self._sit_break - self._sit_start)
            self._sit_start = self._sit_break = None
        if self._lie_break is not None and now - self._lie_break >= brk:
            self._lie_start = self._lie_break = None
            self._last_reposition_at = None

    # ------------------------------------------------------------ rules

    def _evaluate_rules(self, now: float) -> list[EngineEvent]:
        cfg = self.config
        events: list[EngineEvent] = []
        sleeping = in_window(now, cfg.sleep_start, cfg.sleep_end, self.tz)
        c = self._committed
        current = c.posture if c is not None else None
        held = self._hold_until is not None and now < self._hold_until

        self._end_streaks(now)
        if sleeping and current == Posture.LYING and c is not None and c.location != Location.FLOOR:
            self._in_bed_night = True
        if not sleeping:
            self._in_bed_night = False
            self._night_exit_at = None

        for reminder in list(self._reminders.values()):
            events += self._progress(reminder, now, sleeping)

        if (
            cfg.enabled(RuleCode.R1_SITTING)
            and not sleeping
            and not held
            and current == Posture.SITTING
            and self._sit_start is not None
            and now - self._sit_start >= cfg.sitting_minutes * 60
        ):
            minutes = int((now - self._sit_start) // 60)
            events += self._create(
                RuleCode.R1_SITTING,
                now,
                "Bác đã ngồi khá lâu rồi. Mình đứng dậy đi lại vài phút cho khỏe nhé.",
                context_minutes=minutes,
            )

        if (
            cfg.enabled(RuleCode.R2_LYING_DAY)
            and not sleeping
            and not held
            and current == Posture.LYING
            and self._lie_start is not None
            and now - self._lie_start >= cfg.lying_day_minutes * 60
        ):
            minutes = int((now - self._lie_start) // 60)
            events += self._create(
                RuleCode.R2_LYING_DAY,
                now,
                "Bác nằm nghỉ cũng lâu rồi. Nếu thấy khỏe, bác ngồi dậy vận động nhẹ một chút nhé.",
                context_minutes=minutes,
            )

        if (
            cfg.enabled(RuleCode.R3_REPOSITION)
            and current == Posture.LYING
            and self._last_reposition_at is not None
            and now - self._last_reposition_at >= cfg.reposition_minutes * 60
        ):
            minutes = int((now - self._last_reposition_at) // 60)
            events += self._create(
                RuleCode.R3_REPOSITION,
                now,
                f"Người thân đã nằm gần như không đổi tư thế {minutes} phút. Vui lòng hỗ trợ trở mình.",
            )

        if cfg.enabled(RuleCode.R4_LOW_ACTIVITY) and not sleeping:
            day = local_date(now, self.tz)
            if day not in self._r4_done and now >= at_local_time(now, cfg.activity_check_time, self.tz):
                self._r4_done.add(day)
                summary = self.day_summary(day, now)
                active_minutes = int(summary.active_seconds // 60)
                # A day the camera barely saw says nothing about activity: no reminder.
                if summary.observed_seconds >= cfg.activity_min_observed_minutes * 60 and active_minutes < cfg.activity_goal_minutes:
                    events += self._create(
                        RuleCode.R4_LOW_ACTIVITY,
                        now,
                        f"Hôm nay bác mới vận động khoảng {active_minutes} phút. "
                        "Mình tập vài động tác nhẹ cho khỏe nhé?",
                    )

        if (
            cfg.enabled(RuleCode.R5_NIGHT_BED_EXIT)
            and self._night_exit_at is not None
            and self._night_exit_alerted_for != self._night_exit_at
            and now - self._night_exit_at >= cfg.night_return_minutes * 60
        ):
            created = self._create(
                RuleCode.R5_NIGHT_BED_EXIT,
                now,
                f"Người thân rời giường lúc {_hhmm(self._night_exit_at, self.tz)} "
                f"và chưa quay lại sau {int(cfg.night_return_minutes)} phút.",
            )
            if created:
                self._night_exit_alerted_for = self._night_exit_at
            events += created

        if (
            cfg.enabled(RuleCode.R6_ABSENCE)
            and not sleeping
            and c is not None
            and current == Posture.UNOBSERVED
            and self._absence_alerted_for != c.started_at
            and now - c.started_at >= cfg.absence_hours * 3600
        ):
            hours = round((now - c.started_at) / 3600, 1)
            created = self._create(
                RuleCode.R6_ABSENCE,
                now,
                f"Không quan sát thấy người thân trong {hours:g} giờ. "
                "Có thể đã ra ngoài hoặc camera bị lệch.",
            )
            if created:
                self._absence_alerted_for = c.started_at
            events += created
        return events

    def _condition_cleared(self, r: Reminder, now: float) -> bool:
        current = self._committed.posture if self._committed is not None else None
        if r.rule == RuleCode.R1_SITTING:
            return self._sit_start is None or self._sit_start > r.triggered_at
        if r.rule == RuleCode.R2_LYING_DAY:
            return self._lie_start is None or self._lie_start > r.triggered_at
        if r.rule == RuleCode.R3_REPOSITION:
            return current != Posture.LYING or (self._last_reposition_at or 0.0) > r.triggered_at
        if r.rule == RuleCode.R4_LOW_ACTIVITY:
            day = local_date(r.triggered_at, self.tz)
            return self.day_summary(day, now).active_seconds >= self.config.activity_goal_minutes * 60
        if r.rule == RuleCode.R5_NIGHT_BED_EXIT:
            return self._night_exit_at is None
        if r.rule == RuleCode.R6_ABSENCE:
            return current != Posture.UNOBSERVED
        return False

    def _progress(self, r: Reminder, now: float, sleeping: bool) -> list[EngineEvent]:
        if self._condition_cleared(r, now):
            return self._close(r, ReminderStatus.AUTO_RESOLVED, now, "auto")
        if sleeping and r.audience == Audience.ELDER and r.status in (ReminderStatus.DELIVERED, ReminderStatus.SNOOZED):
            return self._close(r, ReminderStatus.EXPIRED, now, "system")
        if r.next_action_at is None or now < r.next_action_at:
            return []
        window = self.config.response_window_minutes * 60
        if r.status == ReminderStatus.SNOOZED:
            r.status = ReminderStatus.DELIVERED
            r.snoozed_until = None
            r.next_action_at = now + window
            r.updated_at = now
            return [ReminderChanged(replace(r), "redelivered")]
        if r.status == ReminderStatus.DELIVERED and r.rule in ESCALATING_RULES:
            if r.attempt < MAX_DELIVERY_ATTEMPTS:
                r.attempt += 1
                r.next_action_at = now + window
                r.updated_at = now
                return [ReminderChanged(replace(r), "redelivered")]
            r.status = ReminderStatus.ESCALATED
            r.escalated_at = now
            r.next_action_at = None
            r.updated_at = now
            what = "ngồi" if r.rule == RuleCode.R1_SITTING else "nằm"
            start = self._sit_start if r.rule == RuleCode.R1_SITTING else self._lie_start
            minutes = int((now - start) // 60) if start is not None else 0
            r.caregiver_message = (
                f"Đã nhắc {MAX_DELIVERY_ATTEMPTS} lần nhưng người thân vẫn {what} liên tục khoảng {minutes} phút."
            )
            return [ReminderChanged(replace(r), "escalated")]
        r.next_action_at = None
        return []

    def _create(self, rule: RuleCode, now: float, message: str, context_minutes: int | None = None) -> list[EngineEvent]:
        cfg = self.config
        if any(r.rule == rule for r in self._reminders.values()):
            return []
        last = self._last_closed.get(rule)
        if last is not None and now - last < cfg.cooldown_minutes * 60:
            return []
        audience = Audience.CAREGIVER if rule in CAREGIVER_RULES else Audience.ELDER
        day = local_date(now, self.tz)
        if audience == Audience.ELDER:
            if self._elder_count.get(day, 0) >= cfg.max_reminders_per_day:
                return []
            self._elder_count[day] = self._elder_count.get(day, 0) + 1
        reminder = Reminder(
            reminder_id=self._new_id(),
            patient_id=self.patient_id,
            rule=rule,
            audience=audience,
            status=ReminderStatus.DELIVERED if audience == Audience.ELDER else ReminderStatus.ESCALATED,
            triggered_at=now,
            message=message,
            next_action_at=now + cfg.response_window_minutes * 60 if rule in ESCALATING_RULES else None,
            escalated_at=now if audience == Audience.CAREGIVER else None,
            caregiver_message=message if audience == Audience.CAREGIVER else None,
            updated_at=now,
        )
        self._reminders[reminder.reminder_id] = reminder
        return [ReminderChanged(replace(reminder), "created")]

    def _close(self, r: Reminder, status: ReminderStatus, now: float, by: str) -> list[EngineEvent]:
        r.status = status
        r.resolved_at = now
        r.resolved_by = by
        r.next_action_at = None
        r.updated_at = now
        self._reminders.pop(r.reminder_id, None)
        self._last_closed[r.rule] = now
        return [ReminderChanged(replace(r), status.value.lower())]

    # ------------------------------------------------------------ helpers

    def _day(self, day: str) -> DaySummary:
        self._dirty_days.add(day)
        if day not in self._days:
            self._days[day] = DaySummary(day)
        return self._days[day]

    def _flush_days(self) -> list[EngineEvent]:
        events = [DaySummaryUpdated(self.patient_id, self._days[d].copy()) for d in sorted(self._dirty_days)]
        self._dirty_days.clear()
        return events

    def _state_event(self, now: float) -> list[EngineEvent]:
        state = self.current_state(now)
        key = (state.posture, state.location, state.observed, state.since)
        if key == self._last_state_key:
            return []
        self._last_state_key = key
        return [StateChanged(state)]
