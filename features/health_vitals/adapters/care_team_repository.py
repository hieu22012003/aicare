"""
Care Team Repository implementation using SQLite.
Enforces that clinicians can ONLY access patients for whom they have an active, unexpired care_team relationship.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import List, Optional

from features.health_vitals.database.connection import DatabaseManager
from features.health_vitals.domain.contracts import ICareTeamRepository
from features.health_vitals.domain.models import (
    Clinician,
    PatientCardSummary,
    ReviewStatus,
)

logger = logging.getLogger("CareTeamRepository")


class SqlCareTeamRepository(ICareTeamRepository):
    def __init__(self, db: DatabaseManager) -> None:
        self.db = db

    def get_clinician(self, clinician_id: str) -> Optional[Clinician]:
        with self.db.connect() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT c.clinician_id, c.user_id, u.full_name, c.workplace, c.specialty, c.license_number, c.is_verified
                FROM clinicians c
                JOIN users u ON c.user_id = u.user_id
                WHERE c.clinician_id = ?
                """,
                (clinician_id,),
            )
            row = cursor.fetchone()
            if not row:
                return None
            return Clinician(
                clinician_id=row["clinician_id"],
                user_id=row["user_id"],
                full_name=row["full_name"],
                workplace=row["workplace"],
                specialty=row["specialty"],
                license_number=row["license_number"],
                is_verified=bool(row["is_verified"]),
            )

    def is_clinician_authorized(self, clinician_id: str, patient_id: str) -> bool:
        """
        Check if clinician has an active, unexpired care_team assignment for this patient.
        If expired or inactive, returns False.
        """
        now_iso = datetime.now(timezone.utc).isoformat()
        with self.db.connect() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT 1
                FROM care_team ct
                WHERE ct.clinician_id = ?
                  AND ct.patient_id = ?
                  AND ct.is_active = 1
                  AND ct.valid_from <= ?
                  AND ct.valid_to >= ?
                """,
                (clinician_id, patient_id, now_iso, now_iso),
            )
            return cursor.fetchone() is not None

    def get_clinician_patients(self, clinician_id: str) -> List[PatientCardSummary]:
        """
        Return the list of patients assigned to this clinician with unexpired care_team links.
        Computes last measurement time, unreviewed record count, and review status.
        """
        now_iso = datetime.now(timezone.utc).isoformat()
        with self.db.connect() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT p.patient_id, p.full_name, p.gender, p.timezone
                FROM care_team ct
                JOIN patients p ON ct.patient_id = p.patient_id
                WHERE ct.clinician_id = ?
                  AND ct.is_active = 1
                  AND ct.valid_from <= ?
                  AND ct.valid_to >= ?
                ORDER BY p.full_name ASC
                """,
                (clinician_id, now_iso, now_iso),
            )
            patient_rows = cursor.fetchall()

            results: List[PatientCardSummary] = []
            for p_row in patient_rows:
                pat_id = p_row["patient_id"]

                # 1. Latest measurement timestamp
                cursor.execute(
                    """
                    SELECT MAX(measured_at) as last_measured
                    FROM measurement_groups
                    WHERE patient_id = ? AND status = 'active'
                    """,
                    (pat_id,),
                )
                last_measured = cursor.fetchone()["last_measured"]

                # 2. Latest review timestamp by this clinician
                cursor.execute(
                    """
                    SELECT MAX(reviewed_at) as last_reviewed
                    FROM clinician_reviews
                    WHERE clinician_id = ? AND patient_id = ? AND status = 'CONFIRMED'
                    """,
                    (clinician_id, pat_id),
                )
                last_reviewed = cursor.fetchone()["last_reviewed"]

                # 3. Unreviewed count
                if last_reviewed:
                    cursor.execute(
                        """
                        SELECT COUNT(*) as unrev_count
                        FROM measurement_groups
                        WHERE patient_id = ? AND status = 'active' AND recorded_at > ?
                        """,
                        (pat_id, last_reviewed),
                    )
                else:
                    cursor.execute(
                        """
                        SELECT COUNT(*) as unrev_count
                        FROM measurement_groups
                        WHERE patient_id = ? AND status = 'active'
                        """,
                        (pat_id,),
                    )
                unrev_count = cursor.fetchone()["unrev_count"]

                # 4. Check if clinician has sent a message after the last measurement
                has_message_response = False
                if last_measured:
                    cursor.execute(
                        """
                        SELECT 1
                        FROM clinical_messages
                        WHERE clinician_id = ? AND patient_id = ? AND created_at >= ?
                        LIMIT 1
                        """,
                        (clinician_id, pat_id, last_measured),
                    )
                    has_message_response = cursor.fetchone() is not None

                # Determine review status text
                if not last_measured:
                    review_status_str = ReviewStatus.NO_DATA.value
                elif unrev_count > 0:
                    review_status_str = ReviewStatus.UNREVIEWED.value
                elif has_message_response:
                    review_status_str = ReviewStatus.RESPONDED.value
                else:
                    review_status_str = ReviewStatus.REVIEWED.value

                # 5. Get latest Blood Pressure snippet
                cursor.execute(
                    """
                    SELECT
                        MAX(CASE WHEN ot.code = 'blood_pressure_systolic' THEN ho.value_numeric END) as sys,
                        MAX(CASE WHEN ot.code = 'blood_pressure_diastolic' THEN ho.value_numeric END) as dia
                    FROM measurement_groups mg
                    JOIN health_observations ho ON mg.group_id = ho.group_id
                    JOIN observation_types ot ON ho.observation_type_id = ot.observation_type_id
                    WHERE mg.patient_id = ? AND mg.status = 'active' AND ho.status = 'active'
                      AND ot.code IN ('blood_pressure_systolic', 'blood_pressure_diastolic')
                    GROUP BY mg.group_id
                    ORDER BY mg.measured_at DESC, mg.recorded_at DESC, mg.group_id DESC
                    LIMIT 1
                    """,
                    (pat_id,),
                )
                bp_row = cursor.fetchone()
                latest_bp = None
                if bp_row and bp_row["sys"] is not None and bp_row["dia"] is not None:
                    latest_bp = f"{int(bp_row['sys'])}/{int(bp_row['dia'])} mmHg"

                # 6. Get latest Heart Rate snippet
                cursor.execute(
                    """
                    SELECT ho.value_numeric as hr
                    FROM measurement_groups mg
                    JOIN health_observations ho ON mg.group_id = ho.group_id
                    JOIN observation_types ot ON ho.observation_type_id = ot.observation_type_id
                    WHERE mg.patient_id = ? AND mg.status = 'active' AND ho.status = 'active'
                      AND ot.code = 'heart_rate'
                    ORDER BY mg.measured_at DESC, mg.recorded_at DESC, ho.observation_id DESC
                    LIMIT 1

                    """,
                    (pat_id,),
                )
                hr_row = cursor.fetchone()
                latest_hr = None
                if hr_row and hr_row["hr"] is not None:
                    latest_hr = f"{int(hr_row['hr'])} bpm"

                results.append(
                    PatientCardSummary(
                        patient_id=pat_id,
                        full_name=p_row["full_name"],
                        gender=p_row["gender"],
                        timezone=p_row["timezone"],
                        last_measured_at=last_measured,
                        unreviewed_records_count=unrev_count,
                        last_reviewed_at=last_reviewed,
                        review_status=review_status_str,
                        latest_bp=latest_bp,
                        latest_hr=latest_hr,
                    )
                )

            return results
