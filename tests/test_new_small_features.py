"""Clock answers, sadness comfort, face sighting log and the two new exercises."""

import math
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace as P
from zoneinfo import ZoneInfo

import pytest

from features.exercise_rehab.rehab_engine import ArmCircleTracker, RehabilitationEngine, SideBendTracker
from features.face_monitoring.sighting_log import FaceSightingLog, unusual_today
from features.music_care.comfort import SadnessComfort
from features.voice_assistant.clock_answers import answer
from shared.database.connection import UnifiedDatabaseManager
from shared.database.migrator import Migrator

VN = ZoneInfo("Asia/Ho_Chi_Minh")
UTC = timezone.utc


@pytest.fixture
def db():
    d = UnifiedDatabaseManager(":memory:")
    Migrator(d).apply_all()
    d.execute("INSERT INTO patients(patient_id, full_name) VALUES ('p1', 'Cụ Một')")
    return d


# ------------------------------------------------------------------ clock


def test_clock_answers_time_and_date_in_vietnamese():
    now = datetime(2026, 11, 7, 15, 5, tzinfo=VN)
    assert answer("mấy giờ rồi cháu", now) == "Thưa bác, bây giờ là 3 giờ 05 phút chiều."
    assert answer("Hôm nay thứ mấy?", now, honorific="anh") == "Thưa anh, hôm nay là thứ Bảy, ngày 7 tháng 11 năm 2026."
    assert answer("gọi con tôi", now) is None


# ------------------------------------------------------------------ comfort


def add_summary(db, sid, label, at):
    db.execute("INSERT INTO emotion_summaries_v2 VALUES (?, 'p1', ?, ?, ?, ?, ?, ?, 'window', '{}', 3600, 0)",
               (sid, at, at, at, at, label, label))


def test_sad_summary_speaks_once_starts_music_and_notifies_family(db):
    now = datetime(2026, 11, 7, 3, 0, tzinfo=UTC)
    spoken, played = [], []
    c = SadnessComfort(db, spoken.append, lambda: played.append(1) or True, honorific="anh", clock=lambda: now.timestamp())
    add_summary(db, "s1", "Neutral", now.isoformat())
    assert c.check("p1") is None
    add_summary(db, "s2", "Sad", (now + timedelta(seconds=1)).isoformat())
    text = c.check("p1")
    assert text and "anh" in text and played == [1]
    assert c.check("p1") is None  # same summary
    events = db.fetchall("SELECT event_type FROM outbox_events WHERE event_type='emotion.support_offered'")
    assert len(events) == 1


def test_old_sad_summary_is_ignored(db):
    now = datetime(2026, 11, 7, 3, 0, tzinfo=UTC)
    spoken = []
    c = SadnessComfort(db, spoken.append, None, clock=lambda: now.timestamp())
    add_summary(db, "s3", "Sad", (now - timedelta(hours=2)).isoformat())
    assert c.check("p1") is None and spoken == []


# ------------------------------------------------------------------ face log


def test_face_log_counts_visits_and_flags_late_and_strangers(db):
    log = FaceSightingLog(db)
    base = datetime(2026, 11, 7, 0, 30, tzinfo=UTC)  # 07:30 in Hà Nội
    for d in range(1, 5):  # usual: first seen ~07:30
        log.record("p1", "patient", (base - timedelta(days=d)).timestamp())
    late = base + timedelta(hours=3)  # 10:30 today
    for k in range(5):
        log.record("p1", "patient", late.timestamp() + k * 10)
    log.record("p1", "patient", late.timestamp() + 3600)  # a second visit after a long gap
    log.record("p1", "stranger", late.timestamp() + 100)
    row = db.fetchone("SELECT * FROM ai_face_sightings WHERE sighting_id='p1:2026-11-07:patient'")
    assert row["appearances"] == 2 and row["seen_seconds"] > 0
    notes = unusual_today(db, "p1", "2026-11-07", late + timedelta(hours=2), VN)
    kinds = {n["kind"] for n in notes}
    assert kinds == {"late", "stranger"}


def test_face_log_flags_missing_elder(db):
    log = FaceSightingLog(db)
    base = datetime(2026, 11, 7, 0, 30, tzinfo=UTC)
    for d in range(1, 5):
        log.record("p1", "patient", (base - timedelta(days=d)).timestamp())
    notes = unusual_today(db, "p1", "2026-11-07", base + timedelta(hours=3), VN)
    assert [n["kind"] for n in notes] == ["missing"]


# ------------------------------------------------------------------ exercises


def pose(**over):
    pts = [P(x=0.5, y=0.5, z=0.0, visibility=0.99) for _ in range(33)]
    for i, (x, y) in over.items():
        pts[int(i[1:])] = P(x=x, y=y, z=0.0, visibility=0.99)
    return pts


def test_arm_circles_count_full_turns():
    t = ArmCircleTracker(target_reps=5)
    ls, rs = (0.4, 0.4), (0.6, 0.4)
    for step in range(0, 360 * 3 + 1, 15):
        a = math.radians(step)
        lw = (ls[0] + 0.18 * math.cos(a), ls[1] + 0.18 * math.sin(a))
        rw = (rs[0] - 0.18 * math.cos(a), rs[1] + 0.18 * math.sin(a))
        le = ((ls[0] + lw[0]) / 2, (ls[1] + lw[1]) / 2)
        re = ((rs[0] + rw[0]) / 2, (rs[1] + rw[1]) / 2)
        t.process(pose(p11=ls, p12=rs, p13=le, p14=re, p15=lw, p16=rw), 640, 480)
    assert t.reps == 3 and t.correct_reps == 3


def test_side_bends_need_both_sides_and_a_return(monkeypatch):
    clock = {"t": 1000.0}
    monkeypatch.setattr("features.exercise_rehab.rehab_engine.time.time", lambda: clock["t"])
    t = SideBendTracker(target_reps=3)

    def frame(tilt_deg):
        hip = (0.5, 0.75)
        dx = 0.3 * math.tan(math.radians(tilt_deg)) * 480 / 640
        sh = (0.5 + dx, 0.45)
        clock["t"] += 0.2
        return t.process(pose(p11=(sh[0] - 0.08, sh[1]), p12=(sh[0] + 0.08, sh[1]), p23=(hip[0] - 0.06, hip[1]),
                              p24=(hip[0] + 0.06, hip[1])), 640, 480)

    for seq in ([0] * 3 + [20] * 3 + [0] * 3 + [-20] * 3 + [0] * 3) * 2:
        frame(seq)
    assert t.reps == 2 and t.correct_reps == 2
    for seq in [20] * 4 + [0] * 3:  # one side only: no rep
        frame(seq)
    assert t.reps == 2


def test_engine_knows_the_new_exercises():
    assert {"arm_circles", "side_bend"} <= set(RehabilitationEngine.TRACKERS)
