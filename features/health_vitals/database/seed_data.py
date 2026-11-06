"""
Database Seed Script for Health Monitoring Module (P0-P2).
Seeds realistic data:
- 2 patients (Patient A, Patient B)
- 2 clinicians (Dr. Le Quang [active both], Dr. Pham Mai [active A, expired for B])
- 2 caregivers (active permissions)
- Observation types with plausible ranges
- 30-day realistic measurements for Patient A with specific test scenarios:
  * Blood pressure 128/78 with separate measured_at and recorded_at
  * 5-day missing data gap
  * 3 measurements on a single day for n=3 aggregation test
  * Distinct blood glucose units (mg/dL and mmol/L)
  * Corrected observation with preserved audit trail
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone

from features.health_vitals.database.connection import DatabaseManager

logger = logging.getLogger("HealthSeedData")


def seed_database(db: DatabaseManager) -> None:
    """Populate database with deterministic seed data."""
    now = datetime(2026, 10, 5, 16, 0, 0, tzinfo=timezone.utc)
    now_iso = now.isoformat()

    with db.connect() as conn:
        cursor = conn.cursor()

        # Check if already seeded
        cursor.execute("SELECT COUNT(*) as cnt FROM users")
        if cursor.fetchone()["cnt"] > 0:
            logger.info("Database already contains users. Skipping seed.")
            return

        logger.info("Seeding Health Monitoring database...")

        # 1. USERS
        users_data = [
            ("usr-pat-001", "an.nguyen@example.com", "0901234567", "Cụ Nguyễn Văn An", 1, now_iso),
            ("usr-pat-002", "binh.tran@example.com", "0902345678", "Cụ Trần Thị Bình", 1, now_iso),
            ("usr-cg-001", "binh.nguyen.cg@example.com", "0911222333", "Nguyễn Văn Bình (Con trai)", 1, now_iso),
            ("usr-cg-002", "hoa.tran.cg@example.com", "0922333444", "Trần Thị Hoa (Con gái)", 1, now_iso),
            ("usr-doc-001", "contact-91e5c40b@example.com", "0988777666", "BS.CKII Lê Quang", 1, now_iso),
            ("usr-doc-002", "contact-4bcc03e4@example.com", "0977666555", "ThS.BS Phạm Mai", 1, now_iso),
        ]
        cursor.executemany(
            "INSERT INTO users (user_id, email, phone, full_name, is_active, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            users_data,
        )

        # 2. PATIENTS
        patients_data = [
            ("pat-001", "usr-pat-001", "Cụ Nguyễn Văn An", "1948-03-12", "Nam", "Asia/Ho_Chi_Minh", "ACTIVE", "Tiền sử tăng huyết áp 10 năm, theo dõi huyết áp và nhịp tim tại nhà."),
            ("pat-002", "usr-pat-002", "Cụ Trần Thị Bình", "1952-08-25", "Nữ", "Asia/Ho_Chi_Minh", "ACTIVE", "Đái tháo đường type 2, rối loạn mỡ máu."),
        ]
        cursor.executemany(
            "INSERT INTO patients (patient_id, user_id, full_name, date_of_birth, gender, timezone, status, notes) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            patients_data,
        )

        # 3. CAREGIVERS
        caregivers_data = [
            ("cg-001", "usr-cg-001", 1, 1),
            ("cg-002", "usr-cg-002", 1, 1),
        ]
        cursor.executemany(
            "INSERT INTO caregivers (caregiver_id, user_id, email_verified, phone_verified) VALUES (?, ?, ?, ?)",
            caregivers_data,
        )

        # 4. CLINICIANS
        clinicians_data = [
            ("doc-001", "usr-doc-001", "Bệnh viện Lão khoa Trung Ương", "Tim mạch học", "CCHN-001298-BYT", 1),
            ("doc-002", "usr-doc-002", "Bệnh viện Bạch Mai", "Nội tiết & Chuyển hóa", "CCHN-004567-BYT", 1),
        ]
        cursor.executemany(
            "INSERT INTO clinicians (clinician_id, user_id, workplace, specialty, license_number, is_verified) VALUES (?, ?, ?, ?, ?, ?)",
            clinicians_data,
        )

        # 5. PATIENT CAREGIVERS
        patient_caregivers_data = [
            ("pcg-001", "pat-001", "cg-001", "Con trai", 1, 1, 1, 1, "GRANTED", "2026-01-01T00:00:00Z", "2027-12-31T23:59:59Z", 1),
            ("pcg-002", "pat-002", "cg-002", "Con gái", 1, 1, 1, 1, "GRANTED", "2026-01-01T00:00:00Z", "2027-12-31T23:59:59Z", 1),
        ]
        cursor.executemany(
            """
            INSERT INTO patient_caregivers (
                patient_caregiver_id, patient_id, caregiver_id, relationship_type,
                is_primary, notification_priority, can_edit_schedule, can_view_health,
                consent_status, valid_from, valid_to, is_active
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            patient_caregivers_data,
        )

        # 6. CARE TEAM (P0: Dr. Quang active for A & B; Dr. Mai active for A, EXPIRED for B)
        care_team_data = [
            # Dr. Quang active for Patient A and Patient B
            ("ct-001", "pat-001", "doc-001", "Bác sĩ tim mạch phụ trách", "2026-01-01T00:00:00Z", "2027-12-31T23:59:59Z", "GRANTED", 1),
            ("ct-002", "pat-002", "doc-001", "Bác sĩ tim mạch phụ trách", "2026-01-01T00:00:00Z", "2027-12-31T23:59:59Z", "GRANTED", 1),
            # Dr. Mai active for Patient A
            ("ct-003", "pat-001", "doc-002", "Bác sĩ nội tiết phối hợp", "2026-01-01T00:00:00Z", "2027-12-31T23:59:59Z", "GRANTED", 1),
            # Dr. Mai EXPIRED for Patient B (Expired in 2025!)
            ("ct-004", "pat-002", "doc-002", "Bác sĩ hội chẩn trước đây (hết hạn)", "2025-01-01T00:00:00Z", "2025-12-31T23:59:59Z", "EXPIRED", 0),
        ]
        cursor.executemany(
            """
            INSERT INTO care_team (
                care_team_id, patient_id, clinician_id, role_description,
                valid_from, valid_to, consent_status, is_active
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            care_team_data,
        )

        # 7. OBSERVATION TYPES
        obs_types = [
            ("ot-sys", "blood_pressure_systolic", "Huyết áp tâm thu", "mmHg", 50.0, 300.0, "vitals"),
            ("ot-dia", "blood_pressure_diastolic", "Huyết áp tâm trương", "mmHg", 30.0, 200.0, "vitals"),
            ("ot-hr", "heart_rate", "Nhịp tim", "bpm", 30.0, 250.0, "vitals"),
            ("ot-spo2", "spo2", "Độ bão hòa oxy SpO2", "%", 50.0, 100.0, "vitals"),
            ("ot-temp", "temperature", "Thân nhiệt", "°C", 34.0, 43.0, "vitals"),
            ("ot-glu", "blood_glucose", "Đường huyết", "mg/dL", 20.0, 600.0, "labs"),
        ]
        cursor.executemany(
            """
            INSERT INTO observation_types (
                observation_type_id, code, name, standard_unit,
                min_plausible_value, max_plausible_value, category
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            obs_types,
        )

        # 8. HISTORICAL MEASUREMENTS FOR PATIENT A (pat-001)
        # Days 28 to 21 (1 reading per day)
        for d in range(28, 20, -1):
            m_dt = now - timedelta(days=d, hours=9)
            m_iso = m_dt.isoformat()
            r_iso = (m_dt + timedelta(minutes=5)).isoformat()
            grp_id = f"grp-seed-d{d}"
            cursor.execute(
                """
                INSERT INTO measurement_groups (
                    group_id, patient_id, measured_at, recorded_at,
                    entered_by_user_id, source_type, device_label,
                    context, status, client_request_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    grp_id, "pat-001", m_iso, r_iso, "usr-cg-001",
                    "manual_caregiver", "Máy đo bắp tay Omron HEM-7120", "Nghỉ ngơi 15 phút trước đo",
                    "active", f"req-seed-d{d}",
                ),
            )
            # Systolic & Diastolic
            cursor.execute(
                "INSERT INTO health_observations (observation_id, group_id, patient_id, observation_type_id, value_numeric, unit_code, status) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (f"obs-sys-d{d}", grp_id, "pat-001", "ot-sys", 125.0 + (d % 4), "mmHg", "active"),
            )
            cursor.execute(
                "INSERT INTO health_observations (observation_id, group_id, patient_id, observation_type_id, value_numeric, unit_code, status) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (f"obs-dia-d{d}", grp_id, "pat-001", "ot-dia", 76.0 + (d % 3), "mmHg", "active"),
            )
            # Heart rate
            cursor.execute(
                "INSERT INTO health_observations (observation_id, group_id, patient_id, observation_type_id, value_numeric, unit_code, status) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (f"obs-hr-d{d}", grp_id, "pat-001", "ot-hr", 72.0 + (d % 5), "bpm", "active"),
            )

        # Days 20 to 16: An initial review was conducted by Dr. Quang 15 days ago
        cursor.execute(
            """
            INSERT INTO clinician_reviews (
                review_id, clinician_id, patient_id, reviewed_at,
                period_start, period_end, status, notes
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "rev-seed-001", "doc-001", "pat-001",
                (now - timedelta(days=15, hours=10)).isoformat(),
                (now - timedelta(days=30)).isoformat(),
                (now - timedelta(days=15, hours=12)).isoformat(),
                "CONFIRMED",
                "Huyết áp ổn định trong 2 tuần qua, tiếp tục duy trì chế độ giảm muối.",
            ),
        )

        # NOTE: 5-DAY GAP (Days 15, 14, 13, 12, 11 have NO measurements) -> tests Criterion 13!

        # Day 10: 3 measurements in a single day -> tests Criterion 12 (n=3)
        day10_base = datetime(2026, 9, 25, 0, 0, 0, tzinfo=timezone.utc)
        day10_readings = [
            (day10_base + timedelta(hours=7), 120.0, 75.0, 70.0, "Sáng ngủ dậy", "m1"),
            (day10_base + timedelta(hours=12), 126.0, 80.0, 76.0, "Trưa sau ăn 1h", "m2"),
            (day10_base + timedelta(hours=19), 130.0, 82.0, 74.0, "Tối trước khi ngủ", "m3"),
        ]
        for m_dt, sys_v, dia_v, hr_v, ctx, tag in day10_readings:
            grp_id = f"grp-day10-{tag}"

            cursor.execute(
                """
                INSERT INTO measurement_groups (
                    group_id, patient_id, measured_at, recorded_at,
                    entered_by_user_id, source_type, device_label,
                    context, status, client_request_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    grp_id, "pat-001", m_dt.isoformat(), (m_dt + timedelta(minutes=2)).isoformat(),
                    "usr-pat-001", "manual_patient", "Máy đo cá nhân", ctx,
                    "active", f"req-day10-{tag}",
                ),
            )
            cursor.execute(
                "INSERT INTO health_observations (observation_id, group_id, patient_id, observation_type_id, value_numeric, unit_code, status) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (f"obs-sys-d10-{tag}", grp_id, "pat-001", "ot-sys", sys_v, "mmHg", "active"),
            )
            cursor.execute(
                "INSERT INTO health_observations (observation_id, group_id, patient_id, observation_type_id, value_numeric, unit_code, status) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (f"obs-dia-d10-{tag}", grp_id, "pat-001", "ot-dia", dia_v, "mmHg", "active"),
            )
            cursor.execute(
                "INSERT INTO health_observations (observation_id, group_id, patient_id, observation_type_id, value_numeric, unit_code, status) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (f"obs-hr-d10-{tag}", grp_id, "pat-001", "ot-hr", hr_v, "bpm", "active"),
            )

        # Day 6: Blood glucose in mg/dL and mmol/L on separate days -> tests Criterion 14
        m_dt6 = now - timedelta(days=6, hours=8)
        cursor.execute(
            """
            INSERT INTO measurement_groups (
                group_id, patient_id, measured_at, recorded_at,
                entered_by_user_id, source_type, device_label,
                context, status, client_request_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "grp-glu-mgdl", "pat-001", m_dt6.isoformat(), m_dt6.isoformat(),
                "usr-cg-001", "manual_caregiver", "Máy đo đường huyết Accu-Chek", "Nhịn ăn sáng",
                "active", "req-glu-mgdl",
            ),
        )
        cursor.execute(
            "INSERT INTO health_observations (observation_id, group_id, patient_id, observation_type_id, value_numeric, unit_code, status) VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("obs-glu-1", "grp-glu-mgdl", "pat-001", "ot-glu", 115.0, "mg/dL", "active"),
        )

        m_dt5 = now - timedelta(days=5, hours=8)
        cursor.execute(
            """
            INSERT INTO measurement_groups (
                group_id, patient_id, measured_at, recorded_at,
                entered_by_user_id, source_type, device_label,
                context, status, client_request_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "grp-glu-mmol", "pat-001", m_dt5.isoformat(), m_dt5.isoformat(),
                "usr-cg-001", "manual_caregiver", "Máy đo test que mmol/L", "Nhịn ăn sáng",
                "active", "req-glu-mmol",
            ),
        )
        cursor.execute(
            "INSERT INTO health_observations (observation_id, group_id, patient_id, observation_type_id, value_numeric, unit_code, status) VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("obs-glu-2", "grp-glu-mmol", "pat-001", "ot-glu", 6.4, "mmol/L", "active"),
        )

        # Day 3: Measurement entered late (measured at 07:00, entered at 20:00) -> tests Criterion 3
        m_dt3_meas = now - timedelta(days=3, hours=9)  # 07:00
        m_dt3_rec = now - timedelta(days=3, hours=-4)  # 20:00 (13 hours later)
        cursor.execute(
            """
            INSERT INTO measurement_groups (
                group_id, patient_id, measured_at, recorded_at,
                entered_by_user_id, source_type, device_label,
                context, status, client_request_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "grp-day3-late", "pat-001", m_dt3_meas.isoformat(), m_dt3_rec.isoformat(),
                "usr-cg-001", "manual_caregiver", "Người dùng nhập", "Đo buổi sáng, người nhà nhập lại buổi tối",
                "active", "req-day3-late",
            ),
        )
        cursor.execute(
            "INSERT INTO health_observations (observation_id, group_id, patient_id, observation_type_id, value_numeric, unit_code, status) VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("obs-sys-d3", "grp-day3-late", "pat-001", "ot-sys", 124.0, "mmHg", "active"),
        )
        cursor.execute(
            "INSERT INTO health_observations (observation_id, group_id, patient_id, observation_type_id, value_numeric, unit_code, status) VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("obs-dia-d3", "grp-day3-late", "pat-001", "ot-dia", 78.0, "mmHg", "active"),
        )

        # Day 2: An observation that was corrected -> tests Criterion 8
        m_dt2 = now - timedelta(days=2, hours=8)
        cursor.execute(
            """
            INSERT INTO measurement_groups (
                group_id, patient_id, measured_at, recorded_at,
                entered_by_user_id, source_type, device_label,
                context, status, client_request_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "grp-day2-orig", "pat-001", m_dt2.isoformat(), m_dt2.isoformat(),
                "usr-cg-001", "manual_caregiver", "Omron HEM-7120", "",
                "active", "req-day2-orig",
            ),
        )
        # Original mistyped observation (190 mmHg) marked as 'corrected'
        cursor.execute(
            "INSERT INTO health_observations (observation_id, group_id, patient_id, observation_type_id, value_numeric, unit_code, status) VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("obs-sys-d2-mistyped", "grp-day2-orig", "pat-001", "ot-sys", 190.0, "mmHg", "corrected"),
        )
        cursor.execute(
            "INSERT INTO health_observations (observation_id, group_id, patient_id, observation_type_id, value_numeric, unit_code, status) VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("obs-dia-d2", "grp-day2-orig", "pat-001", "ot-dia", 78.0, "mmHg", "active"),
        )

        # Correction group for the corrected observation
        cursor.execute(
            """
            INSERT INTO measurement_groups (
                group_id, patient_id, measured_at, recorded_at,
                entered_by_user_id, source_type, device_label,
                context, status, client_request_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "grp-day2-corrected", "pat-001", m_dt2.isoformat(), (m_dt2 + timedelta(minutes=10)).isoformat(),
                "usr-cg-001", "manual_caregiver", "Omron HEM-7120", "correction",
                "active", "req-day2-corrected",
            ),
        )
        cursor.execute(
            """
            INSERT INTO health_observations (
                observation_id, group_id, patient_id, observation_type_id,
                value_numeric, unit_code, status, corrects_observation_id, notes
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "obs-sys-d2-corrected", "grp-day2-corrected", "pat-001", "ot-sys",
                126.0, "mmHg", "active", "obs-sys-d2-mistyped", "Hiệu chỉnh lỗi gõ nhầm số 9 thành 2",
            ),
        )


        # Today: Latest measurement (128/78 mmHg, 72 bpm, 98% SpO2, 36.8°C) -> tests Criterion 1
        m_dt_today = now - timedelta(hours=2)
        cursor.execute(
            """
            INSERT INTO measurement_groups (
                group_id, patient_id, measured_at, recorded_at,
                entered_by_user_id, source_type, device_label,
                context, status, client_request_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "grp-today-latest", "pat-001", m_dt_today.isoformat(), m_dt_today.isoformat(),
                "usr-pat-001", "manual_patient", "Máy đo bắp tay Omron", "Đo sau bữa sáng",
                "active", "req-today-latest",
            ),
        )
        cursor.execute(
            "INSERT INTO health_observations (observation_id, group_id, patient_id, observation_type_id, value_numeric, unit_code, status) VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("obs-sys-today", "grp-today-latest", "pat-001", "ot-sys", 128.0, "mmHg", "active"),
        )
        cursor.execute(
            "INSERT INTO health_observations (observation_id, group_id, patient_id, observation_type_id, value_numeric, unit_code, status) VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("obs-dia-today", "grp-today-latest", "pat-001", "ot-dia", 78.0, "mmHg", "active"),
        )
        cursor.execute(
            "INSERT INTO health_observations (observation_id, group_id, patient_id, observation_type_id, value_numeric, unit_code, status) VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("obs-hr-today", "grp-today-latest", "pat-001", "ot-hr", 72.0, "bpm", "active"),
        )
        cursor.execute(
            "INSERT INTO health_observations (observation_id, group_id, patient_id, observation_type_id, value_numeric, unit_code, status) VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("obs-spo2-today", "grp-today-latest", "pat-001", "ot-spo2", 98.0, "%", "active"),
        )
        cursor.execute(
            "INSERT INTO health_observations (observation_id, group_id, patient_id, observation_type_id, value_numeric, unit_code, status) VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("obs-temp-today", "grp-today-latest", "pat-001", "ot-temp", 36.8, "°C", "active"),
        )

        # Symptom entry for Patient A
        cursor.execute(
            """
            INSERT INTO symptom_entries (
                symptom_id, patient_id, reported_by_user_id,
                reported_at, text, discomfort_level, status
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "sym-001", "pat-001", "usr-pat-001",
                (now - timedelta(hours=3)).isoformat(),
                "Hơi choáng nhẹ khi đứng dậy nhanh lúc sáng sớm",
                2, "active",
            ),
        )

        # Initial message from Dr. Quang
        cursor.execute(
            """
            INSERT INTO clinical_messages (
                message_id, patient_id, clinician_id, review_id,
                observation_id, body, visibility, created_at, status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "msg-seed-001", "pat-001", "doc-001", "rev-seed-001", None,
                "Chào cụ An, huyết áp tuần rồi rất đẹp. Cụ nhớ tiếp tục uống đủ nước và duy trì tập nhẹ nhàng nhé.",
                "PATIENT_AND_CAREGIVERS", (now - timedelta(days=15, hours=9)).isoformat(), "read",
            ),
        )

        # 9. PATIENT B MEASUREMENTS (pat-002)
        m_dt_b = now - timedelta(days=1, hours=10)
        cursor.execute(
            """
            INSERT INTO measurement_groups (
                group_id, patient_id, measured_at, recorded_at,
                entered_by_user_id, source_type, device_label,
                context, status, client_request_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "grp-pat2-1", "pat-002", m_dt_b.isoformat(), m_dt_b.isoformat(),
                "usr-cg-002", "manual_caregiver", "Máy đo gia đình", "Nghỉ ngơi",
                "active", "req-pat2-1",
            ),
        )
        cursor.execute(
            "INSERT INTO health_observations (observation_id, group_id, patient_id, observation_type_id, value_numeric, unit_code, status) VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("obs-sys-pat2", "grp-pat2-1", "pat-002", "ot-sys", 135.0, "mmHg", "active"),
        )
        cursor.execute(
            "INSERT INTO health_observations (observation_id, group_id, patient_id, observation_type_id, value_numeric, unit_code, status) VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("obs-dia-pat2", "grp-pat2-1", "pat-002", "ot-dia", 85.0, "mmHg", "active"),
        )
        cursor.execute(
            "INSERT INTO health_observations (observation_id, group_id, patient_id, observation_type_id, value_numeric, unit_code, status) VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("obs-hr-pat2", "grp-pat2-1", "pat-002", "ot-hr", 80.0, "bpm", "active"),
        )
        cursor.execute(
            "INSERT INTO health_observations (observation_id, group_id, patient_id, observation_type_id, value_numeric, unit_code, status) VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("obs-glu-pat2", "grp-pat2-1", "pat-002", "ot-glu", 142.0, "mg/dL", "active"),
        )

        # 10. PATIENT B HISTORICAL MEASUREMENT (2 days ago)
        m_dt_b2 = now - timedelta(days=2, hours=9)
        cursor.execute(
            """
            INSERT INTO measurement_groups (
                group_id, patient_id, measured_at, recorded_at,
                entered_by_user_id, source_type, device_label,
                context, status, client_request_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "grp-pat2-2", "pat-002", m_dt_b2.isoformat(), m_dt_b2.isoformat(),
                "usr-cg-002", "manual_caregiver", "Máy đo gia đình", "Sáng sớm",
                "active", "req-pat2-2",
            ),
        )
        cursor.execute(
            "INSERT INTO health_observations (observation_id, group_id, patient_id, observation_type_id, value_numeric, unit_code, status) VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("obs-sys-pat2-2", "grp-pat2-2", "pat-002", "ot-sys", 138.0, "mmHg", "active"),
        )
        cursor.execute(
            "INSERT INTO health_observations (observation_id, group_id, patient_id, observation_type_id, value_numeric, unit_code, status) VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("obs-dia-pat2-2", "grp-pat2-2", "pat-002", "ot-dia", 88.0, "mmHg", "active"),
        )
        cursor.execute(
            "INSERT INTO health_observations (observation_id, group_id, patient_id, observation_type_id, value_numeric, unit_code, status) VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("obs-hr-pat2-2", "grp-pat2-2", "pat-002", "ot-hr", 82.0, "bpm", "active"),
        )
        cursor.execute(
            "INSERT INTO health_observations (observation_id, group_id, patient_id, observation_type_id, value_numeric, unit_code, status) VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("obs-glu-pat2-2", "grp-pat2-2", "pat-002", "ot-glu", 148.0, "mg/dL", "active"),
        )

        logger.info("Seed data completed successfully.")
