"""
Care Team and Authorization Repository for Cognitive Games module.
Enforces role-based permissions across patients, caregivers, and clinicians.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from features.music_care.cognitive_games.database.connection import DatabaseManager
from features.music_care.cognitive_games.domain.contracts import ICareTeamRepository
from features.music_care.cognitive_games.domain.models import Patient

logger = logging.getLogger("CognitiveCareTeamRepo")


class SqlCareTeamRepository(ICareTeamRepository):
    def __init__(self, db: DatabaseManager) -> None:
        self.db = db

    def is_clinician_authorized(self, clinician_id: str, patient_id: str) -> bool:
        """
        Verifies active care_team entry:
        - consent_status = 'GRANTED'
        - is_active = 1
        - valid_from <= now <= valid_to
        """
        now_iso = datetime.now(timezone.utc).isoformat()
        with self.db.connect() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT COUNT(*) as cnt
                FROM care_team
                WHERE clinician_id = ?
                  AND patient_id = ?
                  AND is_active = 1
                  AND consent_status = 'GRANTED'
                  AND valid_from <= ?
                  AND valid_to >= ?
                """,
                (clinician_id, patient_id, now_iso, now_iso),
            )
            row = cursor.fetchone()
            return row["cnt"] > 0

    def can_caregiver_view_progress(self, caregiver_id: str, patient_id: str) -> bool:
        """
        Verifies caregiver authorization:
        - can_view_health = 1
        - is_active = 1
        - valid_from <= now <= valid_to
        """
        now_iso = datetime.now(timezone.utc).isoformat()
        with self.db.connect() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT COUNT(*) as cnt
                FROM patient_caregivers
                WHERE caregiver_id = ?
                  AND patient_id = ?
                  AND can_view_health = 1
                  AND is_active = 1
                  AND valid_from <= ?
                  AND valid_to >= ?
                """,
                (caregiver_id, patient_id, now_iso, now_iso),
            )
            row = cursor.fetchone()
            return row["cnt"] > 0

    def get_patient(self, patient_id: str) -> Optional[Patient]:
        with self.db.connect() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT * FROM patients WHERE patient_id = ?",
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
