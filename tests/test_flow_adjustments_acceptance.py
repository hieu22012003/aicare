"""
Comprehensive End-to-End Acceptance Test Suite for the 4 Medical Business Flows:
1. Prescription Creation, Versioning, Cancellation & 409 Conflict.
2. Medication One-Touch Reminders, Overdue Scanning, Late Confirmation & Emergency Help.
3. Rehabilitation Plan Versioning, Target Verification & Leisure Games Separation.
4. Health Measurement Requests, Auto-fulfillment, Expiration Without Fake Zeros & Broken Lines.
5. Offline Recovery, Idempotency & Strict Multi-Tenant RBAC.

Reference: plan/KE_HOACH_DIEU_CHINH_LUONG_VA_DONG_BO_BA_GIAO_DIEN.md (Section 7)
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import time
import unittest
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from shared.database.connection import UnifiedDatabaseManager
from shared.database.migrator import Migrator
from shared.database.importer import DataImporter
from features.web_portal.auth import AuthService
from features.web_portal.api_router import ApiRouter
from features.web_portal.sse_hub import sse_hub
from shared.services.outbox_relay import OutboxRelayService
from shared.services.elder_sync_client import ElderSyncClient

UTC = timezone.utc


class MockEmailService:
    def __init__(self) -> None:
        self.attempts: List[Dict[str, Any]] = []

    def send_alert_email(self, recipient: str, subject: str, body: str) -> bool:
        self.attempts.append({"recipient": recipient, "subject": subject, "body": body})
        return True


class TestFlowAdjustmentsAcceptance(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.mkdtemp(prefix="flow_test_")
        self.queue_dir = Path(self.temp_dir) / "sync_queues"
        self.queue_dir.mkdir(parents=True, exist_ok=True)

        self.db = UnifiedDatabaseManager(":memory:")
        Migrator(self.db).apply_all()
        DataImporter(self.db).import_all()
        self.auth = AuthService(self.db)
        self.router = ApiRouter(self.db, self.auth)

        # Clear SSE singleton
        sse_hub._clients.clear()
        sse_hub._user_clients.clear()

        # Connect Outbox Relay
        self.mock_email = MockEmailService()
        self.relay = OutboxRelayService(self.db, self.auth, poll_interval=0.05, email_service=self.mock_email)
        self.router.outbox_relay = self.relay

        # Create session tokens for test actors
        self.doc_token = self.auth.create_session(self.auth.authenticate_user("user_doc_001"))
        self.care_token = self.auth.create_session(self.auth.authenticate_user("user_care_001"))

    def tearDown(self) -> None:
        self.relay.stop()
        shutil.rmtree(self.temp_dir, ignore_errors=True)
        sse_hub._clients.clear()
        sse_hub._user_clients.clear()

    # =========================================================================
    # KỊCH BẢN 1: BÁC SĨ KÊ ĐƠN, ĐIỀU CHỈNH PHIÊN BẢN (409 CONFLICT), HỦY ĐƠN & RBAC
    # =========================================================================
    def test_scenario_1_prescription_lifecycle_and_version_conflict(self) -> None:
        patient_id = "patient_001"

        # 1. Bác sĩ kê đơn thuốc mới hoàn toàn (v1)
        today_str = date.today().isoformat()
        next_week = (date.today() + timedelta(days=7)).isoformat()
        rx_payload = {
            "prescription_code": f"RX-NEW-{uuid.uuid4().hex[:6].upper()}",
            "clinical_indication": "Tăng huyết áp vô căn độ 1",
            "start_date": today_str,
            "end_date": next_week,
            "items": [
                {
                    "medication_id": "med_001",
                    "dose_amount": 1.0,
                    "dose_unit": "viên",
                    "instructions": "Uống sau bữa ăn sáng 15 phút",
                    "times_of_day": ["08:00"],
                }
            ],
        }

        code, resp = self.router.handle_request(
            method="POST",
            path=f"/api/patients/{patient_id}/prescriptions",
            query_params={},
            body=rx_payload,
            token=self.doc_token,
        )
        self.assertEqual(code, 201, f"Bác sĩ phải kê đơn mới thành công: {resp}")
        rx_id = resp["prescription_id"]
        self.assertEqual(resp["version"], 1)

        # 2. Xử lý Outbox và kiểm tra SSE
        self.relay.process_pending_events()

        # 3. Người già thấy liều thuốc trong snapshot hôm nay
        elder_client = ElderSyncClient(self.db, patient_id=patient_id, queue_dir=self.queue_dir)
        snap = elder_client.get_today_snapshot()
        dose_names = [d["medication_name_snapshot"] for d in snap["doses"]]
        self.assertTrue(any("Amlor" in name for name in dose_names), "Người già phải thấy thuốc mới được kê")

        # 4. Thử điều chỉnh đơn với expected_version SAI (mô phỏng 2 bác sĩ sửa đồng thời)
        rev_payload_conflict = {
            "expected_version": 999,  # Version không khớp
            "reason": "Điều chỉnh liều trùng lặp",
            "items": [
                {
                    "medication_id": "med_001",
                    "dose_amount": 2.0,
                    "dose_unit": "viên",
                    "instructions": "Uống 2 viên buổi sáng",
                    "local_times": ["08:00"],
                }
            ],
        }
        code, resp = self.router.handle_request(
            method="POST",
            path=f"/api/patients/{patient_id}/prescriptions/{rx_id}/revisions",
            query_params={},
            body=rev_payload_conflict,
            token=self.doc_token,
        )
        self.assertEqual(code, 409, "Phải trả về HTTP 409 Conflict khi sai expected_version")
        self.assertIn("Xung đột phiên bản", resp.get("error", ""))

        # 5. Điều chỉnh với expected_version ĐÚNG (1) -> thành công lên v2
        rev_payload_ok = {
            "expected_version": 1,
            "reason": "Tăng liều điều trị theo phác đồ",
            "items": [
                {
                    "medication_id": "med_001",
                    "dose_amount": 2.0,
                    "dose_unit": "viên",
                    "instructions": "Uống 2 viên buổi sáng sau ăn",
                    "local_times": ["08:00"],
                }
            ],
        }
        code, resp = self.router.handle_request(
            method="POST",
            path=f"/api/patients/{patient_id}/prescriptions/{rx_id}/revisions",
            query_params={},
            body=rev_payload_ok,
            token=self.doc_token,
        )
        self.assertIn(code, (200, 201), f"Điều chỉnh đơn phải thành công: {resp}")
        self.assertEqual(resp["version"], 2)

        # Kiểm tra phiên bản cũ v1 còn nguyên vẹn trong DB với status SUPERSEDED
        with self.db.connect() as conn:
            old_rx = conn.fetchone("SELECT * FROM prescriptions WHERE prescription_id = ?", (rx_id,))
            self.assertEqual(old_rx["status"], "SUPERSEDED", "Đơn cũ v1 phải chuyển sang SUPERSEDED, không bị xóa")

        # 6. Bác sĩ hủy đơn thuốc mới (v2)
        new_rx_id = resp["prescription_id"]
        code, resp = self.router.handle_request(
            method="POST",
            path=f"/api/patients/{patient_id}/prescriptions/{new_rx_id}/cancel",
            query_params={},
            body={"reason": "Bệnh nhân chuyển viện điều trị"},
            token=self.doc_token,
        )
        self.assertEqual(code, 200)
        self.assertEqual(resp["status"], "CANCELLED")

        # Sau khi hủy, snapshot của người già không còn liều tương lai của đơn bị hủy
        snap2 = elder_client.get_today_snapshot()
        v2_doses = [d for d in snap2["doses"] if d.get("prescription_id") == new_rx_id]
        self.assertEqual(len(v2_doses), 0, "Các liều tương lai của đơn đã hủy phải được gỡ bỏ khỏi app người già")

        # 7. Phân quyền (RBAC): Người nhà (caregiver) thử kê đơn thuốc -> BỊ CHẶN 403
        code, resp = self.router.handle_request(
            method="POST",
            path=f"/api/patients/{patient_id}/prescriptions",
            query_params={},
            body=rx_payload,
            token=self.care_token,
        )
        self.assertEqual(code, 403, "Người nhà tuyệt đối không có quyền kê đơn thuốc (RBAC 403)")

    # =========================================================================
    # KỊCH BẢN 2: UỐNG THUỐC 1 CHẠM, SCAN QUÁ HẠN TẠO ALERT, XÁC NHẬN TRỄ & CẦN HỖ TRỢ
    # =========================================================================
    def test_scenario_2_medication_one_touch_overdue_and_late_confirmation(self) -> None:
        patient_id = "patient_001"
        elder_client = ElderSyncClient(self.db, patient_id=patient_id, queue_dir=self.queue_dir)

        # 1. Lấy danh sách liều hôm nay
        snap = elder_client.get_today_snapshot()
        self.assertGreater(len(snap["doses"]), 0)
        occ = snap["doses"][0]
        occ_id = occ["occurrence_id"]

        # Đảm bảo hạn grace còn trong tương lai để xác nhận đúng giờ
        future_grace = (datetime.now(UTC) + timedelta(hours=2)).isoformat()
        with self.db.connect() as conn:
            conn.execute("UPDATE dose_occurrences SET grace_deadline_utc = ? WHERE occurrence_id = ?", (future_grace, occ_id))
            conn.commit()

        # 2. Người già bấm một chạm 'Đã uống' đúng giờ -> CONFIRMED (self_reported_taken)
        res = elder_client.confirm_dose_taken(occ_id, action="self_reported_taken")
        self.assertEqual(res["status"], "processed")

        with self.db.connect() as conn:
            d_row = conn.fetchone("SELECT * FROM dose_occurrences WHERE occurrence_id = ?", (occ_id,))
            self.assertEqual(d_row["state"], "CONFIRMED")
            self.assertEqual(d_row["action_actor"], "self_reported_taken")

        # 3. Tạo một liều quá hạn (grace_deadline_utc đã qua 15 phút)
        past_iso = (datetime.now(UTC) - timedelta(minutes=15)).isoformat()
        ovd_occ_id = f"occ_ovd_{uuid.uuid4().hex[:8]}"
        with self.db.connect() as conn:
            conn.execute(
                """
                INSERT INTO dose_occurrences (
                    occurrence_id, schedule_id, revision, scheduled_at_utc, local_scheduled_time,
                    grace_deadline_utc, state, medication_name_snapshot, dose_snapshot, instructions_snapshot, patient_id
                ) VALUES (?, ?, 1, ?, '08:00', ?, 'PENDING', 'Amlor 5mg', '1 viên', 'Uống sau ăn', ?)
                """,
                (ovd_occ_id, occ["schedule_id"], past_iso, past_iso, patient_id),
            )
            conn.commit()

        # 4. Worker quét liều quá hạn -> Chuyển sang OVERDUE và tạo đúng 1 alert cho người nhà
        handled = self.relay.scan_overdue_tasks()
        self.assertGreaterEqual(handled, 1, "Worker phải phát hiện và xử lý liều quá hạn")

        with self.db.connect() as conn:
            ovd_row = conn.fetchone("SELECT * FROM dose_occurrences WHERE occurrence_id = ?", (ovd_occ_id,))
            self.assertEqual(ovd_row["state"], "OVERDUE", "Trạng thái phải chuyển sang OVERDUE")

            alerts = conn.fetchall("SELECT * FROM alerts WHERE occurrence_id = ? AND alert_type = 'OVERDUE'", (ovd_occ_id,))
            self.assertEqual(len(alerts), 1, "Phải tạo đúng 1 cảnh báo OVERDUE duy nhất")
            self.assertEqual(alerts[0]["status"], "NEW")

        # 5. Người già xác nhận uống muộn sau đó -> Trạng thái thể hiện confirmed_late, alert được RESOLVED (giữ lịch sử)
        res_late = elder_client.confirm_dose_taken(ovd_occ_id, action="self_reported_taken")
        self.assertEqual(res_late["status"], "processed")

        with self.db.connect() as conn:
            late_row = conn.fetchone("SELECT * FROM dose_occurrences WHERE occurrence_id = ?", (ovd_occ_id,))
            self.assertEqual(late_row["state"], "CONFIRMED")
            self.assertEqual(late_row["action_actor"], "confirmed_late", "Phải ghi nhận là uống trễ (confirmed_late)")

            # Alert chuyển sang RESOLVED, bảo lưu bản ghi lịch sử đã cảnh báo
            alert_after = conn.fetchone("SELECT * FROM alerts WHERE occurrence_id = ?", (ovd_occ_id,))
            self.assertEqual(alert_after["status"], "RESOLVED", "Cảnh báo phải được đánh dấu đã giải quyết")

        # 6. Người già bấm '🆘 Cần hỗ trợ' trên một liều khác
        help_occ_id = f"occ_help_{uuid.uuid4().hex[:8]}"
        past_iso_2 = (datetime.now(UTC) - timedelta(minutes=45)).isoformat()
        with self.db.connect() as conn:
            conn.execute(
                """
                INSERT INTO dose_occurrences (
                    occurrence_id, schedule_id, revision, scheduled_at_utc, local_scheduled_time,
                    grace_deadline_utc, state, medication_name_snapshot, dose_snapshot, instructions_snapshot, patient_id
                ) VALUES (?, ?, 1, ?, '12:00', ?, 'PENDING', 'Panadol 500mg', '1 viên', 'Uống khi sốt', ?)
                """,
                (help_occ_id, occ["schedule_id"], past_iso_2, past_iso_2, patient_id),
            )
            conn.commit()

        res_help = elder_client.confirm_dose_taken(help_occ_id, action="help_requested")
        self.assertEqual(res_help["status"], "processed")

        with self.db.connect() as conn:
            help_alert = conn.fetchone("SELECT * FROM alerts WHERE occurrence_id = ? AND alert_type = 'HELP_REQUESTED'", (help_occ_id,))
            self.assertIsNotNone(help_alert, "Bấm 'Cần hỗ trợ' phải sinh cảnh báo HELP_REQUESTED cho người nhà")
            self.assertEqual(help_alert["status"], "NEW")

    # =========================================================================
    # KỊCH BẢN 3: PHỤC HỒI CHỨC NĂNG, TĂNG VERSION KẾ HOẠCH, CAMERA TARGET & GAMES
    # =========================================================================
    def test_scenario_3_rehabilitation_plan_versioning_and_target_verification(self) -> None:
        patient_id = "patient_001"

        # 1. Người nhà đổi mục tiêu và giờ tập bài vươn tay (rp_001)
        plan_id = "rp_001"
        patch_payload = {
            "local_reminder_time": "16:15",
            "target_count": 12,
            "expected_version": 1,
        }
        code, resp = self.router.handle_request(
            method="PATCH",
            path=f"/api/patients/{patient_id}/exercise-plans/{plan_id}",
            query_params={},
            body=patch_payload,
            token=self.care_token,
        )
        self.assertEqual(code, 200, f"Người nhà cập nhật kế hoạch tập phải thành công: {resp}")
        self.assertEqual(resp["version"], 2, "Phiên bản kế hoạch phải tăng lên v2")
        self.assertEqual(resp["target_count"], 12)

        # Kiểm tra bảng exercise_plan_versions ghi nhận lịch sử thay đổi
        with self.db.connect() as conn:
            hist = conn.fetchall("SELECT * FROM exercise_plan_versions WHERE plan_id = ? ORDER BY version ASC", (plan_id,))
            self.assertEqual(len(hist), 2, "Phải lưu cả v1 và v2 trong exercise_plan_versions")
            self.assertEqual(hist[1]["target_count"], 12)

        # 2. Thử sửa với expected_version cũ (1) -> Trả về HTTP 409 Conflict
        code_conflict, resp_conflict = self.router.handle_request(
            method="PATCH",
            path=f"/api/patients/{patient_id}/exercise-plans/{plan_id}",
            query_params={},
            body={"expected_version": 1, "target_count": 15},
            token=self.care_token,
        )
        self.assertEqual(code_conflict, 409, "Xung đột phiên bản phải trả về 409 Conflict")

        # 3. Tạo buổi tập hôm nay theo target 12
        sess_id = f"sess_test_{uuid.uuid4().hex[:8]}"
        today_str = date.today().isoformat()
        with self.db.connect() as conn:
            conn.execute(
                """
                INSERT INTO rehab_sessions (
                    session_id, patient_id, plan_id, plan_version, local_date,
                    target_count_snapshot, verified_count, completion_source, status
                ) VALUES (?, ?, ?, 2, ?, 12, 0, 'camera', 'pending')
                """,
                (sess_id, patient_id, plan_id, today_str),
            )
            conn.commit()

        # 4. Camera AI đếm 8 lần (chưa đủ mục tiêu 12) -> status là in_progress, KHÔNG được đánh dấu completed
        for r in range(1, 9):
            code, resp = self.router.handle_request(
                method="POST",
                path=f"/api/patients/{patient_id}/rehab-progress",
                query_params={},
                body={"session_id": sess_id, "rep_number": r, "confidence": 0.95},
                token=self.care_token,
            )
            self.assertEqual(code, 200)

        with self.db.connect() as conn:
            s_row = conn.fetchone("SELECT * FROM rehab_sessions WHERE session_id = ?", (sess_id,))
            self.assertEqual(s_row["verified_count"], 8)
            self.assertEqual(s_row["status"], "in_progress", "Chưa đủ 12 lần thì không được báo hoàn thành")

        # 5. Camera AI đếm đủ 12 lần -> status chuyển thành camera_verified_completed
        for r in range(9, 13):
            code, resp = self.router.handle_request(
                method="POST",
                path=f"/api/patients/{patient_id}/rehab-progress",
                query_params={},
                body={"session_id": sess_id, "rep_number": r, "confidence": 0.98},
                token=self.care_token,
            )
            self.assertEqual(code, 200)

        with self.db.connect() as conn:
            s_row = conn.fetchone("SELECT * FROM rehab_sessions WHERE session_id = ?", (sess_id,))
            self.assertEqual(s_row["verified_count"], 12)
            self.assertEqual(s_row["status"], "camera_verified_completed", "Đủ 12 lần mới được gắn hoàn thành")

        # 6. Người già chơi trò chơi trí nhớ khi buồn chán -> KHÔNG ảnh hưởng/không làm sai lệch số lần tập phục hồi
        elder_client = ElderSyncClient(self.db, patient_id=patient_id, queue_dir=self.queue_dir)
        elder_client.record_mood(rating=5, note="Chơi game lật hình vui vẻ khi rảnh rỗi")

        with self.db.connect() as conn:
            s_check = conn.fetchone("SELECT * FROM rehab_sessions WHERE session_id = ?", (sess_id,))
            self.assertEqual(s_check["verified_count"], 12, "Chơi game trí nhớ không được tự tăng số lần tập phục hồi")

    # =========================================================================
    # KỊCH BẢN 4: NHẮC ĐO CHỈ SỐ, TỰ ĐỘNG ĐÓNG KHI ĐO THẬT, QUÁ HẠN KHÔNG TẠO SỐ 0
    # =========================================================================
    def test_scenario_4_measurement_requests_and_broken_line_vitals(self) -> None:
        patient_id = "patient_001"

        # 1. Người nhà tạo yêu cầu nhắc đo Huyết áp (ot-sys)
        due_future = (datetime.now(UTC) + timedelta(hours=2)).isoformat()
        req_payload = {
            "observation_type_id": "ot-sys",
            "due_at_utc": due_future,
            "instructions": "Nghỉ ngơi 5 phút trước khi bấm máy đo huyết áp",
        }
        code, resp = self.router.handle_request(
            method="POST",
            path=f"/api/patients/{patient_id}/measurement-requests",
            query_params={},
            body=req_payload,
            token=self.care_token,
        )
        self.assertEqual(code, 201, f"Tạo yêu cầu nhắc đo phải thành công: {resp}")
        req_id = resp["request_id"]
        self.assertEqual(resp["status"], "PENDING")

        # App người già thấy yêu cầu này trong snapshot
        elder_client = ElderSyncClient(self.db, patient_id=patient_id, queue_dir=self.queue_dir)
        snap = elder_client.get_today_snapshot()
        self.assertTrue(any(r["request_id"] == req_id for r in snap.get("measurement_requests", [])))

        # 2. Đo thật: Người nhà/thiết bị nhập kết quả huyết áp 125/82 mmHg
        meas_payload = {
            "measured_at": datetime.now(UTC).isoformat(),
            "device_label": "Máy đo Omron bắp tay",
            "notes": "Đo sau khi ngồi nghỉ theo nhắc nhở",
            "readings": [
                {"observation_type_id": "ot-sys", "value_numeric": 125.0},
                {"observation_type_id": "ot-dia", "value_numeric": 82.0},
            ],
        }
        code, resp = self.router.handle_request(
            method="POST",
            path=f"/api/patients/{patient_id}/measurements",
            query_params={},
            body=meas_payload,
            token=self.care_token,
        )
        self.assertEqual(code, 201)

        # Kiểm tra yêu cầu đo ban đầu tự động chuyển sang COMPLETED
        with self.db.connect() as conn:
            req_row = conn.fetchone("SELECT * FROM measurement_requests WHERE request_id = ?", (req_id,))
            self.assertEqual(req_row["status"], "COMPLETED", "Yêu cầu phải tự động đóng khi có số đo thật cùng loại")
            self.assertIsNotNone(req_row["completed_observation_id"])

        # 3. Yêu cầu đo bị quá hạn (due_at_utc trong quá khứ)
        due_past = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
        req_past_id = f"mr_past_{uuid.uuid4().hex[:8]}"
        with self.db.connect() as conn:
            conn.execute(
                """
                INSERT INTO measurement_requests (
                    request_id, patient_id, requested_by_user_id, observation_type_id,
                    due_at_utc, local_due_time, instructions, status, created_at_utc
                ) VALUES (?, ?, 'user_care_001', 'ot-glu', ?, '07:00', 'Đo đường huyết trước ăn sáng', 'PENDING', ?)
                """,
                (req_past_id, patient_id, due_past, due_past),
            )
            conn.commit()

        # Đếm số bản ghi health_observations trước khi quét
        with self.db.connect() as conn:
            obs_count_before = conn.fetchone("SELECT COUNT(*) as cnt FROM health_observations")["cnt"]

        # Worker quét quá hạn
        self.relay.scan_overdue_tasks()

        # Kiểm tra yêu cầu chuyển sang MISSED
        with self.db.connect() as conn:
            req_missed = conn.fetchone("SELECT * FROM measurement_requests WHERE request_id = ?", (req_past_id,))
            self.assertEqual(req_missed["status"], "MISSED", "Quá hạn mà không đo thì trạng thái là MISSED")

            # TUYỆT ĐỐI KHÔNG TẠO BẢN GHI SỐ ĐO GIẢ HOẶC ĐIỂM 0 TRONG DB (Nguyên tắc y tế an toàn)
            obs_count_after = conn.fetchone("SELECT COUNT(*) as cnt FROM health_observations")["cnt"]
            self.assertEqual(obs_count_before, obs_count_after, "Tuyệt đối không được chèn số đo 0 hoặc số đo giả vào CSDL")

            zero_obs = conn.fetchall("SELECT * FROM health_observations WHERE value_numeric = 0.0")
            self.assertEqual(len(zero_obs), 0, "Không được tồn tại giá trị huyết áp/đường huyết 0 giả tạo")

    # =========================================================================
    # KỊCH BẢN 5: NGOẠI TUYẾN, KHÔI PHỤC SAU MẤT MẠNG, IDEMPOTENCY & MULTI-TENANT RBAC
    # =========================================================================
    def test_scenario_5_offline_recovery_idempotency_and_patient_rbac(self) -> None:
        patient_id = "patient_001"
        elder_client = ElderSyncClient(self.db, patient_id=patient_id, queue_dir=self.queue_dir)

        # 1. Lấy một liều thuốc để thực hiện thao tác ngoại tuyến
        snap = elder_client.get_today_snapshot()
        target_occ = snap["doses"][0]
        occ_id = target_occ["occurrence_id"]

        # Giả lập thiết bị ngoại tuyến (offline)
        elder_client.is_online = False
        cid = f"offline_evt_{uuid.uuid4().hex[:8]}"
        elder_client.enqueue_event(
            event_type="medication.confirm",
            payload={"occurrence_id": occ_id, "action": "self_reported_taken"},
            client_event_id=cid,
        )
        self.assertGreater(elder_client.get_sync_status()["pending_count"], 0)

        # 2. Khôi phục mạng -> Flush queue
        elder_client.is_online = True
        flushed = elder_client.flush_offline_queue()
        self.assertGreaterEqual(flushed, 1, "Phải đồng bộ thành công các sự kiện ngoại tuyến lên server")
        self.assertEqual(elder_client.get_sync_status()["pending_count"], 0)

        # 3. Idempotency: Flush lần thứ 2 không còn gì
        flushed_again = elder_client.flush_offline_queue()
        self.assertEqual(flushed_again, 0, "Lần flush tiếp theo không còn sự kiện nào")

        # 4. Multi-Tenant RBAC: Bác sĩ thử truy cập bệnh nhân không thuộc care team
        code_unauth, resp_unauth = self.router.handle_request(
            method="GET",
            path="/api/patients/patient_999/overview",
            query_params={},
            body={},
            token=self.doc_token,
        )
        self.assertIn(code_unauth, (403, 404), "Không được truy cập hồ sơ bệnh nhân không thuộc Care Team (403/404)")


if __name__ == "__main__":
    unittest.main()
