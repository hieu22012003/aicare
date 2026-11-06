"""
Structured Observability and Distributed Tracing for Voice AI Pipeline.
Implements Phase 1 of Voice AI Reliability Requirements.

Features:
- Unique interaction IDs (vint_*) and session IDs (vses_*).
- Per-stage latency measurement: Audio Capture, ASR, Safety Triage, Semantic, Workflow, TTS.
- Strict PII masking: Masked patient IDs, truncated preview transcripts, zero API key exposure.
- Structured JSON and machine-parseable log outputs.
"""

from __future__ import annotations

from enum import Enum
import json
import logging
import os
import re
import time
from typing import Any, Dict, List, Optional
import uuid

logger = logging.getLogger("voice_ai.trace")


class TraceStage(str, Enum):
    AUDIO_CAPTURE = "audio_capture"
    ASR = "asr"
    SAFETY_TRIAGE = "safety_triage"
    SEMANTIC_ANALYSIS = "semantic_analysis"
    INTENT_VALIDATION = "intent_validation"
    WORKFLOW_EXECUTION = "workflow_execution"
    TTS_PLAYBACK = "tts_playback"


def mask_patient_id(pid: Optional[str]) -> str:
    """Masks patient identifier for privacy in logs."""
    if not pid:
        return "anon"
    s = str(pid).strip()
    if len(s) <= 4:
        return "***"
    return f"{s[:2]}***{s[-2:]}"


def mask_transcript_preview(text: Optional[str], max_len: int = 20) -> str:
    """Provides a safe truncated snippet without logging full sensitive speech."""
    if not text:
        return "<empty>"
    clean = re.sub(r"\s+", " ", str(text).strip())
    # Mask numbers that might be phone numbers or dates
    clean = re.sub(r"\b\d{4,}\b", "****", clean)
    if len(clean) > max_len:
        return f"{clean[:max_len]}... (len={len(text)})"
    return clean


def mask_secret(text: str) -> str:
    """Strips API keys and sensitive tokens."""
    if not text:
        return ""
    masked = re.sub(r"AIza[0-9A-Za-z_\-]{20,50}", "***REDACTED_KEY***", str(text))
    masked = re.sub(r"(key|api_key|token)=([^&\s]+)", r"\1=***REDACTED***", masked, flags=re.IGNORECASE)
    return masked


class VoiceTraceContext:
    """
    Tracks lifecycle metrics, latencies, and state transitions for a single voice interaction.
    """

    def __init__(
        self,
        interaction_id: Optional[str] = None,
        session_id: Optional[str] = None,
        patient_id: Optional[str] = None,
    ) -> None:
        self.interaction_id = interaction_id or f"vint_{uuid.uuid4().hex[:12]}"
        self.session_id = session_id or f"vses_{uuid.uuid4().hex[:12]}"
        self.patient_id = patient_id or "unknown"
        self.start_epoch_sec = time.time()
        self.start_perf = time.perf_counter()

        self._stage_starts: Dict[str, float] = {}
        self.stage_latencies_ms: Dict[str, float] = {}
        self.stage_statuses: Dict[str, str] = {}
        self.intents: List[str] = []
        self.overall_status: str = "IN_PROGRESS"
        self.error_code: Optional[str] = None
        self.metadata: Dict[str, Any] = {}

    def start_stage(self, stage: str | TraceStage) -> None:
        stage_key = stage.value if isinstance(stage, TraceStage) else str(stage)
        self._stage_starts[stage_key] = time.perf_counter()

    def end_stage(
        self,
        stage: str | TraceStage,
        status: str = "OK",
        error_code: Optional[str] = None,
    ) -> float:
        stage_key = stage.value if isinstance(stage, TraceStage) else str(stage)
        start = self._stage_starts.get(stage_key, time.perf_counter())
        elapsed_ms = round((time.perf_counter() - start) * 1000, 2)
        self.stage_latencies_ms[stage_key] = elapsed_ms
        self.stage_statuses[stage_key] = status
        if error_code:
            self.error_code = error_code
        return elapsed_ms

    def record_intent(self, intent_type: str) -> None:
        if intent_type not in self.intents:
            self.intents.append(intent_type)

    def finish(self, status: str = "COMPLETED", error_code: Optional[str] = None) -> Dict[str, Any]:
        self.overall_status = status
        if error_code:
            self.error_code = error_code
        total_ms = round((time.perf_counter() - self.start_perf) * 1000, 2)
        self.stage_latencies_ms["total_e2e_ms"] = total_ms

        trace_summary = {
            "interaction_id": self.interaction_id,
            "session_id": self.session_id,
            "patient_id": mask_patient_id(self.patient_id),
            "status": self.overall_status,
            "error_code": self.error_code,
            "intents": self.intents,
            "latencies_ms": self.stage_latencies_ms,
            "stages": self.stage_statuses,
        }

        # Structured log output
        logger.info("[VOICE_TRACE] %s", json.dumps(trace_summary, ensure_ascii=False))
        return trace_summary


def start_trace(
    interaction_id: Optional[str] = None,
    session_id: Optional[str] = None,
    patient_id: Optional[str] = None,
) -> VoiceTraceContext:
    """Convenience helper to initialize a trace context."""
    return VoiceTraceContext(
        interaction_id=interaction_id,
        session_id=session_id,
        patient_id=patient_id,
    )

