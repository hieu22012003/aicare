"""
Abstract Base Class for Automated Speech Recognition (ASR) Providers.
Defines unified interface for legacy backend ASR and test mocks; desktop STT uses local Zipformer.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict

from features.voice_assistant.voice_ai.domain.contracts import TranscriptResult


class ASRProvider(ABC):
    """Abstract interface for speech transcription services."""

    @abstractmethod
    def transcribe(self, audio_bytes: bytes, language: str = "vi") -> TranscriptResult:
        """
        Transcribes a 16kHz mono WAV or PCM byte stream into text.
        Returns TranscriptResult containing text, confidence, and metadata.
        """
        pass

    @abstractmethod
    def is_available(self) -> bool:
        """Returns True if the underlying ASR model/service is ready for inference."""
        pass

    @abstractmethod
    def get_model_info(self) -> Dict[str, Any]:
        """Returns metadata about the active ASR engine and model."""
        pass

