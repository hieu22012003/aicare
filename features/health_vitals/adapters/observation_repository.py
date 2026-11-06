"""
Observation Repository implementation using SQLite.
Handles measurement groups, health observations, corrections with audit trails,
reviews, clinical messages, and symptoms with ACID guarantees.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Dict, List, Optional

from features.health_vitals.database.connection import DatabaseManager
from features.health_vitals.domain.contracts import IObservationRepository
from features.health_vitals.domain.models import (
    BloodPressureReading,
    ClinicalMessage,
    ClinicianReview,
    HealthObservation,
    MeasurementComponent,
    MeasurementGroup,
    ObservationType,
    SymptomEntry,
)

logger = logging.getLogger("ObservationRepository")


class SqlObservationRepository(IObservationRepository):
    def __init__(self, db: DatabaseManager) -> None:
        self.db = db

    def get_observation_types(self) -> Dict[str, ObservationType]:
        with self.db.connect() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT observation_type_id, code, name, standard_unit, min_plausible_value, max_plausible_value, category
                FROM observation_types
                """
            )
            rows = cursor.fetchall()
            return {
                row["code"]: ObservationType(
                    observation_type_id=row["observation_type_id"],
                    code=row["code"],
                    name=row["name"],
                    standard_unit=row["standard_unit"],
                    min_plausible_value=float(row["min_plausible_value"]),
                    max_plausible_value=float(row["max_plausible_value"]),
                    category=row["category"],
                )
                for row in rows
            }

    def get_group_by_request_id(self, client_request_id: str) -> Optional[MeasurementGroup]:
        with self.db.connect() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT mg.group_id, mg.patient_id, mg.measured_at, mg.recorded_at,
                       mg.entered_by_user_id, u.full_name as entered_by_name,
                       mg.source_type, mg.device_label, mg.context, mg.status, mg.client_request_id
                FROM measurement_groups mg
                JOIN users u ON mg.entered_by_user_id = u.user_id
                WHERE mg.client_request_id = ?
                """,
                (client_request_id,),
            )
            row = cursor.fetchone()
            if not row:
                return None

            group = MeasurementGroup(
                group_id=row["group_id"],
                patient_id=row["patient_id"],
                measured_at=row["measured_at"],
                recorded_at=row["recorded_at"],
                entered_by_user_id=row["entered_by_user_id"],
                entered_by_name=row["entered_by_name"],
                source_type=row["source_type"],
                device_label=row["device_label"],
                context=row["context"] or "",
                status=row["status"],
                client_request_id=row["client_request_id"],
            )

            # Load child observations
            cursor.execute(
                """
                SELECT ho.observation_id, ho.group_id, ho.patient_id, ho.observation_type_id,
                       ot.code as type_code, ot.name as type_name, ho.value_numeric, ho.unit_code,
                       mg.measured_at, mg.recorded_at, mg.source_type, mg.device_label,
                       u.full_name as entered_by_name, ho.status, ho.corrects_observation_id, ho.notes
                FROM health_observations ho
                JOIN observation_types ot ON ho.observation_type_id = ot.observation_type_id
                JOIN measurement_groups mg ON ho.group_id = mg.group_id
                JOIN users u ON mg.entered_by_user_id = u.user_id
                WHERE ho.group_id = ?
                """,
                (group.group_id,),
            )
            obs_rows = cursor.fetchall()
            group.observations = [
                HealthObservation(
                    observation_id=o["observation_id"],
                    group_id=o["group_id"],
                    patient_id=o["patient_id"],
                    observation_type_id=o["observation_type_id"],
                    type_code=o["type_code"],
                    type_name=o["type_name"],
                    value_numeric=float(o["value_numeric"]),
                    unit_code=o["unit_code"],
                    measured_at=o["measured_at"],
                    recorded_at=o["recorded_at"],
                    source_type=o["source_type"],
                    device_label=o["device_label"],
                    entered_by_name=o["entered_by_name"],
                    status=o["status"],
                    corrects_observation_id=o["corrects_observation_id"],
                    notes=o["notes"] or "",
                )
                for o in obs_rows
            ]
            return group

    def create_measurement_group(
        self,
        group: MeasurementGroup,
        components: List[MeasurementComponent],
    ) -> MeasurementGroup:
        """
        Create a measurement group with its constituent observations atomically.
        Idempotent: if client_request_id exists, returns existing group without duplicates.
        """
        existing = self.get_group_by_request_id(group.client_request_id)
        if existing:
            logger.info(f"Idempotent hit for client_request_id '{group.client_request_id}'. Returning existing group.")
            return existing

        types_map = self.get_observation_types()

        with self.db.connect() as conn:
            cursor = conn.cursor()

            # Insert measurement group
            cursor.execute(
                """
                INSERT INTO measurement_groups (
                    group_id, patient_id, measured_at, recorded_at,
                    entered_by_user_id, source_type, device_label,
                    context, status, client_request_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    group.group_id,
                    group.patient_id,
                    group.measured_at,
                    group.recorded_at,
                    group.entered_by_user_id,
                    group.source_type,
                    group.device_label,
                    group.context,
                    group.status,
                    group.client_request_id,
                ),
            )

            # Insert each observation component
            created_observations: List[HealthObservation] = []
            for comp in components:
                obs_type = types_map.get(comp.type_code)
                if not obs_type:
                    raise ValueError(f"Unknown observation type code: '{comp.type_code}'")

                obs_id = str(uuid.uuid4()).replace("-", "")
                cursor.execute(
                    """
                    INSERT INTO health_observations (
                        observation_id, group_id, patient_id, observation_type_id,
                        value_numeric, unit_code, status, notes
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        obs_id,
                        group.group_id,
                        group.patient_id,
                        obs_type.observation_type_id,
                        comp.value_numeric,
                        comp.unit_code,
                        "active",
                        comp.notes,
                    ),
                )

                created_observations.append(
                    HealthObservation(
                        observation_id=obs_id,
                        group_id=group.group_id,
                        patient_id=group.patient_id,
                        observation_type_id=obs_type.observation_type_id,
                        type_code=comp.type_code,
                        type_name=obs_type.name,
                        value_numeric=comp.value_numeric,
                        unit_code=comp.unit_code,
                        measured_at=group.measured_at,
                        recorded_at=group.recorded_at,
                        source_type=group.source_type,
                        device_label=group.device_label,
                        entered_by_name=group.entered_by_name,
                        status="active",
                        notes=comp.notes,
                    )
                )

            group.observations = created_observations
            return group

    def get_observations(
        self,
        patient_id: str,
        type_codes: Optional[List[str]] = None,
        start_time: Optional[str] = None,
        end_time: Optional[str] = None,
        status: str = "active",
        limit: int = 100,
        offset: int = 0,
    ) -> List[HealthObservation]:
        """Query individual health observations with filtering and pagination."""
        params: list = [patient_id, status]
        query = """
            SELECT ho.observation_id, ho.group_id, ho.patient_id, ho.observation_type_id,
                   ot.code as type_code, ot.name as type_name, ho.value_numeric, ho.unit_code,
                   mg.measured_at, mg.recorded_at, mg.source_type, mg.device_label,
                   u.full_name as entered_by_name, ho.status, ho.corrects_observation_id, ho.notes
            FROM health_observations ho
            JOIN observation_types ot ON ho.observation_type_id = ot.observation_type_id
            JOIN measurement_groups mg ON ho.group_id = mg.group_id
            JOIN users u ON mg.entered_by_user_id = u.user_id
            WHERE ho.patient_id = ? AND ho.status = ?
        """

        if type_codes:
            placeholders = ",".join(["?"] * len(type_codes))
            query += f" AND ot.code IN ({placeholders})"
            params.extend(type_codes)

        if start_time:
            query += " AND mg.measured_at >= ?"
            params.append(start_time)

        if end_time:
            query += " AND mg.measured_at <= ?"
            params.append(end_time)

        query += " ORDER BY mg.measured_at DESC, mg.recorded_at DESC, ho.observation_id DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])


        with self.db.connect() as conn:
            cursor = conn.cursor()
            cursor.execute(query, tuple(params))
            rows = cursor.fetchall()

            return [
                HealthObservation(
                    observation_id=r["observation_id"],
                    group_id=r["group_id"],
                    patient_id=r["patient_id"],
                    observation_type_id=r["observation_type_id"],
                    type_code=r["type_code"],
                    type_name=r["type_name"],
                    value_numeric=float(r["value_numeric"]),
                    unit_code=r["unit_code"],
                    measured_at=r["measured_at"],
                    recorded_at=r["recorded_at"],
                    source_type=r["source_type"],
                    device_label=r["device_label"],
                    entered_by_name=r["entered_by_name"],
                    status=r["status"],
                    corrects_observation_id=r["corrects_observation_id"],
                    notes=r["notes"] or "",
                )
                for r in rows
            ]

    def get_blood_pressure_readings(
        self,
        patient_id: str,
        start_time: Optional[str] = None,
        end_time: Optional[str] = None,
        status: str = "active",
        limit: int = 100,
    ) -> List[BloodPressureReading]:
        """
        Query blood pressure pairings. Systolic and diastolic are matched from the SAME group.
        Guarantees that a reading never combines systolic from day X with diastolic from day Y.
        """
        params: list = [patient_id, status, status]
        query = """
            SELECT mg.group_id, mg.patient_id, mg.measured_at, mg.recorded_at,
                   mg.source_type, mg.device_label, u.full_name as entered_by_name, mg.status,
                   MAX(CASE WHEN ot.code = 'blood_pressure_systolic' THEN ho.value_numeric END) as systolic,
                   MAX(CASE WHEN ot.code = 'blood_pressure_diastolic' THEN ho.value_numeric END) as diastolic,
                   MAX(CASE WHEN ot.code = 'blood_pressure_systolic' THEN ho.unit_code END) as unit_code,
                   MAX(ho.notes) as notes
            FROM measurement_groups mg
            JOIN health_observations ho ON mg.group_id = ho.group_id
            JOIN observation_types ot ON ho.observation_type_id = ot.observation_type_id
            JOIN users u ON mg.entered_by_user_id = u.user_id
            WHERE mg.patient_id = ? AND mg.status = ? AND ho.status = ?
              AND ot.code IN ('blood_pressure_systolic', 'blood_pressure_diastolic')
        """

        if start_time:
            query += " AND mg.measured_at >= ?"
            params.append(start_time)

        if end_time:
            query += " AND mg.measured_at <= ?"
            params.append(end_time)

        query += " GROUP BY mg.group_id ORDER BY mg.measured_at DESC, mg.recorded_at DESC, mg.group_id DESC LIMIT ?"
        params.append(limit)


        with self.db.connect() as conn:
            cursor = conn.cursor()
            cursor.execute(query, tuple(params))
            rows = cursor.fetchall()

            return [
                BloodPressureReading(
                    group_id=r["group_id"],
                    patient_id=r["patient_id"],
                    measured_at=r["measured_at"],
                    recorded_at=r["recorded_at"],
                    systolic=float(r["systolic"]) if r["systolic"] is not None else None,
                    diastolic=float(r["diastolic"]) if r["diastolic"] is not None else None,
                    unit_code=r["unit_code"] or "mmHg",
                    source_type=r["source_type"],
                    device_label=r["device_label"],
                    entered_by_name=r["entered_by_name"],
                    status=r["status"],
                    notes=r["notes"] or "",
                )
                for r in rows
            ]

    def correct_observation(
        self,
        observation_id: str,
        new_value: float,
        new_unit: str,
        entered_by_user_id: str,
        client_request_id: str,
        notes: str = "",
    ) -> HealthObservation:
        """
        Correct an incorrect observation.
        The old observation is set to 'corrected', NOT overwritten or deleted.
        A new observation row is inserted pointing back to the old one via corrects_observation_id.
        """
        now_iso = datetime.now(timezone.utc).isoformat()
        with self.db.connect() as conn:
            cursor = conn.cursor()

            # 1. Fetch old observation
            cursor.execute(
                """
                SELECT ho.observation_id, ho.group_id, ho.patient_id, ho.observation_type_id,
                       ot.code as type_code, ot.name as type_name, ho.value_numeric, ho.unit_code,
                       mg.measured_at, mg.source_type, mg.device_label
                FROM health_observations ho
                JOIN observation_types ot ON ho.observation_type_id = ot.observation_type_id
                JOIN measurement_groups mg ON ho.group_id = mg.group_id
                WHERE ho.observation_id = ?
                """,
                (observation_id,),
            )
            old = cursor.fetchone()
            if not old:
                raise ValueError(f"Observation {observation_id} not found.")

            # 2. Mark old observation as corrected
            cursor.execute(
                "UPDATE health_observations SET status = 'corrected' WHERE observation_id = ?",
                (observation_id,),
            )

            # 3. Create a correction measurement group
            new_group_id = str(uuid.uuid4()).replace("-", "")
            cursor.execute(
                """
                INSERT INTO measurement_groups (
                    group_id, patient_id, measured_at, recorded_at,
                    entered_by_user_id, source_type, device_label,
                    context, status, client_request_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    new_group_id,
                    old["patient_id"],
                    old["measured_at"],
                    now_iso,
                    entered_by_user_id,
                    old["source_type"],
                    old["device_label"],
                    "correction",
                    "active",
                    client_request_id,
                ),
            )

            # 4. Insert corrected observation linking to old
            new_obs_id = str(uuid.uuid4()).replace("-", "")
            cursor.execute(
                """
                INSERT INTO health_observations (
                    observation_id, group_id, patient_id, observation_type_id,
                    value_numeric, unit_code, status, corrects_observation_id, notes
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    new_obs_id,
                    new_group_id,
                    old["patient_id"],
                    old["observation_type_id"],
                    new_value,
                    new_unit,
                    "active",
                    observation_id,
                    notes or f"Hiệu chỉnh từ bản ghi {observation_id}",
                ),
            )

            cursor.execute("SELECT full_name FROM users WHERE user_id = ?", (entered_by_user_id,))
            user_row = cursor.fetchone()
            user_name = user_row["full_name"] if user_row else "Hệ thống"

            return HealthObservation(
                observation_id=new_obs_id,
                group_id=new_group_id,
                patient_id=old["patient_id"],
                observation_type_id=old["observation_type_id"],
                type_code=old["type_code"],
                type_name=old["type_name"],
                value_numeric=new_value,
                unit_code=new_unit,
                measured_at=old["measured_at"],
                recorded_at=now_iso,
                source_type=old["source_type"],
                device_label=old["device_label"],
                entered_by_name=user_name,
                status="active",
                corrects_observation_id=observation_id,
                notes=notes,
            )

    def create_review(self, review: ClinicianReview) -> ClinicianReview:
        with self.db.connect() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO clinician_reviews (
                    review_id, clinician_id, patient_id, reviewed_at,
                    period_start, period_end, status, notes
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    review.review_id,
                    review.clinician_id,
                    review.patient_id,
                    review.reviewed_at,
                    review.period_start,
                    review.period_end,
                    review.status,
                    review.notes,
                ),
            )
            return review

    def get_latest_review(self, patient_id: str, clinician_id: str) -> Optional[ClinicianReview]:
        with self.db.connect() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT review_id, clinician_id, patient_id, reviewed_at, period_start, period_end, status, notes
                FROM clinician_reviews
                WHERE patient_id = ? AND clinician_id = ? AND status = 'CONFIRMED'
                ORDER BY reviewed_at DESC
                LIMIT 1
                """,
                (patient_id, clinician_id),
            )
            row = cursor.fetchone()
            if not row:
                return None
            return ClinicianReview(
                review_id=row["review_id"],
                clinician_id=row["clinician_id"],
                patient_id=row["patient_id"],
                reviewed_at=row["reviewed_at"],
                period_start=row["period_start"],
                period_end=row["period_end"],
                status=row["status"],
                notes=row["notes"] or "",
            )

    def create_message(self, message: ClinicalMessage) -> ClinicalMessage:
        with self.db.connect() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO clinical_messages (
                    message_id, patient_id, clinician_id, review_id,
                    observation_id, body, visibility, created_at, status
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    message.message_id,
                    message.patient_id,
                    message.clinician_id,
                    message.review_id,
                    message.observation_id,
                    message.body,
                    message.visibility,
                    message.created_at,
                    message.status,
                ),
            )
            return message

    def get_messages(self, patient_id: str, limit: int = 50) -> List[ClinicalMessage]:
        with self.db.connect() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT cm.message_id, cm.patient_id, cm.clinician_id, u.full_name as clinician_name,
                       cm.review_id, cm.observation_id, cm.body, cm.visibility, cm.created_at, cm.status,
                       (SELECT COUNT(*) FROM message_receipts mr WHERE mr.message_id = cm.message_id) as receipt_count
                FROM clinical_messages cm
                JOIN clinicians c ON cm.clinician_id = c.clinician_id
                JOIN users u ON c.user_id = u.user_id
                WHERE cm.patient_id = ?
                ORDER BY cm.created_at DESC
                LIMIT ?
                """,
                (patient_id, limit),
            )
            rows = cursor.fetchall()
            return [
                ClinicalMessage(
                    message_id=r["message_id"],
                    patient_id=r["patient_id"],
                    clinician_id=r["clinician_id"],
                    clinician_name=r["clinician_name"],
                    review_id=r["review_id"],
                    observation_id=r["observation_id"],
                    body=r["body"],
                    visibility=r["visibility"],
                    created_at=r["created_at"],
                    status=r["status"],
                    is_read_by_patient=r["receipt_count"] > 0,
                )
                for r in rows
            ]

    def mark_message_read(self, message_id: str, user_id: str) -> None:
        now_iso = datetime.now(timezone.utc).isoformat()
        with self.db.connect() as conn:
            cursor = conn.cursor()
            receipt_id = str(uuid.uuid4()).replace("-", "")
            cursor.execute(
                """
                INSERT OR IGNORE INTO message_receipts (receipt_id, message_id, user_id, read_at)
                VALUES (?, ?, ?, ?)
                """,
                (receipt_id, message_id, user_id, now_iso),
            )
            cursor.execute(
                "UPDATE clinical_messages SET status = 'read' WHERE message_id = ?",
                (message_id,),
            )

    def record_symptom(self, symptom: SymptomEntry) -> SymptomEntry:
        with self.db.connect() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO symptom_entries (
                    symptom_id, patient_id, reported_by_user_id,
                    reported_at, text, discomfort_level, status
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    symptom.symptom_id,
                    symptom.patient_id,
                    symptom.reported_by_user_id,
                    symptom.reported_at,
                    symptom.text,
                    symptom.discomfort_level,
                    symptom.status,
                ),
            )
            return symptom

    def get_symptoms(self, patient_id: str, limit: int = 20) -> List[SymptomEntry]:
        with self.db.connect() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT se.symptom_id, se.patient_id, se.reported_by_user_id, u.full_name as reported_by_name,
                       se.reported_at, se.text, se.discomfort_level, se.status
                FROM symptom_entries se
                JOIN users u ON se.reported_by_user_id = u.user_id
                WHERE se.patient_id = ? AND se.status = 'active'
                ORDER BY se.reported_at DESC
                LIMIT ?
                """,
                (patient_id, limit),
            )
            rows = cursor.fetchall()
            return [
                SymptomEntry(
                    symptom_id=r["symptom_id"],
                    patient_id=r["patient_id"],
                    reported_by_user_id=r["reported_by_user_id"],
                    reported_by_name=r["reported_by_name"],
                    reported_at=r["reported_at"],
                    text=r["text"],
                    discomfort_level=r["discomfort_level"],
                    status=r["status"],
                )
                for r in rows
            ]
