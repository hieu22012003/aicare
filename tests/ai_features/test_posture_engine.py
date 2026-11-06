from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from features.posture_activity.domain import (
    Audience,
    Identity,
    Location,
    Posture,
    ReminderStatus,
    RuleCode,
    RulesConfig,
    Zone,
    ZoneType,
)
from features.posture_activity.engine import (
    DaySummaryUpdated,
    NightBedExit,
    PostureEngine,
    ReminderChanged,
    SegmentClosed,
)
from tests.ai_features.synth import obs, stream

TZ = ZoneInfo("Asia/Ho_Chi_Minh")


def at(hour: int, minute: int = 0, second: int = 0, day: int = 3) -> float:
    return datetime(2026, 11, day, hour, minute, second, tzinfo=TZ).timestamp()


def segments(events):
    return [e.segment for e in events if isinstance(e, SegmentClosed)]


def reminders(events, rule=None):
    out = [e for e in events if isinstance(e, ReminderChanged)]
    return [e for e in out if rule is None or e.reminder.rule == rule]


def make_engine(**cfg) -> PostureEngine:
    return PostureEngine("P1", config=RulesConfig(**cfg))


def test_posture_changes_become_segments_after_commit_delay():
    eng = make_engine()
    ev, t = stream(eng, at(8), 30, "standing")
    ev2, t = stream(eng, t, 60, "sitting")
    ev3, t = stream(eng, t, 30, "standing")
    segs = segments(ev + ev2 + ev3)
    assert [s.posture for s in segs] == [Posture.STANDING, Posture.SITTING]
    # sitting segment is about 60 s (smoothing shifts both edges by the same ~1-2 s)
    assert 55 <= segs[1].duration <= 65
    summary = eng.day_summary("2026-11-03", t)
    assert summary.sit_to_stand_count == 1
    assert 55 <= summary.seconds["sitting"] <= 65
    assert eng.current_state(t).posture == Posture.STANDING


def test_short_change_does_not_break_segment():
    eng = make_engine()
    ev, t = stream(eng, at(8), 60, "sitting")
    ev2, t = stream(eng, t, 6, "standing")  # shorter than min_segment_sec
    ev3, t = stream(eng, t, 60, "sitting")
    assert segments(ev + ev2 + ev3) == []
    assert eng.day_summary("2026-11-03", t).sit_to_stand_count == 0


def test_front_facing_sitting_and_walking():
    eng = make_engine()
    _, t = stream(eng, at(9), 20, "sitting_front")
    assert eng.current_state(t).posture == Posture.SITTING
    _, t = stream(eng, t, 30, "standing", fps=4, walk_speed=0.1)
    assert eng.current_state(t).posture == Posture.WALKING
    ev, t = stream(eng, t, 20, "standing", fps=4)
    assert eng.current_state(t).posture == Posture.STANDING
    walking = [s for s in segments(ev) if s.posture == Posture.WALKING]
    assert walking and walking[0].duration >= 15


def test_short_occlusion_is_stitched_and_long_one_is_unobserved():
    eng = make_engine()
    _, t = stream(eng, at(10), 60, "sitting")
    ev, t = stream(eng, t, 60, None)  # 60 s nobody < stitch gap
    ev2, t = stream(eng, t, 30, "sitting")
    assert segments(ev + ev2) == []

    last_seen = t - 0.5
    ev3, t = stream(eng, t, 200, None)
    segs = segments(ev3)
    assert len(segs) == 1 and segs[0].posture == Posture.SITTING
    assert segs[0].ended_at == pytest.approx(last_seen)
    assert eng.current_state(t).posture == Posture.UNOBSERVED


def test_identity_other_is_ignored():
    eng = make_engine()
    _, t = stream(eng, at(10), 30, "sitting")
    for i in range(300):
        eng.ingest(obs(t + i, "standing", identity=Identity.OTHER))
    assert eng.current_state(t + 299).posture == Posture.UNOBSERVED


