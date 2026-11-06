"""ONNX object detector in the D-FINE / RT-DETR export format (no NMS needed).

Expected graph (as produced by D-FINE's ``tools/deployment/export_onnx.py``):
    inputs : images [N, 3, S, S] float32 RGB in [0, 1];  orig_target_sizes [N, 2] int64 (w, h)
    outputs: labels [N, Q] int64;  boxes [N, Q, 4] float32 xyxy in pixels of orig size;  scores [N, Q]

ONNX files hold no executable code, unlike pickled ``.pth`` checkpoints, so loading one is safe.
``detect`` is blocking: call it through ``asyncio.to_thread``.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np

from ai_core.shared.detect.coco import COCO80

# (x, y, w, h) normalized to the full frame
Box = tuple[float, float, float, float]


@dataclass(frozen=True, slots=True)
class Detection:
    label: str
    score: float
    box: Box
    embedding: tuple[float, ...] | None = None

    @property
    def center(self) -> tuple[float, float]:
        x, y, w, h = self.box
        return (x + w / 2, y + h / 2)


def iou(a: Box, b: Box) -> float:
    ax2, ay2, bx2, by2 = a[0] + a[2], a[1] + a[3], b[0] + b[2], b[1] + b[3]
    iw = max(0.0, min(ax2, bx2) - max(a[0], b[0]))
    ih = max(0.0, min(ay2, by2) - max(a[1], b[1]))
    inter = iw * ih
    union = a[2] * a[3] + b[2] * b[3] - inter
    return inter / union if union > 0 else 0.0


def load_labels(path: str | None) -> list[str]:
    if not path:
        return list(COCO80)
    return [line.strip() for line in Path(path).read_text(encoding="utf-8").splitlines() if line.strip()]


class OnnxDetector:
    def __init__(
        self,
        model_path: str,
        labels: list[str] | None = None,
        input_size: int = 640,
        score_threshold: float = 0.4,
        threads: int = 2,
    ) -> None:
        import onnxruntime as ort

        options = ort.SessionOptions()
        options.intra_op_num_threads = threads
        self.session = ort.InferenceSession(model_path, options, providers=["CPUExecutionProvider"])
        self.labels = labels or list(COCO80)
        self.input_size = input_size
        self.score_threshold = score_threshold
        names = [i.name for i in self.session.get_inputs()]
        self._image_input = "images" if "images" in names else names[0]
        self._size_input = "orig_target_sizes" if "orig_target_sizes" in names else (names[1] if len(names) > 1 else None)

    def _run(self, rgb: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        import cv2

        h, w = rgb.shape[:2]
        resized = cv2.resize(rgb, (self.input_size, self.input_size), interpolation=cv2.INTER_LINEAR)
        tensor = resized.astype(np.float32).transpose(2, 0, 1)[None] / 255.0
        feeds = {self._image_input: tensor}
        if self._size_input is not None:
            feeds[self._size_input] = np.array([[w, h]], dtype=np.int64)
        outputs = {o.name: v for o, v in zip(self.session.get_outputs(), self.session.run(None, feeds))}
        labels = outputs.get("labels", next(iter(outputs.values())))
        return labels[0], outputs["boxes"][0], outputs["scores"][0]

    def detect(self, bgr: np.ndarray, roi: Box | None = None, wanted: set[str] | None = None) -> list[Detection]:
        """Detect in the whole frame or in a region of interest (scaled up: small objects stay visible)."""
        import cv2

        H, W = bgr.shape[:2]
        ox = oy = 0
        image = bgr
        if roi is not None:
            x0, y0 = int(max(0.0, roi[0]) * W), int(max(0.0, roi[1]) * H)
            x1, y1 = int(min(1.0, roi[0] + roi[2]) * W), int(min(1.0, roi[1] + roi[3]) * H)
            if x1 - x0 < 8 or y1 - y0 < 8:
                return []
            image, ox, oy = bgr[y0:y1, x0:x1], x0, y0
        rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
        labels, boxes, scores = self._run(rgb)
        out: list[Detection] = []
        for label_id, box, score in zip(labels, boxes, scores):
            if score < self.score_threshold or not (0 <= int(label_id) < len(self.labels)):
                continue
            name = self.labels[int(label_id)]
            if wanted is not None and name not in wanted:
                continue
            x1, y1, x2, y2 = (float(v) for v in box)
            x1, x2 = max(0.0, x1) + ox, min(image.shape[1], x2) + ox
            y1, y2 = max(0.0, y1) + oy, min(image.shape[0], y2) + oy
            if x2 <= x1 or y2 <= y1:
                continue
            out.append(Detection(name, float(score), (x1 / W, y1 / H, (x2 - x1) / W, (y2 - y1) / H)))
        return out

    def detect_regions(self, bgr: np.ndarray, rois: list[Box], wanted: set[str] | None = None) -> list[Detection]:
        """Full frame plus each region; duplicates of the same label (IoU > 0.5) keep the best score."""
        found = self.detect(bgr, None, wanted)
        for roi in rois:
            found += self.detect(bgr, roi, wanted)
        found.sort(key=lambda d: d.score, reverse=True)
        kept: list[Detection] = []
        for det in found:
            if all(det.label != k.label or iou(det.box, k.box) <= 0.5 for k in kept):
                kept.append(det)
        return kept
