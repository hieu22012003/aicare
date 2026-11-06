"""
TTS Playback Coordinator and Barge-In Controller for Voice AI.
Implements Phase 2 of Voice AI Reliability Requirements.

Features:
- Global lifecycle tracking: Detects whether TTS audio is currently speaking.
- Barge-in interruptibility: Elderly speech or PTT press terminates TTS playback instantly.
- Process isolation: Uses safe subprocess execution to prevent Linux C/ctypes segmentation faults.
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
import threading
from typing import Optional

logger = logging.getLogger("voice_ai.audio.tts")


class TTSPlaybackCoordinator:
    """Coordinates speech synthesis playback, status detection, and barge-in cancellation."""

    _instance: Optional["TTSPlaybackCoordinator"] = None
    _lock = threading.Lock()

    def __init__(self) -> None:
        self._current_process: Optional[subprocess.Popen] = None
        self._proc_lock = threading.Lock()
        self._recording_suspended = False

    @classmethod
    def get_instance(cls) -> "TTSPlaybackCoordinator":
        with cls._lock:
            if cls._instance is None:
                cls._instance = cls()
            return cls._instance

    def is_speaking(self) -> bool:
        """Returns True if a TTS synthesis process is currently active."""
        with self._proc_lock:
            if self._current_process is None:
                return False
            poll_result = self._current_process.poll()
            if poll_result is None:
                return True
            self._current_process = None
            return False

    def stop(self) -> None:
        """Immediately interrupts and terminates active speech playback (barge-in)."""
        with self._proc_lock:
            if self._current_process is not None:
                if self._current_process.poll() is None:
                    try:
                        self._current_process.kill()
                        self._current_process.wait(timeout=1)
                        logger.info("TTS playback interrupted (barge-in).")
                    except Exception as e:
                        logger.debug("Failed to terminate TTS subprocess: %s", e)
                self._current_process = None

    def suspend_for_recording(self, suspended: bool) -> None:
        with self._proc_lock:
            self._recording_suspended = suspended
            if suspended and self._current_process is not None:
                process, self._current_process = self._current_process, None
                if process.poll() is None:
                    process.kill()
                process.wait(timeout=1)

    def speak(self, text: str) -> None:
        """Speaks text aloud asynchronously with automatic barge-in cancellation of prior speech."""
        clean_text = (text or "").strip()
        if not clean_text:
            return

        if os.getenv("AI_CARE_DISABLE_TTS") == "1":
            logger.info("[Assistant speaking (simulated)]: %s", clean_text)
            return

        # Stop any existing speech before starting new speech
        self.stop()

        cmd = [
            sys.executable,
            "-c",
            "import sys, pyttsx3\n"
            "try:\n"
            "    engine = pyttsx3.init()\n"
            "    for v in engine.getProperty('voices'):\n"
            "        if 'vi' in getattr(v, 'languages', []) or 'vi' in v.id.lower():\n"
            "            engine.setProperty('voice', v.id)\n"
            "            break\n"
            "    engine.setProperty('rate', 135)\n"
            "    engine.setProperty('volume', 1.0)\n"
            "    engine.say(sys.argv[1])\n"
            "    engine.runAndWait()\n"
            "except Exception:\n"
            "    pass\n",
            clean_text,
        ]

        if getattr(sys, 'frozen', False):
            cmd = [sys.executable, '--local-tts', clean_text]

        with self._proc_lock:
            if self._recording_suspended:
                return
            try:
                self._current_process = subprocess.Popen(
                    cmd,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                logger.debug("Started TTS playback for: %s", clean_text[:40])
            except Exception as e:
                logger.error("Failed to start TTS playback: %s", e)
                self._current_process = None


def get_tts_coordinator() -> TTSPlaybackCoordinator:
    return TTSPlaybackCoordinator.get_instance()

