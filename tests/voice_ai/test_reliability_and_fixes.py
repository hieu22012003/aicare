"""
Automated Reliability and Bug Fix Verification Tests for Voice AI Pipeline.
Covers all 6 observed runtime symptoms and acceptance criteria from Phase 0 to Phase 4:
1. Medication generic name deduplication & scheduled time context (no "Thuốc, Thuốc, Thuốc").
2. Medication non-matching name safety (does not confirm random doses).
3. Spacebar auto-repeat bounce suppression in VoiceButtonWidget.
4. AudioRecorder debounce protection and single active capture session.
5. TTS barge-in cancellation and acoustic feedback prevention.
6. Conversation state: Urgent request supersedes pending clarification.
7. Conversation state: Unrelated intent supersedes pending clarification.
8. Conversation state: Turn limit expiration prevents infinite prompt loop.
9. Observability: Structured tracing, latency tracking, PII & secret masking.
10. Gemini error & malformed response graceful degradation.
11. Idempotency prevents duplicate workflow actions.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
import time
from unittest.mock import MagicMock, patch

from PyQt5.QtCore import QEvent, Qt
from PyQt5.QtGui import QKeyEvent
from PyQt5.QtWidgets import QApplication
import pytest

from apps.desktop.voice_button_widget import VoiceButtonWidget
from shared.database.connection import UnifiedDatabaseManager
from shared.database.importer import DataImporter
from shared.database.migrator import Migrator
from features.voice_assistant.voice_ai.asr.provider import ASRProvider
from features.voice_assistant.voice_ai.audio.capture import AudioRecorder
from features.voice_assistant.voice_ai.audio.tts_player import TTSPlaybackCoordinator
from features.voice_assistant.voice_ai.business_adapters.medication_adapter import MedicationAdapter
from features.voice_assistant.voice_ai.domain.contracts import (
    ExecutionStatus,
    IntentPriority,
    IntentType,
    TranscriptResult,
    VoiceIntent,
)
from features.voice_assistant.voice_ai.observability.tracer import (
    mask_patient_id,
    mask_secret,
    mask_transcript_preview,
    start_trace,
)
from features.voice_assistant.voice_ai.orchestrator.router import WorkflowOrchestrator
from features.voice_assistant.voice_ai.orchestrator.state_machine import SessionState
from features.voice_assistant.voice_ai.semantic.gemini_provider import GeminiSemanticProvider


@pytest.fixture
def test_db():
    db = UnifiedDatabaseManager(":memory:")
    with open("shared/database/migrations/001_initial_schema.sql", encoding="utf-8") as f:
        sql1 = f.read()
    with open("shared/database/migrations/003_outbox_and_sync.sql", encoding="utf-8") as f:
        sql3 = f.read()

    with db.connect() as conn:
        conn.executescript(sql1)
        conn.executescript(sql3)

        now_iso = datetime.now(timezone.utc).isoformat()
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
            "INSERT INTO medication_schedules (schedule_id, prescription_item_id, revision, local_times, days_of_week, effective_from) "
            "VALUES ('sch_1', 'pi_1', 1, '[\"08:00\"]', '[0,1,2,3,4,5,6]', ?)",
            (now_iso,),
        )
        conn.commit()

    return db


# ---------------------------------------------------------------------------
# Symptom 1 & 2: Medication Disambiguation & Safe Matching
# ---------------------------------------------------------------------------

def test_medication_generic_names_deduplicated_and_timed(test_db):
    """Verifies that multiple doses named 'Thuốc' are formatted with schedule times, never 'Thuốc, Thuốc, Thuốc'."""
    now_iso = datetime.now(timezone.utc).isoformat()
    with test_db.connect() as conn:
        conn.execute(
            """
            INSERT INTO dose_occurrences (
                occurrence_id, schedule_id, revision, scheduled_at_utc, local_scheduled_time,
                grace_deadline_utc, state, medication_name_snapshot, dose_snapshot,
                instructions_snapshot, patient_id
            ) VALUES 
            ('occ_1', 'sch_1', 1, '2026-10-09T01:00:00Z', '08:00', '2026-10-09T02:00:00Z', 'PENDING', 'Thuốc', '1 viên', 'Sau ăn sáng', 'p_elder_1'),
            ('occ_2', 'sch_1', 1, '2026-10-09T05:00:00Z', '12:00', '2026-10-09T06:00:00Z', 'PENDING', 'Thuốc', '1 viên', 'Sau ăn trưa', 'p_elder_1'),
            ('occ_3', 'sch_1', 1, '2026-10-09T11:00:00Z', '18:00', '2026-10-09T12:00:00Z', 'PENDING', 'Thuốc', '1 viên', 'Sau ăn tối', 'p_elder_1')
            """
        )
        conn.commit()


    adapter = MedicationAdapter(db=test_db)
    intent = VoiceIntent(
        type=IntentType.MEDICATION_CONFIRM,
        entities={},  # No specific drug name given
        evidence_span="tôi đã uống thuốc rồi",
    )
    result = adapter.handle_confirmation({"patient_id": "p_elder_1"}, intent)

    assert result["status"] == "NEEDS_DISAMBIGUATION"
    assert result["requires_clarification"] is True
    prompt = result["clarification_prompt"]

    # Crucial assertion: Must NOT repeat generic "Thuốc, Thuốc, Thuốc"
    assert "Thuốc, Thuốc, Thuốc" not in prompt
    assert "08:00" in prompt
    assert "12:00" in prompt
    assert "18:00" in prompt


def test_medication_non_matching_name_does_not_confirm_random_dose(test_db):
    """Verifies that speaking an unknown drug name returns NO_MATCH and never confirms an unrelated scheduled dose."""
    now_iso = datetime.now(timezone.utc).isoformat()
    with test_db.connect() as conn:
        conn.execute(
            """
            INSERT INTO dose_occurrences (
                occurrence_id, schedule_id, revision, scheduled_at_utc, local_scheduled_time,
                grace_deadline_utc, state, medication_name_snapshot, dose_snapshot,
                instructions_snapshot, patient_id
            ) VALUES 
            ('occ_bp', 'sch_1', 1, ?, '08:00', ?, 'PENDING', 'Thuốc huyết áp Amlodipine', '1 viên 5mg', 'Sáng', 'p_elder_1')
            """,
            (now_iso, now_iso),
        )
        conn.commit()


    adapter = MedicationAdapter(db=test_db)
    # Elder claims they took Panadol, which is NOT in their prescription
    intent = VoiceIntent(
        type=IntentType.MEDICATION_CONFIRM,
        entities={"medication_ref": "panadol"},
        evidence_span="tôi đã uống panadol",
    )
    result = adapter.handle_confirmation({"patient_id": "p_elder_1"}, intent)

    assert result["status"] == "NO_MATCH"
    assert result["requires_clarification"] is True
    assert "panadol" in result["message"].lower()

    # Verify that the actual blood pressure medication in DB remained PENDING (NOT confirmed)
    with test_db.connect() as conn:
        row = conn.fetchone("SELECT state FROM dose_occurrences WHERE occurrence_id = 'occ_bp'")
        assert row["state"] == "PENDING"


# ---------------------------------------------------------------------------
# Symptom 3 & 4: Audio Lifecycle & Spacebar Auto-Repeat Suppression
# ---------------------------------------------------------------------------

def test_spacebar_auto_repeat_suppressed_in_voice_button():
    """Verifies that OS keyboard auto-repeat does not cause rapid start/stop toggling."""
    app = QApplication.instance() or QApplication([])
    widget = VoiceButtonWidget()

    # Mock recorder
    widget.recorder.start_recording = MagicMock(return_value=True)
    widget.recorder.stop_recording = MagicMock(return_value=b"RIFF...")

    # 1. First physical key press (not auto-repeat)
    event_press = QKeyEvent(QEvent.KeyPress, Qt.Key_Space, Qt.NoModifier)
    assert not event_press.isAutoRepeat()
    filtered = widget.eventFilter(widget.btn_ptt, event_press)
    assert filtered is True
    assert widget.recorder.start_recording.call_count == 1

    # 2. Simulated OS keyboard auto-repeats while key is held down
    event_repeat_press = QKeyEvent(QEvent.KeyPress, Qt.Key_Space, Qt.NoModifier, " ", True, 1)
    event_repeat_release = QKeyEvent(QEvent.KeyRelease, Qt.Key_Space, Qt.NoModifier, " ", True, 1)

    for _ in range(5):
        filtered_repeat_p = widget.eventFilter(widget.btn_ptt, event_repeat_press)
        filtered_repeat_r = widget.eventFilter(widget.btn_ptt, event_repeat_release)
        assert filtered_repeat_p is True
        assert filtered_repeat_r is True

    # Crucial assertion: start_recording must NOT have been called again during auto-repeat!
    assert widget.recorder.start_recording.call_count == 1
    assert widget.recorder.stop_recording.call_count == 0


def test_audio_recorder_debounce_protection():
    """Verifies that rapid start_recording within 150ms of stop is rejected."""
    recorder = AudioRecorder()

    assert recorder.start_recording() is True
    recorder.feed_pcm_chunk(b"\x00" * 3200)
    wav = recorder.stop_recording()
    assert wav is not None

    # Immediate second start (< 150ms) must be rejected by debounce
    assert recorder.start_recording() is False

    # After debounce window expires, start_recording succeeds
    time.sleep(0.16)
    assert recorder.start_recording() is True
    recorder.stop_recording()


# ---------------------------------------------------------------------------
# Symptom 6: TTS Barge-In & Acoustic Feedback Prevention
# ---------------------------------------------------------------------------

def test_tts_barge_in_stops_playback_on_recording_start():
    """Verifies that initiating recording immediately stops active TTS playback to prevent feedback."""
    coordinator = TTSPlaybackCoordinator.get_instance()

    mock_process = MagicMock()
    mock_process.poll.return_value = None  # Process is running
    coordinator._current_process = mock_process

    assert coordinator.is_speaking() is True

    # Starting audio recording triggers barge-in
    recorder = AudioRecorder()
    recorder.start_recording()

    # Active TTS process must be killed
    assert mock_process.kill.called
    assert coordinator._current_process is None
    recorder.stop_recording()


# ---------------------------------------------------------------------------
# Conversation State Transitions & Supersede Handling
# ---------------------------------------------------------------------------

def test_conversation_state_clarification_superseded_by_urgent(test_db):
    """Verifies that an urgent assistance request immediately supersedes a pending medication clarification."""
    orchestrator = WorkflowOrchestrator(db=test_db)
    session = orchestrator.get_or_create_session("p_elder_1", "vses_urgent_test")

    # Set session into AWAITING_CLARIFICATION state
    session.set_awaiting_clarification(
        intent=VoiceIntent(type=IntentType.MEDICATION_CONFIRM, entities={}),
        question="Dạ cụ vừa uống thuốc nào ạ?",
    )
    assert session.pending_clarification is True

    # User speaks urgent emergency phrase
    res = orchestrator.process_voice_interaction(
        auth_context={"patient_id": "p_elder_1"},
        transcript="Cứu tôi với, gọi bác sĩ ngay",
        session_id="vses_urgent_test",
    )

    # Crucial assertion: Must execute DOCTOR_ASSISTANCE, NOT ask about medication
    assert any(ir.intent_type == IntentType.DOCTOR_ASSISTANCE for ir in res.intent_results)
    assert res.overall_status == ExecutionStatus.COMPLETED
    assert session.context.state == SessionState.COMPLETED
    assert "bác sĩ" in res.response_speech_text.lower()


def test_conversation_state_clarification_superseded_by_unrelated_intent(test_db):
    """Verifies that an elder changing the topic (e.g. asking for music) cancels the pending clarification."""
    orchestrator = WorkflowOrchestrator(db=test_db)
    session = orchestrator.get_or_create_session("p_elder_1", "vses_music_test")

    session.set_awaiting_clarification(
        intent=VoiceIntent(type=IntentType.MEDICATION_CONFIRM, entities={}),
        question="Dạ cụ vừa uống thuốc nào ạ?",
    )
    assert session.pending_clarification is True

    # User speaks an entertainment intent
    res = orchestrator.process_voice_interaction(
        auth_context={"patient_id": "p_elder_1"},
        transcript="Bật nhạc thư giãn cho tôi",
        session_id="vses_music_test",
    )

    assert any(ir.intent_type == IntentType.MUSIC_PLAY for ir in res.intent_results)
    assert "nhạc" in res.response_speech_text.lower()
    assert session.pending_clarification is False


def test_conversation_state_turn_limit_prevents_infinite_clarification_loop(test_db):
    """Verifies that after 2 turns without resolution, the system gracefully closes the clarification prompt."""
    orchestrator = WorkflowOrchestrator(db=test_db)
    session = orchestrator.get_or_create_session("p_elder_1", "vses_turn_limit_test")

    session.set_awaiting_clarification(
        intent=VoiceIntent(type=IntentType.MEDICATION_CONFIRM, entities={}),
        question="Dạ cụ vừa uống thuốc nào ạ?",
    )
    session.context.turn_count = 2  # Already asked twice

    res = orchestrator.process_voice_interaction(
        auth_context={"patient_id": "p_elder_1"},
        transcript="ờ ờ...",  # Unclear input
        session_id="vses_turn_limit_test",
    )

    # Must NOT ask "Dạ cụ vừa uống thuốc nào ạ?" again
    assert "Dạ cụ vừa uống thuốc nào ạ?" not in res.response_speech_text
    assert "tạm gác lại" in res.response_speech_text
    assert res.overall_status == ExecutionStatus.COMPLETED
    assert session.context.state == SessionState.IDLE


# ---------------------------------------------------------------------------
# Symptom 5: Observability & Masking
# ---------------------------------------------------------------------------

def test_structured_observability_tracing_emits_masked_fields(test_db):
    """Verifies that structured tracing logs latencies, masked IDs, and zero API keys."""
    orchestrator = WorkflowOrchestrator(db=test_db)
    res = orchestrator.process_voice_interaction(
        auth_context={"patient_id": "p_elder_1"},
        transcript="Mở trò chơi lật thẻ",
    )

    assert res.trace_summary is not None
    assert "total_e2e_ms" in res.trace_summary["latencies_ms"]
    assert res.trace_summary["patient_id"] == "p_***_1"
    assert res.trace_summary["status"] == "COMPLETED"

    # Verify secret masking helpers
    dirty_log = "Error calling Gemini with key=AIzaSyA1B2C3D4E5F6G7H8I9J0 and token=secret123"
    cleaned = mask_secret(dirty_log)
    assert "AIzaSy" not in cleaned
    assert "REDACTED" in cleaned
    assert "secret123" not in cleaned


# ---------------------------------------------------------------------------
# Gemini Fallback & Malformed Response Handling
# ---------------------------------------------------------------------------

def test_gemini_malformed_response_graceful_fallback():
    """Verifies that invalid Gemini JSON returns safe UNKNOWN fallback without crashing."""
    provider = GeminiSemanticProvider(api_key="test-mock-key")

    with patch("requests.post") as mock_post:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "candidates": [{"content": {"parts": [{"text": "this is not json at all {"}]}}]
        }
        mock_post.return_value = mock_resp

        result = provider.analyze("uống thuốc rồi")
        assert len(result.intents) == 1
        assert result.intents[0].type == IntentType.UNKNOWN
        assert result.intents[0].requires_clarification is True


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------

def test_idempotency_prevents_duplicate_medication_intake(test_db):
    """Verifies that duplicate requests with the same idempotency key do not double-confirm or re-execute DB updates."""
    now_iso = datetime.now(timezone.utc).isoformat()
    with test_db.connect() as conn:
        conn.execute(
            """
            INSERT INTO dose_occurrences (
                occurrence_id, schedule_id, revision, scheduled_at_utc, local_scheduled_time,
                grace_deadline_utc, state, medication_name_snapshot, dose_snapshot,
                instructions_snapshot, patient_id
            ) VALUES 
            ('occ_single', 'sch_1', 1, ?, '08:00', ?, 'PENDING', 'Thuốc Metformin', '1 viên 500mg', 'Sáng', 'p_elder_1')
            """,
            (now_iso, now_iso),
        )
        conn.commit()


    orchestrator = WorkflowOrchestrator(db=test_db)
    key = "idemp_test_duplicate_key_123"

    res1 = orchestrator.process_voice_interaction(
        auth_context={"patient_id": "p_elder_1"},
        transcript="Tôi đã uống thuốc rồi",
        idempotency_key=key,
    )
    assert res1.overall_status == ExecutionStatus.COMPLETED

    # Second call with the same idempotency key
    res2 = orchestrator.process_voice_interaction(
        auth_context={"patient_id": "p_elder_1"},
        transcript="Tôi đã uống thuốc rồi",
        idempotency_key=key,
    )
    assert res2.interaction_id == res1.interaction_id

    # Verify dose_events table has only 1 record
    with test_db.connect() as conn:
        events = conn.fetchall("SELECT * FROM dose_events WHERE occurrence_id = 'occ_single'")
        assert len(events) == 1
