"""
Audio Capture & Push-to-Talk (PTT) Controller for Voice AI.
Captures 16 kHz mono 16-bit PCM audio, applies bounds (max 20s),
and formats WAV bytes strictly in memory without raw disk storage.
Implements Section 1, 2, and 7 of VOICE_AI_IMPLEMENTATION_PLAN.md.
"""

from __future__ import annotations

import io
import logging
import os
import re
import shutil
import struct
import subprocess
import threading
import time
import wave
from dataclasses import dataclass
from typing import Callable, Optional

logger = logging.getLogger("AudioCapture")


@dataclass
class AudioCaptureConfig:
    """Configuration parameters for speech capture."""
    sample_rate: int = 16000          # 16 kHz standard for Whisper
    channels: int = 1                 # Mono
    sample_width: int = 2             # 16-bit PCM (2 bytes per sample)
    max_duration_seconds: float = 20.0 # Strict safety upper bound
    min_duration_seconds: float = 0.3  # Discard ultra-short accidental clicks
    store_raw_audio: bool = False      # Default False: Privacy & retention non-negotiable


class _AlsaStreamReader:
    """Streams 16kHz mono raw PCM from an ALSA capture device via arecord."""

    def __init__(self, device: str, sample_rate: int, chunk_callback: Callable[[bytes], None]) -> None:
        self.device = device
        self.sample_rate = sample_rate
        self.chunk_callback = chunk_callback
        self.proc: Optional[subprocess.Popen] = None
        self.thread: Optional[threading.Thread] = None
        self._running = False

    def start(self) -> None:
        self._running = True
        self.proc = subprocess.Popen(
            ["arecord", "-D", self.device, "-r", str(self.sample_rate), "-c", "1", "-f", "S16_LE", "-t", "raw"],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            bufsize=1024,
        )
        self.thread = threading.Thread(target=self._read_loop, daemon=True)
        self.thread.start()

    def _read_loop(self) -> None:
        while self._running and self.proc and self.proc.poll() is None:
            try:
                chunk = self.proc.stdout.read(1024)
                if chunk:
                    self.chunk_callback(chunk)
                else:
                    break
            except Exception:
                break

    def stop(self) -> None:
        self._running = False
        if self.proc:
            try:
                self.proc.terminate()
                self.proc.wait(timeout=0.3)
            except Exception:
                pass
            self.proc = None
        if self.thread:
            self.thread.join(timeout=0.3)
            self.thread = None


