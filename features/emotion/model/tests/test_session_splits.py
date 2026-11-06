import pandas as pd
import pytest

from src.config import EXPRESSION_CLASSES, FEATURE_COLUMNS
from src.data import create_session_splits
from src.data import load_or_create_splits


def make_dataset(session_ids):
    rows = []
    sample_id = 0
    for session_id in session_ids:
        for label in EXPRESSION_CLASSES:
            sample_id += 1
            row = {
                "session_id": session_id,
                "sample_id": sample_id,
                "timestamp_utc": "2026-08-27T00:00:00+00:00",
                "subject_id": "subject_001",
                "label": label,
            }
            row.update({column: 0.0 for column in FEATURE_COLUMNS})
            rows.append(row)
    return pd.DataFrame(rows)


def test_session_split_refuses_too_few_sessions(tmp_path):
    with pytest.raises(ValueError, match="at least"):
        create_session_splits(
            make_dataset(["session_a", "session_b", "session_c", "session_d"]),
            tmp_path / "splits.json",
        )


def test_session_split_separates_sessions(tmp_path):
    splits = create_session_splits(
        make_dataset(["session_a", "session_b", "session_c", "session_d", "session_e"]),
        tmp_path / "splits.json",
    )
    train = set(splits["train"])
    validation = set(splits["validation"])
    test = set(splits["test"])
    assert train
    assert validation
    assert test
    assert train.isdisjoint(validation)
    assert train.isdisjoint(test)
    assert validation.isdisjoint(test)


def test_existing_split_refuses_missing_current_sessions(tmp_path):
    df = make_dataset(["session_a", "session_b", "session_c", "session_d", "session_e"])
    split_path = tmp_path / "splits.json"
    create_session_splits(df, split_path)
    expanded_df = make_dataset(["session_a", "session_b", "session_c", "session_d", "session_e", "session_f"])

    with pytest.raises(ValueError, match="does not exactly match"):
        load_or_create_splits(expanded_df, split_path)


def test_existing_split_refuses_unknown_deleted_sessions(tmp_path):
    df = make_dataset(["session_a", "session_b", "session_c", "session_d", "session_e"])
    split_path = tmp_path / "splits.json"
    create_session_splits(df, split_path)
    reduced_df = make_dataset(["session_a", "session_b", "session_c", "session_d"])

    with pytest.raises(ValueError, match="does not exactly match"):
        load_or_create_splits(reduced_df, split_path)
