import torch
import torch.nn as nn

from src.config import INPUT_SIZE


class ExpressionNet(nn.Module):
    def __init__(self, input_size: int = INPUT_SIZE, num_classes: int = 5, dropout: float = 0.25):
        super().__init__()
        self.network = nn.Sequential(
            nn.Linear(input_size, 128),
            nn.ReLU(),
            nn.Dropout(dropout),
            nn.Linear(128, 64),
            nn.ReLU(),
            nn.Dropout(dropout),
            nn.Linear(64, num_classes),
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.network(x)
