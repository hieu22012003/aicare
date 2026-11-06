from __future__ import annotations

import argparse
from pathlib import Path

import joblib
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import accuracy_score, f1_score
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from src.config import EXPRESSION_CLASSES, FEATURE_COLUMNS, PATHS, TRAINING
from src.data import features_and_labels, load_expression_dataset, load_or_create_splits, split_dataframe


def train_baseline(data_path: Path = PATHS.data_path, baseline_path: Path = PATHS.baseline_path) -> None:
    df = load_expression_dataset(data_path)
    splits = load_or_create_splits(df, PATHS.split_path)
    split_dfs = split_dataframe(df, splits)

    x_train, y_train = features_and_labels(split_dfs["train"])
    x_val, y_val = features_and_labels(split_dfs["validation"])

    model = Pipeline(
        steps=[
            ("scaler", StandardScaler()),
            (
                "classifier",
                LogisticRegression(
                    max_iter=2000,
                    class_weight="balanced",
                    random_state=TRAINING.seed,
                    multi_class="auto",
                ),
            ),
        ]
    )
    model.fit(x_train, y_train)

    val_predictions = model.predict(x_val)
    print(f"Validation accuracy: {accuracy_score(y_val, val_predictions):.4f}")
    print(f"Validation macro F1: {f1_score(y_val, val_predictions, average='macro', zero_division=0):.4f}")

    baseline_path.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(
        {
            "model": model,
            "classes": EXPRESSION_CLASSES,
            "feature_columns": FEATURE_COLUMNS,
            "split_path": str(PATHS.split_path),
        },
        baseline_path,
    )
    print(f"Baseline saved to {baseline_path}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Train a logistic-regression baseline on the same session split.")
    parser.add_argument("--data", type=Path, default=PATHS.data_path)
    parser.add_argument("--output", type=Path, default=PATHS.baseline_path)
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    train_baseline(args.data, args.output)
