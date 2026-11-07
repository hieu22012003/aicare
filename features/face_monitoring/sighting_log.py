"""Face monitoring log: when the elder, or a stranger, appeared in front of the camera each day.

``record`` is cheap to call on every recognition result: it writes at most once every few seconds per
person. One row per (patient, local day, who) keeps first/last seen, total seconds in view and the
separate visits (a gap of 10 minutes starts a new visit). ``unusual_today`` compares today with the
previous days to flag a late first appearance, a missing elder, or a stranger.
"""

from __future__ import annotations

import json
import statistics
import threading
import time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

UTC = timezone.utc
VISIT_GAP = 600.0
WRITE_EVERY = 5.0


class FaceSightingLog:
    def __init__(self, db, clock=time.time):
        self.db = db
        self.clock = clock
        self._last_write: dict[tuple[str, str], float] = {}
        self._lock = threading.Lock()

    def _tz(self, patient_id: str):
        try:
            row = self.db.fetchone("SELECT timezone FROM patients WHERE patient_id=?", (patient_id,))
            return ZoneInfo((row or {}).get("timezone") or "Asia/Ho_Chi_Minh")
        except Exception:
            return ZoneInfo("Asia/Ho_Chi_Minh")

    def record(self, patient_id: str | None, who: str = "patient", ts: float | None = None) -> bool:
        if not patient_id or who not in ("patient", "stranger"):
            return False
        now = ts if ts is not None else self.clock()
        key = (patient_id, who)
        with self._lock:
            if 0 <= now - self._last_write.get(key, -1e9) < WRITE_EVERY:
                return False
            self._last_write[key] = now
        at = datetime.fromtimestamp(now, UTC)
        day = at.astimezone(self._tz(patient_id)).date().isoformat()
        sid = f"{patient_id}:{day}:{who}"
        with self.db.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.fetchone("SELECT * FROM ai_face_sightings WHERE sighting_id=?", (sid,))
            if not row:
                conn.execute("INSERT INTO ai_face_sightings (sighting_id, patient_id, local_date, who, first_seen_utc, last_seen_utc, "
                             "seen_seconds, appearances, visits_json) VALUES (?,?,?,?,?,?,0,1,?)",
                             (sid, patient_id, day, who, at.isoformat(), at.isoformat(), json.dumps([[at.isoformat(), at.isoformat()]])))
                return True
            gap = (at - datetime.fromisoformat(row["last_seen_utc"])).total_seconds()
            if gap < 0:
                return False
            visits = json.loads(row["visits_json"] or "[]")
            appearances = row["appearances"]
            if gap > VISIT_GAP or not visits:
                appearances += 1
                visits.append([at.isoformat(), at.isoformat()])
            else:
                visits[-1][1] = at.isoformat()
            conn.execute("UPDATE ai_face_sightings SET last_seen_utc=?, seen_seconds=seen_seconds+?, appearances=?, visits_json=? "
                         "WHERE sighting_id=?", (at.isoformat(), min(gap, WRITE_EVERY * 2), appearances, json.dumps(visits[-50:]), sid))
        return True


def unusual_today(db, patient_id: str, day: str, now: datetime | None = None, tz=None) -> list[dict]:
    """Things a caregiver should notice about today's appearances (Vietnamese text)."""
    now = now or datetime.now(UTC)
    tz = tz or ZoneInfo("Asia/Ho_Chi_Minh")
    out: list[dict] = []
    try:
        rows = db.fetchall("SELECT * FROM ai_face_sightings WHERE patient_id=? AND local_date<=? AND local_date>=? ",
                           (patient_id, day, (datetime.fromisoformat(day) - timedelta(days=14)).date().isoformat()))
    except Exception:
        return out
    today = {r["who"]: r for r in rows if r["local_date"] == day}
    past = [r for r in rows if r["who"] == "patient" and r["local_date"] < day]

    def minutes(iso: str) -> float:
        t = datetime.fromisoformat(iso).astimezone(tz)
        return t.hour * 60 + t.minute

    if "stranger" in today:
        s = today["stranger"]
        out.append({"kind": "stranger", "at_utc": s["first_seen_utc"],
                    "text": f"Phát hiện người lạ trước camera ({s['appearances']} lần)"})
    if len(past) >= 3:
        usual = statistics.median(minutes(r["first_seen_utc"]) for r in past)
        is_today = now.astimezone(tz).date().isoformat() == day
        now_min = now.astimezone(tz).hour * 60 + now.astimezone(tz).minute
        if "patient" in today:
            first = minutes(today["patient"]["first_seen_utc"])
            if first - usual >= 120:
                out.append({"kind": "late", "at_utc": today["patient"]["first_seen_utc"],
                            "text": f"Hôm nay xuất hiện muộn hơn thường lệ ({int(first // 60):02d}:{int(first % 60):02d}, "
                                    f"thường khoảng {int(usual // 60):02d}:{int(usual % 60):02d})"})
        elif is_today and now_min - usual >= 120:
            out.append({"kind": "missing", "at_utc": now.isoformat(),
                        "text": f"Đến giờ này chưa thấy người dùng trước camera (thường xuất hiện khoảng "
                                f"{int(usual // 60):02d}:{int(usual % 60):02d})"})
    return out
