"""
Integration and Unit Tests for API STT Architecture.
Covers:
- SttGateway & ProviderAdapter (contracts, bounds, auth, secrets on backend)
- VoiceSessionController (state machine, cancel, late response, elder switch, off-UI thread)
- Business command integration (medication, rehab camera rule, idempotency, version conflict)
- Negation handling ("chưa uống", "không nhớ đã uống", "đừng đánh dấu đã uống")
- Network failure, retry, web update concurrency
"""

from datetime import datetime, timezone
import io
import math
import time
from typing import Any, Dict
from unittest.mock import MagicMock, patch
import wave

import pytest
from PyQt5.QtCore import QCoreApplication
from PyQt5.QtWidgets import QApplication

from shared.database.connection import UnifiedDatabaseManager
from shared.database.migrator import Migrator
from features.voice_assistant.voice_ai.domain.contracts import ExecutionStatus, IntentType, VoiceIntent
from features.voice_assistant.voice_ai.orchestrator.router import WorkflowOrchestrator
from features.voice_assistant.voice_ai.safety.assistance_rules import DeterministicSafetyDetector
from features.voice_assistant.voice_ai.stt.contracts import (
    BusinessCommand,
    SttErrorCode,
    SttRequest,
    SttResponse,
    SttSessionState,
)
from features.voice_assistant.voice_ai.stt.controller import VoiceSessionController
from features.voice_assistant.voice_ai.stt.gateway import BackendSttGateway
from features.voice_assistant.voice_ai.stt.provider_adapter import (
    GenericHttpSTTProviderAdapter,
    MockSTTProviderAdapter,
)
from features.web_portal.api_router import ApiRouter
from features.web_portal.auth import AuthService


# Headless Qt App Fixture
@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication(["-platform", "offscreen"])
    return app


@pytest.fixture
def memory_db():
    db = UnifiedDatabaseManager(":memory:")
    migrator = Migrator(db)
    migrator.apply_all()

    # Seed patient
    with db.connect() as conn:
        conn.execute(
            """
            INSERT INTO patients (patient_id, full_name, date_of_birth, gender, status)
            VALUES ('pat_001', 'Cụ Nguyễn Văn An', '1945-05-15', 'MALE', 'ACTIVE')
            """
        )
        conn.execute(
            """
            INSERT INTO users (user_id, email, phone, full_name, role, password_hash, is_active, created_at)
            VALUES ('user_cg', 'contact-a4c15ad3@example.com', '0901234567', 'Người Nhà An', 'caregiver', 'pbkdf2:demo', 1, '2026-10-09T08:00:00Z')
            """
        )
        conn.execute(
            """
            INSERT INTO caregivers (caregiver_id, user_id, relationship_desc)
            VALUES ('cg_001', 'user_cg', 'Con trai')
            """
        )
        conn.execute(
            """
            INSERT INTO patient_caregivers (patient_caregiver_id, patient_id, caregiver_id, is_primary, valid_from, valid_to, is_active)
            VALUES ('pcg_001', 'pat_001', 'cg_001', 1, '2026-01-01', '2099-12-31', 1)
            """
        )
        # Seed medication dose occurrence
        conn.execute(
            """
            INSERT INTO dose_occurrences (
                occurrence_id, schedule_id, revision, scheduled_at_utc,
                local_scheduled_time, grace_deadline_utc, state,
                medication_name_snapshot, dose_snapshot, instructions_snapshot,
                patient_id
            ) VALUES (
                'dose_123', 'sched_01', 1, '2026-10-09T08:00:00Z',
                '08:00', '2026-10-09T10:00:00Z', 'PENDING',
                'Amlodipine', '5mg', 'Uống sau ăn', 'pat_001'
            )
            """
        )
    return db


