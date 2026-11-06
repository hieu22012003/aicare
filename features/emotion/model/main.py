from __future__ import annotations

from collections import deque
from pathlib import Path
import time

import cv2
import mediapipe as mp
import numpy as np
import torch

from src.config import EXPRESSION_CLASSES, FEATURE_COLUMNS, INFERENCE, INPUT_SIZE, PATHS, TRAINING
from src.model import ExpressionNet
from utils.preprocess import normalize_face_landmarks


def draw_text(frame, text: str, origin: tuple[int, int], color: tuple[int, int, int]) -> None:
    cv2.putText(frame, text, origin, cv2.FONT_HERSHEY_SIMPLEX, 0.85, (0, 0, 0), 4, cv2.LINE_AA)
    cv2.putText(frame, text, origin, cv2.FONT_HERSHEY_SIMPLEX, 0.85, color, 2, cv2.LINE_AA)


def load_model(model_path: Path) -> ExpressionNet | None:
    if not model_path.exists():
        print(f"No trained model found at {model_path}. Train one with: python -m src.train")
        return None

    checkpoint = torch.load(model_path, map_location="cpu")
    if checkpoint.get("classes") != EXPRESSION_CLASSES:
        raise ValueError("Checkpoint classes do not match src/config.py.")
    if checkpoint.get("feature_columns") != FEATURE_COLUMNS:
        raise ValueError("Checkpoint feature columns do not match src/config.py.")

    model = ExpressionNet(
        input_size=checkpoint.get("input_size", INPUT_SIZE),
        num_classes=len(EXPRESSION_CLASSES),
        dropout=TRAINING.dropout,
    )
    model.load_state_dict(checkpoint["model_state_dict"])
    model.eval()
    return model


def predict_expression(model: ExpressionNet, features: list[float], device: torch.device) -> np.ndarray:
    input_tensor = torch.tensor([features], dtype=torch.float32, device=device)
    with torch.no_grad():
        logits = model(input_tensor)
        probabilities = torch.softmax(logits, dim=1).cpu().numpy()[0]
    return probabilities


def run_demo() -> None:
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    model = load_model(PATHS.model_path)
    if model is not None:
        model.to(device)

    mp_face_mesh = mp.solutions.face_mesh
    face_mesh = mp_face_mesh.FaceMesh(
        refine_landmarks=True,
        max_num_faces=1,
        min_detection_confidence=0.6,
        min_tracking_confidence=0.6,
    )

    cap = cv2.VideoCapture(INFERENCE.camera_index)
    if not cap.isOpened():
        raise RuntimeError("Could not open webcam.")
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, INFERENCE.frame_width)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, INFERENCE.frame_height)

    probability_history: deque[np.ndarray] = deque(maxlen=INFERENCE.smoothing_window)
    previous_time = time.perf_counter()

    while cap.isOpened():
        ok, frame = cap.read()
        if not ok:
            break

        frame = cv2.flip(frame, 1)
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        results = face_mesh.process(rgb)
        face_landmarks = results.multi_face_landmarks[0] if results.multi_face_landmarks else None
        features = normalize_face_landmarks(face_landmarks)

        current_time = time.perf_counter()
        fps = 1.0 / max(current_time - previous_time, 1e-6)
        previous_time = current_time

        if model is None:
            probability_history.clear()
            draw_text(frame, "No trained model found", (24, 42), (80, 80, 255))
            draw_text(frame, "Collect data, train, then rerun the demo", (24, 82), (230, 230, 230))
        elif features is None:
            probability_history.clear()
            draw_text(frame, "No face detected", (24, 42), (80, 80, 255))
        else:
            probabilities = predict_expression(model, features, device)
            probability_history.append(probabilities)
            smoothed = np.mean(np.stack(probability_history), axis=0)
            predicted_index = int(np.argmax(smoothed))
            confidence = float(smoothed[predicted_index])
            expression = EXPRESSION_CLASSES[predicted_index]
            if confidence < INFERENCE.confidence_threshold:
                expression = "Uncertain"

            draw_text(frame, f"Expression: {expression}", (24, 42), (130, 220, 255))
            draw_text(frame, f"Confidence: {confidence * 100:.1f}%", (24, 82), (230, 230, 230))

        draw_text(frame, f"FPS: {fps:.1f}", (24, frame.shape[0] - 24), (200, 200, 200))
        cv2.imshow("Real-Time Facial Expression Recognition Using Facial Landmarks", frame)
        if cv2.waitKey(1) & 0xFF == ord("q"):
            break

    cap.release()
    face_mesh.close()
    cv2.destroyAllWindows()


if __name__ == "__main__":
    run_demo()
