"""
Patient Profile Synchronization Service.
Keeps data/users/*.json and shared/data/patients.json in 100% bidirectional sync
with data/unified_care.sqlite3 (patients, users, caregivers, patient_caregivers, legacy_patient_map).
Ensures that patients registered on Desktop (Camera/AI) immediately reflect on Web Portals
(Caregiver & Clinician) and vice versa.
"""

from __future__ import annotations

import glob
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from shared.database.connection import UnifiedDatabaseManager
from shared.database.outbox_helper import insert_outbox_event
from shared.services.credential_security import protect_profile_credentials, upgrade_legacy_password, DISABLED
from features.exercise_rehab.rehab_session_adapter import ensure_default_rehab_plans

logger = logging.getLogger("PatientSyncService")
UTC = timezone.utc


def resolve_unified_patient_id(conn: Any, legacy_id: str) -> str:
    """Resolve legacy ID ('1', '2', etc.) to unified patient_id ('patient_001', etc.)."""
    clean_id = str(legacy_id).strip()
    if clean_id.startswith("patient_"):
        return clean_id

    row = conn.fetchone(
        "SELECT patient_id FROM legacy_patient_map WHERE legacy_id = ?",
        (clean_id,),
    )
    if row and row.get("patient_id"):
        return row["patient_id"]

    if clean_id.isdigit():
        return f"patient_{clean_id.zfill(3)}"
    return f"patient_{clean_id}"


