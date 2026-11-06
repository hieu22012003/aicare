import numpy as np
import pytest

from src.config import INPUT_SIZE
from utils.preprocess import normalize_face_landmarks, validate_feature_vector


def make_landmarks():
    coords = np.zeros((468, 3), dtype=np.float32)
    for index in range(coords.shape[0]):
        coords[index] = [index / 468.0, index / 936.0, index / 1404.0]
    coords[1] = [0.5, 0.5, 0.0]
    coords[33] = [0.3, 0.5, 0.0]
    coords[263] = [0.7, 0.5, 0.0]
    return coords


def test_normalize_face_landmarks_returns_face_only_features():
    features = normalize_face_landmarks(make_landmarks())
    assert features is not None
    assert len(features) == INPUT_SIZE


def test_normalization_is_translation_invariant():
    original = make_landmarks()
    shifted = original + np.array([10.0, -4.0, 2.0], dtype=np.float32)
    np.testing.assert_allclose(
        normalize_face_landmarks(original),
        normalize_face_landmarks(shifted),
        rtol=1e-5,
        atol=1e-5,
    )


def test_missing_or_invalid_face_returns_none():
    assert normalize_face_landmarks(None) is None
    assert normalize_face_landmarks(np.zeros((10, 3), dtype=np.float32)) is None


def test_validate_feature_vector_rejects_wrong_shape():
    with pytest.raises(ValueError):
        validate_feature_vector([0.0, 1.0])
