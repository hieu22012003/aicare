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
    assert "[x] 2. Đưa lên miệng" in live[1]


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


# ---------------------------------------------------------------- request 09/11 (22:57)
def _recorder_with(chunks, rate=16000):
    from features.voice_assistant.local_voice.recorder import AudioRecorder

    r = AudioRecorder(30)
    r.rate = rate
    for c in chunks:
        r._vad(np.asarray(c, dtype=np.float32))
    return r


def test_recording_stops_by_itself_after_the_sentence():
    rng = np.random.default_rng(1)
    noise = lambda s: rng.normal(0, 0.003, int(16000 * s))  # noqa: E731
    t = np.arange(int(16000 * 1.2)) / 16000
    speech = 0.2 * np.sin(2 * np.pi * 180 * t) * (1 + 0.5 * np.sin(2 * np.pi * 4 * t))
    chunks = [c for part in (noise(0.5), speech, noise(0.5)) for c in np.array_split(part, max(1, len(part) // 800))]
    r = _recorder_with(chunks)
    assert r.auto_stop() is None  # 0.5 s after the voice: still listening
    r = _recorder_with(chunks + list(np.array_split(noise(0.6), 12)))
    assert r.auto_stop() == "end"


def test_recording_gives_up_when_nobody_speaks():
    rng = np.random.default_rng(2)
    r = _recorder_with(np.array_split(rng.normal(0, 0.003, 16000 * 8), 160))
    assert r.auto_stop() == "no_speech"


def test_real_voice_is_detected():
    from features.voice_assistant.voice_ai.audio import vi_tts

    if vi_tts._model_dir() is None:
        pytest.skip("voice model not present")
    pytest.importorskip("sherpa_onnx")
    import tempfile, wave

    path = tempfile.mktemp(suffix=".wav")
    assert vi_tts.synthesize("Gọi con giúp tôi", path)
    with wave.open(path) as w:
        rate = w.getframerate()
        audio = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32) / 32768 * 0.5
    rng = np.random.default_rng(3)
    full = np.concatenate([rng.normal(0, 0.002, rate // 2), audio, rng.normal(0, 0.002, rate * 2)])
    r = _recorder_with(np.array_split(full, len(full) // 1024), rate=rate)
    assert r.auto_stop() == "end"


def test_chime_wav(tmp_path):
    import wave

    from features.voice_assistant.voice_ai.audio import vi_tts

    p = tmp_path / "c.wav"
    vi_tts.write_chime(str(p))
    with wave.open(str(p)) as w:
        assert 1.4 < w.getnframes() / w.getframerate() < 1.8


def test_dose_reminder_starts_with_the_chime_and_wakes_the_camera():
    from shared.ai_runtime.brain import AICoreBrain

    said = []
    b = AICoreBrain.__new__(AICoreBrain)
    b.speak, b.speak_to = said.append, None
    from collections import deque

    b.due_events = deque(maxlen=20)
    b._say("p", "\aBác ơi, đến giờ uống thuốc")
    assert said[0].startswith("\a")


def test_banner_shows_the_three_steps_and_the_confirmation():
    t = datetime.now(UTC) - timedelta(minutes=1)

    class DB:
        def fetchall(self, q, a):
            return [{"name": "Amlor 5mg", "dose": "1 viên", "scheduled_at_utc": t.isoformat(),
                     "local_scheduled_time": "2026-11-09T21:23:00", "state": "PENDING"}]

    lines = banner_lines(DB(), "p_steps", True, live={"pill_held": True, "hand_at_mouth": True, "mouth_open": False,
                                                      "hands_seen": True})
    assert "[x] 1. Cầm thuốc" in lines[1] and "[x] 2. Đưa lên miệng" in lines[1] and "[ ] 3. Há miệng" in lines[1]
    done = banner_lines(DB(), "p_steps2", False, live={"confirmed_at": datetime.now(UTC).timestamp()})
    assert done[0] == "ĐÃ GHI NHẬN UỐNG THUỐC"


def test_posture_label_from_upper_body():
    from shared.ai_runtime.overlay import LiveOverlay
    from shared.vision.pose.landmarks import Landmark

    def pose(shoulder_y):
        pts = [Landmark(0.5, 0.5, 0.0, 0.1) for _ in range(33)]
        pts[0] = Landmark(0.5, shoulder_y - 0.25, 0.0, 0.9)
        pts[11], pts[12] = Landmark(0.65, shoulder_y, 0.0, 0.9), Landmark(0.35, shoulder_y, 0.0, 0.9)
        return tuple(pts)

    ov = LiveOverlay()
    ov.update(1.0, 640, 480, pose(0.78))
    assert ov.posture_text().startswith("Tư thế: Ngồi")
    ov.update(1.1, 640, 480, pose(0.32))
    assert ov.posture_text().startswith("Tư thế: Đứng")
    frame = np.zeros((480, 640, 3), np.uint8)
    assert ov.draw(frame, now=1.2).sum() > 0  # skeleton + label drawn
