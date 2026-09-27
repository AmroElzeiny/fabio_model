"""Session labelling and CME trading-day assignment (DST-safe via zoneinfo)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

DEFAULT_WINDOWS: tuple[tuple[str, str, time, time], ...] = (
    ("NY_RTH", "America/New_York", time(9, 30), time(16, 0)),
    ("POST_RTH", "America/New_York", time(16, 0), time(17, 0)),
    ("MAINTENANCE", "America/New_York", time(17, 0), time(18, 0)),
    ("LONDON", "Europe/London", time(8, 0), time(16, 30)),
)


def ns_to_datetime(ts_ns: int) -> datetime:
    return datetime.fromtimestamp(ts_ns // 1_000_000_000, tz=timezone.utc).replace(microsecond=(ts_ns % 1_000_000_000) // 1000)


def _parse_time(v: Any) -> time:
    if isinstance(v, time):
        return v
    hh, mm = str(v).split(":")[:2]
    return time(int(hh), int(mm))


@dataclass(slots=True)
class SessionCalendar:
    """Configurable session calendar. First matching window wins; otherwise ``default_label``."""

    windows: tuple[tuple[str, ZoneInfo, time, time], ...]
    default_label: str
    exchange_tz: ZoneInfo
    trading_day_start: time
    _label_cache: tuple[int, str] | None = None

    @classmethod
    def from_config(cls, cfg: dict[str, Any] | None = None) -> SessionCalendar:
        if cfg is None:
            return cls(
                windows=tuple((lbl, ZoneInfo(tz), s, e) for lbl, tz, s, e in DEFAULT_WINDOWS),
                default_label="GLOBEX_OVERNIGHT",
                exchange_tz=ZoneInfo("America/New_York"),
                trading_day_start=time(18, 0),
            )
        spec = cfg["data"]["sessions"]
        return cls(
            windows=tuple((w["label"], ZoneInfo(w["timezone"]), _parse_time(w["start"]), _parse_time(w["end"])) for w in spec["windows"]),
            default_label=spec["default_label"],
            exchange_tz=ZoneInfo(spec["exchange_timezone"]),
            trading_day_start=_parse_time(spec["trading_day_start"]),
        )

    def label(self, ts_ns: int) -> str:
        minute = ts_ns // 60_000_000_000
        cached = self._label_cache
        if cached is not None and cached[0] == minute:
            return cached[1]
        lbl = self._label(minute * 60_000_000_000)
        self._label_cache = (minute, lbl)
        return lbl

    def _label(self, ts_ns: int) -> str:
        utc = ns_to_datetime(ts_ns)
        for lbl, tz, start, end in self.windows:
            t = utc.astimezone(tz).time()
            if start <= t < end:
                return lbl
        return self.default_label

    def trading_day(self, ts_ns: int) -> date:
        """CME trading day: the session that opens at ``trading_day_start`` belongs to the next date."""
        local = ns_to_datetime(ts_ns).astimezone(self.exchange_tz)
        if local.time() >= self.trading_day_start:
            local = local + timedelta(days=1)
        return local.date()

    def trading_day_bounds(self, ts_ns: int) -> tuple[date, int, int]:
        """(trading day, start_ns, end_ns) of the CME trading day containing ``ts_ns``."""
        day = self.trading_day(ts_ns)
        start_local = datetime.combine(day - timedelta(days=1), self.trading_day_start, tzinfo=self.exchange_tz)
        end_local = datetime.combine(day, self.trading_day_start, tzinfo=self.exchange_tz)
        return day, int(start_local.timestamp()) * 1_000_000_000, int(end_local.timestamp()) * 1_000_000_000

    def local_time(self, ts_ns: int, tz: ZoneInfo | None = None) -> time:
        return ns_to_datetime(ts_ns).astimezone(tz or self.exchange_tz).time()


_DEFAULT = SessionCalendar.from_config(None)


def session_label(ts_ns: int, tz_name: str = "America/New_York") -> str:
    """Package 1 compatible helper (default calendar)."""
    return _DEFAULT.label(ts_ns)


def is_ny_session(ts_ns: int, tz_name: str = "America/New_York") -> bool:
    return session_label(ts_ns, tz_name) == "NY_RTH"


def trading_day(ts_ns: int) -> date:
    return _DEFAULT.trading_day(ts_ns)
