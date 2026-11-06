from __future__ import annotations

from typing import Iterable, Sequence

import numpy as np

from src.config import FEATURE_COLUMNS, SELECTED_FACE_LANDMARKS


def _as_landmark_array(face_landmarks: object) -> np.ndarray:
    """Convert MediaPipe landmarks or raw coordinate sequences to an array."""
    if hasattr(face_landmarks, "landmark"):
        coords = [[lm.x, lm.y, lm.z] for lm in face_landmarks.landmark]
        return np.asarray(coords, dtype=np.float32)
    elif isinstance(face_landmarks, (list, tuple)) and len(face_landmarks) > 0 and hasattr(face_landmarks[0], "x"):
        coords = [[lm.x, lm.y, lm.z] for lm in face_landmarks]
        return np.asarray(coords, dtype=np.float32)

    coords = np.asarray(face_landmarks, dtype=np.float32)
    if coords.ndim == 1:
        coords = coords.reshape(-1, 3)
    return coords


def normalize_face_landmarks(face_landmarks: object | None) -> list[float] | None:
    """Return normalized selected FaceMesh coordinates, or None if invalid."""
    if face_landmarks is None:
        return None

    full_coords = _as_landmark_array(face_landmarks)
    if full_coords.ndim != 2 or full_coords.shape[1] != 3:
        return None
    if full_coords.shape[0] <= max(SELECTED_FACE_LANDMARKS):
        return None

    selected = full_coords[SELECTED_FACE_LANDMARKS].astype(np.float32)
    if not np.all(np.isfinite(selected)):
        return None

    left_eye_outer = full_coords[33]
    right_eye_outer = full_coords[263]
    eye_distance = float(np.linalg.norm(right_eye_outer - left_eye_outer))
    if not np.isfinite(eye_distance) or eye_distance < 1e-6:
        return None

    center = full_coords[1]
    normalized = (selected - center) / eye_distance
    return normalized.flatten().astype(float).tolist()


def feature_dict(features: Sequence[float]) -> dict[str, float]:
    if len(features) != len(FEATURE_COLUMNS):
        raise ValueError(f"Expected {len(FEATURE_COLUMNS)} features, got {len(features)}")
    return dict(zip(FEATURE_COLUMNS, features))


def extract_face_features(face_landmarks: object | None) -> dict[str, float] | None:
    features = normalize_face_landmarks(face_landmarks)
    if features is None:
        return None
    return feature_dict(features)


def validate_feature_vector(features: Iterable[float]) -> np.ndarray:
    vector = np.asarray(list(features), dtype=np.float32)
    if vector.shape != (len(FEATURE_COLUMNS),):
        raise ValueError(f"Expected feature vector shape {(len(FEATURE_COLUMNS),)}, got {vector.shape}")
    if not np.all(np.isfinite(vector)):
        raise ValueError("Feature vector contains non-finite values")
    return vector
