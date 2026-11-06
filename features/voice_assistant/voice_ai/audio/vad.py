"""
Voice Activity Detection (VAD) & Silence Filtering for Voice AI.
Filters out pure silence, clicks, or background room noise before ASR.
"""

from __future__ import annotations

import io
import math
import struct
import wave
from typing import Tuple


def is_speech_present(wav_bytes: bytes, energy_threshold_rms: float = 200.0) -> Tuple[bool, float]:
    """
    Analyzes a 16-bit PCM WAV byte stream to determine if human speech energy is present.
    Returns (is_speech, calculated_rms).
    """
    try:
        with wave.open(io.BytesIO(wav_bytes), "rb") as wf:
            channels = wf.getnchannels()
            sample_width = wf.getsampwidth()
            frame_count = wf.getnframes()
            if frame_count == 0 or sample_width != 2:
                return False, 0.0

            raw_frames = wf.readframes(frame_count)
    except Exception:
        return False, 0.0

    # Unpack 16-bit signed integers
    num_samples = len(raw_frames) // 2
    if num_samples == 0:
        return False, 0.0

    try:
        samples = struct.unpack(f"<{num_samples}h", raw_frames)
    except Exception:
        return False, 0.0

    # Compute Root Mean Square (RMS) energy
    sum_squares = sum(s * s for s in samples)
    mean_square = sum_squares / num_samples
    rms = math.sqrt(mean_square)

    is_speech = rms >= energy_threshold_rms
    return is_speech, rms

