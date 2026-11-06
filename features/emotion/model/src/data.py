from __future__ import annotations

import json
import random
from dataclasses import asdict
from pathlib import Path

import numpy as np
import pandas as pd

from src.config import EXPRESSION_CLASSES, FEATURE_COLUMNS, PATHS, TRAINING


REQUIRED_COLUMNS = ["session_id", "sample_id", "timestamp_utc", "subject_id", "label"] + FEATURE_COLUMNS


def load_expression_dataset(data_path: Path = PATHS.data_path) -> pd.DataFrame:
    if not data_path.exists():
        raise FileNotFoundError(
            f"No expression dataset found at {data_path}. Collect data with: python -m src.collect_data"
        )

    df = pd.read_csv(data_path)
    missing_columns = [column for column in REQUIRED_COLUMNS if column not in df.columns]
    if missing_columns:
        raise ValueError(f"Dataset is missing required columns: {missing_columns}")

    unknown_labels = sorted(set(df["label"]) - set(EXPRESSION_CLASSES))
    if unknown_labels:
        raise ValueError(f"Dataset contains labels not configured in src/config.py: {unknown_labels}")

    features = df[FEATURE_COLUMNS].to_numpy(dtype=np.float32)
    if not np.isfinite(features).all():
        raise ValueError("Dataset contains non-finite feature values")

    return df


def _labels_by_split(df: pd.DataFrame, sessions: list[str]) -> set[str]:
    return set(df.loc[df["session_id"].isin(sessions), "label"])


def create_session_splits(df: pd.DataFrame, split_path: Path = PATHS.split_path) -> dict[str, list[str]]:
    sessions = sorted(str(session) for session in df["session_id"].unique())
    if len(sessions) < TRAINING.min_sessions_for_split:
        raise ValueError(
            "Session-aware splitting requires at least "
            f"{TRAINING.min_sessions_for_split} distinct recording sessions. "
            f"Found {len(sessions)}. Three sessions is mathematically possible, but this project requires "
            "at least five complete sessions for final training. Collect more separate sessions, "
            "ideally on different days or with meaningful breaks, and record every configured class in each session."
        )

    required_labels = set(EXPRESSION_CLASSES)
    sessions_with_missing_labels = {
        session: sorted(required_labels - _labels_by_split(df, [session]))
        for session in sessions
        if required_labels - _labels_by_split(df, [session])
    }
    if sessions_with_missing_labels:
        raise ValueError(
            "For the initial session-aware protocol, each session should contain all configured classes. "
            f"Sessions with missing labels: {sessions_with_missing_labels}"
        )

    rng = random.Random(TRAINING.seed)
    shuffled = sessions[:]
    rng.shuffle(shuffled)

    n_sessions = len(shuffled)
    n_test = max(1, round(n_sessions * TRAINING.test_session_fraction))
    n_val = max(1, round(n_sessions * TRAINING.validation_session_fraction))
    if n_test + n_val >= n_sessions:
        n_test = 1
        n_val = 1

    test_sessions = sorted(shuffled[:n_test])
    val_sessions = sorted(shuffled[n_test:n_test + n_val])
    train_sessions = sorted(shuffled[n_test + n_val:])

    splits = {
        "train": train_sessions,
        "validation": val_sessions,
        "test": test_sessions,
    }

    for split_name, split_sessions in splits.items():
        if not split_sessions:
            raise ValueError(f"Session split '{split_name}' is empty; collect more sessions.")
        split_labels = _labels_by_split(df, split_sessions)
        missing = sorted(required_labels - split_labels)
        if missing:
            raise ValueError(
                f"Session split '{split_name}' does not contain all configured classes. "
                f"Missing: {missing}. Collect more complete sessions before training."
            )

    split_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "methodology": "session_aware",
        "seed": TRAINING.seed,
        "classes": EXPRESSION_CLASSES,
        "feature_columns": FEATURE_COLUMNS,
        "training_config": asdict(TRAINING),
        "splits": splits,
    }
    split_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return splits


def validate_existing_splits(df: pd.DataFrame, splits: dict[str, list[str]]) -> None:
    expected_keys = {"train", "validation", "test"}
    actual_keys = set(splits)
    if actual_keys != expected_keys:
        raise ValueError(
            f"Existing split file must contain exactly {sorted(expected_keys)}. "
            f"Found {sorted(actual_keys)}. Delete data/splits.json and regenerate it."
        )

    current_sessions = set(str(session) for session in df["session_id"].unique())
    split_session_sets = {
        split_name: set(str(session) for session in session_ids)
        for split_name, session_ids in splits.items()
    }

    overlaps = {}
    split_names = sorted(split_session_sets)
    for index, left_name in enumerate(split_names):
        for right_name in split_names[index + 1:]:
            overlap = sorted(split_session_sets[left_name] & split_session_sets[right_name])
            if overlap:
                overlaps[f"{left_name}/{right_name}"] = overlap
    if overlaps:
        raise ValueError(
            f"Existing split file has overlapping sessions: {overlaps}. "
            "Delete data/splits.json and regenerate it."
        )

    split_sessions = set().union(*split_session_sets.values())
    missing_current_sessions = sorted(current_sessions - split_sessions)
    unknown_or_deleted_sessions = sorted(split_sessions - current_sessions)
    if missing_current_sessions or unknown_or_deleted_sessions:
        raise ValueError(
            "Existing data/splits.json does not exactly match the current dataset sessions. "
            f"Missing current sessions: {missing_current_sessions}. "
            f"Unknown or deleted sessions: {unknown_or_deleted_sessions}. "
            "Delete data/splits.json to regenerate a deterministic session-aware split."
        )

    required_labels = set(EXPRESSION_CLASSES)
    for split_name, split_sessions_for_name in split_session_sets.items():
        if not split_sessions_for_name:
            raise ValueError(
                f"Existing split '{split_name}' is empty. Delete data/splits.json and regenerate it."
            )
        split_labels = _labels_by_split(df, sorted(split_sessions_for_name))
        missing_labels = sorted(required_labels - split_labels)
        if missing_labels:
            raise ValueError(
                f"Existing split '{split_name}' does not contain all configured classes. "
                f"Missing: {missing_labels}. Collect more complete sessions and regenerate data/splits.json."
            )


def load_or_create_splits(df: pd.DataFrame, split_path: Path = PATHS.split_path) -> dict[str, list[str]]:
    if split_path.exists():
        payload = json.loads(split_path.read_text(encoding="utf-8"))
        if payload.get("methodology") != "session_aware":
            raise ValueError("Existing split file is not session-aware. Delete it and recreate splits.")
        if payload.get("classes") != EXPRESSION_CLASSES:
            raise ValueError("Existing split file uses different classes. Delete it and recreate splits.")
        if payload.get("feature_columns") != FEATURE_COLUMNS:
            raise ValueError("Existing split file uses different feature columns. Delete it and recreate splits.")
        splits = payload["splits"]
        validate_existing_splits(df, splits)
        return splits
    return create_session_splits(df, split_path)


def split_dataframe(df: pd.DataFrame, splits: dict[str, list[str]]) -> dict[str, pd.DataFrame]:
    return {
        split_name: df.loc[df["session_id"].astype(str).isin(session_ids)].copy()
        for split_name, session_ids in splits.items()
    }


def features_and_labels(df: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
    x = df[FEATURE_COLUMNS].to_numpy(dtype=np.float32)
    y = df["label"].map({label: index for index, label in enumerate(EXPRESSION_CLASSES)}).to_numpy(dtype=np.int64)
    return x, y
