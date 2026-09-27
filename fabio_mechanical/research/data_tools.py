"""Research data helpers (Package 1 ``export/research.py``): slices, session segmentation,
regime tags, the optional macro-event calendar hook and contract-roll maps."""

from __future__ import annotations

import csv
import json
from collections.abc import Iterable
from datetime import datetime
from pathlib import Path

from fabio_mechanical.contracts import MarketEvent, MarketFeatureSnapshot
from fabio_mechanical.market.session import SessionCalendar, is_ny_session

EVENT_FIELDS = ["event", "timestamp_utc", "source", "notes"]


def export_event_slice(path: str | Path, events: Iterable[MarketEvent], start_ns: int, end_ns: int) -> Path:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("w", encoding="utf-8") as f:
        for e in events:
            if start_ns <= e.clock_ns < end_ns:
                f.write(e.model_dump_json() + "\n")
    return p


def segment_by_session(events: Iterable[MarketEvent]) -> dict[str, list[MarketEvent]]:
    out: dict[str, list[MarketEvent]] = {"NY_RTH": [], "OTHER": []}
    for e in events:
        out["NY_RTH" if is_ny_session(e.clock_ns) else "OTHER"].append(e)
    return out


def regime_tag(snapshot: MarketFeatureSnapshot) -> str:
    return snapshot.auction.state_candidate.value


def write_event_metadata_hook(path: str | Path, rows: list[dict[str, str]] | None = None) -> Path:
    """Optional external macro-event calendar (FOMC/CPI/NFP ...). Never a required strategy input."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=EVENT_FIELDS)
        w.writeheader()
        for row in rows or []:
            w.writerow({k: row.get(k, "") for k in EVENT_FIELDS})
    return p


def load_news_days(path: str | Path, calendar: SessionCalendar | None = None) -> set[str]:
    """Trading days (ISO) containing at least one calendar event; feeds ``news_day`` tags."""
    cal = calendar or SessionCalendar.from_config(None)
    days: set[str] = set()
    with Path(path).open("r", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            ts = row.get("timestamp_utc", "").strip()
            if not ts:
                continue
            dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            days.add(cal.trading_day(int(dt.timestamp()) * 1_000_000_000).isoformat())
    return days


def write_contract_roll_map(path: str | Path, mappings: dict[str, object]) -> Path:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(mappings, indent=2, sort_keys=True), encoding="utf-8")
    return p
