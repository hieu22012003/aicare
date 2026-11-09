"""
REST API Router and Business Logic Controllers for Caregiver & Clinician Portals.
Implements:
- Role & relationship security checks on every endpoint
- Vitals trends (dual blood pressure, missing days not 0, n sample counts)
- Medication occurrences, self-reporting, overdue alerts
- Mood (self-report) vs Camera Emotion separation
- Section 6A Rehabilitation: idempotency, snapshot target, camera verification, failure states
- Prescription revisions with version immutability
- Clinician advice / recommendations with simulation flags
- Emergency hotline on-duty logic and unverified number safety
- Audit logging for all sensitive accesses
"""

from __future__ import annotations

import json
import logging
import math
import re
import shutil
import uuid
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

from shared.database.connection import UnifiedDatabaseManager
from shared.database.outbox_helper import insert_outbox_event
from features.exercise_rehab.rehab_session_adapter import ensure_default_rehab_plans
from features.web_portal.auth import AuthService
from shared.services.credential_security import hash_password
from features.voice_assistant.voice_ai.config import VOICE_AI_ENABLED
from features.voice_assistant.voice_ai.domain.contracts import ExecutionStatus
from features.voice_assistant.voice_ai.realtime.assistance_coordinator import AssistanceCoordinator

logger = logging.getLogger("ApiRouter")
UTC = timezone.utc



def _vn_now() -> datetime:
    """Current wall-clock time in Vietnam (UTC+7), naive. The server may run in UTC (Render)."""
    return datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=7)

