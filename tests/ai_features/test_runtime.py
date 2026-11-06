"""Worker loop with a fake camera: real MediaPipe on blank frames, simulated frames ignored."""

from datetime import datetime, timedelta, timezone

import numpy as np

from shared.ai_runtime.app_hooks import voice_reply
from shared.ai_runtime.runtime import AICoreRuntime, enabled
from shared.database.connection import UnifiedDatabaseManager
from shared.database.migrator import Migrator

PID = "pat_rt_1"
UTC = timezone.utc


class FakeCamera:
    def __init__(self, valid=True):
        self.valid = valid
        self.pts = 0.0

    def get_capture_packet(self):
        self.pts += 0.1
        return np.zeros((240, 320, 3), np.uint8), self.pts, self.valid


def make(valid=True, now=None):
    db = UnifiedDatabaseManager(":memory:")
    Migrator(db).apply_all()
    db.execute("INSERT INTO patients(patient_id, full_name) VALUES (?, ?)", (PID, "Cụ RT"))
    spoken = []
    clock = (lambda: now) if now else None
    kw = {"clock": clock} if clock else {}
    rt = AICoreRuntime(db, FakeCamera(valid), lambda: (PID, True), spoken.append, **kw)
    return db, rt, spoken


def test_enabled_flag(monkeypatch):
    monkeypatch.setenv("AI_CARE_TEST_MODE", "1")
    monkeypatch.delenv("AI_CORE_ENABLED", raising=False)
    assert not enabled()
    monkeypatch.setenv("AI_CORE_ENABLED", "1")
    assert enabled()


def test_step_runs_pose_and_skips_simulated_frames():
    db, rt, _ = make(valid=False)
    rt.step()
    assert rt._pose is None  # simulated frame: no model loaded
    db, rt, _ = make(valid=True)
    rt.step()
    rt.step()
    assert rt._pose is not None and rt._holistic is None
    rt.stop()


def test_medication_window_switches_to_holistic():
    now = datetime.now(UTC)
    db, rt, spoken = make(valid=True, now=now.timestamp())
    due = now + timedelta(minutes=2)
    db.execute(
        "INSERT INTO dose_occurrences (occurrence_id, schedule_id, revision, scheduled_at_utc, local_scheduled_time, "
        "grace_deadline_utc, state, medication_name_snapshot, dose_snapshot, instructions_snapshot, patient_id) "
        "VALUES ('occ_rt', 'sch', 1, ?, ?, ?, 'PENDING', 'Metformin', '1 viên', '', ?)",
        (due.isoformat(), due.replace(tzinfo=None).isoformat(), (due + timedelta(hours=1)).isoformat(), PID))
    rt.step()  # tick opens the window
    assert rt.brain.medication_active(PID)
    rt._last_tick = 0
    rt.step()
    assert rt._holistic is not None
    rt.stop()


def test_voice_reply_without_runtime_is_none():
    assert voice_reply(PID, "kính của tôi đâu") is None


def test_medication_window_switches_the_camera_on():
    from datetime import datetime, timedelta, timezone

    import numpy as np

    from shared.ai_runtime.runtime import AICoreRuntime
    from shared.database.connection import UnifiedDatabaseManager
    from shared.database.migrator import Migrator

    utc = timezone.utc
    t0 = datetime(2026, 11, 7, 3, 0, tzinfo=utc).timestamp()
    db = UnifiedDatabaseManager(":memory:")
    Migrator(db).apply_all()
    db.execute("INSERT INTO patients(patient_id, full_name) VALUES ('pat_w', 'Cụ W')")
    due = datetime.fromtimestamp(t0, utc)
    db.execute(
        "INSERT INTO dose_occurrences (occurrence_id, schedule_id, revision, scheduled_at_utc, local_scheduled_time, "
        "grace_deadline_utc, state, medication_name_snapshot, dose_snapshot, instructions_snapshot, patient_id) "
        "VALUES ('occ_w', 'sch_w', 1, ?, ?, ?, 'PENDING', 'Aspirin', '1 viên', '', 'pat_w')",
        (due.isoformat(), due.replace(tzinfo=None).isoformat(), (due + timedelta(hours=1)).isoformat()))

    class StoppedCamera:  # camera off: no frames at all
        def get_capture_packet(self):
            return None, None, False

    clock = {"now": t0 - 600}
    wakes = []
    rt = AICoreRuntime(db, StoppedCamera(), lambda: ("pat_w", False), lambda s: None,
                       wake_camera=lambda: wakes.append(clock["now"]), clock=lambda: clock["now"])
    rt.step()
    assert wakes == []
    clock["now"] = t0 - 240  # 4 minutes before: window open
    rt.step()
    clock["now"] += 2
    rt.step()
    assert wakes == [t0 - 240]  # once per window
    assert np is not None
