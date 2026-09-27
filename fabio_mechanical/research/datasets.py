"""Research datasets: ordered event sources labelled by CME trading day.

A dataset is a list of re-iterable ``EventSource`` objects (one per trading day for Databento
daily files or synthetic days). Everything streams; nothing loads a whole dataset into RAM.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator, Sequence
from datetime import date, timedelta
from typing import Any

from fabio_mechanical.contracts import MarketEvent
from fabio_mechanical.ingest.databento import DatabentoMBOAdapter
from fabio_mechanical.ingest.sources import EventSource, GeneratedSource, chain_events
from fabio_mechanical.ingest.synthetic import SCENARIOS

NS_PER_DAY = 86_400_000_000_000

EventFactory = Callable[[], Iterable[MarketEvent]]
DatasetLike = Sequence[EventSource] | EventFactory


def events_of(dataset: DatasetLike) -> Iterator[MarketEvent]:
    if callable(dataset):
        return iter(dataset())
    return chain_events(dataset)


def _shift_records(records: list[dict[str, Any]], days: int, seq_offset: int) -> list[dict[str, Any]]:
    shift = days * NS_PER_DAY
    out = []
    for r in records:
        x = dict(r)
        x["ts_event"] += shift
        x["ts_recv"] += shift
        x["sequence"] += seq_offset
        out.append(x)
    return out


def _business_days(start: date, n: int) -> list[date]:
    out: list[date] = []
    d = start
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


def synthetic_dataset(
    n_days: int = 5,
    mix: Sequence[str] = ("trend_long", "mean_reversion_short", "quiet_balance", "trend_short", "mean_reversion_long"),
    start: date = date(2026, 1, 13),
) -> list[EventSource]:
    """Deterministic multi-day synthetic dataset (scenario days shifted onto business days)."""
    base = date(2026, 1, 13)
    sources: list[EventSource] = []
    for i, day in enumerate(_business_days(start, n_days)):
        name = mix[i % len(mix)]
        shift = (day - base).days

        def factory(name: str = name, shift: int = shift, i: int = i) -> list[MarketEvent]:
            m = SCENARIOS[name]()
            recs = _shift_records(m.records, shift, (i + 1) * 10_000_000)
            return list(DatabentoMBOAdapter(m.resolver()).events(recs))

        sources.append(
            GeneratedSource(
                factory=factory,
                spec={"generator": "fabio_mechanical.ingest.synthetic", "scenario": name, "shift_days": shift, "index": i},
                trading_day=day,
                name=f"synthetic:{name}:{day.isoformat()}",
            )
        )
    return sources


def slice_days(sources: Sequence[EventSource], start: date | None, end: date | None) -> list[EventSource]:
    return [
        s for s in sources if s.trading_day is None or ((start is None or s.trading_day >= start) and (end is None or s.trading_day <= end))
    ]
