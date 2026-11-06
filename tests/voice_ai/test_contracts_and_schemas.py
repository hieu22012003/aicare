"""
Comprehensive Contract & Schema Test Suite for Voice AI Module.
Validates all DTOs, JSON Schemas, Fast-Path Safety Detector, State Machine,
and all 16 Mandatory Utterances from Section 9 of VOICE_AI_IMPLEMENTATION_PLAN.md.
"""

from __future__ import annotations

import time
import unittest
from typing import Any, Dict

from features.voice_assistant.voice_ai.domain.contracts import (
    INTENT_PRIORITY_MAP,
    ExecutionStatus,
    IntentPriority,
    IntentType,
    RedFlagSeverity,
    VoiceIntent,
)
from features.voice_assistant.voice_ai.orchestrator.state_machine import (
    SessionState,
    VoiceSessionStateMachine,
)
from features.voice_assistant.voice_ai.safety.assistance_rules import DeterministicSafetyDetector
from features.voice_assistant.voice_ai.semantic.mock_provider import MockSemanticProvider
from features.voice_assistant.voice_ai.semantic.schemas import (
    ALLOWED_ENTITIES_PER_INTENT,
    GEMINI_SEMANTIC_JSON_SCHEMA,
    validate_semantic_payload,
)
from tests.voice_ai.test_fixtures import MANDATORY_UTTERANCE_TESTS


