"""
Patient Repository Implementation for Medication Adherence Module.
"""

from __future__ import annotations

import logging
from typing import List, Optional

from features.medication.adherence.database.connection import DatabaseManager
from features.medication.adherence.domain.contracts import IPatientRepository
from features.medication.adherence.domain.models import Patient

logger = logging.getLogger("PatientRepository")


class SqlPatientRepository(IPatientRepository):
    """SQL Implementation of IPatientRepository."""

    def __init__(self, db: DatabaseManager) -> None:
        self.db = db

    def get_by_id(self, patient_id: str) -> Optional[Patient]:
        if not patient_id:
            return None
        with self.db.connect() as conn:
            row = conn.execute(
                "SELECT * FROM patients WHERE patient_id = ?",
                (patient_id.strip(),),
            ).fetchone()
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

    def list_all(self) -> List[Patient]:
        with self.db.connect() as conn:
            rows = conn.execute("SELECT * FROM patients ORDER BY full_name").fetchall()
            return [
                Patient(
                    patient_id=r["patient_id"],
                    user_id=r["user_id"],
                    full_name=r["full_name"],
                    date_of_birth=r["date_of_birth"],
                    gender=r["gender"],
                    timezone=r["timezone"],
                    status=r["status"],
                    notes=r["notes"] or "",
                )
                for r in rows
            ]

    def save(self, patient: Patient) -> None:
        with self.db.connect() as conn:
            conn.execute(
                """
                INSERT INTO patients (patient_id, user_id, full_name, date_of_birth, gender, timezone, status, notes)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(patient_id) DO UPDATE SET
                    full_name=excluded.full_name,
                    date_of_birth=excluded.date_of_birth,
                    gender=excluded.gender,
                    timezone=excluded.timezone,
                    status=excluded.status,
                    notes=excluded.notes
                """,
                (
                    patient.patient_id,
                    patient.user_id,
                    patient.full_name,
                    patient.date_of_birth,
                    patient.gender,
                    patient.timezone,
                    patient.status,
                    patient.notes,
                ),
            )
