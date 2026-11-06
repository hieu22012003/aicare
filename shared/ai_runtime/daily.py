"""Caregiver home page data: one patient, one local day (patient's timezone).

    profile        name, age, relative, last update
    current        posture, activity, emotion, alert status (the four tiles)
    stats_24h      seconds lying / sitting / standing-walking / exercising over the last 24 h
    timeline       today's events in time order (medication, posture reminders, falls, exercise, mood)
    alerts         alert history (7 days): missed doses, help requests, falls, sadness, long sitting/lying
Reads only; every source table is optional so a fresh database returns empty lists.
"""

from __future__ import annotations

import json
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

UTC = timezone.utc

POSTURE_VI = {"STANDING": "Đứng", "WALKING": "Đi lại", "SITTING": "Ngồi", "LYING": "Nằm", "BENDING": "Cúi người",
              "UNOBSERVED": "Không thấy trong khung hình"}
LOCATION_VI = {"BED": "trên giường", "CHAIR": "trên ghế", "SOFA": "trên sofa", "FLOOR": "dưới sàn"}
EMOTION_VI = {"Happy": "Vui vẻ", "Neutral": "Bình thường", "Sad": "Buồn"}
DOSE_VI = {"CONFIRMED": "Đã uống", "SKIPPED": "Bỏ liều", "MISSED": "Bỏ lỡ", "OVERDUE": "Quá giờ, chưa uống",
           "PENDING": "Chưa đến giờ", "REMINDING": "Đang nhắc", "SNOOZED": "Hoãn"}


def _rows(db, sql: str, params=()) -> list[dict]:
    try:
        return db.fetchall(sql, params)
    except Exception:  # table missing on an old database
        return []


def _row(db, sql: str, params=()) -> dict | None:
    rows = _rows(db, sql, params)
    return rows[0] if rows else None


def _iso(dt: datetime) -> str:
    return dt.astimezone(UTC).isoformat()


def _age(dob: str | None, today: date) -> int | None:
    try:
        b = date.fromisoformat((dob or "")[:10])
    except ValueError:
        return None
    return today.year - b.year - ((today.month, today.day) < (b.month, b.day))


