"""
Test Suite for Caregiver Rehabilitation Configuration & Real-time Elderly App Sync.
Validates:
1. Caregiver API: Fetching plans auto-seeds defaults (arm_raise, sit_to_stand, marching).
2. Caregiver API: Modifying target count and reminder time increments version, updates DB, and emits outbox events.
3. Caregiver API: Concurrency detection (409 Conflict) when version is stale.
4. Caregiver API: Master reset/init endpoint.
5. Desktop App Sync: DashboardWindow dynamically updates button labels, routine steps, and routine_time.
6. Desktop App Sync: TodayTodoWidget dynamically reflects new reminder time and target counts.
7. Vision Adapter Sync: RehabilitationEngine and RehabilitationAdapter configure target_reps dynamically.
"""

import sys
import unittest
from datetime import date, datetime, timezone
from unittest.mock import MagicMock

from PyQt5.QtWidgets import QApplication

# Ensure QApplication singleton
app = QApplication.instance()
if app is None:
    app = QApplication(sys.argv)

from features.exercise_rehab.rehabilitation_adapter import RehabilitationAdapter
from apps.desktop.dashboard import DashboardWindow
from apps.desktop.today_todo_widget import TodayTodoWidget
from shared.database.connection import UnifiedDatabaseManager
from shared.database.migrator import Migrator
from shared.database.importer import DataImporter
from features.exercise_rehab.rehab_session_adapter import ensure_default_rehab_plans
from features.web_portal.api_router import ApiRouter
from features.web_portal.auth import AuthService


