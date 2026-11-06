"""
Curated Vietnamese Test Utterances and Benchmark Dataset for Voice AI.
Strictly implements Section 9 (Mandatory Utterance Tests) and Section 3 of
VOICE_AI_IMPLEMENTATION_PLAN.md.
"""

from __future__ import annotations

from typing import Any, Dict, List

from features.voice_assistant.voice_ai.domain.contracts import (
    IntentPriority,
    IntentType,
    RedFlagSeverity,
)

# 16 Mandatory Test Utterances from Section 9
MANDATORY_UTTERANCE_TESTS: List[Dict[str, Any]] = [
    {
        "id": "TC-01",
        "utterance": "Tôi đã uống thuốc rồi",
        "description": "Medication confirmation intent",
        "expected_safety": {"is_urgent_assistance": False, "has_negation": False},
        "expected_intents": [
            {
                "type": IntentType.MEDICATION_CONFIRM,
                "priority": IntentPriority.P3,
                "entities": {"status": "taken"},
                "requires_clarification": False,
            }
        ],
    },
    {
        "id": "TC-02",
        "utterance": "Tôi chưa uống thuốc",
        "description": "Medication not taken intent (must never confirm taken)",
        "expected_safety": {"is_urgent_assistance": False, "has_negation": False},
        "expected_intents": [
            {
                "type": IntentType.MEDICATION_NOT_TAKEN,
                "priority": IntentPriority.P3,
                "requires_clarification": False,
            }
        ],
    },
    {
        "id": "TC-03",
        "utterance": "Tôi không nói là tôi đã uống thuốc",
        "description": "Complex negation: must not trigger confirmation",
        "expected_safety": {"is_urgent_assistance": False, "has_negation": True},
        "expected_intents": [
            {
                "type": IntentType.CLARIFY,
                "priority": IntentPriority.NONE,
                "requires_clarification": True,
            }
        ],
    },
    {
        "id": "TC-04",
        "utterance": "Bây giờ tôi phải uống thuốc nào?",
        "description": "Medication guidance retrieval",
        "expected_safety": {"is_urgent_assistance": False, "has_negation": False},
        "expected_intents": [
            {
                "type": IntentType.MEDICATION_GUIDANCE,
                "priority": IntentPriority.P3,
                "requires_clarification": False,
            }
        ],
    },
    {
        "id": "TC-05",
        "utterance": "Tôi chóng mặt từ sáng",
        "description": "Health symptom observation",
        "expected_safety": {"is_urgent_assistance": False, "is_red_flag_health": False},
        "expected_intents": [
            {
                "type": IntentType.HEALTH_REPORT,
                "priority": IntentPriority.P2,
                "entities": {"symptoms": ["chóng mặt"], "onset": "từ sáng"},
                "requires_clarification": False,
            }
        ],
    },
    {
        "id": "TC-06",
        "utterance": "Gọi bác sĩ ngay",
        "description": "Explicit emergency doctor assistance (Fast-Path bypass LLM)",
        "expected_safety": {
            "is_urgent_assistance": True,
            "has_negation": False,
            "suggested_action": "DISPATCH_ASSISTANCE_IMMEDIATELY",
        },
        "expected_intents": [
            {
                "type": IntentType.DOCTOR_ASSISTANCE,
                "priority": IntentPriority.P1,
                "requires_clarification": False,
            }
        ],
    },
    {
        "id": "TC-07",
        "utterance": "Không cần gọi bác sĩ",
        "description": "Explicit negation of calling doctor: NO assistance request",
        "expected_safety": {
            "is_urgent_assistance": False,
            "has_negation": True,
        },
        "expected_intents": [
            {
                "type": IntentType.UNKNOWN,
                "priority": IntentPriority.NONE,
            }
        ],
    },
    {
        "id": "TC-08",
        "utterance": "Tôi uống thuốc rồi, chóng mặt, gọi bác sĩ",
        "description": "Multi-intent: Medication + Health symptom + Doctor assistance (P1 executed first)",
        "expected_safety": {
            "is_urgent_assistance": True,
            "suggested_action": "DISPATCH_ASSISTANCE_IMMEDIATELY",
        },
        "expected_intents": [
            {"type": IntentType.DOCTOR_ASSISTANCE, "priority": IntentPriority.P1},
            {"type": IntentType.HEALTH_REPORT, "priority": IntentPriority.P2},
            {"type": IntentType.MEDICATION_CONFIRM, "priority": IntentPriority.P3},
        ],
    },
    {
        "id": "TC-09",
        "utterance": "TV: phim đang chiếu người ta kêu gọi bác sĩ",
        "description": "Third-party TV background voice: PTT protects, or disambiguate",
        "expected_safety": {"is_urgent_assistance": True},
        "expected_intents": [
            {"type": IntentType.DOCTOR_ASSISTANCE, "priority": IntentPriority.P1}
        ],
    },
    {
        "id": "TC-10",
        "utterance": "Mở trò lật thẻ",
        "description": "Allowlisted game launch action",
        "expected_safety": {"is_urgent_assistance": False},
        "expected_intents": [
            {
                "type": IntentType.GAME_LAUNCH,
                "priority": IntentPriority.P4,
                "entities": {"game_type": "card_matching"},
                "requires_clarification": False,
            }
        ],
    },
    {
        "id": "TC-11",
        "utterance": "Bật nhạc thư giãn",
        "description": "Allowlisted relaxing music play action",
        "expected_safety": {"is_urgent_assistance": False},
        "expected_intents": [
            {
                "type": IntentType.MUSIC_PLAY,
                "priority": IntentPriority.P4,
                "requires_clarification": False,
            }
        ],
    },
    {
        "id": "TC-12",
        "utterance": "",
        "description": "Empty transcript / nonsensical audio: safe fallback, no side effect",
        "expected_safety": {"is_urgent_assistance": False},
        "expected_intents": [
            {
                "type": IntentType.UNKNOWN,
                "priority": IntentPriority.NONE,
                "requires_clarification": True,
            }
        ],
    },
    {
        "id": "TC-13",
        "utterance": "Hôm nay trời có mưa không con?",
        "description": "Out-of-domain query: reject and clarify politely",
        "expected_safety": {"is_urgent_assistance": False},
        "expected_intents": [
            {
                "type": IntentType.UNKNOWN,
                "priority": IntentPriority.NONE,
                "requires_clarification": True,
            }
        ],
    },
    {
        "id": "TC-14",
        "utterance": "Tắt nhạc đi",
        "description": "Stop media playback",
        "expected_safety": {"is_urgent_assistance": False},
        "expected_intents": [
            {
                "type": IntentType.MUSIC_STOP,
                "priority": IntentPriority.P4,
                "requires_clarification": False,
            }
        ],
    },
    {
        "id": "TC-15",
        "utterance": "Tôi bị đau thắt ngực dữ dội, khó thở quá",
        "description": "Critical clinical red-flag symptom: automatic emergency escalation",
        "expected_safety": {
            "is_urgent_assistance": True,
            "is_red_flag_health": True,
            "red_flag_severity": RedFlagSeverity.CRITICAL,
        },
        "expected_intents": [
            {
                "type": IntentType.DOCTOR_ASSISTANCE,
                "priority": IntentPriority.P1,
            }
        ],
    },
    {
        "id": "TC-16",
        "utterance": "10 phút nữa hãy nhắc tôi uống thuốc",
        "description": "Snooze / reschedule medication reminder",
        "expected_safety": {"is_urgent_assistance": False},
        "expected_intents": [
            {
                "type": IntentType.MEDICATION_REMIND_LATER,
                "priority": IntentPriority.P3,
                "entities": {"delay_minutes": 10},
                "requires_clarification": False,
            }
        ],
    },
]

