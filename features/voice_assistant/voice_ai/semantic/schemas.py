"""
Strict JSON Schema and Validation Engine for Gemini Flash Structured Outputs.
Implements Section 3 of VOICE_AI_IMPLEMENTATION_PLAN.md.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Set, Tuple

from features.voice_assistant.voice_ai.domain.contracts import (
    INTENT_PRIORITY_MAP,
    IntentType,
    SemanticResult,
    VoiceIntent,
)

from pydantic import BaseModel, Field

# Allowed entities per intent type (Strict Whitelist)
ALLOWED_ENTITIES_PER_INTENT: Dict[IntentType, Set[str]] = {
    IntentType.DOCTOR_ASSISTANCE: {"reason", "urgency", "symptoms"},
    IntentType.HEALTH_REPORT: {
        "symptoms", "onset", "severity", "duration", "negated",
        "blood_pressure", "heart_rate", "temperature", "blood_glucose", "notes",
    },
    IntentType.MEDICATION_CONFIRM: {
        "medication_name", "medication_ref", "schedule_ref", "status", "time", "dosage",
    },
    IntentType.MEDICATION_NOT_TAKEN: {
        "medication_name", "medication_ref", "schedule_ref", "reason", "time",
    },
    IntentType.MEDICATION_GUIDANCE: {
        "medication_name", "medication_ref", "requested_info", "medication_color",
        "question_type", "timing", "dosage",
    },
    IntentType.MEDICATION_REMIND_LATER: {
        "medication_name", "schedule_ref", "delay_minutes", "time",
    },
    IntentType.REHABILITATION_START: {
        "exercise_type", "reps", "target_duration",
    },
    IntentType.GAME_LAUNCH: {"game_type", "difficulty"},
    IntentType.MUSIC_PLAY: {"playlist", "mood", "genre", "song_name"},
    IntentType.MUSIC_PAUSE: set(),
    IntentType.MUSIC_STOP: set(),
    IntentType.UNKNOWN: {"reason", "error_detail"},
    IntentType.CLARIFY: {"question", "ambiguous_entities", "reason"},
}

# Supported game types allowlist
ALLOWED_GAME_TYPES = {"card_matching", "sequence_recall"}

# Supported rehabilitation exercise types allowlist
ALLOWED_REHAB_EXERCISES = {"sit_to_stand", "arm_raise", "march", "general"}


class VoiceIntentSchema(BaseModel):
    """Pydantic schema for individual intent structured output."""
    type: str = Field(description="Intent type enum")
    entities: Dict[str, Any] = Field(default_factory=dict, description="Validated entity parameters")
    evidence_span: str = Field(default="", description="Exact excerpt from user utterance")
    requires_clarification: bool = Field(default=False, description="Whether clarification is required")
    clarification_prompt: Optional[str] = Field(default=None, description="Clarifying question if needed")
    reason: Optional[str] = Field(default=None, description="Classification rationale")
    risk_flags: List[str] = Field(default_factory=list, description="Abnormal or high risk flags")


class SemanticPayloadSchema(BaseModel):
    """Pydantic schema for complete semantic payload structured output."""
    schema_version: str = Field(default="1.0")
    language: str = Field(default="vi")
    requires_clarification: bool = Field(default=False)
    clarification_question: Optional[str] = Field(default=None)
    reason: Optional[str] = Field(default=None)
    risk_flags: List[str] = Field(default_factory=list)
    intents: List[VoiceIntentSchema] = Field(default_factory=list)


# Gemini Structured Output JSON Schema (compatible with google-genai response_schema)
GEMINI_SEMANTIC_JSON_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "schema_version": {"type": "string", "enum": ["1.0"]},
        "language": {"type": "string", "enum": ["vi"]},
        "requires_clarification": {"type": "boolean"},
        "clarification_question": {"type": "string", "nullable": True},
        "reason": {"type": "string"},
        "risk_flags": {
            "type": "array",
            "items": {"type": "string"},
        },
        "intents": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "type": {
                        "type": "string",
                        "enum": [it.value for it in IntentType],
                    },
                    "entities": {
                        "type": "object",
                        "description": "Validated entity parameters",
                    },
                    "evidence_span": {
                        "type": "string",
                        "description": "Exact excerpt from user utterance justifying this intent",
                    },
                    "requires_clarification": {
                        "type": "boolean",
                        "description": "True if parameters are ambiguous or missing mandatory referents",
                    },
                    "clarification_prompt": {
                        "type": "string",
                        "nullable": True,
                        "description": "Single concise clarifying question in polite Vietnamese if needed",
                    },
                    "reason": {
                        "type": "string",
                        "description": "Classification rationale for debug",
                    },
                    "risk_flags": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Abnormal or dangerous clinical signs",
                    },
                },
                "required": ["type", "entities", "evidence_span", "requires_clarification"],
            },
        },
    },
    "required": ["schema_version", "language", "intents"],
}


def validate_semantic_payload(
    payload: Dict[str, Any],
    original_transcript: str = "",
) -> Tuple[bool, List[str], Optional[SemanticResult]]:
    """
    Validates that a dictionary produced by LLM complies with the semantic schema,
    verifies entity whitelists, bounds lengths, and checks evidence spans.
    """
    errors: List[str] = []

    if not isinstance(payload, dict):
        return False, ["Payload must be a JSON object"], None

    if payload.get("schema_version") != "1.0":
        errors.append(f"Invalid schema_version: {payload.get('schema_version')}")

    if payload.get("language") != "vi":
        errors.append(f"Invalid language: {payload.get('language')}")

    raw_intents = payload.get("intents")
    if not isinstance(raw_intents, list):
        return False, ["Field 'intents' must be a list"], None

    if len(raw_intents) == 0:
        # Empty intents list defaults to UNKNOWN
        validated_intents = [
            VoiceIntent(
                type=IntentType.UNKNOWN,
                entities={},
                evidence_span=original_transcript[:100],
                requires_clarification=True,
                clarification_prompt="Dạ cụ vừa nói gì ạ? Con chưa nghe rõ.",
            )
        ]
        return True, [], SemanticResult(intents=validated_intents)

    validated_intents: List[VoiceIntent] = []
    normalized_transcript = original_transcript.strip().lower()

    for idx, item in enumerate(raw_intents):
        if not isinstance(item, dict):
            errors.append(f"Intent[{idx}] must be an object")
            continue

        raw_type = str(item.get("type", "")).strip().upper()
        try:
            intent_type = IntentType(raw_type)
        except ValueError:
            errors.append(f"Intent[{idx}] has unsupported type enum: {raw_type}")
            continue

        entities = item.get("entities", {})
        if not isinstance(entities, dict):
            errors.append(f"Intent[{idx}] entities must be a dict")
            entities = {}

        # Whitelist entity keys
        allowed_keys = ALLOWED_ENTITIES_PER_INTENT.get(intent_type, set())
        for k in list(entities.keys()):
            if k not in allowed_keys:
                errors.append(f"Intent[{idx}] ({intent_type}) contains unauthorized entity key: '{k}'")

        # Specific entity validations
        if intent_type == IntentType.GAME_LAUNCH and "game_type" in entities:
            g_type = str(entities["game_type"]).lower()
            if g_type not in ALLOWED_GAME_TYPES:
                # Fallback to general clarification or default game
                entities["game_type"] = "card_matching"

        evidence_span = str(item.get("evidence_span", "")).strip()
        if len(evidence_span) > 300:
            evidence_span = evidence_span[:300]

        # Verify evidence alignment with transcript if transcript is non-empty
        if normalized_transcript and evidence_span:
            norm_span = evidence_span.lower()
            if norm_span not in normalized_transcript:
                # Evidence hallucination detected: flag as warning, don't crash
                item["requires_clarification"] = True

        requires_clarification = bool(item.get("requires_clarification", False))
        clarification_prompt = item.get("clarification_prompt")
        if clarification_prompt and len(clarification_prompt) > 200:
            clarification_prompt = clarification_prompt[:200]

        item_reason = item.get("reason")
        item_risk_flags = item.get("risk_flags", []) if isinstance(item.get("risk_flags"), list) else []

        validated_intents.append(
            VoiceIntent(
                type=intent_type,
                entities=entities,
                evidence_span=evidence_span,
                requires_clarification=requires_clarification,
                clarification_prompt=clarification_prompt,
                priority=INTENT_PRIORITY_MAP[intent_type],
                reason=item_reason,
                risk_flags=item_risk_flags,
            )
        )

    if errors:
        return False, errors, None

    overall_requires_clarif = bool(payload.get("requires_clarification", any(i.requires_clarification for i in validated_intents)))
    overall_clarif_question = payload.get("clarification_question") or next((i.clarification_prompt for i in validated_intents if i.clarification_prompt), None)
    overall_reason = payload.get("reason") or next((i.reason for i in validated_intents if i.reason), None)
    overall_risk_flags = payload.get("risk_flags", []) if isinstance(payload.get("risk_flags"), list) else []
    for vi in validated_intents:
        for rf in vi.risk_flags:
            if rf not in overall_risk_flags:
                overall_risk_flags.append(rf)

    result = SemanticResult(
        schema_version="1.0",
        language="vi",
        intents=validated_intents,
        raw_model_output=json.dumps(payload, ensure_ascii=False),
        requires_clarification=overall_requires_clarif,
        clarification_question=overall_clarif_question,
        reason=overall_reason,
        risk_flags=overall_risk_flags,
    )
    return True, [], result

