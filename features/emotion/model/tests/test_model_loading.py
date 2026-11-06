import torch

from main import load_model
from src.config import EXPRESSION_CLASSES, FEATURE_COLUMNS, INPUT_SIZE, TRAINING
from src.model import ExpressionNet


def test_load_model_returns_none_for_missing_checkpoint(tmp_path):
    assert load_model(tmp_path / "missing.pth") is None


def test_load_model_round_trip(tmp_path):
    model = ExpressionNet(input_size=INPUT_SIZE, num_classes=len(EXPRESSION_CLASSES))
    checkpoint_path = tmp_path / "expression_net.pth"
    torch.save(
        {
            "model_state_dict": model.state_dict(),
            "classes": EXPRESSION_CLASSES,
            "feature_columns": FEATURE_COLUMNS,
            "input_size": INPUT_SIZE,
            "training_config": TRAINING.__dict__,
        },
        checkpoint_path,
    )
    loaded = load_model(checkpoint_path)
    assert loaded is not None
    assert isinstance(loaded, ExpressionNet)