def daily_summary(db, patient_id: str, day: str | None = None, now: datetime | None = None) -> dict:
    now = now or datetime.now(UTC)
    patient = _row(db, "SELECT patient_id, full_name, date_of_birth, gender, timezone, notes FROM patients WHERE patient_id=?",
                   (patient_id,)) or {"patient_id": patient_id, "full_name": patient_id, "timezone": "Asia/Ho_Chi_Minh"}
    try:
        tz = ZoneInfo(patient.get("timezone") or "Asia/Ho_Chi_Minh")
    except Exception:
        tz = ZoneInfo("Asia/Ho_Chi_Minh")
    local_day = date.fromisoformat(day) if day else now.astimezone(tz).date()
    start = datetime.combine(local_day, time.min, tz)
    end = start + timedelta(days=1)
    s, e = _iso(start), _iso(end)

    relative = _row(db, "SELECT u.full_name, pc.relationship_type FROM patient_caregivers pc JOIN caregivers c "
                        "ON c.caregiver_id=pc.caregiver_id JOIN users u ON u.user_id=c.user_id "
                        "WHERE pc.patient_id=? AND pc.is_active=1 ORDER BY pc.is_primary DESC LIMIT 1", (patient_id,))

    # ---------------------------------------------------------------- current state
    posture = _row(db, "SELECT posture, location, since_utc, observed, updated_at_utc FROM ai_posture_state WHERE patient_id=?",
                   (patient_id,))
    emotion = _row(db, "SELECT label, dominant_label, published_at FROM emotion_summaries_v2 WHERE patient_id=? "
                       "ORDER BY published_at DESC LIMIT 1", (patient_id,))
    recent = _iso(now - timedelta(hours=2))
    open_alerts = alerts(db, patient_id, recent, _iso(now + timedelta(minutes=1)), tz)
    open_alerts = [a for a in open_alerts if a["status_key"] in ("open", "new")]
    p_key = (posture or {}).get("posture") or "UNOBSERVED"
    stale = not posture or not posture.get("observed")
    current = {
        "posture": {"key": p_key, "label": POSTURE_VI.get(p_key, p_key),
                    "where": LOCATION_VI.get((posture or {}).get("location") or "", ""),
                    "since_utc": (posture or {}).get("since_utc"), "observed": not stale},
        "activity": {"label": "Không quan sát được" if stale else (
            "Đang hoạt động" if p_key in ("WALKING", "STANDING", "BENDING") else "Đang nghỉ")},
        "emotion": {"key": (emotion or {}).get("label"), "label": EMOTION_VI.get((emotion or {}).get("label"), "Chưa có dữ liệu"),
                    "at_utc": (emotion or {}).get("published_at")},
        "alert": {"count": len(open_alerts), "label": f"{len(open_alerts)} cảnh báo cần xem" if open_alerts else "Bình thường",
                  "latest": open_alerts[0] if open_alerts else None},
    }

    # ---------------------------------------------------------------- 24 h statistics
    since24 = _iso(now - timedelta(hours=24))
    seg = {r["posture"]: float(r["seconds"] or 0) for r in _rows(
        db, "SELECT posture, SUM(duration_seconds) AS seconds FROM ai_posture_segments WHERE patient_id=? AND ended_at_utc>=? "
            "GROUP BY posture", (patient_id, since24))}
    exercise = 0.0
    for r in _rows(db, "SELECT started_at, ended_at FROM rehab_sessions WHERE patient_id=? AND started_at>=? AND ended_at IS NOT NULL",
                   (patient_id, since24)):
        try:
            exercise += max(0.0, (datetime.fromisoformat(r["ended_at"]) - datetime.fromisoformat(r["started_at"])).total_seconds())
        except (TypeError, ValueError):
            pass
    stats = [
        {"key": "lying", "label": "Nằm", "seconds": round(seg.get("LYING", 0))},
        {"key": "sitting", "label": "Ngồi", "seconds": round(seg.get("SITTING", 0))},
        {"key": "moving", "label": "Đứng/Di chuyển",
         "seconds": round(seg.get("STANDING", 0) + seg.get("WALKING", 0) + seg.get("BENDING", 0))},
        {"key": "exercise", "label": "Vận động", "seconds": round(exercise)},
    ]
    observed = sum(x["seconds"] for x in stats)
    for x in stats:
        x["percent"] = round(100 * x["seconds"] / observed) if observed else 0

    return {
        "patient_id": patient_id,
        "date": local_day.isoformat(),
        "timezone": str(tz),
        "profile": {
            "patient_id": patient_id, "full_name": patient.get("full_name"), "age": _age(patient.get("date_of_birth"), local_day),
            "gender": patient.get("gender"), "notes": patient.get("notes") or "",
            "relative": relative["full_name"] + (f" ({relative['relationship_type']})" if relative.get("relationship_type") else "")
            if relative else None,
            "updated_at_utc": max(filter(None, [(posture or {}).get("updated_at_utc"), (emotion or {}).get("published_at")]),
                                  default=None),
        },
        "current": current,
        "stats_24h": {"observed_seconds": observed, "items": stats},
        "timeline": timeline(db, patient_id, s, e, tz),
        "alerts": alerts(db, patient_id, _iso(start - timedelta(days=6)), e, tz),
    }


def _local(iso: str | None, tz) -> str:
    try:
        return datetime.fromisoformat(iso).astimezone(tz).strftime("%H:%M")
    except (TypeError, ValueError):
        return ""


def _dur(seconds: float) -> str:
    m = int(round(seconds / 60))
    return f"{m // 60} giờ {m % 60} phút" if m >= 60 else f"{m} phút"


