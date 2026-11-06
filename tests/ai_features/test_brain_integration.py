"""AICoreBrain against the real unified schema (all migrations, in-memory SQLite)."""

from datetime import datetime, timedelta, timezone

import pytest

from shared.ai_runtime.brain import AICoreBrain, is_find_object
from shared.ai_runtime.store import AICoreStore
from features.medication.intake_monitoring.domain import IntakeObservation, IntakeVerdict
from features.object_finder.domain import DetectionFrame, DetectionIn
from shared.vision.pose.observation import Observation
from shared.database.connection import UnifiedDatabaseManager
from shared.database.migrator import Migrator
from tests.ai_features.synth import landmarks

UTC = timezone.utc
T0 = datetime(2026, 11, 5, 3, 0, tzinfo=UTC).timestamp()  # 10:00 in Hà Nội
PID = "pat_ai_1"


class Clock:
    def __init__(self, now):
        self.now = now

    def __call__(self):
        return self.now


@pytest.fixture
def env():
    db = UnifiedDatabaseManager(":memory:")
    Migrator(db).apply_all()
    db.execute("INSERT INTO patients(patient_id, full_name) VALUES (?, ?)", (PID, "Cụ Test"))
    store = AICoreStore(db)
    spoken = []
    clock = Clock(T0 - 600)
    brain = AICoreBrain(store, spoken.append, clock)
    return db, store, brain, spoken, clock


def add_dose(db, occ="occ_1", at=T0, name="Amlodipin"):
    due = datetime.fromtimestamp(at, UTC)
    db.execute(
        "INSERT INTO dose_occurrences (occurrence_id, schedule_id, revision, scheduled_at_utc, local_scheduled_time, "
        "grace_deadline_utc, state, medication_name_snapshot, dose_snapshot, instructions_snapshot, patient_id) "
        "VALUES (?,?,?,?,?,?, 'PENDING', ?, '1 viên', 'Sau ăn', ?)",
        (occ, "sch_1", 1, due.isoformat(), due.replace(tzinfo=None).isoformat(), (due + timedelta(minutes=60)).isoformat(), name, PID),
    )


def events(db, prefix):
    return [r["event_type"] for r in db.fetchall("SELECT event_type FROM outbox_events WHERE event_type LIKE ? ORDER BY rowid",
                                                 (prefix + "%",))]


def intake(ts, verdict=IntakeVerdict.VERIFIED):
    return IntakeObservation(ts=ts, verdict=verdict, hand="right", contact_at=ts - 1, mouth_open_at=ts - 0.5, score=0.9,
                             steps={"contact_at": ts - 1, "drink_evidence": "cup"})


def test_medication_milestones_speak_and_alert(env):
    db, store, brain, spoken, clock = env
    add_dose(db)
    for minute in (-6, -4, 0, 5, 10, 30, 61):
        clock.now = T0 + minute * 60
        brain.tick(home_patient=PID)
    types = events(db, "ai.medication")
    assert types == ["ai.medication.monitoring_started", "ai.medication.due_reminded", "ai.medication.not_taken",
                     "ai.medication.escalated", "ai.medication.monitoring_closed"]
    assert "đến giờ uống thuốc Amlodipin" in spoken[0]
    assert "chưa uống" in spoken[1]
    alert = db.fetchone("SELECT alert_type, status FROM alerts WHERE occurrence_id='occ_1'")
    assert alert == {"alert_type": "OVERDUE", "status": "NEW"}
    assert "medication.alert_created" in events(db, "medication.")


def test_camera_verified_intake_confirms_the_dose(env):
    db, store, brain, spoken, clock = env
    add_dose(db)
    clock.now = T0 - 200
    brain.tick(home_patient=PID)
    assert brain.medication_active(PID)
    clock.now = T0 + 120
    brain._intake(brain.session(PID), intake(clock.now))
    occ = db.fetchone("SELECT state, action_actor FROM dose_occurrences WHERE occurrence_id='occ_1'")
    assert occ == {"state": "CONFIRMED", "action_actor": "camera_verified"}
    ev = db.fetchone("SELECT verdict, applied FROM ai_medication_evidence")
    assert ev == {"verdict": "VERIFIED", "applied": "confirmed"}
    assert db.fetchone("SELECT event_type, actor_role FROM dose_events") == {"event_type": "resolved", "actor_role": "camera_ai"}
    assert spoken[-1].startswith("Cháu đã ghi nhận")
    clock.now = T0 + 11 * 60
    brain.tick(home_patient=PID)
    assert "ai.medication.not_taken" not in events(db, "ai.medication")  # taken: no overdue reminder
    assert not brain.medication_active(PID)


def test_likely_intake_only_asks(env):
    db, store, brain, spoken, clock = env
    add_dose(db)
    clock.now = T0 - 200
    brain.tick(home_patient=PID)
    brain._intake(brain.session(PID), intake(T0 + 60, IntakeVerdict.LIKELY))
    assert db.fetchone("SELECT state FROM dose_occurrences")["state"] == "PENDING"
    assert db.fetchone("SELECT verdict, applied FROM ai_medication_evidence") == {"verdict": "LIKELY", "applied": "none"}
    assert "phải không" in spoken[-1]


def test_restart_does_not_replay_reminders(env):
    db, store, brain, spoken, clock = env
    add_dose(db)
    clock.now = T0 + 12 * 60  # app started late
    brain.tick(home_patient=PID)
    assert len(spoken) == 1 and "chưa uống" in spoken[0]


def test_posture_and_fall_in_trial_mode(env):
    db, store, brain, spoken, clock = env
    t = T0
    for i in range(50):
        brain.on_observation(PID, Observation(t + i / 10, landmarks("standing")))
    t += 5
    for i in range(400):
        brain.on_observation(PID, Observation(t + i / 10, landmarks("lying")))
    assert db.fetchone("SELECT posture FROM ai_posture_state WHERE patient_id=?", (PID,))["posture"] == "LYING"
    fall = db.fetchone("SELECT status, trial FROM ai_fall_events")
    assert fall["trial"] == 1
    assert any("có sao không" in s for s in spoken)  # check-in prompt spoken
    reply = brain.handle_transcript(PID, "tôi ổn", now=t + 41)
    assert reply.startswith("Vâng")
    assert db.fetchone("SELECT status FROM ai_fall_events")["status"] == "USER_OK"
    assert db.fetchone("SELECT COUNT(*) AS n FROM voice_requests")["n"] == 0  # trial: nobody is paged


def test_objects_answer_by_voice(env):
    db, store, brain, spoken, clock = env
    for i in range(3):
        brain.on_detections(PID, DetectionFrame(T0 + i, 0, (DetectionIn("remote", 0.8, (0.2, 0.7, 0.05, 0.04)),)))
    assert events(db, "ai.objects") == ["ai.objects.confirmed"]
    reply = brain.handle_transcript(PID, "Cái điều khiển đâu rồi?", now=T0 + 60)
    assert "điều khiển" in reply and "vừa thấy" in reply
    assert brain.handle_transcript(PID, "hôm nay thứ mấy", now=T0) is None
    assert is_find_object("tìm giúp tôi cái kính") and not is_find_object("tôi đã uống thuốc rồi")
    # A fresh brain (app restart) answers from the database.
    again = AICoreBrain(store, clock=clock)
    assert "điều khiển" in again.answer_where(PID, "điều khiển", T0 + 120).text
