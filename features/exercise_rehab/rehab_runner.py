"""
Standalone Rehabilitation Runner for AI Rehabilitation Assistant.
Supports the 3 prioritized research exercises:
1. Arm Raise (Nâng tay qua đầu)
2. Sit-to-Stand (Đứng lên ngồi xuống)
3. Marching (Đi bộ tại chỗ / Nâng cao đùi)
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import cv2
from features.exercise_rehab.rehabilitation_adapter import RehabilitationAdapter


def main() -> None:
    parser = argparse.ArgumentParser(description="Rehabilitation Exercise Standalone Runner")
    parser.add_argument(
        "--exercise",
        default="arm_raise",
        choices=["arm_raise", "sit_to_stand", "marching"],
        help="Exercise ID to execute",
    )
    parser.add_argument("--source", default="0", help="Camera index or video file")
    args = parser.parse_args()

    adapter = RehabilitationAdapter()
    adapter.start_exercise(args.exercise)
    print(f"Started exercise session: {args.exercise}")

    cap = cv2.VideoCapture(int(args.source) if args.source.isdigit() else args.source)
    if not cap.isOpened():
        print("Could not open camera, generating 10 simulated frames...")
        for _ in range(10):
            dummy = np.zeros((480, 640, 3), dtype=np.uint8)
            adapter.process_frame(dummy)
        print("Summary:", adapter.stop_exercise())
        return

    try:
        while cap.isOpened():
            ret, frame = cap.read()
            if not ret:
                break
            annotated, metrics = adapter.process_frame(frame)
            cv2.imshow("Rehabilitation Assistant", annotated)
            if cv2.waitKey(1) & 0xFF == ord("q"):
                break
    finally:
        cap.release()
        cv2.destroyAllWindows()
        print("Final Results:", adapter.stop_exercise())


if __name__ == "__main__":
    import numpy as np
    main()
