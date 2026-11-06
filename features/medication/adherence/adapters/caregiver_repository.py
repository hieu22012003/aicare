"""
Caregiver Repository Implementation for Medication Adherence Module.
Enforces active relationship filtering, validity timestamps, consent, and email verification.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import List, Optional

from features.medication.adherence.database.connection import DatabaseManager
from features.medication.adherence.domain.contracts import ICaregiverRepository
from features.medication.adherence.domain.models import Caregiver, PatientCaregiver, User

logger = logging.getLogger("CaregiverRepository")


class SqlCaregiverRepository(ICaregiverRepository):
    """SQL Implementation of ICaregiverRepository."""

    def __init__(self, db: DatabaseManager) -> None:
        self.db = db

    def get_by_id(self, caregiver_id: str) -> Optional[Caregiver]:
        if not caregiver_id:
            return None
        with self.db.connect() as conn:
            row = conn.execute(
                """
                SELECT c.*, u.email, u.phone, u.full_name, u.is_active as user_active, u.created_at
                FROM caregivers c
                JOIN users u ON c.user_id = u.user_id
                WHERE c.caregiver_id = ?
                """,
                (caregiver_id.strip(),),
            ).fetchone()
            if not row:
                return None
            user_info = User(
                user_id=row["user_id"],
                email=row["email"],
                phone=row["phone"],
                full_name=row["full_name"],
                is_active=bool(row["user_active"]),
                created_at=row["created_at"],
            )
            return Caregiver(
                caregiver_id=row["caregiver_id"],
                user_id=row["user_id"],
                email_verified=bool(row["email_verified"]),
                phone_verified=bool(row["phone_verified"]),
                user_info=user_info,
            )

    def get_active_caregivers_for_patient(
        self, patient_id: str, at_time: Optional[datetime] = None
    ) -> List[PatientCaregiver]:
        """
        Query caregivers currently authorized and eligible to receive urgent alerts.
        Filters strictly by:
          1. pc.is_active = 1
          2. pc.consent_status = 'GRANTED'
          3. c.email_verified = 1
          4. pc.valid_from <= at_time <= pc.valid_to
        Ordered by notification_priority ASC.
        """
        if not patient_id:
            return []
        now = at_time or datetime.now(timezone.utc)
        now_iso = now.isoformat()

        query = """
            SELECT pc.*, u.full_name as caregiver_name, u.email as caregiver_email, u.phone as caregiver_phone
            FROM patient_caregivers pc
            JOIN caregivers c ON pc.caregiver_id = c.caregiver_id
            JOIN users u ON c.user_id = u.user_id
            WHERE pc.patient_id = ?
              AND pc.is_active = 1
              AND pc.consent_status = 'GRANTED'
              AND c.email_verified = 1
              AND pc.valid_from <= ?
              AND pc.valid_to >= ?
            ORDER BY pc.notification_priority ASC, pc.is_primary DESC
        """
        with self.db.connect() as conn:
            rows = conn.execute(query, (patient_id.strip(), now_iso, now_iso)).fetchall()
            return [
                PatientCaregiver(
                    patient_caregiver_id=r["patient_caregiver_id"],
                    patient_id=r["patient_id"],
                    caregiver_id=r["caregiver_id"],
                    relationship_type=r["relationship_type"],
                    is_primary=bool(r["is_primary"]),
                    notification_priority=r["notification_priority"],
                    can_edit_schedule=bool(r["can_edit_schedule"]),
                    can_view_health=bool(r["can_view_health"]),
                    consent_status=r["consent_status"],
                    valid_from=r["valid_from"],
                    valid_to=r["valid_to"],
                    is_active=bool(r["is_active"]),
                    caregiver_name=r["caregiver_name"],
                    caregiver_email=r["caregiver_email"],
                    caregiver_phone=r["caregiver_phone"],
                )
                for r in rows
            ]

    def list_caregivers_for_patient(self, patient_id: str) -> List[PatientCaregiver]:
        """List all caregivers for a patient including expired or unverified ones."""
        if not patient_id:
            return []
        query = """
            SELECT pc.*, u.full_name as caregiver_name, u.email as caregiver_email, u.phone as caregiver_phone
            FROM patient_caregivers pc
            JOIN caregivers c ON pc.caregiver_id = c.caregiver_id
            JOIN users u ON c.user_id = u.user_id
            WHERE pc.patient_id = ?
            ORDER BY pc.is_active DESC, pc.valid_to DESC
        """
        with self.db.connect() as conn:
            rows = conn.execute(query, (patient_id.strip(),)).fetchall()
            return [
                PatientCaregiver(
                    patient_caregiver_id=r["patient_caregiver_id"],
                    patient_id=r["patient_id"],
                    caregiver_id=r["caregiver_id"],
                    relationship_type=r["relationship_type"],
                    is_primary=bool(r["is_primary"]),
                    notification_priority=r["notification_priority"],
                    can_edit_schedule=bool(r["can_edit_schedule"]),
                    can_view_health=bool(r["can_view_health"]),
                    consent_status=r["consent_status"],
                    valid_from=r["valid_from"],
                    valid_to=r["valid_to"],
                    is_active=bool(r["is_active"]),
                    caregiver_name=r["caregiver_name"],
                    caregiver_email=r["caregiver_email"],
                    caregiver_phone=r["caregiver_phone"],
                )
                for r in rows
            ]
