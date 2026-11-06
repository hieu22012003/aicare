"""
Initial Seed Data Generator for Medication Adherence Module.
Populates standard mock users, patients, caregivers, clinician, medications,
prescriptions, and both active and expired patient-caregiver relationships.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Optional

from features.medication.adherence.database.connection import DatabaseManager


def seed_database(db: DatabaseManager) -> None:
    """Insert baseline records into the 3NF database if not already populated."""
    with db.connect() as conn:
        # Check if already seeded
        row = conn.execute("SELECT COUNT(*) FROM patients").fetchone()
        if row and row[0] > 0:
            return

        now_iso = datetime.now(timezone.utc).isoformat()

        # 1. USERS
        users_data = [
            ("user_pat_001", "hoanganh.patient@example.com", "0901111222", "Cụ Hoàng Anh", 1, now_iso),
            ("user_pat_002", "maihuong.patient@example.com", "0902222333", "Cụ Mai Hương", 1, now_iso),
            ("user_care_001", "tuan.caregiver@example.com", "0913333444", "Nguyễn Văn Tuấn", 1, now_iso),
            ("user_care_002", "lan.caregiver@example.com", "0914444555", "Lê Thị Lan", 1, now_iso),
            ("user_care_003", "binh.old@example.com", "0915555666", "Trần Văn Bình", 1, now_iso),
            ("user_doc_001", "dr.an@laokhoa.example.com", "0988888999", "BS. Nguyễn Văn An", 1, now_iso),
        ]
        conn.executemany(
            "INSERT INTO users VALUES (?, ?, ?, ?, ?, ?)",
            users_data,
        )

        # 2. PATIENTS
        patients_data = [
            ("patient_001", "user_pat_001", "Cụ Hoàng Anh", "1951-04-15", "Nam", "Asia/Ho_Chi_Minh", "ACTIVE", "Tiền sử tăng huyết áp nhẹ, minh mẫn"),
            ("patient_002", "user_pat_002", "Cụ Mai Hương", "1948-09-20", "Nữ", "Asia/Ho_Chi_Minh", "ACTIVE", "Đái tháo đường type 2, cần nhắc nhở đều đặn"),
        ]
        conn.executemany(
            "INSERT INTO patients VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            patients_data,
        )

        # 3. CAREGIVERS
        caregivers_data = [
            ("caregiver_001", "user_care_001", 1, 1),
            ("caregiver_002", "user_care_002", 1, 1),
            ("caregiver_003", "user_care_003", 1, 1),
        ]
        conn.executemany(
            "INSERT INTO caregivers VALUES (?, ?, ?, ?)",
            caregivers_data,
        )

        # 4. CLINICIANS
        clinicians_data = [
            ("clinician_001", "user_doc_001", "Bệnh viện Lão khoa Trung Ương", "Lão khoa & Tim mạch", "CCHN-0012984", 1),
        ]
        conn.executemany(
            "INSERT INTO clinicians VALUES (?, ?, ?, ?, ?, ?)",
            clinicians_data,
        )

        # 5. PATIENT_CAREGIVERS (Active & Expired Relationships)
        # Relationship 1: Caregiver 1 -> Patient 1 (ACTIVE, Primary, Priority 1)
        # Relationship 2: Caregiver 2 -> Patient 2 (ACTIVE, Primary, Priority 1)
        # Relationship 3: Caregiver 3 -> Patient 1 (EXPIRED on 2025-01-01 -> Must NOT receive alerts!)
        patient_caregivers_data = [
            ("pc_001", "patient_001", "caregiver_001", "Con trai", 1, 1, 1, 1, "GRANTED", "2024-01-01T00:00:00+00:00", "2099-12-31T23:59:59+00:00", 1),
            ("pc_002", "patient_002", "caregiver_002", "Con gái", 1, 1, 1, 1, "GRANTED", "2024-01-01T00:00:00+00:00", "2099-12-31T23:59:59+00:00", 1),
            ("pc_003", "patient_001", "caregiver_003", "Người chăm sóc cũ", 0, 2, 0, 0, "GRANTED", "2023-01-01T00:00:00+00:00", "2025-01-01T00:00:00+00:00", 1),
        ]
        conn.executemany(
            "INSERT INTO patient_caregivers VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            patient_caregivers_data,
        )

        # 6. CARE_TEAM
        care_team_data = [
            ("ct_001", "patient_001", "clinician_001", "Bác sĩ theo dõi huyết áp", "2024-01-01T00:00:00+00:00", "2099-12-31T23:59:59+00:00", "GRANTED"),
            ("ct_002", "patient_002", "clinician_001", "Bác sĩ nội tiết theo dõi đường huyết", "2024-01-01T00:00:00+00:00", "2099-12-31T23:59:59+00:00", "GRANTED"),
        ]
        conn.executemany(
            "INSERT INTO care_team VALUES (?, ?, ?, ?, ?, ?, ?)",
            care_team_data,
        )

        # 7. MEDICATIONS (Catalog of products with product strength)
        medications_data = [
            (
                "med_001",
                "Amlor 5mg (Amlodipine)",
                "Amlodipine besylate",
                "5",
                "mg",
                "Viên nén",
                "",
                "Thuốc hạ huyết áp nhóm chẹn kênh canxi. Uống nguyên viên với một cốc nước vào buổi sáng.",
            ),
            (
                "med_002",
                "Glucophage 500mg (Metformin)",
                "Metformin Hydrochloride",
                "500",
                "mg",
                "Viên bao phim",
                "",
                "Thuốc kiểm soát đường huyết. Uống sau khi ăn sáng và ăn tối để giảm tác dụng phụ tiêu hoá.",
            ),
        ]
        conn.executemany(
            "INSERT INTO medications VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            medications_data,
        )

        # 8. PRESCRIPTIONS
        prescriptions_data = [
            (
                "rx_001",
                "patient_001",
                "user_care_001",
                "DON-TIM-2026-001",
                "2026-10-01",
                "clinician_001",
                "caregiver_transcribed",
                "",
                "ACTIVE",
                "2026-10-01",
                None,
            ),
            (
                "rx_002",
                "patient_002",
                "user_care_002",
                "DON-NOITIET-2026-002",
                "2026-10-01",
                "clinician_001",
                "caregiver_transcribed",
                "",
                "ACTIVE",
                "2026-10-01",
                None,
            ),
        ]
        conn.executemany(
            "INSERT INTO prescriptions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            prescriptions_data,
        )

        # 9. PRESCRIPTION_ITEMS (Dose per intake explicitly separated from product strength!)
        prescription_items_data = [
            (
                "item_001",
                "rx_001",
                "med_001",
                1.0,           # Liều: 1.0 viên (khác với hàm lượng 5mg)
                "viên",
                "Đường uống",
                "Uống 1 lần/ngày vào buổi sáng",
                "Uống 1 viên sau ăn sáng lúc 08:00 hàng ngày. Không tự ý ngưng thuốc khi huyết áp bình thường.",
                "2026-10-01",
                None,
                "ACTIVE",
            ),
            (
                "item_002",
                "rx_002",
                "med_002",
                1.0,           # Liều: 1.0 viên (khác với hàm lượng 500mg)
                "viên",
                "Đường uống",
                "Uống 2 lần/ngày sau bữa ăn",
                "Uống 1 viên sau ăn sáng (08:00) và 1 viên sau ăn tối (20:00). Uống nhiều nước.",
                "2026-10-01",
                None,
                "ACTIVE",
            ),
        ]
        conn.executemany(
            "INSERT INTO prescription_items VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            prescription_items_data,
        )

        # 10. MEDICATION_SCHEDULES
        schedules_data = [
            (
                "sched_001",
                "item_001",
                1,
                "Asia/Ho_Chi_Minh",
                json.dumps(["08:00"]),
                json.dumps([0, 1, 2, 3, 4, 5, 6]),
                10,
                60,
                1,
                1,
                "2026-10-01T00:00:00+00:00",
                None,
            ),
            (
                "sched_002",
                "item_002",
                1,
                "Asia/Ho_Chi_Minh",
                json.dumps(["08:00", "20:00"]),
                json.dumps([0, 1, 2, 3, 4, 5, 6]),
                10,
                60,
                1,
                1,
                "2026-10-01T00:00:00+00:00",
                None,
            ),
        ]
        conn.executemany(
            "INSERT INTO medication_schedules VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            schedules_data,
        )

        # 11. Initial Heartbeat Meta
        conn.execute(
            "INSERT OR REPLACE INTO meta VALUES (?, ?)",
            ("heartbeat", now_iso),
        )
