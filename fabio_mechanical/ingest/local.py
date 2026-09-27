"""Local JSONL MarketEvent files (fixtures, synthetic data, converted slices)."""

from __future__ import annotations

import json
from collections.abc import Iterable, Iterator
from pathlib import Path

from fabio_mechanical.contracts import MarketEvent


def iter_jsonl_events(path: str | Path) -> Iterator[MarketEvent]:
    with Path(path).open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                yield MarketEvent.model_validate_json(line)


def write_jsonl_events(path: str | Path, events: Iterable[MarketEvent]) -> Path:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("w", encoding="utf-8", newline="\n") as f:
        for event in events:
            f.write(json.dumps(event.model_dump(mode="json"), sort_keys=True, separators=(",", ":")) + "\n")
    return p