def test_r1_sitting_reminder_redelivers_then_escalates_then_resolves():
    eng = make_engine()
    ev, t = stream(eng, at(8), 46 * 60, "sitting", fps=1)
    created = reminders(ev, RuleCode.R1_SITTING)
    assert [e.change for e in created] == ["created"]
    r = created[0].reminder
    assert r.audience == Audience.ELDER and r.status == ReminderStatus.DELIVERED
    assert 45 * 60 <= r.triggered_at - at(8) <= 45 * 60 + 10

    ev, t = stream(eng, t, 21 * 60, "sitting", fps=1)
    changes = [e.change for e in reminders(ev, RuleCode.R1_SITTING)]
    assert changes == ["redelivered", "escalated"]
    escalated = reminders(ev, RuleCode.R1_SITTING)[-1].reminder
    assert escalated.status == ReminderStatus.ESCALATED and "2 lần" in escalated.caregiver_message

    ev, t = stream(eng, t, 90, "standing", fps=1)
    assert [e.change for e in reminders(ev, RuleCode.R1_SITTING)] == ["auto_resolved"]
    assert eng.open_reminders == []
    assert eng.day_summary("2026-11-03", t).longest_sitting_sec >= 66 * 60


def test_r1_snooze_then_redeliver():
    eng = make_engine()
    ev, t = stream(eng, at(8), 46 * 60, "sitting", fps=1)
    rid = reminders(ev, RuleCode.R1_SITTING)[0].reminder.reminder_id
    snoozed = eng.snooze(rid, t)
    assert snoozed[0].reminder.status == ReminderStatus.SNOOZED
    with pytest.raises(ValueError):
        eng.snooze(rid, t)  # already snoozed, not DELIVERED
    ev, t = stream(eng, t, 16 * 60, "sitting", fps=1)
    assert reminders(ev, RuleCode.R1_SITTING)[0].change == "redelivered"


def test_r1_elder_acknowledge_and_cooldown():
    eng = make_engine(cooldown_minutes=30)
    ev, t = stream(eng, at(8), 46 * 60, "sitting", fps=1)
    rid = reminders(ev, RuleCode.R1_SITTING)[0].reminder.reminder_id
    eng.acknowledge(rid, "elder", t)
    ev, t = stream(eng, t, 20 * 60, "sitting", fps=1)
    assert reminders(ev, RuleCode.R1_SITTING) == []  # cooldown
    ev, t = stream(eng, t, 11 * 60, "sitting", fps=1)
    assert [e.change for e in reminders(ev, RuleCode.R1_SITTING)] == ["created"]


def test_r6_absence_via_tick_and_resolution():
    eng = make_engine()
    _, t = stream(eng, at(9), 30, "sitting")
    events = []
    for minute in range(1, 200, 5):
        events += eng.tick(t + minute * 60)
    created = reminders(events, RuleCode.R6_ABSENCE)
    assert [e.change for e in created] == ["created"]
    assert created[0].reminder.audience == Audience.CAREGIVER
    assert created[0].reminder.status == ReminderStatus.ESCALATED
    ev, _ = stream(eng, t + 200 * 60, 20, "sitting")
    assert [e.change for e in reminders(ev, RuleCode.R6_ABSENCE)] == ["auto_resolved"]


def test_tick_does_nothing_before_first_observation():
    eng = make_engine()
    assert eng.tick(at(12)) == []
    assert eng.current_state(at(12)).posture == Posture.UNOBSERVED


def test_segment_across_midnight_is_split_by_local_day():
    eng = make_engine(enabled_rules=[])
    _, t = stream(eng, at(23, 58), 240, "sitting")
    ev, t = stream(eng, t, 30, "standing")
    days = {e.summary.local_date: e.summary for e in ev if isinstance(e, DaySummaryUpdated)}
    assert set(days) == {"2026-11-03", "2026-11-04"}
    assert 110 <= days["2026-11-03"].seconds["sitting"] <= 125
    assert 110 <= days["2026-11-04"].seconds["sitting"] <= 125