def timeline(db, pid: str, s: str, e: str, tz) -> list[dict]:
    items: list[dict] = []

    def add(at, text, kind, status=None, status_key=None):
        if at:
            items.append({"at_utc": at, "time": _local(at, tz), "text": text, "kind": kind, "status": status,
                          "status_key": status_key})

    for d in _rows(db, "SELECT medication_name_snapshot AS med, dose_snapshot AS dose, scheduled_at_utc, state, action_actor, "
                       "action_taken_at_utc FROM dose_occurrences WHERE patient_id=? AND scheduled_at_utc>=? AND scheduled_at_utc<? "
                       "AND state!='CANCELLED'", (pid, s, e)):
        how = " (camera ghi nhận)" if d["action_actor"] == "camera_verified" else ""
        ok = d["state"] == "CONFIRMED"
        add(d["scheduled_at_utc"], f"Nhắc uống thuốc {d['med']} – {d['dose']}", "medication",
            DOSE_VI.get(d["state"], d["state"]) + how, "done" if ok else ("bad" if d["state"] in ("OVERDUE", "MISSED") else "info"))
    for m in _monitored(db, pid, s, e):
        if "open" in m["done"]:
            add(m["opened_at"], f"Bật camera giám sát uống thuốc {m['med']}", "camera", None, "info")
        if "overdue" in m["done"]:
            add(m["overdue_at"], f"Quá {m['overdue_minutes']} phút chưa uống thuốc {m['med']}", "medication", "Đã cảnh báo", "bad")
    for r in _rows(db, "SELECT posture, location, started_at_utc, duration_seconds FROM ai_posture_segments WHERE patient_id=? "
                       "AND started_at_utc>=? AND started_at_utc<? AND duration_seconds>=600 ORDER BY started_at_utc", (pid, s, e)):
        where = LOCATION_VI.get(r["location"], "")
        add(r["started_at_utc"], f"{POSTURE_VI.get(r['posture'], r['posture'])} {where} ({_dur(r['duration_seconds'])})".replace("  ", " "),
            "posture", None, "info")
    for r in _rows(db, "SELECT message, caregiver_message, triggered_at_utc, status FROM ai_posture_reminders WHERE patient_id=? "
                       "AND triggered_at_utc>=? AND triggered_at_utc<?", (pid, s, e)):
        add(r["triggered_at_utc"], r["caregiver_message"] or r["message"], "reminder", "Đã nhắc", "info")
    for r in _rows(db, "SELECT message, caregiver_message, detected_at_utc, status, trial FROM ai_fall_events WHERE patient_id=? "
                       "AND detected_at_utc>=? AND detected_at_utc<?", (pid, s, e)):
        add(r["detected_at_utc"], r["caregiver_message"] or r["message"], "fall",
            "Chế độ thử" if r["trial"] else "Đã cảnh báo", "warn")
    for r in _rows(db, "SELECT started_at, verified_count, target_count_snapshot, status FROM rehab_sessions WHERE patient_id=? "
                       "AND started_at>=? AND started_at<?", (pid, s, e)):
        add(r["started_at"], f"Tập bài tập vận động ({r['verified_count']}/{r['target_count_snapshot']} lần)", "exercise",
            "Hoàn thành" if r["status"] in ("camera_verified_completed", "manual_reviewed") else "Chưa xong",
            "done" if r["status"] in ("camera_verified_completed", "manual_reviewed") else "info")
    for r in _rows(db, "SELECT published_at, label, reason FROM emotion_summaries_v2 WHERE patient_id=? AND published_at>=? "
                       "AND published_at<? AND label='Sad'", (pid, s, e)):
        add(r["published_at"], "Phát hiện biểu hiện buồn kéo dài", "emotion", "Đã báo người nhà", "warn")
    items.sort(key=lambda x: x["at_utc"])
    return items


