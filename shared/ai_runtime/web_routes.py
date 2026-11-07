"""Portal API for the AI features, mounted by features/web_portal/api_router.py under /api/patients/{id}/ai/...

The web server runs in its own process, so everything here reads the unified DB (written by the
desktop worker). Patient access ('view') is already checked by ApiRouter; writes additionally need
'edit_schedule', like other care-plan settings.

    GET  ai                         overview: posture, fall events, risk signals, objects, medication evidence
    GET  ai/daily?date=YYYY-MM-DD   home page: profile, current state, today's timeline, 24 h stats, alert history
    GET  ai/report?days=7|30        weekly / monthly report: adherence, alerts, falls, mood, activity by day
    GET  ai/objects/where?q=...     "kính của bà ở đâu?" answered from the last sightings
    GET  ai/settings/{feature}      posture | fall | objects | medication
    PUT  ai/settings/{feature}      validated with the same pydantic models the worker uses
    POST ai/fall/{event_id}/label   {label: real_fall|near_fall|false_alarm|unsure, note}
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

from pydantic import ValidationError

from shared.ai_runtime.brain import SETTINGS, AICoreBrain
from shared.ai_runtime.store import AICoreStore

UTC = timezone.utc
FALL_LABELS = ("real_fall", "near_fall", "false_alarm", "unsure")


def handle_ai_route(db, auth, method: str, rest: list[str], query: dict, body: dict | None, patient_id: str, user_id: str):
    store = AICoreStore(db)
    if not store.ready():
        return 503, {"error": "Chưa áp dụng migration 014_ai_core"}
    body = body or {}

    if method == "GET" and not rest:
        return 200, overview(store, patient_id)

    if method == "GET" and rest == ["report"]:
        from shared.ai_runtime.daily import period_report

        try:
            days = int(_first(query, "days").strip() or 7)
        except ValueError:
            return 400, {"error": "days phải là số ngày (7 hoặc 30)"}
        return 200, period_report(db, patient_id, days)

    if method == "GET" and rest == ["daily"]:
        from shared.ai_runtime.daily import daily_summary

        day = _first(query, "date").strip() or None
        try:
            return 200, daily_summary(db, patient_id, day)
        except ValueError:
            return 400, {"error": "date phải có dạng YYYY-MM-DD"}

    if method == "GET" and rest == ["objects", "where"]:
        q = _first(query, "q").strip()
        if not q or len(q) > 200:
            return 400, {"error": "Thiếu câu hỏi q (tối đa 200 ký tự)"}
        a = AICoreBrain(store).answer_where(patient_id, q, time.time())
        return 200, {"status": a.status, "text": a.text, "item": a.item_name, "suggestions": a.suggestions,
                     "confidence": a.confidence}

    if len(rest) == 2 and rest[0] == "settings" and rest[1] in SETTINGS:
        feature = rest[1]
        model = SETTINGS[feature]
        if method == "GET":
            return 200, {"feature": feature, "config": model.model_validate(store.get_settings(patient_id, feature)).model_dump(mode="json")}
        if method == "PUT":
            ok, err = auth.check_patient_access(user_id, patient_id, "edit_schedule")
            if not ok:
                return 403, {"error": err}
            try:
                value = model.model_validate(body.get("config", body))
            except ValidationError as exc:
                return 422, {"error": "Cấu hình không hợp lệ", "detail": exc.errors(include_url=False, include_context=False)}
            store.set_settings(patient_id, feature, value.model_dump(mode="json"), user_id)
            return 200, {"feature": feature, "config": value.model_dump(mode="json")}

    if method == "POST" and len(rest) == 3 and rest[0] == "fall" and rest[2] == "label":
        label = body.get("label")
        if label is not None and label not in FALL_LABELS:
            return 400, {"error": f"label phải là một trong {', '.join(FALL_LABELS)}"}
        note = (body.get("note") or "")[:500] or None
        if not store.label_fall_event(patient_id, rest[1], label, note, user_id):
            return 404, {"error": "Không tìm thấy sự kiện"}
        return 200, {"event_id": rest[1], "label": label}

    return 404, {"error": "Endpoint không tồn tại"}


def overview(store: AICoreStore, patient_id: str) -> dict:
    db = store.raw()
    since = (datetime.now(UTC) - timedelta(days=1)).isoformat()
    posture = db.fetchone("SELECT * FROM ai_posture_state WHERE patient_id=?", (patient_id,))
    reminders = db.fetchall("SELECT rule, audience, status, message, caregiver_message, triggered_at_utc FROM ai_posture_reminders "
                            "WHERE patient_id=? AND triggered_at_utc>=? ORDER BY triggered_at_utc DESC LIMIT 10", (patient_id, since))
    segments = db.fetchall("SELECT posture, SUM(duration_seconds) AS seconds FROM ai_posture_segments "
                           "WHERE patient_id=? AND started_at_utc>=? GROUP BY posture", (patient_id, since))
    memory = AICoreBrain(store).session(patient_id).objects
    sightings = [
        {"item_key": s.item_key, "name": memory.item_name(s.item_key), "region_id": s.region_id, "place": memory.place_text(s),
         "last_seen_utc": datetime.fromtimestamp(s.last_seen, UTC).isoformat(), "status": s.status.value,
         "confidence": s.confidence}
        for s in memory.sightings()[:30]
    ]
    doses = db.fetchall(
        "SELECT d.occurrence_id, d.medication_name_snapshot AS medication, d.scheduled_at_utc, d.state, d.action_actor, "
        "m.actions_done FROM dose_occurrences d LEFT JOIN ai_medication_monitor m ON m.occurrence_id=d.occurrence_id "
        "WHERE d.patient_id=? AND d.scheduled_at_utc>=? AND d.state!='CANCELLED' ORDER BY d.scheduled_at_utc DESC LIMIT 20",
        (patient_id, since))
    return {
        "patient_id": patient_id,
        "posture": {"current": posture, "last_24h_seconds": {r["posture"]: round(r["seconds"] or 0) for r in segments},
                    "reminders": reminders},
        "fall": {"events": store.fall_events(patient_id, 10), "signals": store.signals(patient_id, 10),
                 "trial": not SETTINGS["fall"].model_validate(store.get_settings(patient_id, "fall")).config.alerts_enabled},
        "objects": {"sightings": sightings},
        "medication": {"doses": doses, "evidence": store.evidence(patient_id, since)},
    }


def _first(query: dict, key: str) -> str:
    value = (query or {}).get(key, "")
    return value[0] if isinstance(value, list) else str(value or "")
