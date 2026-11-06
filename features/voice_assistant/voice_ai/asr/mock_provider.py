"""
Deterministic Mock ASR Provider for Offline Testing and CI/CD Automation.
"""

from __future__ import annotations

import time
from typing import Any, Dict, Optional

from features.voice_assistant.voice_ai.asr.provider import ASRProvider
from features.voice_assistant.voice_ai.domain.contracts import TranscriptResult


class MockASRProvider(ASRProvider):
    """Mock ASR provider for headless tests and verification without GPU or PortAudio."""

    _instance: Optional[MockASRProvider] = None

    def __init__(self, default_text: str = "Tôi đã uống thuốc rồi") -> None:
        self.default_text = default_text
        self._preset_transcript: Optional[str] = None

    @classmethod
    def get_instance(cls) -> MockASRProvider:
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def set_preset_transcript(self, text: Optional[str]) -> None:
        """Inject specific transcript for automated test scenarios."""
        self._preset_transcript = text

    def is_available(self) -> bool:
        return True

    def get_model_info(self) -> Dict[str, Any]:
        return {"engine": "mock-asr", "is_loaded": True}

    def transcribe(self, audio_bytes: bytes, language: str = "vi") -> TranscriptResult:
        transcript = self._preset_transcript if self._preset_transcript is not None else self.default_text
        return TranscriptResult(
            transcript=transcript,
            language=language,
            duration_seconds=0.05,
            confidence=0.98,
            is_final=True,
            metadata={"mock": True, "audio_size_bytes": len(audio_bytes)},
        )

