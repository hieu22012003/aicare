"""Field report 09/11 (customer video test_Aicare.mp4)."""

from datetime import UTC, datetime, timedelta

import numpy as np
import pytest

from features.voice_assistant.voice_ai.audio import vi_tts
from features.web_portal.api_router import ApiRouter, _vn_now
from features.web_portal.auth import AuthService
from shared.ai_runtime.med_banner import banner_lines
from shared.database.connection import UnifiedDatabaseManager
from shared.database.migrator import Migrator


@pytest.fixture
def care(tmp_path):
    db = UnifiedDatabaseManager(tmp_path / "care.sqlite3")
    Migrator(db).apply_all()
    return db, ApiRouter(db, AuthService(db))


def _times_today(db):
    today = _vn_now().date().isoformat()
    return sorted(r["local_scheduled_time"][11:16] for r in db.fetchall(
        "SELECT local_scheduled_time FROM dose_occurrences WHERE local_scheduled_time LIKE ?", (today + "%",)))


def test_new_prescription_does_not_create_this_mornings_doses_as_missed(care):
    db, router = care
    now = _vn_now()
    past, future = (now - timedelta(hours=1)).strftime("%H:%M"), (now + timedelta(minutes=30)).strftime("%H:%M")
    if future < past:  # around midnight the "future" time is tomorrow
        pytest.skip("runs across midnight")
    body = {"start_date": now.date().isoformat(), "items": [{"medication_id": "med_001", "dose_amount": 1, "dose_unit": "viên",
            "instructions": "Sau ăn", "times_of_day": [past, future]}]}
    code, doc = router._admin_create_doctor({"full_name": "BS Test", "email": "bs@test.local", "password": "Mat-khau-dai-123",
                                             "patient_ids": ["patient_001"]}, "user_admin_001")
    assert code == 201, doc
    uid = db.fetchone("SELECT user_id FROM users WHERE email='bs@test.local'")["user_id"]
    code, res = router._create_prescription("patient_001", uid, body)
    assert code == 201, res
    assert _times_today(db) == [future]


def test_banner_while_window_open():
    t = datetime.now(UTC) + timedelta(minutes=3)

    class DB:
        def fetchall(self, q, a):
            return [{"name": "Amlor 5mg (Amlodipine)", "dose": "1 viên", "scheduled_at_utc": t.isoformat(),
                     "local_scheduled_time": "2026-11-09T21:23:00", "state": "PENDING"}]

    assert banner_lines(DB(), "p", False) is None
    lines = banner_lines(DB(), "p_banner", True)
    assert lines[0].startswith("ĐANG GIÁM SÁT UỐNG THUỐC") and "21:23" in lines[0] and "Amlor 5mg" in lines[0]
    assert "phút đến giờ uống" in lines[1]
    live = banner_lines(DB(), "p_banner2", True, live={"hand_at_mouth": True, "mouth_open": False})
    assert live[1].startswith("Đã thấy tay đưa lên miệng")


def test_vietnamese_speech_text_and_voice_choice():
    assert vi_tts.normalize("Amlor 5mg 1.0 viên lúc 21:23") == \
        "Amlor năm mi li gam một viên lúc hai mươi mốt giờ hai mươi ba phút"
    assert vi_tts.read_number(2026) == "hai nghìn không trăm hai mươi sáu"
    assert vi_tts.read_number(105) == "một trăm lẻ năm" and vi_tts.read_number(21) == "hai mươi mốt"

    class V:
        def __init__(self, i, n):
            self.id, self.name, self.languages = i, n, []

    david = V(r"HKEY\...\TTS_MS_EN-US_DAVID_11.0", "Microsoft David Desktop - English (United States)")
    an = V(r"HKEY\...\TTS_MS_VI-VN_AN_11.0", "Microsoft An - Vietnamese (Vietnam)")
    assert not vi_tts.is_vietnamese_voice(david)  # the old picker chose this one: "david" contains "vi"
    assert vi_tts.is_vietnamese_voice(an)


def test_bundled_vietnamese_voice_synthesizes(tmp_path):
    if vi_tts._model_dir() is None:
        pytest.skip("voice model not present")
    pytest.importorskip("sherpa_onnx")
    out = tmp_path / "x.wav"
    assert vi_tts.synthesize("Bác ơi, đến giờ uống thuốc rồi.", str(out))
    assert out.stat().st_size > 20000


def test_weak_sad_is_shown_as_neutral():
    from features.emotion.emotion_adapter import EmotionAdapter

    e = EmotionAdapter.__new__(EmotionAdapter)
    e.face_ttl = 1.2
    e._ema, e._ema_ts, e._stable_idx, e._challenger, e._challenger_since = None, 0.0, None, None, 0.0
    shown = [e._stable_label(np.array([0.31, 0.30, 0.39]), t / 30) for t in range(60)]
    assert set(shown) == {0}
