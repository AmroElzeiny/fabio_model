from __future__ import annotations

from pathlib import Path
from typing import Iterator
import json

from fabio_market_replay.contracts import MarketEvent


def iter_jsonl_events(path: str | Path) -> Iterator[MarketEvent]:
    with Path(path).open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                yield MarketEvent.model_validate_json(line)


def write_jsonl_events(path: str | Path, events: list[MarketEvent]) -> None:
    p = Path(path); p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("w", encoding="utf-8") as f:
        for event in events:
            f.write(event.model_dump_json() + "\n")


def iter_dbn_mbo(path: str | Path):
    try:
        import databento as db
    except ImportError as exc:
        raise RuntimeError("Install production extra: pip install '.[databento]'") from exc
    store = db.DBNStore.from_file(path)
    yield from store