class AudioRecorder:
    """
    Thread-safe in-memory audio capture engine.
    Supports hardware capture via sounddevice/PortAudio or ALSA arecord when available,
    with automatic graceful fallback to software simulation in headless environments.
    """

    def __init__(self, config: Optional[AudioCaptureConfig] = None) -> None:
        self.config = config or AudioCaptureConfig()
        self._is_recording = False
        self._lock = threading.Lock()
        self._pcm_buffer = bytearray()
        self._start_time: float = 0.0
        self._stop_timer: Optional[threading.Timer] = None
        self._hardware_available = False
        self._hardware_backend = "none"  # "sounddevice", "alsa", or "none"
        self._alsa_device: Optional[str] = None
        self._on_max_duration_callback: Optional[Callable[[], None]] = None

        self._stream = None
        self._alsa_reader: Optional[_AlsaStreamReader] = None
        self._last_stop_time: float = 0.0

        # Check hardware availability across supported backends
        self._init_hardware_backend()

    def _detect_alsa_device(self) -> Optional[str]:
        """Discovers a functional ALSA recording device on Linux."""
        if not shutil.which("arecord"):
            return None

        candidates = ["plughw:0,6", "plughw:0,7", "plughw:0,0", "default"]
        try:
            res = subprocess.run(
                ["arecord", "-l"],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                timeout=2,
            )
            if res.returncode == 0:
                matches = re.findall(r"card\s+(\d+):.*device\s+(\d+):", res.stdout)
                for card, dev in matches:
                    cand = f"plughw:{card},{dev}"
                    if cand not in candidates:
                        candidates.append(cand)
        except Exception:
            pass

        for dev in candidates:
            try:
                p = subprocess.Popen(
                    ["arecord", "-D", dev, "-r", str(self.config.sample_rate), "-c", "1", "-f", "S16_LE", "-t", "raw"],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                )
                time.sleep(0.12)
                p.terminate()
                stdout, _ = p.communicate()
                if len(stdout) > 0:
                    return dev
            except Exception:
                continue
        return None

    def _init_hardware_backend(self) -> None:
        """Detect whether physical audio input is accessible via sounddevice or ALSA."""
        # 1. Try sounddevice (primary on Windows and systems with PortAudio)
        try:
            import sounddevice as sd  # type: ignore
            devices = sd.query_devices()
            input_devices = [d for d in devices if d.get("max_input_channels", 0) > 0]
            if input_devices:
                self._hardware_available = True
                self._hardware_backend = "sounddevice"
                logger.info("Physical audio input detected via sounddevice (%d devices).", len(input_devices))
                return
        except Exception as e:
            logger.debug("Sounddevice unavailable: %s (checking ALSA fallback)", e)

        # 2. Try ALSA arecord (native Linux fallback when libportaudio2 is not installed)
        alsa_dev = self._detect_alsa_device()
        if alsa_dev:
            self._hardware_available = True
            self._hardware_backend = "alsa"
            self._alsa_device = alsa_dev
            logger.info("Physical audio input detected via ALSA backend (device: %s).", alsa_dev)
            return

        self._hardware_available = False
        self._hardware_backend = "none"
        logger.info("No physical audio input devices detected (using software simulation fallback).")

    def _audio_stream_callback(self, indata, frames, time_info, status) -> None:
        """Callback from sounddevice stream appending real microphone audio frames."""
        if status:
            logger.debug("Audio input stream status: %s", status)
        self.feed_pcm_chunk(bytes(indata))

    @property
    def is_hardware_available(self) -> bool:
        return self._hardware_available

    @property
    def is_recording(self) -> bool:
        with self._lock:
            return self._is_recording

    def set_max_duration_callback(self, callback: Callable[[], None]) -> None:
        """Callback triggered if elder holds button beyond max_duration_seconds."""
        self._on_max_duration_callback = callback

    def start_recording(self) -> bool:
        """Begin audio capture buffer."""
        # 1. Debounce protection against rapid key-bounce / chatter
        now = time.time()
        if (now - self._last_stop_time) < 0.150:
            logger.debug("Debounce: rejecting rapid start_recording within 150ms of previous stop.")
            return False

        # 2. Barge-in: cut off any assistant TTS playback so speaker doesn't leak into mic
        try:
            from features.voice_assistant.voice_ai.audio.tts_player import get_tts_coordinator
            get_tts_coordinator().stop()
        except Exception:
            pass

        with self._lock:
            if self._is_recording:
                logger.warning("Recording already in progress.")
                return False

            self._is_recording = True
            self._pcm_buffer.clear()
            self._start_time = now

            # Start hardware capture stream according to detected backend
            if self._hardware_available:
                if self._hardware_backend == "sounddevice":
                    try:
                        import sounddevice as sd  # type: ignore
                        self._stream = sd.RawInputStream(
                            samplerate=self.config.sample_rate,
                            channels=self.config.channels,
                            dtype="int16",
                            callback=self._audio_stream_callback,
                        )
                        self._stream.start()
                        logger.debug("Hardware audio stream started via sounddevice.")
                    except Exception as e:
                        logger.warning("Failed to start sounddevice stream: %s. Using software buffer.", e)
                        self._stream = None
                elif self._hardware_backend == "alsa" and self._alsa_device:
                    try:
                        self._alsa_reader = _AlsaStreamReader(
                            device=self._alsa_device,
                            sample_rate=self.config.sample_rate,
                            chunk_callback=self.feed_pcm_chunk,
                        )
                        self._alsa_reader.start()
                        logger.debug("Hardware audio stream started via ALSA (%s).", self._alsa_device)
                    except Exception as e:
                        logger.warning("Failed to start ALSA stream: %s. Using software buffer.", e)
                        self._alsa_reader = None

            # Schedule safety cut-off timer at max_duration_seconds
            self._stop_timer = threading.Timer(
                self.config.max_duration_seconds,
                self._handle_max_duration_reached,
            )
            self._stop_timer.daemon = True
            self._stop_timer.start()

            logger.info("Audio recording started (max: %.1fs, backend: %s).", self.config.max_duration_seconds, self._hardware_backend)
            return True


    def feed_pcm_chunk(self, chunk: bytes) -> None:
        """Append raw PCM chunk (used by audio stream callback or simulation)."""
        with self._lock:
            if not self._is_recording:
                return

            # Check if duration limit reached
            elapsed = time.time() - self._start_time
            if elapsed >= self.config.max_duration_seconds:
                return

            self._pcm_buffer.extend(chunk)

    def _handle_max_duration_reached(self) -> None:
        """Safety handler when recording exceeds 20s."""
        logger.info("Maximum recording duration (%.1fs) reached. Auto-stopping.", self.config.max_duration_seconds)
        if self._on_max_duration_callback:
            try:
                self._on_max_duration_callback()
            except Exception as e:
                logger.error("Error in max duration callback: %s", e)

    def stop_recording(self) -> Optional[bytes]:
        """
        Stop recording, cancel safety timer, and return valid WAV bytes.
        Returns None if duration was below min_duration_seconds and no chunks fed.
        """
        with self._lock:
            if not self._is_recording:
                return None

            self._is_recording = False
            self._last_stop_time = time.time()
            if self._stop_timer:
                self._stop_timer.cancel()
                self._stop_timer = None

            if self._stream is not None:
                try:
                    self._stream.stop()
                    self._stream.close()
                except Exception as e:
                    logger.warning("Error closing sounddevice stream: %s", e)
                finally:
                    self._stream = None

            if self._alsa_reader is not None:
                try:
                    self._alsa_reader.stop()
                except Exception as e:
                    logger.warning("Error closing ALSA stream: %s", e)
                finally:
                    self._alsa_reader = None

            now = time.time()
            duration = now - self._start_time
            self._last_stop_time = now

            if duration < self.config.min_duration_seconds and len(self._pcm_buffer) == 0:
                logger.info("Recording too short (%.2fs < %.2fs), discarded.", duration, self.config.min_duration_seconds)
                self._pcm_buffer.clear()
                return None

            pcm_data = bytes(self._pcm_buffer)
            self._pcm_buffer.clear()

        # Collect audio diagnostics and apply gentle AGC if real audio was captured
        if len(pcm_data) > 0:
            import numpy as np
            samples = np.frombuffer(pcm_data, dtype=np.int16).astype(np.float32)
            peak = float(np.max(np.abs(samples)))
            rms = float(np.sqrt(np.mean(samples**2)))
            near_zero = float(np.mean(np.abs(samples) < 50) * 100)

            logger.info(
                "[AudioDiagnostics] duration=%.2fs peak=%.0f rms=%.1f near_zero=%.1f%% backend=%s",
                duration, peak, rms, near_zero, self._hardware_backend,
            )

            if peak < 50 and rms < 15:
                logger.warning("[AudioDiagnostics] Captured audio is silent or microphone muted.")
            elif 100 < peak < 16000:
                gain = min(16000.0 / peak, 6.0)
                samples = np.clip(samples * gain, -32768, 32767).astype(np.int16)
                pcm_data = samples.tobytes()
                logger.debug("[AudioDiagnostics] Gentle AGC boost x%.2f (peak: %.0f -> %.0f)", gain, peak, peak * gain)

        # Convert raw PCM to 16kHz mono WAV format in memory
        wav_bytes = self._pcm_to_wav(pcm_data, duration=duration)
        logger.info("Audio recording stopped. Generated %d bytes WAV (duration: %.2fs).", len(wav_bytes), duration)

        # Non-negotiable rule: Do NOT store raw audio on disk unless explicitly configured
        if self.config.store_raw_audio and os.getenv("VOICE_STORE_RAW_AUDIO", "false").lower() == "true":
            self._debug_save_raw_audio(wav_bytes)

        return wav_bytes

    def _pcm_to_wav(self, pcm_data: bytes, duration: float = 0.0) -> bytes:
        """Encodes raw PCM bytes into standard 16kHz mono 16-bit WAV header."""
        # If buffer was empty (e.g. simulated or headless), generate silence proportional to duration
        if len(pcm_data) == 0:
            target_duration = max(duration, self.config.min_duration_seconds)
            sample_count = int(self.config.sample_rate * target_duration)
            pcm_data = bytes(sample_count * self.config.sample_width)

        wav_io = io.BytesIO()
        with wave.open(wav_io, "wb") as wf:
            wf.setnchannels(self.config.channels)
            wf.setsampwidth(self.config.sample_width)
            wf.setframerate(self.config.sample_rate)
            wf.writeframes(pcm_data)

        return wav_io.getvalue()

    def _debug_save_raw_audio(self, wav_bytes: bytes) -> None:
        """Stores raw audio only when explicitly opted-in by user consent."""
        try:
            from shared.config import DATA_DIR
            out_dir = DATA_DIR / "voice_recordings"
            out_dir.mkdir(parents=True, exist_ok=True)
            out_path = out_dir / f"recording_{int(time.time())}.wav"
            out_path.write_bytes(wav_bytes)
            logger.info("Debug audio saved to %s (consent active).", out_path)
        except Exception as e:
            logger.error("Failed to save debug audio: %s", e)

