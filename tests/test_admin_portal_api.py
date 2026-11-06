"""
Unit and Integration Tests for Admin Portal API & Doctor Management:
- Admin Authentication (admin@unifiedcare.local / admin123 or admin)
- RBAC: Non-admin users are blocked (403 Forbidden)
- Doctor CRUD (List, Create, Update, Reset Password, Toggle Active, Delete)
- Care Team Assignment & Removal
- Patient Creation via Admin API
- Doctor Login verification after creation and password reset
"""

import json
import unittest
from datetime import datetime, timezone

from shared.database.connection import UnifiedDatabaseManager
from shared.database.migrator import Migrator
from features.web_portal.auth import AuthService
from features.web_portal.api_router import ApiRouter


class TestAdminPortalApi(unittest.TestCase):
    def setUp(self) -> None:
        self.db = UnifiedDatabaseManager(":memory:")
        Migrator(self.db).apply_all()

        # Seed initial admin account
        now_iso = datetime.now(timezone.utc).isoformat()
        with self.db.connect() as conn:
            conn.execute(
                """
                INSERT INTO users (user_id, email, phone, full_name, password_hash, role, is_active, created_at)
                VALUES ('user_admin_001', 'admin@unifiedcare.local', '0900000000', 'Quản Trị Viên Hệ Thống', 'pbkdf2:admin123', 'admin', 1, ?)
                """,
                (now_iso,),
            )

        self.auth = AuthService(self.db)
        self.router = ApiRouter(self.db, self.auth)

    def test_admin_login_and_token(self) -> None:
        # Test login with email
        status, data = self.router.handle_request(
            "POST", "/api/auth/login", {},
            {"email": "admin@unifiedcare.local", "password": "admin123", "expected_role": "admin"}
        )
        self.assertEqual(status, 200)
        self.assertIn("token", data)
        self.assertEqual(data["user"]["role"], "admin")

        # Test login with alias "admin"
        status2, data2 = self.router.handle_request(
            "POST", "/api/auth/login", {},
            {"email": "admin", "password": "admin123", "expected_role": "admin"}
        )
        self.assertEqual(status2, 200)
        self.assertIn("token", data2)

    def test_non_admin_cannot_access_admin_api(self) -> None:
        # Create a caregiver
        now_iso = datetime.now(timezone.utc).isoformat()
        with self.db.connect() as conn:
            conn.execute(
                """
                INSERT INTO users (user_id, email, phone, full_name, password_hash, role, is_active, created_at)
                VALUES ('user_cg_001', 'caregiver@example.com', '0901112222', 'Nguyễn Chăm Sóc', 'pbkdf2:demo', 'caregiver', 1, ?)
                """,
                (now_iso,),
            )

        status, data = self.router.handle_request(
            "POST", "/api/auth/login", {},
            {"email": "caregiver@example.com", "password": "demo", "expected_role": "caregiver"}
        )
        self.assertEqual(status, 200)
        cg_token = data["token"]

        # Attempt to access /api/admin/doctors with caregiver token
        status_admin, data_admin = self.router.handle_request(
            "GET", "/api/admin/doctors", {}, {}, token=cg_token
        )
        self.assertEqual(status_admin, 403)
        self.assertIn("error", data_admin)

    def test_admin_doctor_crud_lifecycle(self) -> None:
        # Login as admin
        _, login_data = self.router.handle_request(
            "POST", "/api/auth/login", {},
            {"email": "admin@unifiedcare.local", "password": "admin123"}
        )
        admin_token = login_data["token"]

        # 1. Initially doctors list is empty
        status, res = self.router.handle_request("GET", "/api/admin/doctors", {}, {}, token=admin_token)
        self.assertEqual(status, 200)
        self.assertEqual(len(res["doctors"]), 0)
        self.assertEqual(res["stats"]["total_doctors"], 0)

        # 2. Admin creates a new doctor
        doc_payload = {
            "full_name": "BS. Nguyễn Văn An",
            "email": "dr.an@laokhoa.example.com",
            "password": "doctorpassword123",
            "phone": "0912345678",
            "license_number": "CCHN-LK-001",
            "workplace": "Bệnh viện Lão khoa TW",
            "specialty": "Tim mạch & Lão khoa",
            "is_simulation": 0
        }
        status, res = self.router.handle_request("POST", "/api/admin/doctors", {}, doc_payload, token=admin_token)
        self.assertEqual(status, 201)
        self.assertIn("clinician_id", res)
        clinician_id = res["clinician_id"]

        # 3. Doctor can now log in
        status_doc_login, doc_login_data = self.router.handle_request(
            "POST", "/api/auth/login", {},
            {"email": "dr.an@laokhoa.example.com", "password": "doctorpassword123", "expected_role": "clinician"}
        )
        self.assertEqual(status_doc_login, 200)
        self.assertEqual(doc_login_data["user"]["role"], "clinician")

        # 4. Admin updates doctor info
        update_payload = {
            "full_name": "PGS.TS. Nguyễn Văn An",
            "specialty": "Khoa Hồi sức tích cực",
            "workplace": "BV Lão khoa TW - Khu Điều trị kỹ thuật cao",
        }
        status, res = self.router.handle_request("POST", f"/api/admin/doctors/{clinician_id}", {}, update_payload, token=admin_token)
        self.assertEqual(status, 200)
        self.assertEqual(res["status"], "updated")

        # 5. Admin resets doctor password
        status, res = self.router.handle_request(
            "POST", f"/api/admin/doctors/{clinician_id}/reset-password", {},
            {"new_password": "newsecretpassword456"}, token=admin_token
        )
        self.assertEqual(status, 200)
        self.assertEqual(res["status"], "password_reset")

        # Old password fails
        status_fail, _ = self.router.handle_request(
            "POST", "/api/auth/login", {},
            {"email": "dr.an@laokhoa.example.com", "password": "doctorpassword123", "expected_role": "clinician"}
        )
        self.assertNotEqual(status_fail, 200)

        # New password succeeds
        status_ok, _ = self.router.handle_request(
            "POST", "/api/auth/login", {},
            {"email": "dr.an@laokhoa.example.com", "password": "newsecretpassword456", "expected_role": "clinician"}
        )
        self.assertEqual(status_ok, 200)

        # 6. Admin toggles doctor active status (Lock account)
        status, res = self.router.handle_request(
            "POST", f"/api/admin/doctors/{clinician_id}/toggle-status", {},
            {"is_active": 0}, token=admin_token
        )
        self.assertEqual(status, 200)
        self.assertEqual(res["is_active"], 0)

        # Locked doctor cannot login
        status_locked, _ = self.router.handle_request(
            "POST", "/api/auth/login", {},
            {"email": "dr.an@laokhoa.example.com", "password": "newsecretpassword456", "expected_role": "clinician"}
        )
        self.assertNotEqual(status_locked, 200)

        # Unlock doctor
        status, res = self.router.handle_request(
            "POST", f"/api/admin/doctors/{clinician_id}/toggle-status", {},
            {"is_active": 1}, token=admin_token
        )
        self.assertEqual(status, 200)
        self.assertEqual(res["is_active"], 1)

        # 7. Create patient and assign doctor to Care Team
        pat_payload = {
            "patient_id": "patient_test_001",
            "full_name": "Cụ Hoàng Thị Mai",
            "gender": "Nữ",
            "date_of_birth": "1948-05-12",
            "notes": "Tăng huyết áp độ 2"
        }
        status, res = self.router.handle_request("POST", "/api/admin/patients", {}, pat_payload, token=admin_token)
        self.assertEqual(status, 201)
        self.assertEqual(res["patient_id"], "patient_test_001")

        # Assign care team
        ct_payload = {
            "clinician_id": clinician_id,
            "patient_id": "patient_test_001",
            "role_description": "Bác sĩ điều trị chính"
        }
        status, res = self.router.handle_request("POST", "/api/admin/care-team", {}, ct_payload, token=admin_token)
        self.assertEqual(status, 201)
        care_team_id = res["care_team_id"]

        # Check care team in doctors list
        status, res = self.router.handle_request("GET", "/api/admin/doctors", {}, {}, token=admin_token)
        self.assertEqual(status, 200)
        self.assertEqual(len(res["doctors"]), 1)
        doc_entry = res["doctors"][0]
        self.assertEqual(len(doc_entry["care_teams"]), 1)
        self.assertEqual(doc_entry["care_teams"][0]["patient_id"], "patient_test_001")

        # Gỡ care team
        status, res = self.router.handle_request("DELETE", f"/api/admin/care-team/{care_team_id}", {}, {}, token=admin_token)
        self.assertEqual(status, 200)
        self.assertEqual(res["status"], "deleted")

        # 8. Admin deletes doctor
        status, res = self.router.handle_request("DELETE", f"/api/admin/doctors/{clinician_id}", {}, {}, token=admin_token)
        self.assertEqual(status, 200)
        self.assertEqual(res["status"], "deleted")

        # Check list is now empty again
        status, res = self.router.handle_request("GET", "/api/admin/doctors", {}, {}, token=admin_token)
        self.assertEqual(status, 200)
        self.assertEqual(len(res["doctors"]), 0)

    def test_admin_caregiver_crud_lifecycle(self) -> None:
        _, login_data = self.router.handle_request(
            "POST", "/api/auth/login", {},
            {"email": "admin@unifiedcare.local", "password": "admin123"}
        )
        admin_token = login_data["token"]

        # 1. Caregiver list initially empty
        status, res = self.router.handle_request("GET", "/api/admin/caregivers", {}, {}, token=admin_token)
        self.assertEqual(status, 200)
        self.assertEqual(len(res["caregivers"]), 0)

        # 2. Create caregiver
        cg_payload = {
            "full_name": "Trần Thị Mai (Con gái)",
            "email": "contact-3956d4d4@example.com",
            "password": "caregiverPass123",
            "phone": "0987654321",
            "relationship_desc": "Con gái"
        }
        status, res = self.router.handle_request("POST", "/api/admin/caregivers", {}, cg_payload, token=admin_token)
        self.assertEqual(status, 201)
        caregiver_id = res["caregiver_id"]
        user_id = res["user_id"]
        self.assertIn("contact-3956d4d4@example.com", res["email"])

        # 3. Get detail
        status, res = self.router.handle_request("GET", f"/api/admin/caregivers/{caregiver_id}", {}, {}, token=admin_token)
        self.assertEqual(status, 200)
        self.assertEqual(res["caregiver"]["full_name"], "Trần Thị Mai (Con gái)")

        # 4. Update caregiver
        update_payload = {
            "full_name": "Trần Thị Thanh Mai",
            "phone": "0988889999",
            "relationship_desc": "Con gái trưởng"
        }
        status, res = self.router.handle_request("POST", f"/api/admin/caregivers/{caregiver_id}", {}, update_payload, token=admin_token)
        self.assertEqual(status, 200)
        self.assertEqual(res["status"], "updated")

        # 5. Verify caregiver login with original password
        status, login_res = self.router.handle_request(
            "POST", "/api/auth/login", {},
            {"email": "contact-3956d4d4@example.com", "password": "caregiverPass123", "expected_role": "caregiver"}
        )
        self.assertEqual(status, 200)
        self.assertEqual(login_res["user"]["full_name"], "Trần Thị Thanh Mai")

        # 6. Reset password
        status, res = self.router.handle_request(
            "POST", f"/api/admin/caregivers/{caregiver_id}/reset-password", {},
            {"new_password": "NewCarePassword456"}, token=admin_token
        )
        self.assertEqual(status, 200)
        self.assertEqual(res["status"], "password_reset")

        # Verify login with new password
        status, _ = self.router.handle_request(
            "POST", "/api/auth/login", {},
            {"email": "contact-3956d4d4@example.com", "password": "NewCarePassword456", "expected_role": "caregiver"}
        )
        self.assertEqual(status, 200)

        # 7. Toggle status
        status, res = self.router.handle_request(
            "POST", f"/api/admin/caregivers/{caregiver_id}/toggle-status", {},
            {"is_active": 0}, token=admin_token
        )
        self.assertEqual(status, 200)
        self.assertEqual(res["is_active"], 0)

        # Verify locked account cannot log in
        status, login_locked = self.router.handle_request(
            "POST", "/api/auth/login", {},
            {"email": "contact-3956d4d4@example.com", "password": "NewCarePassword456", "expected_role": "caregiver"}
        )
        self.assertEqual(status, 401)

        # 8. Delete caregiver
        status, res = self.router.handle_request("DELETE", f"/api/admin/caregivers/{caregiver_id}", {}, {}, token=admin_token)
        self.assertEqual(status, 200)
        self.assertEqual(res["status"], "deleted")

        # 9. List empty again
        status, res = self.router.handle_request("GET", "/api/admin/caregivers", {}, {}, token=admin_token)
        self.assertEqual(status, 200)
        self.assertEqual(len(res["caregivers"]), 0)

    def test_admin_patient_crud_and_family_linking(self) -> None:
        _, login_data = self.router.handle_request(
            "POST", "/api/auth/login", {},
            {"email": "admin@unifiedcare.local", "password": "admin123"}
        )
        admin_token = login_data["token"]

        # 1. Create Patient
        pat_payload = {
            "patient_id": "patient_elderly_888",
            "full_name": "Cụ Lê Văn Ba",
            "gender": "Nam",
            "date_of_birth": "1945-03-15",
            "notes": "Tiền sử đột quỵ nhẹ, cần hỗ trợ vận động"
        }
        status, res = self.router.handle_request("POST", "/api/admin/patients", {}, pat_payload, token=admin_token)
        self.assertEqual(status, 201)
        self.assertEqual(res["patient_id"], "patient_elderly_888")

        # 2. Create Caregiver
        cg_payload = {
            "full_name": "Lê Văn Hùng (Con trai)",
            "email": "contact-4088ae39@example.com",
            "password": "hungPass123",
            "phone": "0911223344",
            "relationship_desc": "Con trai"
        }
        status, res = self.router.handle_request("POST", "/api/admin/caregivers", {}, cg_payload, token=admin_token)
        self.assertEqual(status, 201)
        caregiver_id = res["caregiver_id"]

        # 3. Link Patient and Caregiver
        link_payload = {
            "patient_id": "patient_elderly_888",
            "caregiver_id": caregiver_id,
            "relationship_type": "Con trai",
            "is_primary": 1,
            "can_edit_schedule": 1,
            "can_view_health": 1
        }
        status, res = self.router.handle_request("POST", "/api/admin/patient-caregivers", {}, link_payload, token=admin_token)
        self.assertEqual(status, 201)
        self.assertEqual(res["status"], "created")
        pc_id = res["patient_caregiver_id"]

        # 4. Verify in Patients List and Detail
        status, res = self.router.handle_request("GET", "/api/admin/patients", {}, {}, token=admin_token)
        self.assertEqual(status, 200)
        p = next(x for x in res["patients"] if x["patient_id"] == "patient_elderly_888")
        self.assertEqual(len(p["caregivers"]), 1)
        self.assertEqual(p["caregivers"][0]["caregiver_name"], "Lê Văn Hùng (Con trai)")

        # 5. Verify in Caregivers List
        status, res = self.router.handle_request("GET", "/api/admin/caregivers", {}, {}, token=admin_token)
        self.assertEqual(status, 200)
        c = next(x for x in res["caregivers"] if x["caregiver_id"] == caregiver_id)
        self.assertEqual(len(c["patients"]), 1)
        self.assertEqual(c["patients"][0]["patient_name"], "Cụ Lê Văn Ba")

        # 6. Update patient info
        status, res = self.router.handle_request(
            "POST", "/api/admin/patients/patient_elderly_888", {},
            {"full_name": "Cụ Lê Văn Ba (Thượng thọ)", "notes": "Đã phục hồi tốt"}, token=admin_token
        )
        self.assertEqual(status, 200)
        self.assertEqual(res["status"], "updated")

        # 7. Toggle patient status
        status, res = self.router.handle_request(
            "POST", "/api/admin/patients/patient_elderly_888/toggle-status", {},
            {"status": "INACTIVE"}, token=admin_token
        )
        self.assertEqual(status, 200)
        self.assertEqual(res["patient_status"], "INACTIVE")

        # 8. Unlink Caregiver
        status, res = self.router.handle_request(
            "DELETE", f"/api/admin/patient-caregivers/{pc_id}", {}, {}, token=admin_token
        )
        self.assertEqual(status, 200)
        self.assertEqual(res["status"], "unlinked")

        # 9. Delete Patient
        status, res = self.router.handle_request("DELETE", "/api/admin/patients/patient_elderly_888", {}, {}, token=admin_token)
        self.assertEqual(status, 200)
        self.assertEqual(res["status"], "deleted")

        # 10. Clean up caregiver
        self.router.handle_request("DELETE", f"/api/admin/caregivers/{caregiver_id}", {}, {}, token=admin_token)

    def test_admin_stats_endpoint(self) -> None:
        _, login_data = self.router.handle_request(
            "POST", "/api/auth/login", {},
            {"email": "admin@unifiedcare.local", "password": "admin123"}
        )
        admin_token = login_data["token"]

        status, res = self.router.handle_request("GET", "/api/admin/stats", {}, {}, token=admin_token)
        self.assertEqual(status, 200)
        self.assertIn("stats", res)
        stats = res["stats"]
        self.assertIn("total_doctors", stats)
        self.assertIn("total_patients", stats)
        self.assertIn("total_caregivers", stats)
        self.assertIn("total_care_teams", stats)
        self.assertIn("total_patient_caregivers", stats)


    def test_caregiver_registration_and_admin_sync(self) -> None:
        # 1. Login as admin
        _, login_data = self.router.handle_request(
            "POST", "/api/auth/login", {},
            {"email": "admin@unifiedcare.local", "password": "admin123"}
        )
        admin_token = login_data["token"]

        # 0. Seed a test patient
        with self.db.connect() as conn:
            conn.execute(
                """
                INSERT INTO patients (patient_id, full_name, date_of_birth, gender, status, notes)
                VALUES ('patient_001', 'Cụ Hoàng Anh', '1950-01-01', 'Nam', 'ACTIVE', 'Tăng huyết áp')
                """
            )
            conn.commit()

        # Check initial caregiver count
        status, init_cg = self.router.handle_request("GET", "/api/admin/caregivers", {}, {}, token=admin_token)
        self.assertEqual(status, 200)
        initial_count = len(init_cg["caregivers"])

        # 2. Caregiver registers via public auth endpoint
        reg_payload = {
            "email": "nguoigia_con@example.com",
            "password": "Password@123",
            "full_name": "Trần Văn Con",
            "phone": "0987654321",
            "relationship_desc": "Con trai",
        }
        status_reg, res_reg = self.router.handle_request("POST", "/api/auth/register", {}, reg_payload)
        self.assertEqual(status_reg, 201)
        self.assertEqual(res_reg["status"], "registered")
        self.assertEqual(res_reg["user"]["email"], "nguoigia_con@example.com")

        # Verify outbox event 'caregiver.registered' was created
        with self.db.connect() as conn:
            outbox_ev = conn.fetchone(
                "SELECT * FROM outbox_events WHERE event_type = 'caregiver.registered' ORDER BY occurred_at DESC LIMIT 1"
            )
            self.assertIsNotNone(outbox_ev)
            payload = json.loads(outbox_ev["payload_minimal"])
            self.assertEqual(payload["email"], "nguoigia_con@example.com")

        # 3. Admin queries caregivers list - newly registered caregiver MUST appear immediately
        status, cg_res = self.router.handle_request("GET", "/api/admin/caregivers", {}, {}, token=admin_token)
        self.assertEqual(status, 200)
        self.assertGreaterEqual(len(cg_res["caregivers"]), initial_count + 1)
        found = any(c["email"] == "nguoigia_con@example.com" for c in cg_res["caregivers"])
        self.assertTrue(found, "Newly registered caregiver should appear in Admin Portal caregivers list")

        # 4. Trigger Admin sync accounts endpoint (/api/admin/sync-accounts)
        status_sync, res_sync = self.router.handle_request("POST", "/api/admin/sync-accounts", {}, {}, token=admin_token)
        self.assertEqual(status_sync, 200)
        self.assertEqual(res_sync["status"], "synced")
        self.assertIn("stats", res_sync)
        self.assertGreaterEqual(res_sync["stats"]["total_caregivers"], 1)

        # Verify outbox event 'system.accounts_synced' was generated
        with self.db.connect() as conn:
            sync_ev = conn.fetchone(
                "SELECT * FROM outbox_events WHERE event_type = 'system.accounts_synced' ORDER BY occurred_at DESC LIMIT 1"
            )
            self.assertIsNotNone(sync_ev)

    def test_caregiver_multi_identifier_login_and_diagnostics(self) -> None:
        """Verify caregiver can login by email, phone, caregiver_id, patient_id with exact diagnostic messages."""
        now_iso = datetime.now(timezone.utc).isoformat()
        with self.db.connect() as conn:
            # Patient
            conn.execute(
                """
                INSERT INTO users (user_id, email, phone, full_name, password_hash, role, is_active, created_at)
                VALUES ('user_pat_100', 'patient_100@unifiedcare.local', '', 'Cụ Nguyễn Văn C', 'pbkdf2:demo', 'patient', 1, ?)
                """,
                (now_iso,),
            )
            conn.execute(
                """
                INSERT INTO patients (patient_id, user_id, full_name, date_of_birth, gender, timezone, status)
                VALUES ('patient_100', 'user_pat_100', 'Cụ Nguyễn Văn C', '1945-05-05', 'Nam', 'Asia/Ho_Chi_Minh', 'ACTIVE')
                """
            )
            conn.execute(
                """
                INSERT INTO legacy_patient_map (legacy_id, source_system, patient_id)
                VALUES ('100', 'camera_face', 'patient_100')
                """
            )
            # Caregiver
            conn.execute(
                """
                INSERT INTO users (user_id, email, phone, full_name, password_hash, role, is_active, created_at)
                VALUES ('user_care_100', 'cg100@example.com', '0912345678', 'Nguyễn Thị D', 'pbkdf2:mypassword', 'caregiver', 1, ?)
                """,
                (now_iso,),
            )
            conn.execute(
                """
                INSERT INTO caregivers (caregiver_id, user_id, email_verified, phone_verified, relationship_desc)
                VALUES ('cg_100', 'user_care_100', 1, 1, 'Con gái')
                """
            )
            conn.execute(
                """
                INSERT INTO patient_caregivers (patient_caregiver_id, patient_id, caregiver_id, relationship_type, is_primary, notification_priority, can_edit_schedule, can_view_health, consent_status, valid_from, valid_to, is_active)
                VALUES ('pc_100', 'patient_100', 'cg_100', 'Con gái', 1, 1, 1, 1, 'GRANTED', '2020-01-01', '2099-01-01', 1)
                """
            )
            conn.commit()

        # 1. Login by Email
        s, res = self.router.handle_request("POST", "/api/auth/login", {}, {"email": "cg100@example.com", "password": "mypassword", "expected_role": "caregiver"})
        self.assertEqual(s, 200)
        self.assertEqual(res["user"]["email"], "cg100@example.com")

        # 2. Login by Phone
        s, res = self.router.handle_request("POST", "/api/auth/login", {}, {"email": "0912345678", "password": "mypassword", "expected_role": "caregiver"})
        self.assertEqual(s, 200)

        # 3. Login by caregiver_id
        s, res = self.router.handle_request("POST", "/api/auth/login", {}, {"email": "cg_100", "password": "mypassword", "expected_role": "caregiver"})
        self.assertEqual(s, 200)

        # 4. Login by linked patient_id
        s, res = self.router.handle_request("POST", "/api/auth/login", {}, {"email": "patient_100", "password": "mypassword", "expected_role": "caregiver"})
        self.assertEqual(s, 200)

        # 5. Login by linked patient numeric legacy id
        s, res = self.router.handle_request("POST", "/api/auth/login", {}, {"email": "100", "password": "mypassword", "expected_role": "caregiver"})
        self.assertEqual(s, 200)

        # 6. Wrong password must return accurate diagnostic error (NEVER claim account does not exist)
        s, res = self.router.handle_request("POST", "/api/auth/login", {}, {"email": "cg100@example.com", "password": "wrongpassword", "expected_role": "caregiver"})
        self.assertEqual(s, 401)
        self.assertIn("Mật khẩu không chính xác", res["error"])

        # 7. Non-existent account returns appropriate missing error
        s, res = self.router.handle_request("POST", "/api/auth/login", {}, {"email": "nobody@example.com", "password": "123", "expected_role": "caregiver"})
        self.assertEqual(s, 401)
        self.assertIn("chưa tồn tại trong hệ thống", res["error"])

    def test_unrestricted_admin_deletion_of_all_account_types_with_foreign_keys(self) -> None:
        """Verify Admin can delete ANY account (Doctor, Caregiver, Patient) even when referenced by PK/FK."""
        # Get admin token
        _, res_adm = self.router.handle_request("POST", "/api/auth/login", {}, {"email": "admin@unifiedcare.local", "password": "admin123", "expected_role": "admin"})
        adm_token = res_adm["token"]

        now_iso = datetime.now(timezone.utc).isoformat()
        with self.db.connect() as conn:
            # Create Doctor
            conn.execute(
                """
                INSERT INTO users (user_id, email, phone, full_name, password_hash, role, is_active, created_at)
                VALUES ('user_doc_test', 'contact-534a5719@example.com', '0999999999', 'BS. Test', 'pbkdf2:demo', 'clinician', 1, ?)
                """,
                (now_iso,),
            )
            conn.execute(
                """
                INSERT INTO clinicians (clinician_id, user_id, workplace, specialty, license_number, is_verified, is_simulation)
                VALUES ('cl_test', 'user_doc_test', 'BV Test', 'Tim mạch', 'CCHN-TEST', 1, 0)
                """
            )
            # Create Patient
            conn.execute(
                """
                INSERT INTO users (user_id, email, phone, full_name, password_hash, role, is_active, created_at)
                VALUES ('user_pat_test', 'pat_test@unifiedcare.local', '', 'Cụ Test', 'pbkdf2:demo', 'patient', 1, ?)
                """,
                (now_iso,),
            )
            conn.execute(
                """
                INSERT INTO patients (patient_id, user_id, full_name, date_of_birth, gender, timezone, status)
                VALUES ('pat_test_01', 'user_pat_test', 'Cụ Test', '1940-01-01', 'Nam', 'Asia/Ho_Chi_Minh', 'ACTIVE')
                """
            )
            # Create Caregiver
            conn.execute(
                """
                INSERT INTO users (user_id, email, phone, full_name, password_hash, role, is_active, created_at)
                VALUES ('user_cg_test', 'cg_test@example.com', '0988776655', 'Người nhà Test', 'pbkdf2:demo', 'caregiver', 1, ?)
                """,
                (now_iso,),
            )
            conn.execute(
                """
                INSERT INTO caregivers (caregiver_id, user_id, email_verified, phone_verified, relationship_desc)
                VALUES ('cg_test_01', 'user_cg_test', 1, 1, 'Con')
                """
            )
            # Link patient & caregiver
            conn.execute(
                """
                INSERT INTO patient_caregivers (patient_caregiver_id, patient_id, caregiver_id, relationship_type, is_primary, notification_priority, can_edit_schedule, can_view_health, consent_status, valid_from, valid_to, is_active)
                VALUES ('pc_test_01', 'pat_test_01', 'cg_test_01', 'Con', 1, 1, 1, 1, 'GRANTED', '2020-01-01', '2099-01-01', 1)
                """
            )
            # Link care team
            conn.execute(
                """
                INSERT INTO care_team (care_team_id, patient_id, clinician_id, role_description, valid_from, valid_to, consent_status, is_active)
                VALUES ('ct_test_01', 'pat_test_01', 'cl_test', 'Bác sĩ chính', '2020-01-01', '2099-01-01', 'GRANTED', 1)
                """
            )
            # Add Caregiver Report referencing caregiver_id
            conn.execute(
                """
                INSERT INTO caregiver_reports (report_id, patient_id, caregiver_id, description, observed_at, severity, status, attachments, created_at)
                VALUES ('rep_test_01', 'pat_test_01', 'cg_test_01', 'Bệnh nhân chóng mặt', ?, 'NORMAL', 'NEW', '[]', ?)
                """,
                (now_iso, now_iso),
            )
            conn.commit()

        # 1. Admin deletes caregiver with active FKs in caregiver_reports and patient_caregivers
        s_cg, res_cg = self.router.handle_request("DELETE", "/api/admin/caregivers/cg_test_01", {}, {}, token=adm_token)
        self.assertEqual(s_cg, 200)
        self.assertEqual(res_cg["status"], "deleted")
        with self.db.connect() as conn:
            self.assertIsNone(conn.fetchone("SELECT * FROM caregivers WHERE caregiver_id = 'cg_test_01'"))
            self.assertIsNone(conn.fetchone("SELECT * FROM users WHERE user_id = 'user_cg_test'"))

        # 2. Admin deletes doctor with active FKs in care_team
        s_doc, res_doc = self.router.handle_request("DELETE", "/api/admin/doctors/cl_test", {}, {}, token=adm_token)
        self.assertEqual(s_doc, 200)
        self.assertEqual(res_doc["status"], "deleted")
        with self.db.connect() as conn:
            self.assertIsNone(conn.fetchone("SELECT * FROM clinicians WHERE clinician_id = 'cl_test'"))
            self.assertIsNone(conn.fetchone("SELECT * FROM users WHERE user_id = 'user_doc_test'"))

        # 3. Admin deletes patient
        s_pat, res_pat = self.router.handle_request("DELETE", "/api/admin/patients/pat_test_01", {}, {}, token=adm_token)
        self.assertEqual(s_pat, 200)
        self.assertEqual(res_pat["status"], "deleted")
        with self.db.connect() as conn:
            self.assertIsNone(conn.fetchone("SELECT * FROM patients WHERE patient_id = 'pat_test_01'"))
            self.assertIsNone(conn.fetchone("SELECT * FROM users WHERE user_id = 'user_pat_test'"))


if __name__ == "__main__":
    unittest.main()

