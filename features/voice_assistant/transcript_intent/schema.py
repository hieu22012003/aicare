"""Strict JSON contract. No model-supplied policy, IDs, confidence or tools."""
from enum import Enum
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field
from features.voice_assistant.transcript_intent.registry import Intent

SCHEMA_VERSION = 'intent-v1'


class Decision(str, Enum):
    ROUTE = 'route'
    CLARIFY = 'clarify'
    UNSUPPORTED = 'unsupported'


class Slots(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    # Current snooze handler only supports schedule-defined default.
    snooze_minutes: None = None


class IntentProposal(BaseModel):
    model_config = ConfigDict(extra='forbid')
    intent: Intent
    slots: Slots
    decision: Decision
    evidence_text: str = Field(max_length=240)


class IntentRequest(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    session_id: str = Field(pattern=r'^[a-zA-Z0-9_-]{1,64}$')
    request_id: str = Field(pattern=r'^[a-zA-Z0-9_-]{1,64}$')
    patient_id: str = Field(min_length=1, max_length=64)
    context_version: str = Field(pattern=r'^[0-9a-f]{64}$')
    context_type: Literal['home_navigation', 'medication', 'rehabilitation']
    exercise: str = Field(max_length=48)
    running: bool
    paused: bool
    utterance: str = Field(min_length=1, max_length=240)


def proposal(intent=Intent.UNKNOWN, decision=Decision.CLARIFY, evidence=''):
    return IntentProposal(intent=intent, slots=Slots(), decision=decision, evidence_text=evidence)
