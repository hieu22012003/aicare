from __future__ import annotations

import argparse
import copy
import random
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import torch
import torch.nn as nn
from sklearn.metrics import accuracy_score, f1_score
from torch.utils.data import DataLoader, TensorDataset

from src.config import EXPRESSION_CLASSES, FEATURE_COLUMNS, INPUT_SIZE, PATHS, TRAINING
from src.data import features_and_labels, load_expression_dataset, load_or_create_splits, split_dataframe
from src.model import ExpressionNet


def set_seed(seed: int) -> None:
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(seed)
    torch.backends.cudnn.deterministic = True
    torch.backends.cudnn.benchmark = False


def make_loader(x: np.ndarray, y: np.ndarray, batch_size: int, shuffle: bool) -> DataLoader:
    dataset = TensorDataset(torch.tensor(x, dtype=torch.float32), torch.tensor(y, dtype=torch.long))
    return DataLoader(dataset, batch_size=batch_size, shuffle=shuffle)


def evaluate_phase(model: nn.Module, loader: DataLoader, criterion: nn.Module, device: torch.device) -> dict[str, float]:
    model.eval()
    losses: list[float] = []
    all_predictions: list[int] = []
    all_targets: list[int] = []

    with torch.no_grad():
        for features, targets in loader:
            features = features.to(device)
            targets = targets.to(device)
            logits = model(features)
            loss = criterion(logits, targets)
            losses.append(float(loss.item()))
            predictions = torch.argmax(logits, dim=1)
            all_predictions.extend(predictions.cpu().numpy().tolist())
            all_targets.extend(targets.cpu().numpy().tolist())

    return {
        "loss": float(np.mean(losses)) if losses else 0.0,
        "accuracy": accuracy_score(all_targets, all_predictions),
        "macro_f1": f1_score(all_targets, all_predictions, average="macro", zero_division=0),
    }


def class_weights(y: np.ndarray, num_classes: int) -> torch.Tensor:
    counts = np.bincount(y, minlength=num_classes).astype(np.float32)
    counts[counts == 0] = 1.0
    weights = counts.sum() / (num_classes * counts)
    return torch.tensor(weights, dtype=torch.float32)


