"""
Durable Sync Client for Elder Application.
Handles:
1. Online / Offline state management.
2. Local offline queue persistence with JSON storage.
3. Strict idempotency checking via client_event_id.
4. Auto-retry & flush upon reconnection.
5. Consolidated 'Today's To-Do' snapshot fetching.
"""

from __future__ import annotations

import json
import logging
import os
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from shared.database.connection import UnifiedDatabaseManager
from shared.database.outbox_helper import insert_outbox_event
from features.web_portal.auth import AuthService

logger = logging.getLogger("ElderSyncClient")
UTC = timezone.utc


class ElderSyncClient:
    def __init__(
        self,
        db: UnifiedDatabaseManager,
        patient_id: str = "patient_001",
        queue_dir: Optional[Path] = None,
        is_online: bool = True,
    ) -> None:
        self.db = db
        self.auth = AuthService(db)
        self.patient_id = patient_id
        self.is_online = is_online

        if queue_dir is None:
            from shared.config import DATA_DIR
            self.queue_dir = DATA_DIR / "sync_queues"
        else:
            self.queue_dir = Path(queue_dir)
        self.queue_dir.mkdir(parents=True, exist_ok=True)
        self.queue_file = self.queue_dir / f"offline_queue_{patient_id}.json"

        # Sync states: 'synced' (đã ghi nhận), 'syncing' (đang đồng bộ), 'pending_offline' (chưa gửi được / chờ mạng)
        self._sync_status = "synced"
        self._last_synced_at: Optional[str] = None
        self._last_error: Optional[str] = None

    def switch_patient(self, new_patient_id: str) -> None:
        """Safely switch patient identity and load new queue."""
        self.patient_id = new_patient_id
        self.queue_file = self.queue_dir / f"offline_queue_{new_patient_id}.json"
        self._sync_status = "synced"

    def set_online(self, online: bool) -> None:
        self.is_online = online
        if online:
            self.flush_offline_queue()

    def get_sync_status(self) -> Dict[str, Any]:
        queue = self._load_queue()
        pending_count = len([q for q in queue if q.get("status") == "pending"])
        label = "Đã ghi nhận"
        if pending_count > 0:
            label = "Đang đồng bộ..." if self.is_online else "Chưa gửi được (chờ mạng)"
        return {
            "status_code": self._sync_status,
            "status_label": label,
            "pending_count": pending_count,
            "is_online": self.is_online,
            "last_synced_at": self._last_synced_at,
            "last_error": self._last_error,
        }

    # =========================================================================
    # QUEUE PERSISTENCE
    # =========================================================================
    def _load_queue(self) -> List[Dict[str, Any]]:
        if not self.queue_file.exists():
            return []
        try:
            with open(self.queue_file, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.error("Error reading queue file %s: %s", self.queue_file, e)
            return []

    def _save_queue(self, queue: List[Dict[str, Any]]) -> None:
        try:
            with open(self.queue_file, "w", encoding="utf-8") as f:
                json.dump(queue, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error("Error writing queue file %s: %s", self.queue_file, e)

    def enqueue_event(self, event_type: str, payload: Dict[str, Any], client_event_id: Optional[str] = None) -> Dict[str, Any]:
        """Enqueue an event locally. If online, attempts immediate processing."""
        if payload.get('source') == 'voice' and not self.is_online:
            return {'status': 'failed', 'error': 'Không ghi lệnh voice khi offline; hãy dùng nút bấm.'}
        cid = client_event_id or f"clevt_{uuid.uuid4().hex[:16]}"
        now_iso = datetime.now(UTC).isoformat()
        item = {
            "client_event_id": cid,
            "patient_id": self.patient_id,
            "event_type": event_type,
            "payload": payload,
            "occurred_at": now_iso,
            "recorded_at": now_iso,
            "status": "pending",
            "server_received_at": None,
        }

        queue = self._load_queue()
        existing_item = next((q for q in queue if q.get('client_event_id') == cid), None)
        if existing_item:
            return existing_item
        queue.append(item)
        self._save_queue(queue)
        self._sync_status = "syncing" if self.is_online else "pending_offline"

        if self.is_online:
            try:
                self.flush_offline_queue(voice_command_id=cid if payload.get('source') == 'voice' else None)
            except Exception as exc:
                if payload.get('source') != 'voice':
                    raise
                # Voice is never replayed after a failed immediate transaction.
                queue = self._load_queue()
                for entry in queue:
                    if entry.get('client_event_id') == cid:
                        entry['status'], entry['error'] = 'failed', str(exc)
                self._save_queue(queue)
            for q in self._load_queue():
                if q.get("client_event_id") == cid:
                    return q

        return item

    def flush_offline_queue(self, voice_command_id: Optional[str] = None) -> int:
        """Processes all pending events in queue with database sync_inbox idempotency."""
        if not self.is_online:
            self._sync_status = "pending_offline"
            return 0

        queue = self._load_queue()
        pending = [q for q in queue if q.get("status") == "pending"]
        if not pending:
            self._sync_status = "synced"
            return 0

        now_iso = datetime.now(UTC).isoformat()
        synced_count = 0

        with self.db.connect() as conn:
            if any(item['payload'].get('source') == 'voice' for item in pending):
                conn.execute('BEGIN IMMEDIATE')
            for item in pending:
                cid = item["client_event_id"]
                etype = item["event_type"]
                payload = item["payload"]
                if payload.get('source') == 'voice' and cid != voice_command_id:
                    item['status'], item['error'] = 'failed', 'Lệnh voice cũ không được phát lại'
                    continue
                occ_at = item["occurred_at"]
                pat_id = item["patient_id"]

                # 1. Check sync_inbox idempotency
                existing = conn.fetchone("SELECT * FROM sync_inbox WHERE client_event_id = ?", (cid,))
                if existing:
                    item["status"] = "processed"
                    item["server_received_at"] = existing["received_at"]
                    item["canonical_event_id"] = existing["canonical_event_id"]
                    synced_count += 1
                    continue

                canonical_id = f"evt_{uuid.uuid4().hex[:16]}"

                # 2. Execute business transaction
                if etype in ("medication.confirm", "medication.dose_changed"):
                    occ_id = payload.get("occurrence_id")
                    action = payload.get("action", "self_reported_taken")
                    occ = conn.fetchone("SELECT * FROM dose_occurrences WHERE occurrence_id = ? AND patient_id = ?", (occ_id, pat_id))
                    if payload.get('source') == 'voice':
                        current = conn.fetchone("""
                            SELECT d.*, p.version AS prescription_version, p.status AS prescription_status
                            FROM dose_occurrences d
                            JOIN medication_schedules s ON d.schedule_id=s.schedule_id AND d.revision=s.revision
                            LEFT JOIN prescription_items pi ON s.prescription_item_id=pi.prescription_item_id
                            LEFT JOIN prescriptions p ON pi.prescription_id=p.prescription_id
                            WHERE d.occurrence_id=? AND d.patient_id=? AND s.is_active=1
                              AND (p.status IS NULL OR p.status='ACTIVE')
                  AND (pi.status IS NULL OR pi.status='ACTIVE')
                        """, (occ_id, pat_id))
                        requested = payload.get('snooze_minutes')
                        if action=='snooze' and requested is not None and (type(requested) is not int or not 1<=requested<=30):
                            item['status'], item['error'] = 'failed', 'Thời gian hoãn ngoài giới hạn 1–30 phút'
                            continue
                        if action == 'snooze' and occ and (occ['state']=='OVERDUE' or occ['snooze_count']>=3 or occ['grace_deadline_utc']<=occ_at):
                            item['status'], item['error'] = 'failed', 'Lượt thuốc quá hạn hoặc đã hoãn tối đa 3 lần'
                            continue
                        if (current != payload.get('expected_context') or not occ or
                            occ['state'] not in ('REMINDING', 'SNOOZED', 'OVERDUE') or
                            action not in ('self_reported_taken', 'snooze', 'help_requested', 'not_taken')):
                            item['status'], item['error'] = 'failed', 'Lần uống/ngữ cảnh đã thay đổi'
                            continue
                    if occ:
                        is_late = False
                        actor_str = pat_id
                        if action == "self_reported_taken":
                            new_state = "CONFIRMED"
                            actor_str = "self_reported_taken"
                            if occ.get("grace_deadline_utc") and occ_at > occ["grace_deadline_utc"]:
                                is_late = True
                                note_str = f"One-touch: self_reported_taken (confirmed_late at {occ_at})"
                                actor_str = "confirmed_late"
                            else:
                                note_str = f"One-touch: self_reported_taken (clicked at {occ_at})"
                            ev_type = "self_reported_taken"
                        elif action == "snooze":
                            new_state = "SNOOZED"
                            note_str = f"One-touch: snooze (clicked at {occ_at})"
                            ev_type = "snoozed"
                        elif action == 'not_taken' and payload.get('source')=='voice':
                            new_state = occ['state']
                            note_str = 'voice_self_report_not_taken'
                            ev_type = 'reminded'  # No dose_event is created for this response (outbox audit below).
                        elif action == "help_requested":
                            new_state = "REMINDING"
                            note_str = f"One-touch: help_requested (cụ bấm cần hỗ trợ lúc {occ_at})"
                            ev_type = "help_requested"
                        else:
                            new_state = "REMINDING"
                            note_str = f"One-touch: {action} (clicked at {occ_at})"
                            ev_type = "self_reported_taken"

                        conn.execute(
                            """
                            UPDATE dose_occurrences
                            SET state = ?, action_taken_at_utc = ?, action_actor = ?, action_notes = ?
                            WHERE occurrence_id = ?
                            """,
                            (new_state, occ_at, actor_str, note_str, occ_id),
                        )
                        if action == 'snooze' and payload.get('source') == 'voice':
                            schedule = conn.fetchone('SELECT reminder_delay_minutes FROM medication_schedules WHERE schedule_id=? AND revision=?', (occ['schedule_id'],occ['revision']))
                            until = (datetime.fromisoformat(occ_at) + timedelta(minutes=payload.get('snooze_minutes',schedule['reminder_delay_minutes']))).isoformat()
                            conn.execute('UPDATE dose_occurrences SET snooze_count=snooze_count+1, snoozed_until_utc=? WHERE occurrence_id=?', (until,occ_id))
                        if payload.get('source')=='voice' and action=='self_reported_taken':
                            payload['report_source']='voice_self_report'
                        if new_state == "CONFIRMED":
                            conn.execute(
                                """
                                UPDATE alerts
                                SET status = 'RESOLVED', resolved_at_utc = ?, resolution_notes = 'Người già đã xác nhận uống thuốc'
                                WHERE occurrence_id = ? AND status = 'NEW'
                                """,
                                (now_iso, occ_id),
                            )
                        if action != 'not_taken':
                            conn.execute(
                                """
                                INSERT INTO dose_events (event_id, occurrence_id, event_type, actor_id, actor_role, timestamp_utc, idempotency_key, metadata)
                                VALUES (?, ?, ?, ?, 'patient', ?, ?, ?)
                                """,
                                (canonical_id, occ_id, ev_type, pat_id, occ_at, cid, json.dumps(payload)),
                            )
                        insert_outbox_event(
                            conn=conn,
                            patient_id=pat_id,
                            event_type="medication.dose_changed",
                            aggregate_type="dose_occurrence",
                            aggregate_id=occ_id,
                            aggregate_version=1,
                            occurred_at=now_iso,
                            actor_id=pat_id,
                            source=payload.get("source", "elder_app"),
                            payload_minimal={"occurrence_id": occ_id, "state": new_state, "action": action, "is_late": is_late, "client_occurred_at": occ_at},
                            event_id=canonical_id,
                        )

                        if action == "help_requested":
                            alt_id = f"alt_{uuid.uuid4().hex[:16]}"
                            conn.execute(
                                """
                                INSERT INTO alerts (alert_id, occurrence_id, patient_id, alert_type, status, created_at_utc)
                                VALUES (?, ?, ?, 'HELP_REQUESTED', 'NEW', ?)
                                ON CONFLICT(occurrence_id, alert_type) DO NOTHING
                                """,
                                (alt_id, occ_id, pat_id, now_iso),
                            )
                            insert_outbox_event(
                                conn=conn,
                                patient_id=pat_id,
                                event_type="medication.alert_created",
                                aggregate_type="alert",
                                aggregate_id=alt_id,
                                aggregate_version=1,
                                occurred_at=now_iso,
                                actor_id=pat_id,
                                source="elder_app",
                                payload_minimal={"alert_id": alt_id, "occurrence_id": occ_id, "alert_type": "HELP_REQUESTED"},
                            )

                elif etype == "rehab.rep":
                    sess_id = payload.get("session_id")
                    rep_num = int(payload.get("rep_number", 1))
                    score = float(payload.get("score", 95.0))
                    ex_id = payload.get("exercise_id", "arm_raise")
                    sess = conn.fetchone("SELECT * FROM rehab_sessions WHERE session_id = ? AND patient_id = ?", (sess_id, pat_id))
                    if sess:
                        conn.execute(
                            """
                            INSERT INTO rehab_events (event_id, session_id, patient_id, exercise_id, rep_number, score, timestamp_utc, idempotency_key, metadata)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                            """,
                            (canonical_id, sess_id, pat_id, ex_id, rep_num, score, occ_at, cid, json.dumps(payload)),
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
                            (new_count, new_status, ended_at, sess_id),
                        )
                        insert_outbox_event(
                            conn=conn,
                            patient_id=pat_id,
                            event_type="rehab.session_updated",
                            aggregate_type="rehab_session",
                            aggregate_id=sess_id,
                            aggregate_version=new_count,
                            occurred_at=now_iso,
                            actor_id=None,
                            source="camera_ai",
                            payload_minimal={"session_id": sess_id, "verified_count": new_count, "target_count": sess["target_count_snapshot"], "status": new_status},
                            event_id=canonical_id,
                        )

                elif etype == "mood.checkin":
                    rating = int(payload.get("rating", 3))
                    note = payload.get("note", "")
                    conn.execute(
                        """
                        INSERT INTO mood_checkins (checkin_id, patient_id, self_rating, note, recorded_at, recorded_by)
                        VALUES (?, ?, ?, ?, ?, ?)
                        """,
                        (canonical_id, pat_id, rating, note, occ_at, pat_id),
                    )
                    insert_outbox_event(
                        conn=conn,
                        patient_id=pat_id,
                        event_type="mood.checkin_created",
                        aggregate_type="mood_checkin",
                        aggregate_id=canonical_id,
                        aggregate_version=1,
                        occurred_at=now_iso,
                        actor_id=pat_id,
                        source="elder_app",
                        payload_minimal={"checkin_id": canonical_id, "self_rating": rating},
                        event_id=canonical_id,
                    )

                # 3. Insert into sync_inbox
                conn.execute(
                    """
                    INSERT INTO sync_inbox (client_event_id, patient_id, source_device_id, received_at, status, canonical_event_id, error_code)
                    VALUES (?, ?, 'elder_desktop', ?, 'PROCESSED', ?, NULL)
                    """,
                    (cid, pat_id, now_iso, canonical_id),
                )

                item["status"] = "processed"
                item["server_received_at"] = now_iso
                item["canonical_event_id"] = canonical_id
                synced_count += 1

            conn.commit()

        self._save_queue(queue)
        self._sync_status = "synced"
        self._last_synced_at = now_iso
        return synced_count

    # =========================================================================
    # CONVENIENCE METHODS
    # =========================================================================
    def confirm_dose_taken(self, occurrence_id: str, action: str = "self_reported_taken", client_event_id: Optional[str] = None,
                           expected_context: Optional[Dict[str, Any]] = None,
                           source: str = "elder_app", voice_snooze_minutes: Optional[int] = None) -> Dict[str, Any]:
        """Shared button/voice entrypoint; voice includes a transaction snapshot."""
        return self.enqueue_event(
            event_type="medication.confirm",
            payload={"occurrence_id": occurrence_id, "action": action,
                     **({'expected_context': expected_context, 'source': source,
                         **({'snooze_minutes': voice_snooze_minutes} if voice_snooze_minutes is not None else {})} if source == 'voice' else {})},
            client_event_id=client_event_id,
        )

    def record_mood(self, rating: int, note: str = "", client_event_id: Optional[str] = None) -> Dict[str, Any]:
        """Optional self-reported mood."""
        return self.enqueue_event(
            event_type="mood.checkin",
            payload={"rating": rating, "note": note},
            client_event_id=client_event_id,
        )

    def mark_camera_issue(self, session_id: str, reason: str = "Tôi đã tập nhưng camera không nhận") -> Dict[str, Any]:
        """Report observation failure without false completed mark."""
        now_iso = datetime.now(UTC).isoformat()
        with self.db.connect() as conn:
            conn.execute(
                "UPDATE rehab_sessions SET status = 'verification_unavailable', notes = ? WHERE session_id = ?",
                (reason, session_id),
            )
            insert_outbox_event(
                conn=conn,
                patient_id=self.patient_id,
                event_type="rehab.session_updated",
                aggregate_type="rehab_session",
                aggregate_id=session_id,
                aggregate_version=1,
                occurred_at=now_iso,
                actor_id=self.patient_id,
                source="elder_app",
                payload_minimal={"session_id": session_id, "status": "verification_unavailable", "reason": reason},
            )
            conn.commit()
        return {"session_id": session_id, "status": "verification_unavailable"}

    def mark_recommendation_read(self, rec_id: str) -> Dict[str, Any]:
        now_iso = datetime.now(UTC).isoformat()
        with self.db.connect() as conn:
            p = conn.fetchone("SELECT user_id FROM patients WHERE patient_id = ?", (self.patient_id,))
            uid = p["user_id"] if p and p.get("user_id") else self.patient_id
            receipt_id = f"rcp_{uuid.uuid4().hex[:12]}"
            conn.execute(
                """
                INSERT INTO recommendation_receipts (receipt_id, recommendation_id, user_id, read_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(recommendation_id, user_id) DO UPDATE SET read_at = ?
                """,
                (receipt_id, rec_id, uid, now_iso, now_iso),
            )
            insert_outbox_event(
                conn=conn,
                patient_id=self.patient_id,
                event_type="recommendation.read",
                aggregate_type="care_recommendation",
                aggregate_id=rec_id,
                aggregate_version=1,
                occurred_at=now_iso,
                actor_id=self.patient_id,
                source="elder_app",
                payload_minimal={"recommendation_id": rec_id, "read_at": now_iso},
            )
            conn.commit()
        return {"recommendation_id": rec_id, "status": "read"}

    def get_today_snapshot(self) -> Dict[str, Any]:
        """Fetch today's consolidated schedule, rehab, advice, and vitals."""
        with self.db.connect() as conn:
            p = conn.fetchone("SELECT * FROM patients WHERE patient_id = ?", (self.patient_id,))
            pat_tz_str = p["timezone"] if p and p.get("timezone") else "Asia/Ho_Chi_Minh"
            try:
                import zoneinfo
                tz = zoneinfo.ZoneInfo(pat_tz_str)
                today_date = datetime.now(tz).date().isoformat()
            except Exception:
                today_date = date.today().isoformat()

            # Doses (strictly active prescriptions and non-cancelled occurrences)
            doses = conn.fetchall(
                """
                SELECT d.*, p.status as prescription_status, p.prescription_code
                FROM dose_occurrences d
                JOIN medication_schedules s ON d.schedule_id = s.schedule_id
                JOIN prescription_items pi ON s.prescription_item_id = pi.prescription_item_id
                JOIN prescriptions p ON pi.prescription_id = p.prescription_id
                WHERE d.patient_id = ?
                  AND d.local_scheduled_time LIKE ?
                  AND p.status = 'ACTIVE'
                  AND d.state != 'CANCELLED'
                ORDER BY d.local_scheduled_time ASC
                """,
                (self.patient_id, f"{today_date}%"),
            )
            # Rehab
            rehab_sess = conn.fetchone(
                """
                SELECT rs.*, rp.exercise_name, rp.target_count as plan_target, rp.local_reminder_time
                FROM rehab_sessions rs
                JOIN rehab_plans rp ON rs.plan_id = rp.plan_id
                WHERE rs.patient_id = ? AND rs.local_date = ?
                """,
                (self.patient_id, today_date),
            )
            if not rehab_sess:
                plan = conn.fetchone(
                    """
                    SELECT * FROM rehab_plans
                    WHERE patient_id = ? AND is_active = 1
                    ORDER BY local_reminder_time ASC LIMIT 1
                    """,
                    (self.patient_id,),
                )
                if plan:
                    new_sess_id = f"rs_{self.patient_id}_{today_date.replace('-', '')}"
                    conn.execute(
                        """
                        INSERT INTO rehab_sessions (
                            session_id, patient_id, plan_id, plan_version, local_date,
                            started_at, ended_at, target_count_snapshot, verified_count,
                            completion_source, status, algorithm_version, notes
                        ) VALUES (?, ?, ?, ?, ?, NULL, NULL, ?, 0, 'camera', 'pending', 'MediaPipe-Pose-FSM-v1.0', 'Tự động lên lịch theo kế hoạch 16:00')
                        ON CONFLICT(session_id) DO NOTHING
                        """,
                        (new_sess_id, self.patient_id, plan["plan_id"], plan["version"], today_date, plan["target_count"]),
                    )
                    conn.commit()
                    rehab_sess = conn.fetchone(
                        """
                        SELECT rs.*, rp.exercise_name, rp.target_count as plan_target, rp.local_reminder_time
                        FROM rehab_sessions rs
                        JOIN rehab_plans rp ON rs.plan_id = rp.plan_id
                        WHERE rs.session_id = ?
                        """,
                        (new_sess_id,),
                    )
            # Recommendations
            recs = conn.fetchall(
                """
                SELECT cr.*, u.full_name as doctor_name
                FROM care_recommendations cr
                JOIN clinicians cl ON cr.clinician_id = cl.clinician_id
                JOIN users u ON cl.user_id = u.user_id
                WHERE cr.patient_id = ? AND cr.status = 'ACTIVE'
                ORDER BY cr.created_at DESC
                """,
                (self.patient_id,),
            )
            # Recent vitals
            bp = conn.fetchone(
                """
                SELECT ho_sys.value_numeric as systolic, ho_dia.value_numeric as diastolic,
                       mg.measured_at, mg.entered_by_user_id, u.full_name as entered_by_name
                FROM measurement_groups mg
                JOIN health_observations ho_sys ON mg.group_id = ho_sys.group_id
                JOIN health_observations ho_dia ON mg.group_id = ho_dia.group_id
                JOIN observation_types ot_sys ON ho_sys.observation_type_id = ot_sys.observation_type_id AND ot_sys.code IN ('blood_pressure_systolic', 'bp_systolic')
                JOIN observation_types ot_dia ON ho_dia.observation_type_id = ot_dia.observation_type_id AND ot_dia.code IN ('blood_pressure_diastolic', 'bp_diastolic')
                LEFT JOIN users u ON mg.entered_by_user_id = u.user_id
                WHERE mg.patient_id = ? AND mg.status = 'active'
                ORDER BY mg.measured_at DESC LIMIT 1
                """,
                (self.patient_id,),
            )
            # Measurement requests (pending for today)
            meas_reqs = conn.fetchall(
                """
                SELECT mr.*, ot.name as observation_name, ot.standard_unit as unit, u.full_name as requester_name
                FROM measurement_requests mr
                LEFT JOIN observation_types ot ON mr.observation_type_id = ot.observation_type_id
                LEFT JOIN users u ON mr.requested_by_user_id = u.user_id
                WHERE mr.patient_id = ? AND mr.status = 'PENDING'
                ORDER BY mr.due_at_utc ASC
                """,
                (self.patient_id,),
            )
            return {
                "patient_id": self.patient_id,
                "today_date": today_date,
                "doses": doses,
                "rehab_session": rehab_sess,
                "recommendations": recs,
                "latest_blood_pressure": bp,
                "measurement_requests": meas_reqs,
            }
