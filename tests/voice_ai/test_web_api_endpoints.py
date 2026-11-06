"""
Tests for Voice AI REST API Endpoints mounted in features/web_portal/api_router.py.
Validates Section 5 and Phase 4-6 of VOICE_AI_IMPLEMENTATION_PLAN.md:
- POST /api/v1/voice/interactions (transcript, audio_base64, idempotency, auth)
- GET /api/v1/voice/interactions/{interaction_id} (status, intents, authorization)
- POST /api/v1/voice/assistance/{request_id}/acknowledge (doctor ack & outbox)
- GET /api/v1/voice/sessions/{session_id} (session state)
- POST /api/v1/voice/interactions/{interaction_id}/confirm & /cancel
"""

from __future__ import annotations

import base64
import json
import unittest
from typing import Any, Dict

from shared.database.connection import UnifiedDatabaseManager
from shared.database.migrator import Migrator
from shared.database.importer import DataImporter
from features.web_portal.auth import AuthService
from features.web_portal.api_router import ApiRouter
from features.voice_assistant.voice_ai.semantic.mock_provider import MockSemanticProvider
from features.voice_assistant.voice_ai.asr.mock_provider import MockASRProvider
from features.voice_assistant.voice_ai.orchestrator.router import WorkflowOrchestrator


