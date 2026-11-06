"""
Section 6A Rehabilitation Session & Idempotent Event Adapter.
Connects Vision Repetition Counting to Database Sessions.
Enforces:
1. Local reminder time 16:00 per patient timezone.
2. Target count snapshot locked at session start.
3. Event idempotency key per repetition (prevents duplicate counting on retransmission).
4. Atomic transition: 'in_progress' -> 'camera_verified_completed' when verified_count >= target_count_snapshot.
5. Incomplete / Verification Unavailable tracking when camera view is lost or occluded.
6. Unified session reports for both patient and clinician.
"""

from __future__ import annotations

import logging
import uuid
from datetime import date, datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from shared.database.connection import UnifiedDatabaseManager
from shared.database.outbox_helper import insert_outbox_event

logger = logging.getLogger("RehabSessionAdapter")

DEFAULT_REHAB_EXERCISES = [
    {
        "exercise_id": "arm_raise",
        "exercise_name": "Nâng tay qua đầu (Vươn vai)",
        "target_count": 10,
        "local_reminder_time": "16:00",
    },
    {
        "exercise_id": "sit_to_stand",
        "exercise_name": "Đứng lên ngồi xuống (Co duỗi chân)",
        "target_count": 10,
        "local_reminder_time": "16:00",
    },
    {
        "exercise_id": "marching",
        "exercise_name": "Đi bộ tại chỗ (Nâng cao đùi)",
        "target_count": 15,
        "local_reminder_time": "16:00",
    },
]


def ensure_default_rehab_plans(
    conn: Any, patient_id: str, default_time: str = "16:00"
) -> List[Dict[str, Any]]:
    """
    Ensure the 3 standard rehabilitation exercises exist for a patient in rehab_plans.
    If absent, seeds them with default targets and reminder time.
    """
    now_iso = datetime.now(timezone.utc).isoformat()
    created_or_existing = []

    for item in DEFAULT_REHAB_EXERCISES:
        ex_id = item["exercise_id"]
        row = conn.fetchone(
            """
            SELECT * FROM rehab_plans
            WHERE patient_id = ? AND exercise_id = ? AND is_active = 1
            ORDER BY version DESC LIMIT 1
            """,
            (patient_id, ex_id),
        )
        if row:
            created_or_existing.append(dict(row))
            continue

        plan_id = f"rp_{patient_id}_{ex_id}"
        ex_name = item["exercise_name"]
        target = item["target_count"]
        rem_time = default_time or item["local_reminder_time"]

        conn.execute(
            """
            INSERT INTO rehab_plans (
                plan_id, patient_id, exercise_id, exercise_name, target_count,
                local_reminder_time, timezone, version, is_active, effective_from
            ) VALUES (?, ?, ?, ?, ?, ?, 'Asia/Ho_Chi_Minh', 1, 1, ?)
            ON CONFLICT(plan_id) DO UPDATE SET
                is_active = 1, target_count = excluded.target_count,
                local_reminder_time = excluded.local_reminder_time
            """,
            (plan_id, patient_id, ex_id, ex_name, target, rem_time, now_iso),
        )

        pat = conn.fetchone("SELECT user_id FROM patients WHERE patient_id = ?", (patient_id,))
        creator_id = pat["user_id"] if pat and pat.get("user_id") else None
        if not creator_id:
            u = conn.fetchone("SELECT user_id FROM users LIMIT 1")
            creator_id = u["user_id"] if u else "user_doc_001"

        pv_id = f"epv_{plan_id}_v1"
        conn.execute(
            """
            INSERT INTO exercise_plan_versions (
                plan_version_id, plan_id, patient_id, version, target_count,
                quality_threshold, local_reminder_time, timezone, effective_from,
                created_by_user_id, created_at, reason
            ) VALUES (?, ?, ?, 1, ?, 80.0, ?, 'Asia/Ho_Chi_Minh', ?, ?, ?, 'Khởi tạo kế hoạch bài tập tiêu chuẩn')
            ON CONFLICT(plan_id, version) DO NOTHING
            """,
            (pv_id, plan_id, patient_id, target, rem_time, now_iso, creator_id, now_iso),
        )

        created_or_existing.append({
            "plan_id": plan_id,
            "patient_id": patient_id,
            "exercise_id": ex_id,
            "exercise_name": ex_name,
            "target_count": target,
            "local_reminder_time": rem_time,
            "version": 1,
            "is_active": 1,
        })

    return created_or_existing