class TestCaregiverRehabConfigSync(unittest.TestCase):
    def setUp(self) -> None:
        self.db = UnifiedDatabaseManager()
        Migrator(self.db).apply_all()
        DataImporter(self.db).import_all()
        self.auth = AuthService(self.db)
        self.router = ApiRouter(self.db, self.auth)
        self.patient_id = "patient_001"
        self.caregiver_user_id = "user_care_001"

        # Ensure clean state for test
        with self.db.connect() as conn:
            conn.execute(
                """
                UPDATE rehab_plans
                SET target_count = CASE WHEN exercise_id = 'marching' THEN 15 ELSE 10 END,
                    local_reminder_time = '16:00',
                    version = 1
                WHERE patient_id = ?
                """,
                (self.patient_id,),
            )
            ensure_default_rehab_plans(conn, self.patient_id, default_time="16:00")
            conn.commit()

    def test_fetch_plans_auto_seeds_defaults(self) -> None:
        """Verify GET /api/patients/{id}/exercise-plans returns 3 standard exercises."""
        status_code, resp = self.router._get_exercise_plans(self.patient_id)
        self.assertEqual(status_code, 200)
        plans = resp.get("plans", [])
        self.assertEqual(len(plans), 3)

        plan_map = {p["exercise_id"]: p for p in plans}
        self.assertIn("arm_raise", plan_map)
        self.assertIn("sit_to_stand", plan_map)
        self.assertIn("marching", plan_map)
        self.assertEqual(plan_map["arm_raise"]["target_count"], 10)
        self.assertEqual(plan_map["marching"]["target_count"], 15)
        self.assertEqual(plan_map["arm_raise"]["local_reminder_time"], "16:00")

    def test_update_plan_target_and_time(self) -> None:
        """Verify caregiver can update target_count and local_reminder_time with version bump."""
        # 1. Fetch current arm_raise plan
        _, resp = self.router._get_exercise_plans(self.patient_id)
        arm_plan = next(p for p in resp["plans"] if p["exercise_id"] == "arm_raise")
        plan_id = arm_plan["plan_id"]
        v1 = arm_plan["version"]

        # 2. Update to 14 reps at 15:30
        status_code, update_resp = self.router._update_exercise_plan(
            self.patient_id,
            plan_id,
            self.caregiver_user_id,
            {
                "target_count": 14,
                "local_reminder_time": "15:30",
                "expected_version": v1,
            },
        )
        self.assertEqual(status_code, 200)
        self.assertEqual(update_resp["version"], v1 + 1)
        self.assertEqual(update_resp["target_count"], 14)
        self.assertEqual(update_resp["local_reminder_time"], "15:30")

        # 3. Verify DB record and version history
        with self.db.connect() as conn:
            plan_row = conn.fetchone("SELECT * FROM rehab_plans WHERE plan_id = ?", (plan_id,))
            self.assertEqual(plan_row["target_count"], 14)
            self.assertEqual(plan_row["local_reminder_time"], "15:30")
            self.assertEqual(plan_row["version"], v1 + 1)

            # Check outbox event
            event = conn.fetchone(
                "SELECT * FROM outbox_events WHERE aggregate_id = ? AND event_type = 'rehab.plan_updated' ORDER BY event_id DESC LIMIT 1",
                (plan_id,),
            )
            self.assertIsNotNone(event)

    def test_concurrency_conflict_409(self) -> None:
        """Verify conflicting edits with stale expected_version return HTTP 409."""
        _, resp = self.router._get_exercise_plans(self.patient_id)
        arm_plan = next(p for p in resp["plans"] if p["exercise_id"] == "arm_raise")
        plan_id = arm_plan["plan_id"]
        stale_version = arm_plan["version"] - 1

        status_code, err_resp = self.router._update_exercise_plan(
            self.patient_id,
            plan_id,
            self.caregiver_user_id,
            {
                "target_count": 20,
                "expected_version": stale_version,
            },
        )
        self.assertEqual(status_code, 409)
        self.assertIn("Xung đột", err_resp.get("error", ""))

    def test_reset_plans_endpoint(self) -> None:
        """Verify caregiver can reset/reinitialize plans via _reset_exercise_plans."""
        status_code, resp = self.router._reset_exercise_plans(
            self.patient_id,
            self.caregiver_user_id,
            {"local_reminder_time": "17:00"},
        )
        self.assertEqual(status_code, 200)
        self.assertTrue(len(resp.get("plans", [])) >= 3)

    def test_desktop_app_live_sync_and_ui_update(self) -> None:
        """Verify Desktop DashboardWindow and TodayTodoWidget dynamically update when DB plans change."""
        # 1. Update plans in DB
        with self.db.connect() as conn:
            conn.execute(
                """
                UPDATE rehab_plans
                SET target_count = 12, local_reminder_time = '15:45', version = version + 1
                WHERE patient_id = ? AND exercise_id = 'arm_raise'
                """,
                (self.patient_id,),
            )
            conn.execute(
                """
                UPDATE rehab_plans
                SET target_count = 18, local_reminder_time = '15:45', version = version + 1
                WHERE patient_id = ? AND exercise_id = 'marching'
                """,
                (self.patient_id,),
            )
            conn.commit()

        # 2. Instantiate DashboardWindow
        window = DashboardWindow()
        try:
            window.current_patient_id = self.patient_id
            window._load_rehab_plans_from_db(self.patient_id)

            # Check routine sequence steps updated
            arm_step = next(s for s in window.routine_steps if s["id"] == "arm_raise")
            march_step = next(s for s in window.routine_steps if s["id"] == "marching")
            self.assertEqual(arm_step["target"], 12)
            self.assertEqual(march_step["target"], 18)
            self.assertEqual(window.routine_time, "15:45")

            # Check button labels updated
            self.assertIn("12 lần", window.btn_ex_arm.text())
            self.assertIn("18 bước", window.btn_ex_march.text())
            self.assertIn("15:45", window.btn_start_routine.text())
            self.assertIn("12 VƯƠN TAY", window.btn_start_routine.text())

            # Check TodayTodoWidget updated
            todo = TodayTodoWidget(db=self.db, patient_id=self.patient_id)
            todo.refresh_data()
            self.assertIn("15:45", todo.rehab_card.title_label.text())

        finally:
            window.close()

    def test_vision_engine_target_reps(self) -> None:
        """Verify RehabilitationAdapter and RehabilitationEngine track custom target_reps."""
        adapter = RehabilitationAdapter()
        adapter.select_exercise("arm_raise", target_reps=25)
        metrics = adapter.engine.get_metrics()
        self.assertEqual(metrics["target_reps"], 25)

        # Dynamic setter test
        adapter.set_target_reps(30)
        metrics2 = adapter.engine.get_metrics()
        self.assertEqual(metrics2["target_reps"], 30)


if __name__ == "__main__":
    unittest.main()