def create_sample_wav(duration_s: float = 0.5) -> bytes:
    wav_io = io.BytesIO()
    sample_count = int(16000 * duration_s)
    samples = []
    for i in range(sample_count):
        val = int(8000 * math.sin(2 * math.pi * 440.0 * i / 16000))
        samples.append(val.to_bytes(2, byteorder="little", signed=True))
    pcm_bytes = b"".join(samples)

    with wave.open(wav_io, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(16000)
        wf.writeframes(pcm_bytes)
    return wav_io.getvalue()


# =============================================================================
# 1. GATEWAY & PROVIDER ADAPTER TESTS
# =============================================================================

class TestSttGatewayAndProviderAdapter:
    """Tests the backend STT Gateway contracts and vendor adapter isolation."""

    def test_gateway_successful_transcription(self):
        mock_adapter = MockSTTProviderAdapter(default_text="Tôi đã uống thuốc rồi")
        gateway = BackendSttGateway(provider_adapter=mock_adapter)

        audio_bytes = create_sample_wav(0.5)
        req = SttRequest(patient_id="pat_001", language="vi", duration_ms=500)

        resp = gateway.process_stt_request(req, raw_audio_bytes=audio_bytes)
        assert resp.status == "completed"
        assert resp.transcript == "Tôi đã uống thuốc rồi"
        assert resp.error_code is None
        assert resp.confidence is not None

    def test_gateway_rejects_empty_or_too_short_audio(self):
        gateway = BackendSttGateway(provider_adapter=MockSTTProviderAdapter())
        req = SttRequest(patient_id="pat_001", language="vi")

        resp = gateway.process_stt_request(req, raw_audio_bytes=b"")
        assert resp.status == "failed"
        assert resp.error_code == SttErrorCode.NO_AUDIO.value

        resp_short = gateway.process_stt_request(req, raw_audio_bytes=b"RIFF_TOO_SHORT")
        assert resp_short.status == "failed"
        assert resp_short.error_code == SttErrorCode.NO_AUDIO.value

    def test_gateway_rejects_oversized_audio(self):
        gateway = BackendSttGateway(provider_adapter=MockSTTProviderAdapter(), max_audio_size_bytes=100)
        req = SttRequest(patient_id="pat_001")
        resp = gateway.process_stt_request(req, raw_audio_bytes=b"A" * 200)
        assert resp.status == "failed"
        assert resp.error_code == SttErrorCode.INVALID_AUDIO.value

    def test_gateway_rejects_unauthorized_patient_access(self, memory_db):
        auth = AuthService(memory_db)
        gateway = BackendSttGateway(auth_service=auth)

        # Actor 'user_unrelated' has no relationship to pat_001
        auth_context = {"user_id": "user_unrelated", "role": "caregiver"}
        req = SttRequest(patient_id="pat_001")
        resp = gateway.process_stt_request(req, auth_context=auth_context, raw_audio_bytes=create_sample_wav(0.2))

        assert resp.status == "failed"
        assert resp.error_code == SttErrorCode.FORBIDDEN.value

    def test_generic_http_adapter_error_normalization(self):
        adapter = GenericHttpSTTProviderAdapter(
            api_key="mock_secret_key",
            api_endpoint="http://127.0.0.1:9999/non_existent_endpoint",
            timeout_seconds=0.2,
            max_retries=0,
        )
        audio_bytes = create_sample_wav(0.2)
        resp = adapter.transcribe(audio_bytes=audio_bytes)
        assert resp.status == "failed"
        assert resp.error_code in (SttErrorCode.PROVIDER_UNAVAILABLE.value, SttErrorCode.TIMEOUT.value)


# =============================================================================
# 2. VOICE SESSION CONTROLLER TESTS
# =============================================================================

class TestVoiceSessionController:
    """Verifies state machine transitions, cancellation, late responses, and elder changes."""

    def test_controller_normal_flow(self, qapp):
        mock_adapter = MockSTTProviderAdapter(default_text="Bác sĩ ơi tôi mệt")
        gateway = BackendSttGateway(provider_adapter=mock_adapter)
        from features.voice_assistant.voice_ai.stt.client import SttClient
        client = SttClient(direct_gateway=gateway)

        ctrl = VoiceSessionController(stt_client=client)
        states_recorded = []
        ctrl.state_changed.connect(lambda st, msg: states_recorded.append(st))

        # 1. Start recording
        assert ctrl.on_recording_started(patient_id="pat_001") is True
        assert ctrl.state == SttSessionState.RECORDING

        # 2. Process audio
        audio = create_sample_wav(0.4)
        received_transcripts = []
        ctrl.transcript_ready.connect(lambda res: received_transcripts.append(res))

        ctrl.process_audio(audio)
        if ctrl._active_worker:
            ctrl._active_worker.wait(2000)
            qapp.processEvents()

        assert ctrl.state == SttSessionState.REVIEW_CONFIRMING
        assert len(received_transcripts) == 1
        assert received_transcripts[0].transcript == "Bác sĩ ơi tôi mệt"

        ctrl.execution_success()
        assert ctrl.state == SttSessionState.IDLE

    def test_controller_cancel_discards_late_http_response(self, qapp):
        """Cancelling during transcribing must discard any late arriving response."""
        mock_adapter = MockSTTProviderAdapter(default_text="Câu nói muộn")
        gateway = BackendSttGateway(provider_adapter=mock_adapter)
        from features.voice_assistant.voice_ai.stt.client import SttClient
        client = SttClient(direct_gateway=gateway)

        ctrl = VoiceSessionController(stt_client=client)
        ctrl.on_recording_started(patient_id="pat_001")

        received_transcripts = []
        ctrl.transcript_ready.connect(lambda res: received_transcripts.append(res))

        # Explicit cancel
        ctrl.cancel()
        assert ctrl.state == SttSessionState.IDLE
        assert ctrl.active_session_id is None

        # Simulate late response arriving from previous worker
        late_resp = SttResponse(
            request_id="old_req",
            voice_session_id="old_vses_123",
            status="completed",
            transcript="Câu nói muộn",
        )
        ctrl._on_stt_worker_finished(late_resp)
        qapp.processEvents()

        # Late response MUST be discarded
        assert len(received_transcripts) == 0

    def test_controller_elder_change_aborts_session(self, qapp):
        """Switching elder profile during session must abort session immediately."""
        ctrl = VoiceSessionController()
        ctrl.snapshot_context(patient_id="pat_001")
        ctrl.on_recording_started()

        assert ctrl.state == SttSessionState.RECORDING
        assert ctrl.snapshot_patient_id == "pat_001"

        # Switch to pat_002
        ctrl.notify_patient_changed("pat_002")

        assert ctrl.state == SttSessionState.IDLE
        assert ctrl.snapshot_patient_id == "pat_002"
        assert ctrl.active_session_id is None


# =============================================================================
# 3. NEGATION HANDLING TESTS (CRITICAL CLINICAL RULES)
# =============================================================================

class TestNegationAndSafetyRules:
    """Verifies that negation phrases never mark medication as taken."""

    @pytest.mark.parametrize(
        "utterance",
        [
            "Tôi không nhớ đã uống chưa",
            "Đừng đánh dấu đã uống",
            "Chưa cần gọi bác sĩ",
            "Không nói là tôi đã uống",
        ],
    )
    def test_medication_negations_detected_by_safety_detector(self, utterance):
        detector = DeterministicSafetyDetector()
        sig = detector.analyze(utterance)
        assert sig.has_negation is True, f"Failed to detect negation in: '{utterance}'"

    def test_orchestrator_overrides_medication_confirm_on_negation(self, memory_db):
        orchestrator = WorkflowOrchestrator(db=memory_db)
        auth_context = {"patient_id": "pat_001", "role": "patient"}

        # Even if semantic model extracts MEDICATION_CONFIRM, negation rule converts to CLARIFY
        result = orchestrator.process_voice_interaction(
            auth_context=auth_context,
            transcript="Tôi chưa uống thuốc",
        )

        # Must not be completed MEDICATION_CONFIRM
        for ir in result.intent_results:
            assert ir.intent_type != IntentType.MEDICATION_CONFIRM or ir.status != ExecutionStatus.COMPLETED

        # Check DB dose occurrence state remains PENDING
        with memory_db.connect() as conn:
            row = conn.fetchone("SELECT state FROM dose_occurrences WHERE occurrence_id = 'dose_123'")
            assert row["state"] == "PENDING"

    def test_rehab_verbal_completion_never_marks_rehab_finished(self, memory_db):
        """Rule: 'Tôi tập xong rồi' does NOT mark rehabilitation as completed."""
        orchestrator = WorkflowOrchestrator(db=memory_db)
        auth_context = {"patient_id": "pat_001", "role": "patient"}

        result = orchestrator.process_voice_interaction(
            auth_context=auth_context,
            transcript="Tôi tập xong rồi nhé",
        )

        # Must not contain any completed rehabilitation confirmation
        for ir in result.intent_results:
            assert ir.intent_type != IntentType.REHABILITATION_START or ir.status != ExecutionStatus.COMPLETED

        # No rehab sessions marked as verified
        with memory_db.connect() as conn:
            sessions = conn.fetchall("SELECT * FROM rehab_sessions WHERE patient_id = 'pat_001'")
            assert len(sessions) == 0


# =============================================================================
# 4. BUSINESS INTEGRATION, VERSION CONFLICT, AND IDEMPOTENCY
# =============================================================================

class TestBusinessCommandAndConcurrency:
    """Verifies authoritative DB updates, expected_version conflict, and idempotency."""

    def test_record_dose_action_success_and_outbox_event(self, memory_db):
        auth = AuthService(memory_db)
        router = ApiRouter(db=memory_db, auth=auth)

        status, resp = router._record_dose_action(
            patient_id="pat_001",
            occurrence_id="dose_123",
            user_id="user_cg",
            body={"action": "self_reported_taken", "expected_version": 1},
        )
        assert status == 200

        # Verify DB state
        with memory_db.connect() as conn:
            occ = conn.fetchone("SELECT state FROM dose_occurrences WHERE occurrence_id = 'dose_123'")
            assert occ["state"] == "CONFIRMED"

            # Outbox event committed atomically in SAME transaction
            outbox = conn.fetchone(
                "SELECT * FROM outbox_events WHERE aggregate_id = 'dose_123' AND event_type = 'medication.dose_changed'"
            )
            assert outbox is not None
            assert outbox["patient_id"] == "pat_001"

    def test_record_dose_action_version_conflict_409(self, memory_db):
        auth = AuthService(memory_db)
        router = ApiRouter(db=memory_db, auth=auth)

        # Record has revision=1. Client sends expected_version=99 (changed on web)
        status, resp = router._record_dose_action(
            patient_id="pat_001",
            occurrence_id="dose_123",
            user_id="user_cg",
            body={"action": "self_reported_taken", "expected_version": 99},
        )
        assert status == 409
        assert "Xung đột phiên bản" in resp["error"]

    def test_record_dose_action_idempotency_deduplication(self, memory_db):
        auth = AuthService(memory_db)
        router = ApiRouter(db=memory_db, auth=auth)

        idemp_key = "idemp_unique_dose_test_1"
        status1, resp1 = router._record_dose_action(
            patient_id="pat_001",
            occurrence_id="dose_123",
            user_id="user_cg",
            body={"action": "self_reported_taken", "idempotency_key": idemp_key},
        )
        assert status1 == 200

        # Repeated request with same idempotency_key
        status2, resp2 = router._record_dose_action(
            patient_id="pat_001",
            occurrence_id="dose_123",
            user_id="user_cg",
            body={"action": "self_reported_taken", "idempotency_key": idemp_key},
        )
        assert status2 == 200
        assert resp2.get("status") in ("duplicate_idempotent", "already_confirmed")

        # Confirm only ONE dose_events record exists for this idempotency key
        with memory_db.connect() as conn:
            events = conn.fetchall("SELECT * FROM dose_events WHERE idempotency_key = ?", (idemp_key,))
            assert len(events) == 1


class TestDecommissionedSttEndpoints:
    """Verifies that external STT API endpoints and audio ingestion are safely decommissioned."""

    def test_voice_stt_endpoint_returns_410_gone(self, memory_db):
        auth = AuthService(memory_db)
        token = auth.create_session({
            "user_id": "user_cg",
            "email": "contact-a4c15ad3@example.com",
            "full_name": "Người Nhà An",
            "role": "caregiver",
        })
        router = ApiRouter(db=memory_db, auth=auth)

        # Call decommissioned endpoint POST /api/v1/voice/stt
        status, resp = router.handle_request(
            "POST",
            "/api/v1/voice/stt",
            {},
            {"patient_id": "pat_001", "audio_base64": "dummy"},
            token=token,
        )
        assert status == 410
        assert resp.get("status") == "disabled"
        assert "decommissioned" in resp.get("error", "").lower()

    def test_voice_interaction_submit_rejects_audio_base64_without_calling_provider(self, memory_db):
        auth = AuthService(memory_db)
        token = auth.create_session({
            "user_id": "user_cg",
            "email": "contact-a4c15ad3@example.com",
            "full_name": "Người Nhà An",
            "role": "caregiver",
        })
        router = ApiRouter(db=memory_db, auth=auth)

        # Submitting audio_base64 without pre-transcribed text must be rejected with 400
        # and never call any external provider
        status, resp = router.handle_request(
            "POST",
            "/api/v1/voice/interactions",
            {},
            {"patient_id": "pat_001", "audio_base64": "UklGRiQAAABXQVZFZm10IBAAAA=="},
            token=token,
        )
        assert status == 400
        assert "Audio STT processing is disabled" in resp.get("error", "")