class RehabSessionAdapter:
    def __init__(self, db: UnifiedDatabaseManager) -> None:
        self.db = db

    def get_or_create_daily_session(self, patient_id: str, exercise_id: str = "arm_raise", expected_plan: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """
        Get or initialize today's rehab session for patient and exercise.
        Locks target_count_snapshot from active rehab_plan.
        """
        today_date = date.today().isoformat()
        now_iso = datetime.now(timezone.utc).isoformat()

        with self.db.connect() as conn:
            if expected_plan is not None:
                conn.execute('BEGIN IMMEDIATE')
            # 1. Lookup active plan for patient & exercise
            plan = conn.fetchone(
                """
                SELECT * FROM rehab_plans
                WHERE patient_id = ? AND exercise_id = ? AND is_active = 1
                ORDER BY version DESC LIMIT 1
                """,
                (patient_id, exercise_id),
            )
            if expected_plan is not None and (not plan or any(
                    plan.get(key) != expected_plan.get(key) for key in ('plan_id', 'version', 'target_count', 'is_active'))):
                raise ValueError('Kế hoạch bài tập đã đổi; hãy chọn lại bài tập')
            if not plan:
                ensure_default_rehab_plans(conn, patient_id)
                conn.commit()
                plan = conn.fetchone(
                    """
                    SELECT * FROM rehab_plans
                    WHERE patient_id = ? AND exercise_id = ? AND is_active = 1
                    ORDER BY version DESC LIMIT 1
                    """,
                    (patient_id, exercise_id),
                )


            # 2. Lookup existing session today
            session = conn.fetchone(
                """
                SELECT rs.*, rp.exercise_name
                FROM rehab_sessions rs
                JOIN rehab_plans rp ON rs.plan_id = rp.plan_id
                WHERE rs.patient_id = ? AND rs.plan_id = ? AND rs.local_date = ?
                """,
                (patient_id, plan["plan_id"], today_date),
            )
            if session:
                if expected_plan is not None and (session['plan_version'] != plan['version'] or
                        session['status'] == 'camera_verified_completed'):
                    raise ValueError('Phiên tập đã đổi hoặc hoàn thành; hãy dùng nút bấm')
                return dict(session)

            # 3. Create fresh session locking target_count_snapshot
            session_id = f"rs_{uuid.uuid4().hex[:16]}"
            target_snapshot = plan["target_count"]
            conn.execute(
                """
                INSERT INTO rehab_sessions (
                    session_id, patient_id, plan_id, plan_version, local_date,
                    started_at, target_count_snapshot, verified_count, completion_source,
                    status, algorithm_version, notes
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 0, 'camera', 'pending', 'MediaPipe-Pose-FSM-v1.0', '')
                """,
                (session_id, patient_id, plan["plan_id"], plan["version"], today_date, now_iso, target_snapshot),
            )
            conn.commit()

            new_sess = conn.fetchone("SELECT * FROM rehab_sessions WHERE session_id = ?", (session_id,))
            return dict(new_sess)

    def record_verified_rep(
        self, session_id: str, rep_number: int, score: float = 95.0, idempotency_key: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Record a single valid repetition event identified by camera AI.
        Guarantees strict idempotency and atomic status transition.
        """
        now_iso = datetime.now(timezone.utc).isoformat()

        with self.db.connect() as conn:
            session = conn.fetchone("SELECT * FROM rehab_sessions WHERE session_id = ?", (session_id,))
            if not session:
                raise ValueError(f"Session not found: {session_id}")

            target_snapshot = session["target_count_snapshot"]
            plan_id = session["plan_id"]
            patient_id = session["patient_id"]

            plan = conn.fetchone("SELECT exercise_id FROM rehab_plans WHERE plan_id = ?", (plan_id,))
            exercise_id = plan["exercise_id"] if plan else "arm_raise"

            event_id = f"rev_{uuid.uuid4().hex[:16]}"
            ikey = idempotency_key or f"{session_id}_{exercise_id}_{rep_number}"

            # Check if this exact rep event already exists
            existing_event = conn.fetchone("SELECT * FROM rehab_events WHERE idempotency_key = ?", (ikey,))
            if existing_event:
                logger.info("Duplicate rep event rejected for key %s", ikey)
                return {
                    "event_id": existing_event["event_id"],
                    "is_duplicate": True,
                    "verified_count": session["verified_count"],
                    "target_count": target_snapshot,
                    "status": session["status"],
                }

            # Insert rep event
            conn.execute(
                """
                INSERT INTO rehab_events (event_id, session_id, patient_id, exercise_id, rep_number, score, timestamp_utc, idempotency_key, metadata)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, '{}')
                """,
                (event_id, session_id, patient_id, exercise_id, rep_number, score, now_iso, ikey),
            )

            # Increment count
            new_count = session["verified_count"] + 1
            new_status = session["status"]
            ended_at = session["ended_at"]

            if new_count >= target_snapshot:
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

            # Insert outbox event in the SAME transaction
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
                payload_minimal={
                    "session_id": session_id,
                    "verified_count": new_count,
                    "target_count": target_snapshot,
                    "status": new_status,
                    "rep_number": rep_number,
                },
                event_id=event_id,
            )

            conn.commit()

            return {
                "event_id": event_id,
                "is_duplicate": False,
                "verified_count": new_count,
                "target_count": target_snapshot,
                "status": new_status,
            }

    def mark_camera_unavailable(self, session_id: str, reason: str = "Góc nhìn bị khuất") -> Dict[str, Any]:
        """Mark session as verification_unavailable when camera view is lost."""
        now_iso = datetime.now(timezone.utc).isoformat()
        with self.db.connect() as conn:
            sess = conn.fetchone("SELECT patient_id, verified_count, target_count_snapshot FROM rehab_sessions WHERE session_id = ?", (session_id,))
            patient_id = sess["patient_id"] if sess else ""

            conn.execute(
                """
                UPDATE rehab_sessions
                SET status = 'verification_unavailable', notes = ?
                WHERE session_id = ?
                """,
                (reason, session_id),
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
                    source="camera_ai",
                    payload_minimal={
                        "session_id": session_id,
                        "status": "verification_unavailable",
                        "reason": reason,
                    },
                )

            conn.commit()
            updated = conn.fetchone("SELECT * FROM rehab_sessions WHERE session_id = ?", (session_id,))
            return dict(updated)

    def mark_manual_reviewed(self, session_id: str, reviewer_notes: str) -> Dict[str, Any]:
        """Mark session with manual_reviewed separate from camera_verified_completed."""
        with self.db.connect() as conn:
            conn.execute(
                """
                UPDATE rehab_sessions
                SET completion_source = 'manual_reviewed', status = 'manual_reviewed', notes = ?
                WHERE session_id = ?
                """,
                (reviewer_notes, session_id),
            )
            conn.commit()
            updated = conn.fetchone("SELECT * FROM rehab_sessions WHERE session_id = ?", (session_id,))
            return dict(updated)

    def generate_rehab_report(self, patient_id: str, days: int = 7) -> Dict[str, Any]:
        """Generate adherence summary report for patient and clinician."""
        since_date = (date.today() - datetime.timedelta(days=days)).isoformat() if hasattr(datetime, 'timedelta') else date.today().isoformat()
        with self.db.connect() as conn:
            sessions = conn.fetchall(
                """
                SELECT rs.*, rp.exercise_name
                FROM rehab_sessions rs
                JOIN rehab_plans rp ON rs.plan_id = rp.plan_id
                WHERE rs.patient_id = ?
                ORDER BY rs.local_date DESC LIMIT ?
                """,
                (patient_id, days),
            )
            total = len(sessions)
            completed = sum(1 for s in sessions if s["status"] == "camera_verified_completed")
            incomplete = sum(1 for s in sessions if s["status"] == "incomplete")
            unavailable = sum(1 for s in sessions if s["status"] == "verification_unavailable")

            return {
                "patient_id": patient_id,
                "days_evaluated": days,
                "total_scheduled_sessions": total,
                "camera_verified_completed": completed,
                "incomplete": incomplete,
                "verification_unavailable": unavailable,
                "completion_rate_pct": round((completed / max(1, total)) * 100, 1),
                "sessions": sessions,
            }

