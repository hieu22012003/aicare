"""
Authentication, Authorization and Relationship-Based Access Control.
Enforces:
- Caregiver access strictly via active patient_caregivers relationship
- Clinician access strictly via active care_team relationship
- Instant revocation enforcement
- Audit logging for sensitive reads, prescription edits, alert acks, and recommendations
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import re
import secrets
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from shared.database.connection import UnifiedDatabaseManager
from shared.database.outbox_helper import insert_outbox_event
from shared.services.credential_security import hash_password, verify_password, migrate_database_passwords, DISABLED

logger = logging.getLogger("AuthService")
_DUMMY_PASSWORD_HASH = hash_password(secrets.token_hex(32))


class AuthService:
    def __init__(self, db: UnifiedDatabaseManager) -> None:
        self.db = db
        migrate_database_passwords(db)
        # In-memory session store token -> session dict
        # Token format: secure random hex string
        self._sessions: Dict[str, Dict[str, Any]] = {}
        self._session_credentials: Dict[str, str] = {}

    def authenticate_credentials(
        self, email_or_id: str, password: str = ""
    ) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
        """Authenticate without disclosing account existence or stored credentials."""
        clean_id = str(email_or_id or "").strip()
        failure = (False, "Thông tin đăng nhập không hợp lệ", None)
        if not clean_id:
            return failure

        with self.db.connect() as conn:
            # 1. Direct user match: email, phone, user_id, or admin
            user = conn.fetchone(
                """
                SELECT * FROM users
                WHERE (
                    LOWER(email) = ? 
                    OR phone = ? 
                    OR user_id = ? 
                    OR (LOWER(?) IN ('admin', 'administrator', 'admin@unifiedcare.local') AND role = 'admin')
                )
                LIMIT 1
                """,
                (clean_id.lower(), clean_id, clean_id, clean_id.lower()),
            )

            # 2. Caregiver match by caregiver_id (e.g. 'cg_1')
            if not user:
                cg = conn.fetchone("SELECT user_id FROM caregivers WHERE caregiver_id = ?", (clean_id,))
                if cg:
                    user = conn.fetchone("SELECT * FROM users WHERE user_id = ?", (cg["user_id"],))

            # 3. Clinician match by clinician_id (e.g. 'clin_001')
            if not user:
                cl = conn.fetchone("SELECT user_id FROM clinicians WHERE clinician_id = ?", (clean_id,))
                if cl:
                    user = conn.fetchone("SELECT * FROM users WHERE user_id = ?", (cl["user_id"],))

            # 4. Caregiver match by patient identifier (patient_id, legacy_id or full_name)
            if not user:
                pat = conn.fetchone(
                    """
                    SELECT p.patient_id FROM patients p
                    LEFT JOIN legacy_patient_map lpm ON p.patient_id = lpm.patient_id
                    WHERE p.patient_id = ? OR lpm.legacy_id = ? OR LOWER(p.full_name) = ?
                    LIMIT 1
                    """,
                    (clean_id, clean_id, clean_id.lower()),
                )
                if pat:
                    pc = conn.fetchone(
                        """
                        SELECT c.user_id FROM patient_caregivers pc
                        JOIN caregivers c ON pc.caregiver_id = c.caregiver_id
                        WHERE pc.patient_id = ? AND pc.is_active = 1
                        ORDER BY pc.is_primary DESC LIMIT 1
                        """,
                        (pat["patient_id"],),
                    )
                    if pc:
                        user = conn.fetchone("SELECT * FROM users WHERE user_id = ?", (pc["user_id"],))

            # 5. Full name match if exact
            if not user:
                matches = conn.fetchall("SELECT * FROM users WHERE LOWER(full_name) = ?", (clean_id.lower(),))
                if len(matches) == 1:
                    user = matches[0]

            stored = user.get("password_hash") if user else None
            valid = verify_password(password, stored if stored and stored != DISABLED else _DUMMY_PASSWORD_HASH)
            if not user or not user["is_active"] or not valid:
                return failure
            safe_user = {key: value for key, value in user.items() if key != "password_hash"}
            return True, "Xác thực thành công", safe_user

    def authenticate_user(self, email_or_id: str, password: str = "") -> Optional[Dict[str, Any]]:
        """Backwards-compatible authenticate_user method returning User dict or None."""
        ok, _, user = self.authenticate_credentials(email_or_id, password)
        return user if ok else None

    def register_caregiver(
        self,
        full_name: str,
        email: str,
        phone: str = "",
        password: str = "",
        patient_id: str = "",
        relationship_type: str = "Người thân",
    ) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
        """
        Self-service registration for Caregivers.
        Enforces:
        - Email uniqueness & standard validation
        - Active caregiver and patient_caregivers relationship created
        - Immediate authorization to view health and medication
        """
        clean_email = email.strip().lower()
        if not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", clean_email):
            return False, "Định dạng email không hợp lệ (ví dụ: contact-8c16d220@example.com)", None

        clean_name = full_name.strip()
        if not clean_name:
            return False, "Vui lòng nhập Họ và tên người nhà", None

        now_iso = datetime.now(timezone.utc).isoformat()
        future_iso = (datetime.now(timezone.utc) + timedelta(days=3650)).isoformat()
        user_id = f"user_care_{secrets.token_hex(4)}"
        caregiver_id = f"cg_{secrets.token_hex(4)}"
        rel_id = f"pcg_{secrets.token_hex(4)}"
        if not password:
            return False, "Vui lòng nhập mật khẩu", None
        pw_hash = hash_password(password)
        clean_phone = phone.strip() or "0900000000"
        rel_type = relationship_type.strip() or "Người thân"

        with self.db.connect() as conn:
            existing = conn.fetchone("SELECT user_id FROM users WHERE LOWER(email) = ?", (clean_email,))
            if existing:
                return False, f"Email '{clean_email}' đã được đăng ký trong hệ thống. Vui lòng chuyển sang trang Đăng nhập.", None

            # Verify target patient if provided or fallback
            target_patient_id = patient_id.strip() if patient_id else None
            if target_patient_id:
                p = conn.fetchone("SELECT patient_id FROM patients WHERE patient_id = ?", (target_patient_id,))
                if not p:
                    return False, f"Không tìm thấy hồ sơ người cao tuổi với mã '{target_patient_id}'", None
            else:
                p = conn.fetchone("SELECT patient_id FROM patients WHERE status = 'ACTIVE' ORDER BY patient_id LIMIT 1")
                if p:
                    target_patient_id = p["patient_id"]

            # Insert user
            conn.execute(
                """
                INSERT INTO users (user_id, email, phone, full_name, password_hash, role, is_active, created_at)
                VALUES (?, ?, ?, ?, ?, 'caregiver', 1, ?)
                """,
                (user_id, clean_email, clean_phone, clean_name, pw_hash, now_iso),
            )

            # Insert caregiver
            conn.execute(
                """
                INSERT INTO caregivers (caregiver_id, user_id, email_verified, phone_verified, relationship_desc)
                VALUES (?, ?, 1, 1, ?)
                """,
                (caregiver_id, user_id, rel_type),
            )

            # Insert patient_caregiver relationship if a patient exists
            if target_patient_id:
                conn.execute(
                    """
                    INSERT INTO patient_caregivers (
                        patient_caregiver_id, patient_id, caregiver_id, relationship_type,
                        is_primary, notification_priority, can_edit_schedule, can_view_health,
                        consent_status, valid_from, valid_to, is_active
                    ) VALUES (?, ?, ?, ?, 1, 1, 1, 1, 'GRANTED', ?, ?, 1)
                    """,
                    (rel_id, target_patient_id, caregiver_id, rel_type, now_iso, future_iso),
                )
                insert_outbox_event(
                    conn=conn,
                    patient_id=target_patient_id,
                    event_type="caregiver.registered",
                    aggregate_type="caregiver",
                    aggregate_id=caregiver_id,
                    aggregate_version=1,
                    occurred_at=now_iso,
                    actor_id=user_id,
                    source="web_registration",
                    payload_minimal={
                        "caregiver_id": caregiver_id,
                        "user_id": user_id,
                        "email": clean_email,
                        "full_name": clean_name,
                        "phone": clean_phone,
                        "relationship_type": rel_type,
                        "patient_id": target_patient_id,
                    },
                )
            else:
                from features.web_portal.sse_hub import sse_hub
                for c in list(sse_hub._clients.values()):
                    if c.active and c.role == "admin":
                        c.send_event({
                            "type": "caregiver.registered",
                            "patient_id": "",
                            "aggregate_id": caregiver_id,
                            "occurred_at": now_iso,
                            "payload": {
                                "caregiver_id": caregiver_id,
                                "user_id": user_id,
                                "email": clean_email,
                                "full_name": clean_name,
                                "phone": clean_phone,
                                "relationship_type": rel_type,
                                "patient_id": "",
                            },
                        })
            conn.commit()

            created_user = {
                "user_id": user_id,
                "email": clean_email,
                "full_name": clean_name,
                "role": "caregiver",
                "phone": clean_phone,
            }
            logger.info("Caregiver account created")
            return True, "Đăng ký thành công", created_user

    def issue_clinician_account(
        self,
        full_name: str,
        email: str,
        workplace: str = "Bệnh viện Lão khoa Trung Ương",
        specialty: str = "Lão khoa & Tim mạch",
        license_number: str = "CCHN-DEFAULT",
        phone: str = "0988888999",
        patient_ids: Optional[List[str]] = None,
        password: str = "",
    ) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
        """
        Hospital-issued Clinician account.
        Clinicians do NOT self-register to prevent unauthorized clinical roles.
        """
        clean_email = email.strip().lower()
        clean_name = full_name.strip()
        now_iso = datetime.now(timezone.utc).isoformat()
        future_iso = (datetime.now(timezone.utc) + timedelta(days=3650)).isoformat()
        user_id = f"user_doc_{secrets.token_hex(4)}"
        clinician_id = f"cl_{secrets.token_hex(4)}"

        with self.db.connect() as conn:
            existing = conn.fetchone("SELECT user_id FROM users WHERE LOWER(email) = ?", (clean_email,))
            if existing:
                return False, f"Email bác sĩ '{clean_email}' đã tồn tại", None

            conn.execute(
                """
                INSERT INTO users (user_id, email, phone, full_name, password_hash, role, is_active, created_at)
                VALUES (?, ?, ?, ?, ?, 'clinician', 1, ?)
                """,
                (user_id, clean_email, phone, clean_name, hash_password(password) if password else DISABLED, now_iso),
            )
            conn.execute(
                """
                INSERT INTO clinicians (clinician_id, user_id, workplace, specialty, license_number, is_verified, is_simulation)
                VALUES (?, ?, ?, ?, ?, 1, 1)
                """,
                (clinician_id, user_id, workplace, specialty, license_number),
            )

            # Assign to patients
            assigned_patients = patient_ids or ["patient_001", "patient_002"]
            for pid in assigned_patients:
                ct_id = f"ct_{secrets.token_hex(4)}"
                conn.execute(
                    """
                    INSERT INTO care_team (care_team_id, patient_id, clinician_id, role_description, valid_from, valid_to, consent_status, is_active)
                    VALUES (?, ?, ?, 'Bác sĩ điều trị', ?, ?, 'GRANTED', 1)
                    ON CONFLICT(patient_id, clinician_id) DO NOTHING
                    """,
                    (ct_id, pid, clinician_id, now_iso, future_iso),
                )
            conn.commit()

            return True, "Cấp tài khoản Bác sĩ thành công", {
                "user_id": user_id,
                "email": clean_email,
                "full_name": clean_name,
                "role": "clinician",
            }

    def create_session(self, user: Dict[str, Any]) -> str:
        """Create a new session token for authenticated user."""
        token = secrets.token_hex(24)
        current = self.db.fetchone("SELECT password_hash FROM users WHERE user_id=?", (user["user_id"],))
        if not current:
            raise ValueError("Không thể tạo phiên đăng nhập")
        self._session_credentials[token] = current["password_hash"]
        now = time.time()
        self._sessions[token] = {
            "token": token,
            "user_id": user["user_id"],
            "email": user["email"],
            "full_name": user["full_name"],
            "role": user["role"],
            "created_at": now,
            "expires_at": now + 86400 * 7,  # 7 days
        }
        return token

    def get_session(self, token: str) -> Optional[Dict[str, Any]]:
        """Verify token and return active session, or None."""
        if not token or token not in self._sessions:
            return None
        sess = self._sessions[token]
        current = self.db.fetchone("SELECT is_active, password_hash FROM users WHERE user_id=?", (sess["user_id"],))
        if (not current or not current["is_active"] or
                current["password_hash"] != self._session_credentials.get(token)):
            self.revoke_session(token)
            return None
        if time.time() > sess["expires_at"]:
            self.revoke_session(token)
            return None
        return sess

    def revoke_session(self, token: str) -> None:
        self._session_credentials.pop(token, None)
        if token in self._sessions:
            del self._sessions[token]

    def check_patient_access(
        self, user_id: str, patient_id: str, permission: str = "view"
    ) -> Tuple[bool, str]:
        """
        Server-side relationship check.
        Queries active database records at the moment of request:
        - Caregiver: patient_caregivers must be active, within valid_from/to, and have permission.
        - Clinician: care_team must be active, within valid_from/to, consent_status == 'GRANTED'.
        - Patient: user_id must match patient's user_id.
        - Admin: granted.
        """
        now_iso = datetime.now(timezone.utc).isoformat()

        with self.db.connect() as conn:
            user = conn.fetchone("SELECT * FROM users WHERE user_id = ? AND is_active = 1", (user_id,))
            if not user:
                return False, "Tài khoản không tồn tại hoặc đã bị khóa"

            role = user["role"]
            if role == "admin":
                return True, "Admin access granted"

            # Check if patient exists
            patient = conn.fetchone("SELECT * FROM patients WHERE patient_id = ?", (patient_id,))
            if not patient:
                return False, "Không tìm thấy hồ sơ người cao tuổi"

            if role == "patient":
                if patient["user_id"] == user_id:
                    return True, "Patient self-access"
                return False, "Người cao tuổi chỉ có quyền truy cập hồ sơ của chính mình"

            if role == "caregiver":
                cg = conn.fetchone("SELECT * FROM caregivers WHERE user_id = ?", (user_id,))
                if not cg:
                    return False, "Không tìm thấy hồ sơ người chăm sóc"

                # Check active relationship
                rel = conn.fetchone(
                    """
                    SELECT * FROM patient_caregivers
                    WHERE patient_id = ? AND caregiver_id = ?
                      AND is_active = 1
                      AND valid_from <= ? AND valid_to >= ?
                      AND consent_status = 'GRANTED'
                    """,
                    (patient_id, cg["caregiver_id"], now_iso, now_iso),
                )
                if not rel:
                    return False, "Bạn không có quyền hoặc quyền chăm sóc người bệnh này đã hết hiệu lực"

                if permission == "view_health" and not rel["can_view_health"]:
                    return False, "Người chăm sóc chưa được cấp quyền xem chỉ số sức khỏe"
                if permission == "edit_schedule" and not rel["can_edit_schedule"]:
                    return False, "Người chăm sóc chưa được cấp quyền sửa lịch thuốc"
                if permission == "edit_prescription":
                    return False, "Người chăm sóc không có quyền kê đơn hoặc sửa đơn thuốc (chỉ bác sĩ phụ trách)"

                return True, "Caregiver authorized"

            if role == "clinician":
                cl = conn.fetchone("SELECT * FROM clinicians WHERE user_id = ?", (user_id,))
                if not cl:
                    return False, "Không tìm thấy hồ sơ bác sĩ"

                # Check care_team assignment
                team = conn.fetchone(
                    """
                    SELECT * FROM care_team
                    WHERE patient_id = ? AND clinician_id = ?
                      AND is_active = 1
                      AND valid_from <= ? AND valid_to >= ?
                      AND consent_status = 'GRANTED'
                    """,
                    (patient_id, cl["clinician_id"], now_iso, now_iso),
                )
                if not team:
                    return False, "Bác sĩ không thuộc care team hoặc quyền theo dõi bệnh nhân này đã hết hiệu lực"

                return True, "Clinician authorized"

            return False, "Vai trò không hợp lệ"

    def list_accessible_patients(self, user_id: str) -> List[Dict[str, Any]]:
        """Return list of patients this user is legally authorized to see."""
        now_iso = datetime.now(timezone.utc).isoformat()
        with self.db.connect() as conn:
            user = conn.fetchone("SELECT * FROM users WHERE user_id = ?", (user_id,))
            if not user:
                return []

            role = user["role"]
            if role == "admin":
                return conn.fetchall("SELECT * FROM patients WHERE status = 'ACTIVE' ORDER BY full_name")

            if role == "caregiver":
                patients = conn.fetchall(
                    """
                    SELECT p.*, pc.relationship_type, pc.can_view_health, pc.can_edit_schedule, pc.is_primary
                    FROM patients p
                    JOIN patient_caregivers pc ON p.patient_id = pc.patient_id
                    JOIN caregivers c ON pc.caregiver_id = c.caregiver_id
                    WHERE c.user_id = ?
                      AND pc.is_active = 1
                      AND pc.valid_from <= ? AND pc.valid_to >= ?
                      AND pc.consent_status = 'GRANTED'
                    ORDER BY p.full_name
                    """,
                    (user_id, now_iso, now_iso),
                )
                for p in patients:
                    pid = p["patient_id"]
                    r_c = conn.fetchone("SELECT COUNT(*) as c FROM caregiver_reports WHERE patient_id = ? AND status = 'NEW'", (pid,))["c"]
                    a_c = conn.fetchone("SELECT COUNT(*) as c FROM alerts WHERE patient_id = ? AND status = 'NEW'", (pid,))["c"]
                    o_c = conn.fetchone("SELECT COUNT(*) as c FROM measurement_groups WHERE patient_id = ? AND recorded_at >= datetime('now', '-24 hours')", (pid,))["c"]
                    p["has_new_update"] = bool(r_c > 0 or a_c > 0 or o_c > 0)
                return patients

            if role == "clinician":
                patients = conn.fetchall(
                    """
                    SELECT p.*, ct.role_description, ct.valid_from, ct.valid_to
                    FROM patients p
                    JOIN care_team ct ON p.patient_id = ct.patient_id
                    JOIN clinicians cl ON ct.clinician_id = cl.clinician_id
                    WHERE cl.user_id = ?
                      AND ct.is_active = 1
                      AND ct.valid_from <= ? AND ct.valid_to >= ?
                      AND ct.consent_status = 'GRANTED'
                    ORDER BY p.full_name
                    """,
                    (user_id, now_iso, now_iso),
                )
                for p in patients:
                    pid = p["patient_id"]
                    r_c = conn.fetchone("SELECT COUNT(*) as c FROM caregiver_reports WHERE patient_id = ? AND status = 'NEW'", (pid,))["c"]
                    a_c = conn.fetchone("SELECT COUNT(*) as c FROM alerts WHERE patient_id = ? AND status = 'NEW'", (pid,))["c"]
                    o_c = conn.fetchone("SELECT COUNT(*) as c FROM measurement_groups WHERE patient_id = ? AND recorded_at >= datetime('now', '-24 hours')", (pid,))["c"]
                    p["has_new_update"] = bool(r_c > 0 or a_c > 0 or o_c > 0)
                    p["new_reports_count"] = r_c
                    p["new_alerts_count"] = a_c
                    p["new_observations_count"] = o_c
                return patients

            if role == "patient":
                return conn.fetchall("SELECT * FROM patients WHERE user_id = ?", (user_id,))

            return []

    def log_audit(
        self,
        user_id: str,
        action: str,
        entity_type: str,
        entity_id: str,
        details: Dict[str, Any] = None,
        ip_address: str = "127.0.0.1",
    ) -> None:
        """Append an immutable audit entry."""
        import uuid
        audit_id = f"aud_{uuid.uuid4().hex[:16]}"
        now_iso = datetime.now(timezone.utc).isoformat()
        details_str = json.dumps(details or {}, ensure_ascii=False)

        with self.db.connect() as conn:
            conn.execute(
                """
                INSERT INTO audit_events (audit_id, user_id, action, entity_type, entity_id, details, ip_address, timestamp_utc)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (audit_id, user_id, action, entity_type, entity_id, details_str, ip_address, now_iso),
            )
            conn.commit()

