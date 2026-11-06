from dataclasses import dataclass
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


EXPRESSION_CLASSES = ["Neutral", "Happy", "Sad", "Angry", "Surprised"]
CLASS_TO_INDEX = {name: index for index, name in enumerate(EXPRESSION_CLASSES)}
INDEX_TO_CLASS = {index: name for name, index in CLASS_TO_INDEX.items()}


# Selected MediaPipe FaceMesh landmarks covering central face anchors,
# eyebrows, eyes, and mouth. This first version intentionally uses only
# normalized landmark coordinates, with no engineered geometric features.
SELECTED_FACE_LANDMARKS = [
    1,
    107, 105, 336, 334,
    33, 133, 159, 145,
    362, 263, 386, 374,
    61, 291,
    0, 13, 14, 17,
    39, 269,
]


FEATURE_COLUMNS = [
    f"landmark_{landmark_id}_{axis}"
    for landmark_id in SELECTED_FACE_LANDMARKS
    for axis in ("x", "y", "z")
]


@dataclass(frozen=True)
class Paths:
    data_path: Path = PROJECT_ROOT / "data" / "expression_landmarks.csv"
    split_path: Path = PROJECT_ROOT / "data" / "splits.json"
    model_path: Path = PROJECT_ROOT / "models" / "expression_net.pth"
    baseline_path: Path = PROJECT_ROOT / "models" / "baseline_logistic_regression.joblib"
    results_dir: Path = PROJECT_ROOT / "results"


@dataclass(frozen=True)
class TrainingConfig:
    seed: int = 42
    batch_size: int = 32
    epochs: int = 200
    learning_rate: float = 0.001
    weight_decay: float = 0.0001
    dropout: float = 0.25
    patience: int = 25
    validation_session_fraction: float = 0.2
    test_session_fraction: float = 0.2
    min_sessions_for_split: int = 5


@dataclass(frozen=True)
class InferenceConfig:
    confidence_threshold: float = 0.65
    smoothing_window: int = 7
    camera_index: int = 0
    frame_width: int = 960
    frame_height: int = 720


PATHS = Paths()
TRAINING = TrainingConfig()
INFERENCE = InferenceConfig()
INPUT_SIZE = len(FEATURE_COLUMNS)