class TestVoiceAIContractsAndSchemas(unittest.TestCase):
    def setUp(self) -> None:
        self.safety_detector = DeterministicSafetyDetector()
        self.mock_provider = MockSemanticProvider()

    def test_intent_types_and_priority_mapping(self) -> None:
        """Verify all 10 canonical intents are defined with correct backend priorities."""
        self.assertEqual(INTENT_PRIORITY_MAP[IntentType.DOCTOR_ASSISTANCE], IntentPriority.P1)
        self.assertEqual(INTENT_PRIORITY_MAP[IntentType.HEALTH_REPORT], IntentPriority.P2)
        self.assertEqual(INTENT_PRIORITY_MAP[IntentType.MEDICATION_CONFIRM], IntentPriority.P3)
        self.assertEqual(INTENT_PRIORITY_MAP[IntentType.MEDICATION_NOT_TAKEN], IntentPriority.P3)
        self.assertEqual(INTENT_PRIORITY_MAP[IntentType.MEDICATION_GUIDANCE], IntentPriority.P3)
        self.assertEqual(INTENT_PRIORITY_MAP[IntentType.MEDICATION_REMIND_LATER], IntentPriority.P3)
        self.assertEqual(INTENT_PRIORITY_MAP[IntentType.GAME_LAUNCH], IntentPriority.P4)
        self.assertEqual(INTENT_PRIORITY_MAP[IntentType.MUSIC_PLAY], IntentPriority.P4)
        self.assertEqual(INTENT_PRIORITY_MAP[IntentType.MUSIC_PAUSE], IntentPriority.P4)
        self.assertEqual(INTENT_PRIORITY_MAP[IntentType.MUSIC_STOP], IntentPriority.P4)

    def test_json_schema_validation_valid(self) -> None:
        """Verify schema validation succeeds on well-formed payload."""
        valid_payload = {
            "schema_version": "1.0",
            "language": "vi",
            "intents": [
                {
                    "type": "MEDICATION_CONFIRM",
                    "entities": {"status": "taken", "medication_ref": "Amlodipine"},
                    "evidence_span": "tôi đã uống amlodipine rồi",
                    "requires_clarification": False,
                }
            ],
        }
        ok, errors, res = validate_semantic_payload(valid_payload, "tôi đã uống amlodipine rồi")
        self.assertTrue(ok)
        self.assertEqual(len(errors), 0)
        self.assertIsNotNone(res)
        self.assertEqual(res.intents[0].type, IntentType.MEDICATION_CONFIRM)
        self.assertEqual(res.intents[0].priority, IntentPriority.P3)

    def test_json_schema_validation_rejects_unauthorized_entities(self) -> None:
        """Verify schema validation strictly rejects arbitrary entity injection."""
        invalid_payload = {
            "schema_version": "1.0",
            "language": "vi",
            "intents": [
                {
                    "type": "MUSIC_STOP",
                    "entities": {"malicious_code": "drop_database()"},  # unauthorized entity
                    "evidence_span": "dừng nhạc",
                    "requires_clarification": False,
                }
            ],
        }
        ok, errors, res = validate_semantic_payload(invalid_payload, "dừng nhạc")
        self.assertFalse(ok)
        self.assertTrue(any("unauthorized entity key" in e for e in errors))

    def test_json_schema_validation_rejects_invalid_intent_type(self) -> None:
        """Verify schema rejects unknown intent enums."""
        bad_payload = {
            "schema_version": "1.0",
            "language": "vi",
            "intents": [
                {
                    "type": "EXECUTE_ARBITRARY_COMMAND",
                    "entities": {},
                    "evidence_span": "xóa dữ liệu",
                    "requires_clarification": False,
                }
            ],
        }
        ok, errors, res = validate_semantic_payload(bad_payload, "xóa dữ liệu")
        self.assertFalse(ok)
        self.assertTrue(any("unsupported type enum" in e for e in errors))

    def test_safety_detector_execution_time_under_5ms(self) -> None:
        """Verify Fast-Path safety detector executes in strictly under 5 milliseconds."""
        transcript = "Tôi đau ngực quá, gọi bác sĩ cấp cứu giúp tôi ngay với"
        start_time = time.perf_counter()
        signal = self.safety_detector.analyze(transcript)
        elapsed_ms = (time.perf_counter() - start_time) * 1000.0

        self.assertLess(elapsed_ms, 5.0, f"Safety detector must execute in < 5ms (was {elapsed_ms:.2f}ms)")
        self.assertTrue(signal.is_urgent_assistance)
        self.assertFalse(signal.has_negation)
        self.assertEqual(signal.suggested_action, "DISPATCH_ASSISTANCE_IMMEDIATELY")

    def test_safety_detector_negation_handling(self) -> None:
        """Verify 'Không cần gọi bác sĩ' does NOT trigger an emergency dispatch."""
        signal = self.safety_detector.analyze("Tôi hơi mệt nhưng không cần gọi bác sĩ đâu")
        self.assertTrue(signal.has_negation)
        self.assertFalse(signal.is_urgent_assistance)

    def test_safety_detector_red_flag_triage(self) -> None:
        """Verify clinical red-flag symptoms trigger critical alert and urgent assistance."""
        signal = self.safety_detector.analyze("Tôi bị đau thắt ngực dữ dội, khó thở quá")
        self.assertTrue(signal.is_urgent_assistance)
        self.assertTrue(signal.is_red_flag_health)
        self.assertEqual(signal.red_flag_severity, RedFlagSeverity.CRITICAL)

    def test_state_machine_lifecycle(self) -> None:
        """Verify conversation state machine transitions correctly."""
        sm = VoiceSessionStateMachine(patient_id="patient_001")
        self.assertEqual(sm.current_state, SessionState.IDLE)

        sm.start_listening()
        self.assertEqual(sm.current_state, SessionState.LISTENING)

        sm.start_processing()
        self.assertEqual(sm.current_state, SessionState.PROCESSING)

        # Ambiguous medication -> Awaiting clarification
        med_intent = VoiceIntent(type=IntentType.MEDICATION_CONFIRM, requires_clarification=True)
        sm.set_awaiting_clarification(med_intent, question="Bác vừa uống thuốc huyết áp hay tiểu đường ạ?")
        self.assertEqual(sm.current_state, SessionState.AWAITING_CLARIFICATION)
        self.assertEqual(sm.context.pending_intent.type, IntentType.MEDICATION_CONFIRM)

        # Clarified and dispatched -> Completed
        sm.dispatch_completed(outcome_summary="Đã xác nhận liều Amlodipine")
        self.assertEqual(sm.current_state, SessionState.COMPLETED)
        self.assertIsNone(sm.context.pending_intent)

    def test_all_16_mandatory_utterances_dataset(self) -> None:
        """
        Verify all 16 mandatory test utterances from Section 9 produce
        valid semantic outputs and correct safety signals.
        """
        for tc in MANDATORY_UTTERANCE_TESTS:
            t_id = tc["id"]
            utterance = tc["utterance"]
            expected_safety = tc["expected_safety"]
            expected_intents = tc["expected_intents"]

            # 1. Test safety detector
            signal = self.safety_detector.analyze(utterance)
            if "is_urgent_assistance" in expected_safety:
                self.assertEqual(
                    signal.is_urgent_assistance,
                    expected_safety["is_urgent_assistance"],
                    f"[{t_id}] is_urgent_assistance mismatch for: '{utterance}'",
                )
            if "has_negation" in expected_safety:
                self.assertEqual(
                    signal.has_negation,
                    expected_safety["has_negation"],
                    f"[{t_id}] has_negation mismatch for: '{utterance}'",
                )

            # 2. Test semantic analysis
            sem_res = self.mock_provider.analyze(utterance)
            self.assertIsNotNone(sem_res, f"[{t_id}] Semantic result was None")
            self.assertGreater(len(sem_res.intents), 0, f"[{t_id}] No intents returned")

            # Check expected primary intent
            primary_expected = expected_intents[0]
            primary_actual = sem_res.intents[0]

            self.assertEqual(
                primary_actual.type,
                primary_expected["type"],
                f"[{t_id}] Intent type mismatch: expected {primary_expected['type']}, got {primary_actual.type}",
            )

            # Check priority ordering for multi-intent cases (e.g. TC-08)
            if len(expected_intents) > 1:
                self.assertEqual(len(sem_res.intents), len(expected_intents))
                priorities = [it.priority for it in sem_res.intents]
                # P1 should come first
                self.assertEqual(priorities[0], IntentPriority.P1)


if __name__ == "__main__":
    unittest.main()

