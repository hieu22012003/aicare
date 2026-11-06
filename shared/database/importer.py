"""
Safe, Idempotent Data Importer and Legacy Migration Utility.
Merges fragmented SQLite and JSON databases into the Unified 3NF Relational Store.
Guarantees:
- Safe rerunability (running multiple times produces zero duplicate rows and zero errors)
- Foreign key integrity preservation
- Unambiguous patient isolation: patient_001 and patient_002 never mix
- Idempotent legacy ID mapping
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from shared.database.connection import UnifiedDatabaseManager

logger = logging.getLogger("DataImporter")

ROOT_DIR = Path(__file__).resolve().parents[2]
MED_STANDALONE_DB = ROOT_DIR / "data" / "medication_standalone.sqlite3"
HEALTH_STANDALONE_DB = ROOT_DIR / "data" / "health_standalone.sqlite3"
GAMES_DB = ROOT_DIR / "features" / "music_care" / "cognitive_games" / "database" / "cognitive_games.db"
USERS_DIR = ROOT_DIR / "data" / "users"


class DataImporter:
    def __init__(self, target_db: UnifiedDatabaseManager) -> None:
        self.db = target_db
        # Standardized patient mappings
        self.patient_id_map = {
            "patient_001": "patient_001",
            "pat-001": "patient_001",
            "1": "patient_001",
            "patient_002": "patient_002",
            "pat-002": "patient_002",
            "2": "patient_002",
        }
        self.user_id_map = {
            "usr-pat-001": "user_pat_001",
            "usr-pat-002": "user_pat_002",
            "usr-cg-001": "user_care_001",
            "usr-cg-002": "user_care_002",
            "cg-001": "caregiver_001",
            "cg-002": "caregiver_002",
            "doc-001": "clinician_001",
            "usr-doc-001": "user_doc_001",
        }

    def import_all(self, seed_sample_data: Optional[bool] = None) -> Dict[str, int]:
        """
        Run complete idempotent import across all legacy data sources.
        In production, only the single system administrator account is created.
        Sample test accounts are only seeded when running under pytest or when explicitly requested.
        """
        if seed_sample_data is None:
            seed_sample_data = bool(
                "pytest" in sys.modules
                or os.environ.get("PYTEST_CURRENT_TEST")
                or os.environ.get("AICARE_SEED_DEMO_DATA") == "1"
            )

        stats: Dict[str, int] = {}
        with self.db.connect() as conn:
            self._seed_core_actors(conn, seed_sample_data=seed_sample_data)
            if seed_sample_data:
                self._seed_legacy_map(conn)
                self._import_medication_standalone(conn)
                self._import_health_standalone(conn)
                self._import_cognitive_games(conn)
                self._seed_rehabilitation_plans_and_sessions(conn)
                self._seed_mood_and_emotions(conn)
                self._seed_caregiver_reports_and_recommendations(conn)
                self._seed_emergency_contacts(conn)
                self._seed_measurement_requests_and_plan_versions(conn)
            conn.commit()

        # Query row counts for audit report
        with self.db.connect() as conn:
            tables = [
                "users", "patients", "legacy_patient_map", "caregivers", "clinicians",
                "patient_caregivers", "care_team", "medications", "prescriptions",
                "prescription_items", "medication_schedules", "dose_occurrences",
                "dose_events", "alerts", "notification_deliveries", "observation_types",
                "measurement_groups", "health_observations", "symptom_entries",
                "clinician_reviews", "clinical_messages", "game_sessions", "game_results",
                "rehab_plans", "rehab_sessions", "rehab_events", "mood_checkins",
                "emotion_inferences", "caregiver_reports", "report_messages",
                "care_recommendations", "emergency_contacts", "emergency_contact_events",
                "measurement_requests", "exercise_plan_versions"
            ]
            for t in tables:
                row = conn.fetchone(f"SELECT COUNT(*) as cnt FROM {t}")
                stats[t] = row["cnt"] if row else 0

        return stats

    def _seed_core_actors(self, conn: Any, seed_sample_data: bool = False) -> None:
        """Seed default admin account, and optionally sample test actors."""
        now_iso = datetime.now(timezone.utc).isoformat()

        # Ensure the single system administrator account always exists
        admin_user = (
            "user_admin_001",
            "admin@unifiedcare.local",
            "0900000000",
            "Quản trị viên Hệ thống",
            "!",
            "admin",
            1,
            now_iso,
        )
        conn.execute(
            """
            INSERT INTO users (user_id, email, phone, full_name, password_hash, role, is_active, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                full_name=excluded.full_name,
                role=excluded.role,
                is_active=excluded.is_active
            """,
            admin_user,
        )

        if not seed_sample_data:
            return

        users = [
            ("user_pat_001", "hoanganh.patient@example.com", "0901111222", "Cụ Hoàng Anh", "", "patient", 1, now_iso),
            ("user_pat_002", "maihuong.patient@example.com", "0902222333", "Cụ Mai Hương", "", "patient", 1, now_iso),
            ("user_care_001", "tuan.caregiver@example.com", "0913333444", "Nguyễn Văn Tuấn", "!", "caregiver", 1, now_iso),
            ("user_care_002", "lan.caregiver@example.com", "0914444555", "Lê Thị Lan", "!", "caregiver", 1, now_iso),
            ("user_care_003", "binh.old@example.com", "0915555666", "Trần Văn Bình", "!", "caregiver", 1, now_iso),
            ("user_care_004", "contact-a2e402b0@example.com", "0987654321", "Người chăm sóc mẫu", "!", "caregiver", 1, now_iso),
            ("user_doc_001", "dr.an@laokhoa.example.com", "0988888999", "BS. Nguyễn Văn An", "!", "clinician", 1, now_iso),
            ("user_doc_002", "contact-4bcc03e4@example.com", "0977666555", "ThS.BS Phạm Mai", "!", "clinician", 1, now_iso),
            ("user_doc_003", "dr.cuong@laokhoa.example.com", "0989998877", "BS. Lê Văn Cường", "!", "clinician", 1, now_iso),
        ]
        for u in users:
            conn.execute(
                """
                INSERT INTO users (user_id, email, phone, full_name, password_hash, role, is_active, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    full_name=excluded.full_name,
                    role=excluded.role,
                    is_active=excluded.is_active
                """,
                u,
            )

        patients = [
            ("patient_001", "user_pat_001", "Cụ Hoàng Anh", "1951-04-15", "Nam", "Asia/Ho_Chi_Minh", "ACTIVE", "Tiền sử tăng huyết áp nhẹ, minh mẫn"),
            ("patient_002", "user_pat_002", "Cụ Mai Hương", "1948-09-20", "Nữ", "Asia/Ho_Chi_Minh", "ACTIVE", "Đái tháo đường type 2, cần nhắc nhở đều đặn"),
        ]
        for p in patients:
            conn.execute(
                """
                INSERT INTO patients (patient_id, user_id, full_name, date_of_birth, gender, timezone, status, notes)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(patient_id) DO UPDATE SET
                    full_name=excluded.full_name,
                    timezone=excluded.timezone,
                    status=excluded.status
                """,
                p,
            )

        caregivers = [
            ("caregiver_001", "user_care_001", 1, 1, "Con trai"),
            ("caregiver_002", "user_care_002", 1, 1, "Con gái"),
            ("caregiver_003", "user_care_003", 1, 1, "Người chăm sóc cũ"),
            ("caregiver_004", "user_care_004", 1, 1, "Con trai"),
        ]
        for c in caregivers:
            conn.execute(
                """
                INSERT INTO caregivers (caregiver_id, user_id, email_verified, phone_verified, relationship_desc)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(caregiver_id) DO UPDATE SET
                    email_verified=excluded.email_verified,
                    phone_verified=excluded.phone_verified
                """,
                c,
            )

        clinicians = [
            ("clinician_001", "user_doc_001", "Bệnh viện Lão khoa Trung Ương", "Lão khoa & Tim mạch", "CCHN-0012984", 1, 1),
            ("clinician_002", "user_doc_002", "Bệnh viện Bạch Mai", "Nội tiết & Chuyển hóa", "CCHN-004567-BYT", 1, 1),
            ("clinician_003", "user_doc_003", "Bệnh viện Lão khoa Trung Ương", "Khoa Nội Tim Mạch", "CCHN-009988-BYT", 1, 1),
        ]
        for cl in clinicians:
            conn.execute(
                """
                INSERT INTO clinicians (clinician_id, user_id, workplace, specialty, license_number, is_verified, is_simulation)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(clinician_id) DO UPDATE SET
                    workplace=excluded.workplace,
                    specialty=excluded.specialty,
                    is_simulation=excluded.is_simulation
                """,
                cl,
            )

        # Patient Caregivers: Caregiver 1 -> Patient 1 (ACTIVE), Caregiver 2 -> Patient 2 (ACTIVE), Caregiver 3 -> Patient 1 (EXPIRED)
        patient_caregivers = [
            ("pc_001", "patient_001", "caregiver_001", "Con trai", 1, 1, 1, 1, "GRANTED", "2024-01-01T00:00:00+00:00", "2099-12-31T23:59:59+00:00", 1),
            ("pc_002", "patient_002", "caregiver_002", "Con gái", 1, 1, 1, 1, "GRANTED", "2024-01-01T00:00:00+00:00", "2099-12-31T23:59:59+00:00", 1),
            ("pc_003", "patient_001", "caregiver_003", "Người chăm sóc cũ", 0, 2, 0, 0, "GRANTED", "2023-01-01T00:00:00+00:00", "2025-01-01T00:00:00+00:00", 0),
            ("pc_004", "patient_001", "caregiver_004", "Con trai", 0, 2, 1, 1, "GRANTED", "2024-01-01T00:00:00+00:00", "2099-12-31T23:59:59+00:00", 1),
        ]
        for pc in patient_caregivers:
            conn.execute(
                """
                INSERT INTO patient_caregivers (
                    patient_caregiver_id, patient_id, caregiver_id, relationship_type,
                    is_primary, notification_priority, can_edit_schedule, can_view_health,
                    consent_status, valid_from, valid_to, is_active
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(patient_id, caregiver_id) DO UPDATE SET
                    is_active=excluded.is_active,
                    valid_to=excluded.valid_to,
                    can_view_health=excluded.can_view_health
                """,
                pc,
            )

        # Care Team: Clinician 1 -> Patient 1 (ACTIVE), Clinician 1 -> Patient 2 (ACTIVE), Clinician 2 -> Patient 1 (EXPIRED)
        care_teams = [
            ("ct_001", "patient_001", "clinician_001", "Bác sĩ phụ trách tim mạch", "2024-01-01T00:00:00+00:00", "2099-12-31T23:59:59+00:00", "GRANTED", 1),
            ("ct_002", "patient_002", "clinician_001", "Bác sĩ theo dõi đường huyết", "2024-01-01T00:00:00+00:00", "2099-12-31T23:59:59+00:00", "GRANTED", 1),
            ("ct_003", "patient_001", "clinician_002", "Bác sĩ tư vấn cũ", "2023-01-01T00:00:00+00:00", "2025-01-01T00:00:00+00:00", "REVOKED", 0),
            ("ct_004", "patient_001", "clinician_003", "Bác sĩ phụ trách ca trực", "2024-01-01T00:00:00+00:00", "2099-12-31T23:59:59+00:00", "GRANTED", 1),
            ("ct_005", "patient_002", "clinician_003", "Bác sĩ phụ trách ca trực", "2024-01-01T00:00:00+00:00", "2099-12-31T23:59:59+00:00", "GRANTED", 1),
        ]
        for ct in care_teams:
            conn.execute(
                """
                INSERT INTO care_team (
                    care_team_id, patient_id, clinician_id, role_description,
                    valid_from, valid_to, consent_status, is_active
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(patient_id, clinician_id) DO UPDATE SET
                    is_active=excluded.is_active,
                    consent_status=excluded.consent_status,
                    valid_to=excluded.valid_to
                """,
                ct,
            )

    def _seed_legacy_map(self, conn: Any) -> None:
        """Seed legacy mappings."""
        mappings = [
            ("1", "camera_face", "patient_001"),
            ("patient_001", "medication_module", "patient_001"),
            ("pat-001", "health_module", "patient_001"),
            ("2", "camera_face", "patient_002"),
            ("patient_002", "medication_module", "patient_002"),
            ("pat-002", "health_module", "patient_002"),
        ]
        for m in mappings:
            conn.execute(
                """
                INSERT INTO legacy_patient_map (legacy_id, source_system, patient_id)
                VALUES (?, ?, ?)
                ON CONFLICT(legacy_id) DO UPDATE SET patient_id=excluded.patient_id
                """,
                m,
            )

    def _import_medication_standalone(self, conn: Any) -> None:
        """Import medications, prescriptions, items, schedules, occurrences, events, alerts."""
        if not MED_STANDALONE_DB.exists():
            return

        sconn = sqlite3.connect(MED_STANDALONE_DB)
        sconn.row_factory = sqlite3.Row

        # 1. Medications
        default_meds = [
            ("med_001", "Amlor 5mg (Amlodipine)", "Amlodipine besylate", "5", "mg", "Viên nén", "", "Thuốc hạ huyết áp nhóm chẹn kênh canxi."),
            ("med_002", "Glucophage 500mg (Metformin)", "Metformin Hydrochloride", "500", "mg", "Viên bao phim", "", "Thuốc kiểm soát đường huyết."),
            ("med_003", "Panadol 500mg (Paracetamol)", "Paracetamol", "500", "mg", "Viên nén", "", "Thuốc giảm đau, hạ sốt thông thường."),
        ]
        for m in default_meds:
            conn.execute(
                """
                INSERT INTO medications (medication_id, brand_name, generic_name, product_strength, strength_unit, dosage_form, image_path, safety_notes)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(medication_id) DO NOTHING
                """,
                m,
            )

        for row in sconn.execute("SELECT * FROM medications").fetchall():
            conn.execute(
                """
                INSERT INTO medications (medication_id, brand_name, generic_name, product_strength, strength_unit, dosage_form, image_path, safety_notes)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(medication_id) DO NOTHING
                """,
                tuple(row),
            )

        # 2. Prescriptions
        for row in sconn.execute("SELECT * FROM prescriptions").fetchall():
            d = dict(row)
            d["patient_id"] = self.patient_id_map.get(d["patient_id"], d["patient_id"])
            conn.execute(
                """
                INSERT INTO prescriptions (prescription_id, patient_id, entered_by_user_id, prescription_code, prescribed_date, prescriber_clinician_id, source, document_url, status, version, valid_from, valid_to)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(prescription_id) DO NOTHING
                """,
                (d["prescription_id"], d["patient_id"], d["entered_by_user_id"], d["prescription_code"], d["prescribed_date"], d["prescriber_clinician_id"], d["source"], d["document_url"], d["status"], 1, d["valid_from"], d["valid_to"]),
            )

        # 3. Prescription items
        for row in sconn.execute("SELECT * FROM prescription_items").fetchall():
            conn.execute(
                """
                INSERT INTO prescription_items (prescription_item_id, prescription_id, medication_id, dose_amount, dose_unit, route_of_administration, frequency_description, original_instructions, start_date, end_date, status)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(prescription_item_id) DO NOTHING
                """,
                tuple(row),
            )

        # 4. Medication schedules
        for row in sconn.execute("SELECT * FROM medication_schedules").fetchall():
            conn.execute(
                """
                INSERT INTO medication_schedules (schedule_id, prescription_item_id, revision, timezone, local_times, days_of_week, reminder_delay_minutes, grace_period_minutes, caregiver_alert_enabled, is_active, effective_from, effective_to)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(schedule_id, revision) DO NOTHING
                """,
                tuple(row),
            )

        # 5. Dose occurrences
        for row in sconn.execute("SELECT * FROM dose_occurrences").fetchall():
            d = dict(row)
            d["patient_id"] = self.patient_id_map.get(d["patient_id"], d["patient_id"])
            conn.execute(
                """
                INSERT INTO dose_occurrences (
                    occurrence_id, schedule_id, revision, scheduled_at_utc, local_scheduled_time, grace_deadline_utc, state,
                    medication_name_snapshot, dose_snapshot, instructions_snapshot, image_snapshot, patient_id,
                    snooze_count, snoozed_until_utc, action_taken_at_utc, action_actor, action_notes
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(occurrence_id) DO NOTHING
                """,
                (
                    d["occurrence_id"], d["schedule_id"], d["revision"], d["scheduled_at_utc"], d["local_scheduled_time"],
                    d["grace_deadline_utc"], d["state"], d["medication_name_snapshot"], d["dose_snapshot"],
                    d["instructions_snapshot"], d["image_snapshot"], d["patient_id"], d["snooze_count"],
                    d["snoozed_until_utc"], d["action_taken_at_utc"], d["action_actor"], d["action_notes"]
                ),
            )

        # 6. Dose events
        for row in sconn.execute("SELECT * FROM dose_events").fetchall():
            conn.execute(
                """
                INSERT INTO dose_events (event_id, occurrence_id, event_type, actor_id, actor_role, timestamp_utc, idempotency_key, metadata)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(event_id) DO NOTHING
                """,
                tuple(row),
            )

        # 7. Alerts
        for row in sconn.execute("SELECT * FROM alerts").fetchall():
            d = dict(row)
            d["patient_id"] = self.patient_id_map.get(d["patient_id"], d["patient_id"])
            conn.execute(
                """
                INSERT INTO alerts (alert_id, occurrence_id, patient_id, alert_type, status, created_at_utc, resolved_at_utc, resolved_by, resolution_notes)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(alert_id) DO NOTHING
                """,
                (d["alert_id"], d["occurrence_id"], d["patient_id"], d["alert_type"], d["status"], d["created_at_utc"], d["resolved_at_utc"], d["resolved_by"], d["resolution_notes"]),
            )

        # 8. Notification deliveries
        for row in sconn.execute("SELECT * FROM notification_deliveries").fetchall():
            conn.execute(
                """
                INSERT INTO notification_deliveries (delivery_id, alert_id, caregiver_id, channel, recipient_snapshot, status, attempt_count, last_attempt_at_utc, error_message, idempotency_key)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(delivery_id) DO NOTHING
                """,
                tuple(row),
            )

        sconn.close()

    def _import_health_standalone(self, conn: Any) -> None:
        """Import observation types, measurement groups, observations, reviews, clinical messages."""
        default_obs_types = [
            ("ot-sys", "blood_pressure_systolic", "Huyết áp tâm thu", "mmHg", 50.0, 300.0, "vitals"),
            ("ot-dia", "blood_pressure_diastolic", "Huyết áp tâm trương", "mmHg", 30.0, 200.0, "vitals"),
            ("ot-hr", "heart_rate", "Nhịp tim", "bpm", 30.0, 250.0, "vitals"),
            ("ot-spo2", "spo2", "Độ bão hòa oxy SpO2", "%", 50.0, 100.0, "vitals"),
            ("ot-temp", "temperature", "Thân nhiệt", "°C", 34.0, 43.0, "vitals"),
            ("ot-glu", "blood_glucose", "Đường huyết", "mg/dL", 20.0, 600.0, "labs"),
            ("ot-weight", "weight", "Cân nặng", "kg", 20.0, 250.0, "physical"),
        ]
        for ot in default_obs_types:
            conn.execute(
                """
                INSERT INTO observation_types (observation_type_id, code, name, standard_unit, min_plausible_value, max_plausible_value, category)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(observation_type_id) DO NOTHING
                """,
                ot,
            )

        if not HEALTH_STANDALONE_DB.exists():
            return

        hconn = sqlite3.connect(HEALTH_STANDALONE_DB)
        hconn.row_factory = sqlite3.Row

        # 1. Observation types from standalone if present
        for row in hconn.execute("SELECT * FROM observation_types").fetchall():
            conn.execute(
                """
                INSERT OR IGNORE INTO observation_types (observation_type_id, code, name, standard_unit, min_plausible_value, max_plausible_value, category)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                tuple(row),
            )

        # 2. Measurement groups
        for row in hconn.execute("SELECT * FROM measurement_groups").fetchall():
            d = dict(row)
            d["patient_id"] = self.patient_id_map.get(d["patient_id"], d["patient_id"])
            d["entered_by_user_id"] = self.user_id_map.get(d["entered_by_user_id"], d["entered_by_user_id"])
            conn.execute(
                """
                INSERT INTO measurement_groups (group_id, patient_id, measured_at, recorded_at, entered_by_user_id, source_type, device_label, context, status, client_request_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(group_id) DO NOTHING
                """,
                (d["group_id"], d["patient_id"], d["measured_at"], d["recorded_at"], d["entered_by_user_id"], d["source_type"], d["device_label"], d["context"], d["status"], d["client_request_id"]),
            )

        # 3. Health observations
        for row in hconn.execute("SELECT * FROM health_observations").fetchall():
            d = dict(row)
            d["patient_id"] = self.patient_id_map.get(d["patient_id"], d["patient_id"])
            conn.execute(
                """
                INSERT INTO health_observations (observation_id, group_id, patient_id, observation_type_id, value_numeric, unit_code, status, corrects_observation_id, notes)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(observation_id) DO NOTHING
                """,
                (d["observation_id"], d["group_id"], d["patient_id"], d["observation_type_id"], d["value_numeric"], d["unit_code"], d["status"], d["corrects_observation_id"], d.get("notes", "")),
            )

        # 4. Clinician reviews
        for row in hconn.execute("SELECT * FROM clinician_reviews").fetchall():
            d = dict(row)
            d["patient_id"] = self.patient_id_map.get(d["patient_id"], d["patient_id"])
            d["clinician_id"] = self.user_id_map.get(d["clinician_id"], d["clinician_id"])
            conn.execute(
                """
                INSERT INTO clinician_reviews (review_id, clinician_id, patient_id, reviewed_at, period_start, period_end, status, notes)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(review_id) DO NOTHING
                """,
                (d["review_id"], d["clinician_id"], d["patient_id"], d["reviewed_at"], d["period_start"], d["period_end"], d["status"], d["notes"]),
            )

        # 5. Clinical messages
        for row in hconn.execute("SELECT * FROM clinical_messages").fetchall():
            d = dict(row)
            d["patient_id"] = self.patient_id_map.get(d["patient_id"], d["patient_id"])
            d["clinician_id"] = self.user_id_map.get(d["clinician_id"], d["clinician_id"])
            conn.execute(
                """
                INSERT INTO clinical_messages (message_id, patient_id, clinician_id, review_id, observation_id, body, visibility, created_at, status)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(message_id) DO NOTHING
                """,
                (d["message_id"], d["patient_id"], d["clinician_id"], d["review_id"], d["observation_id"], d["body"], d["visibility"], d["created_at"], d["status"]),
            )

        # 6. Symptom entries
        for row in hconn.execute("SELECT * FROM symptom_entries").fetchall():
            d = dict(row)
            d["patient_id"] = self.patient_id_map.get(d["patient_id"], d["patient_id"])
            d["reported_by_user_id"] = self.user_id_map.get(d["reported_by_user_id"], d["reported_by_user_id"])
            conn.execute(
                """
                INSERT INTO symptom_entries (symptom_id, patient_id, reported_by_user_id, reported_at, text, discomfort_level, status)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(symptom_id) DO NOTHING
                """,
                (d["symptom_id"], d["patient_id"], d["reported_by_user_id"], d["reported_at"], d["text"], d["discomfort_level"], d["status"]),
            )

        hconn.close()

    def _import_cognitive_games(self, conn: Any) -> None:
        """Import game types, content packs, sessions and results."""
        if not GAMES_DB.exists():
            return

        gconn = sqlite3.connect(GAMES_DB)
        gconn.row_factory = sqlite3.Row

        # 1. Game types
        for row in gconn.execute("SELECT * FROM game_types").fetchall():
            conn.execute(
                """
                INSERT INTO game_types (game_type_id, code, name, description, rules_version, is_active)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(game_type_id) DO NOTHING
                """,
                tuple(row),
            )

        # 2. Content packs
        for row in gconn.execute("SELECT * FROM content_packs").fetchall():
            conn.execute(
                """
                INSERT INTO content_packs (pack_id, game_type_id, title, theme, assets_manifest, license, locale, version)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(pack_id) DO NOTHING
                """,
                tuple(row),
            )

        # 3. Game sessions
        for row in gconn.execute("SELECT * FROM game_sessions").fetchall():
            d = dict(row)
            d["patient_id"] = self.patient_id_map.get(d["patient_id"], d["patient_id"])
            conn.execute(
                """
                INSERT INTO game_sessions (
                    session_id, patient_id, game_type_id, pack_id, difficulty, seed,
                    started_at, ended_at, status, active_seconds, help_count, rules_version,
                    client_request_id, notes
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(session_id) DO NOTHING
                """,
                (
                    d["session_id"], d["patient_id"], d["game_type_id"], d["pack_id"],
                    d["difficulty"], d["seed"], d["started_at"], d["ended_at"],
                    d["status"], d["active_seconds"], d["help_count"], d["rules_version"],
                    d["client_request_id"], d.get("notes", "")
                ),
            )

        # 4. Game results
        for row in gconn.execute("SELECT * FROM game_results").fetchall():
            d = dict(row)
            conn.execute(
                """
                INSERT INTO game_results (
                    session_id, attempts, correct_count, total_tasks,
                    accuracy_percent, user_feedback, enjoyment, metrics_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(session_id) DO NOTHING
                """,
                (
                    d["session_id"], d["attempts"], d["correct_count"], d["total_tasks"],
                    d["accuracy_percent"], d.get("user_feedback", ""), d.get("enjoyment", ""),
                    d.get("metrics_json", "{}")
                ),
            )

        gconn.close()

    def _seed_rehabilitation_plans_and_sessions(self, conn: Any) -> None:
        """Seed rehab plans (with 16:00 reminder) and historical sessions for Section 6A."""
        now_iso = datetime.now(timezone.utc).isoformat()
        today_date = datetime.now().strftime("%Y-%m-%d")

        plans = [
            ("rp_001", "patient_001", "arm_raise", "Vươn vai (Nâng tay qua đầu)", 10, "16:00", "Asia/Ho_Chi_Minh", 1, 1, "2026-01-01T00:00:00Z", None),
            ("rp_002", "patient_001", "sit_to_stand", "Co duỗi chân (Đứng lên ngồi xuống)", 10, "16:00", "Asia/Ho_Chi_Minh", 1, 1, "2026-01-01T00:00:00Z", None),
            ("rp_003", "patient_001", "marching", "Đi bộ tại chỗ (Nâng cao đùi)", 15, "16:00", "Asia/Ho_Chi_Minh", 1, 1, "2026-01-01T00:00:00Z", None),
            ("rp_004", "patient_002", "arm_raise", "Vươn vai (Nâng tay qua đầu)", 10, "16:00", "Asia/Ho_Chi_Minh", 1, 1, "2026-01-01T00:00:00Z", None),
            ("rp_005", "patient_002", "sit_to_stand", "Co duỗi chân (Đứng lên ngồi xuống)", 10, "16:00", "Asia/Ho_Chi_Minh", 1, 1, "2026-01-01T00:00:00Z", None),
            ("rp_006", "patient_002", "marching", "Đi bộ tại chỗ (Nâng cao đùi)", 15, "16:00", "Asia/Ho_Chi_Minh", 1, 1, "2026-01-01T00:00:00Z", None),
        ]
        for p in plans:
            conn.execute(
                """
                INSERT INTO rehab_plans (plan_id, patient_id, exercise_id, exercise_name, target_count, local_reminder_time, timezone, version, is_active, effective_from, effective_to)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(plan_id) DO UPDATE SET
                    patient_id=excluded.patient_id,
                    target_count=excluded.target_count,
                    exercise_name=excluded.exercise_name
                """,
                p,
            )

        # Seed sample sessions across both patients
        sessions = [
            # Patient 1 completed session yesterday
            ("rs_001", "patient_001", "rp_001", 1, "2026-10-05", "2026-10-05T16:00:10Z", "2026-10-05T16:15:20Z", 12, 12, "camera", "camera_verified_completed", "MediaPipe-Pose-FSM-v1.0", "Hoàn thành đủ chỉ tiêu camera xác nhận"),
            # Patient 1 incomplete session 2 days ago
            ("rs_002", "patient_001", "rp_001", 1, "2026-10-04", "2026-10-04T16:01:00Z", "2026-10-04T16:10:00Z", 12, 8, "camera", "incomplete", "MediaPipe-Pose-FSM-v1.0", "Dừng bài tập sớm do mỏi"),
            # Patient 2 session with verification unavailable
            ("rs_003", "patient_002", "rp_003", 1, "2026-10-05", "2026-10-05T16:00:00Z", "2026-10-05T16:05:00Z", 20, 2, "camera", "verification_unavailable", "MediaPipe-Pose-FSM-v1.0", "Góc nhìn bị che khuất không thấy chi dưới"),
        ]
        for s in sessions:
            conn.execute(
                """
                INSERT INTO rehab_sessions (session_id, patient_id, plan_id, plan_version, local_date, started_at, ended_at, target_count_snapshot, verified_count, completion_source, status, algorithm_version, notes)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(session_id) DO NOTHING
                """,
                s,
            )

    def _seed_mood_and_emotions(self, conn: Any) -> None:
        """Seed self-reported mood check-ins and camera emotion inferences."""
        # 1. Self-reported mood (Reliable, directly reported by patient)
        moods = [
            ("mc_001", "patient_001", 4, "Tâm trạng vui vẻ, ngủ ngon giấc", "2026-10-05T09:00:00Z", "user_pat_001"),
            ("mc_002", "patient_001", 3, "Bình thường, hơi mỏi lưng nhẹ", "2026-10-06T08:30:00Z", "user_pat_001"),
            ("mc_003", "patient_002", 4, "Sảng khoái sau khi nghe nhạc", "2026-10-05T10:00:00Z", "user_pat_002"),
        ]
        for m in moods:
            conn.execute(
                """
                INSERT INTO mood_checkins (checkin_id, patient_id, self_rating, note, recorded_at, recorded_by)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(checkin_id) DO NOTHING
                """,
                m,
            )

        # 2. Camera emotion inferences (Reference only, not clinical diagnostics)
        emotions = [
            ("ei_001", "patient_001", "ExpressionNet-v1.0", "Vui vẻ", 0.94, "2026-10-05T09:05:00Z", "cs_001", 1),
            ("ei_002", "patient_001", "ExpressionNet-v1.0", "Bình thường", 0.88, "2026-10-06T08:35:00Z", "cs_001", 1),
            ("ei_003", "patient_002", "ExpressionNet-v1.0", "Bình thường", 0.91, "2026-10-05T10:05:00Z", "cs_002", 1),
        ]
        for e in emotions:
            conn.execute(
                """
                INSERT INTO emotion_inferences (inference_id, patient_id, model_version, label, confidence, observed_at, consent_id, is_consent_active)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(inference_id) DO NOTHING
                """,
                e,
            )

    def _seed_caregiver_reports_and_recommendations(self, conn: Any) -> None:
        """Seed caregiver reports ('Ghi nhận bất thường') and doctor recommendations."""
        reports = [
            ("rep_001", "patient_001", "caregiver_001", "Cụ có biểu hiện chóng mặt nhẹ sau khi thức dậy lúc 07:00, đã đo huyết áp 138/85.", "2026-10-05T07:30:00Z", "NORMAL", "RESPONDED", "[]", "2026-10-05T07:35:00Z"),
            ("rep_002", "patient_002", "caregiver_002", "Cụ than đau nhức khớp gối khi thời tiết trở lạnh.", "2026-10-06T08:00:00Z", "LOW", "VIEWED", "[]", "2026-10-06T08:05:00Z"),
        ]
        for r in reports:
            conn.execute(
                """
                INSERT INTO caregiver_reports (report_id, patient_id, caregiver_id, description, observed_at, severity, status, attachments, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(report_id) DO NOTHING
                """,
                r,
            )

        messages = [
            ("rm_001", "rep_001", "user_doc_001", "Chào anh Tuấn, bác sĩ đã xem số đo huyết áp 138/85. Hãy cho cụ nghỉ ngơi tại giường, uống nước ấm và theo dõi lại huyết áp sau 30 phút. Nếu có dấu hiệu đau ngực, gọi ngay hotline hoặc cấp cứu.", "2026-10-05T08:00:00Z", "sent"),
        ]
        for m in messages:
            conn.execute(
                """
                INSERT INTO report_messages (message_id, report_id, sender_user_id, body, created_at, status)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(message_id) DO NOTHING
                """,
                m,
            )

        # Doctor recommendations (with versions and simulation flag)
        recommendations = [
            ("rec_001", "patient_001", "clinician_001", "nutrition", "Chế độ ăn giảm muối", "Duy trì chế độ ăn nhạt (< 5g muối/ngày), tăng cường rau xanh và cá, hạn chế đồ chiên xào.", 1, "2026-10-01T00:00:00Z", "2099-12-31T23:59:59Z", "ACTIVE", 1, "2026-10-01T08:00:00Z"),
            ("rec_002", "patient_001", "clinician_001", "exercise", "Bài tập vươn vai 16:00", "Tập vươn vai 12 lần mỗi ngày lúc 16:00, hít thở sâu và đều đặn theo hướng dẫn camera.", 1, "2026-10-01T00:00:00Z", "2099-12-31T23:59:59Z", "ACTIVE", 1, "2026-10-01T08:00:00Z"),
            ("rec_003", "patient_002", "clinician_001", "nutrition", "Kiểm soát tinh bột", "Chia nhỏ bữa ăn, kiểm soát lượng đường và tinh bột theo thực đơn mẫu đã tư vấn.", 1, "2026-10-01T00:00:00Z", "2099-12-31T23:59:59Z", "ACTIVE", 1, "2026-10-01T08:00:00Z"),
        ]
        for rec in recommendations:
            conn.execute(
                """
                INSERT INTO care_recommendations (recommendation_id, patient_id, clinician_id, category, title, content, version, valid_from, valid_to, status, is_simulation, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(recommendation_id) DO NOTHING
                """,
                rec,
            )

    def _seed_emergency_contacts(self, conn: Any) -> None:
        """Seed emergency contacts with configured on-duty hours."""
        contacts = [
            # Doctor on duty for Patient 1: unverified mock number (demo) -> button disabled
            ("ec_001", "patient_001", "clinician_on_duty", "BS. Nguyễn Văn An (Bệnh viện Lão khoa)", "000-000-000", "08:00", "17:00", 0, 1, 1, "Số mô phỏng chưa được xác nhận, hiển thị nhãn DEMO"),
            # Backup local emergency 115
            ("ec_002", "patient_001", "local_emergency", "Cấp cứu y tế Quốc gia 115", "115", "00:00", "23:59", 1, 1, 2, "Đường dây nóng cấp cứu 115"),
            # Doctor on duty for Patient 2: unverified mock number (demo)
            ("ec_003", "patient_002", "clinician_on_duty", "BS. Nguyễn Văn An (Bác sĩ phụ trách)", "000-000-000", "08:00", "17:00", 0, 1, 1, "Số mô phỏng chưa được xác nhận, hiển thị nhãn DEMO"),
            ("ec_004", "patient_002", "local_emergency", "Cấp cứu y tế Quốc gia 115", "115", "00:00", "23:59", 1, 1, 2, "Đường dây nóng cấp cứu 115"),
        ]
        for ec in contacts:
            conn.execute(
                """
                INSERT INTO emergency_contacts (contact_id, patient_id, contact_type, contact_name, phone, on_duty_start, on_duty_end, is_verified, is_active, priority, notes)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(contact_id) DO NOTHING
                """,
                ec,
            )

    def _seed_measurement_requests_and_plan_versions(self, conn: Any) -> None:
        """Seed exercise plan versions and initial caregiver measurement requests."""
        plans = conn.fetchall("SELECT * FROM rehab_plans")
        for p in plans:
            p_id = p["plan_id"]
            p_ver = p.get("version", 1)
            pv_id = f"epv_{p_id}_v{p_ver}"
            conn.execute(
                """
                INSERT OR IGNORE INTO exercise_plan_versions (
                    plan_version_id, plan_id, patient_id, version, target_count,
                    quality_threshold, local_reminder_time, timezone, effective_from,
                    created_by_user_id, created_at, reason
                ) VALUES (?, ?, ?, ?, ?, 80.0, ?, ?, ?, 'user_care_001', ?, 'Khởi tạo kế hoạch ban đầu')
                """,
                (pv_id, p_id, p["patient_id"], p["version"], p["target_count"], p["local_reminder_time"], p["timezone"], p["effective_from"], p["effective_from"]),
            )

        today_str = datetime.now().strftime("%Y-%m-%d")
        now_utc = datetime.now(timezone.utc).isoformat()
        due_utc = (datetime.now(timezone.utc) + timedelta(hours=4)).isoformat()
        ot = conn.fetchone("SELECT observation_type_id FROM observation_types WHERE code = 'blood_pressure_systolic'")
        ot_id = ot["observation_type_id"] if ot else "ot-sys"
        reqs = [
            (
                "mreq_001",
                "patient_001",
                "user_care_001",
                ot_id,
                due_utc,
                f"{today_str} 20:00",
                "Bác đo huyết áp buổi tối sau khi nghỉ ngơi 15 phút nhé ạ",
                "PENDING",
                None,
                now_utc,
                "client_req_seed_001",
            )
        ]
        for mr in reqs:
            conn.execute(
                """
                INSERT INTO measurement_requests (
                    request_id, patient_id, requested_by_user_id, observation_type_id,
                    due_at_utc, local_due_time, instructions, status, completed_observation_id,
                    created_at_utc, client_request_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(request_id) DO NOTHING
                """,
                mr,
            )
