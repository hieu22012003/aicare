from datetime import datetime
from zoneinfo import ZoneInfo

from ai_core.fall_risk_detection.domain import (
    CheckinResult,
    EventStatus,
    FallConfig,
    FallKind,
    FallLabel,
    SafetyState,
    SignalCode,
)
from ai_core.fall_risk_detection.engine import (
    CheckinRequested,
    FallEventChanged,
    FallRiskEngine,
    GaitBaselineUpdated,
    RiskSignalDetected,
    SkeletonClipReady,
)
from ai_core.shared.pose.landmarks import Landmark
from ai_core.shared.pose.observation import Identity, Observation
from ai_core.shared.zones import Zone, ZoneType
from tests.ai_features.synth import landmarks

TZ = ZoneInfo("Asia/Ho_Chi_Minh")
T0 = datetime(2026, 11, 3, 10, 0, tzinfo=TZ).timestamp()
FPS = 10.0
# Bed in the top half of the image: the synthetic lying pose (hip y≈0.72) is outside it = floor.
BED_TOP = Zone(zone_type=ZoneType.BED, polygon=[(0.0, 0.0), (1.0, 0.0), (1.0, 0.4), (0.0, 0.4)])
BED_UNDER_LYING = Zone(zone_type=ZoneType.BED, polygon=[(0.2, 0.62), (0.95, 0.62), (0.95, 0.85), (0.2, 0.85)])


def blend(a: str, b: str, t: float) -> tuple[Landmark, ...]:
    la, lb = landmarks(a), landmarks(b)
    return tuple(
        Landmark(pa.x + (pb.x - pa.x) * t, pa.y + (pb.y - pa.y) * t, 0.0, pa.visibility) for pa, pb in zip(la, lb)
    )


def feed(eng, start, seconds, lms_fn, identity=Identity.PATIENT, boxes=()):
    events, n = [], int(seconds * FPS)
    for i in range(n):
        t = start + i / FPS
        lms = lms_fn(i / max(n - 1, 1), t) if callable(lms_fn) else lms_fn
        events += eng.ingest(Observation(t, lms, identity=identity, person_boxes=boxes))
    return events, start + n / FPS


def changes(events, kind=None):
    return [e.change for e in events if isinstance(e, FallEventChanged) and (kind is None or e.event.kind == kind)]


def event_of(events):
    return [e.event for e in events if isinstance(e, FallEventChanged)][-1]


def fall(eng, start=T0, lie_seconds=3.0, **kw):
    ev1, t = feed(eng, start, 5, landmarks("standing"), **kw)
    ev2, t = feed(eng, t, lie_seconds, landmarks("lying"), **kw)
    return ev1 + ev2, t


def test_fast_fall_creates_event_and_asks():
    eng = FallRiskEngine("P1")
    events, _ = fall(eng)
    assert changes(events) == ["created"]
    ev = event_of(events)
    assert ev.kind == FallKind.F1_FALL and ev.status == EventStatus.CHECKING and ev.trial
    asks = [e for e in events if isinstance(e, CheckinRequested)]
    assert [a.attempt for a in asks] == [1] and "có sao không" in asks[0].prompt
    assert eng.state() == SafetyState.EVENT_ACTIVE


def test_slow_lie_down_is_not_a_fall():
    eng = FallRiskEngine("P1")
    feed(eng, T0, 5, landmarks("standing"))
    events, t = feed(eng, T0 + 5, 6, lambda p, _: blend("standing", "lying", p))
    events += feed(eng, t, 30, landmarks("lying"))[0]
    assert changes(events) == []  # no observed fast transition, and no zones -> F2 disabled


def test_fast_lie_down_onto_bed_is_rest():
    eng = FallRiskEngine("P1", zones=[BED_UNDER_LYING])
    events, _ = fall(eng, lie_seconds=30)
    assert changes(events) == []


def test_lying_on_floor_without_seeing_the_fall():
    eng = FallRiskEngine("P1", zones=[BED_TOP])
    feed(eng, T0, 5, landmarks("standing"))
    _, t = feed(eng, T0 + 5, 6, lambda p, _: blend("standing", "lying", p))
    events, _ = feed(eng, t, 22, landmarks("lying"))
    assert changes(events, FallKind.F2_ON_FLOOR) == ["created"]
    assert "nằm dưới sàn" in event_of(events).message


