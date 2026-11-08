"""Identity tracking of FaceAdapter.recognize_face (field report 08/11: the registered patient flickered
between "nguyen van a", "Đang xác minh" and "Người lạ" several times a second, and medication intake was
never watched because the AI core only processed frames while the face was 'recognized').

The per-frame matcher is stubbed: these tests pin the decision logic, not the model.
"""

import numpy as np
import pytest

import features.face_monitoring.face_adapter as fam
from features.face_monitoring.face_adapter import FaceAdapter

BOX = (200, 120, 120, 150, 0.99)
FRAME = np.zeros((480, 640, 3), np.uint8)


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


@pytest.fixture
def fa(tmp_path, monkeypatch):
    clock = Clock()
    monkeypatch.setattr(fam.time, "time", clock)
    a = FaceAdapter(data_dir=tmp_path / "d", users_dir=tmp_path / "u")
    a._is_trained = True
    a.clock = clock
    a.verdicts = []
    a.boxes = []

    def verdict(frame, box, now):
        v = a.verdicts.pop(0)
        return (v, "1", {"match": 0.8, "unsure": 0.35, "reject": 0.1}[v], 20.0)

    monkeypatch.setattr(a, "_frame_verdict", verdict)
    monkeypatch.setattr(a, "detect_face", lambda frame, *k, **kw: a.boxes.pop(0) if a.boxes else BOX)
    return a


def feed(fa, verdicts, dt=1 / 15):
    out = []
    for v in verdicts:
        fa.clock.t += dt
        fa.verdicts.append(v)
        out.append(fa.recognize_face(FRAME)["status"])
    return out


def test_occluded_frames_keep_the_patient(fa):
    """A hand over the mouth (pill) makes frames 'unsure': the patient stays recognized."""
    assert feed(fa, ["match"]) == ["recognized"]
    assert set(feed(fa, ["unsure"] * 60)) == {"recognized"}  # 4 s of occlusion


def test_isolated_bad_frames_never_make_a_stranger(fa):
    feed(fa, ["match"])
    seq = (["reject"] + ["unsure"] * 3 + ["match"]) * 20
    assert "unknown" not in feed(fa, seq)


def test_someone_else_is_reported_after_sustained_rejects(fa):
    feed(fa, ["match"])
    out = feed(fa, ["reject"] * 40)  # ~2.7 s of a clearly different face
    assert out[0] == "recognized"
    assert out[-1] == "unknown"
    assert fa.present_patient(30) is None  # the AI core stops treating frames as the patient


def test_new_face_is_not_called_stranger_on_the_first_frames(fa):
    out = feed(fa, ["unsure", "unsure", "reject", "unsure"])
    assert "unknown" not in out and out[-1] == "uncertain"


def test_identity_needs_a_fresh_match_after_the_hold(fa):
    feed(fa, ["match"])
    fa.clock.t += fam.IDENTITY_HOLD_SECONDS + 1
    assert feed(fa, ["unsure"]) == ["uncertain"]
    assert feed(fa, ["match"]) == ["recognized"]


def test_face_jumping_elsewhere_is_rechecked(fa):
    feed(fa, ["match"])
    fa.boxes.append((520, 300, 60, 70, 0.9))  # a different, small face on the other side of the frame
    assert feed(fa, ["unsure"]) == ["uncertain"]


def test_present_patient_survives_a_hidden_face(fa):
    feed(fa, ["match"])
    fa.clock.t += 5
    assert fa.present_patient(30) == "1"
    fa.clock.t += 40
    assert fa.present_patient(30) is None


def test_emotion_label_does_not_flicker():
    """Field video: the emotion badge changed Vui vẻ/Bình thường/Buồn bã several times a second."""
    from features.emotion.emotion_adapter import EmotionAdapter

    e = EmotionAdapter.__new__(EmotionAdapter)
    e.face_ttl = 1.2
    e._ema, e._ema_ts, e._stable_idx, e._challenger, e._challenger_since = None, 0.0, None, None, 0.0
    t, shown = 0.0, []
    rng = np.random.default_rng(0)
    for i in range(300):  # 10 s at 30 fps of a resting face with noisy per-frame scores
        t += 1 / 30
        p = np.array([0.45, 0.30, 0.25]) + rng.normal(0, 0.15, 3)
        p = np.clip(p, 0.01, None)
        shown.append(e._stable_label(e._smooth(p / p.sum(), t), t))
    changes = sum(a != b for a, b in zip(shown, shown[1:]))
    assert changes <= 1 and shown[-1] == 0
    for i in range(90):  # then a clear smile for 3 s
        t += 1 / 30
        shown.append(e._stable_label(e._smooth(np.array([0.15, 0.8, 0.05]), t), t))
    assert shown[-1] == 1
