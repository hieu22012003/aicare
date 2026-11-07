"""Home app <-> Render: medication plan down, home-only data and dose buttons up."""

import json
from datetime import datetime, timedelta, timezone

import pytest

from features.medication.medication_schedule_sync import ensure_medication_occurrences
from shared.ai_runtime.device_api import handle_device_route
from shared.ai_runtime.replication import apply_from_device, apply_snapshot, collect_for_server, snapshot_for_device
from shared.database.connection import UnifiedDatabaseManager
from shared.database.migrator import Migrator

PID = "patient_001"
UTC = timezone.utc
NOW = datetime(2026, 11, 7, 1, 0, tzinfo=UTC)  # 08:00 in Hà Nội


def fresh():
    db = UnifiedDatabaseManager(":memory:")
    Migrator(db).apply_all()
    return db


def web_plan(db):
    """What the caregiver creates on the web: one pill at 10:00 every day."""
    today = NOW.date().isoformat()
    db.execute("INSERT INTO medications (medication_id, brand_name, product_strength) VALUES ('med_a', 'Amlodipin', '5 mg')")
    db.execute("INSERT INTO prescriptions (prescription_id, patient_id, entered_by_user_id, prescribed_date, valid_from, status) "
               "VALUES ('rx_1', ?, 'user_admin_001', ?, ?, 'ACTIVE')", (PID, today, today))
    db.execute("INSERT INTO prescription_items (prescription_item_id, prescription_id, medication_id, dose_amount, dose_unit, "
               "original_instructions, start_date, status) VALUES ('pi_1', 'rx_1', 'med_a', 1, 'viên', 'Sau ăn', ?, 'ACTIVE')", (today,))
    db.execute("INSERT INTO medication_schedules (schedule_id, revision, prescription_item_id, local_times, days_of_week, "
               "effective_from, timezone, is_active) VALUES ('sch_1', 1, 'pi_1', '[\"10:00\"]', '[0,1,2,3,4,5,6]', ?, "
               "'Asia/Ho_Chi_Minh', 1)", ((NOW - timedelta(days=1)).isoformat(),))
    ensure_medication_occurrences(db, PID, now=NOW, days=2)


def dose_today(db):
    return db.fetchone("SELECT * FROM dose_occurrences WHERE patient_id=? AND local_scheduled_time LIKE '2026-11-07T10:00%'", (PID,))


def test_plan_reaches_home_and_home_button_reaches_server():
    server, home = fresh(), fresh()
    web_plan(server)
    counts = apply_snapshot(home, snapshot_for_device(server, PID, NOW), PID)
    assert counts["medication_schedules"] == 1 and counts["dose_occurrences"] >= 2
    assert dose_today(home)["occurrence_id"] == dose_today(server)["occurrence_id"]
    # home scheduler sees the copied doses and creates no duplicates
    ensure_medication_occurrences(home, PID, now=NOW, days=2)
    assert len(home.fetchall("SELECT 1 FROM dose_occurrences WHERE local_scheduled_time LIKE '2026-11-07T10:00%'")) == 1
    # elder presses "Đã uống" at home
    taken = (NOW + timedelta(hours=2, minutes=3)).isoformat()
    home.execute("UPDATE dose_occurrences SET state='CONFIRMED', action_taken_at_utc=?, action_actor='elder_button' WHERE occurrence_id=?",
                 (taken, dose_today(home)["occurrence_id"]))
    sent = {}
    up = collect_for_server(home, PID, sent=sent, now=NOW + timedelta(hours=3))
    assert len(up["dose_actions"]) == 1
    res = apply_from_device(server, PID, up)
    assert res["dose_actions_applied"] == 1
    srv = dose_today(server)
    assert srv["state"] == "CONFIRMED" and srv["action_actor"] == "elder_button"
    # nothing new to send the second time
    assert collect_for_server(home, PID, sent=sent, now=NOW + timedelta(hours=3))["dose_actions"] == []
    # the next snapshot keeps the home state
    apply_snapshot(home, snapshot_for_device(server, PID, NOW), PID)
    assert dose_today(home)["state"] == "CONFIRMED"


def test_locally_generated_twin_takes_the_server_id_and_keeps_its_state():
    server, home = fresh(), fresh()
    web_plan(server)
    snap = snapshot_for_device(server, PID, NOW)
    # home got the plan earlier (without doses) and generated its own ids, then the elder confirmed
    apply_snapshot(home, {**snap, "dose_occurrences": []}, PID)
    ensure_medication_occurrences(home, PID, now=NOW, days=2)
    local = dose_today(home)
    assert local["occurrence_id"] != dose_today(server)["occurrence_id"]
    home.execute("UPDATE dose_occurrences SET state='CONFIRMED', action_taken_at_utc=?, action_actor='elder_button' WHERE occurrence_id=?",
                 (NOW.isoformat(), local["occurrence_id"]))
    home.execute("INSERT INTO dose_events (event_id, occurrence_id, event_type, actor_id, actor_role, timestamp_utc, idempotency_key) "
                 "VALUES ('ev1', ?, 'resolved', 'u', 'patient', ?, 'k1')", (local["occurrence_id"], NOW.isoformat()))
    apply_snapshot(home, snap, PID)
    merged = dose_today(home)
    assert merged["occurrence_id"] == dose_today(server)["occurrence_id"] and merged["state"] == "CONFIRMED"
    assert home.fetchone("SELECT occurrence_id FROM dose_events WHERE event_id='ev1'")["occurrence_id"] == merged["occurrence_id"]
    assert home.fetchone("PRAGMA foreign_key_check") is None


