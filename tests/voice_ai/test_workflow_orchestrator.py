"""
Unit and Integration Tests for Phase 4: Workflow Orchestrator and Safety Router.
Strictly verifies Phase 4 requirements and gates of VOICE_AI_IMPLEMENTATION_PLAN.md.
"""

from __future__ import annotations

from unittest.mock import MagicMock
import pytest

from features.voice_assistant.voice_ai.domain.contracts import (
    ExecutionStatus,
    IntentPriority,
    IntentType,
    OrchestratorExecutionResult,
)
from features.voice_assistant.voice_ai.orchestrator.actions import ActionType
from features.voice_assistant.voice_ai.orchestrator.idempotency import IdempotencyManager
from features.voice_assistant.voice_ai.orchestrator.router import WorkflowOrchestrator
from features.voice_assistant.voice_ai.safety.assistance_rules import DeterministicSafetyDetector
from features.voice_assistant.voice_ai.semantic.mock_provider import MockSemanticProvider


class TestWorkflowOrchestrator:
    @pytest.fixture
    def auth_context(self):
        return {
            "patient_id": "patient_elder_001",
            "current_screen": "DASHBOARD",
        }

    def test_unauthenticated_request_rejected(self):
        orchestrator = WorkflowOrchestrator()
        result = orchestrator.process_voice_interaction(
            auth_context={},  # missing patient_id
            transcript="Gọi bác sĩ",
        )
        assert result.overall_status == ExecutionStatus.FAILED
        assert "Lỗi xác thực" in result.response_speech_text

    def test_fast_path_urgent_assistance_bypasses_semantic_provider(self, auth_context):
        """Urgent SOS must execute deterministically in <5ms without calling LLM."""
        mock_semantic = MagicMock(spec=MockSemanticProvider)
        safety_detector = DeterministicSafetyDetector()

        orchestrator = WorkflowOrchestrator(
            safety_detector=safety_detector,
            semantic_provider=mock_semantic,
        )

        result = orchestrator.process_voice_interaction(
            auth_context=auth_context,
            transcript="Gọi bác sĩ ngay",
        )

        # Semantic provider should NOT have been called for pure urgent SOS
        assert not mock_semantic.analyze.called

        assert result.overall_status == ExecutionStatus.COMPLETED
        assert len(result.intent_results) == 1
        urgent_res = result.intent_results[0]
        assert urgent_res.intent_type == IntentType.DOCTOR_ASSISTANCE
        assert urgent_res.priority == IntentPriority.P1
        assert urgent_res.client_action is not None
        assert urgent_res.client_action.action_type == ActionType.ASSISTANCE_ALERT.value
        assert "Đã gửi yêu cầu trợ giúp" in result.response_speech_text

    def test_multi_intent_priority_scheduling_and_isolation(self, auth_context):
        """
        'Uống thuốc xong rồi, tôi hơi chóng mặt, gọi bác sĩ giúp tôi'
        Must execute P1 (Doctor) -> P2 (Health) -> P3 (Medication).
        Medication failure must NEVER roll back or abort doctor assistance!
        """
        mock_assistance_handler = MagicMock(return_value={"request_id": "ast_12345"})
        mock_health_handler = MagicMock(return_value={"report_id": "hlth_67890"})

        # Intentionally failing medication handler to test failure isolation
        def failing_medication_handler(ctx, intent):
            raise RuntimeError("Database connection timeout on medication table")

        orchestrator = WorkflowOrchestrator(
            assistance_handler=mock_assistance_handler,
            health_handler=mock_health_handler,
            medication_handler=failing_medication_handler,
        )

        transcript = "Uống thuốc xong rồi, tôi hơi chóng mặt, gọi bác sĩ giúp tôi"
        result = orchestrator.process_voice_interaction(
            auth_context=auth_context,
            transcript=transcript,
        )

        # Verify priority scheduling: P1 first, P2 second, P3 third
        assert len(result.intent_results) == 3
        res_p1 = result.intent_results[0]
        res_p2 = result.intent_results[1]
        res_p3 = result.intent_results[2]

        assert res_p1.priority == IntentPriority.P1
        assert res_p1.intent_type == IntentType.DOCTOR_ASSISTANCE
        assert res_p1.status == ExecutionStatus.COMPLETED
        assert res_p1.aggregate_id == "ast_12345"

        assert res_p2.priority == IntentPriority.P2
        assert res_p2.intent_type == IntentType.HEALTH_REPORT
        assert res_p2.status == ExecutionStatus.COMPLETED
        assert res_p2.aggregate_id == "hlth_67890"

        # P3 failed, but P1 and P2 were NOT rolled back!
        assert res_p3.priority == IntentPriority.P3
        assert res_p3.intent_type == IntentType.MEDICATION_CONFIRM
        assert res_p3.status == ExecutionStatus.FAILED

        # Because P1 urgent assistance succeeded, overall_status is preserved
        assert any(r.status == ExecutionStatus.COMPLETED and r.priority == IntentPriority.P1 for r in result.intent_results)

    def test_complex_negation_never_confirms_medication(self, auth_context):
        """'Tôi không nói là tôi đã uống thuốc' must never confirm medication adherence."""
        orchestrator = WorkflowOrchestrator()
        result = orchestrator.process_voice_interaction(
            auth_context=auth_context,
            transcript="Tôi không nói là tôi đã uống thuốc",
        )

        # Ensure no MEDICATION_CONFIRM was executed
        med_confirm_results = [
            r for r in result.intent_results if r.intent_type == IntentType.MEDICATION_CONFIRM
        ]
        assert len(med_confirm_results) == 0

        # Should require clarification
        assert result.overall_status == ExecutionStatus.NEEDS_CLARIFICATION
        assert any(r.status == ExecutionStatus.NEEDS_CLARIFICATION for r in result.intent_results)

    def test_negation_on_assistance_does_not_trigger_sos(self, auth_context):
        """'Không cần gọi bác sĩ đâu' must NOT trigger doctor assistance."""
        mock_assistance_handler = MagicMock()
        orchestrator = WorkflowOrchestrator(
            assistance_handler=mock_assistance_handler,
        )

        result = orchestrator.process_voice_interaction(
            auth_context=auth_context,
            transcript="Không cần gọi bác sĩ đâu",
        )

        assert not mock_assistance_handler.called
        sos_results = [
            r for r in result.intent_results if r.intent_type == IntentType.DOCTOR_ASSISTANCE
        ]
        assert len(sos_results) == 0

    def test_idempotency_prevents_duplicate_actions(self, auth_context):
        """Submitting the same idempotency key twice returns cached result without duplicate execution."""
        mock_assistance_handler = MagicMock(return_value={"request_id": "ast_unique_999"})
        idempotency_mgr = IdempotencyManager()

        orchestrator = WorkflowOrchestrator(
            assistance_handler=mock_assistance_handler,
            idempotency_manager=idempotency_mgr,
        )

        key = "idemp_test_key_abc_123"
        res1 = orchestrator.process_voice_interaction(
            auth_context=auth_context,
            transcript="Gọi bác sĩ cho tôi",
            idempotency_key=key,
        )

        assert mock_assistance_handler.call_count == 1

        # Second call with the same idempotency key
        res2 = orchestrator.process_voice_interaction(
            auth_context=auth_context,
            transcript="Gọi bác sĩ cho tôi",
            idempotency_key=key,
        )

        # Handler was NOT called a second time
        assert mock_assistance_handler.call_count == 1
        assert res1.interaction_id == res2.interaction_id
        assert res2.intent_results[0].aggregate_id == "ast_unique_999"

    def test_game_launch_allowlisted_action(self, auth_context):
        orchestrator = WorkflowOrchestrator()
        result = orchestrator.process_voice_interaction(
            auth_context=auth_context,
            transcript="Mở trò chơi lật thẻ",
        )

        assert len(result.intent_results) == 1
        res = result.intent_results[0]
        assert res.intent_type == IntentType.GAME_LAUNCH
        assert res.status == ExecutionStatus.COMPLETED
        assert res.client_action.action_type == "game.open"
        assert res.client_action.parameters == {"game_type": "card_matching"}

    def test_music_allowlisted_action(self, auth_context):
        orchestrator = WorkflowOrchestrator()
        result = orchestrator.process_voice_interaction(
            auth_context=auth_context,
            transcript="Bật nhạc nhẹ cho tôi nghe",
        )

        assert len(result.intent_results) == 1
        res = result.intent_results[0]
        assert res.intent_type == IntentType.MUSIC_PLAY
        assert res.status == ExecutionStatus.COMPLETED
        assert res.client_action.action_type == "media.play"
        assert res.client_action.parameters == {"playlist": "nhạc nhẹ"}

