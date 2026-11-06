"""Desktop app in cloud device mode against the real device API (in-process transport).

Scenario from the requirement: dose at 10:00. The window opens at 09:55 and the camera is switched
on; the right hand brings the pill to the open mouth -> the server records the dose as taken.
Without it, at 10:10 the device says "... chưa uống" and the web portal gets ai.medication.not_taken.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import numpy as np
import pytest

from shared.ai_runtime.cloud_device import CloudDeviceRuntime
from shared.ai_runtime.device_api import BackendAIService, handle_device_route, set_service
from shared.database.connection import UnifiedDatabaseManager
from shared.database.migrator import Migrator
from tests.ai_features.test_medication_intake import MOUTH, AWAY, face, hand

UTC = timezone.utc
PID = "pat_cloud_1"
KEY = "device-key-cloud-0123456789"
T0 = datetime(2026, 11, 7, 3, 0, tzinfo=UTC).timestamp()  # 10:00 in Hà Nội


class Clock:
    def __init__(self, now):
        self.now = now

    def __call__(self):
        return self.now


class Transport:
    """Calls the device router directly, like the HTTP server would."""

    def __init__(self, db):
        self.db = db
        self.headers = {"X-Device-Key": KEY}

    def get(self, path, params=None):
        code, data = handle_device_route(self.db, "GET", path, params or {}, None, self.headers)
        assert code == 200, data
        return data

    def post(self, path, body):
        code, data = handle_device_route(self.db, "POST", path, {}, body, self.headers)
        assert code == 200, data
        return data


class Camera:
    def __init__(self):
        self.n = 0

    def get_capture_packet(self):
        self.n += 1
        return np.zeros((480, 640, 3), np.uint8), self.n, True


@dataclass
class Res:
    pose: object = None
    face: tuple = None
    left_hand: tuple = None
    right_hand: tuple = None


class ScriptedHolistic:
    """Returns the next scripted (hand position, mouth open) on every frame."""

    def __init__(self, script):
        self.script = list(script)

    def process(self, frame):
        at, open_mouth = self.script.pop(0) if self.script else (AWAY, False)
        return Res(None, face(open_mouth), None, hand(at))


class NoPose:
    def process(self, frame, ts_ms):
        return None


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setenv("AICARE_DEVICE_KEYS", f"{KEY}:{PID}")
    db = UnifiedDatabaseManager(":memory:")
    Migrator(db).apply_all()
    db.execute("INSERT INTO patients(patient_id, full_name) VALUES (?, ?)", (PID, "Cụ Mây"))
    due = datetime.fromtimestamp(T0, UTC)
    db.execute(
        "INSERT INTO dose_occurrences (occurrence_id, schedule_id, revision, scheduled_at_utc, local_scheduled_time, "
        "grace_deadline_utc, state, medication_name_snapshot, dose_snapshot, instructions_snapshot, patient_id) "
        "VALUES ('occ_c', 'sch_c', 1, ?, ?, ?, 'PENDING', 'Amlodipin', '1 viên', 'Sau ăn', ?)",
        (due.isoformat(), due.replace(tzinfo=None).isoformat(), (due + timedelta(hours=1)).isoformat(), PID))
    clock = Clock(T0 - 10 * 60)
    svc = BackendAIService(db, clock=clock)
    set_service(svc)
    yield db, svc, clock
    set_service(None)


def device(db, clock, script=()):
    spoken, wakes = [], []
    dev = CloudDeviceRuntime(Transport(db), Camera(), lambda: (PID, True), spoken.append, wake_camera=lambda: wakes.append(1),
                             pose_factory=NoPose, holistic_factory=lambda: ScriptedHolistic(script), clock=clock)
    return dev, spoken, wakes


def state(db):
    return db.fetchone("SELECT state FROM dose_occurrences WHERE occurrence_id='occ_c'")["state"]


def test_camera_turns_on_and_right_hand_intake_marks_dose_taken(env):
    db, svc, clock = env
    gesture = [(AWAY, False)] * 10 + [(MOUTH, True)] * 10 + [(AWAY, False)] * 15  # 1 s at the mouth, mouth open
    dev, spoken, wakes = device(db, clock, gesture)
    svc.tick()
    dev.poll_commands()
    assert not dev.holistic_mode and wakes == []  # 09:50: nothing yet
    clock.now = T0 - 4 * 60  # 09:56, window open
    svc.tick()
    dev.poll_commands()
    assert dev.holistic_mode and wakes == [1]
    clock.now = T0 + 30
    svc.tick()
    dev.poll_commands()
    assert any("đến giờ uống thuốc Amlodipin" in s for s in spoken)
    for _ in gesture:
        clock.now += 0.1
        dev.capture_step()
    dev.flush()
    assert state(db) == "CONFIRMED"
    svc.tick()
    dev.poll_commands()
    assert any("ghi nhận" in s and "uống thuốc" in s for s in spoken)
    clock.now = T0 + 11 * 60
    svc.tick()
    dev.poll_commands()
    assert not any("chưa uống" in s for s in spoken)
    assert wakes == [1]


def test_not_taken_after_ten_minutes_speaks_and_alerts_the_portal(env):
    db, svc, clock = env
    dev, spoken, wakes = device(db, clock)
    for minute in (-4, 0, 10.5):
        clock.now = T0 + minute * 60
        svc.tick()
        dev.poll_commands()
    assert any("Đến giờ uống thuốc rồi" in s and "chưa uống" in s for s in spoken)
    events = [r["event_type"] for r in db.fetchall("SELECT event_type FROM outbox_events WHERE event_type LIKE 'ai.medication.%'")]
    assert "ai.medication.not_taken" in events
    assert state(db) != "CONFIRMED"


def test_queue_survives_an_outage(env):
    db, svc, clock = env
    dev, _, _ = device(db, clock)

    class Down:
        def post(self, *a):
            raise ConnectionError("offline")

        def get(self, *a):
            raise ConnectionError("offline")

    real, dev.http = dev.http, Down()
    dev.capture_step()
    with pytest.raises(ConnectionError):
        dev.flush()
    assert len(dev._obs) == 1
    dev.http = real
    dev.flush()
    assert len(dev._obs) == 0
