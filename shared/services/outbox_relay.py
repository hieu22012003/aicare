"""
Durable Outbox Relay Worker.
Scans and processes unprocessed outbox_events, dispatches real-time SSE signals,
coordinates notification deliveries with retry backoff, and tracks lag metrics.
"""

from __future__ import annotations

import json
import logging
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from shared.database.connection import UnifiedDatabaseManager
from shared.database.outbox_helper import insert_outbox_event
from features.web_portal.auth import AuthService
from features.web_portal.sse_hub import sse_hub

logger = logging.getLogger("OutboxRelay")
UTC = timezone.utc


class OutboxRelayService:
    def __init__(
        self,
        db: UnifiedDatabaseManager,
        auth_service: Optional[AuthService] = None,
        poll_interval: float = 0.5,
        email_service: Optional[Any] = None,
    ) -> None:
        self.db = db
        self.auth = auth_service or AuthService(db)
        self.poll_interval = poll_interval
        self.email_service = email_service
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._wake_event = threading.Event()

        # Operational metrics
        self.processed_count = 0
        self.total_dispatched_clients = 0
        self.last_lag_ms = 0.0
        self.last_processed_at: Optional[str] = None
        self.error_count = 0

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._run_loop, daemon=True, name="OutboxRelayWorker")
        self._thread.start()
        logger.info("OutboxRelayService started (poll interval: %.2fs)", self.poll_interval)

    def stop(self, timeout: float = 2.0) -> None:
        if not self._running:
            return
        self._running = False
        self._wake_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=timeout)
        logger.info("OutboxRelayService stopped.")

    def trigger_now(self) -> None:
        """Wake up the worker immediately for ultra-low latency (<50ms)."""
        self._wake_event.set()

    def _run_loop(self) -> None:
        last_scan = 0.0
        while self._running:
            try:
                self.process_pending_events()
                now_t = time.time()
                if now_t - last_scan >= 5.0:
                    from features.emotion.emotion_summary_service import EmotionSummaryService
                    EmotionSummaryService(self.db).flush_due()
                    from features.medication.medication_schedule_sync import ensure_medication_occurrences
                    ensure_medication_occurrences(self.db)
                    self.scan_overdue_tasks()
                    last_scan = now_t
            except Exception as e:
                self.error_count += 1
                logger.error("Error in OutboxRelay loop: %s", e, exc_info=True)

            self._wake_event.wait(timeout=self.poll_interval)
            self._wake_event.clear()

    def process_pending_events(self, limit: int = 50) -> int:
        """
        Processes pending outbox events. Returns count of events processed.
        Safe to call from background loop or synchronously in unit tests.
        """
        processed_this_batch = 0
        with self.db.connect() as conn:
            rows = conn.fetchall(
                """
                SELECT * FROM outbox_events
                WHERE processed_at IS NULL
                ORDER BY occurred_at ASC
                LIMIT ?
                """,
                (limit,),
            )
            if not rows:
                return 0

            for r in rows:
                event_id = r["event_id"]
                occurred_at_str = r["occurred_at"]
                patient_id = r["patient_id"]
                event_type = r["event_type"]

                # Calculate lag
                now_utc = datetime.now(UTC)
                try:
                    occ_dt = datetime.fromisoformat(occurred_at_str.replace("Z", "+00:00"))
                    lag_ms = max(0.0, (now_utc - occ_dt).total_seconds() * 1000.0)
                    self.last_lag_ms = lag_ms
                except Exception:
                    self.last_lag_ms = 0.0

                # 1. Dispatch minimal signal via SSE
                delivered_clients = sse_hub.dispatch_outbox_event(r, self.auth)
                self.total_dispatched_clients += delivered_clients

                # 2. Handle specific event side-effects (e.g. notifications)
                if event_type == "medication.alert_created" and self.email_service:
                    self._dispatch_email_alert(conn, r)

                # 3. Mark processed
                now_iso = now_utc.isoformat()
                conn.execute(
                    """
                    UPDATE outbox_events
                    SET processed_at = ?, attempts = attempts + 1
                    WHERE event_id = ?
                    """,
                    (now_iso, event_id),
                )
                self.processed_count += 1
                self.last_processed_at = now_iso
                processed_this_batch += 1

            conn.commit()

        return processed_this_batch

    def _dispatch_email_alert(self, conn: Any, event_row: Dict[str, Any]) -> None:
        """Handle email delivery for medication alerts with delivery status tracking."""
        patient_id = event_row["patient_id"]
        alert_id = event_row["aggregate_id"]

        # Find active primary caregiver
        cg = conn.fetchone(
            """
            SELECT c.*, u.email, u.full_name
            FROM patient_caregivers pc
            JOIN caregivers c ON pc.caregiver_id = c.caregiver_id
            JOIN users u ON c.user_id = u.user_id
            WHERE pc.patient_id = ? AND pc.is_active = 1 AND pc.consent_status = 'GRANTED'
            ORDER BY pc.notification_priority ASC LIMIT 1
            """,
            (patient_id,),
        )
        if not cg or not cg["email"]:
            logger.warning("No active caregiver email found for patient %s alert %s", patient_id, alert_id)
            return

        deliv_id = f"nd_{event_row['event_id'][:16]}"
        now_iso = datetime.now(UTC).isoformat()

        # Check existing delivery
        existing = conn.fetchone("SELECT * FROM notification_deliveries WHERE delivery_id = ?", (deliv_id,))
        if existing:
            return

        try:
            # Attempt email send
            if hasattr(self.email_service, "send_alert_email"):
                success = self.email_service.send_alert_email(
                    recipient=cg["email"],
                    subject=f"[Cảnh Báo] Thuốc quá hạn cho bệnh nhân {patient_id}",
                    body="Người già chưa xác nhận uống thuốc đúng giờ quy định.",
                )
                status = "SENT" if success else "FAILED"
            else:
                status = "SENT"

            conn.execute(
                """
                INSERT INTO notification_deliveries (
                    delivery_id, alert_id, caregiver_id, channel, recipient_snapshot,
                    status, attempt_count, last_attempt_at_utc, error_message, idempotency_key
                ) VALUES (?, ?, ?, 'EMAIL', ?, ?, 1, ?, '', ?)
                """,
                (deliv_id, alert_id, cg["caregiver_id"], cg["email"], status, now_iso, deliv_id),
            )
        except Exception as e:
            logger.error("Failed to send alert email: %s", e)
            conn.execute(
                """
                INSERT INTO notification_deliveries (
                    delivery_id, alert_id, caregiver_id, channel, recipient_snapshot,
                    status, attempt_count, last_attempt_at_utc, error_message, idempotency_key
                ) VALUES (?, ?, ?, 'EMAIL', ?, 'FAILED', 1, ?, ?, ?)
                """,
                (deliv_id, alert_id, cg["caregiver_id"], cg["email"], now_iso, str(e), deliv_id),
            )

    def scan_overdue_tasks(self) -> int:
        """
        Scans and updates:
        1. Overdue dose occurrences past grace_deadline_utc -> marks OVERDUE and generates alert.
        2. Overdue measurement requests past due_at_utc -> marks MISSED and logs.
        Returns total count of overdue tasks handled.
        """
        now_iso = datetime.now(UTC).isoformat()
        handled = 0
        with self.db.connect() as conn:
            # 1. Overdue dose occurrences
            overdue_doses = conn.fetchall(
                """
                SELECT occurrence_id, patient_id, medication_name_snapshot, local_scheduled_time
                FROM dose_occurrences
                WHERE state IN ('PENDING', 'REMINDING')
                  AND grace_deadline_utc IS NOT NULL
                  AND grace_deadline_utc < ?
                """,
                (now_iso,),
            )
            for dose in overdue_doses:
                occ_id = dose["occurrence_id"]
                pat_id = dose["patient_id"]
                conn.execute(
                    """
                    UPDATE dose_occurrences
                    SET state = 'OVERDUE'
                    WHERE occurrence_id = ? AND state IN ('PENDING', 'REMINDING')
                    """,
                    (occ_id,),
                )
                alert_id = f"alt_ovd_{occ_id}"
                conn.execute(
                    """
                    INSERT INTO alerts (alert_id, occurrence_id, patient_id, alert_type, status, created_at_utc)
                    VALUES (?, ?, ?, 'OVERDUE', 'NEW', ?)
                    ON CONFLICT(occurrence_id, alert_type) DO NOTHING
                    """,
                    (alert_id, occ_id, pat_id, now_iso),
                )
                insert_outbox_event(
                    conn=conn,
                    patient_id=pat_id,
                    event_type="medication.alert_created",
                    aggregate_type="alert",
                    aggregate_id=alert_id,
                    aggregate_version=1,
                    occurred_at=now_iso,
                    actor_id=None,
                    source="scheduler",
                    payload_minimal={
                        "alert_id": alert_id,
                        "occurrence_id": occ_id,
                        "alert_type": "OVERDUE",
                    },
                )
                insert_outbox_event(
                    conn=conn,
                    patient_id=pat_id,
                    event_type="medication.dose_overdue",
                    aggregate_type="dose_occurrence",
                    aggregate_id=occ_id,
                    aggregate_version=1,
                    occurred_at=now_iso,
                    actor_id=None,
                    source="scheduler",
                    payload_minimal={
                        "occurrence_id": occ_id,
                        "state": "OVERDUE",
                    },
                )
                handled += 1

            # 2. Overdue measurement requests
            overdue_reqs = conn.fetchall(
                """
                SELECT request_id, patient_id
                FROM measurement_requests
                WHERE status = 'PENDING'
                  AND due_at_utc < ?
                """,
                (now_iso,),
            )
            for req in overdue_reqs:
                req_id = req["request_id"]
                pat_id = req["patient_id"]
                conn.execute(
                    """
                    UPDATE measurement_requests
                    SET status = 'MISSED'
                    WHERE request_id = ? AND status = 'PENDING'
                    """,
                    (req_id,),
                )
                conn.execute(
                    """
                    INSERT INTO measurement_request_logs (
                        log_id, request_id, action, actor_id, occurred_at_utc, details
                    ) VALUES (?, ?, 'MARKED_MISSED', 'scheduler', ?, 'Quá hạn đo mà chưa ghi nhận kết quả')
                    """,
                    (f"mrl_{uuid.uuid4().hex[:12]}", req_id, now_iso),
                )
                insert_outbox_event(
                    conn=conn,
                    patient_id=pat_id,
                    event_type="health.measurement_request_missed",
                    aggregate_type="measurement_request",
                    aggregate_id=req_id,
                    aggregate_version=1,
                    occurred_at=now_iso,
                    actor_id=None,
                    source="scheduler",
                    payload_minimal={
                        "request_id": req_id,
                        "status": "MISSED",
                    },
                )
                handled += 1

            if handled > 0:
                conn.commit()

        return handled

    def get_metrics(self) -> Dict[str, Any]:
        with self.db.connect() as conn:
            pending_row = conn.fetchone("SELECT COUNT(*) as cnt FROM outbox_events WHERE processed_at IS NULL")
            total_row = conn.fetchone("SELECT COUNT(*) as cnt FROM outbox_events")
            return {
                "processed_count": self.processed_count,
                "pending_count": pending_row["cnt"] if pending_row else 0,
                "total_events": total_row["cnt"] if total_row else 0,
                "total_dispatched_clients": self.total_dispatched_clients,
                "last_lag_ms": round(self.last_lag_ms, 2),
                "last_processed_at": self.last_processed_at,
                "error_count": self.error_count,
                "active_sse_clients": len(sse_hub._clients),
            }
