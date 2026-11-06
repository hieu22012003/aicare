"""Caregiver home page data (/api/patients/{id}/ai/daily)."""

from datetime import datetime, timezone

from shared.ai_runtime.brain import AICoreBrain
from shared.ai_runtime.daily import daily_summary
from shared.ai_runtime.store import AICoreStore
from shared.ai_runtime.web_routes import handle_ai_route
from shared.database.connection import UnifiedDatabaseManager
from shared.database.migrator import Migrator
from tests.ai_features.test_brain_integration import T0, add_dose, Clock

PID = "pat_ai_1"
UTC = timezone.utc


def setup():
    db = UnifiedDatabaseManager(":memory:")
    Migrator(db).apply_all()
    db.execute("INSERT INTO patients(patient_id, full_name, date_of_birth) VALUES (?, ?, '1954-03-02')", (PID, "Nguyễn Văn A"))
    return db


def test_empty_patient_has_a_complete_shape():
    db = setup()
    d = daily_summary(db, PID, now=datetime.fromtimestamp(T0, UTC))
    assert d["profile"]["full_name"] == "Nguyễn Văn A" and d["profile"]["age"] == 72
    assert d["current"]["alert"]["label"] == "Bình thường"
    assert [x["label"] for x in d["stats_24h"]["items"]] == ["Nằm", "Ngồi", "Đứng/Di chuyển", "Vận động"]
    assert d["timeline"] == [] and d["alerts"] == []


def test_missed_dose_and_sitting_show_up_on_the_home_page():
    db = setup()
    add_dose(db)
    clock = Clock(T0 - 600)
    spoken = []
    brain = AICoreBrain(AICoreStore(db), spoken.append, clock)
    for minute in (-6, -4, 0, 11):
        clock.now = T0 + minute * 60
        brain.tick(home_patient=PID)
    sit_from = datetime.fromtimestamp(T0 + 700, UTC)
    db.execute("INSERT INTO ai_posture_segments VALUES ('seg1', ?, 'SITTING', 'CHAIR', ?, ?, 1500, 0.9)",
               (PID, sit_from.isoformat(), datetime.fromtimestamp(T0 + 2200, UTC).isoformat()))
    d = daily_summary(db, PID, now=datetime.fromtimestamp(T0 + 2400, UTC))
    texts = [i["text"] for i in d["timeline"]]
    assert any("Nhắc uống thuốc Amlodipin" in t for t in texts)
    assert any("chưa uống thuốc Amlodipin" in t for t in texts)
    assert d["timeline"][0]["time"] == "09:55"  # local time: camera window opens 5 minutes before 10:00
    kinds = [a["kind"] for a in d["alerts"]]
    assert "medication" in kinds and d["current"]["alert"]["count"] >= 1
    sitting = next(x for x in d["stats_24h"]["items"] if x["key"] == "sitting")
    assert sitting["seconds"] == 1500 and sitting["percent"] == 100
    assert any(t.startswith("Ngồi trên ghế (25 phút)") for t in texts)


def test_route_and_bad_date():
    db = setup()
    code, data = handle_ai_route(db, None, "GET", ["daily"], {"date": "2026-11-05"}, None, PID, "u")
    assert code == 200 and data["date"] == "2026-11-05"
    assert handle_ai_route(db, None, "GET", ["daily"], {"date": "05/11"}, None, PID, "u")[0] == 400
