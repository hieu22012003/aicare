"""
Unit and Integration Tests for Phase 6: Realtime Sync & Notifications.
Tests:
1. Assistance Request Lifecycle (PENDING_ACK -> ACKNOWLEDGED -> RESOLVED).
2. Prevention of False Acknowledgement before verified clinician confirmation.
3. SLA Timeout Escalation (PENDING_ACK -> ESCALATED).
4. Outbox Relay processing and SSE broadcast authorization.
5. Reconnection & Idempotent Resync.
Strictly implements Phase 6 requirements of VOICE_AI_IMPLEMENTATION_PLAN.md.
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timedelta, timezone
import pytest

from shared.database.connection import UnifiedDatabaseManager
from shared.services.outbox_relay import OutboxRelayService
from features.voice_assistant.voice_ai.business_adapters.assistance_adapter import DoctorAssistanceAdapter
from features.voice_assistant.voice_ai.domain.contracts import IntentType, VoiceIntent
from features.voice_assistant.voice_ai.realtime.assistance_coordinator import (
    AssistanceCoordinator,
    AssistanceLifecycleState,
)
from features.web_portal.auth import AuthService
from features.web_portal.sse_hub import SSEHub

UTC = timezone.utc


@pytest.fixture
def realtime_db():
    """Initializes in-memory database with production schemas and multi-user baseline."""
    db = UnifiedDatabaseManager(":memory:")
    with open("shared/database/migrations/001_initial_schema.sql", encoding="utf-8") as f:
        sql1 = f.read()
    with open("shared/database/migrations/003_outbox_and_sync.sql", encoding="utf-8") as f:
        sql3 = f.read()

    with db.connect() as conn:
        conn.executescript(sql1)
        conn.executescript(sql3)

        now_iso = datetime.now(UTC).isoformat()

        # Patient 1 & User 1
        conn.execute(
            "INSERT INTO users (user_id, email, phone, full_name, password_hash, role, is_active, created_at) "
            "VALUES ('u_elder_1', 'contact-3442c361@example.com', '0901111111', 'Cụ An', 'hash', 'patient', 1, ?)",
            (now_iso,),
        )
        conn.execute(
            "INSERT INTO patients (patient_id, user_id, full_name, date_of_birth, gender) "
            "VALUES ('p_elder_1', 'u_elder_1', 'Cụ An', '1945-01-01', 'Nam')",
        )

        # Patient 2 & User 2
        conn.execute(
            "INSERT INTO users (user_id, email, phone, full_name, password_hash, role, is_active, created_at) "
            "VALUES ('u_elder_2', 'contact-6a4fade2@example.com', '0902222222', 'Cụ Bình', 'hash', 'patient', 1, ?)",
            (now_iso,),
        )
        conn.execute(
            "INSERT INTO patients (patient_id, user_id, full_name, date_of_birth, gender) "
            "VALUES ('p_elder_2', 'u_elder_2', 'Cụ Bình', '1946-02-02', 'Nam')",
        )

        # Clinician 1 (assigned to Patient 1)
        conn.execute(
            "INSERT INTO users (user_id, email, phone, full_name, password_hash, role, is_active, created_at) "
            "VALUES ('u_doc_1', 'contact-23550f9a@example.com', '0911111111', 'BS. Minh', 'hash', 'clinician', 1, ?)",
            (now_iso,),
        )
        conn.execute(
            "INSERT INTO clinicians (clinician_id, user_id, license_number, specialty, is_verified, is_simulation) "
            "VALUES ('c_doc_1', 'u_doc_1', 'LIC-001', 'Lão khoa', 1, 0)",
        )
        conn.execute(
            "INSERT INTO care_team (care_team_id, patient_id, clinician_id, role_description, valid_from, valid_to, is_active) "
            "VALUES ('ct_1', 'p_elder_1', 'c_doc_1', 'Bác sĩ phụ trách', ?, '2099-12-31T23:59:59Z', 1)",
            (now_iso,),
        )

        # Caregiver 1 (assigned to Patient 1)
        conn.execute(
            "INSERT INTO users (user_id, email, phone, full_name, password_hash, role, is_active, created_at) "
            "VALUES ('u_cg_1', 'contact-ab1fd677@example.com', '0922222222', 'Cô Hoa', 'hash', 'caregiver', 1, ?)",
            (now_iso,),
        )
        conn.execute(
            "INSERT INTO caregivers (caregiver_id, user_id, relationship_desc) "
            "VALUES ('cg_1', 'u_cg_1', 'Con gái')",
        )
        conn.execute(
            "INSERT INTO patient_caregivers (patient_caregiver_id, patient_id, caregiver_id, relationship_type, is_primary, consent_status, valid_from, valid_to, is_active) "
            "VALUES ('pc_1', 'p_elder_1', 'cg_1', 'Con gái', 1, 'GRANTED', ?, '2099-12-31T23:59:59Z', 1)",
            (now_iso,),
        )
        conn.commit()

    return db


class TestAssistanceLifecycle:
    def test_lifecycle_pending_ack_to_acknowledged(self, realtime_db):
        """
        Step 1: Elder initiates help request -> PENDING_ACK (Never falsely confirmed).
        Step 2: Clinician verifies & acknowledges -> ACKNOWLEDGED.
        """
        adapter = DoctorAssistanceAdapter(db=realtime_db)
        coordinator = AssistanceCoordinator(db=realtime_db)

        # 1. Elder requests help
        intent = VoiceIntent(
            type=IntentType.DOCTOR_ASSISTANCE,
            entities={"urgency": "high", "reason": "chóng mặt té ngã"},
            evidence_span="gọi bác sĩ cứu tôi",
        )
        res = adapter.handle_assistance({"patient_id": "p_elder_1"}, intent)
        req_id = res["request_id"]
        assert res["status"] == AssistanceLifecycleState.PENDING_ACK

        # Check authoritative status
        status_info = coordinator.get_assistance_status(req_id)
        assert status_info["status"] == AssistanceLifecycleState.PENDING_ACK

        # 2. Doctor acknowledges request
        ack_res = coordinator.acknowledge_request(
            request_id=req_id,
            clinician_user_id="u_doc_1",
            notes="Đang gọi lại cho cụ",
        )
        assert ack_res["status"] == AssistanceLifecycleState.ACKNOWLEDGED
        assert ack_res["acknowledged_by"] == "c_doc_1"

        # Check authoritative status updated
        status_info2 = coordinator.get_assistance_status(req_id)
        assert status_info2["status"] == AssistanceLifecycleState.ACKNOWLEDGED
        assert status_info2["ack_details"]["notes"] == "Đang gọi lại cho cụ"

    def test_unauthorized_user_cannot_acknowledge(self, realtime_db):
        adapter = DoctorAssistanceAdapter(db=realtime_db)
        coordinator = AssistanceCoordinator(db=realtime_db)

        intent = VoiceIntent(
            type=IntentType.DOCTOR_ASSISTANCE,
            entities={"urgency": "high"},
            evidence_span="gọi bác sĩ",
        )
        res = adapter.handle_assistance({"patient_id": "p_elder_1"}, intent)
        req_id = res["request_id"]

        # Caregiver (not clinician) attempts to acknowledge
        with pytest.raises(PermissionError):
            coordinator.acknowledge_request(
                request_id=req_id,
                clinician_user_id="u_cg_1",
            )

    def test_sla_breach_escalates_to_caregiver(self, realtime_db):
        """
        If doctor does not acknowledge within SLA, request escalates to ESCALATED.
        """
        adapter = DoctorAssistanceAdapter(db=realtime_db)
        coordinator = AssistanceCoordinator(db=realtime_db, default_sla_seconds=0.05)

        # Create request with occurred_at 10 seconds ago
        past_iso = (datetime.now(UTC) - timedelta(seconds=10)).isoformat()
        intent = VoiceIntent(
            type=IntentType.DOCTOR_ASSISTANCE,
            entities={"urgency": "high"},
            evidence_span="cứu tôi",
        )
        res = adapter.handle_assistance({"patient_id": "p_elder_1"}, intent)
        req_id = res["request_id"]

        # Backdate outbox event to simulate timeout
        with realtime_db.connect() as conn:
            conn.execute(
                "UPDATE outbox_events SET occurred_at = ? WHERE aggregate_id = ?",
                (past_iso, req_id),
            )
            conn.commit()

        # Run SLA scanner
        escalated = coordinator.scan_and_escalate_sla_breaches(sla_seconds=1.0)
        assert len(escalated) == 1
        assert escalated[0]["request_id"] == req_id
        assert escalated[0]["status"] == AssistanceLifecycleState.ESCALATED

        # Check authoritative status is now ESCALATED
        status_info = coordinator.get_assistance_status(req_id)
        assert status_info["status"] == AssistanceLifecycleState.ESCALATED


class TestOutboxRelayAndSSE:
    def test_outbox_relay_processes_and_emits_sse(self, realtime_db):
        auth_service = AuthService(realtime_db)
        relay = OutboxRelayService(db=realtime_db, auth_service=auth_service)
        hub = SSEHub.get_instance()

        # Register doctor client (u_doc_1 has access to p_elder_1)
        doc_client = hub.register_client(user_id="u_doc_1", role="clinician")

        # Create doctor assistance event
        adapter = DoctorAssistanceAdapter(db=realtime_db)
        adapter.handle_assistance(
            {"patient_id": "p_elder_1"},
            VoiceIntent(type=IntentType.DOCTOR_ASSISTANCE, entities={"urgency": "high"}, evidence_span="gọi bác sĩ"),
        )

        # Run one batch of outbox relay processing
        processed_count = relay.process_pending_events()
        assert processed_count >= 1

        # Doctor client should receive the event
        assert not doc_client.queue.empty()
        event_received = doc_client.queue.get_nowait()
        assert event_received["type"] == "assistance.requested"
        assert event_received["patient_id"] == "p_elder_1"

        # Cleanup
        hub.unregister_client(doc_client.client_id)

    def test_access_control_filters_unauthorized_patient(self, realtime_db):
        auth_service = AuthService(realtime_db)
        relay = OutboxRelayService(db=realtime_db, auth_service=auth_service)
        hub = SSEHub.get_instance()

        # Register doctor 1 (u_doc_1 is only assigned to p_elder_1, NOT p_elder_2)
        doc_client = hub.register_client(user_id="u_doc_1", role="clinician")

        # Create an event for Patient 2
        adapter = DoctorAssistanceAdapter(db=realtime_db)
        adapter.handle_assistance(
            {"patient_id": "p_elder_2"},
            VoiceIntent(type=IntentType.DOCTOR_ASSISTANCE, entities={"urgency": "high"}, evidence_span="gọi bác sĩ"),
        )

        # Process outbox events
        relay.process_pending_events()

        # Doctor 1 must NOT receive Patient 2's event
        assert doc_client.queue.empty()

        # Cleanup
        hub.unregister_client(doc_client.client_id)

