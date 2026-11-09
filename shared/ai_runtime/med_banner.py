"""On-camera banner while a medication window is open (field report 09/11: "đến giờ thì màn hình phải hiện
là đang kiểm tra uống thuốc").

Works in both modes: the doses come from the local database (local brain, or the plan pulled from the web in
cloud device mode); whether the window is open comes from the AI runtime.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta

TERMINAL = ("CONFIRMED", "SKIPPED", "CANCELLED", "MISSED", "TAKEN", "TAKEN_LATE")
_cache: dict = {"at": 0.0, "key": None, "value": None}


def _epoch(iso: str) -> float:
    dt = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
    return (dt if dt.tzinfo else dt.replace(tzinfo=UTC)).timestamp()


def banner_lines(db, patient_id: str | None, active: bool, policy=None, live: dict | None = None,
                 now: float | None = None) -> list[str] | None:
    """Two or three short lines for the video overlay, or None when no medication window is open."""
    now = time.time() if now is None else now
    if live and live.get("confirmed_at") and now - float(live["confirmed_at"]) < 20:
        return ["ĐÃ GHI NHẬN UỐNG THUỐC", "Camera đã thấy cầm thuốc, đưa lên miệng và uống. Đã cập nhật lịch thuốc."]
    if not active or not patient_id or db is None:
        return None
    key = (patient_id, int(now // 2))
    if _cache["key"] == key:
        doses = _cache["value"]
    else:
        start = datetime.fromtimestamp(now - 3 * 3600, UTC).isoformat()
        end = datetime.fromtimestamp(now + 3600, UTC).isoformat()
        try:
            doses = db.fetchall(
                "SELECT medication_name_snapshot AS name, dose_snapshot AS dose, scheduled_at_utc, local_scheduled_time, "
                "state FROM dose_occurrences WHERE patient_id=? AND scheduled_at_utc>=? AND scheduled_at_utc<=? "
                "ORDER BY scheduled_at_utc", (patient_id, start, end))
        except Exception:
            doses = []
        _cache.update(key=key, value=doses)
    open_before = getattr(policy, "open_before_minutes", 5)
    overdue = getattr(policy, "overdue_minutes", 5)
    pending = [d for d in doses if d["state"] not in TERMINAL
               and _epoch(d["scheduled_at_utc"]) - open_before * 60 - 5 <= now]
    if not pending:
        return None
    first = min(_epoch(d["scheduled_at_utc"]) for d in pending)
    batch = [d for d in pending if _epoch(d["scheduled_at_utc"]) == first]
    names = ", ".join(sorted({str(d["name"]).split(" (")[0] for d in batch}))
    hhmm = str(batch[0]["local_scheduled_time"])[11:16]
    lines = [f"ĐANG GIÁM SÁT UỐNG THUỐC  ·  {hhmm}  ·  {names}"]
    if live is not None:
        def mark(ok):
            return "[x]" if ok else "[ ]"

        lines.append(f"{mark(live.get('pill_held'))} 1. Cầm thuốc (ngón cái + ngón trỏ)   "
                     f"{mark(live.get('hand_at_mouth'))} 2. Đưa lên miệng   "
                     f"{mark(live.get('mouth_open'))} 3. Há miệng cho thuốc vào")
        if not live.get("hands_seen") and not live.get("hand_at_mouth"):
            lines.append("Chưa thấy tay: giơ tay cầm thuốc vào khung hình" if now >= first
                         else f"Còn {max(1, round((first - now) / 60))} phút đến giờ uống. Cầm viên thuốc trước camera.")
    elif now < first:
        lines.append(f"Còn {max(1, round((first - now) / 60))} phút đến giờ uống. Cầm viên thuốc trước camera.")
    else:
        lines.append("Đưa viên thuốc lên miệng bằng ngón cái và ngón trỏ, há miệng, rồi bỏ tay xuống.")
    warn_at = first + overdue * 60
    if now < warn_at:
        left = int(warn_at - now)
        lines.append(f"Chưa thấy uống sau {left // 60:d}:{left % 60:02d} nữa sẽ nhắc và báo người nhà")
    else:
        lines.append(f"Quá giờ {int((now - first) // 60)} phút – đã báo người nhà")
    return lines


def draw_banner(frame, lines: list[str] | None):
    """Dark band across the top of the frame with the lines (Vietnamese-safe text)."""
    if not lines or frame is None:
        return frame
    import cv2

    from shared.camera.drawing_utils import draw_vietnamese_text, get_text_size

    h, w = frame.shape[:2]
    size1, size2 = max(16, w // 42), max(13, w // 56)
    band_h = 14 + size1 + 8 + (size2 + 8) * (len(lines) - 1) + 6
    overlay = frame.copy()
    cv2.rectangle(overlay, (0, 0), (w, band_h), (20, 60, 25), -1)
    out = cv2.addWeighted(overlay, 0.82, frame, 0.18, 0)
    cv2.rectangle(out, (0, band_h - 3), (w, band_h), (80, 200, 120), -1)
    y = 10
    for i, text in enumerate(lines):
        size = size1 if i == 0 else size2
        while size > 11 and get_text_size(text, size)[0] > w - 24:
            size -= 1
        draw_vietnamese_text(out, text, (14, y), font_size=size,
                             color=(255, 255, 255) if i == 0 else (215, 245, 220))
        y += size + 8
    return out