def train_model(
    data_path: Path = PATHS.data_path,
    model_path: Path = PATHS.model_path,
    epochs: int = TRAINING.epochs,
    learning_rate: float = TRAINING.learning_rate,
) -> None:
    set_seed(TRAINING.seed)
    df = load_expression_dataset(data_path)
    splits = load_or_create_splits(df, PATHS.split_path)
    split_dfs = split_dataframe(df, splits)

    x_train, y_train = features_and_labels(split_dfs["train"])
    x_val, y_val = features_and_labels(split_dfs["validation"])

    train_loader = make_loader(x_train, y_train, TRAINING.batch_size, shuffle=True)
    val_loader = make_loader(x_val, y_val, TRAINING.batch_size, shuffle=False)

    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    model = ExpressionNet(
        input_size=INPUT_SIZE,
        num_classes=len(EXPRESSION_CLASSES),
        dropout=TRAINING.dropout,
    ).to(device)

    criterion = nn.CrossEntropyLoss(weight=class_weights(y_train, len(EXPRESSION_CLASSES)).to(device))
    optimizer = torch.optim.AdamW(model.parameters(), lr=learning_rate, weight_decay=TRAINING.weight_decay)

    best_macro_f1 = -1.0
    best_validation_loss = float("inf")
    best_epoch = 0
    patience_remaining = TRAINING.patience
    best_state = None
    history: list[dict[str, float | int]] = []

    print(f"Device: {device}")
    print(f"Training samples: {len(split_dfs['train'])}")
    print(f"Validation samples: {len(split_dfs['validation'])}")
    print(f"Test samples held out: {len(split_dfs['test'])}")
    print(f"Session split: {splits}")

    for epoch in range(1, epochs + 1):
        model.train()
        epoch_losses: list[float] = []
        for features, targets in train_loader:
            features = features.to(device)
            targets = targets.to(device)

            optimizer.zero_grad()
            logits = model(features)
            loss = criterion(logits, targets)
            loss.backward()
            optimizer.step()
            epoch_losses.append(float(loss.item()))

        val_metrics = evaluate_phase(model, val_loader, criterion, device)
        train_loss = float(np.mean(epoch_losses)) if epoch_losses else 0.0
        history.append(
            {
                "epoch": epoch,
                "training_loss": train_loss,
                "validation_loss": val_metrics["loss"],
                "validation_accuracy": val_metrics["accuracy"],
                "validation_macro_f1": val_metrics["macro_f1"],
            }
        )
        improved = (
            val_metrics["macro_f1"] > best_macro_f1
            or (
                val_metrics["macro_f1"] == best_macro_f1
                and val_metrics["loss"] < best_validation_loss
            )
        )

        if improved:
            best_macro_f1 = val_metrics["macro_f1"]
            best_validation_loss = val_metrics["loss"]
            best_epoch = epoch
            patience_remaining = TRAINING.patience
            best_state = copy.deepcopy(model.state_dict())
        else:
            patience_remaining -= 1

        if epoch == 1 or epoch % 10 == 0 or improved:
            print(
                f"Epoch {epoch:03d}/{epochs} "
                f"train_loss={train_loss:.4f} "
                f"val_loss={val_metrics['loss']:.4f} "
                f"val_acc={val_metrics['accuracy']:.4f} "
                f"val_macro_f1={val_metrics['macro_f1']:.4f}"
            )

        if patience_remaining <= 0:
            print(f"Early stopping at epoch {epoch}.")
            break

    if best_state is None:
        raise RuntimeError("Training did not produce a valid model state.")

    PATHS.results_dir.mkdir(parents=True, exist_ok=True)
    history_path = PATHS.results_dir / "training_history.csv"
    history_df = pd.DataFrame(history)
    history_df.to_csv(history_path, index=False)

    fig, axes = plt.subplots(1, 2, figsize=(11, 4))
    axes[0].plot(history_df["epoch"], history_df["training_loss"], label="Training loss")
    axes[0].plot(history_df["epoch"], history_df["validation_loss"], label="Validation loss")
    axes[0].set_xlabel("Epoch")
    axes[0].set_ylabel("Loss")
    axes[0].legend()
    axes[0].set_title("Loss")

    axes[1].plot(history_df["epoch"], history_df["validation_accuracy"], label="Validation accuracy")
    axes[1].plot(history_df["epoch"], history_df["validation_macro_f1"], label="Validation macro F1")
    axes[1].set_xlabel("Epoch")
    axes[1].set_ylabel("Score")
    axes[1].set_ylim(0.0, 1.0)
    axes[1].legend()
    axes[1].set_title("Validation Metrics")
    fig.tight_layout()
    plot_path = PATHS.results_dir / "training_history.png"
    fig.savefig(plot_path, dpi=160)
    plt.close(fig)

    model_path.parent.mkdir(parents=True, exist_ok=True)
    torch.save(
        {
            "model_state_dict": best_state,
            "classes": EXPRESSION_CLASSES,
            "feature_columns": FEATURE_COLUMNS,
            "input_size": INPUT_SIZE,
            "split_path": str(PATHS.split_path),
            "training_config": TRAINING.__dict__,
            "best_epoch": best_epoch,
            "best_validation_macro_f1": best_macro_f1,
            "best_validation_loss": best_validation_loss,
        },
        model_path,
    )
    print(f"Best model saved to {model_path}")
    print(f"Training history saved to {history_path}")
    print(f"Training history plot saved to {plot_path}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Train ExpressionNet on session-separated landmark data.")
    parser.add_argument("--data", type=Path, default=PATHS.data_path)
    parser.add_argument("--model", type=Path, default=PATHS.model_path)
    parser.add_argument("--epochs", type=int, default=TRAINING.epochs)
    parser.add_argument("--learning-rate", type=float, default=TRAINING.learning_rate)
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    train_model(args.data, args.model, args.epochs, args.learning_rate)
