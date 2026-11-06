"""
Test Suite for Phase 2: Audio Capture, VAD, ASR Provider Abstraction, and Voice UX Widget.
Validates requirements from Section 1, 2, 8, and 9 of VOICE_AI_IMPLEMENTATION_PLAN.md.
"""

from __future__ import annotations

import io
import math
import struct
import unittest
import wave
from unittest.mock import MagicMock, patch

from PyQt5.QtWidgets import QApplication

from apps.desktop.voice_button_widget import VoiceButtonWidget
from features.voice_assistant.voice_ai.asr.factory import get_asr_provider
from features.voice_assistant.voice_ai.asr.mock_provider import MockASRProvider
from features.voice_assistant.voice_ai.asr.provider import ASRProvider
from features.voice_assistant.voice_ai.audio.capture import AudioCaptureConfig, AudioRecorder
from features.voice_assistant.voice_ai.audio.vad import is_speech_present
from features.voice_assistant.voice_ai.domain.contracts import TranscriptResult

# Ensure offscreen Qt application exists for widget tests
app = QApplication.instance()
if app is None:
    app = QApplication(["--platform", "offscreen"])


def generate_synthesized_wav(duration_seconds: float = 1.0, frequency_hz: float = 440.0, sample_rate: int = 16000) -> bytes:
    """Generates synthetic 16kHz mono 16-bit WAV audio for testing."""
    sample_count = int(duration_seconds * sample_rate)
    pcm_samples = []
    for i in range(sample_count):
        # Generate 440Hz sine wave sample
        val = int(16000.0 * math.sin(2.0 * math.pi * frequency_hz * (i / sample_rate)))
        pcm_samples.append(val)

    pcm_bytes = struct.pack(f"<{sample_count}h", *pcm_samples)

    wav_io = io.BytesIO()
    with wave.open(wav_io, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm_bytes)
    return wav_io.getvalue()


class TestAudioCaptureAndASR(unittest.TestCase):
    def test_audio_capture_wav_format_in_memory(self) -> None:
        """Verify AudioRecorder outputs standard 16kHz mono 16-bit WAV bytes."""
        config = AudioCaptureConfig(sample_rate=16000, channels=1, sample_width=2, min_duration_seconds=0.01)
        recorder = AudioRecorder(config)

        self.assertTrue(recorder.start_recording())
        self.assertTrue(recorder.is_recording)

        # Feed 1 second of simulated audio chunks
        chunk = bytes(3200)  # 1600 samples = 0.1s
        for _ in range(5):
            recorder.feed_pcm_chunk(chunk)

        wav_bytes = recorder.stop_recording()
        self.assertFalse(recorder.is_recording)
        self.assertIsNotNone(wav_bytes)

        # Inspect WAV header
        with wave.open(io.BytesIO(wav_bytes), "rb") as wf:
            self.assertEqual(wf.getnchannels(), 1, "Must be single channel (mono)")
            self.assertEqual(wf.getsampwidth(), 2, "Must be 16-bit PCM (2 bytes)")
            self.assertEqual(wf.getframerate(), 16000, "Must be 16kHz sample rate")

    def test_no_raw_audio_saved_to_disk_by_default(self) -> None:
        """Verify strict non-negotiable rule: Raw audio is NEVER saved to disk by default."""
        config = AudioCaptureConfig(store_raw_audio=False, min_duration_seconds=0.01)
        recorder = AudioRecorder(config)

        recorder.start_recording()
        recorder.feed_pcm_chunk(bytes(3200))
        wav_bytes = recorder.stop_recording()
        self.assertIsNotNone(wav_bytes)

        from shared.config import DATA_DIR
        recordings_dir = DATA_DIR / "voice_recordings"
        # Directory should either not exist or have 0 files created by this test
        if recordings_dir.exists():
            recent_files = list(recordings_dir.glob("*.wav"))
            self.assertEqual(len(recent_files), 0, "No raw audio should be saved to disk by default")

    def test_audio_capture_duration_bounds(self) -> None:
        """Verify recording cannot exceed max_duration_seconds."""
        config = AudioCaptureConfig(max_duration_seconds=0.1, min_duration_seconds=0.01)
        cutoff_called = False

        def on_cutoff() -> None:
            nonlocal cutoff_called
            cutoff_called = True

        recorder = AudioRecorder(config)
        recorder.set_max_duration_callback(on_cutoff)

        recorder.start_recording()
        import time
        time.sleep(0.15)  # Wait past max duration

        self.assertTrue(cutoff_called, "Max duration callback should fire when limit reached")
        wav_bytes = recorder.stop_recording()
        self.assertIsNotNone(wav_bytes)

    def test_vad_speech_presence_detection(self) -> None:
        """Verify VAD correctly distinguishes voice audio from flat silence."""
        # Flat silence
        silence_bytes = generate_synthesized_wav(duration_seconds=1.0, frequency_hz=0.0)
        is_speech, rms_silence = is_speech_present(silence_bytes)
        self.assertFalse(is_speech)
        self.assertEqual(rms_silence, 0.0)

        # Tone with energy (simulated speech energy)
        tone_bytes = generate_synthesized_wav(duration_seconds=1.0, frequency_hz=300.0)
        is_speech_tone, rms_tone = is_speech_present(tone_bytes, energy_threshold_rms=200.0)
        self.assertTrue(is_speech_tone)
        self.assertGreater(rms_tone, 200.0)

    def test_asr_provider_and_factory(self) -> None:
        """Verify ASR provider abstraction and factory fallback resolution."""
        # Mock mode
        provider = get_asr_provider("mock")
        self.assertIsInstance(provider, MockASRProvider)
        self.assertTrue(provider.is_available())

        # Test transcription
        test_audio = generate_synthesized_wav(duration_seconds=0.5)
        res = provider.transcribe(test_audio, language="vi")
        self.assertIsInstance(res, TranscriptResult)
        self.assertEqual(res.language, "vi")
        self.assertGreater(len(res.transcript), 0)

    def test_voice_button_widget_ui_interaction(self) -> None:
        """Verify PyQt5 VoiceButtonWidget transitions and emits signals."""
        mock_asr = MockASRProvider("Tôi đã uống thuốc rồi")
        widget = VoiceButtonWidget(asr_provider=mock_asr)

        self.assertEqual(widget.btn_ptt.text(), "🎙 GIỮ ĐỂ NÓI")
        self.assertIn("Nhấn giữ", widget.lbl_status.text())

        # Simulate press
        recording_emitted = False
        widget.recording_started.connect(lambda: nonlocal_rec())
        def nonlocal_rec() -> None:
            nonlocal recording_emitted
            recording_emitted = True

        widget._on_ptt_pressed()
        self.assertTrue(widget.recorder.is_recording)
        self.assertTrue(recording_emitted)
        self.assertIn("ĐANG NGHE", widget.btn_ptt.text())

        # Feed simulated chunk and release
        widget.recorder.feed_pcm_chunk(bytes(3200))

        transcript_captured = []
        widget.transcript_ready.connect(lambda res: transcript_captured.append(res))

        widget._on_ptt_released()

        # Wait for worker thread to complete and process Qt events
        if widget._active_worker:
            widget._active_worker.wait(3000)
            app.processEvents()

        self.assertFalse(widget.recorder.is_recording)
        self.assertEqual(len(transcript_captured), 1)
        self.assertEqual(transcript_captured[0].transcript, "Tôi đã uống thuốc rồi")
        self.assertIn("Tôi đã uống thuốc rồi", widget.lbl_status.text())


if __name__ == "__main__":
    unittest.main()
