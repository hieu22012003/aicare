"""
Emotion Module Adapter for AI Rehabilitation Assistant.
Focuses strictly on 3 core emotional states:
1. Bình thường (Neutral / Relaxed resting state)
2. Vui vẻ (Happy / Smile - AU12 Zygomaticus Major)
3. Buồn bã (Sad / Frown - AU15 Depressor Anguli Oris & AU1/4)

Integrates MediaPipe FaceMesh biometric FACS kinematics, PyTorch ExpressionNet,
temporal smoothing across a sliding window, and crisp Vietnamese HUD rendering.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import time
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import cv2
import mediapipe as mp
import numpy as np

# Set writable matplotlib config dir
import tempfile
os.environ.setdefault("MPLCONFIGDIR", os.path.join(tempfile.gettempdir(), "matplotlib-cache"))

from shared.config import (
    EMOTION_MODULE_DIR,
    SHARED_RESULTS_DIR,
    SHARED_DIR,
    resource_path,
)
from shared.camera.drawing_utils import draw_pill_badge

# Ensure features/emotion/model is on sys.path for internal 'src' imports
EMOTION_ROOT = str(EMOTION_MODULE_DIR)
if EMOTION_ROOT not in sys.path:
    sys.path.insert(0, EMOTION_ROOT)

logger = logging.getLogger("EmotionAdapter")

EMOTION_TAU_SECONDS = 1.5  # time constant of the probability smoothing
EMOTION_SWITCH_MARGIN = 0.08  # another state must lead the shown one by this much ...
EMOTION_SWITCH_SECONDS = 1.0  # ... for this long before the shown label changes
EMOTION_MIN_CONFIDENCE = 0.42  # Vui vẻ / Buồn bã only above this smoothed probability

# 3 Focused Emotion States
EMOTION_CLASSES = ["Neutral", "Happy", "Sad"]

EMOTION_VI_MAP = {
    "Neutral": "Bình thường",
    "neutral": "Bình thường",
    "Happy": "Vui vẻ",
    "happy": "Vui vẻ",
    "Sad": "Buồn bã",
    "sad": "Buồn bã",
}


class EmotionAdapter:
    """Stable In-Memory 3-State Emotion Classifier with Biomechanical FACS & Smoothing."""

    def __init__(self, smoothing_window: int = 10, sad_sensitivity: float = 1.10) -> None:
        self.emotion_dir: Path = EMOTION_MODULE_DIR
        self.result_file: Path = SHARED_RESULTS_DIR / "emotion.json"
        self.state_file: Path = SHARED_DIR / "state.json"
        self.smoothing_window = smoothing_window
        self.sad_sensitivity = sad_sensitivity
        self.classes = EMOTION_CLASSES

        # Temporal smoothing circular buffer for probabilities [Neutral, Happy, Sad]
        self._prob_history: deque[np.ndarray] = deque(maxlen=smoothing_window)
        self._last_emotion = "Chưa phát hiện"
        self._last_emotion_en = "Unknown"
        self._last_confidence = 0.0
        self._last_face_time = 0.0
        self.face_ttl = 1.2  # Seconds before stale emotion is cleared
        self._ema: Optional[np.ndarray] = None
        self._ema_ts = 0.0
        self._stable_idx: Optional[int] = None
        self._challenger: Optional[int] = None
        self._challenger_since = 0.0
        self._model = None
        self._face_mesh = None
        self._face_landmarker = None

        self._init_model()

    def _init_model(self) -> None:
        """Load ExpressionNet PyTorch model and MediaPipe FaceMesh / FaceLandmarker."""
        # The neural net is optional: without torch or expression_net.pth the face geometry (FACS) still works.
        try:
            import torch  # noqa: F401
            from features.emotion.model.main import load_model
            from features.emotion.model.src.config import PATHS

            model_path = resource_path("features/emotion/model/models/expression_net.pth")
            if not model_path.exists():
                model_path = PATHS.model_path
            if model_path.exists():
                self._model = load_model(model_path)
                if self._model is not None:
                    self._model.eval()
                    logger.info("Loaded ExpressionNet PyTorch model successfully from %s.", model_path)
            else:
                logger.info("expression_net.pth not found; emotion uses face geometry (FACS) only.")
        except Exception as e:
            self._model = None
            logger.warning("Emotion neural net unavailable (%s); using face geometry (FACS) only.", e)

        try:
            import mediapipe as mp

            # 1. Try legacy MediaPipe solutions.face_mesh
            if hasattr(mp, "solutions") and hasattr(mp.solutions, "face_mesh"):
                mp_face_mesh = mp.solutions.face_mesh
                self._face_mesh = mp_face_mesh.FaceMesh(
                    refine_landmarks=True,
                    max_num_faces=1,
                    min_detection_confidence=0.5,
                    min_tracking_confidence=0.5,
                )
                logger.info("Initialized MediaPipe FaceMesh for emotion recognition.")
                return

            # 2. Modern MediaPipe Tasks FaceLandmarker
            try:
                from mediapipe.tasks.python import BaseOptions
                from mediapipe.tasks.python.vision import FaceLandmarker, FaceLandmarkerOptions, RunningMode

                task_candidates = [
                    resource_path("features/emotion/model/models/face_landmarker.task"),
                    Path("features/emotion/model/models/face_landmarker.task"),
                    Path(__file__).resolve().parent / "model" / "models" / "face_landmarker.task",
                ]
                task_path = None
                for cand in task_candidates:
                    if cand.exists():
                        task_path = str(cand)
                        break

                if task_path:
                    options = FaceLandmarkerOptions(
                        base_options=BaseOptions(model_asset_path=task_path),
                        running_mode=RunningMode.IMAGE,
                        num_faces=1,
                        min_face_detection_confidence=0.45,
                        min_face_presence_confidence=0.45,
                        min_tracking_confidence=0.45,
                    )
                    self._face_landmarker = FaceLandmarker.create_from_options(options)
                    logger.info("Initialized MediaPipe Tasks FaceLandmarker from %s.", task_path)
                else:
                    logger.info("MediaPipe FaceMesh/FaceLandmarker models not found; using baseline emotion recognition.")
            except Exception as ex:
                logger.debug("MediaPipe Tasks FaceLandmarker init failed: %s", ex)
        except Exception as e:
            logger.warning("Could not initialize MediaPipe face landmarks for emotion: %s", e)

    def _compute_facs_probabilities(self, landmarks: Any) -> np.ndarray:
        """
        Extract Facial Action Coding System (FACS) action units from MediaPipe FaceMesh:
        - AU12: Zygomaticus Major (Lip corner puller -> Happy/Smile)
        - AU15: Depressor Anguli Oris (Lip corner depressor -> Sad/Frown)
        - AU1 + AU4: Corrugator & Frontalis (Inner eyebrow contraction -> Sad)
        - Resting Equilibrium -> Neutral
        """
        # Landmark indices:
        # 33: Left outer eye, 263: Right outer eye
        # 61: Left mouth corner, 291: Right mouth corner
        # 13, 14: Upper & lower inner lip center (stomion)
        # 107: Left inner brow, 336: Right inner brow

        lms = landmarks.landmark if hasattr(landmarks, "landmark") else landmarks
        coords = np.array([[lm.x, lm.y, lm.z] for lm in lms], dtype=np.float32)

        # Scale-invariant normalization factor (distance between outer eye corners)
        p33 = coords[33, :2]
        p263 = coords[263, :2]
        eye_dist = float(np.linalg.norm(p263 - p33))
        if eye_dist < 1e-4:
            return np.array([0.90, 0.05, 0.05], dtype=np.float32)

        # Mouth geometry
        p61 = coords[61, :2]
        p291 = coords[291, :2]
        p13 = coords[13, :2]
        p14 = coords[14, :2]

        mouth_w = float(np.linalg.norm(p291 - p61)) / eye_dist
        lip_center_y = (p13[1] + p14[1]) / 2.0
        corners_y = (p61[1] + p291[1]) / 2.0

        # In image coordinates, y points DOWNWARDS.
        # When smiling, corners move UPWARDS -> corners_y decreases -> (lip_center_y - corners_y) > 0
        corner_elevation = (lip_center_y - corners_y) / eye_dist

        # Eyebrows geometry
        p107 = coords[107, :2]
        p336 = coords[336, :2]
        brow_inner_dist = float(np.linalg.norm(p336 - p107)) / eye_dist

        # 1. Happy score (AU12: Lip Corner Elevation + Mouth Width expansion)
        s_happy = 0.0
        if corner_elevation > 0.012:
            s_happy += (corner_elevation - 0.012) * 40.0
        if mouth_w > 0.49:
            s_happy += (mouth_w - 0.49) * 12.0

        # 2. Sad score (AU15: Lip Corner Depressor + Medial Brow Contraction)
        s_sad = 0.0
        if corner_elevation < -0.010:
            s_sad += (-0.010 - corner_elevation) * 45.0
        if brow_inner_dist < 0.27:
            s_sad += (0.27 - brow_inner_dist) * 10.0

        # Logit computation with strong baseline prior for resting Neutral state
        l_happy = s_happy * 3.6 - 1.2
        # Slightly favor measured sadness cues; a resting face has s_sad=0.
        l_sad = s_sad * self.sad_sensitivity * 3.6 - 1.2
        l_neutral = 2.0 - 1.6 * (s_happy + s_sad)

        logits = np.array([l_neutral, l_happy, l_sad], dtype=np.float32)
        exp_l = np.exp(logits - np.max(logits))
        facs_probs = exp_l / np.sum(exp_l)
        return facs_probs

    def process_frame(self, frame: np.ndarray) -> Dict[str, Any]:
        """
        Extract face landmarks, evaluate 3-state emotion probabilities, apply temporal
        smoothing, and return the filtered dominant emotion.
        """
        now = time.time()
        dominant_en = "Unknown"
        dominant_vi = "Không phát hiện mặt"
        confidence = 0.0
        probs_dict = {cls_name: 0.0 for cls_name in self.classes}

        face_landmarks = None
        if self._face_mesh is not None:
            try:
                rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                results = self._face_mesh.process(rgb)
                face_landmarks = results.multi_face_landmarks[0] if results.multi_face_landmarks else None
            except Exception as e:
                logger.debug("FaceMesh processing error: %s", e)
        elif self._face_landmarker is not None:
            try:
                rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                mp_img = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
                results = self._face_landmarker.detect(mp_img)
                if results.face_landmarks and len(results.face_landmarks) > 0:
                    face_landmarks = results.face_landmarks[0]
            except Exception as e:
                logger.debug("FaceLandmarker processing error: %s", e)

        if face_landmarks is not None:
            try:
                self._last_face_time = now

                # 1. Compute Biomechanical FACS probabilities [Neutral, Happy, Sad]
                facs_probs = self._compute_facs_probabilities(face_landmarks)

                # 2. Blend with Neural Network logits if available
                if self._model is not None:
                    try:
                        import torch
                        from features.emotion.model.utils.preprocess import normalize_face_landmarks

                        features = normalize_face_landmarks(face_landmarks)
                        if features is not None:
                            input_tensor = torch.tensor([features], dtype=torch.float32)
                            with torch.no_grad():
                                raw_logits = self._model(input_tensor).numpy()[0]
                            # Map 5-class model [Neutral(0), Happy(1), Sad(2), Angry(3), Surprised(4)] to 3 classes
                            subset_logits = raw_logits[:3]
                            exp_sub = np.exp(subset_logits - np.max(subset_logits))
                            nn_probs = exp_sub / np.sum(exp_sub)

                            # Weighted ensemble: 75% Biomechanical FACS + 25% Neural Net
                            combined_probs = 0.75 * facs_probs + 0.25 * nn_probs
                        else:
                            combined_probs = facs_probs
                    except Exception:
                        combined_probs = facs_probs
                else:
                    combined_probs = facs_probs

                # 3. Temporal smoothing. A 10-frame window is only ~0.3 s at 30 fps, so the label jumped
                #    Vui vẻ / Bình thường / Buồn bã several times a second. Time-based EMA (tau 1.5 s) plus
                #    hysteresis: the label changes only when another state leads by EMOTION_SWITCH_MARGIN
                #    for EMOTION_SWITCH_SECONDS.
                self._prob_history.append(combined_probs)
                smoothed_probs = self._smooth(combined_probs, now)

                pred_idx = self._stable_label(smoothed_probs, now)
                dominant_en = self.classes[pred_idx]
                dominant_vi = EMOTION_VI_MAP.get(dominant_en, "Bình thường")
                confidence = float(smoothed_probs[pred_idx])

                probs_dict = {
                    cls_name: round(float(p), 3)
                    for cls_name, p in zip(self.classes, smoothed_probs)
                }

                self._last_emotion = dominant_vi
                self._last_emotion_en = dominant_en
                self._last_confidence = confidence
            except Exception as e:
                logger.debug("Emotion evaluation error: %s", e)
                dominant_vi = "Không phát hiện mặt"
                dominant_en = "Unknown"
                confidence = 0.0
        else:
            # Check if face was lost for longer than TTL (1.2s)
            if self._last_face_time > 0 and (now - self._last_face_time) <= self.face_ttl and self._last_confidence > 0.0:
                # Brief flicker/blink within TTL: retain last emotion with gentle decay
                dominant_vi = self._last_emotion
                dominant_en = getattr(self, "_last_emotion_en", "Neutral")
                confidence = round(self._last_confidence * 0.9, 3)
                probs_dict = {cls_name: (0.8 if cls_name == dominant_en else 0.1) for cls_name in self.classes}
            else:
                # TTL expired: clear history and reset to Unknown / Không phát hiện mặt
                self._prob_history.clear()
                self._ema, self._stable_idx, self._challenger = None, None, None
                dominant_en = "Unknown"
                dominant_vi = "Không phát hiện mặt"
                confidence = 0.0
                self._last_emotion = "Không phát hiện mặt"
                self._last_emotion_en = "Unknown"
                self._last_confidence = 0.0
                probs_dict = {cls_name: 0.0 for cls_name in self.classes}

        result = {
            "dominant_emotion": dominant_en,
            "emotion_vi": dominant_vi,
            "face_detected": face_landmarks is not None,
            "confidence": round(confidence, 3),
            "probabilities": probs_dict,
            "timestamp": datetime.now().isoformat(),
        }

        # Update local results and shared state
        self._persist_result(result)
        return result

    def _smooth(self, probs: np.ndarray, now: float) -> np.ndarray:
        if self._ema is None or now - self._ema_ts > self.face_ttl:
            self._ema = np.asarray(probs, dtype=np.float32).copy()
        else:
            alpha = 1.0 - float(np.exp(-max(0.0, now - self._ema_ts) / EMOTION_TAU_SECONDS))
            self._ema = (1.0 - alpha) * self._ema + alpha * np.asarray(probs, dtype=np.float32)
        self._ema_ts = now
        return self._ema / max(1e-6, float(np.sum(self._ema)))

    def _stable_label(self, probs: np.ndarray, now: float) -> int:
        best = int(np.argmax(probs))
        if best != 0 and float(probs[best]) < EMOTION_MIN_CONFIDENCE:
            best = 0  # weak evidence (field 09/11: "Buồn bã 32%" on a smiling face): say Bình thường
        cur = self._stable_idx
        if cur is None:
            self._stable_idx, self._challenger, self._challenger_since = best, None, 0.0
            return best
        weak_current = cur != 0 and float(probs[cur]) < EMOTION_MIN_CONFIDENCE
        if best == cur or (probs[best] - probs[cur] < EMOTION_SWITCH_MARGIN and not weak_current):
            self._challenger = None
            return cur
        if self._challenger != best:
            self._challenger, self._challenger_since = best, now
        elif now - self._challenger_since >= EMOTION_SWITCH_SECONDS:
            self._stable_idx, self._challenger = best, None
            return best
        return cur

    def draw_emotion_badge(self, frame: np.ndarray, emotion_vi: str, confidence: float) -> np.ndarray:
        """Render a compact, high-contrast emotion pill badge at top right of video frame."""
        h, w = frame.shape[:2]
        if emotion_vi in ("Không phát hiện mặt", "Chưa phát hiện", "Unknown") or confidence <= 0.0:
            return frame  # Keep frame clean; outer UI card displays scanning status

        badge_text = emotion_vi  # no emoji: the overlay font has no emoji glyphs (they showed as boxes)

        if emotion_vi == "Vui vẻ":
            border_color = (0, 220, 100)  # Green accent
            text_color = (100, 255, 150)
        elif emotion_vi == "Buồn bã":
            border_color = (180, 100, 255)  # Purple/Pink accent
            text_color = (220, 160, 255)
        else:  # Bình thường
            border_color = (0, 196, 214)  # Cyan accent
            text_color = (0, 229, 250)

        draw_pill_badge(
            frame,
            badge_text,
            top_right=(w - 12, 12),
            bg_color=(18, 24, 36),
            border_color=border_color,
            text_color=text_color,
            font_size=12,
            padding_x=8,
            padding_y=4,
        )
        return frame

    def _persist_result(self, result: Dict[str, Any]) -> None:
        """Write emotion result to local JSON."""
        try:
            with open(self.result_file, "w", encoding="utf-8") as f:
                json.dump(result, f, ensure_ascii=False, indent=2)

            if self.state_file.exists():
                with open(self.state_file, "r", encoding="utf-8") as f:
                    state = json.load(f)
                state["current_emotion"] = result["emotion_vi"]
                state["emotion_confidence"] = result["confidence"]
                state["last_update"] = datetime.now().isoformat()
                with open(self.state_file, "w", encoding="utf-8") as f:
                    json.dump(state, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.debug("Failed writing emotion result: %s", e)

    def record_emotion(self, emotion: str, confidence: float = 0.95) -> Dict[str, Any]:
        """Manually record or override emotion state."""
        dominant_en = emotion.capitalize()
        if dominant_en not in self.classes:
            dominant_en = "Neutral"
        dominant_vi = EMOTION_VI_MAP.get(dominant_en, emotion)
        result = {
            "dominant_emotion": dominant_en,
            "emotion_vi": dominant_vi,
            "confidence": round(float(confidence), 3),
            "probabilities": {c: (0.90 if c == dominant_en else 0.05) for c in self.classes},
            "timestamp": datetime.now().isoformat(),
        }
        self._last_emotion = dominant_vi
        self._last_confidence = confidence
        self._persist_result(result)
        return result

    def get_result(self) -> Dict[str, Any]:
        """Read latest emotion result from local JSON."""
        if self.result_file.exists():
            try:
                with open(self.result_file, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
        return {
            "dominant_emotion": "Neutral",
            "emotion_vi": "Bình thường",
            "confidence": 0.92,
            "probabilities": {"Neutral": 0.92, "Happy": 0.04, "Sad": 0.04},
            "timestamp": datetime.now().isoformat(),
        }

    # Backward compatibility stubs
    def start(self, use_yolo11: bool = False) -> bool:
        return True

    def stop(self) -> None:
        pass

    def is_running(self) -> bool:
        return True
