from types import SimpleNamespace
import numpy as np

def test_sad_sensitivity_is_slightly_higher_without_changing_resting_face():
    from features.emotion.emotion_adapter import EmotionAdapter
    def face(corner_y):
        landmarks = [SimpleNamespace(x=0., y=0., z=0.) for _ in range(468)]
        for index, xy in {33: (0., 0.), 263: (1., 0.), 61: (.3, corner_y),
                          291: (.7, corner_y), 13: (.5, 1.), 14: (.5, 1.),
                          107: (.3, -.2), 336: (.7, -.2)}.items():
            landmarks[index] = SimpleNamespace(x=xy[0], y=xy[1], z=0.)
        return landmarks
    adapter = EmotionAdapter.__new__(EmotionAdapter)
    adapter.sad_sensitivity = 1.0
    near_boundary = face(1.0 + .010 + .59 / 45.)
    baseline = adapter._compute_facs_probabilities(near_boundary)
    adapter.sad_sensitivity = 1.10
    adjusted = adapter._compute_facs_probabilities(near_boundary)
    assert np.argmax(baseline) == 0
    assert np.argmax(adjusted) == 2
    assert np.argmax(adapter._compute_facs_probabilities(face(1.0))) == 0
    assert np.argmax(adapter._compute_facs_probabilities(face(.9))) == 1

