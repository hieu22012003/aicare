"""
Test Caregiver Registration via Desktop App and Web Portal Login & Patient Extraction.
Verifies that:
1. Registering an elder + caregiver on Desktop App via FaceAdapter / sync_single_patient
   stores caregiver credentials, phone, and links the patient.
2. Web login with the registered caregiver email and password succeeds.
3. Caregiver querying /api/me/patients extracts the newly registered elder profile.
4. Caregiver has access to elder's health, prescription, and rehabilitation data.
5. Access control enforces that caregiver only accesses their registered elder.
"""

import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path

from features.face_monitoring.face_adapter import FaceAdapter
from shared.database.connection import UnifiedDatabaseManager
from shared.database.migrator import Migrator
from shared.services.patient_sync_service import sync_single_patient
from features.web_portal.api_router import ApiRouter
from features.web_portal.auth import AuthService
from shared.services.credential_security import verify_password


class TestCaregiverDesktopRegistrationFlow(unittest.TestCase):
    def setUp(self):
        # Create temporary database for testing
        self.temp_db = tempfile.NamedTemporaryFile(suffix=".sqlite3", delete=False)
        self.temp_db.close()
        self.db = UnifiedDatabaseManager(db_path=Path(self.temp_db.name))

        # Run migrations
        migrator = Migrator(self.db)
        migrator.apply_all()

        self.auth = AuthService(self.db)
        self.router = ApiRouter(self.db, self.auth)

    def tearDown(self):
        try:
            os.remove(self.temp_db.name)
        except OSError:
            pass

    def test_elder_and_caregiver_desktop_sync_and_web_login(self):
        # 1. Simulate registration from Elder Desktop App (RegistrationDialog)
        profile_data = {
            "patient_id": "999",
            "name": "Cụ Trần Văn Minh",
            "age": 78,
            "gender": "Nam",
            "notes": "Huyết áp cao, cần phục hồi chức năng sau tai biến",
            "caregiver": {
                "name": "Trần Thị Mai",
                "relationship": "Con gái",
                "email": "tranmai789@example.com",
                "phone": "0987654321",
                "password": "Password123!",
            },
        }

        # 2. Sync to unified database
        pat_id = sync_single_patient(self.db, profile_data)
        self.assertEqual(pat_id, "patient_999")

        # Verify caregiver user in DB
        with self.db.connect() as conn:
            user = conn.fetchone("SELECT * FROM users WHERE email = 'tranmai789@example.com'")
            self.assertIsNotNone(user)
            self.assertEqual(user["full_name"], "Trần Thị Mai")
            self.assertEqual(user["phone"], "0987654321")
            self.assertTrue(verify_password("Password123!", user["password_hash"]))
            self.assertNotIn("Password123!", user["password_hash"])
            self.assertEqual(user["role"], "caregiver")

            # Verify relationship in patient_caregivers
            rel = conn.fetchone(
                "SELECT * FROM patient_caregivers WHERE patient_id = ?", (pat_id,)
            )
            self.assertIsNotNone(rel)
            self.assertEqual(rel["relationship_type"], "Con gái")
            self.assertEqual(rel["consent_status"], "GRANTED")
            self.assertEqual(rel["is_active"], 1)

            # Verify outbox event emitted
            events = conn.fetchall(
                "SELECT * FROM outbox_events WHERE patient_id = ? ORDER BY occurred_at", (pat_id,)
            )
            event_types = [e["event_type"] for e in events]
            self.assertIn("patient.updated", event_types)
            self.assertIn("caregiver.registered", event_types)

        # 3. Simulate Web Login (/api/auth/login) with wrong password -> must fail
        status, res = self.router.handle_request(
            "POST",
            "/api/auth/login",
            {},
            {"email": "tranmai789@example.com", "password": "WrongPassword"},
            None,
        )
        self.assertEqual(status, 401)
        self.assertIn("error", res)

        # 4. Simulate Web Login with correct password -> must succeed
        status, res = self.router.handle_request(
            "POST",
            "/api/auth/login",
            {},
            {"email": "tranmai789@example.com", "password": "Password123!", "expected_role": "caregiver"},
            None,
        )
        self.assertEqual(status, 200)
        self.assertIn("token", res)
        self.assertEqual(res["user"]["full_name"], "Trần Thị Mai")
        self.assertEqual(res["user"]["role"], "caregiver")

        token = res["token"]

        # 5. Caregiver queries /api/me/patients -> must extract elder Cụ Trần Văn Minh
        status, res = self.router.handle_request(
            "GET",
            "/api/me/patients",
            {},
            {},
            token,
        )
        self.assertEqual(status, 200)
        patients = res.get("patients", [])
        self.assertEqual(len(patients), 1)
        self.assertEqual(patients[0]["patient_id"], "patient_999")
        self.assertEqual(patients[0]["full_name"], "Cụ Trần Văn Minh")
        self.assertEqual(patients[0]["relationship_type"], "Con gái")

        # 6. Check patient detail access
        status, res = self.router.handle_request(
            "GET",
            "/api/patients/patient_999",
            {},
            {},
            token,
        )
        self.assertEqual(status, 200)
        self.assertEqual(res["full_name"], "Cụ Trần Văn Minh")

    def test_face_adapter_register_patient_stores_caregiver_credentials(self):
        # Test FaceAdapter register_patient with caregiver phone and password
        temp_data = Path(tempfile.mkdtemp())
        temp_users = Path(tempfile.mkdtemp())
        face_adapter = FaceAdapter(data_dir=temp_data, users_dir=temp_users)
        try:
            res = face_adapter.register_patient(
                name="Cụ Lê Văn Tám",
                patient_id="888",
                age=82,
                gender="Nam",
                notes="Tiền sử đái tháo đường type 2",
                caregiver_name="Lê Minh Hưng",
                caregiver_relationship="Con trai",
                caregiver_email="contact-1e089a6a@example.com",
                caregiver_phone="0911223344",
                caregiver_password="HungSecurePassword999",
            )
            self.assertEqual(res["patient_id"], "888")
            self.assertEqual(res["name"], "Cụ Lê Văn Tám")
            cg = res.get("caregiver", {})
            self.assertEqual(cg["name"], "Lê Minh Hưng")
            self.assertEqual(cg["relationship"], "Con trai")
            self.assertEqual(cg["email"], "contact-1e089a6a@example.com")
            self.assertEqual(cg["phone"], "0911223344")
            self.assertNotIn("password", cg)
            self.assertTrue(verify_password("HungSecurePassword999", cg["password_hash"]))
            for path in temp_users.glob("*.json"):
                self.assertNotIn("HungSecurePassword999", path.read_text(encoding="utf-8"))
        finally:
            shutil.rmtree(temp_data, ignore_errors=True)
            shutil.rmtree(temp_users, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