class ApiRouter:
    def __init__(
        self,
        db: UnifiedDatabaseManager,
        auth: AuthService,
        outbox_relay: Optional[Any] = None,
        orchestrator: Optional[Any] = None,
        assistance_coordinator: Optional[Any] = None,
        asr_provider: Optional[Any] = None,
    ) -> None:
        self.db = db
        self.auth = auth
        self.outbox_relay = outbox_relay
        self.orchestrator = orchestrator  # Legacy semantic pipeline is not activated by local STT.
        self.assistance_coordinator = assistance_coordinator or AssistanceCoordinator(db=self.db, auth_service=self.auth)
        self.asr_provider = asr_provider
        from features.voice_assistant.transcript_intent.backend import TranscriptIntentEndpoint
        self.transcript_intent_endpoint = TranscriptIntentEndpoint(db, auth)

    def _trigger_relay(self) -> None:
        if self.outbox_relay is not None:
            try:
                self.outbox_relay.trigger_now()
            except Exception:
                pass

    def handle_request(
        self,
        method: str,
        path: str,
        query_params: Dict[str, str],
        body: Dict[str, Any],
        token: Optional[str] = None,
        ip_address: str = "127.0.0.1",
        headers: Optional[Dict[str, str]] = None,
    ) -> Tuple[int, Dict[str, Any]]:
        """Main dispatcher for API requests."""
        # Public routes
        if method == "POST" and path == "/api/auth/login":
            return self._login(body)
        elif method == "GET" and path == "/api/auth/quick-accounts":
            return self._get_quick_accounts()
        elif method == "POST" and path in ("/api/auth/register", "/api/auth/register-caregiver"):
            return 404, {"error": "Không tìm thấy chức năng"}
        elif method == "GET" and path == "/api/public/patients":
            return 404, {"error": "Không tìm thấy chức năng"}
        elif path.startswith("/api/device/"):
            # Home devices (camera box) authenticate with X-Device-Key, not a user session.
            from shared.ai_runtime.device_api import handle_device_route
            return handle_device_route(self.db, method, path, query_params, body, headers)

        # Authenticated routes
        session = self.auth.get_session(token) if token else None
        if not session:
            return 401, {"error": "Chưa đăng nhập hoặc phiên làm việc đã hết hạn"}

        user_id = session["user_id"]
        role = session["role"]

        if method == "GET" and path == "/api/auth/me":
            return 200, {"user": session}

        if method == "POST" and path == "/api/auth/logout":
            self.auth.revoke_session(token)
            return 200, {"message": "Đã đăng xuất"}

        if method == "GET" and path in ("/api/me/patients", "/api/patients"):
            patients = self.auth.list_accessible_patients(user_id)
            return 200, {"patients": patients}

        if method == "GET" and path == "/api/medications":
            return self._get_medications_catalog()

        # Admin routes: /api/admin/...
        if path.startswith("/api/admin/"):
            if role != "admin":
                return 403, {"error": "Chỉ Quản trị viên hệ thống (Admin) mới có quyền truy cập chức năng quản trị."}
            return self._handle_admin_route(method, path, query_params, body, user_id, ip_address)

        if method == 'POST' and path == '/api/transcript-intent':
            return self.transcript_intent_endpoint.handle(body, user_id, token)

        # Voice AI routes: /api/v1/voice/... or /api/voice/...
        if path.startswith("/api/v1/voice/") or path.startswith("/api/voice/"):
            return self._handle_voice_route(method, path, query_params, body, user_id, role, ip_address, headers)

        # Patient-specific routes: /api/patients/{patient_id}/...
        parts = path.strip("/").split("/")
        if len(parts) >= 3 and parts[0] == "api" and parts[1] == "patients":
            patient_id = parts[2]
            sub_resource = parts[3] if len(parts) >= 4 else "overview"

            # Check access permission
            perm = "view"
            if sub_resource == "observations" and method == "GET":
                perm = "view_health"
            elif sub_resource == "prescriptions" and method in ("POST", "DELETE", "PATCH"):
                perm = "edit_prescription"
            elif sub_resource in ("exercise-plans", "rehab-plans") and method in ("POST", "PATCH", "PUT"):
                perm = "edit_schedule"
            elif sub_resource == "measurement-requests" and method in ("POST", "PATCH", "PUT", "DELETE"):
                perm = "edit_schedule"

            has_access, err_msg = self.auth.check_patient_access(user_id, patient_id, perm)
            if not has_access:
                return 403, {"error": err_msg}

            # Audit sensitive record view
            if method == "GET" and sub_resource in ("overview", "observations", "prescriptions"):
                self.auth.log_audit(user_id, "READ_RECORDS", "patient", patient_id, {"resource": sub_resource}, ip_address)

            # AI monitoring (ai_core): posture, fall risk, objects, camera-observed medication intake.
            if sub_resource == 'ai':
                from shared.ai_runtime.web_routes import handle_ai_route
                return handle_ai_route(self.db, self.auth, method, parts[4:], query_params, body, patient_id, user_id)

            # Voice requests reuse patient authorization and existing outbox SSE.
            if sub_resource == 'voice-requests':
                from features.voice_assistant.local_rules.requests import RequestService
                service = RequestService(self.db)
                try:
                    if method == 'GET' and len(parts)==4:
                        self.auth.log_audit(user_id,'READ_RECORDS','patient',patient_id,{'resource':'voice-requests'},ip_address)
                        return 200, {'requests':service.list_for_recipient(patient_id,user_id)}
                    if method=='POST' and len(parts)==6 and parts[5] in ('acknowledged','resolved'):
                        service.transition(parts[4],patient_id,user_id,parts[5])
                        return 200, {'status':parts[5]}
                    return 404, {'error':'Endpoint không tồn tại'}
                except PermissionError as exc:
                    return 403, {'error':str(exc)}
                except ValueError as exc:
                    return 409, {'error':str(exc)}

            # Route to handlers
            if sub_resource == 'music':
                from features.music_care.family_music_service import FamilyMusicService
                service = FamilyMusicService(self.db)
                if role not in ('caregiver', 'admin', 'patient'):
                    return 403, {'error': 'Chỉ người nhà và người cao tuổi được truy cập thư viện nhạc này'}
                if method == 'DELETE' and len(parts) == 5:
                    if role not in ('caregiver', 'admin'):
                        return 403, {'error': 'Chỉ người nhà được xoá bài nhạc'}
                    if not service.delete(patient_id, user_id, parts[4]):
                        return 404, {'error': 'Không tìm thấy bài nhạc'}
                    self._trigger_relay()
                    return 200, {'status': 'deleted', 'track_id': parts[4]}
                if method == 'GET' and len(parts) == 4:
                    return 200, {'tracks': [service.public_track(row) for row in service.list_tracks(patient_id)]}
                if method == 'GET' and len(parts) == 6 and parts[5] == 'audio':
                    row = service.get_track(patient_id, parts[4])
                    if not row or not service.audio_path(row).is_file():
                        return 404, {'error': 'Không tìm thấy tệp nhạc'}
                    return 200, {'_audio_path': str(service.audio_path(row)), 'content_type': row['content_type']}
                if method == 'POST' and len(parts) == 4:
                    if role not in ('caregiver', 'admin'):
                        return 403, {'error': 'Chỉ người nhà được tải nhạc lên'}
                    try:
                        row, created = service.upload(patient_id, user_id, body.get('filename', ''),
                                                      body.get('title', ''), body.get('_audio_bytes'))
                    except ValueError as exc:
                        return 400, {'error': str(exc)}
                    self._trigger_relay()
                    return (201 if created else 200), {'track': service.public_track(row), 'created': created}
                return 404, {'error': 'Endpoint không tồn tại'}
            elif sub_resource in ("overview", "today", "dashboard") and method == "GET":
                return self._patient_overview(patient_id, query_params)
            elif sub_resource in ("observations", "measurements", "vitals"):
                if method == "GET":
                    return self._get_observations(patient_id, query_params)
                elif method == "POST":
                    return self._add_observation(patient_id, user_id, body)
            elif sub_resource in ("rehab-progress", "rehab-event", "rehab-events") and method == "POST":
                body_copy = dict(body)
                body_copy["patient_id"] = patient_id
                return self._handle_rehab_event(body_copy)
            elif sub_resource == "medication":
                action_part = parts[4] if len(parts) >= 5 else ""
                if action_part == "doses":
                    if len(parts) == 5 and method == "GET":
                        return self._get_medication_doses(patient_id, query_params)
                    elif len(parts) >= 7 and parts[6] == "action" and method == "POST":
                        occurrence_id = parts[5]
                        return self._record_dose_action(patient_id, occurrence_id, user_id, body)
            elif sub_resource == "alerts":
                if len(parts) == 4 and method == "GET":
                    return self._get_alerts(patient_id)
                elif len(parts) >= 6 and parts[5] == "acknowledge" and method == "POST":
                    alert_id = parts[4]
                    return self._acknowledge_alert(patient_id, alert_id, user_id)
            elif sub_resource == "mood":
                if method == "GET":
                    return self._get_mood(patient_id)
                elif method == "POST":
                    return self._add_mood_checkin(patient_id, user_id, body)
            elif sub_resource == "rehab":
                if len(parts) >= 5 and parts[4] == "sessions" and method == "GET":
                    return self._get_rehab_sessions(patient_id)
                elif len(parts) >= 6 and parts[5] == "mark_unavailable" and method == "POST":
                    session_id = parts[4]
                    return self._mark_rehab_unavailable(session_id)
            elif sub_resource in ("exercise-plans", "rehab-plans"):
                if len(parts) == 4 and method == "GET":
                    return self._get_exercise_plans(patient_id)
                elif len(parts) >= 5 and parts[4] in ("reset", "init", "init-defaults") and method == "POST":
                    return self._reset_exercise_plans(patient_id, user_id, body)
                elif len(parts) >= 5 and method in ("POST", "PATCH", "PUT"):
                    plan_id = parts[4]
                    return self._update_exercise_plan(patient_id, plan_id, user_id, body)
                elif len(parts) == 4 and method in ("POST", "PATCH", "PUT"):
                    if body.get("action") in ("reset", "init", "init-defaults"):
                        return self._reset_exercise_plans(patient_id, user_id, body)
                    return self._update_exercise_plan(patient_id, body.get("plan_id", ""), user_id, body)
            elif sub_resource == "measurement-requests":
                if len(parts) == 4 and method == "GET":
                    return self._get_measurement_requests(patient_id, query_params)
                elif len(parts) == 4 and method == "POST":
                    return self._create_measurement_request(patient_id, user_id, body)
                elif len(parts) >= 6 and parts[5] in ("cancel", "delete") and method == "POST":
                    request_id = parts[4]
                    return self._cancel_measurement_request(patient_id, request_id, user_id, body)
            elif sub_resource == "caregiver-reports":
                if len(parts) == 4 and method == "GET":
                    return self._get_caregiver_reports(patient_id)
                elif len(parts) == 4 and method == "POST":
                    return self._add_caregiver_report(patient_id, user_id, body)
                elif len(parts) >= 6 and (parts[5] in ("respond", "reply", "messages")) and method == "POST":
                    report_id = parts[4]
                    return self._respond_caregiver_report(patient_id, report_id, user_id, body)
            elif sub_resource == "prescriptions":
                if len(parts) == 4 and method == "GET":
                    return self._get_prescriptions(patient_id)
                elif len(parts) == 4 and method == "POST":
                    return self._create_prescription(patient_id, user_id, body)
                elif len(parts) >= 6 and parts[5] == "revisions" and method == "POST":
                    prescription_id = parts[4]
                    return self._revise_prescription(patient_id, prescription_id, user_id, body)
                elif (len(parts) >= 6 and parts[5] in ("cancel", "delete") and method == "POST") or (len(parts) == 5 and method in ("DELETE", "POST")):
                    prescription_id = parts[4]
                    return self._cancel_prescription(patient_id, prescription_id, user_id, body)
            elif sub_resource == "snapshot" and method == "GET":
                return self._get_patient_snapshot(patient_id, query_params)
            elif sub_resource == "events" and method == "POST":
                return self._sync_events(patient_id, user_id, body)
            elif sub_resource == "changes" and method == "GET":
                return self._get_changes(patient_id, query_params)
            elif sub_resource == "recommendations":
                if len(parts) >= 6 and parts[5] == "read" and method == "POST":
                    rec_id = parts[4]
                    return self._mark_recommendation_read(patient_id, rec_id, user_id)
                elif method == "GET":
                    return self._get_recommendations(patient_id)
                elif method == "POST":
                    return self._add_recommendation(patient_id, user_id, body)
            elif sub_resource == "emergency-contacts":
                if method == "GET":
                    return self._get_emergency_contacts(patient_id)
            elif sub_resource == "emergency-contact-events":
                if method == "POST":
                    return self._log_emergency_event(patient_id, user_id, body)

        # Top-level sync route: /api/sync/events
        if method == "POST" and path == "/api/sync/events":
            patient_id = body.get("patient_id")
            if not patient_id:
                return 400, {"error": "Thiếu patient_id"}
            has_access, err_msg = self.auth.check_patient_access(user_id, patient_id)
            if not has_access:
                return 403, {"error": err_msg}
            return self._sync_events(patient_id, user_id, body)

        # Section 6A Vision Event submission route
        if method == "POST" and path == "/api/rehab/event":
            return self._handle_rehab_event(body)

        return 404, {"error": f"Endpoint không tồn tại: {method} {path}"}

    # =========================================================================
    # AUTHENTICATION
    # =========================================================================
    def _login(self, body: Dict[str, Any]) -> Tuple[int, Dict[str, Any]]:
        identifier = body.get("email") or body.get("user_id") or body.get("username") or ""
        if not isinstance(identifier, str):
            return 401, {"error": "Thông tin đăng nhập không hợp lệ"}
        ident = identifier.strip()
        password = body.get("password", "")
        expected_role = body.get("expected_role")  # "caregiver" or "clinician" or "admin"

        ok, msg, user = self.auth.authenticate_credentials(ident, password)
        if not ok or not user:
            return 401, {"error": msg}

        # Role enforcement to avoid role confusion
        if expected_role and user["role"] != "admin":
            if expected_role == "caregiver" and user["role"] != "caregiver":
                return 403, {
                    "error": f"Tài khoản '{ident}' là tài khoản Bác sĩ điều trị. Vui lòng chuyển sang tab 'Bác sĩ' để đăng nhập đúng cổng chuyên môn."
                }
            elif expected_role == "clinician" and user["role"] != "clinician":
                return 403, {
                    "error": f"Tài khoản '{ident}' là tài khoản Người nhà / Người chăm sóc. Vui lòng chuyển sang tab 'Người nhà' để đăng nhập."
                }
            elif expected_role == "admin" and user["role"] != "admin":
                return 403, {
                    "error": f"Tài khoản '{ident}' không có quyền Quản trị viên hệ thống."
                }

        token = self.auth.create_session(user)
        return 200, {
            "token": token,
            "user": {
                "user_id": user["user_id"],
                "email": user["email"],
                "full_name": user["full_name"],
                "role": user["role"],
            },
        }

    def _get_quick_accounts(self) -> Tuple[int, Dict[str, Any]]:
        return 404, {"error": "Không tìm thấy chức năng"}

    def _register_caregiver(self, body: Dict[str, Any]) -> Tuple[int, Dict[str, Any]]:
        full_name = body.get("full_name", "")
        email = body.get("email", "")
        phone = body.get("phone", "")
        password = body.get("password", "")
        patient_id = body.get("patient_id", "")
        relationship_type = body.get("relationship_type", "Người thân")

        ok, msg, user = self.auth.register_caregiver(
            full_name=full_name,
            email=email,
            phone=phone,
            password=password,
            patient_id=patient_id,
            relationship_type=relationship_type,
        )
        if not ok:
            return 400, {"error": msg}

        self._trigger_relay()

        # Sync to local JSON profiles if available
        try:
            from shared.services.patient_sync_service import sync_all_patient_profiles
            sync_all_patient_profiles(self.db)
        except Exception as e:
            logger.warning("Could not sync patient profile after caregiver register: %s", e)

        token = self.auth.create_session(user)
        return 201, {
            "status": "registered",
            "token": token,
            "user": user,
            "message": "Đăng ký tài khoản Người nhà thành công! Hệ thống đang chuyển đến Cổng Người nhà...",
        }

    def _get_public_patients(self) -> Tuple[int, Dict[str, Any]]:
        with self.db.connect() as conn:
            rows = conn.fetchall(
                "SELECT patient_id, full_name, date_of_birth, gender, notes FROM patients WHERE status = 'ACTIVE' ORDER BY full_name"
            )
            return 200, {"patients": rows}

    # =========================================================================
    # PATIENT OVERVIEW
    # =========================================================================
    def _patient_overview(self, patient_id: str, query_params: Optional[Dict[str, str]] = None) -> Tuple[int, Dict[str, Any]]:
        with self.db.connect() as conn:
            patient = conn.fetchone("SELECT * FROM patients WHERE patient_id = ?", (patient_id,))
            if not patient:
                return 404, {"error": "Không tìm thấy người bệnh"}

            # Latest blood pressure
            bp_row = conn.fetchone(
                """
                SELECT mg.measured_at, mg.source_type, mg.device_label,
                       MAX(CASE WHEN ot.code IN ('blood_pressure_systolic', 'bp_systolic') THEN ho.value_numeric END) as systolic,
                       MAX(CASE WHEN ot.code IN ('blood_pressure_diastolic', 'bp_diastolic') THEN ho.value_numeric END) as diastolic
                FROM measurement_groups mg
                JOIN health_observations ho ON mg.group_id = ho.group_id
                JOIN observation_types ot ON ho.observation_type_id = ot.observation_type_id
                WHERE mg.patient_id = ? AND mg.status = 'active' AND ot.category = 'vitals'
                GROUP BY mg.group_id
                HAVING systolic IS NOT NULL OR diastolic IS NOT NULL
                ORDER BY mg.measured_at DESC LIMIT 1
                """,
                (patient_id,),
            )

            # Latest heart rate
            hr_row = conn.fetchone(
                """
                SELECT ho.value_numeric, ho.unit_code, mg.measured_at, mg.device_label
                FROM health_observations ho
                JOIN measurement_groups mg ON ho.group_id = mg.group_id
                JOIN observation_types ot ON ho.observation_type_id = ot.observation_type_id
                WHERE ho.patient_id = ? AND ot.code = 'heart_rate' AND ho.status = 'active'
                ORDER BY mg.measured_at DESC LIMIT 1
                """,
                (patient_id,),
            )

            # Latest blood glucose
            glu_row = conn.fetchone(
                """
                SELECT ho.value_numeric, ho.unit_code, mg.measured_at, mg.device_label
                FROM health_observations ho
                JOIN measurement_groups mg ON ho.group_id = mg.group_id
                JOIN observation_types ot ON ho.observation_type_id = ot.observation_type_id
                WHERE ho.patient_id = ? AND ot.code = 'blood_glucose' AND ho.status = 'active'
                ORDER BY mg.measured_at DESC LIMIT 1
                """,
                (patient_id,),
            )

            # Latest SpO2
            spo2_row = conn.fetchone(
                """
                SELECT ho.value_numeric, ho.unit_code, mg.measured_at, mg.device_label
                FROM health_observations ho
                JOIN measurement_groups mg ON ho.group_id = mg.group_id
                JOIN observation_types ot ON ho.observation_type_id = ot.observation_type_id
                WHERE ho.patient_id = ? AND ot.code = 'spo2' AND ho.status = 'active'
                ORDER BY mg.measured_at DESC LIMIT 1
                """,
                (patient_id,),
            )

            # Latest Temperature
            temp_row = conn.fetchone(
                """
                SELECT ho.value_numeric, ho.unit_code, mg.measured_at, mg.device_label
                FROM health_observations ho
                JOIN measurement_groups mg ON ho.group_id = mg.group_id
                JOIN observation_types ot ON ho.observation_type_id = ot.observation_type_id
                WHERE ho.patient_id = ? AND ot.code = 'temperature' AND ho.status = 'active'
                ORDER BY mg.measured_at DESC LIMIT 1
                """,
                (patient_id,),
            )

            # Medication summary today (strictly for active prescriptions)
            today = date.today().isoformat()
            med_rows = conn.fetchall(
                """
                SELECT d.state, COUNT(*) as cnt
                FROM dose_occurrences d
                JOIN medication_schedules ms ON d.schedule_id = ms.schedule_id
                JOIN prescription_items pi ON ms.prescription_item_id = pi.prescription_item_id
                JOIN prescriptions p ON pi.prescription_id = p.prescription_id
                WHERE d.patient_id = ? AND d.local_scheduled_time LIKE ?
                  AND (p.status = 'ACTIVE' OR (p.status = 'SUPERSEDED' AND d.state = 'CONFIRMED'))
                  AND d.state != 'CANCELLED'
                GROUP BY d.state
                """,
                (patient_id, f"{today}%"),
            )
            med_summary = {r["state"]: r["cnt"] for r in med_rows}

            # Latest rehab session
            rehab_session = conn.fetchone(
                """
                SELECT rs.*, COALESCE(rp.exercise_name, rs.notes, 'Bài tập phục hồi') as exercise_name,
                       COALESCE(rp.target_count, rs.target_count_snapshot, 10) as plan_target
                FROM rehab_sessions rs
                LEFT JOIN rehab_plans rp ON rs.plan_id = rp.plan_id
                WHERE rs.patient_id = ?
                ORDER BY rs.local_date DESC, rs.verified_count DESC, rs.started_at DESC
                LIMIT 1
                """,
                (patient_id,),
            )

            # Unresolved alerts
            alerts_cnt = conn.fetchone(
                "SELECT COUNT(*) as cnt FROM alerts WHERE patient_id = ? AND status != 'RESOLVED'",
                (patient_id,),
            )

            # Unread reports
            reports_cnt = conn.fetchone(
                "SELECT COUNT(*) as cnt FROM caregiver_reports WHERE patient_id = ? AND status = 'submitted'",
                (patient_id,),
            )

            # Doctor info
            doctor = conn.fetchone(
                """
                SELECT cl.*, u.full_name as doctor_name, u.phone as doctor_phone, u.email as doctor_email
                FROM care_team ct
                JOIN clinicians cl ON ct.clinician_id = cl.clinician_id
                JOIN users u ON cl.user_id = u.user_id
                WHERE ct.patient_id = ? AND ct.is_active = 1
                ORDER BY CASE WHEN ct.role_description LIKE '%phụ trách%' THEN 0 ELSE 1 END, ct.care_team_id
                LIMIT 1
                """,
                (patient_id,),
            )

            # Caregiver info
            caregiver = conn.fetchone(
                """
                SELECT cg.*, u.full_name as caregiver_name, u.phone as caregiver_phone, u.email as caregiver_email,
                       pc.relationship_type
                FROM patient_caregivers pc
                JOIN caregivers cg ON pc.caregiver_id = cg.caregiver_id
                JOIN users u ON cg.user_id = u.user_id
                WHERE pc.patient_id = ? AND pc.is_active = 1
                ORDER BY pc.is_primary DESC, pc.notification_priority ASC, pc.patient_caregiver_id ASC
                LIMIT 1
                """,
                (patient_id,),
            )

            now_iso = datetime.now(timezone.utc).isoformat()
            resp_data = {
                "patient": patient,
                "patient_id": patient_id,
                "full_name": patient["full_name"],
                "gender": patient["gender"],
                "timezone": patient["timezone"],
                "server_time_utc": now_iso,
                "last_updated_at": now_iso,
                "latest_blood_pressure": bp_row,
                "latest_heart_rate": hr_row,
                "latest_blood_glucose": glu_row,
                "latest_spo2": spo2_row,
                "latest_temperature": temp_row,
                "medication_today": med_summary,
                "latest_rehab_session": rehab_session,
                "unresolved_alerts_count": alerts_cnt["cnt"] if alerts_cnt else 0,
                "unread_reports_count": reports_cnt["cnt"] if reports_cnt else 0,
                "assigned_doctor": doctor,
                "assigned_caregiver": caregiver,
            }
            try:
                _, snapshot_resp = self._get_patient_snapshot(patient_id, query_params or {})
                if isinstance(snapshot_resp, dict):
                    resp_data["sections"] = snapshot_resp.get("sections", {})
                    for k, v in snapshot_resp.items():
                        if k not in resp_data:
                            resp_data[k] = v
            except Exception:
                pass
            return 200, resp_data

    # =========================================================================
    # HEALTH OBSERVATIONS & TRENDS
    # =========================================================================
    def _get_observations(self, patient_id: str, query_params: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
        days = int(query_params.get("days", 30))
        res = query_params.get("resolution", "raw")
        type_code = query_params.get("type", "blood_pressure")

        since_date = (date.today() - timedelta(days=days)).isoformat()

        with self.db.connect() as conn:
            if type_code == "all":
                limit = int(query_params.get("limit", 30))
                rows = conn.fetchall(
                    """
                    SELECT mg.group_id, mg.measured_at, mg.recorded_at, mg.source_type, mg.device_label,
                           mg.context as notes,
                           u.full_name as entered_by_name,
                           MAX(CASE WHEN ot.code IN ('blood_pressure_systolic', 'bp_systolic') THEN ho.value_numeric END) as systolic,
                           MAX(CASE WHEN ot.code IN ('blood_pressure_diastolic', 'bp_diastolic') THEN ho.value_numeric END) as diastolic,
                           MAX(CASE WHEN ot.code = 'heart_rate' THEN ho.value_numeric END) as heart_rate,
                           MAX(CASE WHEN ot.code = 'blood_glucose' THEN ho.value_numeric END) as blood_glucose,
                           MAX(CASE WHEN ot.code = 'spo2' THEN ho.value_numeric END) as spo2,
                           MAX(CASE WHEN ot.code = 'temperature' THEN ho.value_numeric END) as temperature
                    FROM measurement_groups mg
                    LEFT JOIN health_observations ho ON mg.group_id = ho.group_id
                    LEFT JOIN observation_types ot ON ho.observation_type_id = ot.observation_type_id
                    LEFT JOIN users u ON mg.entered_by_user_id = u.user_id
                    WHERE mg.patient_id = ? AND mg.status = 'active'
                    GROUP BY mg.group_id
                    ORDER BY mg.measured_at DESC
                    LIMIT ?
                    """,
                    (patient_id, limit),
                )
                return 200, {
                    "type": "all",
                    "measurements": [dict(r) for r in rows],
                }

            elif type_code == "blood_pressure":
                rows = conn.fetchall(
                    """
                    SELECT mg.group_id, mg.measured_at, mg.recorded_at, mg.source_type, mg.device_label,
                           u.full_name as entered_by_name,
                           MAX(CASE WHEN ot.code IN ('blood_pressure_systolic', 'bp_systolic') THEN ho.value_numeric END) as systolic,
                           MAX(CASE WHEN ot.code IN ('blood_pressure_diastolic', 'bp_diastolic') THEN ho.value_numeric END) as diastolic,
                           'mmHg' as unit_code
                    FROM measurement_groups mg
                    JOIN health_observations ho ON mg.group_id = ho.group_id
                    JOIN observation_types ot ON ho.observation_type_id = ot.observation_type_id
                    LEFT JOIN users u ON mg.entered_by_user_id = u.user_id
                    WHERE mg.patient_id = ? AND mg.status = 'active'
                      AND mg.measured_at >= ?
                    GROUP BY mg.group_id
                    HAVING systolic IS NOT NULL OR diastolic IS NOT NULL
                    ORDER BY mg.measured_at ASC
                    """,
                    (patient_id, since_date),
                )

                readings = []
                for r in rows:
                    readings.append({
                        "group_id": r["group_id"],
                        "measured_at": r["measured_at"],
                        "recorded_at": r["recorded_at"],
                        "systolic": r["systolic"],
                        "diastolic": r["diastolic"],
                        "unit_code": "mmHg",
                        "source_type": r["source_type"],
                        "device_label": r["device_label"],
                        "entered_by_name": r["entered_by_name"] or "Người dùng nhập",
                    })

                # Check day/week aggregation
                aggregated = []
                if res in ("day", "week") and readings:
                    # Group by date
                    by_day: Dict[str, List[Any]] = {}
                    for r in readings:
                        d_str = r["measured_at"][:10]
                        by_day.setdefault(d_str, []).append(r)

                    for d_str, grp in sorted(by_day.items()):
                        sys_vals = [x["systolic"] for x in grp if x["systolic"] is not None]
                        dia_vals = [x["diastolic"] for x in grp if x["diastolic"] is not None]
                        aggregated.append({
                            "date": d_str,
                            "n": len(grp),
                            "mean_systolic": round(sum(sys_vals) / len(sys_vals), 1) if sys_vals else None,
                            "mean_diastolic": round(sum(dia_vals) / len(dia_vals), 1) if dia_vals else None,
                            "unit_code": "mmHg",
                        })

                return 200, {
                    "type": "blood_pressure",
                    "days": days,
                    "resolution": res,
                    "readings": readings,
                    "aggregated": aggregated,
                }
            else:
                # Other vitals (heart_rate, spo2, glucose, temperature)
                rows = conn.fetchall(
                    """
                    SELECT ho.observation_id, ho.value_numeric, ho.unit_code, ho.status,
                           mg.measured_at, mg.recorded_at, mg.source_type, mg.device_label,
                           ot.code, ot.name as type_name, u.full_name as entered_by_name
                    FROM health_observations ho
                    JOIN measurement_groups mg ON ho.group_id = mg.group_id
                    JOIN observation_types ot ON ho.observation_type_id = ot.observation_type_id
                    LEFT JOIN users u ON mg.entered_by_user_id = u.user_id
                    WHERE ho.patient_id = ? AND ot.code = ? AND ho.status = 'active'
                      AND mg.measured_at >= ?
                    ORDER BY mg.measured_at ASC
                    """,
                    (patient_id, type_code, since_date),
                )
                enhanced_readings = []
                for r in rows:
                    val = r["value_numeric"]
                    unit = r["unit_code"]
                    std_val = val
                    std_unit = unit
                    if type_code == "blood_glucose" and val is not None:
                        if unit == "mmol/L":
                            std_val = round(val * 18.0182, 1)
                            std_unit = "mg/dL"
                        elif unit == "mg/dL":
                            std_val = val
                            std_unit = "mg/dL"
                    d_item = dict(r)
                    d_item["standard_value"] = std_val
                    d_item["standard_unit"] = std_unit
                    enhanced_readings.append(d_item)

                aggregated = []
                if res in ("day", "week") and enhanced_readings:
                    by_day: Dict[str, List[Any]] = {}
                    for r in enhanced_readings:
                        d_str = r["measured_at"][:10]
                        by_day.setdefault(d_str, []).append(r)
                    for d_str, grp in sorted(by_day.items()):
                        vals = [x["standard_value"] for x in grp if x["standard_value"] is not None]
                        std_u = grp[0]["standard_unit"] if grp else ""
                        aggregated.append({
                            "date": d_str,
                            "n": len(grp),
                            "mean_value": round(sum(vals) / len(vals), 1) if vals else None,
                            "min_value": min(vals) if vals else None,
                            "max_value": max(vals) if vals else None,
                            "unit_code": std_u,
                        })

                return 200, {
                    "type": type_code,
                    "days": days,
                    "resolution": res,
                    "readings": enhanced_readings,
                    "aggregated": aggregated,
                }

    def _add_observation(self, patient_id: str, user_id: str, body: Dict[str, Any]) -> Tuple[int, Dict[str, Any]]:
        req_id = body.get("client_request_id") or uuid.uuid4().hex
        measured_at = body.get("measured_at") or datetime.now(timezone.utc).isoformat()
        now_iso = datetime.now(timezone.utc).isoformat()
        source_type = body.get("source_type", "manual_caregiver")
        device_label = body.get("device_label", "Người dùng nhập")
        notes = (body.get("notes") or body.get("context") or "").strip()
        items = body.get("items") or body.get("readings") or []

        if not isinstance(items, list) or not items:
            return 400, {"error": "Cần ít nhất một chỉ số đo"}

        code_aliases = {
            "bp_systolic": "blood_pressure_systolic",
            "systolic": "blood_pressure_systolic",
            "bp_diastolic": "blood_pressure_diastolic",
            "diastolic": "blood_pressure_diastolic",
            "pulse": "heart_rate",
            "hr": "heart_rate",
            "glucose": "blood_glucose",
            "temp": "temperature",
        }

        with self.db.connect() as conn:
            # Check idempotency
            existing = conn.fetchone("SELECT group_id FROM measurement_groups WHERE client_request_id = ?", (req_id,))
            if existing:
                return 200, {"group_id": existing["group_id"], "status": "duplicate_acknowledged"}

            # Validate the entire batch before creating a group or completing requests.
            # Unknown types used to be skipped, leaving empty groups with a false success.
            validated_items = []
            seen_types = set()
            for it in items:
                if not isinstance(it, dict):
                    return 400, {"error": "Chỉ số đo không hợp lệ"}
                raw_code = it.get("code")
                ot_id = it.get("observation_type_id")
                code = code_aliases.get(raw_code, raw_code)
                if ot_id:
                    ot = conn.fetchone(
                        "SELECT observation_type_id, standard_unit FROM observation_types WHERE observation_type_id = ? OR code = ?",
                        (ot_id, ot_id),
                    )
                else:
                    ot = conn.fetchone(
                        "SELECT observation_type_id, standard_unit FROM observation_types WHERE code = ?", (code,)
                    )
                if not ot:
                    return 400, {"error": "Loại chỉ số đo chưa được cấu hình hoặc không hợp lệ"}
                raw_value = it.get("value") if it.get("value") is not None else it.get("value_numeric")
                try:
                    val = float(raw_value)
                except (TypeError, ValueError, OverflowError):
                    return 400, {"error": "Giá trị chỉ số đo phải là số hữu hạn"}
                if isinstance(raw_value, bool) or not math.isfinite(val):
                    return 400, {"error": "Giá trị chỉ số đo phải là số hữu hạn"}
                if ot["observation_type_id"] in seen_types:
                    return 400, {"error": "Một lần đo không được lặp lại cùng loại chỉ số"}
                seen_types.add(ot["observation_type_id"])
                validated_items.append((ot["observation_type_id"], val, it.get("unit") or ot["standard_unit"]))

            group_id = f"grp_{uuid.uuid4().hex[:16]}"
            conn.execute(
                """
                INSERT INTO measurement_groups (group_id, patient_id, measured_at, recorded_at, entered_by_user_id, source_type, device_label, context, status, client_request_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (group_id, patient_id, measured_at, now_iso, user_id, source_type, device_label, notes, "active", req_id),
            )

            for observation_type_id, val, unit in validated_items:
                obs_id = f"obs_{uuid.uuid4().hex[:16]}"
                conn.execute(
                    """
                    INSERT INTO health_observations (observation_id, group_id, patient_id, observation_type_id, value_numeric, unit_code, status, notes)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (obs_id, group_id, patient_id, observation_type_id, val, unit, "active", notes),
                )

                # Auto-complete matching pending measurement requests
                pending_req = conn.fetchone(
                    """
                    SELECT request_id FROM measurement_requests
                    WHERE patient_id = ? AND observation_type_id = ? AND status = 'PENDING'
                    ORDER BY due_at_utc ASC LIMIT 1
                    """,
                    (patient_id, observation_type_id),
                )
                if pending_req:
                    conn.execute(
                        """
                        UPDATE measurement_requests
                        SET status = 'COMPLETED', completed_observation_id = ?
                        WHERE request_id = ?
                        """,
                        (obs_id, pending_req["request_id"]),
                    )
                    conn.execute(
                        """
                        INSERT INTO measurement_request_logs (log_id, request_id, action, actor_id, occurred_at_utc, details)
                        VALUES (?, ?, 'COMPLETED', ?, ?, ?)
                        """,
                        (f"mrl_{uuid.uuid4().hex[:12]}", pending_req["request_id"], user_id, now_iso, json.dumps({"obs_id": obs_id, "group_id": group_id})),
                    )
                    insert_outbox_event(
                        conn=conn,
                        patient_id=patient_id,
                        event_type="health.measurement_request_completed",
                        aggregate_type="measurement_request",
                        aggregate_id=pending_req["request_id"],
                        aggregate_version=1,
                        occurred_at=now_iso,
                        actor_id=user_id,
                        source="web_portal",
                        payload_minimal={"request_id": pending_req["request_id"], "status": "COMPLETED"},
                    )

            # Insert outbox event in the SAME transaction
            insert_outbox_event(
                conn=conn,
                patient_id=patient_id,
                event_type="health.observation_created",
                aggregate_type="measurement_group",
                aggregate_id=group_id,
                aggregate_version=1,
                occurred_at=now_iso,
                actor_id=user_id,
                source="web_portal" if source_type == "manual_entry" else source_type,
                payload_minimal={
                    "group_id": group_id,
                    "source_type": source_type,
                    "measured_at": measured_at,
                    "item_count": len(items),
                },
            )
            conn.commit()
            self._trigger_relay()

        return 201, {"group_id": group_id, "status": "created"}

    # =========================================================================
    # MEDICATION ADHERENCE
    # =========================================================================
    def _get_medication_doses(self, patient_id: str, query_params: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
        with self.db.connect() as conn:
            pat = conn.fetchone("SELECT timezone FROM patients WHERE patient_id = ?", (patient_id,))
            pat_tz_str = pat["timezone"] if pat and pat.get("timezone") else "Asia/Ho_Chi_Minh"
            date_str = query_params.get("date")
            if not date_str:
                try:
                    import zoneinfo
                    tz = zoneinfo.ZoneInfo(pat_tz_str)
                    date_str = datetime.now(tz).date().isoformat()
                except Exception:
                    date_str = date.today().isoformat()

            doses = conn.fetchall(
                """
                SELECT d.*, m.dosage_form, m.product_strength, m.strength_unit, p.prescription_code, p.version as prescription_version
                FROM dose_occurrences d
                JOIN medication_schedules ms ON d.schedule_id = ms.schedule_id
                JOIN prescription_items pi ON ms.prescription_item_id = pi.prescription_item_id
                JOIN medications m ON pi.medication_id = m.medication_id
                JOIN prescriptions p ON pi.prescription_id = p.prescription_id
                WHERE d.patient_id = ? AND d.local_scheduled_time LIKE ?
                  AND (p.status = 'ACTIVE' OR (p.status = 'SUPERSEDED' AND d.state = 'CONFIRMED'))
                  AND d.state != 'CANCELLED'
                ORDER BY d.local_scheduled_time ASC
                """,
                (patient_id, f"{date_str}%"),
            )
            return 200, {
                "date": date_str,
                "doses": doses,
                "note": "Xác nhận 'Đã uống' là thông tin tự khai báo, không chứng minh việc nuốt thuốc thực tế.",
            }

    def _record_dose_action(
        self, patient_id: str, occurrence_id: str, user_id: str, body: Dict[str, Any]
    ) -> Tuple[int, Dict[str, Any]]:
        action = body.get("action")  # 'self_reported_taken', 'snooze', 'help_requested'
        idempotency_key = body.get("idempotency_key") or f"{occurrence_id}_{action}_{uuid.uuid4().hex[:8]}"
        now_iso = datetime.now(timezone.utc).isoformat()

        with self.db.connect() as conn:
            occ = conn.fetchone("SELECT * FROM dose_occurrences WHERE occurrence_id = ? AND patient_id = ?", (occurrence_id, patient_id))
            if not occ:
                return 404, {"error": "Không tìm thấy lần uống thuốc"}

            # Optimistic concurrency check (version / revision check)
            if body.get("expected_version") is not None:
                try:
                    exp_v = int(body["expected_version"])
                    if occ["revision"] != exp_v:
                        return 409, {
                            "error": "Xung đột phiên bản dữ liệu (bản ghi đã thay đổi từ giao diện khác)",
                            "current_version": occ["revision"],
                            "expected_version": exp_v,
                        }
                except (ValueError, TypeError):
                    pass

            # Idempotency check on events
            if idempotency_key:
                existing_event = conn.fetchone(
                    "SELECT event_id FROM dose_events WHERE idempotency_key = ? OR event_id = ?",
                    (idempotency_key, idempotency_key),
                )
                if existing_event:
                    return 200, {"event_id": existing_event["event_id"], "status": "duplicate_idempotent", "state": occ["state"]}

            # If already confirmed and trying to take again, treat idempotently
            if action == "self_reported_taken" and occ["state"] == "CONFIRMED":
                return 200, {
                    "occurrence_id": occurrence_id,
                    "state": "CONFIRMED",
                    "status": "already_confirmed",
                    "message": "Lần uống thuốc này đã được xác nhận trước đó.",
                }

            new_state = occ["state"]
            is_late = False
            action_notes = f"Action: {action}"
            event_type = action
            if action == "self_reported_taken":
                new_state = "CONFIRMED"
                if occ["grace_deadline_utc"] and now_iso > occ["grace_deadline_utc"]:
                    is_late = True
                    action_notes = f"Action: self_reported_taken (confirmed_late at {now_iso})"
                    event_type = "self_reported_taken"
            elif action == "snooze":
                new_state = "SNOOZED"
                action_notes = f"Action: snooze (nhắc lại)"
            elif action == "help_requested":
                new_state = "REMINDING"
                action_notes = f"Action: help_requested (cụ bấm cần hỗ trợ)"

            event_id = f"evt_{uuid.uuid4().hex[:16]}"
            conn.execute(
                """
                INSERT INTO dose_events (event_id, occurrence_id, event_type, actor_id, actor_role, timestamp_utc, idempotency_key, metadata)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (event_id, occurrence_id, event_type, user_id, "patient", now_iso, idempotency_key, json.dumps(body)),
            )

            conn.execute(
                """
                UPDATE dose_occurrences
                SET state = ?, action_taken_at_utc = ?, action_actor = ?, action_notes = ?
                WHERE occurrence_id = ?
                """,
                (new_state, now_iso, user_id, action_notes, occurrence_id),
            )

            # Insert outbox event for dose_changed in the SAME transaction
            insert_outbox_event(
                conn=conn,
                patient_id=patient_id,
                event_type="medication.dose_changed",
                aggregate_type="dose_occurrence",
                aggregate_id=occurrence_id,
                aggregate_version=1,
                occurred_at=now_iso,
                actor_id=user_id,
                source="elder_app",
                payload_minimal={
                    "occurrence_id": occurrence_id,
                    "state": new_state,
                    "action": action,
                    "is_late": is_late,
                    "is_self_reported": True,
                },
                event_id=event_id,
            )

            # If help requested, generate alert
            if action == "help_requested":
                alert_id = f"alt_{uuid.uuid4().hex[:16]}"
                conn.execute(
                    """
                    INSERT INTO alerts (alert_id, occurrence_id, patient_id, alert_type, status, created_at_utc)
                    VALUES (?, ?, ?, 'HELP_REQUESTED', 'NEW', ?)
                    ON CONFLICT(occurrence_id, alert_type) DO NOTHING
                    """,
                    (alert_id, occurrence_id, patient_id, now_iso),
                )
                insert_outbox_event(
                    conn=conn,
                    patient_id=patient_id,
                    event_type="medication.alert_created",
                    aggregate_type="alert",
                    aggregate_id=alert_id,
                    aggregate_version=1,
                    occurred_at=now_iso,
                    actor_id=user_id,
                    source="elder_app",
                    payload_minimal={
                        "alert_id": alert_id,
                        "occurrence_id": occurrence_id,
                        "alert_type": "HELP_REQUESTED",
                    },
                )

            # If confirmed, resolve pending alerts for this occurrence
            if new_state == "CONFIRMED":
                conn.execute(
                    """
                    UPDATE alerts
                    SET status = 'RESOLVED', resolved_at_utc = ?, resolution_notes = 'Người già đã xác nhận uống thuốc'
                    WHERE occurrence_id = ? AND status = 'NEW'
                    """,
                    (now_iso, occurrence_id),
                )

            conn.commit()
            self._trigger_relay()

        return 200, {"event_id": event_id, "state": new_state}

    # =========================================================================
    # ALERTS
    # =========================================================================
    def _get_alerts(self, patient_id: str) -> Tuple[int, Dict[str, Any]]:
        with self.db.connect() as conn:
            alerts = conn.fetchall(
                """
                SELECT a.*, d.medication_name_snapshot, d.dose_snapshot, d.local_scheduled_time,
                       nd.status as delivery_status, nd.channel as delivery_channel
                FROM alerts a
                JOIN dose_occurrences d ON a.occurrence_id = d.occurrence_id
                LEFT JOIN notification_deliveries nd ON a.alert_id = nd.alert_id
                WHERE a.patient_id = ?
                ORDER BY a.created_at_utc DESC
                """,
                (patient_id,),
            )
            return 200, {"alerts": alerts}

    def _acknowledge_alert(self, patient_id: str, alert_id: str, user_id: str) -> Tuple[int, Dict[str, Any]]:
        now_iso = datetime.now(timezone.utc).isoformat()
        with self.db.connect() as conn:
            conn.execute(
                """
                UPDATE alerts
                SET status = 'ACKNOWLEDGED', resolved_at_utc = ?, resolved_by = ?, resolution_notes = 'Người nhà đã tiếp nhận cảnh báo'
                WHERE alert_id = ? AND patient_id = ?
                """,
                (now_iso, user_id, alert_id, patient_id),
            )
            insert_outbox_event(
                conn=conn,
                patient_id=patient_id,
                event_type="medication.alert_acknowledged",
                aggregate_type="alert",
                aggregate_id=alert_id,
                aggregate_version=1,
                occurred_at=now_iso,
                actor_id=user_id,
                source="web_portal",
                payload_minimal={"alert_id": alert_id, "status": "ACKNOWLEDGED"},
            )
            conn.commit()
            self._trigger_relay()
            self.auth.log_audit(user_id, "ACK_ALERT", "alert", alert_id)
        return 200, {"message": "Đã tiếp nhận cảnh báo thành công"}

    # =========================================================================
    # MOOD & EMOTIONS
    # =========================================================================
    def _get_mood(self, patient_id: str) -> Tuple[int, Dict[str, Any]]:
        from features.emotion.emotion_summary_service import EmotionSummaryService
        summaries = EmotionSummaryService(self.db).list_summaries(patient_id)
        with self.db.connect() as conn:
            checkins = conn.fetchall(
                """
                SELECT mc.*, u.full_name as recorded_by_name
                FROM mood_checkins mc
                LEFT JOIN users u ON mc.recorded_by = u.user_id
                WHERE mc.patient_id = ?
                ORDER BY mc.recorded_at DESC LIMIT 30
                """,
                (patient_id,),
            )
            inferences = conn.fetchall(
                """
                SELECT * FROM emotion_inferences
                WHERE patient_id = ? AND is_consent_active = 1
                ORDER BY observed_at DESC LIMIT 30
                """,
                (patient_id,),
            )
            return 200, {
                "self_reported_moods": checkins,
                "camera_emotion_inferences": inferences,
                "camera_emotion_summaries": summaries,
                "camera_emotion_alerts": EmotionSummaryService(self.db).list_alerts(patient_id),
                "disclaimer": "Biểu cảm khuôn mặt qua camera chỉ là dữ liệu tham khảo, không suy diễn chẩn đoán y tế.",
            }

    def _add_mood_checkin(self, patient_id: str, user_id: str, body: Dict[str, Any]) -> Tuple[int, Dict[str, Any]]:
        rating = int(body.get("self_rating", 3))
        note = body.get("note", "").strip()
        now_iso = datetime.now(timezone.utc).isoformat()
        cid = f"mc_{uuid.uuid4().hex[:16]}"
        with self.db.connect() as conn:
            conn.execute(
                """
                INSERT INTO mood_checkins (checkin_id, patient_id, self_rating, note, recorded_at, recorded_by)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (cid, patient_id, rating, note, now_iso, user_id),
            )
            insert_outbox_event(
                conn=conn,
                patient_id=patient_id,
                event_type="mood.checkin_created",
                aggregate_type="mood_checkin",
                aggregate_id=cid,
                aggregate_version=1,
                occurred_at=now_iso,
                actor_id=user_id,
                source="elder_app",
                payload_minimal={"checkin_id": cid, "self_rating": rating},
            )
            conn.commit()
            self._trigger_relay()
        return 201, {"checkin_id": cid, "status": "recorded"}

    # =========================================================================
    # REHABILITATION (SECTION 6A FLOW)
    # =========================================================================
    def _get_rehab_sessions(self, patient_id: str) -> Tuple[int, Dict[str, Any]]:
        with self.db.connect() as conn:
            plans = conn.fetchall("SELECT * FROM rehab_plans WHERE patient_id = ? AND is_active = 1", (patient_id,))
            sessions = conn.fetchall(
                """
                SELECT rs.*, rp.exercise_name
                FROM rehab_sessions rs
                JOIN rehab_plans rp ON rs.plan_id = rp.plan_id
                WHERE rs.patient_id = ?
                ORDER BY rs.local_date DESC LIMIT 30
                """,
                (patient_id,),
            )
            return 200, {
                "active_plans": plans,
                "sessions": sessions,
            }

    def _handle_rehab_event(self, body: Dict[str, Any]) -> Tuple[int, Dict[str, Any]]:
        """
        Receives MediaPipe camera repetition event (Section 6A).
        Guarantees:
        - Rejection of duplicate idempotency_key
        - Increment of verified_count
        - Atomic transition to 'camera_verified_completed' when verified_count >= target_count_snapshot
        """
        event_id = body.get("event_id") or f"rev_{uuid.uuid4().hex[:16]}"
        session_id = body.get("session_id")
        patient_id = body.get("patient_id")
        exercise_id = body.get("exercise_id")
        rep_number = int(body.get("rep_number", 1))
        score = float(body.get("score", 90.0))
        idempotency_key = body.get("idempotency_key") or event_id
        now_iso = datetime.now(timezone.utc).isoformat()

        if not session_id or not patient_id:
            return 400, {"error": "Thiếu session_id hoặc patient_id"}

        with self.db.connect() as conn:
            # 1. Idempotency Check
            existing = conn.fetchone("SELECT event_id FROM rehab_events WHERE idempotency_key = ?", (idempotency_key,))
            if existing:
                session = conn.fetchone("SELECT * FROM rehab_sessions WHERE session_id = ?", (session_id,))
                return 200, {
                    "event_id": existing["event_id"],
                    "status": "duplicate_event_rejected",
                    "verified_count": session["verified_count"] if session else 0,
                    "session_status": session["status"] if session else "unknown",
                }

            # 2. Check Session
            session = conn.fetchone("SELECT * FROM rehab_sessions WHERE session_id = ?", (session_id,))
            if not session:
                return 404, {"error": "Không tìm thấy phiên tập"}

            exercise_id = exercise_id or session.get("plan_id") or "ex_reach"

            # 3. Insert Event
            conn.execute(
                """
                INSERT INTO rehab_events (event_id, session_id, patient_id, exercise_id, rep_number, score, timestamp_utc, idempotency_key, metadata)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (event_id, session_id, patient_id, exercise_id, rep_number, score, now_iso, idempotency_key, json.dumps(body)),
            )

            # 4. Increment Verified Count
            new_verified = session["verified_count"] + 1
            target = session["target_count_snapshot"]

            new_status = session["status"]
            ended_at = session["ended_at"]
            if new_verified >= target:
                new_status = "camera_verified_completed"
                ended_at = now_iso
            elif session["status"] == "pending" or session["status"] == "reminded":
                new_status = "in_progress"

            conn.execute(
                """
                UPDATE rehab_sessions
                SET verified_count = ?, status = ?, ended_at = ?
                WHERE session_id = ?
                """,
                (new_verified, new_status, ended_at, session_id),
            )
            insert_outbox_event(
                conn=conn,
                patient_id=patient_id,
                event_type="rehab.session_updated",
                aggregate_type="rehab_session",
                aggregate_id=session_id,
                aggregate_version=new_verified,
                occurred_at=now_iso,
                actor_id=None,
                source="camera_ai",
                payload_minimal={
                    "session_id": session_id,
                    "verified_count": new_verified,
                    "target_count": target,
                    "status": new_status,
                    "rep_number": rep_number,
                },
                event_id=event_id,
            )
            conn.commit()
            self._trigger_relay()

        return 200, {
            "event_id": event_id,
            "status": "accepted",
            "verified_count": new_verified,
            "target_count": target,
            "session_status": new_status,
        }

    def _mark_rehab_unavailable(self, session_id: str) -> Tuple[int, Dict[str, Any]]:
        now_iso = datetime.now(timezone.utc).isoformat()
        with self.db.connect() as conn:
            sess = conn.fetchone("SELECT patient_id, verified_count FROM rehab_sessions WHERE session_id = ?", (session_id,))
            patient_id = sess["patient_id"] if sess else ""

            conn.execute(
                "UPDATE rehab_sessions SET status = 'verification_unavailable', notes = 'Camera bị che khuất hoặc lỗi đường truyền' WHERE session_id = ?",
                (session_id,),
            )
            if patient_id:
                insert_outbox_event(
                    conn=conn,
                    patient_id=patient_id,
                    event_type="rehab.session_updated",
                    aggregate_type="rehab_session",
                    aggregate_id=session_id,
                    aggregate_version=sess["verified_count"],
                    occurred_at=now_iso,
                    actor_id=None,
                    source="elder_app",
                    payload_minimal={"session_id": session_id, "status": "verification_unavailable"},
                )
            conn.commit()
            self._trigger_relay()
        return 200, {"session_id": session_id, "status": "verification_unavailable"}

    # =========================================================================
    # CAREGIVER REPORTS & DOCTOR REPLIES
    # =========================================================================
    def _get_caregiver_reports(self, patient_id: str) -> Tuple[int, Dict[str, Any]]:
        with self.db.connect() as conn:
            reports = conn.fetchall(
                """
                SELECT cr.*, u.full_name as caregiver_name
                FROM caregiver_reports cr
                JOIN caregivers c ON cr.caregiver_id = c.caregiver_id
                JOIN users u ON c.user_id = u.user_id
                WHERE cr.patient_id = ?
                ORDER BY cr.created_at DESC
                """,
                (patient_id,),
            )
            for r in reports:
                messages = conn.fetchall(
                    """
                    SELECT rm.*, u.full_name as sender_name, u.role as sender_role
                    FROM report_messages rm
                    JOIN users u ON rm.sender_user_id = u.user_id
                    WHERE rm.report_id = ?
                    ORDER BY rm.created_at ASC
                    """,
                    (r["report_id"],),
                )
                r["messages"] = messages
            return 200, {"reports": reports}

    def _add_caregiver_report(self, patient_id: str, user_id: str, body: Dict[str, Any]) -> Tuple[int, Dict[str, Any]]:
        desc = body.get("description", "").strip()
        obs_time = body.get("observed_at") or datetime.now(timezone.utc).isoformat()
        severity = body.get("severity", "NORMAL")
        now_iso = datetime.now(timezone.utc).isoformat()
        rid = f"rep_{uuid.uuid4().hex[:16]}"

        with self.db.connect() as conn:
            cg = conn.fetchone("SELECT caregiver_id FROM caregivers WHERE user_id = ?", (user_id,))
            cg_id = cg["caregiver_id"] if cg else "caregiver_001"

            conn.execute(
                """
                INSERT INTO caregiver_reports (report_id, patient_id, caregiver_id, description, observed_at, severity, status, attachments, created_at)
                VALUES (?, ?, ?, ?, ?, ?, 'NEW', '[]', ?)
                """,
                (rid, patient_id, cg_id, desc, obs_time, severity, now_iso),
            )
            insert_outbox_event(
                conn=conn,
                patient_id=patient_id,
                event_type="caregiver.report_created",
                aggregate_type="caregiver_report",
                aggregate_id=rid,
                aggregate_version=1,
                occurred_at=now_iso,
                actor_id=user_id,
                source="web_caregiver",
                payload_minimal={"report_id": rid, "severity": severity, "caregiver_id": cg_id},
            )
            conn.commit()
            self._trigger_relay()
            self.auth.log_audit(user_id, "SUBMIT_REPORT", "caregiver_report", rid)

        return 201, {"report_id": rid, "status": "NEW"}

    def _respond_caregiver_report(
        self, patient_id: str, report_id: str, user_id: str, body: Dict[str, Any]
    ) -> Tuple[int, Dict[str, Any]]:
        reply_body = body.get("body", "").strip()
        if not reply_body:
            return 400, {"error": "Nội dung phản hồi không được để trống"}

        now_iso = datetime.now(timezone.utc).isoformat()
        msg_id = f"rm_{uuid.uuid4().hex[:16]}"
        with self.db.connect() as conn:
            conn.execute(
                """
                INSERT INTO report_messages (message_id, report_id, sender_user_id, body, created_at, status)
                VALUES (?, ?, ?, ?, ?, 'sent')
                """,
                (msg_id, report_id, user_id, reply_body, now_iso),
            )
            conn.execute("UPDATE caregiver_reports SET status = 'RESPONDED' WHERE report_id = ?", (report_id,))
            insert_outbox_event(
                conn=conn,
                patient_id=patient_id,
                event_type="caregiver.report_replied",
                aggregate_type="caregiver_report",
                aggregate_id=report_id,
                aggregate_version=1,
                occurred_at=now_iso,
                actor_id=user_id,
                source="web_clinician",
                payload_minimal={"report_id": report_id, "message_id": msg_id, "status": "RESPONDED"},
            )
            conn.commit()
            self._trigger_relay()
            self.auth.log_audit(user_id, "RESPOND_REPORT", "caregiver_report", report_id)

        return 200, {"message_id": msg_id, "status": "RESPONDED"}

    # =========================================================================
    # PRESCRIPTIONS & VERSION REVISIONS
    # =========================================================================
    def _get_prescriptions(self, patient_id: str) -> Tuple[int, Dict[str, Any]]:
        with self.db.connect() as conn:
            prescriptions = conn.fetchall(
                """
                SELECT p.*, u.full_name as prescriber_name, cl.specialty as clinician_specialty
                FROM prescriptions p
                LEFT JOIN clinicians cl ON p.prescriber_clinician_id = cl.clinician_id
                LEFT JOIN users u ON cl.user_id = u.user_id
                WHERE p.patient_id = ?
                ORDER BY p.version DESC, p.prescribed_date DESC
                """,
                (patient_id,),
            )
            for p in prescriptions:
                items = conn.fetchall(
                    """
                    SELECT pi.*, m.brand_name, m.generic_name, m.product_strength, m.strength_unit, m.dosage_form
                    FROM prescription_items pi
                    JOIN medications m ON pi.medication_id = m.medication_id
                    WHERE pi.prescription_id = ?
                    """,
                    (p["prescription_id"],),
                )
                p["items"] = items
            return 200, {"prescriptions": prescriptions}

    def _create_prescription(
        self, patient_id: str, user_id: str, body: Dict[str, Any]
    ) -> Tuple[int, Dict[str, Any]]:
        try:
            doctor_id = body.get("doctor_id")
            diagnosis = (body.get("diagnosis") or body.get("clinical_indication") or "").strip()
            notes = body.get("notes", "").strip()
            items = body.get("items", [])
            if not items:
                return 400, {"error": "Đơn thuốc cần có ít nhất một loại thuốc"}

            for item in items:
                times = item.get('local_times') or item.get('times') or item.get('times_of_day') or ['08:00']
                if not isinstance(times, list) or any(not isinstance(t, str) or not re.fullmatch(r'([01]\d|2[0-3]):[0-5]\d', t) for t in times):
                    return 400, {"error": "Giờ uống thuốc phải theo định dạng HH:MM"}

            now_iso = datetime.now(timezone.utc).isoformat()
            start_date_str = str(body.get("start_date") or body.get("effective_from") or now_iso[:10]).strip()
            end_date_str = str(body.get("end_date")).strip() if body.get("end_date") else None
            try:
                start_day = date.fromisoformat(start_date_str[:10])
                end_day = date.fromisoformat(end_date_str[:10]) if end_date_str else None
            except ValueError:
                return 400, {"error": "Ngày bắt đầu/kết thúc đơn thuốc không hợp lệ"}
            if end_day is not None and end_day < start_day:
                return 400, {"error": "Ngày kết thúc không được trước ngày bắt đầu dùng thuốc"}
            effective_from = body.get("effective_from") or (f"{start_date_str}T00:00:00+00:00" if len(start_date_str) == 10 else now_iso)
            if not body.get("effective_from") and effective_from < now_iso:
                # Starting today: the schedule starts now, so the rolling scheduler does not recreate this
                # morning's doses as missed either.
                effective_from = now_iso
            rx_id = f"rx_{uuid.uuid4().hex[:8]}"
            rx_code = body.get("prescription_code") or f"DON-{uuid.uuid4().hex[:6].upper()}"

            with self.db.connect() as conn:
                # Resolve clinician
                cl = conn.fetchone("SELECT clinician_id FROM clinicians WHERE user_id = ?", (user_id,))
                if cl:
                    clinician_id = cl["clinician_id"]
                elif doctor_id:
                    d_row = conn.fetchone("SELECT clinician_id FROM clinicians WHERE clinician_id = ?", (doctor_id,))
                    clinician_id = d_row["clinician_id"] if d_row else None
                else:
                    clinician_id = None

                if not clinician_id:
                    any_c = conn.fetchone("SELECT clinician_id FROM clinicians LIMIT 1")
                    if any_c:
                        clinician_id = any_c["clinician_id"]
                    else:
                        clinician_id = "clinician_001"
                        conn.execute(
                            """
                            INSERT INTO clinicians (clinician_id, user_id, workplace, specialty, license_number, is_verified, is_simulation)
                            VALUES (?, ?, 'Bệnh viện Lão khoa Trung Ương', 'Khoa Nội Tim Mạch', 'CCHN-001', 1, 1)
                            ON CONFLICT(clinician_id) DO NOTHING
                            """,
                            (clinician_id, user_id),
                        )

                conn.execute(
                    """
                    INSERT INTO prescriptions (
                        prescription_id, patient_id, entered_by_user_id, prescription_code,
                        prescribed_date, prescriber_clinician_id, source, document_url,
                        status, version, valid_from, valid_to
                    ) VALUES (?, ?, ?, ?, ?, ?, 'clinician_entered', '', 'ACTIVE', 1, ?, ?)
                    """,
                    (rx_id, patient_id, user_id, rx_code, start_date_str[:10], clinician_id, effective_from, end_date_str),
                )

                KNOWN_DEFAULTS = {
                    "med_001": ("Amlor 5mg (Amlodipine)", "Amlodipine besylate", "5", "mg", "Viên nén"),
                    "med_002": ("Glucophage 500mg (Metformin)", "Metformin Hydrochloride", "500", "mg", "Viên bao phim"),
                    "med_003": ("Panadol 500mg (Paracetamol)", "Paracetamol", "500", "mg", "Viên nén"),
                    "med_004": ("Losartan 50mg (Cozaar)", "Losartan potassium", "50", "mg", "Viên nén"),
                    "med_005": ("Lipitor 20mg (Atorvastatin)", "Atorvastatin calcium", "20", "mg", "Viên bao phim"),
                }

                for it in items:
                    pi_id = f"pi_{uuid.uuid4().hex[:8]}"
                    med_id = it.get("medication_id")
                    if med_id and med_id != "__custom__":
                        med = conn.fetchone(
                            "SELECT medication_id, brand_name, product_strength, dosage_form FROM medications WHERE medication_id = ?",
                            (med_id,),
                        )
                        if med:
                            med_name = med["brand_name"]
                            strength = med["product_strength"]
                            form = med["dosage_form"]
                        else:
                            if med_id in KNOWN_DEFAULTS:
                                med_name, gen_name, str_val, str_u, form = KNOWN_DEFAULTS[med_id]
                            else:
                                med_name = it.get("medication_name") or it.get("brand_name") or "Thuốc"
                                gen_name = it.get("generic_name") or med_name
                                str_val = str(it.get("product_strength") or it.get("strength") or "500")
                                str_u = it.get("strength_unit") or "mg"
                                form = it.get("form") or it.get("dosage_form") or "Viên nén"

                            conn.execute(
                                """
                                INSERT INTO medications (medication_id, brand_name, generic_name, product_strength, strength_unit, dosage_form, image_path, safety_notes)
                                VALUES (?, ?, ?, ?, ?, ?, '', '')
                                ON CONFLICT(medication_id) DO NOTHING
                                """,
                                (med_id, med_name, gen_name, str_val, str_u, form),
                            )
                    else:
                        med_name = it.get("medication_name") or it.get("brand_name") or "Thuốc mới"
                        gen_name = it.get("generic_name") or med_name
                        str_val = str(it.get("product_strength") or it.get("strength") or "500")
                        str_u = it.get("strength_unit") or "mg"
                        form = it.get("form") or it.get("dosage_form") or "Viên nén"
                        med = conn.fetchone(
                            "SELECT medication_id FROM medications WHERE brand_name = ?", (med_name,)
                        )
                        if med:
                            med_id = med["medication_id"]
                        else:
                            med_id = f"med_{uuid.uuid4().hex[:8]}"
                            conn.execute(
                                """
                                INSERT INTO medications (medication_id, brand_name, generic_name, product_strength, strength_unit, dosage_form, image_path, safety_notes)
                                VALUES (?, ?, ?, ?, ?, ?, '', '')
                                """,
                                (med_id, med_name, gen_name, str_val, str_u, form),
                            )

                    raw_amt = str(it.get("dose_amount", "1"))
                    num_match = re.search(r"\d+(\.\d+)?", raw_amt)
                    dose_amt = float(num_match.group(0)) if num_match else 1.0
                    dose_unit = it.get("dose_unit") or it.get("unit") or "viên"
                    instr = it.get("instructions") or "Uống đúng theo đơn"
                    times = it.get("local_times") or it.get("times") or it.get("times_of_day") or ["08:00"]

                    conn.execute(
                        """
                        INSERT INTO prescription_items (
                            prescription_item_id, prescription_id, medication_id, dose_amount,
                            dose_unit, route_of_administration, frequency_description,
                            original_instructions, start_date, end_date, status
                        ) VALUES (?, ?, ?, ?, ?, 'Đường uống', 'Hàng ngày', ?, ?, ?, 'ACTIVE')
                        """,
                        (pi_id, rx_id, med_id, dose_amt, dose_unit, instr, start_date_str[:10], end_date_str),
                    )

                    sched_id = f"sch_{uuid.uuid4().hex[:16]}"
                    conn.execute(
                        """
                        INSERT INTO medication_schedules (
                            schedule_id, prescription_item_id, revision, timezone, local_times,
                            days_of_week, reminder_delay_minutes, grace_period_minutes,
                            caregiver_alert_enabled, is_active, effective_from
                        ) VALUES (?, ?, 1, 'Asia/Ho_Chi_Minh', ?, '[0,1,2,3,4,5,6]', 10, 60, 1, 1, ?)
                        """,
                        (sched_id, pi_id, json.dumps(times), effective_from),
                    )

                    # Generate occurrences covering today and next 7 days. Times of today that are already past
                    # are skipped: a prescription written at 21:00 must not create this morning's 08:00 dose as
                    # "missed" (field 09/11: three "Quá giờ, chưa uống" rows at 08:00 and false alerts).
                    now_local = _vn_now()
                    today_obj = now_local.date()
                    try:
                        s_date = date.fromisoformat(start_date_str[:10])
                    except Exception:
                        s_date = today_obj
                    try:
                        e_date = date.fromisoformat(end_date_str[:10]) if end_date_str else today_obj + timedelta(days=7)
                    except Exception:
                        e_date = today_obj + timedelta(days=7)

                    gen_start = max(s_date, today_obj)
                    gen_end = min(e_date, today_obj + timedelta(days=7))

                    curr_d = gen_start
                    while curr_d <= gen_end:
                        for t_str in times:
                            try:
                                hh, mm = map(int, t_str.split(":"))
                                local_dt = datetime.combine(curr_d, time(hh, mm))
                                if local_dt < now_local - timedelta(minutes=1):
                                    continue
                                utc_dt = local_dt - timedelta(hours=7)  # UTC+7
                                occ_id = f"occ_{uuid.uuid4().hex[:16]}"
                                conn.execute(
                                    """
                                    INSERT INTO dose_occurrences (
                                        occurrence_id, schedule_id, revision, scheduled_at_utc,
                                        local_scheduled_time, grace_deadline_utc, state,
                                        medication_name_snapshot, dose_snapshot, instructions_snapshot,
                                        patient_id
                                    ) VALUES (?, ?, 1, ?, ?, ?, 'PENDING', ?, ?, ?, ?)
                                    """,
                                    (
                                        occ_id,
                                        sched_id,
                                        utc_dt.isoformat(),
                                        local_dt.isoformat(),
                                        (utc_dt + timedelta(minutes=60)).isoformat(),
                                        med_name,
                                        f"{dose_amt} {dose_unit}",
                                        instr,
                                        patient_id,
                                    ),
                                )
                            except Exception as ex_occ:
                                logger.warning("Error generating occurrence: %s", ex_occ)
                        curr_d += timedelta(days=1)

                insert_outbox_event(
                    conn=conn,
                    patient_id=patient_id,
                    event_type="prescription.created",
                    aggregate_type="prescription",
                    aggregate_id=rx_id,
                    aggregate_version=1,
                    occurred_at=now_iso,
                    actor_id=user_id,
                    source="web_clinician",
                    payload_minimal={"prescription_id": rx_id, "prescription_code": rx_code},
                )
                conn.commit()
                self._trigger_relay()
                self.auth.log_audit(
                    user_id, "CREATE_PRESCRIPTION", "prescription", rx_id, {"code": rx_code, "items_count": len(items)}
                )

            return 201, {
                "prescription_id": rx_id,
                "prescription_code": rx_code,
                "version": 1,
                "status": "ACTIVE",
                "message": "Đã kê đơn thuốc mới thành công",
            }
        except Exception as e:
            logger.exception("Error in _create_prescription: %s", e)
            return 500, {"error": f"Lỗi hệ thống khi lưu đơn thuốc: {str(e)}"}

    def _revise_prescription(
        self, patient_id: str, prescription_id: str, user_id: str, body: Dict[str, Any]
    ) -> Tuple[int, Dict[str, Any]]:
        """
        Revision mechanism:
        - Old version & past dose events remain 100% IMMUTABLE
        - Mark old prescription SUPERSEDED
        - Cancel future PENDING doses
        - Create new prescription with version = version + 1
        - Generate new occurrences starting from effective_date
        """
        try:
            reason = body.get("reason", "Bác sĩ điều chỉnh liều")
            items = body.get("items", [])
            expected_version = body.get("expected_version")
            now_iso = datetime.now(timezone.utc).isoformat()
            effective_from = body.get("effective_from") or now_iso

            with self.db.connect() as conn:
                old_p = conn.fetchone("SELECT * FROM prescriptions WHERE prescription_id = ? AND patient_id = ?", (prescription_id, patient_id))
                if not old_p:
                    return 404, {"error": "Không tìm thấy đơn thuốc gốc"}

                # Check expected_version concurrency conflict (HTTP 409)
                if expected_version is not None and int(expected_version) != old_p["version"]:
                    return 409, {
                        "error": f"Xung đột phiên bản: Đơn thuốc đã được chỉnh sửa lên phiên bản {old_p['version']}. Vui lòng làm mới trang để xem thông tin mới nhất.",
                        "current_version": old_p["version"],
                        "prescription_id": prescription_id,
                    }

                # Clinician profile
                cl = conn.fetchone("SELECT clinician_id FROM clinicians WHERE user_id = ?", (user_id,))
                clinician_id = cl["clinician_id"] if cl else old_p["prescriber_clinician_id"]
                if not clinician_id:
                    any_c = conn.fetchone("SELECT clinician_id FROM clinicians LIMIT 1")
                    clinician_id = any_c["clinician_id"] if any_c else "clinician_001"

                new_version = old_p["version"] + 1
                new_p_id = f"rx_{uuid.uuid4().hex[:16]}"
                new_code = f"Đơn thuốc điều chỉnh v{new_version}"

                # 1. Supersede old prescription
                conn.execute("UPDATE prescriptions SET status = 'SUPERSEDED', valid_to = ? WHERE prescription_id = ?", (now_iso, prescription_id))

                # 2. Cancel future pending doses of old prescription
                conn.execute(
                    """
                    UPDATE dose_occurrences
                    SET state = 'CANCELLED'
                    WHERE patient_id = ? AND state = 'PENDING' AND scheduled_at_utc > ?
                    """,
                    (patient_id, now_iso),
                )

                # 3. Insert new prescription
                conn.execute(
                    """
                    INSERT INTO prescriptions (prescription_id, patient_id, entered_by_user_id, prescription_code, prescribed_date, prescriber_clinician_id, source, document_url, status, version, valid_from, valid_to)
                    VALUES (?, ?, ?, ?, ?, ?, 'clinician_entered', '', 'ACTIVE', ?, ?, NULL)
                    """,
                    (new_p_id, patient_id, user_id, new_code, now_iso[:10], clinician_id, new_version, effective_from),
                )

                # 4. Insert items and future occurrences
                KNOWN_DEFAULTS = {
                    "med_001": ("Amlor 5mg (Amlodipine)", "Amlodipine besylate", "5", "mg", "Viên nén"),
                    "med_002": ("Glucophage 500mg (Metformin)", "Metformin Hydrochloride", "500", "mg", "Viên bao phim"),
                    "med_003": ("Panadol 500mg (Paracetamol)", "Paracetamol", "500", "mg", "Viên nén"),
                    "med_004": ("Losartan 50mg (Cozaar)", "Losartan potassium", "50", "mg", "Viên nén"),
                    "med_005": ("Lipitor 20mg (Atorvastatin)", "Atorvastatin calcium", "20", "mg", "Viên bao phim"),
                }

                for it in items:
                    pi_id = f"pi_{uuid.uuid4().hex[:16]}"
                    med_id = it.get("medication_id", "med_001")
                    custom_name = it.get("custom_brand_name") or it.get("custom_med_name") or it.get("brand_name")
                    if med_id == "__custom__" or (not med_id and custom_name):
                        clean_name = custom_name.strip() if custom_name else "Thuốc điều trị mới"
                        existing_m = conn.fetchone("SELECT medication_id FROM medications WHERE LOWER(brand_name) = ?", (clean_name.lower(),))
                        if existing_m:
                            med_id = existing_m["medication_id"]
                        else:
                            med_id = f"med_c_{uuid.uuid4().hex[:8]}"
                            strength = str(it.get("product_strength") or it.get("dose_amount") or "500")
                            unit = str(it.get("strength_unit") or it.get("dose_unit") or "mg")
                            form = str(it.get("dosage_form") or "Viên nén")
                            conn.execute(
                                """
                                INSERT INTO medications (medication_id, brand_name, generic_name, product_strength, strength_unit, dosage_form)
                                VALUES (?, ?, ?, ?, ?, ?)
                                """,
                                (med_id, clean_name, it.get("generic_name", clean_name), strength, unit, form),
                            )
                    else:
                        med_check = conn.fetchone("SELECT medication_id FROM medications WHERE medication_id = ?", (med_id,))
                        if not med_check:
                            if med_id in KNOWN_DEFAULTS:
                                def_brand, def_gen, def_str, def_unit, def_form = KNOWN_DEFAULTS[med_id]
                            else:
                                def_brand = it.get("brand_name") or "Thuốc"
                                def_gen = it.get("generic_name") or def_brand
                                def_str = str(it.get("product_strength") or "500")
                                def_unit = "mg"
                                def_form = "Viên nén"
                            conn.execute(
                                """
                                INSERT INTO medications (medication_id, brand_name, generic_name, product_strength, strength_unit, dosage_form)
                                VALUES (?, ?, ?, ?, ?, ?)
                                ON CONFLICT(medication_id) DO NOTHING
                                """,
                                (med_id, def_brand, def_gen, def_str, def_unit, def_form),
                            )

                    raw_amt = str(it.get("dose_amount", 1.0))
                    import re
                    num_match = re.search(r"[\d.]+", raw_amt)
                    dose_amt = float(num_match.group(0)) if num_match else 1.0
                    dose_unit = it.get("dose_unit", "viên")
                    instr = it.get("instructions", "Uống sau ăn")
                    times = it.get("local_times") or it.get("times") or ["08:00"]

                    conn.execute(
                        """
                        INSERT INTO prescription_items (prescription_item_id, prescription_id, medication_id, dose_amount, dose_unit, route_of_administration, frequency_description, original_instructions, start_date, status)
                        VALUES (?, ?, ?, ?, ?, 'Đường uống', 'Hàng ngày', ?, ?, 'ACTIVE')
                        """,
                        (pi_id, new_p_id, med_id, dose_amt, dose_unit, instr, now_iso[:10]),
                    )

                    sched_id = f"sch_{uuid.uuid4().hex[:16]}"
                    conn.execute(
                        """
                        INSERT INTO medication_schedules (schedule_id, prescription_item_id, revision, timezone, local_times, days_of_week, reminder_delay_minutes, grace_period_minutes, caregiver_alert_enabled, is_active, effective_from)
                        VALUES (?, ?, 1, 'Asia/Ho_Chi_Minh', ?, '[0,1,2,3,4,5,6]', 10, 60, 1, 1, ?)
                        """,
                        (sched_id, pi_id, json.dumps(times), effective_from),
                    )

                    # Generate occurrences for today (if not already taken) and next 3 days
                    med = conn.fetchone("SELECT brand_name FROM medications WHERE medication_id = ?", (med_id,))
                    brand = med["brand_name"] if med else "Thuốc"
                    now_local = _vn_now()
                    for day_offset in range(0, 4):
                        d_obj = now_local.date() + timedelta(days=day_offset)
                        for t_str in times:
                            hh, mm = map(int, t_str.split(":"))
                            local_dt = datetime.combine(d_obj, time(hh, mm))
                            if local_dt < now_local - timedelta(minutes=1):
                                continue  # past times stay with the previous version (history), never re-created
                            utc_dt = local_dt - timedelta(hours=7)  # UTC+7
                            # If today, check if this dose was already confirmed under the old prescription
                            prior_confirmed = None
                            if day_offset == 0:
                                prior_confirmed = conn.fetchone(
                                    """
                                    SELECT action_taken_at_utc, action_actor, action_notes
                                    FROM dose_occurrences
                                    WHERE patient_id = ? AND state = 'CONFIRMED'
                                      AND local_scheduled_time = ?
                                      AND schedule_id IN (
                                          SELECT s.schedule_id FROM medication_schedules s
                                          JOIN prescription_items pi ON s.prescription_item_id = pi.prescription_item_id
                                          WHERE pi.prescription_id = ?
                                      )
                                    """,
                                    (patient_id, local_dt.isoformat(), prescription_id),
                                )
                            occ_id = f"occ_{uuid.uuid4().hex[:16]}"
                            if prior_confirmed:
                                conn.execute(
                                    """
                                    INSERT INTO dose_occurrences (
                                        occurrence_id, schedule_id, revision, scheduled_at_utc, local_scheduled_time, grace_deadline_utc, state,
                                        medication_name_snapshot, dose_snapshot, instructions_snapshot, patient_id,
                                        action_taken_at_utc, action_actor, action_notes
                                    ) VALUES (?, ?, 1, ?, ?, ?, 'CONFIRMED', ?, ?, ?, ?, ?, ?, ?)
                                    """,
                                    (occ_id, sched_id, utc_dt.isoformat(), local_dt.isoformat(), (utc_dt + timedelta(minutes=60)).isoformat(), brand, f"{dose_amt} {dose_unit}", instr, patient_id, prior_confirmed["action_taken_at_utc"], prior_confirmed["action_actor"], prior_confirmed["action_notes"]),
                                )
                            else:
                                conn.execute(
                                    """
                                    INSERT INTO dose_occurrences (
                                        occurrence_id, schedule_id, revision, scheduled_at_utc, local_scheduled_time, grace_deadline_utc, state,
                                        medication_name_snapshot, dose_snapshot, instructions_snapshot, patient_id
                                    ) VALUES (?, ?, 1, ?, ?, ?, 'PENDING', ?, ?, ?, ?)
                                    """,
                                    (occ_id, sched_id, utc_dt.isoformat(), local_dt.isoformat(), (utc_dt + timedelta(minutes=60)).isoformat(), brand, f"{dose_amt} {dose_unit}", instr, patient_id),
                                )

                insert_outbox_event(
                    conn=conn,
                    patient_id=patient_id,
                    event_type="prescription.revised",
                    aggregate_type="prescription",
                    aggregate_id=new_p_id,
                    aggregate_version=new_version,
                    occurred_at=now_iso,
                    actor_id=user_id,
                    source="web_clinician",
                    payload_minimal={"prescription_id": new_p_id, "version": new_version},
                )
                conn.commit()
                self._trigger_relay()
                self.auth.log_audit(user_id, "REVISE_PRESCRIPTION", "prescription", new_p_id, {"version": new_version, "reason": reason})

            return 201, {"prescription_id": new_p_id, "version": new_version, "status": "ACTIVE"}
        except Exception as e:
            logger.exception("Error in _revise_prescription: %s", e)
            return 500, {"error": f"Lỗi hệ thống khi điều chỉnh đơn thuốc: {str(e)}"}

    def _cancel_prescription(
        self, patient_id: str, prescription_id: str, user_id: str, body: Dict[str, Any]
    ) -> Tuple[int, Dict[str, Any]]:
        reason = body.get("reason", "Bác sĩ ngưng hiệu lực đơn thuốc")
        now_iso = datetime.now(timezone.utc).isoformat()
        with self.db.connect() as conn:
            old_p = conn.fetchone(
                "SELECT * FROM prescriptions WHERE prescription_id = ? AND patient_id = ?",
                (prescription_id, patient_id),
            )
            if not old_p:
                return 404, {"error": "Không tìm thấy đơn thuốc"}

            # Mark prescription CANCELLED
            conn.execute(
                "UPDATE prescriptions SET status = 'CANCELLED', valid_to = ? WHERE prescription_id = ?",
                (now_iso, prescription_id),
            )
            # Find all medications in this prescription
            med_rows = conn.fetchall(
                "SELECT DISTINCT medication_id FROM prescription_items WHERE prescription_id = ?",
                (prescription_id,),
            )
            med_ids = [m["medication_id"] for m in med_rows]

            # Deactivate medication schedules for this prescription and any superseded predecessor versions
            conn.execute(
                """
                UPDATE medication_schedules
                SET is_active = 0, effective_to = ?
                WHERE prescription_item_id IN (
                    SELECT prescription_item_id FROM prescription_items WHERE prescription_id = ?
                )
                """,
                (now_iso, prescription_id),
            )
            if med_ids:
                placeholders = ",".join("?" for _ in med_ids)
                conn.execute(
                    f"""
                    UPDATE medication_schedules
                    SET is_active = 0, effective_to = ?
                    WHERE prescription_item_id IN (
                        SELECT pi.prescription_item_id FROM prescription_items pi
                        JOIN prescriptions p ON pi.prescription_id = p.prescription_id
                        WHERE p.patient_id = ? AND p.status = 'SUPERSEDED'
                          AND pi.medication_id IN ({placeholders})
                    )
                    """,
                    [now_iso, patient_id] + med_ids,
                )

            # Cancel all unconfirmed occurrences for this prescription
            conn.execute(
                """
                UPDATE dose_occurrences
                SET state = 'CANCELLED', action_notes = ?
                WHERE patient_id = ? AND state IN ('PENDING', 'REMINDING', 'SNOOZED', 'OVERDUE')
                  AND schedule_id IN (
                    SELECT s.schedule_id FROM medication_schedules s
                    JOIN prescription_items pi ON s.prescription_item_id = pi.prescription_item_id
                    WHERE pi.prescription_id = ?
                  )
                """,
                (f"Đã hủy theo đơn thuốc {old_p.get('prescription_code', prescription_id)}", patient_id, prescription_id),
            )
            # Also cancel all unconfirmed occurrences of predecessor superseded prescriptions of same medications
            if med_ids:
                conn.execute(
                    f"""
                    UPDATE dose_occurrences
                    SET state = 'CANCELLED', action_notes = ?
                    WHERE patient_id = ? AND state IN ('PENDING', 'REMINDING', 'SNOOZED', 'OVERDUE')
                      AND schedule_id IN (
                        SELECT s.schedule_id FROM medication_schedules s
                        JOIN prescription_items pi ON s.prescription_item_id = pi.prescription_item_id
                        JOIN prescriptions p ON pi.prescription_id = p.prescription_id
                        WHERE p.patient_id = ? AND p.status IN ('CANCELLED', 'SUPERSEDED')
                          AND pi.medication_id IN ({placeholders})
                      )
                    """,
                    [f"Đã hủy theo đơn thuốc {old_p.get('prescription_code', prescription_id)}", patient_id, patient_id] + med_ids,
                )

            insert_outbox_event(
                conn=conn,
                patient_id=patient_id,
                event_type="prescription.cancelled",
                aggregate_type="prescription",
                aggregate_id=prescription_id,
                aggregate_version=1,
                occurred_at=now_iso,
                actor_id=user_id,
                source="web_clinician",
                payload_minimal={"prescription_id": prescription_id},
            )
            conn.commit()
            self._trigger_relay()
            self.auth.log_audit(user_id, "CANCEL_PRESCRIPTION", "prescription", prescription_id, {"reason": reason})

        return 200, {
            "message": "Đã ngưng hiệu lực đơn thuốc thành công",
            "prescription_id": prescription_id,
            "status": "CANCELLED",
        }

    # =========================================================================
    # EXERCISE PLANS (REHABILITATION WORKOUT TARGETS & SCHEDULE)
    # =========================================================================
    def _get_exercise_plans(self, patient_id: str) -> Tuple[int, Dict[str, Any]]:
        with self.db.connect() as conn:
            existing = conn.fetchall(
                "SELECT plan_id FROM rehab_plans WHERE patient_id = ? AND is_active = 1",
                (patient_id,),
            )
            if not existing:
                ensure_default_rehab_plans(conn, patient_id)
                conn.commit()

            plans = conn.fetchall(
                """
                SELECT p.*, epv.quality_threshold, epv.version as latest_version, epv.reason
                FROM rehab_plans p
                LEFT JOIN exercise_plan_versions epv ON p.plan_id = epv.plan_id AND p.version = epv.version
                WHERE p.patient_id = ? AND p.is_active = 1
                ORDER BY p.plan_id ASC
                """,
                (patient_id,),
            )
            return 200, {"plans": plans}

    def _reset_exercise_plans(
        self, patient_id: str, user_id: str, body: Dict[str, Any]
    ) -> Tuple[int, Dict[str, Any]]:
        default_time = body.get("local_reminder_time") or "16:00"
        with self.db.connect() as conn:
            ensure_default_rehab_plans(conn, patient_id, default_time=default_time)
            conn.commit()
            self._trigger_relay()
            self.auth.log_audit(user_id, "RESET_EXERCISE_PLANS", "patient", patient_id, {"time": default_time})
        return self._get_exercise_plans(patient_id)

    def _update_exercise_plan(
        self, patient_id: str, plan_id: str, user_id: str, body: Dict[str, Any]
    ) -> Tuple[int, Dict[str, Any]]:
        target_count = body.get("target_count")
        reminder_time = body.get("local_reminder_time") or body.get("reminder_time")
        quality_threshold = float(body.get("quality_threshold", 80.0))
        reason = body.get("reason", "Người nhà điều chỉnh chỉ tiêu luyện tập")
        expected_version = body.get("expected_version")
        now_iso = datetime.now(timezone.utc).isoformat()

        with self.db.connect() as conn:
            plan = conn.fetchone(
                "SELECT * FROM rehab_plans WHERE plan_id = ? AND patient_id = ?",
                (plan_id, patient_id),
            )
            if not plan:
                return 404, {"error": "Không tìm thấy kế hoạch bài tập"}

            # Concurrency version check (HTTP 409)
            if expected_version is not None and int(expected_version) != plan["version"]:
                return 409, {
                    "error": f"Xung đột phiên bản: Kế hoạch đã được cập nhật lên phiên bản {plan['version']}. Vui lòng làm mới trang.",
                    "current_version": plan["version"],
                }

            new_version = plan["version"] + 1
            new_target = int(target_count) if target_count is not None else plan["target_count"]
            new_time = reminder_time if reminder_time else plan["local_reminder_time"]

            conn.execute(
                """
                UPDATE rehab_plans
                SET target_count = ?, local_reminder_time = ?, version = ?
                WHERE plan_id = ?
                """,
                (new_target, new_time, new_version, plan_id),
            )

            # Update today's pending session if exists
            today_date = date.today().isoformat()
            conn.execute(
                """
                UPDATE rehab_sessions
                SET target_count_snapshot = ?, plan_version = ?
                WHERE patient_id = ? AND plan_id = ? AND local_date = ? AND status = 'pending'
                """,
                (new_target, new_version, patient_id, plan_id, today_date),
            )

            pv_id = f"epv_{plan_id}_v{new_version}"
            conn.execute(
                """
                INSERT INTO exercise_plan_versions (
                    plan_version_id, plan_id, patient_id, version, target_count,
                    quality_threshold, local_reminder_time, timezone, effective_from,
                    created_by_user_id, created_at, reason
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(plan_id, version) DO UPDATE SET
                    target_count = excluded.target_count,
                    quality_threshold = excluded.quality_threshold,
                    local_reminder_time = excluded.local_reminder_time
                """,
                (
                    pv_id, plan_id, patient_id, new_version, new_target,
                    quality_threshold, new_time, plan["timezone"], now_iso,
                    user_id, now_iso, reason
                ),
            )

            insert_outbox_event(
                conn=conn,
                patient_id=patient_id,
                event_type="rehab.plan_updated",
                aggregate_type="rehab_plan",
                aggregate_id=plan_id,
                aggregate_version=new_version,
                occurred_at=now_iso,
                actor_id=user_id,
                source="web_caregiver",
                payload_minimal={
                    "plan_id": plan_id,
                    "version": new_version,
                    "target_count": new_target,
                    "local_reminder_time": new_time,
                },
            )
            conn.commit()
            self._trigger_relay()
            self.auth.log_audit(user_id, "UPDATE_EXERCISE_PLAN", "rehab_plan", plan_id, {"version": new_version, "target": new_target})

        return 200, {
            "plan_id": plan_id,
            "version": new_version,
            "target_count": new_target,
            "local_reminder_time": new_time,
            "message": "Cập nhật kế hoạch bài tập thành công",
        }

    # =========================================================================
    # HEALTH MEASUREMENT REQUESTS (CAREGIVER REMINDERS FOR VITALS)
    # =========================================================================
    def _create_measurement_request(
        self, patient_id: str, user_id: str, body: Dict[str, Any]
    ) -> Tuple[int, Dict[str, Any]]:
        raw_code = body.get("observation_type_id") or body.get("observation_type_code") or body.get("type_code") or "blood_pressure_systolic"
        instructions = body.get("instructions", "Người nhà nhắc đo chỉ số sức khỏe").strip()
        due_at_utc = body.get("due_at_utc")
        local_due_time = body.get("local_due_time") or "Trong ngày hôm nay"
        req_id = f"mreq_{uuid.uuid4().hex[:12]}"
        client_req_id = body.get("client_request_id") or req_id
        now_iso = datetime.now(timezone.utc).isoformat()

        if not due_at_utc:
            # Default to 4 hours from now
            due_at_utc = (datetime.now(timezone.utc) + timedelta(hours=4)).isoformat()

        with self.db.connect() as conn:
            ot = conn.fetchone(
                "SELECT observation_type_id, name, standard_unit FROM observation_types WHERE observation_type_id = ? OR code IN (?, ?)",
                (raw_code, raw_code, "blood_pressure_systolic" if "blood_pressure" in str(raw_code) else raw_code),
            )
            if not ot:
                ot = conn.fetchone("SELECT observation_type_id, name, standard_unit FROM observation_types LIMIT 1")
            ot_id = ot["observation_type_id"]

            conn.execute(
                """
                INSERT INTO measurement_requests (
                    request_id, patient_id, requested_by_user_id, observation_type_id,
                    due_at_utc, local_due_time, instructions, status, completed_observation_id,
                    created_at_utc, client_request_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 'PENDING', NULL, ?, ?)
                """,
                (req_id, patient_id, user_id, ot_id, due_at_utc, local_due_time, instructions, now_iso, client_req_id),
            )

            conn.execute(
                """
                INSERT INTO measurement_request_logs (log_id, request_id, action, actor_id, occurred_at_utc, details)
                VALUES (?, ?, 'CREATED', ?, ?, ?)
                """,
                (f"mrl_{uuid.uuid4().hex[:12]}", req_id, user_id, now_iso, json.dumps({"instructions": instructions, "due_at": due_at_utc})),
            )

            insert_outbox_event(
                conn=conn,
                patient_id=patient_id,
                event_type="health.measurement_requested",
                aggregate_type="measurement_request",
                aggregate_id=req_id,
                aggregate_version=1,
                occurred_at=now_iso,
                actor_id=user_id,
                source="web_caregiver",
                payload_minimal={
                    "request_id": req_id,
                    "observation_type_id": ot_id,
                    "observation_type_name": ot["name"],
                    "due_at_utc": due_at_utc,
                    "instructions": instructions,
                },
            )
            conn.commit()
            self._trigger_relay()
            self.auth.log_audit(user_id, "CREATE_MEASUREMENT_REQUEST", "measurement_request", req_id, {"type": ot["name"]})

        return 201, {
            "request_id": req_id,
            "status": "PENDING",
            "observation_type_name": ot["name"],
            "message": "Đã gửi yêu cầu nhắc đo thành công",
        }

    def _get_measurement_requests(
        self, patient_id: str, query_params: Dict[str, str]
    ) -> Tuple[int, Dict[str, Any]]:
        status_filter = query_params.get("status")
        with self.db.connect() as conn:
            if status_filter:
                rows = conn.fetchall(
                    """
                    SELECT mr.*, ot.code as observation_type_code, ot.name as observation_type_name, ot.standard_unit,
                           u.full_name as requested_by_name
                    FROM measurement_requests mr
                    JOIN observation_types ot ON mr.observation_type_id = ot.observation_type_id
                    JOIN users u ON mr.requested_by_user_id = u.user_id
                    WHERE mr.patient_id = ? AND mr.status = ?
                    ORDER BY mr.due_at_utc DESC
                    """,
                    (patient_id, status_filter),
                )
            else:
                rows = conn.fetchall(
                    """
                    SELECT mr.*, ot.code as observation_type_code, ot.name as observation_type_name, ot.standard_unit,
                           u.full_name as requested_by_name
                    FROM measurement_requests mr
                    JOIN observation_types ot ON mr.observation_type_id = ot.observation_type_id
                    JOIN users u ON mr.requested_by_user_id = u.user_id
                    WHERE mr.patient_id = ?
                    ORDER BY mr.due_at_utc DESC
                    """,
                    (patient_id,),
                )
            return 200, {"requests": rows}

    def _cancel_measurement_request(
        self, patient_id: str, request_id: str, user_id: str, body: Dict[str, Any]
    ) -> Tuple[int, Dict[str, Any]]:
        now_iso = datetime.now(timezone.utc).isoformat()
        with self.db.connect() as conn:
            req = conn.fetchone(
                "SELECT * FROM measurement_requests WHERE request_id = ? AND patient_id = ?",
                (request_id, patient_id),
            )
            if not req:
                return 404, {"error": "Không tìm thấy yêu cầu đo"}

            conn.execute(
                "UPDATE measurement_requests SET status = 'CANCELLED' WHERE request_id = ?",
                (request_id,),
            )
            conn.execute(
                """
                INSERT INTO measurement_request_logs (log_id, request_id, action, actor_id, occurred_at_utc, details)
                VALUES (?, ?, 'CANCELLED', ?, ?, ?)
                """,
                (f"mrl_{uuid.uuid4().hex[:12]}", request_id, user_id, now_iso, json.dumps(body)),
            )
            insert_outbox_event(
                conn=conn,
                patient_id=patient_id,
                event_type="health.measurement_request_cancelled",
                aggregate_type="measurement_request",
                aggregate_id=request_id,
                aggregate_version=1,
                occurred_at=now_iso,
                actor_id=user_id,
                source="web_caregiver",
                payload_minimal={"request_id": request_id, "status": "CANCELLED"},
            )
            conn.commit()
            self._trigger_relay()
            self.auth.log_audit(user_id, "CANCEL_MEASUREMENT_REQUEST", "measurement_request", request_id)

        return 200, {"message": "Đã hủy yêu cầu nhắc đo", "request_id": request_id}

    def _get_medications_catalog(self) -> Tuple[int, Dict[str, Any]]:
        with self.db.connect() as conn:
            rows = conn.fetchall(
                "SELECT medication_id, brand_name, generic_name, product_strength, strength_unit, dosage_form FROM medications ORDER BY brand_name"
            )
            return 200, {"medications": rows}

    # =========================================================================
    # CARE RECOMMENDATIONS
    # =========================================================================
    def _get_recommendations(self, patient_id: str) -> Tuple[int, Dict[str, Any]]:
        with self.db.connect() as conn:
            recs = conn.fetchall(
                """
                SELECT cr.*, u.full_name as author_name, cl.workplace
                FROM care_recommendations cr
                JOIN clinicians cl ON cr.clinician_id = cl.clinician_id
                JOIN users u ON cl.user_id = u.user_id
                WHERE cr.patient_id = ? AND cr.status = 'ACTIVE'
                ORDER BY cr.created_at DESC
                """,
                (patient_id,),
            )
            return 200, {"recommendations": recs}

    def _add_recommendation(self, patient_id: str, user_id: str, body: Dict[str, Any]) -> Tuple[int, Dict[str, Any]]:
        cat = body.get("category", "general")
        title = body.get("title", "").strip()
        content = body.get("content", "").strip()
        now_iso = datetime.now(timezone.utc).isoformat()
        rec_id = f"rec_{uuid.uuid4().hex[:16]}"

        with self.db.connect() as conn:
            cl = conn.fetchone("SELECT clinician_id FROM clinicians WHERE user_id = ?", (user_id,))
            clinician_id = cl["clinician_id"] if cl else "clinician_001"

            conn.execute(
                """
                INSERT INTO care_recommendations (recommendation_id, patient_id, clinician_id, category, title, content, version, valid_from, valid_to, status, is_simulation, created_at)
                VALUES (?, ?, ?, ?, ?, ?, 1, ?, '2099-12-31T23:59:59Z', 'ACTIVE', 1, ?)
                """,
                (rec_id, patient_id, clinician_id, cat, title, content, now_iso, now_iso),
            )
            insert_outbox_event(
                conn=conn,
                patient_id=patient_id,
                event_type="recommendation.created",
                aggregate_type="care_recommendation",
                aggregate_id=rec_id,
                aggregate_version=1,
                occurred_at=now_iso,
                actor_id=user_id,
                source="web_clinician",
                payload_minimal={"recommendation_id": rec_id, "category": cat, "title": title},
            )
            conn.commit()
            self._trigger_relay()
            self.auth.log_audit(user_id, "POST_RECOMMENDATION", "care_recommendation", rec_id)

        return 201, {"recommendation_id": rec_id, "status": "ACTIVE"}

    # =========================================================================
    # EMERGENCY CONTACTS & CALL PROTOCOL
    # =========================================================================
    def _get_emergency_contacts(self, patient_id: str) -> Tuple[int, Dict[str, Any]]:
        with self.db.connect() as conn:
            contacts = conn.fetchall(
                "SELECT * FROM emergency_contacts WHERE patient_id = ? AND is_active = 1 ORDER BY priority ASC",
                (patient_id,),
            )
            # Evaluate duty hours right now
            now_local_hhmm = datetime.now().strftime("%H:%M")
            for c in contacts:
                start = c.get("on_duty_start", "08:00")
                end = c.get("on_duty_end", "17:00")
                is_on_duty = (start <= now_local_hhmm <= end)
                c["is_currently_on_duty"] = is_on_duty
                c["is_call_button_enabled"] = bool(c["is_verified"] and is_on_duty)
                c["duty_label"] = f"{start} - {end}" if start != "00:00" else "24/7"

            return 200, {
                "contacts": contacts,
                "current_time": now_local_hhmm,
                "notice": "Hotline là nút liên hệ bác sĩ trong giờ trực được cấu hình. Số chưa xác nhận hiển thị nhãn DEMO và không hỗ trợ quay số.",
            }

    def _log_emergency_event(self, patient_id: str, user_id: str, body: Dict[str, Any]) -> Tuple[int, Dict[str, Any]]:
        cid = body.get("contact_id")
        phone = body.get("phone", "")
        status = body.get("on_duty_status", "ON_DUTY")
        eid = f"ece_{uuid.uuid4().hex[:16]}"
        now_iso = datetime.now(timezone.utc).isoformat()

        with self.db.connect() as conn:
            conn.execute(
                """
                INSERT INTO emergency_contact_events (event_id, patient_id, initiator_user_id, contact_id, phone_snapshot, on_duty_status, channel, user_reported_status, timestamp_utc)
                VALUES (?, ?, ?, ?, ?, ?, 'TEL_PROMPT', 'dial_prompt_opened', ?)
                """,
                (eid, patient_id, user_id, cid, phone, status, now_iso),
            )
            conn.commit()

        return 200, {"event_id": eid, "status": "recorded"}

    def _mark_recommendation_read(self, patient_id: str, rec_id: str, user_id: str) -> Tuple[int, Dict[str, Any]]:
        now_iso = datetime.now(timezone.utc).isoformat()
        with self.db.connect() as conn:
            receipt_id = f"rcp_{uuid.uuid4().hex[:12]}"
            conn.execute(
                """
                INSERT INTO recommendation_receipts (receipt_id, recommendation_id, user_id, read_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(recommendation_id, user_id) DO UPDATE SET read_at = ?
                """,
                (receipt_id, rec_id, user_id, now_iso, now_iso),
            )
            insert_outbox_event(
                conn=conn,
                patient_id=patient_id,
                event_type="recommendation.read",
                aggregate_type="care_recommendation",
                aggregate_id=rec_id,
                aggregate_version=1,
                occurred_at=now_iso,
                actor_id=user_id,
                source="elder_app",
                payload_minimal={"recommendation_id": rec_id, "read_at": now_iso},
            )
            conn.commit()
            self._trigger_relay()
        return 200, {"message": "Đã ghi nhận đã xem lời khuyên", "recommendation_id": rec_id}

    def _get_patient_snapshot(self, patient_id: str, query_params: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
        sections_str = query_params.get("sections", "medication,rehab,vitals,mood,recommendations,emergency")
        sections = [s.strip() for s in sections_str.split(",")]
        now_utc = datetime.now(timezone.utc)
        now_iso = now_utc.isoformat()

        with self.db.connect() as conn:
            patient = conn.fetchone("SELECT * FROM patients WHERE patient_id = ?", (patient_id,))
            if not patient:
                return 404, {"error": "Không tìm thấy hồ sơ người cao tuổi"}

            pat_tz_str = patient["timezone"] or "Asia/Ho_Chi_Minh"
            date_param = query_params.get("date")
            if date_param:
                today_date = date_param
            else:
                try:
                    import zoneinfo
                    tz = zoneinfo.ZoneInfo(pat_tz_str)
                    today_date = datetime.now(tz).date().isoformat()
                except Exception:
                    today_date = date.today().isoformat()

            snapshot: Dict[str, Any] = {
                "patient_id": patient_id,
                "full_name": patient["full_name"],
                "gender": patient["gender"],
                "timezone": patient["timezone"],
                "server_time_utc": now_iso,
                "last_updated_at": now_iso,
                "sections": {},
            }

            if "medication" in sections:
                doses = conn.fetchall(
                    """
                    SELECT d.occurrence_id, d.schedule_id, d.revision, d.scheduled_at_utc,
                           d.local_scheduled_time, d.grace_deadline_utc, d.state,
                           d.medication_name_snapshot, d.dose_snapshot, d.instructions_snapshot, d.image_snapshot,
                           d.action_taken_at_utc, d.action_actor
                    FROM dose_occurrences d
                    JOIN medication_schedules ms ON d.schedule_id = ms.schedule_id
                    JOIN prescription_items pi ON ms.prescription_item_id = pi.prescription_item_id
                    JOIN prescriptions p ON pi.prescription_id = p.prescription_id
                    WHERE d.patient_id = ? AND d.local_scheduled_time LIKE ?
                      AND (p.status = 'ACTIVE' OR (p.status = 'SUPERSEDED' AND d.state = 'CONFIRMED'))
                      AND d.state != 'CANCELLED'
                    ORDER BY d.local_scheduled_time ASC
                    """,
                    (patient_id, f"{today_date}%"),
                )
                confirmed_cnt = sum(1 for d in doses if d["state"] == "CONFIRMED")
                pending_cnt = sum(1 for d in doses if d["state"] in ("PENDING", "REMINDING", "SNOOZED"))
                snapshot["sections"]["medication"] = {
                    "version": len(doses),
                    "today_date": today_date,
                    "total_count": len(doses),
                    "confirmed_count": confirmed_cnt,
                    "pending_count": pending_cnt,
                    "doses": doses,
                }

            if "requests" in sections or "vitals" in sections:
                pending_requests = conn.fetchall(
                    """
                    SELECT mr.*, ot.code as observation_type_code, ot.name as observation_type_name, ot.standard_unit,
                           u.full_name as requested_by_name
                    FROM measurement_requests mr
                    JOIN observation_types ot ON mr.observation_type_id = ot.observation_type_id
                    JOIN users u ON mr.requested_by_user_id = u.user_id
                    WHERE mr.patient_id = ? AND mr.status = 'PENDING'
                    ORDER BY mr.due_at_utc ASC
                    """,
                    (patient_id,),
                )
                snapshot["sections"]["measurement_requests"] = pending_requests

            if "rehab" in sections:
                sess = conn.fetchone(
                    """
                    SELECT rs.*, rp.exercise_name, rp.target_count as default_target, rp.local_reminder_time
                    FROM rehab_sessions rs
                    JOIN rehab_plans rp ON rs.plan_id = rp.plan_id
                    WHERE rs.patient_id = ? AND rs.local_date = ?
                    ORDER BY rs.session_id DESC LIMIT 1
                    """,
                    (patient_id, today_date),
                )
                if not sess:
                    plan = conn.fetchone("SELECT * FROM rehab_plans WHERE patient_id = ? AND is_active = 1 LIMIT 1", (patient_id,))
                    if plan:
                        new_sess_id = f"rs_{patient_id}_{today_date.replace('-', '')}"
                        conn.execute(
                            """
                            INSERT INTO rehab_sessions (
                                session_id, patient_id, plan_id, plan_version, local_date,
                                started_at, ended_at, target_count_snapshot, verified_count,
                                completion_source, status, algorithm_version, notes
                            ) VALUES (?, ?, ?, ?, ?, NULL, NULL, ?, 0, 'camera', 'pending', 'MediaPipe-Pose-FSM-v1.0', 'Tự động lên lịch theo kế hoạch 16:00')
                            ON CONFLICT(session_id) DO NOTHING
                            """,
                            (new_sess_id, patient_id, plan["plan_id"], plan["version"], today_date, plan["target_count"]),
                        )
                        conn.commit()
                        sess = conn.fetchone(
                            """
                            SELECT rs.*, rp.exercise_name, rp.target_count as default_target, rp.local_reminder_time
                            FROM rehab_sessions rs
                            JOIN rehab_plans rp ON rs.plan_id = rp.plan_id
                            WHERE rs.session_id = ?
                            """,
                            (new_sess_id,),
                        )

                if not sess:
                    rehab_info = {
                        "has_session": False,
                        "session_id": None,
                        "status": "pending",
                        "exercise_name": plan["exercise_name"] if plan else "arm_raise",
                        "target_count": plan["target_count"] if plan else 10,
                        "verified_count": 0,
                        "reminder_time": plan["local_reminder_time"] if plan else "16:00",
                    }
                else:
                    rehab_info = {
                        "has_session": True,
                        "session_id": sess["session_id"],
                        "exercise_name": sess["exercise_name"],
                        "target_count": sess["target_count_snapshot"],
                        "verified_count": sess["verified_count"],
                        "status": sess["status"],
                        "reminder_time": sess["local_reminder_time"],
                    }
                snapshot["sections"]["rehab"] = rehab_info

            if "vitals" in sections:
                latest_bp = conn.fetchone(
                    """
                    SELECT ho_sys.value_numeric as systolic, ho_dia.value_numeric as diastolic,
                           mg.measured_at, mg.device_label, mg.source_type, u.full_name as entered_by_name
                    FROM measurement_groups mg
                    JOIN health_observations ho_sys ON mg.group_id = ho_sys.group_id
                    JOIN health_observations ho_dia ON mg.group_id = ho_dia.group_id
                    JOIN observation_types ot_sys ON ho_sys.observation_type_id = ot_sys.observation_type_id AND ot_sys.code IN ('blood_pressure_systolic', 'bp_systolic')
                    JOIN observation_types ot_dia ON ho_dia.observation_type_id = ot_dia.observation_type_id AND ot_dia.code IN ('blood_pressure_diastolic', 'bp_diastolic')
                    LEFT JOIN users u ON mg.entered_by_user_id = u.user_id
                    WHERE mg.patient_id = ? AND mg.status = 'active'
                    ORDER BY mg.measured_at DESC LIMIT 1
                    """,
                    (patient_id,),
                )
                snapshot["sections"]["vitals"] = {
                    "latest_blood_pressure": latest_bp,
                }

            if "mood" in sections:
                latest_mood = conn.fetchone(
                    "SELECT * FROM mood_checkins WHERE patient_id = ? ORDER BY recorded_at DESC LIMIT 1",
                    (patient_id,),
                )
                snapshot["sections"]["mood"] = {
                    "latest_mood": latest_mood,
                }

            if "recommendations" in sections:
                recs = conn.fetchall(
                    """
                    SELECT cr.*, u.full_name as author_name,
                           (SELECT COUNT(*) FROM recommendation_receipts rr WHERE rr.recommendation_id = cr.recommendation_id) as is_read
                    FROM care_recommendations cr
                    JOIN clinicians cl ON cr.clinician_id = cl.clinician_id
                    JOIN users u ON cl.user_id = u.user_id
                    WHERE cr.patient_id = ? AND cr.status = 'ACTIVE'
                    ORDER BY cr.created_at DESC LIMIT 5
                    """,
                    (patient_id,),
                )
                snapshot["sections"]["recommendations"] = {
                    "recommendations": recs,
                }

            if "emergency" in sections:
                contacts = conn.fetchall(
                    "SELECT * FROM emergency_contacts WHERE patient_id = ? AND is_active = 1 ORDER BY priority ASC",
                    (patient_id,),
                )
                snapshot["sections"]["emergency"] = {
                    "contacts": contacts,
                }

            return 200, snapshot

    def _sync_events(self, patient_id: str, user_id: str, body: Dict[str, Any]) -> Tuple[int, Dict[str, Any]]:
        raw_events = body.get("events") if "events" in body else [body]
        now_iso = datetime.now(timezone.utc).isoformat()
        results: List[Dict[str, Any]] = []

        with self.db.connect() as conn:
            for item in raw_events:
                client_event_id = item.get("client_event_id") or item.get("event_id") or f"cl_{uuid.uuid4().hex[:12]}"
                event_type = item.get("event_type") or item.get("type") or "medication.confirm"
                occurred_at = item.get("occurred_at") or now_iso
                payload = item.get("payload") or item
                device_id = item.get("device_id") or "elder_desktop"

                existing = conn.fetchone("SELECT * FROM sync_inbox WHERE client_event_id = ?", (client_event_id,))
                if existing:
                    results.append({
                        "client_event_id": client_event_id,
                        "status": "duplicate_idempotent",
                        "canonical_event_id": existing["canonical_event_id"],
                        "server_recorded_at": existing["received_at"],
                    })
                    continue

                canonical_event_id = f"evt_{uuid.uuid4().hex[:16]}"

                if event_type in ("medication.confirm", "medication.dose_changed"):
                    occurrence_id = payload.get("occurrence_id")
                    action = payload.get("action", "self_reported_taken")
                    occ = conn.fetchone("SELECT * FROM dose_occurrences WHERE occurrence_id = ? AND patient_id = ?", (occurrence_id, patient_id))
                    if occ:
                        new_state = "CONFIRMED" if action == "self_reported_taken" else ("SNOOZED" if action == "snooze" else "REMINDING")
                        conn.execute(
                            """
                            UPDATE dose_occurrences
                            SET state = ?, action_taken_at_utc = ?, action_actor = ?, action_notes = ?
                            WHERE occurrence_id = ?
                            """,
                            (new_state, occurred_at, user_id, f"Sync Action: {action} (offline recorded at {occurred_at})", occurrence_id),
                        )
                        conn.execute(
                            """
                            INSERT INTO dose_events (event_id, occurrence_id, event_type, actor_id, actor_role, timestamp_utc, idempotency_key, metadata)
                            VALUES (?, ?, ?, ?, 'patient', ?, ?, ?)
                            """,
                            (canonical_event_id, occurrence_id, action, user_id, occurred_at, client_event_id, json.dumps(payload)),
                        )
                        insert_outbox_event(
                            conn=conn,
                            patient_id=patient_id,
                            event_type="medication.dose_changed",
                            aggregate_type="dose_occurrence",
                            aggregate_id=occurrence_id,
                            aggregate_version=1,
                            occurred_at=now_iso,
                            actor_id=user_id,
                            source="elder_app",
                            payload_minimal={"occurrence_id": occurrence_id, "state": new_state, "action": action, "client_occurred_at": occurred_at},
                            event_id=canonical_event_id,
                        )

                elif event_type == "rehab.rep":
                    session_id = payload.get("session_id")
                    rep_number = int(payload.get("rep_number", 1))
                    score = float(payload.get("score", 95.0))
                    exercise_id = payload.get("exercise_id", "arm_raise")
                    sess = conn.fetchone("SELECT * FROM rehab_sessions WHERE session_id = ? AND patient_id = ?", (session_id, patient_id))
                    if sess:
                        conn.execute(
                            """
                            INSERT INTO rehab_events (event_id, session_id, patient_id, exercise_id, rep_number, score, timestamp_utc, idempotency_key, metadata)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                            """,
                            (canonical_event_id, session_id, patient_id, exercise_id, rep_number, score, occurred_at, client_event_id, json.dumps(payload)),
                        )
                        new_count = sess["verified_count"] + 1
                        new_status = sess["status"]
                        ended_at = sess["ended_at"]
                        if new_count >= sess["target_count_snapshot"]:
                            new_status = "camera_verified_completed"
                            ended_at = now_iso
                        elif new_status in ("pending", "reminded"):
                            new_status = "in_progress"

                        conn.execute(
                            """
                            UPDATE rehab_sessions
                            SET verified_count = ?, status = ?, ended_at = ?
                            WHERE session_id = ?
                            """,
                            (new_count, new_status, ended_at, session_id),
                        )
                        insert_outbox_event(
                            conn=conn,
                            patient_id=patient_id,
                            event_type="rehab.session_updated",
                            aggregate_type="rehab_session",
                            aggregate_id=session_id,
                            aggregate_version=new_count,
                            occurred_at=now_iso,
                            actor_id=None,
                            source="camera_ai",
                            payload_minimal={"session_id": session_id, "verified_count": new_count, "target_count": sess["target_count_snapshot"], "status": new_status},
                            event_id=canonical_event_id,
                        )

                elif event_type == "mood.checkin":
                    rating = int(payload.get("rating", 3))
                    note = payload.get("note", "")
                    conn.execute(
                        """
                        INSERT INTO mood_checkins (checkin_id, patient_id, self_rating, note, recorded_at, recorded_by)
                        VALUES (?, ?, ?, ?, ?, ?)
                        """,
                        (canonical_event_id, patient_id, rating, note, occurred_at, user_id),
                    )
                    insert_outbox_event(
                        conn=conn,
                        patient_id=patient_id,
                        event_type="mood.checkin_created",
                        aggregate_type="mood_checkin",
                        aggregate_id=canonical_event_id,
                        aggregate_version=1,
                        occurred_at=now_iso,
                        actor_id=user_id,
                        source="elder_app",
                        payload_minimal={"checkin_id": canonical_event_id, "self_rating": rating},
                        event_id=canonical_event_id,
                    )

                conn.execute(
                    """
                    INSERT INTO sync_inbox (client_event_id, patient_id, source_device_id, received_at, status, canonical_event_id, error_code)
                    VALUES (?, ?, ?, ?, 'PROCESSED', ?, NULL)
                    """,
                    (client_event_id, patient_id, device_id, now_iso, canonical_event_id),
                )

                results.append({
                    "client_event_id": client_event_id,
                    "status": "processed",
                    "canonical_event_id": canonical_event_id,
                    "server_recorded_at": now_iso,
                    "occurred_at": occurred_at,
                })

            conn.commit()
            self._trigger_relay()

        return 200, {"results": results, "count": len(results)}

    def _get_changes(self, patient_id: str, query_params: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
        since = query_params.get("since", "")
        with self.db.connect() as conn:
            if since:
                rows = conn.fetchall(
                    """
                    SELECT * FROM outbox_events
                    WHERE patient_id = ? AND occurred_at > ?
                    ORDER BY occurred_at ASC LIMIT 100
                    """,
                    (patient_id, since),
                )
            else:
                rows = conn.fetchall(
                    """
                    SELECT * FROM outbox_events
                    WHERE patient_id = ?
                    ORDER BY occurred_at DESC LIMIT 50
                    """,
                    (patient_id,),
                )
            now_iso = datetime.now(timezone.utc).isoformat()
            return 200, {
                "patient_id": patient_id,
                "cursor": now_iso,
                "events": [
                    {
                        "event_id": r["event_id"],
                        "type": r["event_type"],
                        "aggregate_type": r["aggregate_type"],
                        "aggregate_id": r["aggregate_id"],
                        "version": r["aggregate_version"],
                        "occurred_at": r["occurred_at"],
                        "source": r["source"],
                        "payload": json.loads(r["payload_minimal"]) if r["payload_minimal"] else {},
                    }
                    for r in rows
                ],
            }

    # =========================================================================
    # SYSTEM ADMINISTRATOR CONTROLLERS (/api/admin/...)
    # =========================================================================
    def _handle_admin_route(
        self, method: str, path: str, query_params: Dict[str, str], body: Dict[str, Any], admin_user_id: str, ip: str
    ) -> Tuple[int, Dict[str, Any]]:
        parts = path.strip("/").split("/")
        resource = parts[2] if len(parts) >= 3 else ""

        if resource == "stats" and method == "GET":
            return self._admin_get_stats()

        elif resource == "sync-accounts" and method in ("GET", "POST"):
            return self._admin_sync_all_accounts()

        elif resource == "doctors":
            if len(parts) == 3:
                if method == "GET":
                    return self._admin_get_doctors(query_params)
                elif method == "POST":
                    return self._admin_create_doctor(body, admin_user_id)
            elif len(parts) == 4:
                doctor_id = parts[3]
                if method in ("PUT", "POST"):
                    return self._admin_update_doctor(doctor_id, body)
                elif method == "DELETE":
                    return self._admin_delete_doctor(doctor_id)
                elif method == "GET":
                    return self._admin_get_doctor_detail(doctor_id)
            elif len(parts) == 5:
                doctor_id = parts[3]
                sub_action = parts[4]
                if sub_action == "reset-password" and method in ("POST", "PUT"):
                    return self._admin_reset_doctor_password(doctor_id, body)
                elif sub_action == "toggle-status" and method in ("POST", "PUT"):
                    return self._admin_toggle_doctor_status(doctor_id, body)

        elif resource == "caregivers":
            if len(parts) == 3:
                if method == "GET":
                    return self._admin_get_caregivers(query_params)
                elif method == "POST":
                    return self._admin_create_caregiver(body, admin_user_id)
            elif len(parts) == 4:
                caregiver_id = parts[3]
                if method in ("PUT", "POST"):
                    return self._admin_update_caregiver(caregiver_id, body)
                elif method == "DELETE":
                    return self._admin_delete_caregiver(caregiver_id)
                elif method == "GET":
                    return self._admin_get_caregiver_detail(caregiver_id)
            elif len(parts) == 5:
                caregiver_id = parts[3]
                sub_action = parts[4]
                if sub_action == "reset-password" and method in ("POST", "PUT"):
                    return self._admin_reset_caregiver_password(caregiver_id, body)
                elif sub_action == "toggle-status" and method in ("POST", "PUT"):
                    return self._admin_toggle_caregiver_status(caregiver_id, body)

        elif resource == "patients":
            if len(parts) == 3:
                if method == "GET":
                    return self._admin_get_patients(query_params)
                elif method == "POST":
                    return self._admin_create_patient(body, admin_user_id)
            elif len(parts) == 4:
                patient_id = parts[3]
                if method in ("PUT", "POST"):
                    return self._admin_update_patient(patient_id, body)
                elif method == "DELETE":
                    return self._admin_delete_patient(patient_id)
                elif method == "GET":
                    return self._admin_get_patient_detail(patient_id)
            elif len(parts) == 5:
                patient_id = parts[3]
                sub_action = parts[4]
                if sub_action == "toggle-status" and method in ("POST", "PUT"):
                    return self._admin_toggle_patient_status(patient_id, body)

        elif resource == "patient-caregivers":
            if len(parts) == 3:
                if method == "GET":
                    return self._admin_get_patient_caregivers(query_params)
                elif method == "POST":
                    return self._admin_link_patient_caregiver(body, admin_user_id)
                elif method == "DELETE":
                    return self._admin_unlink_patient_caregiver(body)
            elif len(parts) == 4 and method == "DELETE":
                return self._admin_remove_patient_caregiver_by_id(parts[3])

        elif resource == "care-team":
            if len(parts) == 3 and method == "POST":
                return self._admin_assign_care_team(body, admin_user_id)
            elif len(parts) == 4 and method == "DELETE":
                return self._admin_remove_care_team(parts[3])

        elif resource == "users":
            if len(parts) == 4 and method == "DELETE":
                return self._admin_delete_user(parts[3])
            elif len(parts) == 3 and method == "GET":
                return self._admin_get_all_users()

        return 404, {"error": f"Admin endpoint không tồn tại: {method} {path}"}

    def _admin_get_doctors(self, query_params: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
        with self.db.connect() as conn:
            docs = conn.fetchall(
                """
                SELECT c.*, u.email, u.phone, u.full_name, u.is_active, u.created_at
                FROM clinicians c
                JOIN users u ON c.user_id = u.user_id
                ORDER BY u.created_at DESC
                """
            )
            doctor_list = []
            for d in docs:
                doc_dict = dict(d)
                teams = conn.fetchall(
                    """
                    SELECT ct.care_team_id, ct.patient_id, ct.role_description, ct.is_active, p.full_name as patient_name
                    FROM care_team ct
                    LEFT JOIN patients p ON ct.patient_id = p.patient_id
                    WHERE ct.clinician_id = ?
                    ORDER BY ct.is_active DESC, p.full_name ASC
                    """,
                    (d["clinician_id"],),
                )
                doc_dict["care_teams"] = [dict(t) for t in teams]
                doc_dict["patient_count"] = len([t for t in teams if t["is_active"] == 1])
                doctor_list.append(doc_dict)

            total_docs = len(doctor_list)
            active_docs = len([d for d in doctor_list if d.get("is_active") == 1])
            sim_docs = len([d for d in doctor_list if d.get("is_simulation") == 1])
            total_patients = conn.fetchone("SELECT COUNT(*) as cnt FROM patients")["cnt"]
            active_patients = conn.fetchone("SELECT COUNT(*) as cnt FROM patients WHERE status = 'ACTIVE'")["cnt"]
            total_caregivers = conn.fetchone("SELECT COUNT(*) as cnt FROM caregivers")["cnt"]
            active_caregivers = conn.fetchone("SELECT COUNT(*) as cnt FROM caregivers c JOIN users u ON c.user_id = u.user_id WHERE u.is_active = 1")["cnt"]
            total_care_teams = conn.fetchone("SELECT COUNT(*) as cnt FROM care_team WHERE is_active = 1")["cnt"]
            total_patient_caregivers = conn.fetchone("SELECT COUNT(*) as cnt FROM patient_caregivers WHERE is_active = 1")["cnt"]

            return 200, {
                "doctors": doctor_list,
                "stats": {
                    "total_doctors": total_docs,
                    "active_doctors": active_docs,
                    "simulation_doctors": sim_docs,
                    "total_patients": total_patients,
                    "active_patients": active_patients,
                    "total_caregivers": total_caregivers,
                    "active_caregivers": active_caregivers,
                    "total_care_teams": total_care_teams,
                    "total_patient_caregivers": total_patient_caregivers,
                },
            }

    def _admin_get_stats(self) -> Tuple[int, Dict[str, Any]]:
        with self.db.connect() as conn:
            total_docs = conn.fetchone("SELECT COUNT(*) as cnt FROM clinicians")["cnt"]
            active_docs = conn.fetchone("SELECT COUNT(*) as cnt FROM clinicians c JOIN users u ON c.user_id = u.user_id WHERE u.is_active = 1")["cnt"]
            sim_docs = conn.fetchone("SELECT COUNT(*) as cnt FROM clinicians WHERE is_simulation = 1")["cnt"]
            total_patients = conn.fetchone("SELECT COUNT(*) as cnt FROM patients")["cnt"]
            active_patients = conn.fetchone("SELECT COUNT(*) as cnt FROM patients WHERE status = 'ACTIVE'")["cnt"]
            total_caregivers = conn.fetchone("SELECT COUNT(*) as cnt FROM caregivers")["cnt"]
            active_caregivers = conn.fetchone("SELECT COUNT(*) as cnt FROM caregivers c JOIN users u ON c.user_id = u.user_id WHERE u.is_active = 1")["cnt"]
            total_care_teams = conn.fetchone("SELECT COUNT(*) as cnt FROM care_team WHERE is_active = 1")["cnt"]
            total_patient_caregivers = conn.fetchone("SELECT COUNT(*) as cnt FROM patient_caregivers WHERE is_active = 1")["cnt"]

            return 200, {
                "stats": {
                    "total_doctors": total_docs,
                    "active_doctors": active_docs,
                    "simulation_doctors": sim_docs,
                    "total_patients": total_patients,
                    "active_patients": active_patients,
                    "total_caregivers": total_caregivers,
                    "active_caregivers": active_caregivers,
                    "total_care_teams": total_care_teams,
                    "total_patient_caregivers": total_patient_caregivers,
                }
            }

    def _admin_sync_all_accounts(self) -> Tuple[int, Dict[str, Any]]:
        """
        Force full bidirectional synchronization of all user accounts across SQLite and JSON profiles:
        1. Reads data/users/*.json and shared/data/patients.json and syncs into unified_care.sqlite3.
        2. Exports back unified patients to shared/data/patients.json.
        3. Ensures every caregiver user has row in caregivers table.
        4. Broadcasts system sync event via outbox and SSE relay.
        """
        try:
            from shared.services.patient_sync_service import sync_all_patient_profiles
            synced_count = sync_all_patient_profiles(self.db)
        except Exception as e:
            logger.error("Error in sync_all_patient_profiles: %s", e)
            synced_count = 0

        now_iso = datetime.now(timezone.utc).isoformat()
        with self.db.connect() as conn:
            # Check for any caregiver users missing caregivers record
            cg_users = conn.fetchall(
                """
                SELECT u.user_id, u.full_name, u.phone FROM users u
                LEFT JOIN caregivers c ON u.user_id = c.user_id
                WHERE u.role = 'caregiver' AND c.caregiver_id IS NULL
                """
            )
            for cu in cg_users:
                new_cg_id = f"cg_{uuid.uuid4().hex[:8]}"
                conn.execute(
                    "INSERT INTO caregivers (caregiver_id, user_id, email_verified, phone_verified, relationship_desc) VALUES (?, ?, 1, 1, 'Người thân')",
                    (new_cg_id, cu["user_id"]),
                )

            # Insert outbox event for real-time notification if a patient exists
            sample_pt = conn.fetchone("SELECT patient_id FROM patients LIMIT 1")
            if sample_pt:
                insert_outbox_event(
                    conn=conn,
                    patient_id=sample_pt["patient_id"],
                    event_type="system.accounts_synced",
                    aggregate_type="system",
                    aggregate_id="admin_sync",
                    aggregate_version=1,
                    occurred_at=now_iso,
                    actor_id="admin",
                    source="admin_portal",
                    payload_minimal={"synced_patients": synced_count},
                )

            # Direct broadcast to active admin clients
            from features.web_portal.sse_hub import sse_hub
            for c in list(sse_hub._clients.values()):
                if c.active and c.role == "admin":
                    c.send_event({
                        "type": "system.accounts_synced",
                        "patient_id": sample_pt["patient_id"] if sample_pt else "",
                        "occurred_at": now_iso,
                        "payload": {"synced_patients": synced_count},
                    })
            conn.commit()

        self._trigger_relay()

        _, stats_res = self._admin_get_stats()
        return 200, {
            "status": "synced",
            "message": f"Đã đồng bộ thành công toàn bộ tài khoản và {synced_count} hồ sơ trong cơ sở dữ liệu.",
            "synced_patients": synced_count,
            "stats": stats_res.get("stats", {}),
        }

    def _admin_get_doctor_detail(self, doctor_id: str) -> Tuple[int, Dict[str, Any]]:
        with self.db.connect() as conn:
            doc = conn.fetchone(
                """
                SELECT c.*, u.email, u.phone, u.full_name, u.is_active, u.created_at
                FROM clinicians c
                JOIN users u ON c.user_id = u.user_id
                WHERE c.clinician_id = ? OR c.user_id = ?
                """,
                (doctor_id, doctor_id),
            )
            if not doc:
                return 404, {"error": "Không tìm thấy bác sĩ"}

            doc_dict = dict(doc)
            teams = conn.fetchall(
                """
                SELECT ct.*, p.full_name as patient_name
                FROM care_team ct
                LEFT JOIN patients p ON ct.patient_id = p.patient_id
                WHERE ct.clinician_id = ?
                ORDER BY ct.is_active DESC
                """,
                (doc["clinician_id"],),
            )
            doc_dict["care_teams"] = [dict(t) for t in teams]
            return 200, {"doctor": doc_dict}

    def _admin_create_doctor(self, body: Dict[str, Any], admin_user_id: str) -> Tuple[int, Dict[str, Any]]:
        full_name = (body.get("full_name") or "").strip()
        email = (body.get("email") or "").strip().lower()
        if not full_name or not email:
            return 400, {"error": "Vui lòng nhập đầy đủ Họ và tên và Email của Bác sĩ"}

        password = body.get("password") or ""
        if not password:
            return 400, {"error": "Vui lòng nhập mật khẩu"}
        phone = (body.get("phone") or "").strip()
        workplace = (body.get("workplace") or "Bệnh viện Lão khoa Trung Ương").strip()
        specialty = (body.get("specialty") or "Lão khoa & Tim mạch").strip()
        license_number = (body.get("license_number") or f"CCHN-{uuid.uuid4().hex[:6].upper()}").strip()
        is_simulation = int(body.get("is_simulation", 0))

        now_iso = datetime.now(timezone.utc).isoformat()
        future_iso = (datetime.now(timezone.utc) + timedelta(days=3650)).isoformat()
        user_id = f"user_doc_{uuid.uuid4().hex[:8]}"
        clinician_id = f"clinician_{uuid.uuid4().hex[:8]}"
        pw_hash = hash_password(password)

        with self.db.connect() as conn:
            existing = conn.fetchone("SELECT user_id FROM users WHERE LOWER(email) = ?", (email,))
            if existing:
                return 400, {"error": f"Email '{email}' đã tồn tại trong hệ thống"}

            conn.execute(
                """
                INSERT INTO users (user_id, email, phone, full_name, password_hash, role, is_active, created_at)
                VALUES (?, ?, ?, ?, ?, 'clinician', 1, ?)
                """,
                (user_id, email, phone, full_name, pw_hash, now_iso),
            )
            conn.execute(
                """
                INSERT INTO clinicians (clinician_id, user_id, workplace, specialty, license_number, is_verified, is_simulation)
                VALUES (?, ?, ?, ?, ?, 1, ?)
                """,
                (clinician_id, user_id, workplace, specialty, license_number, is_simulation),
            )

            # Assign initial patients if provided
            patient_ids = body.get("patient_ids") or []
            for pid in patient_ids:
                ct_id = f"ct_{uuid.uuid4().hex[:8]}"
                conn.execute(
                    """
                    INSERT INTO care_team (care_team_id, patient_id, clinician_id, role_description, valid_from, valid_to, consent_status, is_active)
                    VALUES (?, ?, ?, 'Bác sĩ điều trị', ?, ?, 'GRANTED', 1)
                    """,
                    (ct_id, pid, clinician_id, now_iso, future_iso),
                )

            conn.commit()

        return 201, {
            "clinician_id": clinician_id,
            "user_id": user_id,
            "email": email,
            "full_name": full_name,
            "specialty": specialty,
            "workplace": workplace,
            "is_simulation": is_simulation,
            "message": "Đã tạo tài khoản Bác sĩ thành công",
        }

    def _admin_update_doctor(self, doctor_id: str, body: Dict[str, Any]) -> Tuple[int, Dict[str, Any]]:
        with self.db.connect() as conn:
            cl = conn.fetchone("SELECT * FROM clinicians WHERE clinician_id = ? OR user_id = ?", (doctor_id, doctor_id))
            if not cl:
                return 404, {"error": "Không tìm thấy thông tin bác sĩ"}

            user_id = cl["user_id"]
            clinician_id = cl["clinician_id"]

            full_name = body.get("full_name")
            email = body.get("email")
            phone = body.get("phone")
            workplace = body.get("workplace")
            specialty = body.get("specialty")
            license_number = body.get("license_number")
            is_simulation = body.get("is_simulation")
            is_active = body.get("is_active")

            if email:
                email = email.strip().lower()
                dup = conn.fetchone("SELECT user_id FROM users WHERE LOWER(email) = ? AND user_id != ?", (email, user_id))
                if dup:
                    return 400, {"error": f"Email '{email}' đã được sử dụng bởi tài khoản khác"}

            # Update users
            u_updates = []
            u_params = []
            if full_name is not None:
                u_updates.append("full_name = ?")
                u_params.append(full_name.strip())
            if email is not None:
                u_updates.append("email = ?")
                u_params.append(email)
            if phone is not None:
                u_updates.append("phone = ?")
                u_params.append(phone.strip())
            if is_active is not None:
                u_updates.append("is_active = ?")
                u_params.append(int(is_active))

            if u_updates:
                u_params.append(user_id)
                conn.execute(f"UPDATE users SET {', '.join(u_updates)} WHERE user_id = ?", u_params)

            # Update clinicians
            c_updates = []
            c_params = []
            if workplace is not None:
                c_updates.append("workplace = ?")
                c_params.append(workplace.strip())
            if specialty is not None:
                c_updates.append("specialty = ?")
                c_params.append(specialty.strip())
            if license_number is not None:
                c_updates.append("license_number = ?")
                c_params.append(license_number.strip())
            if is_simulation is not None:
                c_updates.append("is_simulation = ?")
                c_params.append(int(is_simulation))

            if c_updates:
                c_params.append(clinician_id)
                conn.execute(f"UPDATE clinicians SET {', '.join(c_updates)} WHERE clinician_id = ?", c_params)

            conn.commit()

        return 200, {"status": "updated", "clinician_id": clinician_id, "message": "Đã cập nhật thông tin bác sĩ"}

    def _admin_reset_doctor_password(self, doctor_id: str, body: Dict[str, Any]) -> Tuple[int, Dict[str, Any]]:
        new_pw = (body.get("new_password") or body.get("password") or "").strip()
        if not new_pw:
            return 400, {"error": "Vui lòng nhập mật khẩu mới"}

        with self.db.connect() as conn:
            cl = conn.fetchone("SELECT user_id FROM clinicians WHERE clinician_id = ? OR user_id = ?", (doctor_id, doctor_id))
            if not cl:
                return 404, {"error": "Không tìm thấy bác sĩ"}

            conn.execute("UPDATE users SET password_hash = ? WHERE user_id = ?", (hash_password(new_pw), cl["user_id"]))
            conn.commit()

        return 200, {"status": "password_reset", "message": "Đã đổi mật khẩu bác sĩ thành công"}

    def _admin_toggle_doctor_status(self, doctor_id: str, body: Dict[str, Any]) -> Tuple[int, Dict[str, Any]]:
        with self.db.connect() as conn:
            cl = conn.fetchone("SELECT user_id FROM clinicians WHERE clinician_id = ? OR user_id = ?", (doctor_id, doctor_id))
            if not cl:
                return 404, {"error": "Không tìm thấy bác sĩ"}

            u = conn.fetchone("SELECT is_active FROM users WHERE user_id = ?", (cl["user_id"],))
            new_status = 1 if u["is_active"] == 0 else 0
            if "is_active" in body:
                new_status = int(body["is_active"])

            conn.execute("UPDATE users SET is_active = ? WHERE user_id = ?", (new_status, cl["user_id"]))
            conn.commit()

        return 200, {"status": "status_updated", "is_active": new_status, "message": "Đã cập nhật trạng thái tài khoản"}

    def _admin_delete_doctor(self, doctor_id: str) -> Tuple[int, Dict[str, Any]]:
        with self.db.connect() as conn:
            cl = conn.fetchone("""
                SELECT c.clinician_id, c.user_id, u.email
                FROM clinicians c
                JOIN users u ON c.user_id = u.user_id
                WHERE c.clinician_id = ? OR c.user_id = ? OR LOWER(u.email) = ?
            """, (doctor_id, doctor_id, doctor_id.lower()))
            if not cl:
                u = conn.fetchone("SELECT user_id FROM users WHERE (user_id = ? OR LOWER(email) = ?) AND role = 'clinician'", (doctor_id, doctor_id.lower()))
                if not u:
                    return 404, {"error": "Không tìm thấy bác sĩ"}
                c_id = doctor_id
                u_id = u["user_id"]
            else:
                c_id = cl["clinician_id"]
                u_id = cl["user_id"]

            conn.execute("PRAGMA foreign_keys = OFF;")
            try:
                conn.execute("DELETE FROM care_team WHERE clinician_id = ?", (c_id,))
                conn.execute("DELETE FROM clinician_reviews WHERE clinician_id = ?", (c_id,))
                conn.execute("DELETE FROM clinical_messages WHERE clinician_id = ?", (c_id,))
                conn.execute("DELETE FROM monitoring_plans WHERE clinician_id = ?", (c_id,))
                conn.execute("DELETE FROM care_recommendations WHERE clinician_id = ?", (c_id,))
                conn.execute("UPDATE prescriptions SET prescriber_clinician_id = NULL WHERE prescriber_clinician_id = ?", (c_id,))
                conn.execute("DELETE FROM message_receipts WHERE user_id = ?", (u_id,))
                conn.execute("DELETE FROM recommendation_receipts WHERE user_id = ?", (u_id,))
                conn.execute("DELETE FROM audit_events WHERE user_id = ?", (u_id,))
                conn.execute("DELETE FROM clinicians WHERE clinician_id = ? OR user_id = ?", (c_id, u_id))
                conn.execute("DELETE FROM users WHERE user_id = ?", (u_id,))
                conn.commit()
            finally:
                conn.execute("PRAGMA foreign_keys = ON;")

        return 200, {"status": "deleted", "message": "Đã xóa tài khoản bác sĩ khỏi hệ thống"}

    # -------------------------------------------------------------
    # PATIENTS CONTROLLERS
    # -------------------------------------------------------------
    def _admin_get_patients(self, query_params: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
        with self.db.connect() as conn:
            rows = conn.fetchall(
                """
                SELECT p.patient_id, p.user_id, p.full_name, p.date_of_birth, p.gender, p.status, p.notes,
                       u.email, u.phone, u.is_active as user_is_active, u.created_at
                FROM patients p
                LEFT JOIN users u ON p.user_id = u.user_id
                ORDER BY p.full_name ASC
                """
            )
            patients = []
            for r in rows:
                p_dict = dict(r)
                # fetch linked caregivers
                cg_rows = conn.fetchall(
                    """
                    SELECT pc.patient_caregiver_id, pc.caregiver_id, pc.relationship_type, pc.is_primary, pc.is_active,
                           u.full_name as caregiver_name, u.email as caregiver_email, u.phone as caregiver_phone
                    FROM patient_caregivers pc
                    JOIN caregivers c ON pc.caregiver_id = c.caregiver_id
                    JOIN users u ON c.user_id = u.user_id
                    WHERE pc.patient_id = ?
                    ORDER BY pc.is_primary DESC, u.full_name ASC
                    """,
                    (r["patient_id"],),
                )
                p_dict["caregivers"] = [dict(c) for c in cg_rows]

                # fetch linked care team doctors
                doc_rows = conn.fetchall(
                    """
                    SELECT ct.care_team_id, ct.clinician_id, ct.role_description, ct.is_active,
                           u.full_name as doctor_name, u.email as doctor_email, c.specialty, c.workplace
                    FROM care_team ct
                    JOIN clinicians c ON ct.clinician_id = c.clinician_id
                    JOIN users u ON c.user_id = u.user_id
                    WHERE ct.patient_id = ?
                    ORDER BY ct.is_active DESC, u.full_name ASC
                    """,
                    (r["patient_id"],),
                )
                p_dict["care_teams"] = [dict(d) for d in doc_rows]
                patients.append(p_dict)

            return 200, {"patients": patients}

    def _admin_get_patient_detail(self, patient_id: str) -> Tuple[int, Dict[str, Any]]:
        with self.db.connect() as conn:
            p = conn.fetchone(
                """
                SELECT p.patient_id, p.user_id, p.full_name, p.date_of_birth, p.gender, p.status, p.notes,
                       u.email, u.phone, u.is_active as user_is_active, u.created_at
                FROM patients p
                LEFT JOIN users u ON p.user_id = u.user_id
                WHERE p.patient_id = ? OR p.user_id = ?
                """,
                (patient_id, patient_id),
            )
            if not p:
                return 404, {"error": "Không tìm thấy hồ sơ người bệnh"}

            p_dict = dict(p)
            cg_rows = conn.fetchall(
                """
                SELECT pc.patient_caregiver_id, pc.caregiver_id, pc.relationship_type, pc.is_primary, pc.is_active,
                       u.full_name as caregiver_name, u.email as caregiver_email, u.phone as caregiver_phone
                FROM patient_caregivers pc
                JOIN caregivers c ON pc.caregiver_id = c.caregiver_id
                JOIN users u ON c.user_id = u.user_id
                WHERE pc.patient_id = ?
                ORDER BY pc.is_primary DESC, u.full_name ASC
                """,
                (p["patient_id"],),
            )
            p_dict["caregivers"] = [dict(c) for c in cg_rows]

            doc_rows = conn.fetchall(
                """
                SELECT ct.care_team_id, ct.clinician_id, ct.role_description, ct.is_active,
                       u.full_name as doctor_name, u.email as doctor_email, c.specialty, c.workplace
                FROM care_team ct
                JOIN clinicians c ON ct.clinician_id = c.clinician_id
                JOIN users u ON c.user_id = u.user_id
                WHERE ct.patient_id = ?
                ORDER BY ct.is_active DESC, u.full_name ASC
                """,
                (p["patient_id"],),
            )
            p_dict["care_teams"] = [dict(d) for d in doc_rows]
            return 200, {"patient": p_dict}

    def _admin_create_patient(self, body: Dict[str, Any], admin_user_id: str) -> Tuple[int, Dict[str, Any]]:
        full_name = (body.get("full_name") or "").strip()
        if not full_name:
            return 400, {"error": "Vui lòng nhập Họ và tên người bệnh"}

        pid = (body.get("patient_id") or f"patient_{uuid.uuid4().hex[:6]}").strip()
        gender = body.get("gender") or "Nam"
        dob = body.get("date_of_birth") or "1950-01-01"
        notes = body.get("notes") or ""
        user_id = f"user_pat_{uuid.uuid4().hex[:6]}"
        now_iso = datetime.now(timezone.utc).isoformat()

        with self.db.connect() as conn:
            if conn.fetchone("SELECT patient_id FROM patients WHERE patient_id = ?", (pid,)):
                return 400, {"error": f"Mã bệnh nhân '{pid}' đã tồn tại trong hệ thống"}

            conn.execute(
                "INSERT INTO users (user_id, email, phone, full_name, role, is_active, created_at) VALUES (?, ?, '', ?, 'patient', 1, ?)",
                (user_id, f"{pid}@unifiedcare.local", full_name, now_iso),
            )
            conn.execute(
                "INSERT INTO patients (patient_id, user_id, full_name, date_of_birth, gender, timezone, status, notes) VALUES (?, ?, ?, ?, ?, 'Asia/Ho_Chi_Minh', 'ACTIVE', ?)",
                (pid, user_id, full_name, dob, gender, notes),
            )
            conn.commit()

        # Sync to UserService for desktop compatibility (skip for in-memory test DBs)
        if str(getattr(self.db, "db_path", "")) != ":memory:":
            try:
                from shared.services.user_service import UserService
                age = 70
                if dob and len(dob) >= 4 and dob[:4].isdigit():
                    age = max(1, datetime.now().year - int(dob[:4]))
                UserService().save_user_profile(user_id=pid, name=full_name, age=age, gender=gender, notes=notes)
            except Exception as e:
                logger.warning("Could not sync patient to UserService: %s", e)

        return 201, {"patient_id": pid, "full_name": full_name, "message": "Đã tạo hồ sơ người bệnh thành công"}

    def _admin_update_patient(self, patient_id: str, body: Dict[str, Any]) -> Tuple[int, Dict[str, Any]]:
        with self.db.connect() as conn:
            p = conn.fetchone("SELECT * FROM patients WHERE patient_id = ? OR user_id = ?", (patient_id, patient_id))
            if not p:
                return 404, {"error": "Không tìm thấy hồ sơ người bệnh"}

            pid = p["patient_id"]
            uid = p["user_id"]

            full_name = body.get("full_name")
            date_of_birth = body.get("date_of_birth")
            gender = body.get("gender")
            notes = body.get("notes")
            status = body.get("status")

            p_updates = []
            p_params = []
            if full_name is not None:
                p_updates.append("full_name = ?")
                p_params.append(full_name.strip())
            if date_of_birth is not None:
                p_updates.append("date_of_birth = ?")
                p_params.append(date_of_birth.strip())
            if gender is not None:
                p_updates.append("gender = ?")
                p_params.append(gender.strip())
            if notes is not None:
                p_updates.append("notes = ?")
                p_params.append(notes.strip())
            if status is not None:
                p_updates.append("status = ?")
                p_params.append(status.strip().upper())

            if p_updates:
                p_params.append(pid)
                conn.execute(f"UPDATE patients SET {', '.join(p_updates)} WHERE patient_id = ?", p_params)

            if full_name and uid:
                conn.execute("UPDATE users SET full_name = ? WHERE user_id = ?", (full_name.strip(), uid))

            conn.commit()

        # Update local user profile (skip for in-memory test DBs)
        if str(getattr(self.db, "db_path", "")) != ":memory:":
            try:
                from shared.services.user_service import UserService
                us = UserService()
                prof = us.get_user_profile(pid) or {}
                us.save_user_profile(
                    user_id=pid,
                    name=full_name.strip() if full_name else prof.get("name", "Người dùng"),
                    age=prof.get("age", 70),
                    gender=gender.strip() if gender else prof.get("gender", "Nam"),
                    notes=notes.strip() if notes is not None else prof.get("notes", ""),
                )
            except Exception as e:
                logger.warning("Could not update patient in UserService: %s", e)

        return 200, {"status": "updated", "patient_id": pid, "message": "Đã cập nhật hồ sơ người bệnh"}

    def _admin_toggle_patient_status(self, patient_id: str, body: Dict[str, Any]) -> Tuple[int, Dict[str, Any]]:
        with self.db.connect() as conn:
            p = conn.fetchone("SELECT * FROM patients WHERE patient_id = ? OR user_id = ?", (patient_id, patient_id))
            if not p:
                return 404, {"error": "Không tìm thấy hồ sơ người bệnh"}

            pid = p["patient_id"]
            uid = p["user_id"]

            new_status = "INACTIVE" if p["status"] == "ACTIVE" else "ACTIVE"
            if "status" in body:
                new_status = str(body["status"]).strip().upper()

            conn.execute("UPDATE patients SET status = ? WHERE patient_id = ?", (new_status, pid))
            if uid:
                is_active = 1 if new_status == "ACTIVE" else 0
                conn.execute("UPDATE users SET is_active = ? WHERE user_id = ?", (is_active, uid))
            conn.commit()

        return 200, {"status": "status_updated", "patient_status": new_status, "message": "Đã đổi trạng thái người bệnh"}

    def _cleanup_patient_files(self, all_pids: List[str]) -> None:
        """Purge patient files from shared/data/patients.json, data/users/*.json, registered_faces, and state."""
        clean_pids = set([str(x).strip() for x in all_pids if str(x).strip()])
        if not clean_pids:
            return

        # 1. Update shared/data/patients.json
        patients_json_path = Path("shared/data/patients.json")
        if patients_json_path.exists():
            try:
                with open(patients_json_path, "r", encoding="utf-8") as f:
                    pdict = json.load(f)
                keys_to_del = [
                    k for k, v in pdict.items()
                    if str(k) in clean_pids
                    or str(v.get("patient_id") or "") in clean_pids
                    or str(v.get("user_id") or "") in clean_pids
                ]
                for k in keys_to_del:
                    del pdict[k]
                with open(patients_json_path, "w", encoding="utf-8") as f:
                    json.dump(pdict, f, ensure_ascii=False, indent=2)
            except Exception as e:
                logger.warning("Error cleaning patient from patients.json: %s", e)

        # 2. Delete data/users/*.json
        users_dir = Path("data/users")
        if users_dir.exists():
            for ppath in list(users_dir.glob("*.json")):
                try:
                    with open(ppath, "r", encoding="utf-8") as f:
                        udata = json.load(f)
                    uid = str(udata.get("user_id") or udata.get("patient_id") or ppath.stem)
                    if uid in clean_pids or ppath.stem in clean_pids:
                        ppath.unlink(missing_ok=True)
                except Exception as e:
                    logger.debug("Error inspecting user file %s: %s", ppath, e)

        # 3. Delete registered_faces folders
        for faces_root in (Path("shared/data/registered_faces"), Path("data/registered_faces")):
            if faces_root.exists():
                for p in clean_pids:
                    target_dir = faces_root / p
                    if target_dir.exists() and target_dir.is_dir():
                        shutil.rmtree(target_dir, ignore_errors=True)

        # 4. Clean active patient.json
        for pf in (Path("shared/data/patient.json"), Path("data/patient.json")):
            if pf.exists():
                try:
                    with open(pf, "r", encoding="utf-8") as f:
                        cur = json.load(f)
                    if str(cur.get("patient_id") or "") in clean_pids or str(cur.get("user_id") or "") in clean_pids:
                        pf.unlink(missing_ok=True)
                except Exception:
                    pass

        # 5. Reset state.json if current patient is deleted
        state_file = Path("data/state.json")
        if state_file.exists():
            try:
                with open(state_file, "r", encoding="utf-8") as f:
                    st = json.load(f)
                if str(st.get("patient_id") or "") in clean_pids:
                    st["patient_id"] = None
                    st["name"] = "Chưa nhận diện"
                    with open(state_file, "w", encoding="utf-8") as f:
                        json.dump(st, f, ensure_ascii=False, indent=2)
            except Exception:
                pass

        # 6. Retrain face recognizer if available
        try:
            from features.face_monitoring.face_adapter import FaceAdapter
            fa = FaceAdapter()
            fa.train_recognizer()
        except Exception as e:
            logger.debug("FaceAdapter retrain after deletion: %s", e)

    def _admin_delete_patient(self, patient_id: str) -> Tuple[int, Dict[str, Any]]:
        with self.db.connect() as conn:
            p = conn.fetchone("""
                SELECT p.*, lpm.legacy_id
                FROM patients p
                LEFT JOIN legacy_patient_map lpm ON p.patient_id = lpm.patient_id
                WHERE p.patient_id = ? OR p.user_id = ? OR lpm.legacy_id = ?
                LIMIT 1
            """, (patient_id, patient_id, patient_id))
            if not p:
                return 404, {"error": "Không tìm thấy hồ sơ người bệnh"}

            pid = p["patient_id"]
            uid = p["user_id"]
            legacy_id = p.get("legacy_id") or ""
            num_id = pid.replace("patient_", "").lstrip("0") or "1" if pid.startswith("patient_") else ""
            all_pids = list(set(filter(None, [pid, legacy_id, num_id, patient_id])))

            conn.execute("PRAGMA foreign_keys = OFF;")
            try:
                # 1. Prescriptions and medication items
                rx_rows = conn.fetchall("SELECT prescription_id FROM prescriptions WHERE patient_id = ?", (pid,))
                rx_ids = [r["prescription_id"] for r in rx_rows]
                if rx_ids:
                    rx_placeholders = ",".join(["?"] * len(rx_ids))
                    pi_rows = conn.fetchall(f"SELECT prescription_item_id FROM prescription_items WHERE prescription_id IN ({rx_placeholders})", rx_ids)
                    pi_ids = [r["prescription_item_id"] for r in pi_rows]
                    if pi_ids:
                        pi_placeholders = ",".join(["?"] * len(pi_ids))
                        conn.execute(f"DELETE FROM medication_schedules WHERE prescription_item_id IN ({pi_placeholders})", pi_ids)
                    conn.execute(f"DELETE FROM prescription_items WHERE prescription_id IN ({rx_placeholders})", rx_ids)
                conn.execute("DELETE FROM prescriptions WHERE patient_id = ?", (pid,))

                # 2. Alerts and notifications
                alert_rows = conn.fetchall("SELECT alert_id FROM alerts WHERE patient_id = ?", (pid,))
                al_ids = [r["alert_id"] for r in alert_rows]
                if al_ids:
                    al_placeholders = ",".join(["?"] * len(al_ids))
                    conn.execute(f"DELETE FROM notification_deliveries WHERE alert_id IN ({al_placeholders})", al_ids)
                conn.execute("DELETE FROM alerts WHERE patient_id = ?", (pid,))

                # 3. Caregiver reports and messages
                rep_rows = conn.fetchall("SELECT report_id FROM caregiver_reports WHERE patient_id = ?", (pid,))
                rep_ids = [r["report_id"] for r in rep_rows]
                if rep_ids:
                    rep_placeholders = ",".join(["?"] * len(rep_ids))
                    conn.execute(f"DELETE FROM report_messages WHERE report_id IN ({rep_placeholders})", rep_ids)
                conn.execute("DELETE FROM caregiver_reports WHERE patient_id = ?", (pid,))

                # 4. Clinical messages and receipts
                msg_rows = conn.fetchall("SELECT message_id FROM clinical_messages WHERE patient_id = ?", (pid,))
                msg_ids = [r["message_id"] for r in msg_rows]
                if msg_ids:
                    msg_placeholders = ",".join(["?"] * len(msg_ids))
                    conn.execute(f"DELETE FROM message_receipts WHERE message_id IN ({msg_placeholders})", msg_ids)
                conn.execute("DELETE FROM clinical_messages WHERE patient_id = ?", (pid,))

                # 5. Care recommendations and receipts
                rec_rows = conn.fetchall("SELECT recommendation_id FROM care_recommendations WHERE patient_id = ?", (pid,))
                rec_ids = [r["recommendation_id"] for r in rec_rows]
                if rec_ids:
                    rec_placeholders = ",".join(["?"] * len(rec_ids))
                    conn.execute(f"DELETE FROM recommendation_receipts WHERE recommendation_id IN ({rec_placeholders})", rec_ids)
                conn.execute("DELETE FROM care_recommendations WHERE patient_id = ?", (pid,))

                # 6. All remaining dependent tables
                dependent_tables = [
                    "dose_occurrences", "measurement_groups", "health_observations",
                    "symptom_entries", "clinician_reviews", "monitoring_plans",
                    "game_sessions", "mood_checkins", "emotion_inferences", "rehab_events",
                    "rehab_sessions", "rehab_plans", "emergency_contacts",
                    "emergency_contact_events", "outbox_events", "sync_inbox",
                    "device_sessions", "measurement_requests", "exercise_plan_versions",
                    "patient_caregivers", "care_team", "legacy_patient_map"
                ]
                for tbl in dependent_tables:
                    try:
                        conn.execute(f"DELETE FROM {tbl} WHERE patient_id = ?", (pid,))
                    except Exception:
                        pass

                for p_alias in all_pids:
                    try:
                        conn.execute("DELETE FROM legacy_patient_map WHERE legacy_id = ? OR patient_id = ?", (p_alias, p_alias))
                    except Exception:
                        pass

                # 7. Audit events for patient and user
                conn.execute("DELETE FROM audit_events WHERE (entity_type = 'patient' AND entity_id = ?) OR user_id = ?", (pid, uid))

                # 8. Finally delete patients and users record
                conn.execute("DELETE FROM patients WHERE patient_id = ?", (pid,))
                if uid:
                    conn.execute("DELETE FROM users WHERE user_id = ?", (uid,))
                conn.commit()
            finally:
                conn.execute("PRAGMA foreign_keys = ON;")

        self._cleanup_patient_files(all_pids)
        return 200, {"status": "deleted", "message": f"Đã xóa hoàn toàn hồ sơ người cao tuổi {pid} khỏi hệ thống"}

    # -------------------------------------------------------------
    # CAREGIVERS CONTROLLERS
    # -------------------------------------------------------------
    def _admin_get_caregivers(self, query_params: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
        with self.db.connect() as conn:
            rows = conn.fetchall(
                """
                SELECT c.caregiver_id, c.user_id, c.relationship_desc, c.email_verified, c.phone_verified,
                       u.email, u.phone, u.full_name, u.is_active, u.created_at
                FROM caregivers c
                JOIN users u ON c.user_id = u.user_id
                ORDER BY u.created_at DESC
                """
            )
            caregivers = []
            for r in rows:
                cg_dict = dict(r)
                pat_rows = conn.fetchall(
                    """
                    SELECT pc.patient_caregiver_id, pc.patient_id, pc.relationship_type, pc.is_primary, pc.is_active,
                           p.full_name as patient_name, p.status as patient_status
                    FROM patient_caregivers pc
                    JOIN patients p ON pc.patient_id = p.patient_id
                    WHERE pc.caregiver_id = ?
                    ORDER BY pc.is_primary DESC, p.full_name ASC
                    """,
                    (r["caregiver_id"],),
                )
                cg_dict["patients"] = [dict(p) for p in pat_rows]
                caregivers.append(cg_dict)

            return 200, {"caregivers": caregivers}

    def _admin_get_caregiver_detail(self, caregiver_id: str) -> Tuple[int, Dict[str, Any]]:
        with self.db.connect() as conn:
            c = conn.fetchone(
                """
                SELECT c.caregiver_id, c.user_id, c.relationship_desc, c.email_verified, c.phone_verified,
                       u.email, u.phone, u.full_name, u.is_active, u.created_at
                FROM caregivers c
                JOIN users u ON c.user_id = u.user_id
                WHERE c.caregiver_id = ? OR c.user_id = ?
                """,
                (caregiver_id, caregiver_id),
            )
            if not c:
                return 404, {"error": "Không tìm thấy người nhà"}

            cg_dict = dict(c)
            pat_rows = conn.fetchall(
                """
                SELECT pc.patient_caregiver_id, pc.patient_id, pc.relationship_type, pc.is_primary, pc.is_active,
                       p.full_name as patient_name, p.status as patient_status
                FROM patient_caregivers pc
                JOIN patients p ON pc.patient_id = p.patient_id
                WHERE pc.caregiver_id = ?
                ORDER BY pc.is_primary DESC, p.full_name ASC
                """,
                (c["caregiver_id"],),
            )
            cg_dict["patients"] = [dict(p) for p in pat_rows]
            return 200, {"caregiver": cg_dict}

    def _admin_create_caregiver(self, body: Dict[str, Any], admin_user_id: str) -> Tuple[int, Dict[str, Any]]:
        full_name = (body.get("full_name") or "").strip()
        email = (body.get("email") or "").strip().lower()
        if not full_name or not email:
            return 400, {"error": "Vui lòng nhập đầy đủ Họ và tên và Email người nhà"}

        phone = (body.get("phone") or "").strip()
        password = body.get("password") or ""
        if not password:
            return 400, {"error": "Vui lòng nhập mật khẩu"}
        relationship_desc = (body.get("relationship_desc") or "Người thân").strip()
        patient_id = body.get("patient_id")
        relationship_type = (body.get("relationship_type") or relationship_desc).strip()
        is_primary = int(body.get("is_primary", 1))

        now_iso = datetime.now(timezone.utc).isoformat()
        future_iso = (datetime.now(timezone.utc) + timedelta(days=3650)).isoformat()
        user_id = f"user_cg_{uuid.uuid4().hex[:8]}"
        caregiver_id = f"caregiver_{uuid.uuid4().hex[:8]}"
        pw_hash = hash_password(password)

        with self.db.connect() as conn:
            existing = conn.fetchone("SELECT user_id FROM users WHERE LOWER(email) = ?", (email,))
            if existing:
                return 400, {"error": f"Email '{email}' đã tồn tại trong hệ thống"}

            conn.execute(
                """
                INSERT INTO users (user_id, email, phone, full_name, password_hash, role, is_active, created_at)
                VALUES (?, ?, ?, ?, ?, 'caregiver', 1, ?)
                """,
                (user_id, email, phone, full_name, pw_hash, now_iso),
            )
            conn.execute(
                """
                INSERT INTO caregivers (caregiver_id, user_id, email_verified, phone_verified, relationship_desc)
                VALUES (?, ?, 1, 1, ?)
                """,
                (caregiver_id, user_id, relationship_desc),
            )

            if patient_id:
                p_exists = conn.fetchone("SELECT patient_id FROM patients WHERE patient_id = ?", (patient_id,))
                if p_exists:
                    pc_id = f"pc_{uuid.uuid4().hex[:8]}"
                    conn.execute(
                        """
                        INSERT INTO patient_caregivers (
                            patient_caregiver_id, patient_id, caregiver_id, relationship_type,
                            is_primary, notification_priority, can_edit_schedule, can_view_health,
                            consent_status, valid_from, valid_to, is_active
                        ) VALUES (?, ?, ?, ?, ?, 1, 1, 1, 'GRANTED', ?, ?, 1)
                        """,
                        (pc_id, patient_id, caregiver_id, relationship_type, is_primary, now_iso, future_iso),
                    )

            conn.commit()

        return 201, {
            "caregiver_id": caregiver_id,
            "user_id": user_id,
            "full_name": full_name,
            "email": email,
            "relationship_desc": relationship_desc,
            "message": "Đã cấp tài khoản Người nhà thành công",
        }

    def _admin_update_caregiver(self, caregiver_id: str, body: Dict[str, Any]) -> Tuple[int, Dict[str, Any]]:
        with self.db.connect() as conn:
            cg = conn.fetchone("SELECT * FROM caregivers WHERE caregiver_id = ? OR user_id = ?", (caregiver_id, caregiver_id))
            if not cg:
                return 404, {"error": "Không tìm thấy người nhà"}

            c_id = cg["caregiver_id"]
            u_id = cg["user_id"]

            full_name = body.get("full_name")
            email = body.get("email")
            phone = body.get("phone")
            relationship_desc = body.get("relationship_desc")
            is_active = body.get("is_active")

            if email:
                email = email.strip().lower()
                dup = conn.fetchone("SELECT user_id FROM users WHERE LOWER(email) = ? AND user_id != ?", (email, u_id))
                if dup:
                    return 400, {"error": f"Email '{email}' đã được sử dụng bởi tài khoản khác"}

            u_updates = []
            u_params = []
            if full_name is not None:
                u_updates.append("full_name = ?")
                u_params.append(full_name.strip())
            if email is not None:
                u_updates.append("email = ?")
                u_params.append(email)
            if phone is not None:
                u_updates.append("phone = ?")
                u_params.append(phone.strip())
            if is_active is not None:
                u_updates.append("is_active = ?")
                u_params.append(int(is_active))

            if u_updates:
                u_params.append(u_id)
                conn.execute(f"UPDATE users SET {', '.join(u_updates)} WHERE user_id = ?", u_params)

            if relationship_desc is not None:
                conn.execute("UPDATE caregivers SET relationship_desc = ? WHERE caregiver_id = ?", (relationship_desc.strip(), c_id))

            conn.commit()

        return 200, {"status": "updated", "caregiver_id": c_id, "message": "Đã cập nhật thông tin người nhà"}

    def _admin_reset_caregiver_password(self, caregiver_id: str, body: Dict[str, Any]) -> Tuple[int, Dict[str, Any]]:
        new_pw = (body.get("new_password") or body.get("password") or "").strip()
        if not new_pw:
            return 400, {"error": "Vui lòng nhập mật khẩu mới"}

        with self.db.connect() as conn:
            cg = conn.fetchone("SELECT user_id FROM caregivers WHERE caregiver_id = ? OR user_id = ?", (caregiver_id, caregiver_id))
            if not cg:
                return 404, {"error": "Không tìm thấy người nhà"}

            conn.execute("UPDATE users SET password_hash = ? WHERE user_id = ?", (hash_password(new_pw), cg["user_id"]))
            conn.commit()

        return 200, {"status": "password_reset", "message": "Đã đặt lại mật khẩu người nhà thành công"}

    def _admin_toggle_caregiver_status(self, caregiver_id: str, body: Dict[str, Any]) -> Tuple[int, Dict[str, Any]]:
        with self.db.connect() as conn:
            cg = conn.fetchone("SELECT user_id FROM caregivers WHERE caregiver_id = ? OR user_id = ?", (caregiver_id, caregiver_id))
            if not cg:
                return 404, {"error": "Không tìm thấy người nhà"}

            u = conn.fetchone("SELECT is_active FROM users WHERE user_id = ?", (cg["user_id"],))
            new_status = 1 if u["is_active"] == 0 else 0
            if "is_active" in body:
                new_status = int(body["is_active"])

            conn.execute("UPDATE users SET is_active = ? WHERE user_id = ?", (new_status, cg["user_id"]))
            conn.commit()

        return 200, {"status": "status_updated", "is_active": new_status, "message": "Đã cập nhật trạng thái người nhà"}

    def _cleanup_caregiver_files(self, email: str, phone: str = "") -> None:
        """Remove caregiver details from shared/data/patients.json and data/users/*.json."""
        clean_email = (email or "").strip().lower()
        clean_phone = (phone or "").strip()
        if not clean_email and not clean_phone:
            return

        patients_json_path = Path("shared/data/patients.json")
        if patients_json_path.exists():
            try:
                with open(patients_json_path, "r", encoding="utf-8") as f:
                    pdict = json.load(f)
                changed = False
                for pid, pdata in pdict.items():
                    cg = pdata.get("caregiver") or {}
                    c_em = str(cg.get("email") or "").strip().lower()
                    c_ph = str(cg.get("phone") or "").strip()
                    if (clean_email and c_em == clean_email) or (clean_phone and c_ph == clean_phone):
                        pdata["caregiver"] = {"name": "", "relationship": "", "email": "", "phone": "", "password": ""}
                        pdata["last_updated"] = datetime.now().isoformat()
                        changed = True
                if changed:
                    with open(patients_json_path, "w", encoding="utf-8") as f:
                        json.dump(pdict, f, ensure_ascii=False, indent=2)
            except Exception as e:
                logger.warning("Error cleaning caregiver from patients.json: %s", e)

        users_dir = Path("data/users")
        if users_dir.exists():
            for ppath in users_dir.glob("*.json"):
                try:
                    with open(ppath, "r", encoding="utf-8") as f:
                        udata = json.load(f)
                    cg = udata.get("caregiver") or {}
                    c_em = str(cg.get("email") or "").strip().lower()
                    c_ph = str(cg.get("phone") or "").strip()
                    if (clean_email and c_em == clean_email) or (clean_phone and c_ph == clean_phone):
                        udata["caregiver"] = {"name": "", "relationship": "", "email": "", "phone": "", "password": ""}
                        udata["last_updated"] = datetime.now().isoformat()
                        with open(ppath, "w", encoding="utf-8") as f:
                            json.dump(udata, f, ensure_ascii=False, indent=2)
                except Exception as e:
                    logger.debug("Error cleaning caregiver from %s: %s", ppath, e)

    def _admin_delete_caregiver(self, caregiver_id: str) -> Tuple[int, Dict[str, Any]]:
        with self.db.connect() as conn:
            cg = conn.fetchone("""
                SELECT c.caregiver_id, c.user_id, u.email, u.phone
                FROM caregivers c
                JOIN users u ON c.user_id = u.user_id
                WHERE c.caregiver_id = ? OR c.user_id = ? OR LOWER(u.email) = ?
            """, (caregiver_id, caregiver_id, caregiver_id.lower()))
            if not cg:
                u = conn.fetchone("SELECT user_id, email, phone FROM users WHERE (user_id = ? OR LOWER(email) = ?) AND role = 'caregiver'", (caregiver_id, caregiver_id.lower()))
                if not u:
                    return 404, {"error": "Không tìm thấy người nhà"}
                c_id = caregiver_id
                u_id = u["user_id"]
                cg_email = u["email"]
                cg_phone = u.get("phone", "")
            else:
                c_id = cg["caregiver_id"]
                u_id = cg["user_id"]
                cg_email = cg["email"]
                cg_phone = cg.get("phone", "")

            conn.execute("PRAGMA foreign_keys = OFF;")
            try:
                conn.execute("DELETE FROM notification_deliveries WHERE caregiver_id = ?", (c_id,))
                reports = conn.fetchall("SELECT report_id FROM caregiver_reports WHERE caregiver_id = ?", (c_id,))
                rep_ids = [r["report_id"] for r in reports]
                if rep_ids:
                    q = ",".join(["?"] * len(rep_ids))
                    conn.execute(f"DELETE FROM report_messages WHERE report_id IN ({q})", rep_ids)
                conn.execute("DELETE FROM caregiver_reports WHERE caregiver_id = ?", (c_id,))
                conn.execute("DELETE FROM patient_caregivers WHERE caregiver_id = ?", (c_id,))
                conn.execute("DELETE FROM message_receipts WHERE user_id = ?", (u_id,))
                conn.execute("DELETE FROM recommendation_receipts WHERE user_id = ?", (u_id,))
                conn.execute("DELETE FROM audit_events WHERE user_id = ?", (u_id,))
                conn.execute("DELETE FROM caregivers WHERE caregiver_id = ? OR user_id = ?", (c_id, u_id))
                conn.execute("DELETE FROM users WHERE user_id = ?", (u_id,))
                conn.commit()
            finally:
                conn.execute("PRAGMA foreign_keys = ON;")

        self._cleanup_caregiver_files(cg_email, cg_phone)
        return 200, {"status": "deleted", "message": "Đã xóa hoàn toàn tài khoản người nhà khỏi hệ thống"}

    def _admin_delete_user(self, user_id: str) -> Tuple[int, Dict[str, Any]]:
        with self.db.connect() as conn:
            u = conn.fetchone("SELECT user_id, email, role FROM users WHERE user_id = ? OR LOWER(email) = ?", (user_id, user_id.lower()))
            if not u:
                cg = conn.fetchone("SELECT caregiver_id FROM caregivers WHERE caregiver_id = ?", (user_id,))
                if cg:
                    return self._admin_delete_caregiver(cg["caregiver_id"])
                cl = conn.fetchone("SELECT clinician_id FROM clinicians WHERE clinician_id = ?", (user_id,))
                if cl:
                    return self._admin_delete_doctor(cl["clinician_id"])
                p = conn.fetchone("SELECT patient_id FROM patients WHERE patient_id = ?", (user_id,))
                if p:
                    return self._admin_delete_patient(p["patient_id"])
                return 404, {"error": "Không tìm thấy tài khoản người dùng"}

            role = u["role"]
            if role == "caregiver":
                return self._admin_delete_caregiver(u["user_id"])
            elif role == "clinician":
                return self._admin_delete_doctor(u["user_id"])
            elif role == "patient":
                return self._admin_delete_patient(u["user_id"])
            elif role == "admin":
                admin_count = conn.fetchone("SELECT COUNT(*) as c FROM users WHERE role = 'admin'")["c"]
                if admin_count <= 1:
                    return 400, {"error": "Không thể xóa tài khoản Quản trị viên duy nhất của hệ thống"}
                conn.execute("PRAGMA foreign_keys = OFF;")
                try:
                    conn.execute("DELETE FROM audit_events WHERE user_id = ?", (u["user_id"],))
                    conn.execute("DELETE FROM message_receipts WHERE user_id = ?", (u["user_id"],))
                    conn.execute("DELETE FROM recommendation_receipts WHERE user_id = ?", (u["user_id"],))
                    conn.execute("DELETE FROM users WHERE user_id = ?", (u["user_id"],))
                    conn.commit()
                finally:
                    conn.execute("PRAGMA foreign_keys = ON;")
                return 200, {"status": "deleted", "message": "Đã xóa tài khoản Quản trị viên"}
            else:
                conn.execute("PRAGMA foreign_keys = OFF;")
                try:
                    conn.execute("DELETE FROM users WHERE user_id = ?", (u["user_id"],))
                    conn.commit()
                finally:
                    conn.execute("PRAGMA foreign_keys = ON;")
                return 200, {"status": "deleted", "message": "Đã xóa tài khoản người dùng"}

    def _admin_get_all_users(self) -> Tuple[int, Dict[str, Any]]:
        with self.db.connect() as conn:
            users = conn.fetchall("""
                SELECT user_id, email, phone, full_name, role, is_active, created_at
                FROM users
                ORDER BY created_at DESC
            """)
            return 200, {"users": [dict(u) for u in users]}

    # -------------------------------------------------------------
    # PATIENT-CAREGIVER LINKING CONTROLLERS
    # -------------------------------------------------------------
    def _admin_get_patient_caregivers(self, query_params: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
        with self.db.connect() as conn:
            sql = """
                SELECT pc.*, p.full_name as patient_name, u.full_name as caregiver_name, u.email as caregiver_email, u.phone as caregiver_phone
                FROM patient_caregivers pc
                JOIN patients p ON pc.patient_id = p.patient_id
                JOIN caregivers c ON pc.caregiver_id = c.caregiver_id
                JOIN users u ON c.user_id = u.user_id
                ORDER BY pc.is_active DESC, p.full_name ASC
            """
            rows = conn.fetchall(sql)
            return 200, {"patient_caregivers": [dict(r) for r in rows]}

    def _admin_link_patient_caregiver(self, body: Dict[str, Any], admin_user_id: str) -> Tuple[int, Dict[str, Any]]:
        patient_id = (body.get("patient_id") or "").strip()
        caregiver_id = (body.get("caregiver_id") or "").strip()
        relationship_type = (body.get("relationship_type") or "Người thân").strip()
        is_primary = int(body.get("is_primary", 1))
        can_edit_schedule = int(body.get("can_edit_schedule", 1))
        can_view_health = int(body.get("can_view_health", 1))

        if not patient_id or not caregiver_id:
            return 400, {"error": "Vui lòng chọn đầy đủ Người cao tuổi và Người nhà"}

        now_iso = datetime.now(timezone.utc).isoformat()
        future_iso = (datetime.now(timezone.utc) + timedelta(days=3650)).isoformat()
        pc_id = f"pc_{uuid.uuid4().hex[:8]}"

        with self.db.connect() as conn:
            p = conn.fetchone("SELECT patient_id FROM patients WHERE patient_id = ?", (patient_id,))
            if not p:
                return 404, {"error": "Không tìm thấy hồ sơ người cao tuổi"}
            c = conn.fetchone("SELECT caregiver_id FROM caregivers WHERE caregiver_id = ?", (caregiver_id,))
            if not c:
                return 404, {"error": "Không tìm thấy tài khoản người nhà"}

            existing = conn.fetchone(
                "SELECT patient_caregiver_id FROM patient_caregivers WHERE patient_id = ? AND caregiver_id = ?",
                (patient_id, caregiver_id),
            )
            if existing:
                conn.execute(
                    """
                    UPDATE patient_caregivers
                    SET relationship_type = ?, is_primary = ?, can_edit_schedule = ?, can_view_health = ?, is_active = 1
                    WHERE patient_caregiver_id = ?
                    """,
                    (relationship_type, is_primary, can_edit_schedule, can_view_health, existing["patient_caregiver_id"]),
                )
                conn.commit()
                return 200, {
                    "patient_caregiver_id": existing["patient_caregiver_id"],
                    "status": "updated",
                    "message": "Đã cập nhật liên kết chăm sóc thành công",
                }

            conn.execute(
                """
                INSERT INTO patient_caregivers (
                    patient_caregiver_id, patient_id, caregiver_id, relationship_type,
                    is_primary, notification_priority, can_edit_schedule, can_view_health,
                    consent_status, valid_from, valid_to, is_active
                ) VALUES (?, ?, ?, ?, ?, 1, ?, ?, 'GRANTED', ?, ?, 1)
                """,
                (pc_id, patient_id, caregiver_id, relationship_type, is_primary, can_edit_schedule, can_view_health, now_iso, future_iso),
            )
            conn.commit()

        return 201, {
            "patient_caregiver_id": pc_id,
            "status": "created",
            "message": "Đã phân công người nhà chăm sóc thành công",
        }

    def _admin_unlink_patient_caregiver(self, body: Dict[str, Any]) -> Tuple[int, Dict[str, Any]]:
        pc_id = body.get("patient_caregiver_id")
        patient_id = body.get("patient_id")
        caregiver_id = body.get("caregiver_id")

        with self.db.connect() as conn:
            if pc_id:
                conn.execute("DELETE FROM patient_caregivers WHERE patient_caregiver_id = ?", (pc_id,))
            elif patient_id and caregiver_id:
                conn.execute("DELETE FROM patient_caregivers WHERE patient_id = ? AND caregiver_id = ?", (patient_id, caregiver_id))
            else:
                return 400, {"error": "Thiếu mã liên kết chăm sóc để gỡ bỏ"}
            conn.commit()

        return 200, {"status": "unlinked", "message": "Đã gỡ liên kết chăm sóc thành công"}

    def _admin_remove_patient_caregiver_by_id(self, patient_caregiver_id: str) -> Tuple[int, Dict[str, Any]]:
        with self.db.connect() as conn:
            conn.execute("DELETE FROM patient_caregivers WHERE patient_caregiver_id = ?", (patient_caregiver_id,))
            conn.commit()
        return 200, {"status": "unlinked", "message": "Đã gỡ liên kết chăm sóc thành công"}

    # -------------------------------------------------------------
    # CARE-TEAM CONTROLLERS
    # -------------------------------------------------------------
    def _admin_assign_care_team(self, body: Dict[str, Any], admin_user_id: str) -> Tuple[int, Dict[str, Any]]:
        clinician_id = body.get("clinician_id")
        patient_id = body.get("patient_id")
        role_desc = body.get("role_description") or "Bác sĩ điều trị phụ trách"
        if not clinician_id or not patient_id:
            return 400, {"error": "Thiếu clinician_id hoặc patient_id"}

        now_iso = datetime.now(timezone.utc).isoformat()
        future_iso = (datetime.now(timezone.utc) + timedelta(days=3650)).isoformat()
        ct_id = f"ct_{uuid.uuid4().hex[:8]}"

        with self.db.connect() as conn:
            existing = conn.fetchone("SELECT care_team_id FROM care_team WHERE clinician_id = ? AND patient_id = ?", (clinician_id, patient_id))
            if existing:
                conn.execute("UPDATE care_team SET is_active = 1, role_description = ? WHERE care_team_id = ?", (role_desc, existing["care_team_id"]))
                conn.commit()
                return 200, {"care_team_id": existing["care_team_id"], "message": "Đã kích hoạt lại phân công bác sĩ"}

            conn.execute(
                """
                INSERT INTO care_team (care_team_id, patient_id, clinician_id, role_description, valid_from, valid_to, consent_status, is_active)
                VALUES (?, ?, ?, ?, ?, ?, 'GRANTED', 1)
                """,
                (ct_id, patient_id, clinician_id, role_desc, now_iso, future_iso),
            )
            conn.commit()

        return 201, {"care_team_id": ct_id, "message": "Đã phân công bác sĩ phụ trách bệnh nhân"}

    def _admin_remove_care_team(self, care_team_id: str) -> Tuple[int, Dict[str, Any]]:
        with self.db.connect() as conn:
            conn.execute("DELETE FROM care_team WHERE care_team_id = ?", (care_team_id,))
            conn.commit()
        return 200, {"status": "deleted", "message": "Đã gỡ phân công bác sĩ khỏi bệnh nhân"}

    # =========================================================================
    # VOICE AI ENDPOINTS (Section 5 & Phase 4-6 Implementation)
    # =========================================================================

    def _handle_voice_route(
        self,
        method: str,
        path: str,
        query_params: Dict[str, str],
        body: Dict[str, Any],
        user_id: str,
        role: str,
        ip_address: str,
        headers: Optional[Dict[str, str]] = None,
    ) -> Tuple[int, Dict[str, Any]]:
        """Dispatcher for /api/v1/voice/... and /api/voice/... endpoints."""
        from features.voice_assistant.voice_ai.config import is_voice_ai_enabled
        if not is_voice_ai_enabled():
            return 503, {"error": "Hệ thống Voice AI đang tạm thời dừng hoạt động bảo trì (feature flag tắt)."}

        norm_path = path
        if norm_path.startswith("/api/v1/voice/"):
            sub_path = norm_path[len("/api/v1/voice/"):]
        elif norm_path.startswith("/api/voice/"):
            sub_path = norm_path[len("/api/voice/"):]
        else:
            sub_path = ""

        sub_path_clean = sub_path.strip("/")
        parts = sub_path_clean.split("/")
        # 0. POST /api/v1/voice/stt (Decommissioned: STT API direction removed)
        if sub_path_clean == "stt":
            return 410, {
                "error": "Voice STT API is decommissioned. System operates via direct button interactions.",
                "status": "disabled",
            }

        # 1. POST /api/v1/voice/interactions
        if method == "POST" and (sub_path_clean == "interactions" or sub_path_clean == ""):
            return self._handle_voice_interaction_submit(body, user_id, role, ip_address, headers)

        # 2. GET /api/v1/voice/interactions/{interaction_id}
        if method == "GET" and len(parts) == 2 and parts[0] == "interactions":
            interaction_id = parts[1]
            return self._handle_voice_interaction_get(interaction_id, user_id, role, ip_address)

        # 3. POST /api/v1/voice/assistance/{request_id}/acknowledge
        if method == "POST" and len(parts) == 3 and parts[0] == "assistance" and parts[2] == "acknowledge":
            request_id = parts[1]
            return self._handle_voice_assistance_acknowledge(request_id, user_id, role, body, ip_address)

        # 4. GET /api/v1/voice/sessions/{session_id}
        if method == "GET" and len(parts) == 2 and parts[0] == "sessions":
            session_id = parts[1]
            return self._handle_voice_session_get(session_id, user_id, role, ip_address)

        # 5. POST /api/v1/voice/interactions/{interaction_id}/confirm
        if method == "POST" and len(parts) == 3 and parts[0] == "interactions" and parts[2] == "confirm":
            interaction_id = parts[1]
            return self._handle_voice_interaction_confirm(interaction_id, user_id, role, body, ip_address)

        # 6. POST /api/v1/voice/interactions/{interaction_id}/cancel
        if method == "POST" and len(parts) == 3 and parts[0] == "interactions" and parts[2] == "cancel":
            interaction_id = parts[1]
            return self._handle_voice_interaction_cancel(interaction_id, user_id, role, body, ip_address)

        return 404, {"error": f"Không tìm thấy endpoint Voice AI: {path}"}

    def _handle_voice_interaction_submit(
        self,
        body: Dict[str, Any],
        user_id: str,
        role: str,
        ip_address: str,
        headers: Optional[Dict[str, str]] = None,
    ) -> Tuple[int, Dict[str, Any]]:
        """Handles voice interaction submissions (transcript or audio) with auth and idempotency."""
        body = body or {}
        # Resolve patient_id
        patient_id = str(body.get("patient_id") or "").strip()
        if not patient_id:
            if role == "patient":
                patient_id = user_id
            else:
                accessible = self.auth.list_accessible_patients(user_id)
                if len(accessible) == 1:
                    patient_id = accessible[0]["patient_id"]
                elif len(accessible) == 0:
                    return 403, {"error": "Người dùng không được phân quyền cho bất kỳ bệnh nhân nào"}
                else:
                    return 400, {"error": "Cần cung cấp patient_id cụ thể cho người bệnh"}

        # Check permission to access patient
        has_access, err = self.auth.check_patient_access(user_id, patient_id, "view")
        if not has_access:
            return 403, {"error": f"Không có quyền thao tác với bệnh nhân: {err}"}

        # Resolve transcript
        transcript = str(body.get("transcript") or "").strip()
        if not transcript:
            if body.get("audio_base64"):
                return 400, {
                    "error": "Audio STT processing is disabled. The system operates via direct button interactions or pre-transcribed text."
                }
            return 400, {"error": "Yêu cầu phải có 'transcript' dạng văn bản"}

        # Idempotency Key
        hdrs = {k.lower(): v for k, v in (headers or {}).items()}
        idempotency_key = hdrs.get("idempotency-key") or body.get("idempotency_key")

        # Prepare auth_context
        auth_context = {
            "patient_id": patient_id,
            "user_id": user_id,
            "role": role,
            "current_screen": body.get("current_screen", "unknown"),
            "extra_state": body.get("extra_state", {}),
        }

        # Dispatch through WorkflowOrchestrator
        try:
            result = self.orchestrator.process_voice_interaction(
                auth_context=auth_context,
                transcript=transcript,
                idempotency_key=idempotency_key,
                session_id=body.get("session_id"),
            )
        except Exception as e:
            logger.error("WorkflowOrchestrator processing error: %s", e)
            return 500, {"error": f"Lỗi điều phối tương tác thoại: {str(e)}"}

        # Trigger outbox relay for immediate SSE broadcasting
        self._trigger_relay()

        # Audit log
        self.auth.log_audit(
            user_id,
            "VOICE_INTERACTION",
            "patient",
            patient_id,
            {
                "interaction_id": result.interaction_id,
                "overall_status": result.overall_status.value if hasattr(result.overall_status, "value") else str(result.overall_status),
                "intents_count": len(result.intent_results),
            },
            ip_address,
        )

        response_data = {
            "interaction_id": result.interaction_id,
            "session_id": result.session_id,
            "patient_id": result.patient_id,
            "timestamp_utc": result.timestamp_utc,
            "overall_status": result.overall_status.value if hasattr(result.overall_status, "value") else str(result.overall_status),
            "response_speech_text": result.response_speech_text,
            "clarification_question": result.clarification_question,
            "safety_signal": {
                "is_urgent_assistance": result.safety_signal.is_urgent_assistance,
                "is_red_flag_health": result.safety_signal.is_red_flag_health,
                "red_flag_severity": result.safety_signal.red_flag_severity.value if hasattr(result.safety_signal.red_flag_severity, "value") else str(result.safety_signal.red_flag_severity),
                "urgency_reason": result.safety_signal.urgency_reason,
            },
            "intents": [
                {
                    "intent_id": ir.intent_id,
                    "intent_type": ir.intent_type.value if hasattr(ir.intent_type, "value") else str(ir.intent_type),
                    "status": ir.status.value if hasattr(ir.status, "value") else str(ir.status),
                    "priority": ir.priority.value if hasattr(ir.priority, "value") else str(ir.priority),
                    "aggregate_id": ir.aggregate_id,
                    "aggregate_type": ir.aggregate_type,
                    "client_action": {
                        "action_type": ir.client_action.action_type,
                        "parameters": ir.client_action.parameters,
                    } if ir.client_action else None,
                    "message_to_elder": ir.message_to_elder,
                    "error_code": ir.error_code,
                }
                for ir in result.intent_results
            ],
        }
        return 200, response_data

    def _handle_voice_interaction_get(
        self,
        interaction_id: str,
        user_id: str,
        role: str,
        ip_address: str,
    ) -> Tuple[int, Dict[str, Any]]:
        """Retrieves details of a past voice interaction."""
        result = self.orchestrator.get_interaction(interaction_id)
        if not result:
            return 404, {"error": f"Không tìm thấy tương tác giọng nói với mã {interaction_id}"}

        has_access, err = self.auth.check_patient_access(user_id, result.patient_id, "view")
        if not has_access:
            return 403, {"error": "Không có quyền xem chi tiết tương tác của bệnh nhân này"}

        self.auth.log_audit(user_id, "READ_VOICE_INTERACTION", "voice_interaction", interaction_id, {}, ip_address)

        return 200, {
            "interaction_id": result.interaction_id,
            "session_id": result.session_id,
            "patient_id": result.patient_id,
            "timestamp_utc": result.timestamp_utc,
            "overall_status": result.overall_status.value if hasattr(result.overall_status, "value") else str(result.overall_status),
            "transcript_redacted": result.transcript,
            "response_speech_text": result.response_speech_text,
            "clarification_question": result.clarification_question,
            "safety_signal": {
                "is_urgent_assistance": result.safety_signal.is_urgent_assistance,
                "is_red_flag_health": result.safety_signal.is_red_flag_health,
                "red_flag_severity": result.safety_signal.red_flag_severity.value if hasattr(result.safety_signal.red_flag_severity, "value") else str(result.safety_signal.red_flag_severity),
                "urgency_reason": result.safety_signal.urgency_reason,
            },
            "intents": [
                {
                    "intent_id": ir.intent_id,
                    "intent_type": ir.intent_type.value if hasattr(ir.intent_type, "value") else str(ir.intent_type),
                    "status": ir.status.value if hasattr(ir.status, "value") else str(ir.status),
                    "priority": ir.priority.value if hasattr(ir.priority, "value") else str(ir.priority),
                    "aggregate_id": ir.aggregate_id,
                    "aggregate_type": ir.aggregate_type,
                    "client_action": {
                        "action_type": ir.client_action.action_type,
                        "parameters": ir.client_action.parameters,
                    } if ir.client_action else None,
                    "message_to_elder": ir.message_to_elder,
                    "error_code": ir.error_code,
                }
                for ir in result.intent_results
            ],
        }

    def _handle_voice_assistance_acknowledge(
        self,
        request_id: str,
        user_id: str,
        role: str,
        body: Dict[str, Any],
        ip_address: str,
    ) -> Tuple[int, Dict[str, Any]]:
        """Doctor acknowledges an assistance request."""
        if role not in ("doctor", "clinician", "admin"):
            return 403, {"error": "Chỉ bác sĩ phụ trách (Clinician) hoặc Quản trị viên mới có quyền xác nhận tiếp nhận cuộc gọi hỗ trợ."}

        notes = str((body or {}).get("notes") or "").strip()

        try:
            ack_result = self.assistance_coordinator.acknowledge_request(
                request_id=request_id,
                clinician_user_id=user_id,
                notes=notes,
            )
            self._trigger_relay()

            self.auth.log_audit(
                user_id,
                "ACKNOWLEDGE_ASSISTANCE",
                "assistance_request",
                request_id,
                {"status": ack_result.get("status")},
                ip_address,
            )
            return 200, ack_result
        except PermissionError as pe:
            return 403, {"error": str(pe)}
        except ValueError as ve:
            return 404, {"error": str(ve)}
        except Exception as e:
            logger.error("Error acknowledging assistance request %s: %s", request_id, e)
            return 500, {"error": f"Lỗi hệ thống khi xác nhận yêu cầu hỗ trợ: {str(e)}"}

    def _handle_voice_session_get(
        self,
        session_id: str,
        user_id: str,
        role: str,
        ip_address: str,
    ) -> Tuple[int, Dict[str, Any]]:
        """Retrieves session state and clarification information."""
        session = self.orchestrator.get_session_by_id(session_id)
        if not session:
            return 404, {"error": f"Không tìm thấy phiên làm việc thoại với mã {session_id}"}

        has_access, err = self.auth.check_patient_access(user_id, session.patient_id, "view")
        if not has_access:
            return 403, {"error": "Không có quyền xem phiên làm việc thoại của bệnh nhân này"}

        return 200, {
            "session_id": session.session_id,
            "patient_id": session.patient_id,
            "state": session.state.value if hasattr(session.state, "value") else str(session.state),
            "active_workflow": session.active_workflow.value if session.active_workflow and hasattr(session.active_workflow, "value") else (str(session.active_workflow) if session.active_workflow else None),
            "pending_clarification": session.pending_clarification,
            "clarification_prompt": session.clarification_prompt,
            "history_length": len(session.history),
        }

    def _handle_voice_interaction_confirm(
        self,
        interaction_id: str,
        user_id: str,
        role: str,
        body: Dict[str, Any],
        ip_address: str,
    ) -> Tuple[int, Dict[str, Any]]:
        result = self.orchestrator.get_interaction(interaction_id)
        if not result:
            return 404, {"error": f"Không tìm thấy tương tác với mã {interaction_id}"}

        has_access, err = self.auth.check_patient_access(user_id, result.patient_id, "edit_schedule")
        if not has_access:
            return 403, {"error": "Không có quyền xác nhận hành động cho bệnh nhân này"}

        session = self.orchestrator.get_session_by_id(result.session_id)
        if session:
            session.confirm_action()

        return 200, {
            "interaction_id": interaction_id,
            "status": "CONFIRMED",
            "message": "Đã xác nhận hành động thành công.",
        }

    def _handle_voice_interaction_cancel(
        self,
        interaction_id: str,
        user_id: str,
        role: str,
        body: Dict[str, Any],
        ip_address: str,
    ) -> Tuple[int, Dict[str, Any]]:
        result = self.orchestrator.get_interaction(interaction_id)
        if not result:
            return 404, {"error": f"Không tìm thấy tương tác với mã {interaction_id}"}

        has_access, err = self.auth.check_patient_access(user_id, result.patient_id, "view")
        if not has_access:
            return 403, {"error": "Không có quyền hủy thao tác của bệnh nhân này"}

        session = self.orchestrator.get_session_by_id(result.session_id)
        if session:
            session.cancel_action()

        return 200, {
            "interaction_id": interaction_id,
            "status": "CANCELLED",
            "message": "Đã hủy hành động thành công.",
        }
