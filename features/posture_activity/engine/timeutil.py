"""Local-time helpers. Everything is stored as epoch seconds (UTC) and bucketed by local day (BR-04)."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from functools import lru_cache
from zoneinfo import ZoneInfo


@lru_cache(maxsize=32)
def tz(name: str) -> ZoneInfo:
    return ZoneInfo(name)


def local_dt(ts: float, tz_name: str) -> datetime:
    return datetime.fromtimestamp(ts, tz(tz_name))


def local_date(ts: float, tz_name: str) -> str:
    return local_dt(ts, tz_name).date().isoformat()


def day_bounds(day: str | date, tz_name: str) -> tuple[float, float]:
    """Epoch range [start, end) of a local calendar day."""
    d = date.fromisoformat(day) if isinstance(day, str) else day
    start = datetime.combine(d, time(0, 0), tz(tz_name))
    end = datetime.combine(d + timedelta(days=1), time(0, 0), tz(tz_name))
    return start.timestamp(), end.timestamp()


def at_local_time(ts: float, at: time, tz_name: str) -> float:
    """Epoch of ``at`` on the local day containing ``ts``."""
    d = local_dt(ts, tz_name).date()
    return datetime.combine(d, at, tz(tz_name)).timestamp()


def in_window(ts: float, start: time, end: time, tz_name: str) -> bool:
    """True if local time of ``ts`` is within [start, end); handles windows crossing midnight."""
    now = local_dt(ts, tz_name).time()
    if start <= end:
        return start <= now < end
    return now >= start or now < end


def split_by_local_day(start: float, end: float, tz_name: str) -> list[tuple[str, float]]:
    """Split [start, end) into (local_date, seconds) chunks at local midnights."""
    parts: list[tuple[str, float]] = []
    cursor = start
    while cursor < end:
        day = local_dt(cursor, tz_name).date()
        _, day_end = day_bounds(day, tz_name)
        chunk_end = min(end, day_end)
        parts.append((day.isoformat(), chunk_end - cursor))
        cursor = chunk_end
    return parts
