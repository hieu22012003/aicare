"""Portal API for AI features (handler level, real schema)."""

import time

import pytest

from shared.ai_runtime.brain import AICoreBrain
from shared.ai_runtime.store import AICoreStore
from shared.ai_runtime.web_routes import handle_ai_route
from features.object_finder.domain import DetectionFrame, DetectionIn
from shared.database.connection import UnifiedDatabaseManager
from shared.database.migrator import Migrator

PID = "pat_web_1"


class FakeAuth:
    def __init__(self, can_edit=True):
        self.can_edit = can_edit

    def check_patient_access(self, user_id, patient_id, perm):
        return (self.can_edit, None if self.can_edit else "Không có quyền")


@pytest.fixture
def db():
    db = UnifiedDatabaseManager(":memory:")
    Migrator(db).apply_all()
    db.execute("INSERT INTO patients(patient_id, full_name) VALUES (?, ?)", (PID, "Cụ Web"))
    return db


def call(db, method, rest, query=None, body=None, auth=None):
    return handle_ai_route(db, auth or FakeAuth(), method, rest, query or {}, body, PID, "usr_cg")


def test_overview_and_where(db):
    code, data = call(db, "GET", [])
    assert code == 200 and data["fall"]["trial"] is False and data["posture"]["current"] is None
    brain = AICoreBrain(AICoreStore(db))
    now = time.time()
    for i in range(3):
        brain.on_detections(PID, DetectionFrame(now + i, 0, (DetectionIn("book", 0.9, (0.8, 0.1, 0.05, 0.05)),)))
    code, data = call(db, "GET", ["objects", "where"], {"q": "quyển sách đâu"})
    assert code == 200 and data["status"] == "found"
    assert call(db, "GET", ["objects", "where"], {"q": ""})[0] == 400
    assert len(call(db, "GET", [])[1]["objects"]["sightings"]) == 1


def test_settings_validate_and_need_edit_permission(db):
    code, data = call(db, "GET", ["settings", "medication"])
    assert code == 200 and data["config"]["policy"]["overdue_minutes"] == 5
    cfg = data["config"]
    cfg["policy"]["overdue_minutes"] = 15
    cfg["intake"]["hand"] = "right"
    code, data = call(db, "PUT", ["settings", "medication"], body={"config": cfg})
    assert code == 200 and data["config"]["policy"]["overdue_minutes"] == 15
    assert AICoreBrain(AICoreStore(db)).session(PID).med.intake.hand.value == "right"
    cfg["policy"]["escalate_minutes"] = 5  # must be after overdue
    assert call(db, "PUT", ["settings", "medication"], body={"config": cfg})[0] == 422
    assert call(db, "PUT", ["settings", "fall"], body={}, auth=FakeAuth(can_edit=False))[0] == 403
    assert call(db, "GET", ["settings", "nope"])[0] == 404
    assert db.fetchone("SELECT COUNT(*) AS n FROM outbox_events WHERE event_type='ai.settings_changed'")["n"] == 1


def test_label_fall_event(db):
    db.execute("INSERT INTO ai_fall_events (event_id, patient_id, kind, status, trial, detected_at_utc, updated_at_utc, message) "
               "VALUES ('fe1', ?, 'F1', 'USER_OK', 1, '2026-11-05T03:00:00+00:00', '2026-11-05T03:00:00+00:00', 'x')", (PID,))
    assert call(db, "POST", ["fall", "fe1", "label"], body={"label": "false_alarm", "note": "ngồi xuống sàn lau nhà"})[0] == 200
    assert db.fetchone("SELECT label, label_by FROM ai_fall_events")["label"] == "false_alarm"
    assert call(db, "POST", ["fall", "fe1", "label"], body={"label": "bad"})[0] == 400
    assert call(db, "POST", ["fall", "nope", "label"], body={"label": "unsure"})[0] == 404


def test_overview_doses_show_today_not_next_week(db):
    """Field 08/11: a week of future doses filled the 20-row list and hid the dose being watched now."""
    from datetime import UTC, datetime, timedelta

    now = datetime.now(UTC).replace(microsecond=0, tzinfo=None)
    for i, at in enumerate([now - timedelta(minutes=5)] + [now + timedelta(hours=h) for h in range(13, 13 + 24 * 7, 6)]):
        db.execute(
            "INSERT INTO dose_occurrences (occurrence_id, schedule_id, revision, scheduled_at_utc, local_scheduled_time, "
            "grace_deadline_utc, state, medication_name_snapshot, dose_snapshot, instructions_snapshot, patient_id) "
            "VALUES (?,?,?,?,?,?, 'PENDING', 'Amlor', '1 viên', 'Sau ăn', ?)",
            (f"occ_{i}", "sch_1", 1, at.isoformat(), at.isoformat(), (at + timedelta(hours=1)).isoformat(), PID))
    doses = call(db, "GET", [])[1]["medication"]["doses"]
    assert [d["occurrence_id"] for d in doses] == ["occ_0"]
