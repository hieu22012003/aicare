"""Persistence of AI results in AICare's unified SQLite DB (migration 014) + outbox events.

Every write that the web portal should see is paired with an outbox event in the same
transaction; the existing OutboxRelay pushes it to authorised portals over SSE.
Event types: ai.posture.*, ai.fall.*, ai.objects.*, ai.medication.*, plus the existing
medication.dose_changed when the camera confirms a dose.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

from database.outbox_helper import insert_outbox_event

UTC = timezone.utc
SOURCE = "camera_ai"


def iso(ts: float | None) -> str | None:
    return None if ts is None else datetime.fromtimestamp(ts, UTC).isoformat()


def epoch(value: str | None) -> float | None:
    if not value:
        return None
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return (dt if dt.tzinfo else dt.replace(tzinfo=UTC)).timestamp()


class AICoreStore:
    def __init__(self, db) -> None:
        self.db = db

    def ready(self) -> bool:
        return self.db.table_exists("ai_settings")

    def _outbox(self, conn, patient_id: str, event_type: str, aggregate_type: str, aggregate_id: str, payload: dict) -> None:
        insert_outbox_event(conn, patient_id, event_type, aggregate_type, aggregate_id, source=SOURCE,
                            payload_minimal=payload)

    # ------------------------------------------------------------ settings

    def get_settings(self, patient_id: str, feature: str) -> dict:
        row = self.db.fetchone("SELECT config FROM ai_settings WHERE patient_id=? AND feature=?", (patient_id, feature))
        return json.loads(row["config"]) if row else {}

    def set_settings(self, patient_id: str, feature: str, config: dict, by: str | None) -> None:
        with self.db.connect() as conn:
            conn.execute(
                "INSERT INTO ai_settings(patient_id, feature, config, updated_at_utc, updated_by) VALUES (?,?,?,?,?) "
                "ON CONFLICT(patient_id, feature) DO UPDATE SET config=excluded.config, "
                "updated_at_utc=excluded.updated_at_utc, updated_by=excluded.updated_by",
                (patient_id, feature, json.dumps(config, ensure_ascii=False, default=str), datetime.now(UTC).isoformat(), by),
            )
            self._outbox(conn, patient_id, "ai.settings_changed", "ai_settings", f"{patient_id}:{feature}", {"feature": feature})

    # ------------------------------------------------------------ posture

    def save_posture_state(self, s) -> None:
        with self.db.connect() as conn:
            conn.execute(
                "INSERT INTO ai_posture_state VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(patient_id) DO UPDATE SET "
                "posture=excluded.posture, location=excluded.location, since_utc=excluded.since_utc, observed=excluded.observed, "
                "confidence=excluded.confidence, sitting_streak_seconds=excluded.sitting_streak_seconds, "
                "updated_at_utc=excluded.updated_at_utc",
                (s.patient_id, s.posture.value, s.location.value, iso(s.since), int(s.observed), s.confidence,
                 s.sitting_streak_seconds, iso(s.updated_at)),
            )
            self._outbox(conn, s.patient_id, "ai.posture.state_changed", "ai_posture", s.patient_id,
                         {"posture": s.posture.value, "location": s.location.value})

    def add_segment(self, seg) -> None:
        with self.db.connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO ai_posture_segments VALUES (?,?,?,?,?,?,?,?)",
                (seg.segment_id, seg.patient_id, seg.posture.value, seg.location.value, iso(seg.started_at),
                 iso(seg.ended_at), round(seg.ended_at - seg.started_at, 1), seg.confidence),
            )

    def save_reminder(self, r, change: str) -> None:
        with self.db.connect() as conn:
            conn.execute(
                "INSERT INTO ai_posture_reminders VALUES (?,?,?,?,?,?,?,?,?) ON CONFLICT(reminder_id) DO UPDATE SET "
                "status=excluded.status, caregiver_message=excluded.caregiver_message, updated_at_utc=excluded.updated_at_utc",
                (r.reminder_id, r.patient_id, r.rule.value, r.audience.value, r.status.value, r.message, r.caregiver_message,
                 iso(r.triggered_at), iso(r.updated_at or r.triggered_at)),
            )
            self._outbox(conn, r.patient_id, f"ai.posture.reminder_{change}", "ai_posture_reminder", r.reminder_id,
                         {"rule": r.rule.value, "status": r.status.value})

    # ------------------------------------------------------------ fall

    def save_fall_event(self, ev, change: str) -> None:
        detail = {"attempts": ev.attempts, "long_lie": ev.long_lie, "closed_by": ev.closed_by,
                  "escalated_at": iso(ev.escalated_at), "closed_at": iso(ev.closed_at),
                  "checkins": [{"ts": iso(c.ts), "attempt": c.attempt, "result": c.result.value if c.result else None}
                               for c in ev.checkins]}
        with self.db.connect() as conn:
            conn.execute(
                "INSERT INTO ai_fall_events VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(event_id) DO UPDATE SET "
                "status=excluded.status, updated_at_utc=excluded.updated_at_utc, caregiver_message=excluded.caregiver_message, "
                "label=excluded.label, label_by=excluded.label_by, note=excluded.note, detail=excluded.detail",
                (ev.event_id, ev.patient_id, ev.kind.value, ev.status.value, int(ev.trial), iso(ev.detected_at),
                 iso(ev.updated_at or ev.detected_at), ev.message, ev.caregiver_message,
                 ev.label.value if ev.label else None, ev.label_by, ev.note, json.dumps(detail)),
            )
            self._outbox(conn, ev.patient_id, f"ai.fall.{change}", "ai_fall_event", ev.event_id,
                         {"status": ev.status.value, "kind": ev.kind.value, "trial": ev.trial})

    def add_signal(self, s) -> None:
        with self.db.connect() as conn:
            conn.execute("INSERT OR IGNORE INTO ai_risk_signals VALUES (?,?,?,?,?,?,?)",
                         (s.signal_id, s.patient_id, s.code.value, iso(s.ts), s.value, s.threshold, s.message))
            self._outbox(conn, s.patient_id, "ai.fall.risk_signal", "ai_risk_signal", s.signal_id, {"code": s.code.value})

    def fall_events(self, patient_id: str, limit: int = 20) -> list[dict]:
        rows = self.db.fetchall("SELECT * FROM ai_fall_events WHERE patient_id=? ORDER BY detected_at_utc DESC LIMIT ?",
                                (patient_id, limit))
        for r in rows:
            r["detail"] = json.loads(r["detail"] or "{}")
        return rows

    def label_fall_event(self, patient_id: str, event_id: str, label: str | None, note: str | None, by: str) -> bool:
        with self.db.connect() as conn:
            row = conn.fetchone("SELECT event_id FROM ai_fall_events WHERE event_id=? AND patient_id=?", (event_id, patient_id))
            if not row:
                return False
            conn.execute("UPDATE ai_fall_events SET label=COALESCE(?, label), note=COALESCE(?, note), label_by=?, "
                         "updated_at_utc=? WHERE event_id=?", (label, note, by, datetime.now(UTC).isoformat(), event_id))
            self._outbox(conn, patient_id, "ai.fall.labeled", "ai_fall_event", event_id, {"label": label})
        return True

    def signals(self, patient_id: str, limit: int = 20) -> list[dict]:
        return self.db.fetchall("SELECT * FROM ai_risk_signals WHERE patient_id=? ORDER BY observed_at_utc DESC LIMIT ?",
                                (patient_id, limit))

    # ------------------------------------------------------------ objects

    def save_sighting(self, s, change: str) -> None:
        with self.db.connect() as conn:
            conn.execute(
                "INSERT INTO ai_object_sightings VALUES (?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(sighting_id) DO UPDATE SET "
                "box=excluded.box, last_seen_utc=excluded.last_seen_utc, confidence=excluded.confidence, "
                "status=excluded.status, status_at_utc=excluded.status_at_utc",
                (s.sighting_id, s.patient_id, s.item_key, s.type_key, s.camera_index, s.region_id, json.dumps(list(s.box)),
                 iso(s.first_seen), iso(s.last_seen), s.confidence, s.status.value, iso(s.status_at)),
            )
            if change != "refreshed":
                self._outbox(conn, s.patient_id, f"ai.objects.{change}", "ai_object_sighting", s.sighting_id,
                             {"item_key": s.item_key, "status": s.status.value})

    def sightings(self, patient_id: str, limit: int = 500) -> list:
        from ai_core.object_finder.domain import Sighting, SightingStatus

        rows = self.db.fetchall("SELECT * FROM ai_object_sightings WHERE patient_id=? ORDER BY last_seen_utc DESC LIMIT ?",
                                (patient_id, limit))
        return [Sighting(r["sighting_id"], r["patient_id"], r["item_key"], r["type_key"], r["camera_index"], r["region_id"],
                         tuple(json.loads(r["box"])), epoch(r["first_seen_utc"]), epoch(r["last_seen_utc"]), r["confidence"],
                         SightingStatus(r["status"]), epoch(r["status_at_utc"])) for r in rows]

    def place_counts(self, patient_id: str, since_iso: str) -> dict[str, dict[str, int]]:
        rows = self.db.fetchall(
            "SELECT item_key, region_id, COUNT(*) AS n FROM ai_object_sightings WHERE patient_id=? AND first_seen_utc>=? "
            "AND region_id IS NOT NULL GROUP BY item_key, region_id", (patient_id, since_iso))
        out: dict[str, dict[str, int]] = {}
        for r in rows:
            out.setdefault(r["item_key"], {})[r["region_id"]] = r["n"]
        return out

    # ------------------------------------------------------------ medication

    def doses_in_window(self, patient_id: str, start_iso: str, end_iso: str) -> list[dict]:
        """Doses scheduled in [start, end) of active prescriptions, any state."""
        return self.db.fetchall(
            "SELECT occurrence_id, patient_id, state, scheduled_at_utc, local_scheduled_time, medication_name_snapshot, "
            "dose_snapshot, action_actor, action_taken_at_utc FROM dose_occurrences "
            "WHERE patient_id=? AND scheduled_at_utc>=? AND scheduled_at_utc<? AND state!='CANCELLED' ORDER BY scheduled_at_utc",
            (patient_id, start_iso, end_iso))

    def monitor_done(self, occurrence_id: str) -> set[str]:
        row = self.db.fetchone("SELECT actions_done FROM ai_medication_monitor WHERE occurrence_id=?", (occurrence_id,))
        return set(json.loads(row["actions_done"])) if row else set()

    def mark_monitor(self, occurrence_id: str, patient_id: str, done: set[str], event: str | None = None,
                     payload: dict | None = None) -> None:
        with self.db.connect() as conn:
            conn.execute(
                "INSERT INTO ai_medication_monitor VALUES (?,?,?,?) ON CONFLICT(occurrence_id) DO UPDATE SET "
                "actions_done=excluded.actions_done, updated_at_utc=excluded.updated_at_utc",
                (occurrence_id, patient_id, json.dumps(sorted(done)), datetime.now(UTC).isoformat()))
            if event:
                self._outbox(conn, patient_id, f"ai.medication.{event}", "dose_occurrence", occurrence_id,
                             dict(payload or {}, occurrence_id=occurrence_id))

    def add_evidence(self, occurrence_id: str, patient_id: str, obs) -> str:
        evidence_id = "ev_" + uuid.uuid4().hex[:16]
        with self.db.connect() as conn:
            conn.execute("INSERT INTO ai_medication_evidence VALUES (?,?,?,?,?,?,?,?)",
                         (evidence_id, occurrence_id, patient_id, obs.verdict.value, iso(obs.ts), obs.score,
                          json.dumps({k: (iso(v) if k.endswith("_at") else v) for k, v in obs.steps.items()}), "none"))
            self._outbox(conn, patient_id, "ai.medication.intake_observed", "dose_occurrence", occurrence_id,
                         {"verdict": obs.verdict.value, "evidence_id": evidence_id})
        return evidence_id

    def confirm_dose_by_camera(self, occurrence_id: str, patient_id: str, evidence_id: str, observed_at: float) -> bool:
        """Mark the dose CONFIRMED by the camera, exactly like a self-report but attributed to the camera."""
        at = iso(observed_at)
        with self.db.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            occ = conn.fetchone("SELECT state, grace_deadline_utc FROM dose_occurrences WHERE occurrence_id=? AND patient_id=?",
                                (occurrence_id, patient_id))
            if not occ or occ["state"] not in ("PENDING", "REMINDING", "SNOOZED", "OVERDUE"):
                conn.execute("UPDATE ai_medication_evidence SET applied='not_applicable' WHERE evidence_id=?", (evidence_id,))
                return False
            conn.execute(
                "UPDATE dose_occurrences SET state='CONFIRMED', action_taken_at_utc=?, action_actor='camera_verified', "
                "action_notes=? WHERE occurrence_id=?",
                (at, f"Camera ghi nhận uống thuốc lúc {at} (bằng chứng {evidence_id})", occurrence_id))
            conn.execute("UPDATE alerts SET status='RESOLVED', resolved_at_utc=?, resolution_notes='Camera ghi nhận đã uống thuốc' "
                         "WHERE occurrence_id=? AND status IN ('NEW', 'DISPATCHED')", (at, occurrence_id))
            conn.execute(
                "INSERT INTO dose_events (event_id, occurrence_id, event_type, actor_id, actor_role, timestamp_utc, "
                "idempotency_key, metadata) VALUES (?,?,?,?,?,?,?,?)",
                ("evt_" + uuid.uuid4().hex[:16], occurrence_id, "resolved", "camera_ai", "camera_ai", at, "camera_" + evidence_id,
                 json.dumps({"source": "camera_ai", "verdict": "VERIFIED", "evidence_id": evidence_id})))
            conn.execute("UPDATE ai_medication_evidence SET applied='confirmed' WHERE evidence_id=?", (evidence_id,))
            insert_outbox_event(conn, patient_id, "medication.dose_changed", "dose_occurrence", occurrence_id, source=SOURCE,
                                payload_minimal={"occurrence_id": occurrence_id, "state": "CONFIRMED", "by": "camera"})
        return True

    def escalate_overdue(self, occurrence_id: str, patient_id: str) -> None:
        """Reuse the existing OVERDUE alert (portal + caregiver email through OutboxRelay)."""
        now = datetime.now(UTC).isoformat()
        alert_id = f"alt_ovd_{occurrence_id}"
        with self.db.connect() as conn:
            cur = conn.execute(
                "INSERT INTO alerts (alert_id, occurrence_id, patient_id, alert_type, status, created_at_utc) "
                "VALUES (?, ?, ?, 'OVERDUE', 'NEW', ?) ON CONFLICT(occurrence_id, alert_type) DO NOTHING",
                (alert_id, occurrence_id, patient_id, now))
            if cur.rowcount:
                insert_outbox_event(conn, patient_id, "medication.alert_created", "alert", alert_id, source=SOURCE,
                                    payload_minimal={"alert_id": alert_id, "occurrence_id": occurrence_id, "alert_type": "OVERDUE"})

    def evidence(self, patient_id: str, start_iso: str) -> list[dict]:
        rows = self.db.fetchall("SELECT * FROM ai_medication_evidence WHERE patient_id=? AND observed_at_utc>=? "
                                "ORDER BY observed_at_utc DESC", (patient_id, start_iso))
        for r in rows:
            r["steps"] = json.loads(r["steps"] or "{}")
        return rows

    def monitor_rows(self, patient_id: str) -> dict[str, list[str]]:
        rows = self.db.fetchall("SELECT occurrence_id, actions_done FROM ai_medication_monitor WHERE patient_id=?", (patient_id,))
        return {r["occurrence_id"]: json.loads(r["actions_done"]) for r in rows}

    def patient_name(self, patient_id: str) -> str:
        row = self.db.fetchone("SELECT full_name FROM patients WHERE patient_id=?", (patient_id,))
        return row["full_name"] if row else patient_id

    def raw(self) -> Any:
        return self.db
