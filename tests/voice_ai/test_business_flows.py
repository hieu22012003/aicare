"""
End-to-End Business Flow Integration Tests for Phase 5.
Validates:
W1: Medication Confirmation (with user-declared adherence & disambiguation)
W2: Medication Guidance (approved prescription reading, no hallucination)
W3: Health Symptom Reporting (symptom_entries, outbox events)
W4: Doctor Assistance (PENDING_ACK status, doctor assignment, outbox events)
W5: Games (allowlisted game.open tokens)
W6: Music (allowlisted media tokens)
Strictly implements Phase 5 requirements of VOICE_AI_IMPLEMENTATION_PLAN.md.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
import pytest

from shared.database.connection import UnifiedDatabaseManager
from features.voice_assistant.voice_ai.business_adapters.assistance_adapter import DoctorAssistanceAdapter
from features.voice_assistant.voice_ai.business_adapters.health_adapter import HealthReportAdapter
from features.voice_assistant.voice_ai.business_adapters.media_games_adapter import MediaAndGamesAdapter
from features.voice_assistant.voice_ai.business_adapters.medication_adapter import MedicationAdapter
from features.voice_assistant.voice_ai.domain.contracts import (
    ExecutionStatus,
    IntentPriority,
    IntentType,
    VoiceIntent,
)
from features.voice_assistant.voice_ai.orchestrator.router import WorkflowOrchestrator

UTC = timezone.utc


@pytest.fixture
def in_memory_db():
    """Initializes in-memory database with 001 and 003 schemas and baseline test records."""
    db = UnifiedDatabaseManager(":memory:")
    with open("shared/database/migrations/001_initial_schema.sql", encoding="utf-8") as f:
        sql1 = f.read()
    with open("shared/database/migrations/003_outbox_and_sync.sql", encoding="utf-8") as f:
        sql3 = f.read()

    with db.connect() as conn:
        conn.executescript(sql1)
        conn.executescript(sql3)

        # Baseline seed data for tests
        now_iso = datetime.now(UTC).isoformat()
        # 1. Insert user and patient
        conn.execute(
            "INSERT INTO users (user_id, email, phone, full_name, password_hash, role, is_active, created_at) "
            "VALUES ('u_elder_1', 'contact-8dc33d97@example.com', '0901234567', 'Cụ Nguyễn Văn An', 'hash', 'patient', 1, ?)",
            (now_iso,),
        )
        conn.execute(
            "INSERT INTO patients (patient_id, user_id, full_name, date_of_birth, gender) "
            "VALUES ('p_elder_1', 'u_elder_1', 'Cụ Nguyễn Văn An', '1945-05-15', 'Nam')",
        )

        # 2. Insert doctor and care_team
        conn.execute(
            "INSERT INTO users (user_id, email, phone, full_name, password_hash, role, is_active, created_at) "
            "VALUES ('u_doc_1', 'contact-2b094030@example.com', '0912345678', 'BS. Trần Văn Minh', 'hash', 'clinician', 1, ?)",
            (now_iso,),
        )
        conn.execute(
            "INSERT INTO clinicians (clinician_id, user_id, license_number, specialty, is_verified, is_simulation) "
            "VALUES ('c_doc_1', 'u_doc_1', 'CCHN-12345', 'Tim Mạch Lão Khoa', 1, 0)",
        )
        conn.execute(
            "INSERT INTO care_team (care_team_id, patient_id, clinician_id, role_description, valid_from, valid_to, is_active) "
            "VALUES ('ct_1', 'p_elder_1', 'c_doc_1', 'Bác sĩ phụ trách chính', ?, '2099-12-31T23:59:59Z', 1)",
            (now_iso,),
        )

        # 3. Insert medication prescription & prescription_items
        conn.execute(
            "INSERT INTO medications (medication_id, brand_name, generic_name, product_strength, strength_unit, dosage_form) "
            "VALUES ('med_1', 'Amlodipine', 'Amlodipine Besylate', '5', 'mg', 'Viên nén')",
        )
        conn.execute(
            "INSERT INTO medications (medication_id, brand_name, generic_name, product_strength, strength_unit, dosage_form) "
            "VALUES ('med_2', 'Metformin', 'Metformin HCL', '500', 'mg', 'Viên bao phim')",
        )
        conn.execute(
            "INSERT INTO prescriptions (prescription_id, patient_id, entered_by_user_id, prescriber_clinician_id, prescribed_date, valid_from, status) "
            "VALUES ('rx_1', 'p_elder_1', 'u_doc_1', 'c_doc_1', ?, ?, 'ACTIVE')",
            (now_iso, now_iso),
        )
        conn.execute(
            "INSERT INTO prescription_items (prescription_item_id, prescription_id, medication_id, dose_amount, dose_unit, original_instructions, start_date, status) "
            "VALUES ('pi_1', 'rx_1', 'med_1', 1.0, 'viên', 'Uống sau bữa ăn sáng', ?, 'ACTIVE')",
            (now_iso,),
        )
        conn.execute(
            "INSERT INTO prescription_items (prescription_item_id, prescription_id, medication_id, dose_amount, dose_unit, original_instructions, start_date, status) "
            "VALUES ('pi_2', 'rx_1', 'med_2', 1.0, 'viên', 'Uống sau bữa ăn trưa', ?, 'ACTIVE')",
            (now_iso,),
        )
        conn.execute(
            "INSERT INTO medication_schedules (schedule_id, prescription_item_id, revision, local_times, days_of_week, effective_from) "
            "VALUES ('sch_1', 'pi_1', 1, '[\"08:00\"]', '[0,1,2,3,4,5,6]', ?)",
            (now_iso,),
        )
        conn.execute(
            "INSERT INTO medication_schedules (schedule_id, prescription_item_id, revision, local_times, days_of_week, effective_from) "
            "VALUES ('sch_2', 'pi_2', 1, '[\"12:00\"]', '[0,1,2,3,4,5,6]', ?)",
            (now_iso,),
        )
        conn.commit()

    return db


class TestFlow1DoctorAssistance:
    def test_assistance_flow_with_db(self, in_memory_db):
        orchestrator = WorkflowOrchestrator(db=in_memory_db)
        auth_context = {"patient_id": "p_elder_1"}

        result = orchestrator.process_voice_interaction(
            auth_context=auth_context,
            transcript="Gọi bác sĩ cho tôi",
        )

        assert result.overall_status == ExecutionStatus.COMPLETED
        assert len(result.intent_results) == 1
        res = result.intent_results[0]
        assert res.intent_type == IntentType.DOCTOR_ASSISTANCE
        assert res.priority == IntentPriority.P1
        assert res.client_action.action_type == "assistance.alert"
        assert "BS. Trần Văn Minh" in res.message_to_elder or "bác sĩ trực" in res.message_to_elder

        # Verify outbox event in SQLite
        with in_memory_db.connect() as conn:
            outbox = conn.fetchone(
                "SELECT * FROM outbox_events WHERE patient_id = ? AND event_type = 'assistance.requested'",
                ("p_elder_1",),
            )
            assert outbox is not None
            payload = json.loads(outbox["payload_minimal"])
            assert payload["status"] == "PENDING_ACK"
            assert payload["patient_id"] == "p_elder_1"


class TestFlow2MedicationConfirmation:
    def test_single_matching_dose_confirmation(self, in_memory_db):
        # Insert a due occurrence for Amlodipine
        now_iso = datetime.now(UTC).isoformat()
        with in_memory_db.connect() as conn:
            conn.execute(
                """
                INSERT INTO dose_occurrences (
                    occurrence_id, schedule_id, revision, scheduled_at_utc, local_scheduled_time,
                    grace_deadline_utc, state, medication_name_snapshot, dose_snapshot,
                    instructions_snapshot, patient_id
                ) VALUES (
                    'occ_amlodipine_01', 'sch_1', 1, ?, '08:00', ?, 'PENDING',
                    'Amlodipine 5mg', '1 viên', 'Uống sau bữa ăn sáng', 'p_elder_1'
                )
                """,
                (now_iso, now_iso),
            )
            conn.commit()

        orchestrator = WorkflowOrchestrator(db=in_memory_db)
        auth_context = {"patient_id": "p_elder_1"}

        result = orchestrator.process_voice_interaction(
            auth_context=auth_context,
            transcript="Tôi đã uống thuốc rồi",
        )

        assert result.overall_status == ExecutionStatus.COMPLETED
        res = result.intent_results[0]
        assert res.intent_type == IntentType.MEDICATION_CONFIRM
        assert res.status == ExecutionStatus.COMPLETED
        assert "Amlodipine" in res.message_to_elder

        # Verify database state
        with in_memory_db.connect() as conn:
            occ = conn.fetchone(
                "SELECT * FROM dose_occurrences WHERE occurrence_id = 'occ_amlodipine_01'"
            )
            assert occ["state"] == "CONFIRMED"
            assert occ["action_actor"] == "voice_user_declared"

            # Check audit event
            event = conn.fetchone(
                "SELECT * FROM dose_events WHERE occurrence_id = 'occ_amlodipine_01'"
            )
            assert event is not None
            assert event["event_type"] == "self_reported_taken"
            meta = json.loads(event["metadata"])
            assert meta["source"] == "voice_user_declared"

            # Check outbox
            outbox = conn.fetchone(
                "SELECT * FROM outbox_events WHERE aggregate_id = 'occ_amlodipine_01' AND event_type = 'medication.dose_changed'"
            )
            assert outbox is not None

    def test_disambiguation_when_multiple_due_occurrences(self, in_memory_db):
        # Insert two due occurrences at the same time
        now_iso = datetime.now(UTC).isoformat()
        with in_memory_db.connect() as conn:
            conn.execute(
                """
                INSERT INTO dose_occurrences (
                    occurrence_id, schedule_id, revision, scheduled_at_utc, local_scheduled_time,
                    grace_deadline_utc, state, medication_name_snapshot, dose_snapshot,
                    instructions_snapshot, patient_id
                ) VALUES 
                ('occ_med_a', 'sch_1', 1, ?, '08:00', ?, 'PENDING', 'Amlodipine 5mg', '1 viên', 'Uống sau ăn sáng', 'p_elder_1'),
                ('occ_med_b', 'sch_2', 1, ?, '08:00', ?, 'PENDING', 'Metformin 500mg', '1 viên', 'Uống sau ăn sáng', 'p_elder_1')
                """,
                (now_iso, now_iso, now_iso, now_iso),
            )
            conn.commit()

        orchestrator = WorkflowOrchestrator(db=in_memory_db)
        auth_context = {"patient_id": "p_elder_1"}

        result = orchestrator.process_voice_interaction(
            auth_context=auth_context,
            transcript="Tôi đã uống thuốc rồi",
        )

        # Must require clarification/disambiguation without writing updates
        assert result.overall_status == ExecutionStatus.NEEDS_CLARIFICATION
        assert "Amlodipine" in result.response_speech_text
        assert "Metformin" in result.response_speech_text

        # Verify neither occurrence was marked CONFIRMED
        with in_memory_db.connect() as conn:
            occ_a = conn.fetchone("SELECT state FROM dose_occurrences WHERE occurrence_id = 'occ_med_a'")
            occ_b = conn.fetchone("SELECT state FROM dose_occurrences WHERE occurrence_id = 'occ_med_b'")
            assert occ_a["state"] == "PENDING"
            assert occ_b["state"] == "PENDING"

    def test_specific_medication_mention_resolves_disambiguation(self, in_memory_db):
        now_iso = datetime.now(UTC).isoformat()
        with in_memory_db.connect() as conn:
            conn.execute(
                """
                INSERT INTO dose_occurrences (
                    occurrence_id, schedule_id, revision, scheduled_at_utc, local_scheduled_time,
                    grace_deadline_utc, state, medication_name_snapshot, dose_snapshot,
                    instructions_snapshot, patient_id
                ) VALUES 
                ('occ_med_a', 'sch_1', 1, ?, '08:00', ?, 'PENDING', 'Amlodipine 5mg', '1 viên', 'Uống sau ăn sáng', 'p_elder_1'),
                ('occ_med_b', 'sch_2', 1, ?, '08:00', ?, 'PENDING', 'Metformin 500mg', '1 viên', 'Uống sau ăn sáng', 'p_elder_1')
                """,
                (now_iso, now_iso, now_iso, now_iso),
            )
            conn.commit()

        adapter = MedicationAdapter(db=in_memory_db)
        intent = VoiceIntent(
            type=IntentType.MEDICATION_CONFIRM,
            entities={"medication_ref": "Amlodipine", "status": "taken"},
            evidence_span="uống thuốc Amlodipine rồi",
        )
        res = adapter.handle_confirmation({"patient_id": "p_elder_1"}, intent)

        assert res["status"] == "CONFIRMED"
        assert res["occurrence_id"] == "occ_med_a"
        assert res["medication_name"] == "Amlodipine 5mg"


class TestFlow3MedicationGuidance:
    def test_approved_guidance_for_existing_prescription(self, in_memory_db):
        adapter = MedicationAdapter(db=in_memory_db)
        intent = VoiceIntent(
            type=IntentType.MEDICATION_GUIDANCE,
            entities={"medication_ref": "Amlodipine"},
            evidence_span="uống thuốc Amlodipine như thế nào",
        )

        res = adapter.handle_guidance({"patient_id": "p_elder_1"}, intent)
        assert res["status"] == "APPROVED_GUIDANCE"
        assert "Amlodipine" in res["medication_name"]
        assert "1.0 viên" in res["dose_info"]
        assert "Uống sau bữa ăn sáng" in res["instructions"]

    def test_negative_guidance_unknown_drug_safely_rejected(self, in_memory_db):
        adapter = MedicationAdapter(db=in_memory_db)
        intent = VoiceIntent(
            type=IntentType.MEDICATION_GUIDANCE,
            entities={"medication_ref": "Thuốc ngủ lạ"},
            evidence_span="uống thuốc ngủ lạ",
        )

        res = adapter.handle_guidance({"patient_id": "p_elder_1"}, intent)
        assert res["status"] == "MEDICATION_NOT_FOUND"
        assert "hỏi lại bác sĩ" in res["message"]


class TestFlow4HealthReport:
    def test_health_symptom_logged_and_outbox_emitted(self, in_memory_db):
        orchestrator = WorkflowOrchestrator(db=in_memory_db)
        auth_context = {"patient_id": "p_elder_1"}

        result = orchestrator.process_voice_interaction(
            auth_context=auth_context,
            transcript="Sáng nay tôi thấy chóng mặt và đau đầu",
        )

        assert result.overall_status == ExecutionStatus.COMPLETED
        res = result.intent_results[0]
        assert res.intent_type == IntentType.HEALTH_REPORT
        assert res.status == ExecutionStatus.COMPLETED
        assert "sổ theo dõi" in res.message_to_elder

        # Verify SQLite symptom_entries and outbox_events
        with in_memory_db.connect() as conn:
            entry = conn.fetchone("SELECT * FROM symptom_entries WHERE patient_id = 'p_elder_1'")
            assert entry is not None
            assert entry["status"] == "active"
            assert "chóng mặt" in entry["text"]

            outbox = conn.fetchone("SELECT * FROM outbox_events WHERE event_type = 'health.symptom_reported'")
            assert outbox is not None


class TestFlow5And6GamesAndMusic:
    def test_game_launch_adapter(self):
        adapter = MediaAndGamesAdapter()
        intent = VoiceIntent(
            type=IntentType.GAME_LAUNCH,
            entities={"game_type": "sequence_recall"},
            evidence_span="chơi trò ghi nhớ",
        )
        res = adapter.handle_game({}, intent)
        assert res["status"] == "COMPLETED"
        assert res["game_type"] == "sequence_recall"
        assert res["action"]["action_type"] == "game.open"

    def test_music_play_adapter(self):
        adapter = MediaAndGamesAdapter()
        intent = VoiceIntent(
            type=IntentType.MUSIC_PLAY,
            entities={"playlist": "nhạc quan họ"},
            evidence_span="bật nhạc quan họ",
        )
        res = adapter.handle_music({}, intent)
        assert res["status"] == "COMPLETED"
        assert res["action_type"] == "media.play"
        assert res["action"]["payload"]["playlist"] == "nhạc quan họ"
