"""
Face Module Adapter for AI Rehabilitation Assistant.
Provides separated, robust OpenCV DNN face detection and LBPH face recognition,
decoupling user registration from real-time identification.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import threading
import time
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import cv2
import numpy as np

from shared.config import (
    FACE_MODULE_DIR,
    SHARED_DATA_DIR,
    SHARED_RESULTS_DIR,
    SHARED_DIR,
    USERS_DIR,
    resource_path,
)

logger = logging.getLogger("FaceAdapter")

# ---------------------------------------------------------------------------------------------------
# Recognition engines
#
# 1. SFace (OpenCV FaceRecognizerSF, deep embedding, cosine similarity) + YuNet landmarks for alignment.
#    Used whenever models/face_recognition_sface_2021dec.onnx and face_detection_yunet_2023mar.onnx exist
#    (scripts/download_models.py fetches them). Measured on the customer's own webcam recordings
#    (dim room, glasses, 2 sessions): same person median similarity 0.81, 10th percentile 0.67;
#    OpenCV's published decision threshold for this model is 0.363 (LFW).
# 2. LBPH (legacy fallback when the ONNX files are missing). On the same recordings the same person scored
#    LBPH distance 37-52 (median 45), so the old threshold 40 rejected ~74% of the patient's own frames:
#    the "Người lạ" flicker in the field. The old 17-20 figure came from testing on augmented copies of the
#    registration photo itself.
#
# A decision is never taken from one frame: an identity is kept while the same face is tracked, and a
# "stranger" verdict needs several clearly-different frames over >= 1.5 s (a hand in front of the mouth while
# taking a pill, a head turn or a blink only make a frame "unsure", never "stranger").
# ---------------------------------------------------------------------------------------------------
FACE_ENGINE = os.getenv("AICARE_FACE_ENGINE", "auto").strip().lower()  # auto | sface | lbph
SFACE_ACCEPT = float(os.getenv("AICARE_SFACE_ACCEPT", "0.42"))  # >= : this frame is the registered person
SFACE_REJECT = float(os.getenv("AICARE_SFACE_REJECT", "0.28"))  # <  : this frame is clearly someone else
RECOGNITION_THRESHOLD = float(os.getenv("AICARE_FACE_THRESHOLD", "52"))  # LBPH distance accept (fallback)
LBPH_REJECT = float(os.getenv("AICARE_FACE_REJECT", "68"))  # LBPH distance above which a frame is "different"
IDENTITY_HOLD_SECONDS = float(os.getenv("AICARE_FACE_HOLD_SECONDS", "10"))  # keep identity while tracked
STRANGER_MIN_FRAMES = 4
STRANGER_MIN_SECONDS = 1.5
SFACE_MODEL_FILE = "face_recognition_sface_2021dec.onnx"
YUNET_MODEL_FILE = "face_detection_yunet_2023mar.onnx"


class FaceAdapter:
    """Stable In-Memory Face Detector, Recognizer, and Registration Manager."""

    def __init__(self, data_dir: Optional[Path] = None, users_dir: Optional[Path] = None, db: Optional[Any] = None) -> None:
        self.module_dir: Path = FACE_MODULE_DIR
        self.result_file: Path = SHARED_RESULTS_DIR / "face.json"
        self._is_isolated = bool(data_dir or users_dir)
        _data_dir = Path(data_dir) if data_dir else SHARED_DATA_DIR
        self.patients_file: Path = _data_dir / "patients.json"
        self.patient_file: Path = _data_dir / "patient.json"
        self.registered_faces_dir: Path = _data_dir / "registered_faces"
        self.users_dir: Path = Path(users_dir) if users_dir else USERS_DIR
        self.state_file: Path = (_data_dir / "state.json") if self._is_isolated else (SHARED_DIR / "state.json")
        self.db = db

        self.registered_faces_dir.mkdir(parents=True, exist_ok=True)
        self.patients_file.parent.mkdir(parents=True, exist_ok=True)
        self.users_dir.mkdir(parents=True, exist_ok=True)

        # 1. Detectors
        self._net: Optional[cv2.dnn.Net] = None
        self._haar_cascade: Optional[cv2.CascadeClassifier] = None
        self._load_detector()

        # 2. LBPH Face Recognizer
        self._model_lock = threading.RLock()
        self._recognizer = cv2.face.LBPHFaceRecognizer_create(
            radius=1, neighbors=8, grid_x=8, grid_y=8
        )
        self._label_to_id: Dict[int, str] = {}
        self._id_to_label: Dict[str, int] = {}
        self._is_trained: bool = False

        # Deep embedding engine (SFace) when its model files are present
        self._sface = None
        self._yunet = None
        self._gallery: Dict[str, np.ndarray] = {}
        self._load_sface()

        # Load & train database on start
        self.train_recognizer()

        # 3. Dynamic Tracking State & Temporal Smoothing
        self._last_face_box: Optional[Tuple[int, int, int, int]] = None
        self._last_recognition: Optional[Dict[str, Any]] = None
        self._frames_without_face: int = 0
        self._pred_history: deque[Dict[str, Any]] = deque(maxlen=15)
        self._consecutive_unknown_count: int = 0
        self._last_stable_pid: Optional[str] = None
        self._last_recognized_time: float = 0.0
        self._identity_ttl: float = IDENTITY_HOLD_SECONDS
        # Tracked identity (see module header)
        self._track_pid: Optional[str] = None
        self._track_last_match: float = 0.0
        self._track_best_score: float = 0.0
        self._track_box: Optional[Tuple[int, int, int, int]] = None
        self._reject_count: int = 0
        self._reject_since: Optional[float] = None
        self._last_reject_ts: float = 0.0
        self._last_face_seen: float = 0.0
        self._stranger_confirmed_at: float = 0.0
        self._last_embed_ts: float = 0.0

    def _model_path(self, name: str) -> Path:
        p = resource_path(f"features/face_monitoring/standalone/models/{name}")
        return p if p.exists() else self.module_dir / "models" / name

    def _load_sface(self) -> None:
        """Load SFace + YuNet (OpenCV >= 4.5.4). Missing files or old OpenCV: LBPH fallback."""
        if FACE_ENGINE == "lbph":
            return
        sface_path, yunet_path = self._model_path(SFACE_MODEL_FILE), self._model_path(YUNET_MODEL_FILE)
        if not (sface_path.exists() and yunet_path.exists()):
            logger.warning("SFace/YuNet models missing (%s, %s): face recognition falls back to LBPH. "
                           "Run deploy/windows/Setup.cmd (scripts/download_models.py) to install them.",
                           sface_path.name, yunet_path.name)
            return
        try:
            self._sface = cv2.FaceRecognizerSF.create(str(sface_path), "")
            self._yunet = cv2.FaceDetectorYN.create(str(yunet_path), "", (320, 320), 0.6, 0.3, 5)
            logger.info("Face recognition engine: SFace (accept >= %.2f, reject < %.2f)", SFACE_ACCEPT, SFACE_REJECT)
        except Exception as e:
            self._sface = self._yunet = None
            logger.warning("SFace unavailable (%s); falling back to LBPH", e)

    @property
    def engine(self) -> str:
        return "sface" if self._sface is not None else "lbph"

    def _embed(self, image: np.ndarray, box: Optional[Any] = None) -> Optional[Tuple[np.ndarray, bool]]:
        """(unit-length SFace embedding, aligned) of the face in `box`, or of a face crop when box is None.

        aligned=False means YuNet found no landmarks (hand over the face, strong profile) and a plain
        112x112 crop was used: such a frame may keep a tracked identity but never establishes one.
        """
        if self._sface is None or image is None or image.size == 0:
            return None
        if len(image.shape) == 2:
            image = cv2.cvtColor(image, cv2.COLOR_GRAY2BGR)
        h, w = image.shape[:2]
        if box is not None:
            bx, by, bw, bh = [int(v) for v in box[:4]]
            m = int(max(bw, bh) * 0.6)
            crop = image[max(0, by - m):min(h, by + bh + m), max(0, bx - m):min(w, bx + bw + m)]
            tight = image[max(0, by):min(h, by + bh), max(0, bx):min(w, bx + bw)]
        else:
            m = int(max(h, w) * 0.3)
            crop = cv2.copyMakeBorder(image, m, m, m, m, cv2.BORDER_REPLICATE)
            tight = image
        if crop.size == 0 or tight.size == 0:
            return None
        scale = min(1.0, 640.0 / max(crop.shape[:2]))  # YuNet is fast on small inputs
        small = crop if scale == 1.0 else cv2.resize(crop, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
        try:
            with self._model_lock:
                self._yunet.setInputSize((small.shape[1], small.shape[0]))
                _, faces = self._yunet.detect(small)
                if faces is not None and len(faces):
                    f = max(faces, key=lambda r: r[2] * r[3]).copy()
                    f[:14] /= scale
                    # A strongly turned head (field 09/11: looking sideways gave "Người lạ") embeds far from the
                    # registered frontal photos: such a frame may keep the identity but never rejects it.
                    face_img, aligned = self._sface.alignCrop(crop, f), self._frontal_enough(f)
                else:
                    face_img, aligned = cv2.resize(tight, (112, 112), interpolation=cv2.INTER_AREA), False
                feat = self._sface.feature(face_img).flatten().astype(np.float32)
        except Exception as e:
            logger.debug("SFace embedding failed: %s", e)
            return None
        n = float(np.linalg.norm(feat))
        return (feat / n, aligned) if n > 0 else None

    @staticmethod
    def _frontal_enough(f) -> bool:
        """YuNet row: x, y, w, h, right eye, left eye, nose, right mouth, left mouth, score."""
        rex, rey, lex, ley, nx = f[4], f[5], f[6], f[7], f[8]
        eye_dist = float(np.hypot(lex - rex, ley - rey))
        if eye_dist < 0.25 * float(f[2]):  # eyes nearly on top of each other: profile
            return False
        offset = (nx - (rex + lex) / 2.0) / eye_dist  # 0 frontal, about +-0.5 at 45 degrees
        return abs(offset) <= 0.38

    def _sface_best(self, vec: np.ndarray) -> Tuple[Optional[str], float]:
        """Best registered patient and its cosine similarity (mean of the 3 closest registered photos)."""
        best_pid, best = None, -1.0
        for pid, gal in self._gallery.items():
            sims = gal @ vec
            k = min(3, len(sims))
            score = float(np.mean(np.sort(sims)[-k:]))
            if score > best:
                best_pid, best = pid, score
        return best_pid, best

    def _load_detector(self) -> None:
        """Load OpenCV DNN face detector with Haar Cascade fallback."""
        pb_path = resource_path("features/face_monitoring/standalone/models/opencv_face_detector_uint8.pb")
        if not pb_path.exists():
            pb_path = self.module_dir / "models" / "opencv_face_detector_uint8.pb"
        pbtxt_path = resource_path("features/face_monitoring/standalone/models/opencv_face_detector.pbtxt")
        if not pbtxt_path.exists():
            pbtxt_path = self.module_dir / "models" / "opencv_face_detector.pbtxt"
        try:
            if pb_path.exists() and pbtxt_path.exists():
                self._net = cv2.dnn.readNetFromTensorflow(
                    np.frombuffer(pb_path.read_bytes(), dtype=np.uint8),
                    np.frombuffer(pbtxt_path.read_bytes(), dtype=np.uint8))
                logger.info("Loaded OpenCV DNN Face Detector model.")
        except Exception as e:
            logger.warning("Failed to load DNN face detector: %s", e)

        if self._net is None:
            try:
                haar_path = cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
                if os.path.exists(haar_path):
                    self._haar_cascade = cv2.CascadeClassifier(haar_path)
                    logger.info("Loaded Haar Cascade face detector fallback.")
            except Exception as e:
                logger.warning("Failed to load Haar Cascade: %s", e)

    def detect_face(
        self,
        frame: np.ndarray,
        conf_threshold: float = 0.45,
        prior_box: Optional[Tuple[int, int, int, int]] = None,
    ) -> Optional[Tuple[int, int, int, int, float]]:
        """
        Detect the primary face in frame.
        Uses OpenCV DNN face detector if available. Falls back to Haar Cascade
        strictly if DNN failed to load (never as frame-by-frame fallback).
        Returns: (x, y, w, h, confidence) or None.
        """
        if frame is None or frame.size == 0:
            return None

        h, w = frame.shape[:2]

        if self._net is not None:
            try:
                blob = cv2.dnn.blobFromImage(frame, 1.0, (300, 300), [104, 117, 123], False, False)
                self._net.setInput(blob)
                detections = self._net.forward()
                best_box = None
                best_score = -1.0

                for i in range(detections.shape[2]):
                    conf = float(detections[0, 0, i, 2])
                    if conf >= conf_threshold:
                        x1 = max(0, int(detections[0, 0, i, 3] * w))
                        y1 = max(0, int(detections[0, 0, i, 4] * h))
                        x2 = min(w, int(detections[0, 0, i, 5] * w))
                        y2 = min(h, int(detections[0, 0, i, 6] * h))
                        bw, bh = x2 - x1, y2 - y1

                        # Validate face aspect ratio (human faces typically 0.55 to 1.6) and minimum size
                        if bw >= 35 and bh >= 35:
                            aspect = bw / float(bh)
                            if 0.50 <= aspect <= 1.70:
                                # Prominence base score
                                score = conf * (bw * bh)
                                # If prior_box is provided, give strong preference to spatial continuity
                                if prior_box is not None:
                                    px, py, pw, ph = prior_box[:4]
                                    cdist_sq = ((x1 + bw / 2.0) - (px + pw / 2.0)) ** 2 + ((y1 + bh / 2.0) - (py + ph / 2.0)) ** 2
                                    pdiag_sq = pw ** 2 + ph ** 2
                                    if cdist_sq < pdiag_sq * 2.0:
                                        score *= 2.0  # continuity boost

                                if score > best_score:
                                    best_score = score
                                    best_box = (x1, y1, bw, bh, conf)

                if best_box is not None:
                    return best_box
                # If DNN is active but no face matches threshold, return None (do not fallback to noisy Haar)
                return None
            except Exception as e:
                logger.debug("DNN detection error: %s", e)
                return None

        elif self._haar_cascade is not None:
            try:
                gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
                faces = self._haar_cascade.detectMultiScale(
                    gray, scaleFactor=1.1, minNeighbors=6, minSize=(60, 60)
                )
                if len(faces) > 0:
                    largest = max(faces, key=lambda b: b[2] * b[3])
                    return (int(largest[0]), int(largest[1]), int(largest[2]), int(largest[3]), 0.85)
            except Exception as e:
                logger.debug("Haar detection error: %s", e)

        return None

    def crop_face(
        self,
        frame: np.ndarray,
        box: Optional[Any] = None,
        padding_ratio: float = 0.15,
    ) -> np.ndarray:
        """Crop face region with standardized padding (default 15%), returning BGR image."""
        if frame is None or frame.size == 0:
            return np.zeros((100, 100, 3), dtype=np.uint8)

        if box is not None:
            bx, by, bw, bh = box[:4]
            h, w = frame.shape[:2]
            pad_x = int(bw * padding_ratio)
            pad_y = int(bh * padding_ratio)
            x1 = max(0, bx - pad_x)
            y1 = max(0, by - pad_y)
            x2 = min(w, bx + bw + pad_x)
            y2 = min(h, by + bh + pad_y)
            return frame[y1:y2, x1:x2].copy()

        return frame.copy()

    def extract_face_roi(
        self,
        frame: np.ndarray,
        box: Optional[Any] = None,
        padding_ratio: float = 0.15,
        target_size: Tuple[int, int] = (200, 200),
    ) -> Optional[np.ndarray]:
        """
        Extract canonical normalized face ROI for training and recognition:
        1. If box is provided, crop with standardized padding.
        2. Convert to grayscale.
        3. Apply CLAHE (Contrast Limited Adaptive Histogram Equalization) to normalize illumination.
        4. Apply mild bilateral filter to eliminate sensor noise while keeping sharp facial features.
        5. Resize to canonical target_size (200, 200).
        """
        if frame is None or frame.size == 0:
            return None

        h, w = frame.shape[:2]
        if box is not None:
            bx, by, bw, bh = box[:4]
            pad_x = int(bw * padding_ratio)
            pad_y = int(bh * padding_ratio)
            x1 = max(0, bx - pad_x)
            y1 = max(0, by - pad_y)
            x2 = min(w, bx + bw + pad_x)
            y2 = min(h, by + bh + pad_y)
            crop = frame[y1:y2, x1:x2]
        else:
            # If no box given, check if already a cropped portrait (w, h <= 400), use directly
            crop = frame

        if crop.size == 0:
            return None

        # Grayscale
        if len(crop.shape) == 3:
            gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
        else:
            gray = crop

        # Illumination normalization with CLAHE
        clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
        eq = clahe.apply(gray)

        # Bilateral filter to smooth sensor noise while preserving sharp facial edges
        filtered = cv2.bilateralFilter(eq, d=5, sigmaColor=25, sigmaSpace=25)

        # Resize to standard size
        normalized = cv2.resize(filtered, target_size, interpolation=cv2.INTER_AREA)
        return normalized

    def train_recognizer(self) -> bool:
        """
        Scan registered_faces directory, extract face features with CLAHE,
        apply data augmentation, add background reference class, and train LBPH recognizer.
        """
        with self._model_lock:
            if not self.registered_faces_dir.exists():
                self._gallery = {}
                self._is_trained = False
                self._recognizer = None
                self._label_to_id = {}
                self._id_to_label = {}
                return False

            faces: List[np.ndarray] = []
            labels: List[int] = []
            label_to_id: Dict[int, str] = {}
            id_to_label: Dict[str, int] = {}
            current_label = 1
            gallery: Dict[str, np.ndarray] = {}

            patient_dirs = [p for p in self.registered_faces_dir.iterdir() if p.is_dir()]

            for pdir in sorted(patient_dirs):
                pid = pdir.name
                img_files = list(pdir.glob("*.jpg")) + list(pdir.glob("*.png")) + list(pdir.glob("*.jpeg"))
                if not img_files:
                    continue

                label_to_id[current_label] = pid
                id_to_label[pid] = current_label

                patient_samples = []
                embeddings = []
                for img_path in img_files:
                    img = cv2.imread(str(img_path))
                    if img is None:
                        continue
                    emb = self._embed(img)
                    if emb is not None and emb[1]:
                        embeddings.append(emb[0])

                    # Canonical extraction WITHOUT double-cropping
                    roi = self.extract_face_roi(img)
                    if roi is not None:
                        patient_samples.append(roi)

                # Apply data augmentation for robust recognition across lighting, small angles & expressions
                for s in patient_samples:
                    # 1. Original
                    faces.append(s)
                    labels.append(current_label)
                    # 2. Horizontal flip
                    faces.append(cv2.flip(s, 1))
                    labels.append(current_label)
                    # 3. Brightness variations (+12, -12)
                    faces.append(np.clip(s.astype(np.int16) + 12, 0, 255).astype(np.uint8))
                    labels.append(current_label)
                    faces.append(np.clip(s.astype(np.int16) - 12, 0, 255).astype(np.uint8))
                    labels.append(current_label)
                    # 4. Slight rotation (-4°, +4°)
                    for angle in [-4, 4]:
                        M = cv2.getRotationMatrix2D((100, 100), angle, 1.0)
                        rot = cv2.warpAffine(s, M, (200, 200), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT)
                        faces.append(rot)
                        labels.append(current_label)

                if embeddings:
                    gallery[pid] = np.stack(embeddings)
                current_label += 1

            self._gallery = gallery
            if self._sface is not None:
                logger.info("SFace gallery: %s", {k: len(v) for k, v in gallery.items()})
                missing = [p for p in label_to_id.values() if p not in gallery]
                if missing:
                    logger.warning("No usable face photo for SFace in registered_faces/%s; re-register these users", missing)

            if len(faces) >= 2 and len(label_to_id) >= 1:
                try:
                    new_rec = cv2.face.LBPHFaceRecognizer_create(
                        radius=1, neighbors=8, grid_x=8, grid_y=8
                    )
                    new_rec.train(faces, np.array(labels, dtype=np.int32))
                    self._recognizer = new_rec
                    self._label_to_id = label_to_id
                    self._id_to_label = id_to_label
                    self._is_trained = True
                    logger.info(
                        "Trained LBPH Face Recognizer with %d augmented samples across %d registered patients.",
                        len(faces),
                        len(label_to_id),
                    )
                    return True
                except Exception as e:
                    logger.error("Failed to train LBPH face recognizer: %s", e)
                    self._is_trained = bool(self._gallery)
                    return self._is_trained
            else:
                self._is_trained = bool(self._gallery)
                self._recognizer = None
                self._label_to_id = label_to_id
                self._id_to_label = id_to_label
                logger.info("Not enough face samples to train recognizer (found %d samples).", len(faces))
                return False

    def _evaluate_temporal_consensus(self, status: str, pid: str, distance: float = 40.0) -> Dict[str, Any]:
        """Apply sliding window temporal consensus logic on incoming face classification."""
        if status == "recognized":
            self._consecutive_unknown_count = 0
            self._pred_history.append({"status": "recognized", "pid": pid, "distance": float(distance)})
        else:
            self._consecutive_unknown_count += 1
            self._pred_history.append({"status": "unknown", "pid": "unknown", "distance": float(distance)})

        recognized_matches = [p for p in self._pred_history if p["status"] == "recognized"]
        if recognized_matches and self._consecutive_unknown_count < 3:
            best_match = recognized_matches[-1]
            return {"status": "recognized", "patient_id": best_match["pid"]}
        else:
            return {"status": "unknown", "patient_id": "unknown"}

    # ------------------------------------------------------------------ per-frame match
    def _frame_verdict(self, frame: np.ndarray, box, now: float) -> Tuple[str, Optional[str], float, float]:
        """One frame on its own: ("match" | "reject" | "unsure", patient id, similarity 0..1, LBPH-like distance)."""
        if self._sface is not None and self._gallery:
            # While a tracked identity was confirmed less than a second ago, embed ~4x/s instead of every frame.
            if (self._track_pid and now - self._track_last_match < 1.0 and now - self._last_embed_ts < 0.25
                    and self._box_continuous(box)):
                return "unsure", self._track_pid, self._track_best_score, 0.0
            self._last_embed_ts = now
            emb = self._embed(frame, box)
            if emb is None:
                return "unsure", None, 0.0, 999.0
            vec, aligned = emb
            pid, sim = self._sface_best(vec)
            dist = round(max(0.0, 1.0 - sim) * 100.0, 1)  # kept for logs / old consumers
            if sim >= SFACE_ACCEPT and aligned:
                return "match", pid, sim, dist
            if sim < SFACE_REJECT and aligned:
                return "reject", pid, sim, dist
            return "unsure", pid, sim, dist

        roi = self.extract_face_roi(frame, box=box)
        if roi is None or self._recognizer is None:
            return "unsure", None, 0.0, 999.0
        int_label, distance = self._recognizer.predict(roi)
        pid = self._label_to_id.get(int_label)
        sim = max(0.0, min(1.0, 1.0 - distance / 100.0))
        if pid is not None and distance < RECOGNITION_THRESHOLD:
            return "match", pid, sim, float(distance)
        if distance > LBPH_REJECT:
            return "reject", pid, sim, float(distance)
        return "unsure", pid, sim, float(distance)

    def _box_continuous(self, box) -> bool:
        """The face box is where the tracked face was (the same person, not someone else in the frame)."""
        if self._track_box is None or box is None:
            return False
        px, py, pw, ph = self._track_box
        bx, by, bw, bh = box[:4]
        dx = (bx + bw / 2.0) - (px + pw / 2.0)
        dy = (by + bh / 2.0) - (py + ph / 2.0)
        return dx * dx + dy * dy <= (pw * pw + ph * ph) * 1.0 and 0.5 <= (bw * bh) / max(1.0, pw * ph) <= 2.0

    def _confidence(self, score: float) -> float:
        if self._sface is not None:
            return float(min(0.99, max(0.60, 0.60 + 0.39 * (score - SFACE_ACCEPT) / max(1e-6, 0.85 - SFACE_ACCEPT))))
        return float(min(0.99, max(0.60, score)))

    def _result(self, status: str, pid: Optional[str], box, score: float, distance: float) -> Dict[str, Any]:
        bx, by, bw, bh = box[:4]
        if status in ("recognized", "uncertain") and pid:
            profile = self.get_patient_profile(pid) or {}
            name = profile.get("name", f"Bệnh nhân {pid}")
            caregiver = profile.get("caregiver") or {}
            caregiver_email = caregiver.get("email") if isinstance(caregiver, dict) else ""
            if status == "uncertain":
                name = f"{name} (Đang xác minh)"
            return {
                "status": status, "user_id": pid, "patient_id": pid, "name": name,
                "confidence": self._confidence(score) if status == "recognized" else 0.55,
                "similarity": round(float(score), 3), "distance": float(distance), "box": (bx, by, bw, bh),
                "profile": profile, "caregiver_email": caregiver_email, "engine": self.engine,
            }
        if status == "uncertain":
            return {"status": "uncertain", "user_id": None, "patient_id": None, "name": "Đang xác minh",
                    "confidence": 0.0, "similarity": round(float(score), 3), "distance": float(distance),
                    "box": (bx, by, bw, bh), "profile": None, "caregiver_email": "", "engine": self.engine}
        return {"status": "unknown", "user_id": "unknown", "patient_id": "unknown", "name": "Người lạ (Chưa đăng ký)",
                "confidence": 0.0, "similarity": round(float(score), 3), "distance": float(distance),
                "box": (bx, by, bw, bh), "profile": None, "caregiver_email": "", "engine": self.engine}

    def recognize_face(self, frame: np.ndarray) -> Dict[str, Any]:
        """
        Detect the main face, score it against the registered patients (SFace, LBPH fallback) and decide
        with tracking + hysteresis (see the module header):
          recognized  this frame matched, or the tracked patient's face is still in the same place and
                      nothing clearly contradicts it (hand over the mouth, head turned, blur)
          unknown     several clearly-different frames for >= 1.5 s, or a new face that never matched
          uncertain   a new face whose first frames are inconclusive
        """
        now = time.time()
        detected = self.detect_face(frame)
        if detected is None:
            self._frames_without_face += 1
            if self._track_pid and now - self._last_face_seen > self._identity_ttl:
                self._track_pid, self._track_box = None, None
            if self._frames_without_face >= 5:
                self._pred_history.clear()
                self._reject_count, self._reject_since = 0, None
            return {"status": "no_face", "patient_id": None, "name": None, "confidence": 0.0,
                    "distance": 999.0, "box": None, "profile": None}

        self._frames_without_face = 0
        self._last_face_seen = now
        box = tuple(int(v) for v in detected[:4])
        with self._model_lock:
            if not self._is_trained:
                return {"status": "unknown", "patient_id": "unknown", "name": "Chưa có dữ liệu đăng ký",
                        "confidence": 0.0, "distance": 999.0, "box": box, "profile": None}
            try:
                verdict, pid, score, distance = self._frame_verdict(frame, box, now)
            except Exception as e:
                logger.error("Recognition error: %s", e)
                verdict, pid, score, distance = "unsure", None, 0.0, 999.0

        continuous = self._box_continuous(box)
        if self._track_pid and not continuous and verdict != "match":
            # The face moved somewhere else in the picture: it has to be matched again.
            self._track_pid = None
        self._track_box = box
        self._pred_history.append({"status": verdict, "pid": pid, "distance": float(distance)})

        if verdict == "match":
            if self._track_pid != pid:
                self._track_best_score = score
            self._track_pid, self._track_last_match = pid, now
            self._track_best_score = max(score, 0.7 * self._track_best_score + 0.3 * score)
            self._reject_count, self._reject_since = 0, None
            self._last_stable_pid, self._last_recognized_time = pid, now
            return self._result("recognized", pid, box, score, distance)

        if verdict == "reject":
            if self._reject_since is not None and now - self._last_reject_ts > 3.0:
                self._reject_count, self._reject_since = 0, None  # old, isolated rejects do not add up
            self._reject_count += 1
            self._reject_since = self._reject_since or now
            self._last_reject_ts = now
        stranger = (self._reject_count >= STRANGER_MIN_FRAMES and self._reject_since is not None
                    and now - self._reject_since >= STRANGER_MIN_SECONDS)

        if self._track_pid and now - self._track_last_match <= self._identity_ttl and not stranger:
            return self._result("recognized", self._track_pid, box, self._track_best_score, distance)
        if stranger:
            if self._track_pid:
                logger.info("Face no longer matches %s: treated as a different person", self._track_pid)
            self._track_pid = None
            self._last_stable_pid = None
            self._stranger_confirmed_at = now
            return self._result("unknown", None, box, score, distance)
        if self._track_pid:  # tracked but the hold expired without a fresh match: re-verify
            return self._result("uncertain", self._track_pid, box, score, distance)
        return self._result("uncertain", None, box, score, distance)

    def reset_tracking(self) -> None:
        """Forget the tracked face (tests, camera source change)."""
        self._pred_history.clear()
        self._track_pid, self._track_box, self._track_last_match, self._track_best_score = None, None, 0.0, 0.0
        self._reject_count, self._reject_since, self._last_reject_ts = 0, None, 0.0
        self._consecutive_unknown_count = 0
        self._last_stable_pid, self._last_recognized_time = None, 0.0
        self._last_embed_ts = 0.0

    def present_patient(self, max_age: float = 30.0) -> Optional[str]:
        """Registered patient seen within `max_age` s with no stranger since (the face may be hidden right now,
        e.g. a hand in front of the mouth while taking a pill). Used by the AI core (medication intake)."""
        now = time.time()
        pid = self._last_stable_pid
        if not pid or not self._last_recognized_time:
            return None
        if now - self._last_recognized_time > max_age or self._stranger_confirmed_at > self._last_recognized_time:
            return None
        return pid

    def draw_tracking_overlay(
        self,
        frame: np.ndarray,
        patient_id: Optional[str] = None,
        patient_name: Optional[str] = None,
    ) -> np.ndarray:
        """
        Draw dynamic tracking bounding box and high-contrast labels:
        - 🟢 Emerald Green: Recognized patient from database
        - 🟠 Amber/Orange: Unknown person / unregistered face
        - Subtle status text: Searching for face
        """
        rec_res = self.recognize_face(frame)
        self._last_recognition = rec_res
        from shared.camera.drawing_utils import draw_vietnamese_text, get_text_size

        status = rec_res["status"]

        if status in ("recognized", "uncertain", "unknown") and rec_res["box"] is not None:
            bx, by, bw, bh = rec_res["box"][:4]

            # Smooth box movement
            if self._last_face_box is not None:
                alpha = 0.65
                lbx, lby, lbw, lbh = self._last_face_box
                bx = int(alpha * bx + (1 - alpha) * lbx)
                by = int(alpha * by + (1 - alpha) * lby)
                bw = int(alpha * bw + (1 - alpha) * lbw)
                bh = int(alpha * bh + (1 - alpha) * lbh)

            self._last_face_box = (bx, by, bw, bh)
            self._frames_without_face = 0

            if status == "recognized":
                pid = rec_res["patient_id"]
                name = rec_res["name"]
                conf = rec_res["confidence"]

                # Emerald Green BBox
                box_color = (128, 222, 74)   # BGR: Green
                corner_color = (160, 255, 110)

                # Draw BBox
                cv2.rectangle(frame, (bx, by), (bx + bw, by + bh), box_color, 2)

                # Tech corner accents
                clen = max(12, min(bw, bh) // 5)
                cv2.line(frame, (bx, by), (bx + clen, by), corner_color, 3)
                cv2.line(frame, (bx, by), (bx, by + clen), corner_color, 3)
                cv2.line(frame, (bx + bw, by), (bx + bw - clen, by), corner_color, 3)
                cv2.line(frame, (bx + bw, by), (bx + bw, by + clen), corner_color, 3)
                cv2.line(frame, (bx, by + bh), (bx + clen, by + bh), corner_color, 3)
                cv2.line(frame, (bx, by + bh), (bx, by + bh - clen), corner_color, 3)
                cv2.line(frame, (bx + bw, by + bh), (bx + bw - clen, by + bh), corner_color, 3)
                cv2.line(frame, (bx + bw, by + bh), (bx + bw, by + bh - clen), corner_color, 3)

                # Compact, bounded tag (full details in outer UI cards)
                display_name = name[:16] + ".." if len(name) > 16 else name
                tag_text = display_name
                font_sz = 13
                tw, th = get_text_size(tag_text, font_size=font_sz)
                tag_w = tw + 12
                tag_h = th + 6

                fh, fw = frame.shape[:2]
                tx = max(8, min(bx, fw - tag_w - 8))
                ty = by - tag_h - 4 if by - tag_h - 4 >= 8 else min(by + bh + 4, fh - tag_h - 8)

                cv2.rectangle(frame, (tx, ty), (tx + tag_w, ty + tag_h), (18, 24, 36), -1)
                cv2.rectangle(frame, (tx, ty), (tx + tag_w, ty + tag_h), box_color, 1)
                draw_vietnamese_text(frame, tag_text, (tx + 6, ty + 3), font_size=font_sz, color=(74, 222, 128))

                # Record recognition in JSON stores
                self._update_face_result(patient_id=pid, name=name, status="recognized", confidence=conf)

            elif status == "uncertain":
                pid = rec_res.get("patient_id")
                name = rec_res.get("name")
                conf = rec_res.get("confidence", 0.5)

                box_color = (0, 215, 255)     # BGR: Yellow-Gold
                corner_color = (50, 230, 255)

                cv2.rectangle(frame, (bx, by), (bx + bw, by + bh), box_color, 2)

                clen = max(12, min(bw, bh) // 5)
                cv2.line(frame, (bx, by), (bx + clen, by), corner_color, 3)
                cv2.line(frame, (bx, by), (bx, by + clen), corner_color, 3)
                cv2.line(frame, (bx + bw, by), (bx + bw - clen, by), corner_color, 3)
                cv2.line(frame, (bx + bw, by), (bx + bw, by + clen), corner_color, 3)
                cv2.line(frame, (bx, by + bh), (bx + clen, by + bh), corner_color, 3)
                cv2.line(frame, (bx, by + bh), (bx, by + bh - clen), corner_color, 3)
                cv2.line(frame, (bx + bw, by + bh), (bx + bw - clen, by + bh), corner_color, 3)
                cv2.line(frame, (bx + bw, by + bh), (bx + bw, by + bh - clen), corner_color, 3)

                tag_text = "Đang xác minh..."
                font_sz = 12
                tw, th = get_text_size(tag_text, font_size=font_sz)
                tag_w = tw + 12
                tag_h = th + 6

                fh, fw = frame.shape[:2]
                tx = max(8, min(bx, fw - tag_w - 8))
                ty = by - tag_h - 4 if by - tag_h - 4 >= 8 else min(by + bh + 4, fh - tag_h - 8)

                cv2.rectangle(frame, (tx, ty), (tx + tag_w, ty + tag_h), (18, 24, 36), -1)
                cv2.rectangle(frame, (tx, ty), (tx + tag_w, ty + tag_h), box_color, 1)
                draw_vietnamese_text(frame, tag_text, (tx + 6, ty + 3), font_size=font_sz, color=(250, 204, 21))

                self._update_face_result(patient_id=pid or "uncertain", name=name or "Đang xác minh", status="uncertain", confidence=conf)

            else:
                # Amber / Warning Orange for Unknown
                box_color = (0, 165, 245)     # BGR: Amber
                corner_color = (30, 200, 255)

                cv2.rectangle(frame, (bx, by), (bx + bw, by + bh), box_color, 2)

                clen = max(12, min(bw, bh) // 5)
                cv2.line(frame, (bx, by), (bx + clen, by), corner_color, 3)
                cv2.line(frame, (bx, by), (bx, by + clen), corner_color, 3)
                cv2.line(frame, (bx + bw, by), (bx + bw - clen, by), corner_color, 3)
                cv2.line(frame, (bx + bw, by), (bx + bw, by + clen), corner_color, 3)
                cv2.line(frame, (bx, by + bh), (bx + clen, by + bh), corner_color, 3)
                cv2.line(frame, (bx, by + bh), (bx, by + bh - clen), corner_color, 3)
                cv2.line(frame, (bx + bw, by + bh), (bx + bw - clen, by + bh), corner_color, 3)
                cv2.line(frame, (bx + bw, by + bh), (bx + bw, by + bh - clen), corner_color, 3)

                tag_text = "Người lạ"
                font_sz = 12
                tw, th = get_text_size(tag_text, font_size=font_sz)
                tag_w = tw + 12
                tag_h = th + 6

                fh, fw = frame.shape[:2]
                tx = max(8, min(bx, fw - tag_w - 8))
                ty = by - tag_h - 4 if by - tag_h - 4 >= 8 else min(by + bh + 4, fh - tag_h - 8)

                cv2.rectangle(frame, (tx, ty), (tx + tag_w, ty + tag_h), (18, 24, 36), -1)
                cv2.rectangle(frame, (tx, ty), (tx + tag_w, ty + tag_h), box_color, 1)
                draw_vietnamese_text(frame, tag_text, (tx + 6, ty + 3), font_size=font_sz, color=(245, 158, 11))

                self._update_face_result(patient_id="unknown", name="Người lạ", status="unknown", confidence=0.0)

        else:
            self._frames_without_face += 1
            if self._frames_without_face > 10:
                self._last_face_box = None
            # Keep camera frame clean; outer UI card displays scanning status

        return frame

    def get_latest_recognition(self) -> Dict[str, Any]:
        """Return the most recent recognition result dict."""
        return self._last_recognition or {
            "status": "no_face",
            "patient_id": None,
            "name": None,
            "confidence": 0.0,
            "distance": 999.0,
            "box": None,
            "profile": None,
        }

    # ==================== REGISTRATION & PROFILE MANAGEMENT ====================
    def register_patient(
        self,
        name: str,
        patient_id: Optional[str] = None,
        age: int = 70,
        gender: str = "Nam",
        notes: str = "",
        face_images: Optional[List[np.ndarray]] = None,
        caregiver_name: str = "",
        caregiver_relationship: str = "Người thân",
        caregiver_email: str = "",
        caregiver_phone: str = "",
        caregiver_password: str = "",
        caregiver: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Any]:
        """
        Register a new patient or update existing profile:
        1. Write metadata into shared/data/patients.json and data/users/{pid}.json.
        2. Save face images into shared/data/registered_faces/{patient_id}/.
        3. Trigger recognizer re-training.
        4. Update active patient.json and state.json.
        """
        if patient_id is not None and str(patient_id).strip():
            pid = str(patient_id).strip()
        else:
            existing = self.get_all_patients_dict()
            pid = "1" if not existing else str(max([int(k) for k in existing.keys() if k.isdigit()] + [0]) + 1)

        pname = name.strip() or f"Bệnh nhân {pid}"

        # 1. Prepare directory
        p_dir = self.registered_faces_dir / pid
        p_dir.mkdir(parents=True, exist_ok=True)

        # 2. Save captured face images if provided
        saved_count = len(list(p_dir.glob("*.jpg")))
        if face_images:
            for idx, img in enumerate(face_images):
                if img is not None and img.size > 0:
                    saved_count += 1
                    fn = f"face_{saved_count:02d}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.jpg"
                    cv2.imwrite(str(p_dir / fn), img)

        # 3. Save profile to patients.json and data/users/{pid}.json
        patients = self.get_all_patients_dict()
        caregiver_obj = caregiver or {
            "name": caregiver_name.strip() or "Người chăm sóc",
            "relationship": caregiver_relationship.strip() or "Người thân",
            "email": caregiver_email.strip(),
            "phone": caregiver_phone.strip(),
            "password": caregiver_password.strip(),
        }
        from shared.services.credential_security import protect_profile_credentials
        caregiver_obj = protect_profile_credentials(caregiver_obj)
        patient_data = {
            "user_id": pid,
            "patient_id": pid,
            "name": pname,
            "age": int(age),
            "gender": gender,
            "notes": notes,
            "caregiver": caregiver_obj,
            "registered_at": patients.get(pid, {}).get("registered_at", datetime.now().isoformat()),
            "last_updated": datetime.now().isoformat(),
            "image_count": saved_count,
        }
        patients[pid] = patient_data

        try:
            # Save to users directory
            users_dir = self.users_dir
            users_dir.mkdir(parents=True, exist_ok=True)
            with open(users_dir / f"{pid}.json", "w", encoding="utf-8") as f:
                json.dump(patient_data, f, ensure_ascii=False, indent=2)

            with open(self.patients_file, "w", encoding="utf-8") as f:
                json.dump(patients, f, ensure_ascii=False, indent=2)

            with open(self.patient_file, "w", encoding="utf-8") as f:
                json.dump(patient_data, f, ensure_ascii=False, indent=2)

            if self.state_file.exists():
                with open(self.state_file, "r", encoding="utf-8") as f:
                    state = json.load(f)
                state["name"] = pname
                state["patient_id"] = pid
                state["last_update"] = datetime.now().isoformat()
                with open(self.state_file, "w", encoding="utf-8") as f:
                    json.dump(state, f, ensure_ascii=False, indent=2)

            logger.info("Patient registration saved")
        except Exception as e:
            logger.error("Failed writing patient data: %s", e)

        # Sync to unified PostgreSQL/SQLite database
        if not self._is_isolated or self.db is not None:
            try:
                from shared.database.connection import UnifiedDatabaseManager
                from shared.services.patient_sync_service import sync_single_patient
                target_db = self.db or UnifiedDatabaseManager()
                sync_single_patient(target_db, patient_data)
            except Exception as e:
                logger.warning("Error syncing registered patient to unified database: %s", e)

        # 4. Retrain recognizer with new patient data
        self.train_recognizer()

        return patient_data

    def get_all_patients_dict(self) -> Dict[str, Dict[str, Any]]:
        """Return dict of all registered patients keyed by patient_id."""
        if self.patients_file.exists():
            try:
                with open(self.patients_file, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception as e:
                logger.error("Error reading patients file: %s", e)
        return {}

    def get_all_patients(self) -> List[Dict[str, Any]]:
        """Return list of all registered patients."""
        return list(self.get_all_patients_dict().values())

    def get_patient_profile(self, patient_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """Retrieve stored patient profile by ID, or fallback to active patient.json if no ID given."""
        patients = self.get_all_patients_dict()
        if patient_id is not None:
            return patients.get(str(patient_id).strip())

        if self.patient_file.exists():
            try:
                with open(self.patient_file, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception as e:
                logger.error("Failed to read patient file: %s", e)

        return None

    def delete_patient(self, patient_id: str) -> bool:
        """Delete patient profile and face images, then retrain."""
        pid = str(patient_id).strip()
        patients = self.get_all_patients_dict()
        if pid in patients:
            del patients[pid]
            with open(self.patients_file, "w", encoding="utf-8") as f:
                json.dump(patients, f, ensure_ascii=False, indent=2)

            # Clean active patient.json if it was this patient
            if self.patient_file.exists():
                try:
                    with open(self.patient_file, "r", encoding="utf-8") as f:
                        cur_active = json.load(f)
                    if cur_active.get("patient_id") == pid:
                        fallback_p = next(iter(patients.values()), None)
                        if fallback_p:
                            with open(self.patient_file, "w", encoding="utf-8") as f:
                                json.dump(fallback_p, f, ensure_ascii=False, indent=2)
                        else:
                            self.patient_file.unlink(missing_ok=True)
                except Exception as e:
                    logger.debug("Failed cleaning active patient file: %s", e)

            # Remove user JSON file
            (self.users_dir / f"{pid}.json").unlink(missing_ok=True)

            # Remove images
            p_dir = self.registered_faces_dir / pid
            if p_dir.exists():
                shutil.rmtree(p_dir, ignore_errors=True)

            # Sync deletion to unified database
            if not self._is_isolated or self.db is not None:
                try:
                    from shared.database.connection import UnifiedDatabaseManager
                    from shared.services.patient_sync_service import resolve_unified_patient_id
                    target_db = self.db or UnifiedDatabaseManager()
                    with target_db.connect() as conn:
                        u_pid = resolve_unified_patient_id(conn, pid)
                        conn.execute("UPDATE patients SET status = 'INACTIVE' WHERE patient_id = ?", (u_pid,))
                        conn.commit()
                except Exception as e:
                    logger.warning("Error marking patient inactive in unified DB: %s", e)

            with self._model_lock:
                self._pred_history.clear()
                self._last_recognition = None
                self._last_stable_pid = None
                self._last_face_box = None
                self.train_recognizer()
            logger.info("Deleted patient ID %s and retrained model.", pid)
            return True
        return False

    def get_result(self) -> Dict[str, Any]:
        """Read latest face result from local JSON."""
        if self.result_file.exists():
            try:
                with open(self.result_file, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception as e:
                logger.error("Error reading face result file: %s", e)
        return {
            "patient_id": "1",
            "name": "Trần Văn Nam",
            "status": "idle",
            "confidence": 0.95,
            "timestamp": datetime.now().isoformat(),
        }

    def _update_face_result(
        self,
        patient_id: str = "1",
        name: Optional[str] = None,
        status: str = "normal",
        confidence: float = 0.95,
    ) -> None:
        """Helper to write face result to JSON."""
        current = self.get_result()
        if name:
            current["name"] = name
        current["patient_id"] = str(patient_id)
        current["status"] = status
        current["confidence"] = round(float(confidence), 3)
        current["timestamp"] = datetime.now().isoformat()
        try:
            with open(self.result_file, "w", encoding="utf-8") as f:
                json.dump(current, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error("Error writing face result: %s", e)

    # Backward compatibility stubs
    def start(self, mode: str = "recognize", input_source: Optional[str] = None) -> bool:
        self._update_face_result(status="running")
        return True

    def stop(self) -> None:
        self._update_face_result(status="stopped")

    def is_running(self) -> bool:
        return True
