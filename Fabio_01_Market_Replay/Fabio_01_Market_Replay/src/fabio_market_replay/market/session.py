from __future__ import annotations

from datetime import datetime, time, timezone
from zoneinfo import ZoneInfo


def ns_to_datetime(ts_ns: int) -> datetime:
    return datetime.fromtimestamp(ts_ns / 1e9, tz=timezone.utc)


def session_label(ts_ns: int, tz_name: str = "America/New_York") -> str:
    local = ns_to_datetime(ts_ns).astimezone(ZoneInfo(tz_name))
    t = local.timetz().replace(tzinfo=None)
    if time(9, 30) <= t < time(16, 0):
        return "NY_RTH"
    if time(18, 0) <= t or t < time(9, 30):
        return "GLOBEX_OVERNIGHT"
    return "POST_RTH"


def is_ny_session(ts_ns: int, tz_name: str = "America/New_York") -> bool:
    return session_label(ts_ns, tz_name) == "NY_RTH"
