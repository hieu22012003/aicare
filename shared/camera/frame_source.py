"""
Unified Frame Source Architecture for AI Rehabilitation & Safety Assistant.
Abstracts live physical/simulated webcam input and video file streams into a common contract.
Supports exact presentation timestamps (PTS), play, pause, seek, restart, and EOF handling.
Complies with FT-0 in KE_HOACH_CHINH_UI_VA_THU_FALL_VIDEO.md.
"""

from __future__ import annotations

import hashlib
import logging
import os
import threading
import time
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Optional, Tuple

import cv2
import numpy as np

logger = logging.getLogger("FrameSource")


class FrameSource(ABC):
    """
    Abstract Base Class for video and camera frame providers.
    Both camera and video file inputs share identical detection, tracking,
    fall state machine, rendering, and event handling contracts.
    """

    @abstractmethod
    def read_frame(self) -> Tuple[bool, Optional[np.ndarray], float, int, bool]:
        """
        Read the next frame.
        Returns:
            (success, frame_bgr, pts_seconds, frame_index, is_eof)
        """

    @abstractmethod
    def play(self) -> None:
        """Resume playback or capture."""

    @abstractmethod
    def pause(self) -> None:
        """Pause playback or capture."""

    @abstractmethod
    def seek(self, target_sec: float) -> bool:
        """Seek to target presentation time in seconds."""

    @abstractmethod
    def restart(self) -> None:
        """Restart playback from the beginning."""

    @abstractmethod
    def release(self) -> None:
        """Release underlying hardware or file handles cleanly."""

    @property
    @abstractmethod
    def source_id(self) -> str:
        """Unique identifier for this frame source session."""

    @property
    @abstractmethod
    def source_type(self) -> str:
        """'camera' or 'video_file'."""

    @property
    @abstractmethod
    def fps(self) -> float:
        """Frame rate (source FPS)."""

    @property
    @abstractmethod
    def total_frames(self) -> int:
        """Total frame count (0 if infinite/live camera)."""

    @property
    @abstractmethod
    def duration_seconds(self) -> float:
        """Total duration in seconds (0.0 if infinite/live camera)."""

    @property
    @abstractmethod
    def is_paused(self) -> bool:
        """True if playback is currently paused."""

    @property
    @abstractmethod
    def is_eof(self) -> bool:
        """True if end of stream reached."""

    @property
    @abstractmethod
    def is_test_mode(self) -> bool:
        """True if in test/dry-run mode (MUST be True for video files)."""


class CameraFrameSource(FrameSource):
    """
    Live Camera Frame Source wrapping the system's CameraManager singleton.
    Provides monotonic presentation timestamps from live capture.
    """

    def __init__(self, camera_index: int = 0) -> None:
        from shared.camera.camera_manager import CameraManager

        self.cam_mgr = CameraManager.get_instance(camera_index=camera_index)
        self._start_time: float = time.monotonic()
        self._frame_count: int = 0
        self._paused: bool = False
        self._lock = threading.Lock()
        self._source_id = f"cam_{camera_index}"
        self._last_capture_pts = None

    def read_frame(self) -> Tuple[bool, Optional[np.ndarray], float, int, bool]:
        with self._lock:
            if self._paused:
                return False, None, self._last_capture_pts or 0.0, self._frame_count, False

            frame, pts, valid = self.cam_mgr.get_capture_packet()
            if not valid or frame is None or frame.size == 0 or pts is None or (self._last_capture_pts is not None and pts <= self._last_capture_pts):
                return False, None, self._last_capture_pts or 0.0, self._frame_count, False

            self._frame_count += 1
            self._last_capture_pts = pts
            return True, frame.copy(), pts, self._frame_count, False

    def play(self) -> None:
        with self._lock:
            self._paused = False
            self.cam_mgr.resume()

    def pause(self) -> None:
        with self._lock:
            self._paused = True
            self.cam_mgr.pause()

    def seek(self, target_sec: float) -> bool:
        # Cannot seek live camera stream
        return False

    def restart(self) -> None:
        with self._lock:
            self._start_time = time.monotonic()
            self._frame_count = 0
            self._last_capture_pts = None

    def release(self) -> None:
        # We don't necessarily kill the singleton unless instructed,
        # but pause it gracefully.
        self.pause()

    @property
    def source_id(self) -> str:
        return self._source_id

    @property
    def source_type(self) -> str:
        return "camera"

    @property
    def fps(self) -> float:
        return 30.0

    @property
    def total_frames(self) -> int:
        return 0

    @property
    def duration_seconds(self) -> float:
        return 0.0

    @property
    def is_paused(self) -> bool:
        return self._paused

    @property
    def is_eof(self) -> bool:
        return False

    @property
    def is_test_mode(self) -> bool:
        return False


