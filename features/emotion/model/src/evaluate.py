from __future__ import annotations

import argparse
import json
from pathlib import Path

import joblib
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import torch
from sklearn.metrics import (
    accuracy_score,
    classification_report,
    confusion_matrix,
    precision_recall_fscore_support,
)

from src.config import EXPRESSION_CLASSES, FEATURE_COLUMNS, INPUT_SIZE, PATHS, TRAINING
from src.data import features_and_labels, load_expression_dataset, load_or_create_splits, split_dataframe
from src.model import ExpressionNet


def _metric_payload(y_true: np.ndarray, y_pred: np.ndarray) -> dict:
    precision, recall, f1, support = precision_recall_fscore_support(
        y_true,
        y_pred,
        labels=list(range(len(EXPRESSION_CLASSES))),
        zero_division=0,
    )
    macro_precision, macro_recall, macro_f1, _ = precision_recall_fscore_support(
        y_true,
        y_pred,
        average="macro",
        zero_division=0,
    )
    return {
        "accuracy": accuracy_score(y_true, y_pred),
        "macro_precision": macro_precision,
        "macro_recall": macro_recall,
        "macro_f1": macro_f1,
        "per_class": {
            label: {
                "precision": float(precision[index]),
                "recall": float(recall[index]),
                "f1": float(f1[index]),
                "support": int(support[index]),
            }
            for index, label in enumerate(EXPRESSION_CLASSES)
        },
    }


def _save_confusion_matrix(y_true: np.ndarray, y_pred: np.ndarray, output_path: Path, title: str) -> None:
    matrix = confusion_matrix(y_true, y_pred, labels=list(range(len(EXPRESSION_CLASSES))))
    fig, ax = plt.subplots(figsize=(7, 6))
    image = ax.imshow(matrix, cmap="Blues")
    fig.colorbar(image, ax=ax)
    ax.set_xticks(np.arange(len(EXPRESSION_CLASSES)), labels=EXPRESSION_CLASSES, rotation=45, ha="right")
    ax.set_yticks(np.arange(len(EXPRESSION_CLASSES)), labels=EXPRESSION_CLASSES)
    ax.set_xlabel("Predicted label")
    ax.set_ylabel("True label")
    ax.set_title(title)

    for row in range(matrix.shape[0]):
        for col in range(matrix.shape[1]):
            ax.text(col, row, str(matrix[row, col]), ha="center", va="center", color="black")

    fig.tight_layout()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_path, dpi=160)
    plt.close(fig)


def _load_expression_net(model_path: Path) -> ExpressionNet:
    checkpoint = torch.load(model_path, map_location="cpu")
    if checkpoint.get("classes") != EXPRESSION_CLASSES:
        raise ValueError("Model checkpoint classes do not match src/config.py.")
    if checkpoint.get("feature_columns") != FEATURE_COLUMNS:
        raise ValueError("Model checkpoint feature columns do not match src/config.py.")

    model = ExpressionNet(
        input_size=checkpoint.get("input_size", INPUT_SIZE),
        num_classes=len(EXPRESSION_CLASSES),
        dropout=TRAINING.dropout,
    )
    model.load_state_dict(checkpoint["model_state_dict"])
    model.eval()
    return model


def _predict_expression_net(model_path: Path, x_test: np.ndarray) -> np.ndarray:
    model = _load_expression_net(model_path)
    with torch.no_grad():
        logits = model(torch.tensor(x_test, dtype=torch.float32))
        return torch.argmax(logits, dim=1).numpy()


def _predict_baseline(baseline_path: Path, x_test: np.ndarray) -> np.ndarray:
    payload = joblib.load(baseline_path)
    if payload.get("classes") != EXPRESSION_CLASSES:
        raise ValueError("Baseline classes do not match src/config.py.")
    if payload.get("feature_columns") != FEATURE_COLUMNS:
        raise ValueError("Baseline feature columns do not match src/config.py.")
    return payload["model"].predict(x_test)


def evaluate(
    data_path: Path = PATHS.data_path,
    model_path: Path = PATHS.model_path,
    baseline_path: Path = PATHS.baseline_path,
    results_dir: Path = PATHS.results_dir,
) -> None:
    df = load_expression_dataset(data_path)
    splits = load_or_create_splits(df, PATHS.split_path)
    test_df = split_dataframe(df, splits)["test"]
    x_test, y_test = features_and_labels(test_df)

    results: dict[str, dict] = {
        "methodology": {
            "split": "session_aware",
            "test_sessions": splits["test"],
            "test_samples": int(len(test_df)),
            "classes": EXPRESSION_CLASSES,
            "note": "Metrics are measured only on held-out recording sessions.",
        }
    }
    reports: list[str] = []

    if model_path.exists():
        nn_predictions = _predict_expression_net(model_path, x_test)
        results["ExpressionNet"] = _metric_payload(y_test, nn_predictions)
        reports.append("ExpressionNet\n" + classification_report(
            y_test,
            nn_predictions,
            labels=list(range(len(EXPRESSION_CLASSES))),
            target_names=EXPRESSION_CLASSES,
            zero_division=0,
        ))
        _save_confusion_matrix(
            y_test,
            nn_predictions,
            results_dir / "confusion_matrix_expression_net.png",
            "ExpressionNet Confusion Matrix",
        )
    else:
        print(f"ExpressionNet checkpoint not found at {model_path}; skipping neural-network evaluation.")

    if baseline_path.exists():
        baseline_predictions = _predict_baseline(baseline_path, x_test)
        results["LogisticRegression"] = _metric_payload(y_test, baseline_predictions)
        reports.append("LogisticRegression\n" + classification_report(
            y_test,
            baseline_predictions,
            labels=list(range(len(EXPRESSION_CLASSES))),
            target_names=EXPRESSION_CLASSES,
            zero_division=0,
        ))
        _save_confusion_matrix(
            y_test,
            baseline_predictions,
            results_dir / "confusion_matrix_baseline.png",
            "Logistic Regression Confusion Matrix",
        )
    else:
        print(f"Baseline model not found at {baseline_path}; skipping baseline evaluation.")

    if "ExpressionNet" not in results and "LogisticRegression" not in results:
        raise FileNotFoundError("No trained model artifacts found to evaluate.")

    results_dir.mkdir(parents=True, exist_ok=True)
    (results_dir / "metrics.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
    (results_dir / "classification_report.txt").write_text("\n\n".join(reports), encoding="utf-8")
    print(f"Saved evaluation outputs to {results_dir}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Evaluate trained models on the held-out test sessions.")
    parser.add_argument("--data", type=Path, default=PATHS.data_path)
    parser.add_argument("--model", type=Path, default=PATHS.model_path)
    parser.add_argument("--baseline", type=Path, default=PATHS.baseline_path)
    parser.add_argument("--results-dir", type=Path, default=PATHS.results_dir)
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    evaluate(args.data, args.model, args.baseline, args.results_dir)
