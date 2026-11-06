from dataclasses import dataclass
import os
from pathlib import Path


@dataclass(frozen=True)
class VoiceConfig:
    model_path: str
    device: str = 'cpu'
    compute_type: str = 'int8'
    cpu_threads: int = 4
    max_seconds: float = 30.0
    beam_size: int = 1

    def __post_init__(self):
        if self.device not in ('cpu', 'cuda') or not 1 <= self.cpu_threads <= 16:
            raise ValueError('Invalid voice device/thread configuration')
        if not 1 <= self.max_seconds <= 30 or self.beam_size not in (1, 3, 5):
            raise ValueError('Invalid recording/beam limit')

    @classmethod
    def from_env(cls):
        from shared.config import DATA_DIR
        base = Path(__file__).resolve().parents[3]
        model_name = 'sherpa-onnx-zipformer-vi-30M-int8-2026-02-09'
        preferred = DATA_DIR / 'models' / model_name
        if not (preferred / 'encoder.int8.onnx').is_file():
            preferred = base / 'data' / 'models' / model_name
        path = Path(os.getenv('VOICE_MODEL_PATH', str(preferred)))
        if not path.is_absolute():
            path = base / path
        return cls(str(path.resolve()), os.getenv('VOICE_DEVICE', 'cpu'),
                   os.getenv('VOICE_COMPUTE_TYPE', 'int8'),
                   int(os.getenv('VOICE_CPU_THREADS', '4')),
                   float(os.getenv('VOICE_MAX_SECONDS', '30')),
                   int(os.getenv('VOICE_BEAM_SIZE', '1')))