class VideoFileFrameSource(FrameSource):
    """
    Video File Frame Source for test and verification runs.
    Reads MP4 files strictly according to video presentation timestamps (PTS),
    NOT system wall clock. Guarantees deterministic dry-run isolation.
    """

    def __init__(self, file_path: str) -> None:
        self.file_path = str(file_path)
        if not os.path.exists(self.file_path):
            raise FileNotFoundError(f"Video file not found: {self.file_path}")

        self._cap = cv2.VideoCapture(self.file_path)
        if not self._cap.isOpened():
            raise IOError(f"Cannot open video file: {self.file_path}")

        self._fps = float(self._cap.get(cv2.CAP_PROP_FPS))
        if self._fps <= 0.0 or np.isnan(self._fps):
            self._fps = 24.0

        self._total_frames = int(self._cap.get(cv2.CAP_PROP_FRAME_COUNT))
        self._width = int(self._cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        self._height = int(self._cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        self._duration = (self._total_frames / self._fps) if self._fps > 0 else 0.0

        self._current_frame_idx = 0
        self._last_pts = None
        self.generation = 0
        self.decoded_frames = 0
        self._is_paused = False
        self._is_eof = False
        self._lock = threading.Lock()

        # Compute SHA256 for provenance tracking
        self._sha256 = self._calculate_sha256(self.file_path)
        self._source_id = f"video_{self._sha256[:12]}"
        logger.info(
            "Initialized VideoFileFrameSource: %s (%dx%d, %.2f FPS, %d frames, %.2fs, sha=%s)",
            os.path.basename(self.file_path),
            self._width,
            self._height,
            self._fps,
            self._total_frames,
            self._duration,
            self._sha256[:8],
        )

    @staticmethod
    def _calculate_sha256(path: str) -> str:
        h = hashlib.sha256()
        with open(path, "rb") as fp:
            while chunk := fp.read(65536):
                h.update(chunk)
        return h.hexdigest()

    def read_frame(self) -> Tuple[bool, Optional[np.ndarray], float, int, bool]:
        with self._lock:
            if self._is_eof or self._cap is None or not self._cap.isOpened():
                pts = self._last_pts if self._last_pts is not None else 0.0
                return False, None, pts, self._current_frame_idx, True

            if self._is_paused:
                return False, None, self.current_pts, self._current_frame_idx, False

            ret, frame = self._cap.read()
            if not ret or frame is None:
                self._is_eof = True
                pts = self._last_pts if self._last_pts is not None else 0.0
                logger.info("Reached EOF for %s at frame %d", self.source_id, self._current_frame_idx)
                return False, None, pts, self._current_frame_idx, True

            pts = float(self._cap.get(cv2.CAP_PROP_POS_MSEC)) / 1000.0
            if not np.isfinite(pts) or pts < 0 or (self._last_pts is not None and pts <= self._last_pts):
                raise ValueError("Decoder PTS missing/non-monotonic; evaluation cannot use frame-index fallback")
            self._last_pts = pts
            self.decoded_frames += 1
            current_idx = self._current_frame_idx
            self._current_frame_idx += 1
            return True, frame, pts, current_idx, False

    def play(self) -> None:
        with self._lock:
            self._is_paused = False

    def pause(self) -> None:
        with self._lock:
            self._is_paused = True

    def seek(self, target_sec: float) -> bool:
        """Seek to specific presentation time in seconds."""
        with self._lock:
            if self._cap is None or not self._cap.isOpened():
                return False
            target_sec = max(0.0, min(target_sec, self._duration))
            target_frame = int(target_sec * self._fps)
            if not self._cap.set(cv2.CAP_PROP_POS_MSEC, target_sec * 1000):
                return False
            self._current_frame_idx = int(self._cap.get(cv2.CAP_PROP_POS_FRAMES))
            self._last_pts = None
            self.generation += 1
            self._is_eof = False
            logger.info("Seeked %s to %.2fs (frame %d)", self.source_id, target_sec, target_frame)
            return True

    def restart(self) -> None:
        """Restart playback from frame 0 and reset EOF status."""
        with self._lock:
            if self._cap is None or not self._cap.isOpened():
                self._cap = cv2.VideoCapture(self.file_path)
            self._cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
            self._current_frame_idx = 0
            self._last_pts = None
            self.decoded_frames = 0
            self.generation += 1
            self._is_eof = False
            self._is_paused = False
            logger.info("Restarted %s from beginning (frame 0)", self.source_id)

    def release(self) -> None:
        with self._lock:
            if self._cap is not None:
                if self._cap.isOpened():
                    self._cap.release()
                self._cap = None
                logger.info("Released VideoCapture handle for %s", self.source_id)
            self._is_eof = True

    @property
    def cap(self) -> Optional[cv2.VideoCapture]:
        return self._cap

    @property
    def frame_idx(self) -> int:
        return self._current_frame_idx

    @property
    def source_id(self) -> str:
        return self._source_id

    @property
    def source_type(self) -> str:
        return "video_file"

    @property
    def fps(self) -> float:
        return self._fps

    @property
    def total_frames(self) -> int:
        return self._total_frames

    @property
    def duration_seconds(self) -> float:
        return self._duration

    @property
    def is_paused(self) -> bool:
        return self._is_paused

    @property
    def is_eof(self) -> bool:
        return self._is_eof

    @property
    def is_test_mode(self) -> bool:
        # Video files are ALWAYS in test mode (immutable dry-run)
        return True

    @property
    def sha256(self) -> str:
        return self._sha256

    @property
    def current_pts(self) -> float:
        return self._last_pts if self._last_pts is not None else 0.0

