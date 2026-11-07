"""Object detection shared by feature 4 (person boxes) and feature 7 (objects)."""

from shared.vision.detect.onnx_detector import Box, Detection, OnnxDetector, iou, load_labels

__all__ = ["Box", "Detection", "OnnxDetector", "iou", "load_labels"]


def default_model_path() -> str | None:
    """The detector shipped with the repo (models/object_detector/*.onnx), unless AI_CORE_DETECTOR_MODEL is set."""
    import os
    from pathlib import Path

    env = os.getenv("AI_CORE_DETECTOR_MODEL")
    if env:
        return env if env.lower() not in ("0", "off", "none") else None
    try:
        from shared.config import BUNDLE_DIR

        root = Path(BUNDLE_DIR)
    except Exception:
        root = Path(__file__).resolve().parents[3]
    for name in ("dfine_s_coco.onnx", "dfine_n_coco.onnx"):
        candidate = root / "models" / "object_detector" / name
        if candidate.is_file():
            return str(candidate)
    return None