def test_night_bed_exit_makes_floor_detection_faster():
    eng = FallRiskEngine("P1", zones=[BED_TOP])
    eng.note_night_bed_exit(T0)
    feed(eng, T0, 5, landmarks("standing"))
    _, t = feed(eng, T0 + 5, 6, lambda p, _: blend("standing", "lying", p))
    events, _ = feed(eng, t, 12, landmarks("lying"))
    assert changes(events, FallKind.F2_ON_FLOOR) == ["created"]


def test_disappearing_cancels_suspicion():
    eng = FallRiskEngine("P1")
    feed(eng, T0, 5, landmarks("standing"))
    events, t = feed(eng, T0 + 5, 1.0, landmarks("lying"))
    events += feed(eng, t, 10, None)[0]
    assert changes(events) == []
    assert eng.state() == SafetyState.UNOBSERVED


def test_no_answer_rechecks_then_escalates():
    eng = FallRiskEngine("P1")
    events, t = fall(eng)
    events, t = feed(eng, t, 30, landmarks("lying"))
    assert changes(events) == ["rechecking"]
    assert [e.attempt for e in events if isinstance(e, CheckinRequested)] == [2]
    events, t = feed(eng, t, 16, landmarks("lying"))
    assert changes(events) == ["escalated"]
    ev = event_of(events)
    assert ev.status == EventStatus.ESCALATED and "không trả lời sau 2 lần" in ev.caregiver_message
    assert [c.result for c in ev.checkins] == [CheckinResult.NO_RESPONSE, CheckinResult.NO_RESPONSE]


def test_answer_ok_closes_and_help_escalates():
    eng = FallRiskEngine("P1")
    events, t = fall(eng)
    ev = event_of(events)
    closed = eng.checkin(ev.event_id, CheckinResult.OK, t, "tôi ổn")
    assert changes(closed) == ["user_ok"] and event_of(closed).closed_by == "elder"

    eng2 = FallRiskEngine("P2")
    events, t = fall(eng2)
    escalated = eng2.checkin(event_of(events).event_id, CheckinResult.HELP, t, "cứu tôi")
    assert changes(escalated) == ["escalated"] and "kêu cứu" in event_of(escalated).caregiver_message


def test_long_lie_escalates_while_checking():
    eng = FallRiskEngine("P1", config=FallConfig(checkin_wait_seconds=120))
    events, t = fall(eng)
    events, _ = feed(eng, t, 62, landmarks("lying"))
    assert changes(events) == ["escalated"]
    assert event_of(events).long_lie or "1 phút" in event_of(events).caregiver_message


def test_standing_up_closes_episode_and_allows_new_event():
    eng = FallRiskEngine("P1")
    events, t = fall(eng)
    events, t = feed(eng, t, 11, landmarks("standing"))
    assert changes(events) == ["self_recovered"]
    assert eng.state() == SafetyState.NORMAL
    events, _ = fall(eng, start=t)
    assert changes(events) == ["created"]


def test_escalated_event_waits_for_caregiver_after_standing_up():
    eng = FallRiskEngine("P1")
    events, t = fall(eng)
    eid = event_of(events).event_id
    eng.checkin(eid, CheckinResult.HELP, t)
    events, t = feed(eng, t, 11, landmarks("standing"))
    assert changes(events) == ["stood_up"]
    assert [e.event_id for e in eng.open_events] == [eid]
    acked = eng.acknowledge(eid, "caregiver", t, FallLabel.CONFIRMED_FALL, "đã gọi điện")
    ev = event_of(acked)
    assert ev.status == EventStatus.ACKNOWLEDGED and ev.label == FallLabel.CONFIRMED_FALL
    assert eng.open_events == []


def test_skeleton_clip_covers_before_and_after():
    eng = FallRiskEngine("P1")
    events, t = fall(eng)
    events, _ = feed(eng, t, 21, landmarks("lying"))
    clips = [e for e in events if isinstance(e, SkeletonClipReady)]
    assert len(clips) == 1
    frames = clips[0].frames
    assert 250 <= len(frames) <= 320  # ~10 s before + 20 s after at ~10 fps
    assert frames[0]["lm"] and len(frames[0]["lm"]) == 33


