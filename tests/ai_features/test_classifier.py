import pytest

from ai_core.posture_activity_monitoring.domain import Observation, Posture
from ai_core.posture_activity_monitoring.engine.classifier import classify
from ai_core.posture_activity_monitoring.engine.geometry import compute_features
from tests.ai_features.synth import landmarks, obs


@pytest.mark.parametrize(
    ("kind", "expected"),
    [
        ("standing", Posture.STANDING),
        ("sitting", Posture.SITTING),
        ("sitting_front", Posture.SITTING),
        ("lying", Posture.LYING),
        ("bending", Posture.BENDING),
    ],
)
def test_classify_synthetic_poses(kind, expected):
    features = compute_features(obs(0.0, kind))
    assert features is not None
    assert classify(features) == expected


def test_no_person_gives_no_features():
    assert compute_features(Observation(ts=0.0, landmarks=None)) is None


def test_hidden_legs_is_ambiguous():
    pts = list(landmarks("standing"))
    for i in (25, 26, 27, 28):
        pts[i] = type(pts[i])(pts[i].x, pts[i].y, 0.0, 0.1)
    features = compute_features(Observation(ts=0.0, landmarks=tuple(pts)))
    assert features is not None and not features.legs_visible
    assert classify(features) is None


def test_hidden_torso_gives_no_features():
    pts = list(landmarks("standing"))
    for i in (11, 12, 23, 24):
        pts[i] = type(pts[i])(pts[i].x, pts[i].y, 0.0, 0.1)
    assert compute_features(Observation(ts=0.0, landmarks=tuple(pts))) is None


def _features(**kw):
    from ai_core.posture_activity_monitoring.engine.geometry import BodyFeatures

    base = dict(torso_px=100.0, torso_orientation=90.0, hip_norm=(0.5, 0.5), hip_px=(320.0, 240.0), legs_visible=True)
    base.update(kw)
    return BodyFeatures(**base)


def test_real_footage_measurements():
    """Feature values measured with MediaPipe on a real fall clip (HumanFallDetection example)."""
    standing = _features(torso_angle=3, knee_angle=180, thigh_angle=82, thigh_ratio=0.70, aspect_ratio=0.20)
    on_the_way_down = _features(torso_angle=41, knee_angle=161, thigh_angle=3, thigh_ratio=0.03, aspect_ratio=2.14)
    assert classify(standing) == Posture.STANDING
    assert classify(on_the_way_down) == Posture.LYING