BED = Zone(zone_type=ZoneType.BED, polygon=[(0.2, 0.62), (0.95, 0.62), (0.95, 0.85), (0.2, 0.85)])


def test_r5_night_bed_exit():
    eng = PostureEngine("P1", config=RulesConfig(), zones=[BED])
    _, t = stream(eng, at(23), 60, "lying")
    assert eng.current_state(t).location == Location.BED
    ev, t = stream(eng, t, 21 * 60, "standing", fps=1)
    assert any(isinstance(e, NightBedExit) for e in ev)
    created = reminders(ev, RuleCode.R5_NIGHT_BED_EXIT)
    assert [e.change for e in created] == ["created"]
    assert "rời giường lúc 23:01" in created[0].reminder.message
    assert eng.day_summary("2026-11-03", t).night_bed_exits == 1
    ev, t = stream(eng, t, 30, "lying")
    assert [e.change for e in reminders(ev, RuleCode.R5_NIGHT_BED_EXIT)] == ["auto_resolved"]


def test_lying_outside_bed_zone_is_floor():
    eng = PostureEngine("P1", zones=[BED])
    t = at(10)
    for i in range(30):
        eng.ingest(obs(t + i, "lying", dy=-0.4))  # hip at y≈0.32, above the bed polygon
    assert eng.current_state(t + 29).location == Location.FLOOR


def test_r4_needs_an_observed_day():
    eng = make_engine()  # default: the camera must have seen 2 hours of the day
    ev, t = stream(eng, at(16, 0, 5), 20, "sitting")
    assert reminders(ev, RuleCode.R4_LOW_ACTIVITY) == []


def test_r4_low_activity_resolves_with_exercise():
    eng = make_engine(activity_min_observed_minutes=0)
    ev, t = stream(eng, at(16, 0, 5), 20, "sitting")
    created = reminders(ev, RuleCode.R4_LOW_ACTIVITY)
    assert [e.change for e in created] == ["created"]
    assert "0 phút" in created[0].reminder.message
    ev = eng.add_exercise(t, t + 31 * 60)
    assert [e.change for e in reminders(ev, RuleCode.R4_LOW_ACTIVITY)] == ["auto_resolved"]
    assert eng.day_summary("2026-11-03", t).exercise_seconds == pytest.approx(31 * 60)


def test_r2_lying_daytime():
    eng = make_engine(lying_day_minutes=10)
    ev, t = stream(eng, at(13), 11 * 60, "lying", fps=1)
    assert [e.change for e in reminders(ev, RuleCode.R2_LYING_DAY)] == ["created"]


def test_r3_reposition_for_caregiver():
    eng = make_engine(enabled_rules=[RuleCode.R3_REPOSITION], reposition_minutes=15)
    ev, t = stream(eng, at(14), 16 * 60, "lying", fps=1)
    created = reminders(ev, RuleCode.R3_REPOSITION)
    assert [e.change for e in created] == ["created"]
    assert created[0].reminder.audience == Audience.CAREGIVER
    ev = []
    for i in range(30):
        ev += eng.ingest(obs(t + i, "lying", dx=0.2))
    assert [e.change for e in reminders(ev, RuleCode.R3_REPOSITION)] == ["auto_resolved"]


def test_max_reminders_per_day():
    eng = make_engine(max_reminders_per_day=1, cooldown_minutes=0)
    ev, t = stream(eng, at(8), 46 * 60, "sitting", fps=1)
    rid = reminders(ev, RuleCode.R1_SITTING)[0].reminder.reminder_id
    eng.acknowledge(rid, "elder", t)
    ev, t = stream(eng, t, 60 * 60, "sitting", fps=1)
    assert reminders(ev, RuleCode.R1_SITTING) == []