def test_person_box_fallback_sees_someone_on_the_floor():
    eng = FallRiskEngine("P1", zones=[BED_TOP])
    lying_box = ((0.25, 0.62, 0.65, 0.18, 0.9),)  # wide box near the bottom, no skeleton
    events, _ = feed(eng, T0, 22, None, boxes=lying_box)
    assert changes(events, FallKind.F2_ON_FLOOR) == ["created"]


def test_unknown_person_fall_still_alerts():
    eng = FallRiskEngine("P1")
    events, _ = fall(eng, identity=Identity.UNKNOWN)
    ev = event_of(events)
    assert ev.identity == "unknown" and "chưa xác định" in ev.message


def _swaying(p, t):
    pts = list(landmarks("standing"))
    dx = 0.03 if int(t * 2) % 2 else -0.03
    for i in (0, 11, 12):
        pts[i] = Landmark(pts[i].x + dx, pts[i].y, 0.0, pts[i].visibility)
    return tuple(pts)


def signals(events, code=None):
    return [e.signal for e in events if isinstance(e, RiskSignalDetected) and (code is None or e.signal.code == code)]


def test_c1_sway_while_standing():
    eng = FallRiskEngine("P1")
    events, _ = feed(eng, T0, 6, _swaying)
    found = signals(events, SignalCode.C1_SWAY)
    assert found and found[0].alerted and found[0].value >= 0.15
    still, _ = feed(FallRiskEngine("P2"), T0, 6, landmarks("standing"))
    assert signals(still) == []


def test_c3_hard_sit_vs_controlled_sit():
    eng = FallRiskEngine("P1")
    feed(eng, T0, 3, landmarks("standing"))
    events, _ = feed(eng, T0 + 3, 3, landmarks("sitting"))
    assert [s.code for s in signals(events)] == [SignalCode.C3_HARD_SIT]

    eng2 = FallRiskEngine("P2")
    feed(eng2, T0, 3, landmarks("standing"))
    events, t = feed(eng2, T0 + 3, 3, lambda p, _: blend("standing", "sitting", p))
    events += feed(eng2, t, 3, landmarks("sitting"))[0]
    assert signals(events, SignalCode.C3_HARD_SIT) == []


def test_c4_quick_resits():
    eng = FallRiskEngine("P1", config=FallConfig(hard_sit_speed=10))
    events, t = feed(eng, T0, 3, landmarks("sitting"))
    for _ in range(2):
        ev, t = feed(eng, t, 2, landmarks("standing"))
        events += ev
        ev, t = feed(eng, t, 3, landmarks("sitting"))
        events += ev
    assert [s.code for s in signals(events, SignalCode.C4_QUICK_RESIT)] == [SignalCode.C4_QUICK_RESIT]


def _walk(speed, direction):
    def fn(p, t):
        dx = -0.3 + direction * 0.0 + (speed * 4 * p if direction > 0 else 0.6 - speed * 4 * p)
        return landmarks("standing", dx=dx)

    return fn


def test_c2_slow_walking_against_personal_baseline():
    eng = FallRiskEngine("P1")
    events, t = [], T0
    for k in range(5):
        ev, t = feed(eng, t, 4, _walk(0.15, 1 if k % 2 == 0 else -1))
        events += ev
        ev, t = feed(eng, t, 2, landmarks("standing", dx=0.3 if k % 2 == 0 else -0.3))
        events += ev
    baselines = [e for e in events if isinstance(e, GaitBaselineUpdated)]
    assert len(baselines) == 5 and baselines[-1].speed > 0.5
    ev, t = feed(eng, t, 4, _walk(0.06, -1))
    ev += feed(eng, t, 2, landmarks("standing"))[0]
    slow = signals(ev, SignalCode.C2_GAIT)
    assert slow and slow[0].value < 0.6


def test_signal_alerts_are_rate_limited():
    eng = FallRiskEngine("P1", config=FallConfig(signal_cooldown_minutes=30))
    events, t = feed(eng, T0, 6, _swaying)
    events += feed(eng, t + 120, 6, _swaying)[0]
    found = signals(events, SignalCode.C1_SWAY)
    assert len(found) == 2 and found[0].alerted and not found[1].alerted


def test_no_dizziness_signals_during_fall_episode():
    eng = FallRiskEngine("P1")
    events, t = fall(eng)
    events, _ = feed(eng, t, 6, _swaying)  # standing up again: recovery period, not dizziness
    assert signals(events) == []
