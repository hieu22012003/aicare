"""Cloud-backend device API: auth, ingestion, voice, speech queue, medication ticker."""

from datetime import datetime, timedelta, timezone

import pytest

from shared.ai_runtime import device_api
from shared.ai_runtime.device_api import BackendAIService, handle_device_route, set_service
from shared.database.connection import UnifiedDatabaseManager
from shared.database.migrator import Migrator
from tests.ai_features.synth import landmarks

PID = "pat_dev_1"
KEY = "device-key-0123456789abcdef"
H = {"X-Device-Key": KEY}
UTC = timezone.utc


@pytest.fixture
def db(monkeypatch):
    monkeypatch.setenv("AICARE_DEVICE_KEYS", f"{KEY}:{PID},short:*")
    db = UnifiedDatabaseManager(":memory:")
    Migrator(db).apply_all()
    db.execute("INSERT INTO patients(patient_id, full_name) VALUES (?, ?)", (PID, "Cụ Dev"))
    db.execute("INSERT INTO patients(patient_id, full_name) VALUES ('other', 'Khác')")
    set_service(None)
    yield db
    set_service(None)


def call(db, method, action, body=None, query=None, headers=H, pid=PID):
    return handle_device_route(db, method, f"/api/device/patients/{pid}/{action}", query or {}, body, headers)


def test_auth(db):
    assert call(db, "GET", "commands", headers={})[0] == 401
    assert call(db, "GET", "commands", headers={"X-Device-Key": "wrong"})[0] == 401
    assert call(db, "GET", "commands", pid="other")[0] == 401  # key not valid for that patient
    assert call(db, "GET", "commands", headers={"x-device-key": "short"})[0] == 401  # keys < 16 chars ignored
    assert call(db, "GET", "commands")[0] == 200


def test_ingest_voice_and_validation(db):
    now = datetime.now(UTC).timestamp()
    obs = [{"ts": now + i / 10, "landmarks": [[p.x, p.y, p.z, p.visibility] for p in landmarks("standing")]} for i in range(20)]
    code, data = call(db, "POST", "observations", {"observations": obs})
    assert code == 200 and data["accepted"]["observations"] == 20
    frames = [{"ts": now + i, "detections": [{"label": "cup", "score": 0.9, "box": [0.7, 0.2, 0.05, 0.05]}]} for i in range(3)]
    assert call(db, "POST", "detections", {"frames": frames})[0] == 200
    code, data = call(db, "POST", "voice", {"text": "cái cốc của tôi đâu"})
    assert code == 200 and "cốc" in data["reply"]
    assert call(db, "POST", "observations", {"observations": [{"ts": 1, "landmarks": [[0, 0, 0, 1]]}]})[0] == 422
    assert call(db, "POST", "holistic", {"frames": [{"ts": 1, "face": [[0, 0]] * 10}]})[0] == 422
    assert call(db, "GET", "commands", query={"after": "x"})[0] == 400


def test_ticker_queues_medication_speech(db):
    t0 = datetime(2026, 11, 6, 3, 0, tzinfo=UTC)
    db.execute(
        "INSERT INTO dose_occurrences (occurrence_id, schedule_id, revision, scheduled_at_utc, local_scheduled_time, "
        "grace_deadline_utc, state, medication_name_snapshot, dose_snapshot, instructions_snapshot, patient_id) "
        "VALUES ('occ_d', 'sch', 1, ?, ?, ?, 'PENDING', 'Metformin', '1 viên', '', ?)",
        (t0.isoformat(), t0.replace(tzinfo=None).isoformat(), (t0 + timedelta(hours=1)).isoformat(), PID))
    clock = {"now": t0.timestamp() - 120}
    svc = BackendAIService(db, clock=lambda: clock["now"])
    set_service(svc)
    svc.tick()
    assert call(db, "GET", "commands")[1]["monitoring"]["holistic"] is True
    clock["now"] = t0.timestamp() + 5
    svc.tick()
    clock["now"] = t0.timestamp() + 11 * 60
    svc.tick()
    cmds = call(db, "GET", "commands")[1]["commands"]
    texts = [c["payload"]["text"] for c in cmds]
    assert "đến giờ uống thuốc Metformin" in texts[0] and "chưa uống" in texts[1]
    after = cmds[0]["command_id"]
    assert [c["command_id"] for c in call(db, "GET", "commands", query={"after": str(after)})[1]["commands"]] == [cmds[1]["command_id"]]