def test_home_rows_go_up_and_foreign_patients_are_refused():
    server, home = fresh(), fresh()
    at = NOW.isoformat()
    home.execute("INSERT INTO emotion_summaries_v2 VALUES ('es1', ?, ?, ?, ?, ?, 'Sad', 'Sad', 'window', '{}', 3600, 0)", (PID, at, at, at, at))
    home.execute("INSERT INTO ai_face_sightings (sighting_id, patient_id, local_date, who, first_seen_utc, last_seen_utc) "
                 "VALUES ('f1', ?, '2026-11-07', 'patient', ?, ?)", (PID, at, at))
    up = collect_for_server(home, PID, now=NOW)
    assert set(up["rows"]) == {"emotion_summaries_v2", "ai_face_sightings"}
    up["rows"]["emotion_summaries_v2"].append({**up["rows"]["emotion_summaries_v2"][0], "summary_id": "evil", "patient_id": "patient_002"})
    res = apply_from_device(server, PID, up)
    assert res["stored"] == {"emotion_summaries_v2": 1, "ai_face_sightings": 1} and res["refused"] == 1
    assert server.fetchone("SELECT label FROM emotion_summaries_v2 WHERE summary_id='es1'")["label"] == "Sad"


def test_device_routes(monkeypatch):
    key = "device-key-replication-0123"
    monkeypatch.setenv("AICARE_DEVICE_KEYS", f"{key}:{PID}")
    server = fresh()
    web_plan(server)
    h = {"X-Device-Key": key}
    code, snap = handle_device_route(server, "GET", f"/api/device/patients/{PID}/snapshot", {}, None, h)
    assert code == 200 and snap["medication_schedules"]
    code, res = handle_device_route(server, "POST", f"/api/device/patients/{PID}/sync", {}, {"rows": {}, "dose_actions": []}, h)
    assert code == 200 and res["dose_actions_applied"] == 0
    assert handle_device_route(server, "GET", f"/api/device/patients/{PID}/music/nope", {}, None, h)[0] == 404
    assert handle_device_route(server, "GET", f"/api/device/patients/{PID}/snapshot", {}, None, {"X-Device-Key": "wrong-key-0000000000"})[0] == 401


def test_exercise_session_reaches_the_server_with_its_default_plan():
    from features.exercise_rehab.rehab_session_adapter import ensure_default_rehab_plans

    server, home = fresh(), fresh()
    with home.connect() as conn:
        plans = ensure_default_rehab_plans(conn, PID)
    plan = plans[0]
    home.execute("INSERT INTO rehab_sessions (session_id, patient_id, plan_id, plan_version, local_date, started_at, ended_at, "
                 "target_count_snapshot, verified_count, status) VALUES ('rs1', ?, ?, 1, '2026-11-07', ?, ?, 10, 10, "
                 "'camera_verified_completed')", (PID, plan["plan_id"], NOW.isoformat(), (NOW + timedelta(minutes=5)).isoformat()))
    res = apply_from_device(server, PID, collect_for_server(home, PID, now=NOW + timedelta(minutes=6)))
    assert res["stored"].get("rehab_sessions") == 1 and res["stored"].get("rehab_plans", 0) >= 1
    # the web changes the target; a later resend from home must not undo it
    server.execute("UPDATE rehab_plans SET target_count=20 WHERE plan_id=?", (plan["plan_id"],))
    apply_from_device(server, PID, collect_for_server(home, PID, now=NOW + timedelta(minutes=6)))
    assert server.fetchone("SELECT target_count FROM rehab_plans WHERE plan_id=?", (plan["plan_id"],))["target_count"] == 20
    apply_snapshot(home, snapshot_for_device(server, PID, NOW), PID)
    assert home.fetchone("SELECT target_count FROM rehab_plans WHERE plan_id=?", (plan["plan_id"],))["target_count"] == 20


def test_home_session_replaces_the_web_placeholder_for_the_same_day():
    from features.exercise_rehab.rehab_session_adapter import ensure_default_rehab_plans

    server, home = fresh(), fresh()
    with home.connect() as conn:
        plan = ensure_default_rehab_plans(conn, PID)[0]
    with server.connect() as conn:
        ensure_default_rehab_plans(conn, PID)
    # opening the web dashboard makes a pending session for today under the web's own id
    server.execute("INSERT INTO rehab_sessions (session_id, patient_id, plan_id, plan_version, local_date, "
                   "target_count_snapshot, status) VALUES ('rs_web', ?, ?, 1, '2026-11-07', 10, 'pending')", (PID, plan["plan_id"]))
    home.execute("INSERT INTO rehab_sessions (session_id, patient_id, plan_id, plan_version, local_date, started_at, ended_at, "
                 "target_count_snapshot, verified_count, status) VALUES ('rs_home', ?, ?, 1, '2026-11-07', ?, ?, 10, 10, "
                 "'camera_verified_completed')", (PID, plan["plan_id"], NOW.isoformat(), (NOW + timedelta(minutes=5)).isoformat()))
    res = apply_from_device(server, PID, collect_for_server(home, PID, now=NOW + timedelta(minutes=6)))
    assert res["stored"].get("rehab_sessions") == 1
    rows = server.fetchall("SELECT session_id, status, verified_count FROM rehab_sessions WHERE plan_id=?", (plan["plan_id"],))
    assert [(r["session_id"], r["status"], r["verified_count"]) for r in rows] == [("rs_home", "camera_verified_completed", 10)]
