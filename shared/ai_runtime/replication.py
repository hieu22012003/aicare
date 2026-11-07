"""Row replication between the home app (desktop) and the cloud backend (Render).

Who owns what
    server -> home ("down"): the medication plan made on the web (medications, prescriptions, items,
        schedules, dose occurrences) and the family music list. The home app copies them so the elder's
        to-do list, reminders and music player show what the family set on the web.
    home -> server ("up"): what happens at home and is only recorded locally: emotion summaries,
        mood check-ins, exercise sessions and reps, vitals typed in at home, cognitive games, face
        sightings, and dose actions pressed on the home screen ("Đã uống" / "Bỏ qua").

Rules
    * Rows keep their ids on both sides, upserts are idempotent (INSERT .. ON CONFLICT DO UPDATE, never
      REPLACE, so foreign-key cascades never delete children).
    * Every replicated row must belong to the device's patient; other rows are refused.
    * A foreign key that points at a row the other side does not have (a portal user, a clinician) is
      set to NULL when the column allows it, or to the patient's own user for required user columns;
      otherwise the row is skipped.
    * Dose occurrences are matched by (schedule_id, revision, local time) because both sides may have
      generated the same dose under different ids. A final state (CONFIRMED/SKIPPED/MISSED) always wins
      over an open one, and the earlier action wins between two final states.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timedelta, timezone

from shared.database.outbox_helper import insert_outbox_event

log = logging.getLogger("AIReplication")
UTC = timezone.utc

DOWN_TABLES = ("medications", "prescriptions", "prescription_items", "medication_schedules", "dose_occurrences", "family_music")
# table -> SQL expression used to pick recent rows on the home side (watermark-free, idempotent resend)
UP_TABLES = {
    "emotion_summaries_v2": "published_at",
    "mood_checkins": "recorded_at",
    "rehab_sessions": "COALESCE(ended_at, started_at)",
    "rehab_events": "timestamp_utc",
    "measurement_groups": "recorded_at",
    "health_observations": None,  # follows its measurement group
    "game_sessions": "COALESCE(ended_at, started_at)",
    "game_results": None,  # follows its game session
    "ai_face_sightings": "last_seen_utc",
}
OPEN_STATES = ("PENDING", "REMINDING", "SNOOZED", "OVERDUE")
FINAL_STATES = ("CONFIRMED", "SKIPPED", "MISSED")


# ---------------------------------------------------------------- generic helpers


def _columns(conn, table: str) -> dict[str, dict]:
    return {r["name"]: r for r in conn.fetchall(f"PRAGMA table_info({table})")}


def _foreign_keys(conn, table: str) -> list[dict]:
    return conn.fetchall(f"PRAGMA foreign_key_list({table})")


def _patient_user(conn, patient_id: str) -> str | None:
    row = conn.fetchone("SELECT user_id FROM patients WHERE patient_id=?", (patient_id,))
    return row["user_id"] if row else None


def upsert(conn, table: str, row: dict, patient_id: str | None = None) -> bool:
    """Insert or update one row by its primary key. Returns False when the row was skipped."""
    cols = _columns(conn, table)
    data = {k: v for k, v in row.items() if k in cols}
    pk = [name for name, c in sorted(cols.items(), key=lambda kv: kv[1]["pk"]) if c["pk"]]
    if not pk or any(data.get(k) is None for k in pk):
        return False
    for fk in _foreign_keys(conn, table):
        col, parent, parent_col = fk["from"], fk["table"], fk["to"] or "rowid"
        value = data.get(col)
        if value is None or col == "patient_id":
            continue
        if conn.fetchone(f"SELECT 1 FROM {parent} WHERE {parent_col}=?", (value,)):
            continue
        if not cols[col]["notnull"]:
            data[col] = None
        elif parent == "users" and patient_id and _patient_user(conn, patient_id):
            data[col] = _patient_user(conn, patient_id)
        else:
            log.debug("skip %s row %s: missing %s.%s=%s", table, data.get(pk[0]), parent, parent_col, value)
            return False
    names = list(data)
    updates = [n for n in names if n not in pk]
    sql = (f"INSERT INTO {table} ({', '.join(names)}) VALUES ({', '.join('?' for _ in names)}) "
           f"ON CONFLICT({', '.join(pk)}) DO " + (f"UPDATE SET {', '.join(f'{n}=excluded.{n}' for n in updates)}" if updates else "NOTHING"))
    conn.execute(sql, [data[n] for n in names])
    return True


def _rows(conn, sql: str, params=()) -> list[dict]:
    return [dict(r) for r in conn.fetchall(sql, params)]


# ---------------------------------------------------------------- server side


def snapshot_for_device(db, patient_id: str, now: datetime | None = None) -> dict:
    """What the home app needs from the web: medication plan + doses (yesterday .. +8 days) + music list."""
    now = now or datetime.now(UTC)
    with db.connect() as conn:
        prescriptions = _rows(conn, "SELECT * FROM prescriptions WHERE patient_id=?", (patient_id,))
        p_ids = [p["prescription_id"] for p in prescriptions]
        items = _rows(conn, f"SELECT * FROM prescription_items WHERE prescription_id IN ({','.join('?' * len(p_ids))})", p_ids) if p_ids else []
        i_ids = [i["prescription_item_id"] for i in items]
        meds = sorted({i["medication_id"] for i in items})
        return {
            "patient_id": patient_id,
            "generated_at_utc": now.isoformat(),
            "medications": _rows(conn, f"SELECT * FROM medications WHERE medication_id IN ({','.join('?' * len(meds))})", meds) if meds else [],
            "prescriptions": prescriptions,
            "prescription_items": items,
            "medication_schedules": _rows(conn, f"SELECT * FROM medication_schedules WHERE prescription_item_id IN ({','.join('?' * len(i_ids))})", i_ids) if i_ids else [],
            "dose_occurrences": _rows(conn, "SELECT * FROM dose_occurrences WHERE patient_id=? AND scheduled_at_utc>=? AND scheduled_at_utc<?",
                                      (patient_id, (now - timedelta(days=1)).isoformat(), (now + timedelta(days=8)).isoformat())),
            "family_music": _rows(conn, "SELECT * FROM family_music WHERE patient_id=? ORDER BY created_at DESC", (patient_id,)),
        }


def apply_from_device(db, patient_id: str, body: dict) -> dict:
    """Store what the home app recorded. Rows of other patients are refused."""
    stored: dict[str, int] = {}
    refused = 0
    rows_by_table = body.get("rows") or {}
    with db.connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        for table in UP_TABLES:  # parents before children
            for row in rows_by_table.get(table) or []:
                if not isinstance(row, dict):
                    continue
                if "patient_id" in _columns(conn, table):
                    if row.get("patient_id") != patient_id:
                        refused += 1
                        continue
                elif table == "health_observations" or table == "game_results":
                    parent = "measurement_groups" if table == "health_observations" else "game_sessions"
                    key = "group_id" if table == "health_observations" else "session_id"
                    owner = conn.fetchone(f"SELECT patient_id FROM {parent} WHERE {key}=?", (row.get(key),))
                    if not owner or owner["patient_id"] != patient_id:
                        refused += 1
                        continue
                if upsert(conn, table, row, patient_id):
                    stored[table] = stored.get(table, 0) + 1
        actions = [a for a in body.get("dose_actions") or [] if isinstance(a, dict)]
        applied = sum(1 for a in actions if _apply_dose_action(conn, patient_id, a))
    if stored.get("emotion_summaries_v2"):
        _notify(db, patient_id, "emotion.summary_updated", {"count": stored["emotion_summaries_v2"]})
    return {"stored": stored, "dose_actions_applied": applied, "refused": refused}


def _apply_dose_action(conn, patient_id: str, a: dict) -> bool:
    state = a.get("state")
    if state not in FINAL_STATES:
        return False
    occ = conn.fetchone("SELECT occurrence_id, state FROM dose_occurrences WHERE patient_id=? AND (occurrence_id=? OR "
                        "(schedule_id=? AND revision=? AND substr(local_scheduled_time,1,16)=substr(?,1,16)))",
                        (patient_id, a.get("occurrence_id"), a.get("schedule_id"), a.get("revision"), a.get("local_scheduled_time") or ""))
    if not occ or occ["state"] not in OPEN_STATES:
        return False
    at = a.get("action_taken_at_utc") or datetime.now(UTC).isoformat()
    actor = (a.get("action_actor") or "home_app")[:60]
    oid = occ["occurrence_id"]
    conn.execute("UPDATE dose_occurrences SET state=?, action_taken_at_utc=?, action_actor=?, action_notes=? WHERE occurrence_id=?",
                 (state, at, actor, (a.get("action_notes") or "Ghi nhận trên thiết bị tại nhà")[:500], oid))
    if state == "CONFIRMED":
        conn.execute("UPDATE alerts SET status='RESOLVED', resolved_at_utc=?, resolution_notes='Người dùng xác nhận đã uống tại nhà' "
                     "WHERE occurrence_id=? AND status IN ('NEW', 'DISPATCHED')", (at, oid))
    conn.execute("INSERT OR IGNORE INTO dose_events (event_id, occurrence_id, event_type, actor_id, actor_role, timestamp_utc, "
                 "idempotency_key, metadata) VALUES (?,?,?,?,?,?,?,?)",
                 ("evt_" + uuid.uuid4().hex[:16], oid, "resolved", actor, "home_device", at, f"home_{oid}_{state}",
                  json.dumps({"source": "home_device", "state": state})))
    insert_outbox_event(conn, patient_id, "medication.dose_changed", "dose_occurrence", oid, source="home_device",
                        payload_minimal={"occurrence_id": oid, "state": state, "by": "home"})
    return True


def _notify(db, patient_id: str, event_type: str, payload: dict) -> None:
    try:
        with db.connect() as conn:
            insert_outbox_event(conn, patient_id, event_type, "patient", patient_id, source="home_device", payload_minimal=payload)
    except Exception:
        log.debug("outbox notify failed", exc_info=True)


# ---------------------------------------------------------------- home side


def collect_for_server(db, patient_id: str, since_days: float = 3.0, sent: dict | None = None,
                       now: datetime | None = None) -> dict:
    """Recent home-only rows + dose actions pressed at home. ``sent`` (pk -> fingerprint) skips unchanged rows."""
    now = now or datetime.now(UTC)
    since = (now - timedelta(days=since_days)).isoformat()
    sent = sent if sent is not None else {}
    out: dict[str, list] = {}
    with db.connect() as conn:
        for table, ts in UP_TABLES.items():
            try:
                if table == "health_observations":
                    rows = _rows(conn, "SELECT o.* FROM health_observations o JOIN measurement_groups g ON g.group_id=o.group_id "
                                       "WHERE o.patient_id=? AND g.recorded_at>=?", (patient_id, since))
                elif table == "game_results":
                    rows = _rows(conn, "SELECT r.* FROM game_results r JOIN game_sessions s ON s.session_id=r.session_id "
                                       "WHERE s.patient_id=? AND COALESCE(s.ended_at, s.started_at)>=?", (patient_id, since))
                else:
                    rows = _rows(conn, f"SELECT * FROM {table} WHERE patient_id=? AND {ts}>=?", (patient_id, since))
            except Exception:  # table missing on an old database
                continue
            fresh = []
            for r in rows:
                key = f"{table}:{next(iter(r.values()))}"
                fp = json.dumps(r, sort_keys=True, default=str)
                if sent.get(key) != fp:
                    fresh.append(r)
                    sent[key] = fp
            if fresh:
                out[table] = fresh
        actions = _rows(conn, "SELECT occurrence_id, schedule_id, revision, local_scheduled_time, state, action_taken_at_utc, "
                              "action_actor, action_notes FROM dose_occurrences WHERE patient_id=? AND state IN ('CONFIRMED','SKIPPED') "
                              "AND COALESCE(action_actor,'') NOT IN ('camera_verified', 'server') AND action_taken_at_utc>=?",
                        (patient_id, since))
    fresh_actions = []
    for a in actions:
        key = f"dose:{a['occurrence_id']}:{a['state']}"
        if sent.get(key) != "1":
            fresh_actions.append(a)
            sent[key] = "1"
    return {"rows": out, "dose_actions": fresh_actions}


def apply_snapshot(db, snap: dict, patient_id: str) -> dict:
    """Copy the web's medication plan and music list into the home database."""
    counts: dict[str, int] = {}
    with db.connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        for table in DOWN_TABLES:
            if table == "dose_occurrences":
                continue
            for row in snap.get(table) or []:
                if row.get("patient_id", patient_id) != patient_id and table in ("prescriptions", "family_music"):
                    continue
                if upsert(conn, table, row, patient_id):
                    counts[table] = counts.get(table, 0) + 1
        for row in snap.get("dose_occurrences") or []:
            if row.get("patient_id") != patient_id:
                continue
            if _merge_dose(conn, row, patient_id):
                counts["dose_occurrences"] = counts.get("dose_occurrences", 0) + 1
    return counts


