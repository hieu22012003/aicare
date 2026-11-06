"""Intake detector + dose policy on synthetic Holistic landmarks (pixels, 640x480 frame)."""

from features.medication.intake_monitoring.detector import (
    CHIN, FACE_LEFT, FACE_RIGHT, FOREHEAD, INDEX_MCP, INDEX_TIP, LIP_LOWER_INNER, LIP_UPPER_INNER,
    MOUTH_LEFT, MOUTH_RIGHT, NOSE_TIP, PINKY_MCP, THUMB_TIP, IntakeDetector,
)
from features.medication.intake_monitoring.domain import Hand, HolisticFrame, IntakeConfig, IntakeVerdict
from features.medication.intake_monitoring.policy import DoseAction, MonitorPolicy, due_actions, skipped_speech

MOUTH = (320.0, 200.0)
FPS = 10


def face(open_mouth=False, tilt=False):
    pts = [(320.0, 180.0)] * 468
    pts = list(pts)
    gap = 14 if open_mouth else 2
    nose_y = 160 if tilt else 175
    pts[FACE_LEFT], pts[FACE_RIGHT] = (260.0, 180.0), (380.0, 180.0)  # face width 120
    pts[MOUTH_LEFT], pts[MOUTH_RIGHT] = (300.0, 200.0), (340.0, 200.0)  # mouth width 40
    pts[LIP_UPPER_INNER], pts[LIP_LOWER_INNER] = (320.0, 200 - gap / 2), (320.0, 200 + gap / 2)
    pts[FOREHEAD], pts[NOSE_TIP], pts[CHIN] = (320.0, 120.0), (320.0, float(nose_y)), (320.0, 230.0)
    return tuple(pts)


def hand(at, pinch=True):
    x, y = at
    pts = [(x, y + 40)] * 21
    pts = list(pts)
    pts[INDEX_MCP], pts[PINKY_MCP] = (x - 20, y + 30), (x + 20, y + 30)  # hand width 40
    pts[THUMB_TIP] = (x, y)
    pts[INDEX_TIP] = (x + (4 if pinch else 30), y)
    return tuple(pts)


AWAY = (420.0, 380.0)


def frames(start, seconds, **kw):
    n = int(seconds * FPS)
    return [frame(start + i / FPS, **kw) for i in range(n)], start + n / FPS


def frame(ts, at=AWAY, open_mouth=False, pinch=True, which="right", tilt=False, **flags):
    h = hand(at, pinch)
    return HolisticFrame(ts, face(open_mouth, tilt), h if which == "left" else None, h if which == "right" else None, **flags)


def run(det, fs):
    out = []
    for f in fs:
        out += det.feed(f)
    return out


def take_pill(t, open_mouth=True, contact=1.0, **kw):
    """Hand with a pill away -> at the mouth (mouth opens) -> away again."""
    fs, t = frames(t, 1.0, **kw)
    more, t = frames(t, contact, at=MOUTH, open_mouth=open_mouth, **kw)
    rest, t = frames(t, 1.5, **kw)
    return fs + more + rest, t


def test_gesture_then_cup_is_verified():
    det = IntakeDetector()
    fs, t = take_pill(0.0)
    assert run(det, fs) == []  # waiting for the drink
    out = run(det, [frame(t + 5, cup_near_mouth=True)])
    assert len(out) == 1 and out[0].verdict == IntakeVerdict.VERIFIED and out[0].steps["drink_evidence"] == "cup"


def test_gesture_alone_becomes_likely_after_drink_window():
    det = IntakeDetector()
    fs, t = take_pill(0.0)
    run(det, fs)
    out = run(det, frames(t, 61)[0])
    assert [o.verdict for o in out] == [IntakeVerdict.LIKELY]


def test_pill_context_verifies_without_drink():
    det = IntakeDetector()
    fs, t = take_pill(0.0, near_pillbox=True)
    out = run(det, fs)
    assert [o.verdict for o in out] == [IntakeVerdict.VERIFIED] and out[0].steps["drink_evidence"] == "pill_context"


def test_second_contact_with_head_tilt_counts_as_drinking():
    det = IntakeDetector()
    fs, t = take_pill(0.0)
    run(det, fs)
    glass, t = frames(t, 1.0, at=MOUTH, tilt=True)
    out = run(det, glass)
    assert [o.verdict for o in out] == [IntakeVerdict.VERIFIED]


def test_mouth_closed_or_too_long_or_phone_is_not_intake():
    for kw in ({"open_mouth": False}, {"contact": 7.0}, {"phone_in_hand": True}):
        det = IntakeDetector()
        contact = kw.pop("contact", 1.0)
        fs, t = take_pill(0.0, contact=contact, **kw)
        out = run(det, fs + frames(t, 70)[0])
        assert out == [], kw


def test_mouth_opening_just_after_the_hand_leaves_still_counts():
    det = IntakeDetector()
    fs, t = frames(0.0, 1.0)
    at, t = frames(t, 0.6, at=MOUTH)
    after, t = frames(t, 0.5, open_mouth=True)
    run(det, fs + at + after)
    assert [o.verdict for o in run(det, frames(t, 61)[0])] == [IntakeVerdict.LIKELY]


def test_hand_setting_and_identity():
    det = IntakeDetector(IntakeConfig(hand=Hand.RIGHT))
    fs, t = take_pill(0.0, which="left", near_pillbox=True)
    assert run(det, fs + frames(t, 70)[0]) == []
    det = IntakeDetector()
    fs, t = take_pill(0.0, near_pillbox=True, identity_ok=False)
    assert run(det, fs) == []


def test_policy_milestones_and_restart():
    p = MonitorPolicy()
    t0 = 10_000.0
    assert due_actions(p, t0, t0 - 400, set(), False) == []
    assert due_actions(p, t0, t0 - 200, set(), False) == [DoseAction.OPEN]
    assert due_actions(p, t0, t0 + 1, {DoseAction.OPEN}, False) == [DoseAction.SPEAK_DUE]
    assert due_actions(p, t0, t0 + 601, {DoseAction.OPEN, DoseAction.SPEAK_DUE}, False) == [DoseAction.OVERDUE]
    # Restart at +40 min: speak only the latest reminder, still escalate.
    assert due_actions(p, t0, t0 + 2400, set(), False) == [DoseAction.OPEN, DoseAction.OVERDUE, DoseAction.ESCALATE]
    assert skipped_speech(p, t0, t0 + 2400, set()) == [DoseAction.SPEAK_DUE]
    assert due_actions(p, t0, t0 + 4000, set(), False) == [DoseAction.CLOSE]
    assert due_actions(p, t0, t0 + 1, {DoseAction.OPEN}, True) == [DoseAction.CLOSE]
