import torch

from src.config import EXPRESSION_CLASSES, INPUT_SIZE
from src.model import ExpressionNet


def test_expression_net_output_shape():
    model = ExpressionNet(input_size=INPUT_SIZE, num_classes=len(EXPRESSION_CLASSES))
    batch = torch.zeros((4, INPUT_SIZE), dtype=torch.float32)
    output = model(batch)
    assert tuple(output.shape) == (4, len(EXPRESSION_CLASSES))