def _merge_dose(conn, server: dict, patient_id: str) -> bool:
    """Bring one server dose into the home DB.

    The home scheduler may already have created the same dose under another id (same schedule, revision
    and time; the table allows only one). That local twin takes the server's id, keeping its history.
    A final state pressed at home is kept until the server reports one too (then the earlier wins).
    """
    sid = server["occurrence_id"]
    conn.execute("PRAGMA defer_foreign_keys = ON")  # children are re-pointed inside this transaction
    twin = conn.fetchone("SELECT occurrence_id FROM dose_occurrences WHERE patient_id=? AND schedule_id=? AND revision=? "
                         "AND occurrence_id!=? AND (scheduled_at_utc=? OR substr(local_scheduled_time,1,16)=substr(?,1,16))",
                         (patient_id, server["schedule_id"], server["revision"], sid, server["scheduled_at_utc"],
                          server["local_scheduled_time"]))
    if twin and not conn.fetchone("SELECT 1 FROM dose_occurrences WHERE occurrence_id=?", (sid,)):
        old = twin["occurrence_id"]
        conn.execute("UPDATE dose_occurrences SET occurrence_id=? WHERE occurrence_id=?", (sid, old))
        for table in ("dose_events", "alerts", "ai_medication_monitor", "ai_medication_evidence"):
            try:
                conn.execute(f"UPDATE {table} SET occurrence_id=? WHERE occurrence_id=?", (sid, old))
            except Exception:
                pass
    local = conn.fetchone("SELECT * FROM dose_occurrences WHERE occurrence_id=?", (sid,))
    row = dict(server)
    if local and local["state"] in FINAL_STATES:
        mine_at, theirs_at = local["action_taken_at_utc"] or "", row.get("action_taken_at_utc") or ""
        if row["state"] in OPEN_STATES or (mine_at and (not theirs_at or mine_at < theirs_at)):
            row.update({k: local[k] for k in ("state", "action_taken_at_utc", "action_actor", "action_notes")})
    return upsert(conn, "dose_occurrences", row, patient_id)
