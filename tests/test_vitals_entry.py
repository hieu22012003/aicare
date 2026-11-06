import os
os.environ["AI_CARE_TEST_MODE"] = "1"
os.environ["QT_QPA_PLATFORM"] = "offscreen"

import unittest
from datetime import datetime, timezone
from pathlib import Path
from PyQt5.QtWidgets import QApplication
import sys

from shared.database.connection import UnifiedDatabaseManager
from features.web_portal.auth import AuthService
from features.web_portal.api_router import ApiRouter
from apps.desktop.today_todo_widget import TodayTodoWidget


class CaregiverVitalsEntryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication(sys.argv)

    def setUp(self):
        self.db = UnifiedDatabaseManager("data/unified_care.sqlite3")
        self.auth = AuthService(self.db)
        self.router = ApiRouter(self.db, self.auth)

        # Ensure patient_001 exists for test
        with self.db.connect() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO users (user_id, email, phone, full_name, password_hash, role, is_active, created_at) "
                "VALUES ('user_pat_001', 'pat001@test.local', '0901111222', 'Cụ Hoàng Anh', 'pbkdf2:demo', 'patient', 1, '2026-01-01T00:00:00Z')"
            )
            conn.execute(
                "INSERT OR IGNORE INTO patients (patient_id, user_id, full_name, status) VALUES ('patient_001', 'user_pat_001', 'Cụ Hoàng Anh', 'ACTIVE')"
            )
            conn.commit()

        # Authenticate caregiver, ensuring test user exists if needed
        self.caregiver = self.auth.authenticate_user("contact-9e2e85e7@example.com", "123456") or self.auth.authenticate_user("contact-927f84f8@example.com", "Caregiver@123")
        if not self.caregiver:
            self.auth.register_caregiver(
                full_name="Hiu Caregiver",
                email="contact-927f84f8@example.com",
                phone="0988776655",
                password="Caregiver@123",
                patient_id="patient_001",
            )
            self.caregiver = self.auth.authenticate_user("contact-927f84f8@example.com", "Caregiver@123")
        self.assertIsNotNone(self.caregiver)
        self.patient_id = "patient_001"

        # Ensure patient_caregivers association exists with view/edit rights
        with self.db.connect() as conn:
            cg_row = conn.fetchone("SELECT caregiver_id FROM caregivers WHERE user_id = ?", (self.caregiver["user_id"],))
            if not cg_row:
                cg_id = f"cg_{self.caregiver['user_id']}"
                conn.execute(
                    "INSERT OR IGNORE INTO caregivers (caregiver_id, user_id, email_verified, phone_verified) VALUES (?, ?, 1, 1)",
                    (cg_id, self.caregiver["user_id"]),
                )
            else:
                cg_id = cg_row["caregiver_id"]
            now_iso = datetime.now(timezone.utc).isoformat()
            future_iso = "2036-12-31T23:59:59Z"
            conn.execute(
                """
                INSERT OR IGNORE INTO patient_caregivers (patient_caregiver_id, patient_id, caregiver_id, relationship_type, is_primary, can_view_health, can_edit_schedule, valid_from, valid_to, is_active)
                VALUES (?, ?, ?, 'Người thân', 1, 1, 1, ?, ?, 1)
                """,
                (f"pcg_{cg_id}_{self.patient_id}", self.patient_id, cg_id, now_iso, future_iso),
            )
            conn.commit()

        self.token = self.auth.create_session(self.caregiver)

    def test_caregiver_can_enter_and_read_vitals(self):
        req_id = f"test_req_vitals_{os.urandom(4).hex()}"
        body = {
            "client_request_id": req_id,
            "measured_at": datetime.now(timezone.utc).isoformat(),
            "source_type": "manual_caregiver",
            "device_label": "Máy đo huyết áp Omron & SpO2",
            "notes": "Người già sau khi tập thể dục nhẹ, cảm giác ổn định",
            "items": [
                {"code": "blood_pressure_systolic", "value": 125.0, "unit": "mmHg"},
                {"code": "blood_pressure_diastolic", "value": 82.0, "unit": "mmHg"},
                {"code": "heart_rate", "value": 76.0, "unit": "bpm"},
                {"code": "spo2", "value": 98.0, "unit": "%"},
                {"code": "blood_glucose", "value": 108.0, "unit": "mg/dL"},
                {"code": "temperature", "value": 36.7, "unit": "°C"},
            ],
        }

        # 1. POST observation
        status, resp = self.router.handle_request("POST", f"/api/patients/{self.patient_id}/observations", {}, body, self.token)
        self.assertIn(status, (200, 201))
        self.assertIn("group_id", resp)

        # 2. Idempotency check
        status_dup, resp_dup = self.router.handle_request("POST", f"/api/patients/{self.patient_id}/observations", {}, body, self.token)
        self.assertEqual(status_dup, 200)
        self.assertEqual(resp_dup.get("status"), "duplicate_acknowledged")

        # 3. GET observation type=all
        status_get, resp_get = self.router.handle_request(
            "GET", f"/api/patients/{self.patient_id}/observations", {"type": "all", "limit": "100"}, {}, self.token
        )
        self.assertEqual(status_get, 200)
        measurements = resp_get.get("measurements", [])
        self.assertGreater(len(measurements), 0)

        latest = next((m for m in measurements if m.get("group_id") == resp["group_id"]), measurements[0])
        self.assertEqual(latest["systolic"], 125.0)
        self.assertEqual(latest["diastolic"], 82.0)
        self.assertEqual(latest["heart_rate"], 76.0)
        self.assertEqual(latest["spo2"], 98.0)
        self.assertEqual(latest["blood_glucose"], 108.0)
        self.assertEqual(latest["temperature"], 36.7)
        self.assertIn("Người già sau khi tập thể dục nhẹ", latest["notes"])

    def test_validation_fails_on_empty_items(self):
        body = {
            "client_request_id": f"test_err_{os.urandom(4).hex()}",
            "items": [],
        }
        status, resp = self.router.handle_request("POST", f"/api/patients/{self.patient_id}/observations", {}, body, self.token)
        self.assertEqual(status, 400)
        self.assertIn("error", resp)

    def test_desktop_today_todo_widget_reflects_vitals(self):
        widget = TodayTodoWidget(self.db, patient_id=self.patient_id)
        widget.refresh_data()
        layout = widget.vitals_content_layout
        texts = [layout.itemAt(i).widget().text() for i in range(layout.count()) if layout.itemAt(i).widget()]
        self.assertTrue(any("mmHg" in t for t in texts))

    def test_caregiver_portal_html_contains_vitals_tab(self):
        html_path = Path("features/web_portal/templates/portal_caregiver.html")
        self.assertTrue(html_path.exists())
        content = html_path.read_text(encoding="utf-8")
        self.assertIn("switchTab('vitals-entry')", content)
        self.assertIn('id="tab-vitals-entry"', content)
        self.assertIn('id="vitalMeasuredAt"', content)
        self.assertIn('id="vitalSys"', content)
        self.assertIn('id="vitalDia"', content)
        self.assertIn('id="vitalSpo2"', content)
        self.assertIn('id="vitalGlucose"', content)
        self.assertIn('id="vitalTemp"', content)
        self.assertIn('id="tblVitalsHistory"', content)
        self.assertIn("handleSaveVitals", content)
        self.assertIn("loadVitalsHistoryTable", content)

