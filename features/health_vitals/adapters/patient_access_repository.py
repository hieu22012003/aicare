"""
Patient Access Repository implementation using SQLite.
Verifies whether a user (patient themselves, active caregiver, or authorized clinician) can access a patient profile.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from features.health_vitals.database.connection import DatabaseManager
from features.health_vitals.domain.contracts import IPatientAccessRepository
from features.health_vitals.domain.models import Patient


class SqlPatientAccessRepository(IPatientAccessRepository):
    def __init__(self, db: DatabaseManager) -> None:
        self.db = db

    def get_patient(self, patient_id: str) -> Optional[Patient]:
        with self.db.connect() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT patient_id, user_id, full_name, date_of_birth, gender, timezone, status, notes
                FROM patients
                WHERE patient_id = ?
                """,
                (patient_id,),
            )
            row = cursor.fetchone()
            if not row:
                return None
            return Patient(
                patient_id=row["patient_id"],
                user_id=row["user_id"],
                full_name=row["full_name"],
                date_of_birth=row["date_of_birth"],
                gender=row["gender"],
                timezone=row["timezone"],
                status=row["status"],
                notes=row["notes"] or "",
            )

    def can_user_access_patient(
        self,
        user_id: str,
        patient_id: str,
        required_permission: str = "can_view_health",
    ) -> bool:
        """
        Check if a user has permission to access the patient's health records.
        Allowed if:
        1. User is the patient themselves (patients.user_id = user_id).
        2. User is an active caregiver with valid time window and required permission (can_view_health = 1).
        3. User is an active clinician in the patient's care_team with valid time window.
        """
        now_iso = datetime.now(timezone.utc).isoformat()
        with self.db.connect() as conn:
            cursor = conn.cursor()

            # 1. Is user the patient?
            cursor.execute(
                "SELECT 1 FROM patients WHERE patient_id = ? AND user_id = ?",
                (patient_id, user_id),
            )
            if cursor.fetchone():
                return True

            # 2. Is user an authorized caregiver?
            perm_col = "can_view_health" if required_permission == "can_view_health" else "can_edit_schedule"
            query = f"""
                SELECT 1
                FROM patient_caregivers pc
                JOIN caregivers c ON pc.caregiver_id = c.caregiver_id
                WHERE pc.patient_id = ?
                  AND c.user_id = ?
                  AND pc.is_active = 1
                  AND pc.{perm_col} = 1
                  AND pc.valid_from <= ?
                  AND pc.valid_to >= ?
            """
            cursor.execute(query, (patient_id, user_id, now_iso, now_iso))
            if cursor.fetchone():
                return True

            # 3. Is user an authorized clinician in care_team?
            cursor.execute(
                """
                SELECT 1
                FROM care_team ct
                JOIN clinicians cl ON ct.clinician_id = cl.clinician_id
                WHERE ct.patient_id = ?
                  AND cl.user_id = ?
                  AND ct.is_active = 1
                  AND ct.valid_from <= ?
                  AND ct.valid_to >= ?
                """,
                (patient_id, user_id, now_iso, now_iso),
            )
            if cursor.fetchone():
                return True

        return False
