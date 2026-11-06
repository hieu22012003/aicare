"""Object detection shared by feature 4 (person boxes) and feature 7 (objects)."""

from ai_core.shared.detect.onnx_detector import Box, Detection, OnnxDetector, iou, load_labels

__all__ = ["Box", "Detection", "OnnxDetector", "iou", "load_labels"]
