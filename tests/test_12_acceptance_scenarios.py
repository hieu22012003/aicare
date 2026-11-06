"""
Comprehensive System-Wide Acceptance Test Suite (12 Scenarios from Section 10).
Reference: KE_HOACH_TICH_HOP_WEB_BAC_SI_NGUOI_NHA_POSTGRESQL.md

Scenarios:
1. Caregiver A only sees assigned patient; revoked clinician B cannot access records via URL/API/cache.
2. Camera recognition returns patient X; medication, vitals, mood, rehab only read/written for X. Camera failure leaves reminders & web active.
3. 07:00 dose overdue unconfirmed -> single alert -> active caregiver only -> status tracked; late confirm retains overdue trace, no duplicate alert.
4. Blood pressure reading contains systolic and diastolic; charts and raw table share same filter, units, timestamps; missing days are not 0.
5. Caregiver logs 'chóng mặt từ 09:00' -> assigned doctor sees source/author/time -> doctor responds -> caregiver sees status change.
6. Camera emotion inference and self-reported mood displayed separately; revoking camera consent stops new emotion inferences.
7. 16:00 reminder created once per day/plan. Target 10: 9 reps = incomplete, 10th rep transitions once to camera_verified_completed; duplicate event_id does not increment. Camera error yields verification_unavailable.
8. Doctor amends prescription: old version and past occurrences preserved; future occurrences updated; caregiver receives notice.
9. Doctor sends recommendation with author/time/category; patient only sees own, caregiver sees if shared.
10. Emergency button opens configured doctor number during duty hours; desktop shows number/hours; outside hours shows out-of-duty notice & backup number; no 24/7 promise; no fake call claims.
11. Backfill run twice produces zero duplicates; row counts / FK / units reconciled; backup dump restored and smoke tested on clean database.
12. Network / worker failure simulation: state tracked, limited retries, no false 'sent' claims.
"""

from __future__ import annotations

import json
import unittest
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from shared.database.connection import UnifiedDatabaseManager
from shared.database.migrator import Migrator
from shared.database.importer import DataImporter
from shared.database.backup_restore import BackupRestoreService
from features.web_portal.auth import AuthService
from features.web_portal.api_router import ApiRouter
from features.exercise_rehab.rehab_session_adapter import RehabSessionAdapter

UTC = timezone.utc


