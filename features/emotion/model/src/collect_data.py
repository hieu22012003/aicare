from __future__ import annotations

import argparse
import csv
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import cv2
import mediapipe as mp

from src.config import EXPRESSION_CLASSES, FEATURE_COLUMNS, INFERENCE, PATHS
from utils.preprocess import extract_face_features


HEADER = ["session_id", "sample_id", "timestamp_utc", "subject_id", "label"] + FEATURE_COLUMNS


def draw_text(frame, text: str, origin: tuple[int, int], color: tuple[int, int, int] = (255, 255, 255)) -> None:
    cv2.putText(frame, text, origin, cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 0), 4, cv2.LINE_AA)
    cv2.putText(frame, text, origin, cv2.FONT_HERSHEY_SIMPLEX, 0.7, color, 2, cv2.LINE_AA)


def append_sample(
    output_path: Path,
    session_id: str,
    sample_id: int,
    subject_id: str,
    label: str,
    features: dict[str, float],
) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    needs_header = not output_path.exists() or output_path.stat().st_size == 0
    with output_path.open("a", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=HEADER)
        if needs_header:
            writer.writeheader()
        row = {
            "session_id": session_id,
            "sample_id": sample_id,
            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
            "subject_id": subject_id,
            "label": label,
            **features,
        }
        writer.writerow(row)


def collect_data(
    output_path: Path,
    subject_id: str,
    session_id: str,
    sample_interval_seconds: float,
) -> None:
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

    active_label_index = 0
    recording = False
    sample_id = 0
    samples_this_session = 0
    last_sample_time = 0.0
    label_counts = {label: 0 for label in EXPRESSION_CLASSES}
    status = "Select class with 1-5. Press R to record. Press Q to quit."

    while cap.isOpened():
        ok, frame = cap.read()
        if not ok:
            break

        frame = cv2.flip(frame, 1)
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        results = face_mesh.process(rgb)
        face_landmarks = results.multi_face_landmarks[0] if results.multi_face_landmarks else None
        features = extract_face_features(face_landmarks)
        face_detected = features is not None

        key = cv2.waitKey(1) & 0xFF
        if key == ord("q"):
            break
        if key == ord("r"):
            recording = not recording
            status = "Recording started." if recording else "Recording paused."
        if ord("1") <= key <= ord(str(len(EXPRESSION_CLASSES))):
            active_label_index = key - ord("1")
            recording = False
            status = f"Target set to {EXPRESSION_CLASSES[active_label_index]}. Press R to record."

        active_label = EXPRESSION_CLASSES[active_label_index]
        now = time.monotonic()
        can_sample = recording and face_detected and (now - last_sample_time) >= sample_interval_seconds
        if can_sample:
            sample_id += 1
            samples_this_session += 1
            label_counts[active_label] += 1
            append_sample(output_path, session_id, sample_id, subject_id, active_label, features)
            last_sample_time = now
            status = f"Saved {active_label} sample {label_counts[active_label]}."
        elif recording and not face_detected:
            status = "Recording paused automatically: no face detected."

        draw_text(frame, f"Target: {active_label}", (20, 35), (130, 220, 255))
        draw_text(frame, f"Session: {session_id}", (20, 70), (220, 220, 220))
        draw_text(frame, f"Samples this session: {samples_this_session}", (20, 105), (220, 220, 220))
        draw_text(frame, f"Face: {'detected' if face_detected else 'not detected'}", (20, 140), (80, 220, 120) if face_detected else (80, 80, 255))
        draw_text(frame, f"Recording: {'ON' if recording else 'OFF'}", (20, 175), (80, 220, 120) if recording else (220, 220, 220))
        draw_text(frame, status, (20, frame.shape[0] - 55), (255, 255, 255))
        draw_text(frame, "1 Neutral  2 Happy  3 Sad  4 Angry  5 Surprised  R Record/Pause  Q Quit", (20, frame.shape[0] - 20), (220, 220, 220))

        cv2.imshow("Facial Expression Data Collection", frame)

    cap.release()
    face_mesh.close()
    cv2.destroyAllWindows()
    print(f"Session {session_id} complete. Samples saved: {samples_this_session}")
    print(f"Output: {output_path}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Collect face-landmark expression samples with session metadata.")
    parser.add_argument("--output", type=Path, default=PATHS.data_path)
    parser.add_argument("--subject-id", default="subject_001")
    parser.add_argument("--session-id", default=None)
    parser.add_argument(
        "--sample-interval-seconds",
        type=float,
        default=0.25,
        help="Minimum time between saved burst samples. Increase this to reduce frame correlation.",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    session = args.session_id or f"session_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"
    collect_data(args.output, args.subject_id, session, args.sample_interval_seconds)
