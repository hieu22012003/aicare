"""MediaPipe pose estimator used by the /frames endpoint and the edge runner.

Two modes, picked automatically:
* ``tasks``     – Pose Landmarker Tasks API in VIDEO mode, when a ``.task`` model file is given.
* ``solutions`` – legacy ``mp.solutions.pose`` in tracking mode (``static_image_mode=False``).
  Its model ships inside the pip wheel, so it works without downloading anything.

Both modes track between frames instead of re-detecting every frame, which is what keeps
CPU usage low. ``process`` is blocking: call it through ``asyncio.to_thread``.
"""

from __future__ import annotations

import threading
from pathlib import Path

import numpy as np

from ai_core.shared.pose.landmarks import NUM_LANDMARKS, Landmark


class PoseUnavailableError(RuntimeError):
    """Raised when mediapipe/opencv are not installed (install the ``vision`` extra)."""


class PoseEstimator:
    def __init__(
        self,
        model_path: str | None = None,
        min_detection_confidence: float = 0.5,
        min_tracking_confidence: float = 0.5,
    ) -> None:
        try:
            import cv2  # noqa: F401
            import mediapipe as mp
        except ImportError as exc:  # pragma: no cover - depends on install
            raise PoseUnavailableError("Install the 'vision' extra: pip install -e '.[vision]'") from exc

        self._mp = mp
        self._lock = threading.Lock()
        self._last_ts_ms = -1
        if model_path and Path(model_path).is_file():
            from mediapipe.tasks import python as mp_python
            from mediapipe.tasks.python import vision

            options = vision.PoseLandmarkerOptions(
                base_options=mp_python.BaseOptions(model_asset_path=str(model_path)),
                running_mode=vision.RunningMode.VIDEO,
                num_poses=1,
                min_pose_detection_confidence=min_detection_confidence,
                min_tracking_confidence=min_tracking_confidence,
            )
            self._landmarker = vision.PoseLandmarker.create_from_options(options)
            self._pose = None
            self.mode = "tasks"
        else:
            self._landmarker = None
            self._pose = mp.solutions.pose.Pose(
                static_image_mode=False,
                model_complexity=1,
                min_detection_confidence=min_detection_confidence,
                min_tracking_confidence=min_tracking_confidence,
            )
            self.mode = "solutions"

    def process(self, bgr: np.ndarray, ts_ms: int) -> tuple[Landmark, ...] | None:
        """Return 33 landmarks for the most prominent person, or None if nobody is found."""
        import cv2

        rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
        with self._lock:
            # VIDEO mode requires strictly increasing timestamps.
            ts_ms = max(int(ts_ms), self._last_ts_ms + 1)
            self._last_ts_ms = ts_ms
            if self._landmarker is not None:
                image = self._mp.Image(image_format=self._mp.ImageFormat.SRGB, data=rgb)
                result = self._landmarker.detect_for_video(image, ts_ms)
                if not result.pose_landmarks:
                    return None
                points = result.pose_landmarks[0]
            else:
                result = self._pose.process(rgb)
                if not result.pose_landmarks:
                    return None
                points = result.pose_landmarks.landmark
        if len(points) < NUM_LANDMARKS:
            return None
        return tuple(
            Landmark(float(p.x), float(p.y), float(p.z), float(getattr(p, "visibility", 1.0) or 0.0))
            for p in points[:NUM_LANDMARKS]
        )

    def close(self) -> None:
        with self._lock:
            if self._landmarker is not None:
                self._landmarker.close()
            if self._pose is not None:
                self._pose.close()


def decode_image(data: bytes) -> np.ndarray | None:
    """Decode JPEG/PNG bytes to a BGR array."""
    import cv2

    array = np.frombuffer(data, dtype=np.uint8)
    image = cv2.imdecode(array, cv2.IMREAD_COLOR)
    return image