def sync_single_patient(db: UnifiedDatabaseManager, profile: Dict[str, Any], emit_events: bool = True) -> str:
    """
    Sync a single patient profile dictionary into data/unified_care.sqlite3.
    Returns the resolved canonical patient_id.
    """
    profile = protect_profile_credentials(profile)
    now_iso = datetime.now(UTC).isoformat()
    future_iso = "2099-12-31T23:59:59+00:00"

    raw_pid = str(profile.get("patient_id") or profile.get("user_id") or "1").strip()
    pname = (profile.get("name") or profile.get("full_name") or f"Bệnh nhân {raw_pid}").strip()
    age = int(profile.get("age", 70))
    gender = profile.get("gender", "Nam")
    notes = str(profile.get("notes", "")).strip()

    # Calculate approximate DOB from age if not specified
    birth_year = max(1900, datetime.now().year - age)
    dob = profile.get("date_of_birth") or f"{birth_year}-01-01"

    cg = profile.get("caregiver") or {}
    cg_name = (cg.get("name") or "Người chăm sóc").strip()
    cg_email = str(cg.get("email") or profile.get("caregiver_email") or "").strip().lower()
    cg_rel = (cg.get("relationship") or "Người thân").strip()
    cg_phone = str(cg.get("phone") or profile.get("caregiver_phone") or "").strip()
    password_hash = upgrade_legacy_password(cg.get("password_hash") or DISABLED)

    with db.connect() as conn:
        pat_id = resolve_unified_patient_id(conn, raw_pid)
        pat_user_id = f"user_pat_{pat_id.replace('patient_', '')}"

        # 1. Ensure patient user in users table
        existing_pat_user = conn.fetchone("SELECT user_id FROM users WHERE user_id = ?", (pat_user_id,))
        if existing_pat_user:
            conn.execute(
                "UPDATE users SET full_name = ? WHERE user_id = ?",
                (pname, pat_user_id),
            )
        else:
            pat_email = f"{pat_id}@unifiedcare.local"
            conn.execute(
                """
                INSERT INTO users (user_id, email, phone, full_name, password_hash, role, is_active, created_at)
                VALUES (?, ?, '', ?, '!', 'patient', 1, ?)
                """,
                (pat_user_id, pat_email, pname, now_iso),
            )

        # 2. Ensure patient in patients table
        existing_pat = conn.fetchone("SELECT patient_id FROM patients WHERE patient_id = ?", (pat_id,))
        if existing_pat:
            # A birth date already on record is exact; the profile's "age" is only an estimate
            # (age 70 -> "1956-01-01"), so it may fill a blank but never replace a real date.
            conn.execute(
                """
                UPDATE patients
                SET full_name = ?, gender = ?, notes = ?,
                    date_of_birth = CASE
                        WHEN ? = 1 OR date_of_birth IS NULL OR date_of_birth = '' THEN ?
                        ELSE date_of_birth END
                WHERE patient_id = ?
                """,
                (pname, gender, notes, 1 if profile.get("date_of_birth") else 0, dob, pat_id),
            )
        else:
            conn.execute(
                """
                INSERT INTO patients (patient_id, user_id, full_name, date_of_birth, gender, timezone, status, notes)
                VALUES (?, ?, ?, ?, ?, 'Asia/Ho_Chi_Minh', 'ACTIVE', ?)
                """,
                (pat_id, pat_user_id, pname, dob, gender, notes),
            )

        # 3. Ensure legacy map
        conn.execute(
            """
            INSERT INTO legacy_patient_map (legacy_id, source_system, patient_id)
            VALUES (?, 'camera_face', ?)
            ON CONFLICT(legacy_id) DO UPDATE SET patient_id = ?
            """,
            (raw_pid, pat_id, pat_id),
        )

        # 4. Sync caregiver user
        caregiver_id = ""
        cg_user_id = ""
        is_new_caregiver = False
        if cg_email:
            existing_cg_user = conn.fetchone(
                "SELECT user_id, password_hash, phone FROM users WHERE LOWER(email) = ?", (cg_email,)
            )
            if existing_cg_user:
                cg_user_id = existing_cg_user["user_id"]
                conn.execute(
                    "UPDATE users SET full_name = ?, phone = COALESCE(NULLIF(?, ''), phone) WHERE user_id = ?",
                    (cg_name, cg_phone, cg_user_id),
                )
            else:
                is_new_caregiver = True
                cg_user_id = f"user_care_{raw_pid}"
                conn.execute(
                    """
                    INSERT INTO users (user_id, email, phone, full_name, password_hash, role, is_active, created_at)
                    VALUES (?, ?, ?, ?, ?, 'caregiver', 1, ?)
                    """,
                    (cg_user_id, cg_email, cg_phone, cg_name, password_hash, now_iso),
                )

            # Ensure row in caregivers
            cg_rec = conn.fetchone("SELECT caregiver_id FROM caregivers WHERE user_id = ?", (cg_user_id,))
            if cg_rec:
                caregiver_id = cg_rec["caregiver_id"]
                conn.execute(
                    "UPDATE caregivers SET relationship_desc = ?, email_verified = 1, phone_verified = 1 WHERE caregiver_id = ?",
                    (cg_rel, caregiver_id),
                )
            else:
                caregiver_id = f"cg_{raw_pid}"
                taken = conn.fetchone("SELECT caregiver_id FROM caregivers WHERE caregiver_id = ?", (caregiver_id,))
                if taken:
                    caregiver_id = f"cg_{raw_pid}_{int(datetime.now().timestamp())}"
                conn.execute(
                    """
                    INSERT INTO caregivers (caregiver_id, user_id, email_verified, phone_verified, relationship_desc)
                    VALUES (?, ?, 1, 1, ?)
                    """,
                    (caregiver_id, cg_user_id, cg_rel),
                )

            # Link in patient_caregivers as primary caregiver
            pc_id = f"pc_{raw_pid}_{caregiver_id}"
            conn.execute(
                """
                INSERT INTO patient_caregivers (
                    patient_caregiver_id, patient_id, caregiver_id, relationship_type,
                    is_primary, notification_priority, can_edit_schedule, can_view_health,
                    consent_status, valid_from, valid_to, is_active
                ) VALUES (?, ?, ?, ?, 1, 1, 1, 1, 'GRANTED', ?, ?, 1)
                ON CONFLICT(patient_id, caregiver_id) DO UPDATE SET
                    relationship_type = ?
                """,
                (pc_id, pat_id, caregiver_id, cg_rel, now_iso, future_iso, cg_rel),
            )

        # 4b. Ensure standard rehabilitation plans (arm_raise, sit_to_stand, marching)
        ensure_default_rehab_plans(conn, pat_id)

        # 5. Insert outbox events for real-time web sync
        if emit_events:
            insert_outbox_event(
                conn=conn,
                patient_id=pat_id,
                event_type="patient.updated",
                aggregate_type="patient",
                aggregate_id=pat_id,
                aggregate_version=1,
                occurred_at=now_iso,
                actor_id="desktop_registration",
                source="elder_app",
                payload_minimal={
                    "patient_id": pat_id,
                    "full_name": pname,
                    "gender": gender,
                    "caregiver_name": cg_name,
                    "caregiver_email": cg_email,
                },
            )

            if cg_email and caregiver_id and is_new_caregiver:
                insert_outbox_event(
                    conn=conn,
                    patient_id=pat_id,
                    event_type="caregiver.registered",
                    aggregate_type="caregiver",
                    aggregate_id=caregiver_id,
                    aggregate_version=1,
                    occurred_at=now_iso,
                    actor_id="desktop_registration",
                    source="elder_app",
                    payload_minimal={
                        "caregiver_id": caregiver_id,
                        "user_id": cg_user_id,
                        "email": cg_email,
                        "full_name": cg_name,
                        "phone": cg_phone,
                        "relationship_type": cg_rel,
                        "patient_id": pat_id,
                    },
                )

        conn.commit()

        # The outbox relay checks patient access before delivering events.

        logger.info("Patient profile synchronized")
        return pat_id


