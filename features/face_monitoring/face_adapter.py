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

# LBPH distance below which a face is the registered person. Measured on 15 faces (same-camera variation:
# the same person 17-20, other people 41+): 58 accepted 51 % of strangers as the patient, 40 accepted none.
# Register 15-20 photos in different light and angles; tune with AICARE_FACE_THRESHOLD if the elder is missed.
RECOGNITION_THRESHOLD = float(os.getenv("AICARE_FACE_THRESHOLD", "40"))


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
        self._identity_ttl: float = 2.0  # seconds to preserve identity during head turn or bowing

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

            patient_dirs = [p for p in self.registered_faces_dir.iterdir() if p.is_dir()]

            for pdir in sorted(patient_dirs):
                pid = pdir.name
                img_files = list(pdir.glob("*.jpg")) + list(pdir.glob("*.png")) + list(pdir.glob("*.jpeg"))
                if not img_files:
                    continue

                label_to_id[current_label] = pid
                id_to_label[pid] = current_label

                patient_samples = []
                for img_path in img_files:
                    img = cv2.imread(str(img_path))
                    if img is None:
                        continue

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

                current_label += 1

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
                    self._is_trained = False
                    return False
            else:
                self._is_trained = False
                self._recognizer = None
                self._label_to_id = {}
                self._id_to_label = {}
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

    def recognize_face(self, frame: np.ndarray) -> Dict[str, Any]:
        """
        Genuine Face Recognition pipeline:
        1. Detect primary face ROI.
        2. Extract canonical normalized face ROI (CLAHE + bilateral + 200x200).
        3. Predict identity using trained LBPH model.
        4. Match against registered patients in patients.json.
        """
        detected = self.detect_face(frame)
        if detected is None:
            self._frames_without_face += 1
            if self._frames_without_face >= 5:
                self._pred_history.clear()
                self._consecutive_unknown_count = 0
                self._last_stable_pid = None
            return {
                "status": "no_face",
                "patient_id": None,
                "name": None,
                "confidence": 0.0,
                "distance": 999.0,
                "box": None,
                "profile": None,
            }

        self._frames_without_face = 0
        bx, by, bw, bh, det_conf = detected
        roi = self.extract_face_roi(frame, box=detected)
        if roi is None:
            return {
                "status": "no_face",
                "patient_id": None,
                "name": None,
                "confidence": 0.0,
                "distance": 999.0,
                "box": detected,
                "profile": None,
            }

        with self._model_lock:
            if self._is_trained and self._recognizer is not None:
                try:
                    int_label, distance = self._recognizer.predict(roi)

                    # Raw single-frame match evaluation
                    is_raw_match = (distance < RECOGNITION_THRESHOLD and int_label in self._label_to_id)
                    raw_pid = self._label_to_id[int_label] if (int_label in self._label_to_id) else "unknown"

                    now = time.time()
                    if is_raw_match:
                        self._consecutive_unknown_count = 0
                        self._last_recognized_time = now
                        self._last_stable_pid = raw_pid
                        self._pred_history.append({"status": "recognized", "pid": raw_pid, "distance": float(distance)})
                    else:
                        self._consecutive_unknown_count += 1
                        self._pred_history.append({"status": "unknown", "pid": "unknown", "distance": float(distance)})

                    # Temporal consensus across sliding window
                    recognized_matches = [p for p in self._pred_history if p["status"] == "recognized"]
                    time_since_rec = (now - self._last_recognized_time) if self._last_recognized_time > 0 else 999.0

                    # 1. Reliable Recognition (raw match or smoothed match within consensus tolerance)
                    if is_raw_match or (recognized_matches and self._consecutive_unknown_count < 6 and time_since_rec <= self._identity_ttl):
                        best_match = recognized_matches[-1] if recognized_matches else {"pid": raw_pid}
                        pid = best_match["pid"]
                        smooth_dist = float(np.mean([p["distance"] for p in recognized_matches])) if recognized_matches else float(distance)

                        profile = self.get_patient_profile(pid) or {}
                        name = profile.get("name", f"Bệnh nhân {pid}")

                        # Calibrated smoothed confidence
                        conf_pct = min(0.99, max(0.65, 0.70 + 0.28 * (1.0 - (min(smooth_dist, RECOGNITION_THRESHOLD) / RECOGNITION_THRESHOLD))))
                        caregiver = profile.get("caregiver") or {}
                        caregiver_email = caregiver.get("email") if isinstance(caregiver, dict) else ""
                        self._last_stable_pid = pid

                        return {
                            "status": "recognized",
                            "user_id": pid,
                            "patient_id": pid,
                            "name": name,
                            "confidence": float(conf_pct),
                            "distance": float(smooth_dist),
                            "box": (bx, by, bw, bh),
                            "profile": profile,
                            "caregiver_email": caregiver_email,
                        }

                    # 2. Intermediate Uncertain State: User briefly turned head or bowed down
                    elif self._last_stable_pid and time_since_rec <= self._identity_ttl and self._consecutive_unknown_count < 15:
                        pid = self._last_stable_pid
                        profile = self.get_patient_profile(pid) or {}
                        name = profile.get("name", f"Bệnh nhân {pid}")
                        caregiver = profile.get("caregiver") or {}
                        caregiver_email = caregiver.get("email") if isinstance(caregiver, dict) else ""
                        return {
                            "status": "uncertain",
                            "user_id": pid,
                            "patient_id": pid,
                            "name": f"{name} (Đang xác minh)",
                            "confidence": 0.55,
                            "distance": float(distance),
                            "box": (bx, by, bw, bh),
                            "profile": profile,
                            "caregiver_email": caregiver_email,
                        }

                    # 3. Persistent Stranger
                    else:
                        self._last_stable_pid = None
                        return {
                            "status": "unknown",
                            "user_id": "unknown",
                            "patient_id": "unknown",
                            "name": "Người lạ (Chưa đăng ký)",
                            "confidence": 0.0,
                            "distance": float(distance),
                            "box": (bx, by, bw, bh),
                            "profile": None,
                            "caregiver_email": "",
                        }
                except Exception as e:
                    logger.error("Recognition error: %s", e)

        return {
            "status": "unknown",
            "patient_id": "unknown",
            "name": "Chưa có dữ liệu đăng ký",
            "confidence": 0.0,
            "distance": 999.0,
            "box": (bx, by, bw, bh),
            "profile": None,
        }

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
                tag_text = f"✓ {display_name}"
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

                tag_text = "🟡 Xác minh..."
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

                tag_text = "🟠 Người lạ"
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
