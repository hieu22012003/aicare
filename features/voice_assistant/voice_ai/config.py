"""
Voice AI Configuration, Feature Flags, and Operational Controls.
Implements Phase 7 (Rollback Switch & Safety Defaults) of VOICE_AI_IMPLEMENTATION_PLAN.md.
"""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class VoiceAIConfig:
    """Central configuration for Voice AI module."""

    # Master feature flag: Set to False for instant zero-downtime rollback to manual GUI
    enabled: bool = os.environ.get("VOICE_AI_ENABLED", "true").lower() in ("true", "1", "yes")

    # Offline / Local fallback: When True, bypasses external LLM API entirely
    force_mock_semantic: bool = os.environ.get("VOICE_AI_FORCE_MOCK_SEMANTIC", "false").lower() in ("true", "1", "yes")

    # Audio Privacy: Default False ensures raw audio WAV is never written to disk
    store_raw_audio: bool = os.environ.get("VOICE_AI_RECORD_AUDIO_CONSENT", "false").lower() in ("true", "1", "yes")

    # Gemini Flash Settings
    gemini_model: str = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")
    gemini_timeout_sec: float = float(os.environ.get("GEMINI_TIMEOUT_SEC", "8.0"))
    gemini_max_retries: int = int(os.environ.get("GEMINI_MAX_RETRIES", "2"))

    # Audio bounding (max recording duration enforced by AudioRecorder)
    max_audio_duration_sec: float = float(os.environ.get("VOICE_AI_MAX_DURATION_SEC", "20.0"))

    # SLA and Realtime
    assistance_sla_seconds: float = float(os.environ.get("VOICE_AI_ASSISTANCE_SLA_SEC", "60.0"))
    idempotency_ttl_seconds: int = int(os.environ.get("VOICE_AI_IDEMPOTENCY_TTL_SEC", "600"))


# Global configuration instance
voice_config = VoiceAIConfig()
VOICE_AI_ENABLED: bool = voice_config.enabled


def is_voice_ai_enabled() -> bool:
    """Dynamic check for instant rollback via environment variable."""
    return os.environ.get("VOICE_AI_ENABLED", "true").lower() in ("true", "1", "yes")

