from __future__ import annotations

from pathlib import Path
import csv
import json
from typing import Iterable

from fabio_market_replay.contracts import MarketEvent, MarketFeatureSnapshot
from fabio_market_replay.market.session import is_ny_session


def export_event_slice(path: str | Path, events: Iterable[MarketEvent], start_ns: int, end_ns: int) -> Path:
    p = Path(path); p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("w", encoding="utf-8") as f:
        for e in events:
            if start_ns <= e.ts_event_ns < end_ns:
                f.write(e.model_dump_json() + "\n")
    return p


def segment_by_session(events: Iterable[MarketEvent]) -> dict[str, list[MarketEvent]]:
    out = {"NY_RTH": [], "OTHER": []}
    for e in events:
        out["NY_RTH" if is_ny_session(e.ts_event_ns) else "OTHER"].append(e)
    return out


def regime_tag(snapshot: MarketFeatureSnapshot) -> str:
    return snapshot.auction.state_candidate.value


def write_event_metadata_hook(path: str | Path, rows: list[dict[str, str]] | None = None) -> Path:
    """Optional external metadata hook; never used as a required strategy input."""
    p = Path(path); p.parent.mkdir(parents=True, exist_ok=True)
    fields = ["event", "timestamp_utc", "source", "notes"]
    with p.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields); w.writeheader()
        for row in rows or []:
            w.writerow({k: row.get(k, "") for k in fields})
    return p


def write_contract_roll_map(path: str | Path, mappings: dict) -> Path:
    p = Path(path); p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(mappings, indent=2, sort_keys=True), encoding="utf-8")
    return p