def sync_all_patient_profiles(db: UnifiedDatabaseManager, repo_root: Optional[Path] = None) -> int:
    """
    Scans data/users/*.json and shared/data/patients.json and syncs all into unified_care.sqlite3.
    Also ensures unified_care.sqlite3 patients are exported back to shared/data/patients.json.
    """
    root = repo_root or Path(__file__).resolve().parents[2]
    users_dir = root / "data" / "users"
    patients_file = root / "shared" / "data" / "patients.json"

    profiles: Dict[str, Dict[str, Any]] = {}

    # 1. Read shared/data/patients.json
    if patients_file.exists():
        try:
            with open(patients_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                protected = protect_profile_credentials(data)
                if protected != data:
                    patients_file.write_text(json.dumps(protected, ensure_ascii=False, indent=2), encoding="utf-8")
                data = protected
                if isinstance(data, dict):
                    profiles.update(data)
        except Exception as e:
            logger.warning("Error reading %s: %s", patients_file, e)

    # 2. Read data/users/*.json
    if users_dir.exists():
        for p in users_dir.glob("*.json"):
            try:
                with open(p, "r", encoding="utf-8") as f:
                    u = json.load(f)
                    protected = protect_profile_credentials(u)
                    if protected != u:
                        p.write_text(json.dumps(protected, ensure_ascii=False, indent=2), encoding="utf-8")
                    u = protected
                    pid = str(u.get("patient_id") or u.get("user_id") or p.stem)
                    if pid not in profiles:
                        profiles[pid] = u
                    else:
                        # Merge prioritizing newer fields
                        profiles[pid].update(u)
            except Exception as e:
                logger.warning("Error reading %s: %s", p, e)

    synced_count = 0
    for pid, prof in profiles.items():
        try:
            sync_single_patient(db, prof, emit_events=False)
            synced_count += 1
        except Exception as e:
            logger.error("Failed to sync patient %s: %s", pid, e)

    # 3. Two-way export: ensure shared/data/patients.json reflects unified patients
    # (Skip export if this is an in-memory database used during tests)
    if getattr(db, "db_path", None) and str(db.db_path) == ":memory:":
        logger.info("Completed patient profile sync (in-memory mode, disk write skipped): %d patients.", synced_count)
        return synced_count

    try:
        with db.connect() as conn:
            all_db_patients = conn.fetchall(
                """
                SELECT p.*, pc.relationship_type, u_cg.full_name as cg_name, u_cg.email as cg_email, u_cg.phone as cg_phone
                FROM patients p
                LEFT JOIN patient_caregivers pc ON p.patient_id = pc.patient_id AND pc.is_primary = 1 AND pc.is_active = 1
                LEFT JOIN caregivers c ON pc.caregiver_id = c.caregiver_id
                LEFT JOIN users u_cg ON c.user_id = u_cg.user_id
                WHERE p.status = 'ACTIVE'
                """
            )
            for row in all_db_patients:
                # Find matching legacy id
                lrow = conn.fetchone(
                    "SELECT legacy_id FROM legacy_patient_map WHERE patient_id = ?",
                    (row["patient_id"],),
                )
                leg_id = lrow["legacy_id"] if lrow else row["patient_id"].replace("patient_", "")

                if leg_id not in profiles:
                    profiles[leg_id] = {
                        "user_id": leg_id,
                        "patient_id": leg_id,
                        "name": row["full_name"],
                        "age": 70,
                        "gender": row["gender"],
                        "notes": row["notes"] or "",
                        "caregiver": {
                            "name": row["cg_name"] or "Người chăm sóc",
                            "relationship": row["relationship_type"] or "Người thân",
                            "email": row["cg_email"] or "",
                            "phone": row["cg_phone"] or "",
                        },
                        "registered_at": datetime.now().isoformat(),
                        "last_updated": datetime.now().isoformat(),
                        "image_count": 1,
                    }

        patients_file.parent.mkdir(parents=True, exist_ok=True)
        with open(patients_file, "w", encoding="utf-8") as f:
            json.dump(profiles, f, ensure_ascii=False, indent=2)

    except Exception as e:
        logger.error("Error writing back to patients.json: %s", e)

    logger.info("Completed patient profile sync: %d patients synchronized.", synced_count)
    return synced_count