class TestVoiceAIWebApiEndpoints(unittest.TestCase):
    def setUp(self) -> None:
        self.db = UnifiedDatabaseManager(":memory:")
        Migrator(self.db).apply_all()
        DataImporter(self.db).import_all()
        self.auth = AuthService(self.db)

        # Mock providers for deterministic testing
        self.mock_semantic = MockSemanticProvider()
        self.mock_asr = MockASRProvider()
        self.orchestrator = WorkflowOrchestrator(
            db=self.db,
            semantic_provider=self.mock_semantic,
        )

        self.router = ApiRouter(
            self.db,
            self.auth,
            orchestrator=self.orchestrator,
            asr_provider=self.mock_asr,
        )

        # Create tokens for test actors
        self.pat_token = self.auth.create_session({
            "user_id": "user_pat_001",
            "email": "hoanganh.patient@example.com",
            "full_name": "Cụ Hoàng Anh",
            "role": "patient",
        })
        self.cg_token = self.auth.create_session({
            "user_id": "user_care_001",
            "email": "tuan.caregiver@example.com",
            "full_name": "Nguyễn Văn Tuấn",
            "role": "caregiver",
        })
        self.doc_token = self.auth.create_session({
            "user_id": "user_doc_001",
            "email": "dr.an@laokhoa.example.com",
            "full_name": "BS. Nguyễn Văn An",
            "role": "clinician",
        })

    def test_unauthenticated_request_returns_401(self) -> None:
        status, res = self.router.handle_request(
            "POST",
            "/api/v1/voice/interactions",
            {},
            {"transcript": "Tôi đã uống thuốc rồi", "patient_id": "patient_001"},
            token=None,
        )
        self.assertEqual(status, 401)
        self.assertIn("Chưa đăng nhập", res.get("error", ""))

    def test_missing_transcript_and_audio_returns_400(self) -> None:
        status, res = self.router.handle_request(
            "POST",
            "/api/v1/voice/interactions",
            {},
            {"patient_id": "patient_001"},
            token=self.pat_token,
        )
        self.assertEqual(status, 400)
        self.assertIn("transcript", res.get("error", ""))

    def test_unauthorized_patient_access_returns_403(self) -> None:
        # Caregiver user_care_002 trying to access patient_001 (not in their care circle)
        unauth_cg_token = self.auth.create_session({
            "user_id": "user_care_002",
            "email": "lan.caregiver@example.com",
            "full_name": "Lê Thị Lan",
            "role": "caregiver",
        })
        status, res = self.router.handle_request(
            "POST",
            "/api/v1/voice/interactions",
            {},
            {"transcript": "Tôi đã uống thuốc rồi", "patient_id": "patient_001"},
            token=unauth_cg_token,
        )
        self.assertEqual(status, 403)
        self.assertIn("Không có quyền", res.get("error", ""))

    def test_post_voice_interaction_with_transcript_success(self) -> None:
        from datetime import datetime, timezone
        now_iso = datetime.now(timezone.utc).isoformat()
        with self.db.connect() as conn:
            conn.execute(
                """
                INSERT INTO dose_occurrences (
                    occurrence_id, schedule_id, revision, scheduled_at_utc, local_scheduled_time,
                    grace_deadline_utc, state, medication_name_snapshot, dose_snapshot,
                    instructions_snapshot, patient_id
                ) VALUES (
                    'occ_amlodipine_01', 'sch_test_01', 1, ?, '08:00', ?, 'PENDING',
                    'Amlodipine 5mg', '1 viên', 'Uống sau ăn sáng', 'patient_001'
                )
                """,
                (now_iso, now_iso),
            )
            conn.commit()

        # 1. Specific medication confirmation -> COMPLETED
        status, res = self.router.handle_request(
            "POST",
            "/api/v1/voice/interactions",
            {},
            {"transcript": "Tôi đã uống thuốc Amlodipin rồi", "patient_id": "patient_001"},
            token=self.pat_token,
        )
        self.assertEqual(status, 200)
        self.assertIn("interaction_id", res)
        self.assertEqual(res["patient_id"], "patient_001")
        self.assertEqual(res["overall_status"], "COMPLETED")
        self.assertGreater(len(res["intents"]), 0)
        self.assertEqual(res["intents"][0]["intent_type"], "MEDICATION_CONFIRM")
        self.assertEqual(res["intents"][0]["aggregate_id"], "occ_amlodipine_01")

        # 2. No matching unconfirmed dose left -> NO_MATCH / NEEDS_CLARIFICATION
        status_amb, res_amb = self.router.handle_request(
            "POST",
            "/api/v1/voice/interactions",
            {},
            {"transcript": "Tôi đã uống thuốc rồi", "patient_id": "patient_001"},
            token=self.pat_token,
        )
        self.assertEqual(status_amb, 200)
        self.assertEqual(res_amb["overall_status"], "NEEDS_CLARIFICATION")

    def test_post_voice_interaction_with_audio_base64_rejected(self) -> None:
        # Mock 1-second WAV audio header
        dummy_wav = b"RIFF\x24\x00\x00\x00WAVEfmt \x10\x00\x00\x00\x01\x00\x01\x00\x80>\x00\x00\x00}\x00\x00\x02\x00\x10\x00data\x00\x00\x00\x00"
        b64_audio = base64.b64encode(dummy_wav).decode("ascii")

        status, res = self.router.handle_request(
            "POST",
            "/api/v1/voice/interactions",
            {},
            {"audio_base64": b64_audio, "patient_id": "patient_001"},
            token=self.pat_token,
        )
        self.assertEqual(status, 400)
        self.assertIn("Audio STT processing is disabled", res.get("error", ""))

    def test_idempotency_prevents_duplicate_processing(self) -> None:
        idem_key = "idem-web-test-key-101"
        payload = {"transcript": "Tôi đã uống thuốc rồi", "patient_id": "patient_001"}

        status1, res1 = self.router.handle_request(
            "POST",
            "/api/v1/voice/interactions",
            {},
            payload,
            token=self.pat_token,
            headers={"Idempotency-Key": idem_key},
        )
        self.assertEqual(status1, 200)

        # Resend identical request with same idempotency key
        status2, res2 = self.router.handle_request(
            "POST",
            "/api/v1/voice/interactions",
            {},
            payload,
            token=self.pat_token,
            headers={"Idempotency-Key": idem_key},
        )
        self.assertEqual(status2, 200)
        self.assertEqual(res1["interaction_id"], res2["interaction_id"])

    def test_fast_path_urgent_doctor_assistance_and_outbox(self) -> None:
        status, res = self.router.handle_request(
            "POST",
            "/api/v1/voice/interactions",
            {},
            {"transcript": "Gọi bác sĩ ngay", "patient_id": "patient_001"},
            token=self.pat_token,
        )
        self.assertEqual(status, 200)
        self.assertTrue(res["safety_signal"]["is_urgent_assistance"])
        self.assertEqual(len(res["intents"]), 1)
        self.assertEqual(res["intents"][0]["intent_type"], "DOCTOR_ASSISTANCE")
        request_id = res["intents"][0]["aggregate_id"]
        self.assertTrue(request_id.startswith("ast_"))

        # Verify outbox event was atomically inserted
        with self.db.connect() as conn:
            outbox = conn.fetchone(
                "SELECT * FROM outbox_events WHERE aggregate_id = ? AND event_type = 'assistance.requested'",
                (request_id,),
            )
            self.assertIsNotNone(outbox)
            self.assertEqual(outbox["patient_id"], "patient_001")

    def test_get_voice_interaction_by_id(self) -> None:
        # Submit an interaction first
        _, submit_res = self.router.handle_request(
            "POST",
            "/api/v1/voice/interactions",
            {},
            {"transcript": "Tôi chóng mặt từ sáng", "patient_id": "patient_001"},
            token=self.pat_token,
        )
        interaction_id = submit_res["interaction_id"]

        # Fetch it via GET /api/v1/voice/interactions/{interaction_id}
        status, get_res = self.router.handle_request(
            "GET",
            f"/api/v1/voice/interactions/{interaction_id}",
            {},
            {},
            token=self.pat_token,
        )
        self.assertEqual(status, 200)
        self.assertEqual(get_res["interaction_id"], interaction_id)
        self.assertEqual(get_res["patient_id"], "patient_001")
        self.assertIn("transcript_redacted", get_res)

        # 404 for nonexistent interaction
        status_404, res_404 = self.router.handle_request(
            "GET",
            "/api/v1/voice/interactions/vint_nonexistent",
            {},
            {},
            token=self.pat_token,
        )
        self.assertEqual(status_404, 404)

        # 403 for unauthorized viewer
        unauth_cg_token = self.auth.create_session({
            "user_id": "user_care_002",
            "email": "lan.caregiver@example.com",
            "full_name": "Lê Thị Lan",
            "role": "caregiver",
        })
        status_403, _ = self.router.handle_request(
            "GET",
            f"/api/v1/voice/interactions/{interaction_id}",
            {},
            {},
            token=unauth_cg_token,
        )
        self.assertEqual(status_403, 403)

    def test_doctor_acknowledgement_lifecycle_and_security(self) -> None:
        # 1. Elder triggers urgent assistance
        _, submit_res = self.router.handle_request(
            "POST",
            "/api/v1/voice/interactions",
            {},
            {"transcript": "Gọi bác sĩ", "patient_id": "patient_001"},
            token=self.pat_token,
        )
        request_id = submit_res["intents"][0]["aggregate_id"]

        # 2. Caregiver tries to acknowledge -> Forbidden (must be clinician or admin)
        status_cg, res_cg = self.router.handle_request(
            "POST",
            f"/api/v1/voice/assistance/{request_id}/acknowledge",
            {},
            {"notes": "Caregiver cannot acknowledge as doctor"},
            token=self.cg_token,
        )
        self.assertEqual(status_cg, 403)

        # 3. Clinician acknowledges
        status_doc, res_doc = self.router.handle_request(
            "POST",
            f"/api/v1/voice/assistance/{request_id}/acknowledge",
            {},
            {"notes": "Bác sĩ An đang tới thăm khám"},
            token=self.doc_token,
        )
        self.assertEqual(status_doc, 200)
        self.assertEqual(res_doc["status"], "ACKNOWLEDGED")

        # 4. Verify outbox event 'assistance.acknowledged' was created
        with self.db.connect() as conn:
            ack_evt = conn.fetchone(
                "SELECT * FROM outbox_events WHERE aggregate_id = ? AND event_type = 'assistance.acknowledged'",
                (request_id,),
            )
            self.assertIsNotNone(ack_evt)
            self.assertEqual(ack_evt["patient_id"], "patient_001")

    def test_voice_sessions_get(self) -> None:
        # Create session by submitting an interaction with session_id
        session_id = "vses_custom_001"
        self.router.handle_request(
            "POST",
            "/api/v1/voice/interactions",
            {},
            {"transcript": "Mở trò lật thẻ", "patient_id": "patient_001", "session_id": session_id},
            token=self.pat_token,
        )

        status, session_res = self.router.handle_request(
            "GET",
            f"/api/v1/voice/sessions/{session_id}",
            {},
            {},
            token=self.pat_token,
        )
        self.assertEqual(status, 200)
        self.assertEqual(session_res["session_id"], session_id)
        self.assertEqual(session_res["patient_id"], "patient_001")

        # Nonexistent session
        status_404, _ = self.router.handle_request(
            "GET",
            "/api/v1/voice/sessions/vses_nonexistent",
            {},
            {},
            token=self.pat_token,
        )
        self.assertEqual(status_404, 404)

    def test_confirm_and_cancel_endpoints(self) -> None:
        _, sub = self.router.handle_request(
            "POST",
            "/api/v1/voice/interactions",
            {},
            {"transcript": "Mở trò lật thẻ", "patient_id": "patient_001"},
            token=self.pat_token,
        )
        iid = sub["interaction_id"]

        # Confirm
        status_c, res_c = self.router.handle_request(
            "POST",
            f"/api/v1/voice/interactions/{iid}/confirm",
            {},
            {},
            token=self.pat_token,
        )
        self.assertEqual(status_c, 200)
        self.assertEqual(res_c["status"], "CONFIRMED")

        # Cancel
        status_x, res_x = self.router.handle_request(
            "POST",
            f"/api/v1/voice/interactions/{iid}/cancel",
            {},
            {},
            token=self.pat_token,
        )
        self.assertEqual(status_x, 200)
        self.assertEqual(res_x["status"], "CANCELLED")


if __name__ == "__main__":
    unittest.main()
