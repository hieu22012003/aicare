"""Short offline answers about the time and date ("mấy giờ rồi", "hôm nay thứ mấy")."""

from __future__ import annotations

import re
import unicodedata
from datetime import datetime
from zoneinfo import ZoneInfo

WEEKDAYS = ("thứ Hai", "thứ Ba", "thứ Tư", "thứ Năm", "thứ Sáu", "thứ Bảy", "Chủ nhật")
_TIME = re.compile(r"\b(mấy giờ|bao nhiêu giờ|giờ là mấy|giờ mấy|giờ rồi|mấy giờ rồi|xem giờ|nói giờ)\b")
_WEEKDAY = re.compile(r"\b(thứ mấy|thứ bao nhiêu)\b")
_DATE = re.compile(r"\b(ngày mấy|ngày bao nhiêu|ngày tháng|hôm nay là ngày|tháng mấy|năm nay là năm)\b")


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", unicodedata.normalize("NFC", text or "").lower()).strip()


def _part_of_day(h: int) -> str:
    return "sáng" if h < 11 else "trưa" if h < 13 else "chiều" if h < 18 else "tối" if h < 22 else "đêm"


def answer(text: str, now: datetime | None = None, tz: str = "Asia/Ho_Chi_Minh", honorific: str = "bác") -> str | None:
    t = _norm(text)
    now = (now or datetime.now(ZoneInfo(tz))).astimezone(ZoneInfo(tz))
    asks_time, asks_weekday, asks_date = bool(_TIME.search(t)), bool(_WEEKDAY.search(t)), bool(_DATE.search(t))
    if not (asks_time or asks_weekday or asks_date):
        return None
    h12 = now.hour % 12 or 12
    parts = []
    if asks_time:
        parts.append(f"Bây giờ là {h12} giờ {now.minute:02d} phút {_part_of_day(now.hour)}")
    if asks_weekday or asks_date:
        parts.append(f"hôm nay là {WEEKDAYS[now.weekday()]}, ngày {now.day} tháng {now.month} năm {now.year}")
    sentence = ", ".join(parts)
    return f"Thưa {honorific}, {sentence[0].lower() + sentence[1:]}."
