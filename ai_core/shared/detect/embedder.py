"""Image embeddings to tell one registered item from another of the same type.

``ColorShapeEmbedder`` is a dependency-free baseline (HSV colour histogram + gradient
orientation histogram, 160 dims). It separates objects that differ in colour or shape
(red-framed vs black-framed glasses) but is not a learned model. Phase 0 of the plan picks a
small learned image encoder (ONNX, licence checked); ``OnnxEmbedder`` loads such a model.

Embeddings from different embedders are never compared (``embedder_id`` must match).
"""

from __future__ import annotations

import numpy as np


def _l2(v: np.ndarray) -> np.ndarray:
    n = float(np.linalg.norm(v))
    return v / n if n > 0 else v


def cosine(a, b) -> float:
    va, vb = np.asarray(a, dtype=np.float32), np.asarray(b, dtype=np.float32)
    na, nb = float(np.linalg.norm(va)), float(np.linalg.norm(vb))
    return float(va @ vb / (na * nb)) if na > 0 and nb > 0 else 0.0


class ColorShapeEmbedder:
    embedder_id = "color-shape-v1"

    def embed(self, bgr: np.ndarray) -> list[float]:
        import cv2

        img = cv2.resize(bgr, (64, 64), interpolation=cv2.INTER_AREA)
        hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
        color = cv2.calcHist([hsv], [0, 1, 2], None, [8, 4, 4], [0, 180, 0, 256, 0, 256]).flatten()
        color = _l2(color.astype(np.float32))
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY).astype(np.float32)
        gx = cv2.Sobel(gray, cv2.CV_32F, 1, 0, ksize=3)
        gy = cv2.Sobel(gray, cv2.CV_32F, 0, 1, ksize=3)
        mag, ang = cv2.cartToPolar(gx, gy, angleInDegrees=True)
        shape, _ = np.histogram(ang % 180, bins=32, range=(0, 180), weights=mag)
        shape = _l2(shape.astype(np.float32))
        return [round(float(v), 5) for v in _l2(np.concatenate([color, shape * 0.7]))]


class OnnxEmbedder:
    """Any image encoder exported to ONNX: input [1, 3, S, S] RGB, ImageNet-normalized; output [1, D]."""

    def __init__(self, model_path: str, size: int = 224, embedder_id: str | None = None) -> None:
        import onnxruntime as ort

        self.session = ort.InferenceSession(model_path, providers=["CPUExecutionProvider"])
        self.input = self.session.get_inputs()[0].name
        self.size = size
        self.embedder_id = embedder_id or f"onnx:{model_path.rsplit('/', 1)[-1]}"

    def embed(self, bgr: np.ndarray) -> list[float]:
        import cv2

        rgb = cv2.cvtColor(cv2.resize(bgr, (self.size, self.size)), cv2.COLOR_BGR2RGB).astype(np.float32) / 255.0
        rgb = (rgb - np.array([0.485, 0.456, 0.406], np.float32)) / np.array([0.229, 0.224, 0.225], np.float32)
        out = self.session.run(None, {self.input: rgb.transpose(2, 0, 1)[None]})[0].reshape(-1)
        return [round(float(v), 5) for v in _l2(out.astype(np.float32))]


def crop(bgr: np.ndarray, box: tuple[float, float, float, float], pad: float = 0.1) -> np.ndarray | None:
    h, w = bgr.shape[:2]
    x, y, bw, bh = box
    x0, y0 = int(max(0.0, x - bw * pad) * w), int(max(0.0, y - bh * pad) * h)
    x1, y1 = int(min(1.0, x + bw * (1 + pad)) * w), int(min(1.0, y + bh * (1 + pad)) * h)
    if x1 - x0 < 4 or y1 - y0 < 4:
        return None
    return bgr[y0:y1, x0:x1]
