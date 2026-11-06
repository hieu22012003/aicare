"""
STT Data Contracts, Enums, and Schemas.
Implements Section 5 of KE_HOACH_TICH_HOP_API_STT.md.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, Optional
import uuid


class SttSessionState(str, Enum):
    """Lifecycle states of the voice interaction session."""
    IDLE = "IDLE"
    RECORDING = "RECORDING"
    TRANSCRIBING = "TRANSCRIBING"
    REVIEW_CONFIRMING = "REVIEW_CONFIRMING"
    EXECUTING = "EXECUTING"
    SUCCESS = "SUCCESS"
    ERROR = "ERROR"
    CANCELLED = "CANCELLED"


class SttErrorCode(str, Enum):
    """Standardized error codes for Client and Gateway."""
    # Client errors
    NO_AUDIO = "NO_AUDIO"
    INVALID_AUDIO = "INVALID_AUDIO"
    MICROPHONE_UNAVAILABLE = "MICROPHONE_UNAVAILABLE"
    SESSION_CANCELLED = "SESSION_CANCELLED"
    PATIENT_MISMATCH = "PATIENT_MISMATCH"

    # Gateway errors
    AUTH_REQUIRED = "AUTH_REQUIRED"
    FORBIDDEN = "FORBIDDEN"
    TIMEOUT = "TIMEOUT"
    RATE_LIMITED = "RATE_LIMITED"
    PROVIDER_UNAVAILABLE = "PROVIDER_UNAVAILABLE"
    INVALID_REQUEST = "INVALID_REQUEST"
    VERSION_CONFLICT = "VERSION_CONFLICT"
    UNKNOWN_ERROR = "UNKNOWN_ERROR"


@dataclass
class SttRequest:
    """
    Standardized request payload sent to the backend STT Gateway.
    Audio is sent as base64 or raw bytes, with contextual metadata.
    """
    request_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    voice_session_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    patient_id: str = ""
    language: str = "vi"
    context_type: Optional[str] = None          # e.g., 'medication_reminder', 'rehabilitation'
    context_id: Optional[str] = None            # e.g., dose occurrence id or plan id
    context_version: Optional[int] = None       # expected version for optimistic concurrency
    duration_ms: int = 0
    audio_base64: Optional[str] = None


@dataclass
class SttResponse:
    """
    Standardized response payload returned by the STT Gateway.
    No fabricated confidence values when provider does not supply them.
    """
    request_id: str
    voice_session_id: str
    status: str                                 # 'completed' or 'failed'
    transcript: str = ""
    language: str = "vi"
    latency_ms: int = 0
    error_code: Optional[str] = None
    confidence: Optional[float] = None          # Real provider confidence or None


@dataclass
class BusinessCommand:
    """
    Structured command dispatched to authoritative business services.
    Enforces audit, versioning, context, and idempotency.
    """
    command_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    voice_session_id: str = ""
    source: str = "voice"
    patient_id: str = ""
    intent: str = ""
    context_id: Optional[str] = None
    expected_version: Optional[int] = None
    confirmation_method: str = "explicit_button"  # 'explicit_button', 'voice_dialog'
    payload: Dict[str, Any] = field(default_factory=dict)