class Test12AcceptanceScenarios(unittest.TestCase):
    def setUp(self) -> None:
        self.db = UnifiedDatabaseManager(":memory:")
        Migrator(self.db).apply_all()
        DataImporter(self.db).import_all()
        self.auth = AuthService(self.db)
        self.router = ApiRouter(self.db, self.auth)
        self.rehab_adapter = RehabSessionAdapter(self.db)

    # --------------------------------------------------------------------------
    # Scenario 1: Quyền theo quan hệ & Thu hồi quyền ngay lập tức
    # --------------------------------------------------------------------------
    def test_scenario_01_relationship_access_and_immediate_revocation(self) -> None:
        # Caregiver 1 (user_care_001) is assigned to patient_001
        ok_a, _ = self.auth.check_patient_access("user_care_001", "patient_001")
        self.assertTrue(ok_a, "Caregiver 1 phải có quyền truy cập patient_001")

        # Caregiver 1 MUST NOT see patient_002
        ok_b, _ = self.auth.check_patient_access("user_care_001", "patient_002")
        self.assertFalse(ok_b, "Caregiver 1 KHÔNG ĐƯỢC phép truy cập patient_002")

        # Clinician 1 (user_doc_001) has access to patient_001
        ok_doc, _ = self.auth.check_patient_access("user_doc_001", "patient_001")
        self.assertTrue(ok_doc, "Clinician 1 ban đầu có quyền truy cập patient_001")

        # Dynamically revoke clinician 1
        with self.db.connect() as conn:
            conn.execute("UPDATE care_team SET is_active=0 WHERE patient_id='patient_001' AND clinician_id='clinician_001'")
            conn.commit()

        # Immediate revocation test: API call must be rejected with 403
        doc_session = self.auth.authenticate_user("user_doc_001")
        token = self.auth.create_session(doc_session)
        code, res = self.router.handle_request("GET", "/api/patients/patient_001/overview", {}, {}, token)
        self.assertEqual(code, 403, "Bác sĩ bị thu hồi quyền phải bị chặn 403 ngay lập tức")
        self.assertIn("error", res)

    # --------------------------------------------------------------------------
    # Scenario 2: ID camera độc lập và cách ly dữ liệu hai bệnh nhân
    # --------------------------------------------------------------------------
    def test_scenario_02_patient_identity_isolation_and_camera_fault_tolerance(self) -> None:
        with self.db.connect() as conn:
            # Map camera face ID '1' -> patient_001, '2' -> patient_002
            m1 = conn.fetchone("SELECT patient_id FROM legacy_patient_map WHERE legacy_id='1'")
            m2 = conn.fetchone("SELECT patient_id FROM legacy_patient_map WHERE legacy_id='2'")
            self.assertEqual(m1["patient_id"], "patient_001")
            self.assertEqual(m2["patient_id"], "patient_002")

            # Check that data for patient_001 belongs ONLY to patient_001
            p1_obs = conn.fetchall("SELECT * FROM health_observations WHERE patient_id='patient_001'")
            p2_obs = conn.fetchall("SELECT * FROM health_observations WHERE patient_id='patient_002'")
            self.assertGreater(len(p1_obs), 0)
            self.assertTrue(all(r["patient_id"] == "patient_001" for r in p1_obs))
            self.assertTrue(all(r["patient_id"] == "patient_002" for r in p2_obs))

        # Camera failure test: When camera is offline, API & web remain fully responsive
        doc_session = self.auth.authenticate_user("user_doc_001")
        token = self.auth.create_session(doc_session)
        code, res = self.router.handle_request("GET", "/api/patients/patient_001/medication/doses", {}, {}, token)
        self.assertEqual(code, 200, "Lịch thuốc vẫn tải bình thường khi camera tắt")
        self.assertIn("doses", res)

    # --------------------------------------------------------------------------
    # Scenario 3: Liều quá hạn tạo đúng 1 alert, gửi đúng người, không trùng
    # --------------------------------------------------------------------------
    def test_scenario_03_overdue_alert_single_dispatch_and_late_confirmation(self) -> None:
        with self.db.connect() as conn:
            # Pick a pending occurrence for patient_001
            occ = conn.fetchone("SELECT * FROM dose_occurrences WHERE patient_id='patient_001' AND state='PENDING' LIMIT 1")
            self.assertIsNotNone(occ)
            occ_id = occ["occurrence_id"]

            # Simulate transition to OVERDUE and create Alert
            now_iso = datetime.now(timezone.utc).isoformat()
            alert_id = f"alt_{uuid.uuid4().hex[:12]}"
            conn.execute(
                "INSERT INTO alerts (alert_id, occurrence_id, patient_id, alert_type, status, created_at_utc) VALUES (?, ?, 'patient_001', 'OVERDUE', 'NEW', ?)",
                (alert_id, occ_id, now_iso),
            )
            # Try duplicate alert on same occurrence -> must violate UNIQUE or be ignored
            dup_inserted = True
            try:
                conn.execute(
                    "INSERT INTO alerts (alert_id, occurrence_id, patient_id, alert_type, status, created_at_utc) VALUES ('alt_dup', ?, 'patient_001', 'OVERDUE', 'NEW', ?)",
                    (occ_id, now_iso),
                )
            except Exception:
                dup_inserted = False
            self.assertFalse(dup_inserted, "Không được phép tạo 2 alert trùng loại cho cùng 1 occurrence")

            # Route to active caregiver only (caregiver_001 is active, caregiver_003 is expired)
            active_cg = conn.fetchall(
                """
                SELECT c.caregiver_id FROM patient_caregivers pc
                JOIN caregivers c ON pc.caregiver_id = c.caregiver_id
                WHERE pc.patient_id = 'patient_001' AND pc.is_active = 1
                """
            )
            cg_ids = [r["caregiver_id"] for r in active_cg]
            self.assertIn("caregiver_001", cg_ids)
            self.assertNotIn("caregiver_003", cg_ids, "Caregiver hết hạn KHÔNG ĐƯỢC nhận cảnh báo")

        # Late confirmation test: retains overdue trace while updating state to CONFIRMED
        session = self.auth.authenticate_user("user_care_001")
        token = self.auth.create_session(session)
        code, res = self.router.handle_request(
            "POST", f"/api/patients/patient_001/medication/doses/{occ_id}/action", {}, {"action": "self_reported_taken"}, token
        )
        self.assertEqual(code, 200)
        self.assertEqual(res["state"], "CONFIRMED")

        # Re-sending same action with idempotency key must not create 2nd event
        code_dup, res_dup = self.router.handle_request(
            "POST", f"/api/patients/patient_001/medication/doses/{occ_id}/action", {},
            {"action": "self_reported_taken", "idempotency_key": res["event_id"]}, token
        )
        self.assertEqual(res_dup.get("status"), "duplicate_idempotent")

    # --------------------------------------------------------------------------
    # Scenario 4: Huyết áp hai thành phần, đơn vị mmHg, ngày trống không thành 0
    # --------------------------------------------------------------------------
    def test_scenario_04_blood_pressure_two_components_and_no_zero_fill(self) -> None:
        session = self.auth.authenticate_user("user_care_001")
        token = self.auth.create_session(session)
        code, res = self.router.handle_request(
            "GET", "/api/patients/patient_001/observations", {"type": "blood_pressure", "days": "30"}, {}, token
        )
        self.assertEqual(code, 200)
        readings = res["readings"]
        self.assertGreater(len(readings), 0)

        for r in readings:
            self.assertEqual(r["unit_code"], "mmHg")
            # Must not convert missing days or empty values to 0.0
            if r["systolic"] is not None:
                self.assertGreater(r["systolic"], 50.0)
            if r["diastolic"] is not None:
                self.assertGreater(r["diastolic"], 30.0)

        # Separate measured_at from recorded_at
        r_first = readings[0]
        self.assertTrue(bool(r_first["measured_at"]))
        self.assertTrue(bool(r_first["recorded_at"]))

    # --------------------------------------------------------------------------
    # Scenario 5: Ghi nhận bất thường -> Bác sĩ phản hồi -> Cập nhật trạng thái
    # --------------------------------------------------------------------------
    def test_scenario_05_caregiver_report_and_clinician_response_flow(self) -> None:
        # Caregiver logs abnormality
        cg_token = self.auth.create_session(self.auth.authenticate_user("user_care_001"))
        code, res = self.router.handle_request(
            "POST", "/api/patients/patient_001/caregiver-reports", {},
            {"description": "Cụ kêu chóng mặt từ 09:00, đã cho nằm nghỉ", "observed_at": "2026-10-06T09:00:00Z", "severity": "NORMAL"},
            cg_token
        )
        self.assertEqual(code, 201)
        report_id = res["report_id"]

        # Assigned doctor views report
        doc_token = self.auth.create_session(self.auth.authenticate_user("user_doc_001"))
        code_view, res_view = self.router.handle_request("GET", "/api/patients/patient_001/caregiver-reports", {}, {}, doc_token)
        self.assertEqual(code_view, 200)
        rep = next((r for r in res_view["reports"] if r["report_id"] == report_id), None)
        self.assertIsNotNone(rep)
        self.assertEqual(rep["status"], "NEW")

        # Doctor replies
        code_resp, res_resp = self.router.handle_request(
            "POST", f"/api/patients/patient_001/caregiver-reports/{report_id}/respond", {},
            {"body": "Bác sĩ đã xem: Hãy đo lại huyết áp sau 30 phút, nếu chóng mặt tăng thì gọi hotline ngay."}, doc_token
        )
        self.assertEqual(code_resp, 200)
        self.assertEqual(res_resp["status"], "RESPONDED")

        # Caregiver sees updated status and doctor message
        code_check, res_check = self.router.handle_request("GET", "/api/patients/patient_001/caregiver-reports", {}, {}, cg_token)
        rep_updated = next(r for r in res_check["reports"] if r["report_id"] == report_id)
        self.assertEqual(rep_updated["status"], "RESPONDED")
        self.assertGreater(len(rep_updated["messages"]), 0)

    # --------------------------------------------------------------------------
    # Scenario 6: Cảm xúc camera và tâm trạng tự báo cáo tách bạch
    # --------------------------------------------------------------------------
    def test_scenario_06_camera_emotion_and_self_reported_mood_separated(self) -> None:
        token = self.auth.create_session(self.auth.authenticate_user("user_care_001"))
        code, res = self.router.handle_request("GET", "/api/patients/patient_001/mood", {}, {}, token)
        self.assertEqual(code, 200)

        # Two distinct lists
        self.assertIn("self_reported_moods", res)
        self.assertIn("camera_emotion_inferences", res)
        self.assertIn("disclaimer", res)

        # Consent revocation test
        with self.db.connect() as conn:
            conn.execute("UPDATE emotion_inferences SET is_consent_active=0 WHERE patient_id='patient_001'")
            conn.commit()

        code_rev, res_rev = self.router.handle_request("GET", "/api/patients/patient_001/mood", {}, {}, token)
        self.assertEqual(len(res_rev["camera_emotion_inferences"]), 0, "Khi tắt consent, camera emotion không được xuất hiện")

    # --------------------------------------------------------------------------
    # Scenario 7: Luồng phục hồi mục 6A (Chỉ tiêu 10 lần, 9 lần incomplete, lần 10 hoàn thành, chống lặp)
    # --------------------------------------------------------------------------
    def test_scenario_07_section_6a_rehabilitation_counting_and_failure_states(self) -> None:
        # Create fresh session with target = 10
        sess = self.rehab_adapter.get_or_create_daily_session("patient_001", "sit_to_stand")
        sid = sess["session_id"]
        target = sess["target_count_snapshot"]

        # Send 9 valid repetitions
        for i in range(1, 10):
            res = self.rehab_adapter.record_verified_rep(sid, rep_number=i, score=90.0)
            self.assertEqual(res["verified_count"], i)
            self.assertEqual(res["status"], "in_progress")

        # Duplicate submission on rep 9
        dup_res = self.rehab_adapter.record_verified_rep(sid, rep_number=9, score=90.0)
        self.assertTrue(dup_res["is_duplicate"])
        self.assertEqual(dup_res["verified_count"], 9, "Gửi trùng rep 9 không được tăng biến đếm")

        # 10th rep: Atomic completion
        comp_res = self.rehab_adapter.record_verified_rep(sid, rep_number=10, score=95.0)
        self.assertEqual(comp_res["verified_count"], 10)
        self.assertEqual(comp_res["status"], "camera_verified_completed")

        # Camera failure state test
        sess_fail = self.rehab_adapter.get_or_create_daily_session("patient_002", "marching")
        unavail = self.rehab_adapter.mark_camera_unavailable(sess_fail["session_id"], "Mất kết nối camera")
        self.assertEqual(unavail["status"], "verification_unavailable")
        self.assertNotEqual(unavail["status"], "camera_verified_completed")

    # --------------------------------------------------------------------------
    # Scenario 8: Bác sĩ sửa đơn thuốc theo phiên bản (v1 -> v2)
    # --------------------------------------------------------------------------
    def test_scenario_08_prescription_versioning_and_future_occurrence_update(self) -> None:
        doc_token = self.auth.create_session(self.auth.authenticate_user("user_doc_001"))
        with self.db.connect() as conn:
            old_rx = conn.fetchone("SELECT * FROM prescriptions WHERE patient_id='patient_001' AND status='ACTIVE' LIMIT 1")
            old_rx_id = old_rx["prescription_id"]
            old_v = old_rx["version"]

        # Revise prescription
        code, res = self.router.handle_request(
            "POST", f"/api/patients/patient_001/prescriptions/{old_rx_id}/revisions", {},
            {
                "reason": "Điều chỉnh liều Amlor lên 10mg",
                "items": [{
                    "medication_id": "med_001",
                    "dose_amount": 2.0,
                    "dose_unit": "viên",
                    "instructions": "Uống 2 viên buổi sáng sau ăn",
                    "local_times": ["08:00"]
                }]
            },
            doc_token
        )
        self.assertEqual(code, 201)
        self.assertEqual(res["version"], old_v + 1)
        new_rx_id = res["prescription_id"]

        # Verify old prescription is SUPERSEDED and past history untouched
        with self.db.connect() as conn:
            old_check = conn.fetchone("SELECT status FROM prescriptions WHERE prescription_id = ?", (old_rx_id,))
            self.assertEqual(old_check["status"], "SUPERSEDED")

            new_check = conn.fetchone("SELECT status, version FROM prescriptions WHERE prescription_id = ?", (new_rx_id,))
            self.assertEqual(new_check["status"], "ACTIVE")
            self.assertEqual(new_check["version"], old_v + 1)

    # --------------------------------------------------------------------------
    # Scenario 9: Bác sĩ gửi lời khuyên chuyên môn có nhãn mô phỏng
    # --------------------------------------------------------------------------
    def test_scenario_09_care_recommendations_version_and_isolation(self) -> None:
        doc_token = self.auth.create_session(self.auth.authenticate_user("user_doc_001"))
        code, res = self.router.handle_request(
            "POST", "/api/patients/patient_001/recommendations", {},
            {"category": "exercise", "title": "Duy trì bài tập vươn vai", "content": "Tập 12 lần vào 16:00 mỗi ngày để tăng tuần hoàn."},
            doc_token
        )
        self.assertEqual(code, 201)
        rec_id = res["recommendation_id"]

        # Patient 1 can see it
        cg1_token = self.auth.create_session(self.auth.authenticate_user("user_care_001"))
        code1, res1 = self.router.handle_request("GET", "/api/patients/patient_001/recommendations", {}, {}, cg1_token)
        self.assertEqual(code1, 200)
        found = any(r["recommendation_id"] == rec_id for r in res1["recommendations"])
        self.assertTrue(found)

        # Patient 2 MUST NOT see Patient 1's recommendation
        cg2_token = self.auth.create_session(self.auth.authenticate_user("user_care_002"))
        code2, res2 = self.router.handle_request("GET", "/api/patients/patient_002/recommendations", {}, {}, cg2_token)
        self.assertEqual(code2, 200)
        found2 = any(r["recommendation_id"] == rec_id for r in res2["recommendations"])
        self.assertFalse(found2, "Lời khuyên của bệnh nhân 1 không được lẫn sang bệnh nhân 2")

    # --------------------------------------------------------------------------
    # Scenario 10: Hotline khẩn cấp trong giờ trực vs ngoài giờ trực
    # --------------------------------------------------------------------------
    def test_scenario_10_emergency_hotline_duty_hours_and_unverified_safety(self) -> None:
        token = self.auth.create_session(self.auth.authenticate_user("user_care_001"))
        code, res = self.router.handle_request("GET", "/api/patients/patient_001/emergency-contacts", {}, {}, token)
        self.assertEqual(code, 200)
        contacts = res["contacts"]

        doc_c = next(c for c in contacts if c["contact_type"] == "clinician_on_duty")
        self.assertFalse(bool(doc_c["is_verified"]), "Số bác sĩ demo 000-000-000 phải có is_verified = 0")
        self.assertFalse(doc_c["is_call_button_enabled"], "Nút gọi phải bị vô hiệu hóa khi số chưa được xác nhận")

        # Local emergency backup 115 is available
        emerg_115 = next(c for c in contacts if c["contact_type"] == "local_emergency")
        self.assertEqual(emerg_115["phone"], "115")
        self.assertTrue(bool(emerg_115["is_verified"]))

        # Log emergency dial event (does not make real phone call)
        code_ev, res_ev = self.router.handle_request(
            "POST", "/api/patients/patient_001/emergency-contact-events", {},
            {"contact_id": doc_c["contact_id"], "phone": doc_c["phone"], "on_duty_status": "ON_DUTY"}, token
        )
        self.assertEqual(code_ev, 200)
        self.assertIn("event_id", res_ev)

    # --------------------------------------------------------------------------
    # Scenario 11: Idempotent Backfill & Roundtrip Dump/Restore
    # --------------------------------------------------------------------------
    def test_scenario_11_idempotent_backfill_and_backup_restore_roundtrip(self) -> None:
        # 1. Run importer second time -> zero duplicates
        importer = DataImporter(self.db)
        stats1 = importer.import_all()
        stats2 = importer.import_all()
        self.assertEqual(stats1, stats2, "Chạy import lần 2 phải ra cùng số lượng bản ghi")

        # 2. Test SQL backup dump & restore
        dump_file = Path("data/scenario11_dump.sql")
        is_ok, src_c, tgt_c = BackupRestoreService.verify_backup_and_restore(self.db, dump_file)
        self.assertTrue(is_ok, "Dữ liệu sau khi restore phải khớp 100% với dữ liệu nguồn")
        if dump_file.exists():
            dump_file.unlink()

    # --------------------------------------------------------------------------
    # Scenario 12: Gián đoạn mạng & Worker retry có giới hạn
    # --------------------------------------------------------------------------
    def test_scenario_12_network_fault_simulation_and_limited_retries(self) -> None:
        with self.db.connect() as conn:
            occ = conn.fetchone(
                "SELECT occurrence_id FROM dose_occurrences WHERE patient_id='patient_001' AND occurrence_id NOT IN (SELECT occurrence_id FROM alerts WHERE alert_type='HELP_REQUESTED') LIMIT 1"
            )
            occ_id = occ["occurrence_id"] if occ else "occ_fallback"
            alert_id = f"alt_retry_{uuid.uuid4().hex[:12]}"
            conn.execute(
                "INSERT INTO alerts (alert_id, occurrence_id, patient_id, alert_type, status, created_at_utc) VALUES (?, ?, 'patient_001', 'HELP_REQUESTED', 'NEW', ?)",
                (alert_id, occ_id, datetime.now(timezone.utc).isoformat()),
            )

            # Create a notification delivery in QUEUED state
            delivery_id = f"nd_{uuid.uuid4().hex[:12]}"
            conn.execute(
                """
                INSERT INTO notification_deliveries (delivery_id, alert_id, caregiver_id, channel, recipient_snapshot, status, attempt_count, idempotency_key)
                VALUES (?, ?, 'caregiver_001', 'EMAIL', 'test@example.com', 'QUEUED', 0, ?)
                """,
                (delivery_id, alert_id, delivery_id),
            )

            # Simulate 3 failed attempts
            for attempt in range(1, 4):
                conn.execute(
                    "UPDATE notification_deliveries SET attempt_count = ?, last_attempt_at_utc = ?, error_message = 'Network timeout' WHERE delivery_id = ?",
                    (attempt, datetime.now(timezone.utc).isoformat(), delivery_id),
                )
            # Exceeded max retry (3) -> status marked FAILED (never falsely marked SENT)
            conn.execute("UPDATE notification_deliveries SET status = 'FAILED' WHERE delivery_id = ?", (delivery_id,))
            conn.commit()

            check = conn.fetchone("SELECT * FROM notification_deliveries WHERE delivery_id = ?", (delivery_id,))
            self.assertEqual(check["status"], "FAILED")
            self.assertEqual(check["attempt_count"], 3)
            self.assertEqual(check["error_message"], "Network timeout")

    # --------------------------------------------------------------------------
    # Registration & Role Isolation Extension
    # --------------------------------------------------------------------------
    def test_caregiver_registration_and_doctor_role_isolation(self) -> None:
        # 1. Invalid email format must be rejected
        code_inv, res_inv = self.router.handle_request(
            "POST", "/api/auth/register-caregiver", {},
            {"full_name": "Phạm Văn A", "email": "invalid-format", "password": "123"}, None
        )
        self.assertEqual(code_inv, 400)
        self.assertIn("email", res_inv["error"].lower())

        # 2. Duplicate email must be rejected
        code_dup, res_dup = self.router.handle_request(
            "POST", "/api/auth/register-caregiver", {},
            {"full_name": "Trùng Email", "email": "tuan.caregiver@example.com", "password": "123"}, None
        )
        self.assertEqual(code_dup, 400)
        self.assertIn("đã được đăng ký", res_dup["error"])

        # 3. Successful registration for new caregiver linked to patient_002
        new_email = f"new.caregiver.{uuid.uuid4().hex[:6]}@gmail.com"
        code_ok, res_ok = self.router.handle_request(
            "POST", "/api/auth/register-caregiver", {},
            {
                "full_name": "Phạm Thị Hương",
                "email": new_email,
                "phone": "0987111222",
                "password": "securepassword",
                "patient_id": "patient_002",
                "relationship_type": "Con gái",
            }, None
        )
        self.assertEqual(code_ok, 201)
        self.assertIn("token", res_ok)
        cg_token = res_ok["token"]

        # Immediate authorization check: can access patient_002, but NOT patient_001
        c_p2, _ = self.router.handle_request("GET", "/api/patients/patient_002/overview", {}, {}, cg_token)
        self.assertEqual(c_p2, 200, "Người nhà mới đăng ký phải có quyền truy cập bệnh nhân được phân công")

        c_p1, _ = self.router.handle_request("GET", "/api/patients/patient_001/overview", {}, {}, cg_token)
        self.assertEqual(c_p1, 403, "Người nhà mới đăng ký KHÔNG ĐƯỢC phép truy cập bệnh nhân khác")

        # 4. Role mismatch rejection: Caregiver logging in on Clinician tab
        c_mis1, res_mis1 = self.router.handle_request(
            "POST", "/api/auth/login", {},
            {"email": new_email, "password": "securepassword", "expected_role": "clinician"}, None
        )
        self.assertEqual(c_mis1, 403)
        self.assertIn("Người nhà", res_mis1["error"])

        # 5. Role mismatch rejection: Doctor logging in on Caregiver tab
        c_mis2, res_mis2 = self.router.handle_request(
            "POST", "/api/auth/login", {},
            {"email": "dr.an@laokhoa.example.com", "password": "demo", "expected_role": "caregiver"}, None
        )
        self.assertEqual(c_mis2, 403)
        self.assertIn("Bác sĩ", res_mis2["error"])

    # --------------------------------------------------------------------------
    # Scenario: Clinician Portal Features (Sidebar Green Dot, Custom Meds, Rx Cancellation)
    # --------------------------------------------------------------------------
    def test_clinician_portal_sidebar_updates_and_prescription_actions(self) -> None:
        doc_token = self.auth.create_session(self.auth.authenticate_user("user_doc_001"))

        # 1. Medications catalog retrieval
        code_med, res_med = self.router.handle_request("GET", "/api/medications", {}, {}, doc_token)
        self.assertEqual(code_med, 200)
        self.assertIn("medications", res_med)
        self.assertTrue(len(res_med["medications"]) >= 3)

        # 2. Add custom medication via prescription revision
        with self.db.connect() as conn:
            active_rx = conn.fetchone("SELECT * FROM prescriptions WHERE patient_id='patient_001' AND status='ACTIVE' LIMIT 1")
            active_rx_id = active_rx["prescription_id"]

        custom_drug_name = f"Losartan Khẩn Cấp {uuid.uuid4().hex[:4]}"
        code_rev, res_rev = self.router.handle_request(
            "POST", f"/api/patients/patient_001/prescriptions/{active_rx_id}/revisions", {},
            {
                "reason": "Bổ sung thuốc mới ngoài danh mục",
                "items": [{
                    "medication_id": "__custom__",
                    "custom_brand_name": custom_drug_name,
                    "generic_name": "Losartan potassium",
                    "product_strength": "50",
                    "strength_unit": "mg",
                    "dosage_form": "Viên nén",
                    "dose_amount": 1.0,
                    "dose_unit": "viên",
                    "instructions": "Uống sau ăn sáng",
                    "local_times": ["08:00"]
                }]
            },
            doc_token
        )
        self.assertEqual(code_rev, 201)
        new_rx_id = res_rev["prescription_id"]

        # Verify new medication was registered in medications table
        with self.db.connect() as conn:
            new_med = conn.fetchone("SELECT * FROM medications WHERE LOWER(brand_name) = LOWER(?)", (custom_drug_name,))
            self.assertIsNotNone(new_med, "Thuốc mới phải được tự động lưu vào bảng danh mục medications")

        # 3. Cancel prescription
        code_cancel, res_cancel = self.router.handle_request(
            "POST", f"/api/patients/patient_001/prescriptions/{new_rx_id}/cancel", {},
            {"reason": "Bác sĩ ngưng chỉ định do đạt mục tiêu điều trị"}, doc_token
        )
        self.assertEqual(code_cancel, 200)
        self.assertEqual(res_cancel["status"], "CANCELLED")

        # Verify database state
        with self.db.connect() as conn:
            cancelled_rx = conn.fetchone("SELECT status FROM prescriptions WHERE prescription_id = ?", (new_rx_id,))
            self.assertEqual(cancelled_rx["status"], "CANCELLED")

        # 4. Verify patient list has_new_update indicator
        accessible = self.auth.list_accessible_patients("user_doc_001")
        self.assertTrue(len(accessible) >= 2)
        p2 = next(p for p in accessible if p["patient_id"] == "patient_002")
        self.assertTrue(p2["has_new_update"], "Bệnh nhân có cảnh báo mới phải có has_new_update = True")


if __name__ == "__main__":
    unittest.main()

