"""
Conversation and Intent Lifecycle State Machine for Voice AI Sessions.
Handles multi-turn clarification, disambiguation, and confirmation state.
Implements Section 4 and Section 5 of VOICE_AI_IMPLEMENTATION_PLAN.md.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional

from features.voice_assistant.voice_ai.domain.contracts import ExecutionStatus, VoiceIntent


class SessionState(str, Enum):
    """Macro-state of the Voice Interaction Session."""
    IDLE = "IDLE"
    LISTENING = "LISTENING"
    PROCESSING = "PROCESSING"
    AWAITING_CLARIFICATION = "AWAITING_CLARIFICATION"
    AWAITING_CONFIRMATION = "AWAITING_CONFIRMATION"
    DISPATCHING = "DISPATCHING"
    COMPLETED = "COMPLETED"
    ERROR = "ERROR"


@dataclass
class ConversationContext:
    """Session context tracking state across multi-turn interactions."""
    session_id: str
    patient_id: str
    state: SessionState = SessionState.IDLE
    active_workflow: Optional[str] = None  # e.g., 'medication_confirm', 'doctor_assistance'
    pending_intent: Optional[VoiceIntent] = None
    clarification_question: Optional[str] = None
    candidate_options: List[Dict[str, Any]] = field(default_factory=list)
    turn_count: int = 0
    created_at_utc: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updated_at_utc: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    interaction_history: List[Dict[str, Any]] = field(default_factory=list)

    def touch(self) -> None:
        self.updated_at_utc = datetime.now(timezone.utc).isoformat()


class VoiceSessionStateMachine:
    """Manages lifecycle transitions for voice sessions."""

    def __init__(self, patient_id: str, session_id: Optional[str] = None) -> None:
        sid = session_id or f"vses_{uuid.uuid4().hex[:12]}"
        self.context = ConversationContext(session_id=sid, patient_id=patient_id)

    @property
    def patient_id(self) -> str:
        return self.context.patient_id

    @property
    def session_id(self) -> str:
        return self.context.session_id

    @property
    def state(self) -> SessionState:
        return self.context.state

    @property
    def active_workflow(self) -> Optional[str]:
        return self.context.active_workflow

    @property
    def pending_clarification(self) -> bool:
        return self.context.state == SessionState.AWAITING_CLARIFICATION or self.context.pending_intent is not None

    @property
    def pending_intent(self) -> Optional[VoiceIntent]:
        return self.context.pending_intent

    @property
    def clarification_prompt(self) -> Optional[str]:
        return self.context.clarification_question

    @property
    def history(self) -> List[Dict[str, Any]]:
        return self.context.interaction_history

    def confirm_action(self) -> None:
        """Confirm pending action."""
        self.context.state = SessionState.COMPLETED
        self.context.pending_intent = None
        self.context.clarification_question = None
        self.context.candidate_options = []
        self.context.touch()

    def cancel_action(self) -> None:
        """Cancel unexecuted action."""
        self.context.state = SessionState.IDLE
        self.context.pending_intent = None
        self.context.clarification_question = None
        self.context.candidate_options = []
        self.context.turn_count = 0
        self.context.touch()

    @property
    def current_state(self) -> SessionState:
        return self.context.state

    def start_listening(self) -> None:
        """User pressed Push-to-Talk button."""
        self.context.state = SessionState.LISTENING
        self.context.touch()

    def start_processing(self) -> None:
        """Audio captured; ASR and semantic analysis begun."""
        if self.context.state != SessionState.AWAITING_CLARIFICATION:
            self.context.state = SessionState.PROCESSING
        self.context.turn_count += 1
        self.context.touch()

    def set_awaiting_clarification(
        self,
        intent: VoiceIntent,
        question: str,
        options: Optional[List[Dict[str, Any]]] = None,
    ) -> None:
        """System needs elder to disambiguate parameters (e.g. which medication)."""
        self.context.state = SessionState.AWAITING_CLARIFICATION
        self.context.pending_intent = intent
        self.context.clarification_question = question
        self.context.candidate_options = options or []
        self.context.turn_count = 0
        self.context.touch()

    def set_awaiting_confirmation(
        self,
        intent: VoiceIntent,
        prompt: str,
    ) -> None:
        """System needs explicit confirmation before committing critical action."""
        self.context.state = SessionState.AWAITING_CONFIRMATION
        self.context.pending_intent = intent
        self.context.clarification_question = prompt
        self.context.turn_count = 0
        self.context.touch()


    def dispatch_completed(self, outcome_summary: str = "") -> None:
        """All intents successfully dispatched."""
        self.context.state = SessionState.COMPLETED
        self.context.interaction_history.append({
            "turn": self.context.turn_count,
            "status": "COMPLETED",
            "summary": outcome_summary,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })
        self.context.pending_intent = None
        self.context.clarification_question = None
        self.context.candidate_options = []
        self.context.touch()

    def set_error(self, error_message: str) -> None:
        """Error occurred during interaction."""
        self.context.state = SessionState.ERROR
        self.context.interaction_history.append({
            "turn": self.context.turn_count,
            "status": "ERROR",
            "error": error_message,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })
        self.context.touch()

    def reset(self) -> None:
        """Reset session back to IDLE state."""
        self.context.state = SessionState.IDLE
        self.context.active_workflow = None
        self.context.pending_intent = None
        self.context.clarification_question = None
        self.context.candidate_options = []
        self.context.touch()