def alerts(db, pid: str, s: str, e: str, tz) -> list[dict]:
    out: list[dict] = []

    def add(at, kind, text, status, status_key):
        if at:
            out.append({"at_utc": at, "time": _local(at, tz), "date": _date(at, tz), "kind": kind, "text": text,
                        "status": status, "status_key": status_key})

    status_vi = {"NEW": ("Mới", "new"), "DISPATCHED": ("Đã gửi người nhà", "open"), "ACKNOWLEDGED": ("Đã xem", "done"),
                 "RESOLVED": ("Đã xử lý", "done")}
    for r in _rows(db, "SELECT a.alert_type, a.status, a.created_at_utc, d.medication_name_snapshot AS med FROM alerts a "
                       "LEFT JOIN dose_occurrences d ON d.occurrence_id=a.occurrence_id WHERE a.patient_id=? AND a.created_at_utc>=? "
                       "AND a.created_at_utc<?", (pid, s, e)):
        label, key = status_vi.get(r["status"], (r["status"], "open"))
        text = (f"Quá giờ uống thuốc {r['med'] or ''}" if r["alert_type"] == "OVERDUE" else "Bấm nút cần hỗ trợ khi uống thuốc").strip()
        add(r["created_at_utc"], "medication" if r["alert_type"] == "OVERDUE" else "help", text, label, key)
    for m in _monitored(db, pid, s, e):
        if "overdue" in m["done"]:
            taken = m["state"] == "CONFIRMED"
            add(m["overdue_at"], "medication", f"Camera: quá {m['overdue_minutes']} phút chưa uống {m['med']}",
                "Sau đó đã uống" if taken else "Chưa uống", "done" if taken else "new")
    fall_vi = {"CHECKING": ("Đang hỏi lại", "open"), "ESCALATED": ("Đã báo người nhà", "new"), "USER_OK": ("Bác báo ổn", "done"),
               "SELF_RECOVERED": ("Tự đứng dậy", "done"), "ACKNOWLEDGED": ("Đã xử lý", "done")}
    for r in _rows(db, "SELECT detected_at_utc, status, trial, caregiver_message, message FROM ai_fall_events WHERE patient_id=? "
                       "AND detected_at_utc>=? AND detected_at_utc<?", (pid, s, e)):
        label, key = fall_vi.get(r["status"], (r["status"], "open"))
        add(r["detected_at_utc"], "fall", "Dấu hiệu ngã" + (" (chế độ thử)" if r["trial"] else ""), label, key)
    for r in _rows(db, "SELECT published_at FROM emotion_summaries_v2 WHERE patient_id=? AND label='Sad' AND published_at>=? "
                       "AND published_at<?", (pid, s, e)):
        add(r["published_at"], "emotion", "Biểu hiện buồn kéo dài", "Đã báo người nhà", "done")
    for r in _rows(db, "SELECT triggered_at_utc, status, rule FROM ai_posture_reminders WHERE patient_id=? AND audience!='elder' "
                       "AND triggered_at_utc>=? AND triggered_at_utc<?", (pid, s, e)):
        add(r["triggered_at_utc"], "posture", "Ngồi/nằm quá lâu", "Đã nhắc", "done")
    out.sort(key=lambda x: x["at_utc"], reverse=True)
    return out[:50]


def _monitored(db, pid: str, s: str, e: str) -> list[dict]:
    """Doses the camera watched, with the milestones reached (open / overdue) at their policy times."""
    try:
        from shared.ai_runtime.brain import SETTINGS
        from shared.ai_runtime.store import AICoreStore

        policy = SETTINGS["medication"].model_validate(AICoreStore(db).get_settings(pid, "medication")).policy
        before, overdue = policy.open_before_minutes, policy.overdue_minutes
    except Exception:
        before, overdue = 5, 10
    out = []
    for r in _rows(db, "SELECT d.medication_name_snapshot AS med, d.scheduled_at_utc, d.state, m.actions_done FROM "
                       "ai_medication_monitor m JOIN dose_occurrences d ON d.occurrence_id=m.occurrence_id WHERE m.patient_id=? "
                       "AND d.scheduled_at_utc>=? AND d.scheduled_at_utc<?", (pid, s, e)):
        at = datetime.fromisoformat(r["scheduled_at_utc"])
        out.append({"med": r["med"], "state": r["state"], "done": set(json.loads(r["actions_done"] or "[]")),
                    "opened_at": _iso(at - timedelta(minutes=before)), "overdue_at": _iso(at + timedelta(minutes=overdue)),
                    "overdue_minutes": int(overdue)})
    return out


def _date(iso: str, tz) -> str:
    try:
        return datetime.fromisoformat(iso).astimezone(tz).strftime("%d/%m")
    except (TypeError, ValueError):
        return ""
